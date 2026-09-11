"""Module 09 的严格公共 DTO 与内部 proposal 类型。

本模块只描述行程规划边界。执行、持久化和外部调用由后续阶段拥有；这里的
validators 只拒绝不合法输入，不替调用方排序、截断或猜测事实。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from enum import Enum
from math import ceil
from typing import Annotated, Literal, TypeAlias
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.baidu_map import (
    BaiduMapErrorCode,
    BaiduCoordinate,
    MapProvenance,
    MatrixTravelMode,
    TravelMode,
)
from app.candidate_research import CandidateResearchOperationPlan, MealIntent, ResearchRole
from app.llm_runtime import LLMRuntimeError, LLMUsage
from app.requirements import ConfirmedTravelRequirement, HubType, TravelPace
from app.workflow_contracts import (
    ArtifactRef,
    ExecutionBudgetKey,
    ExecutionBudgetScope,
    OperationId,
    WorkflowPhase,
)


ITINERARY_PLANNING_SCHEMA_VERSION: Literal["1.0"] = "1.0"
ITINERARY_PLANNING_ITERATION_BUDGET_KEY = ExecutionBudgetKey(
    scope=ExecutionBudgetScope.PHASE,
    name="planning.iteration",
    phase=WorkflowPhase.ITINERARY_PLANNING,
)
ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY = ExecutionBudgetKey(
    scope=ExecutionBudgetScope.PHASE,
    name="planning.direction_call",
    phase=WorkflowPhase.ITINERARY_PLANNING,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_UTC = ZoneInfo("UTC")
_HALF_HOUR = 30

StableId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Handle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
CandidateUid = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
ContentDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
SourceField = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
SafeLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
    )


def _aware_shanghai(value: datetime, *, allow_utc: bool = False) -> bool:
    if value.tzinfo is None or value.utcoffset() is None:
        return False
    if allow_utc and value.utcoffset() == timedelta(0):
        return True
    return value.utcoffset() == timedelta(hours=8)


def _grid(value: datetime) -> bool:
    return (
        value.second == 0
        and value.microsecond == 0
        and value.minute in (0, 30)
    )


def _unique(values: tuple[object, ...], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")


def _contains(intervals: tuple[TimeInterval, ...], start: datetime, end: datetime) -> bool:
    return any(item.start_at <= start and end <= item.end_at for item in intervals)


def _route_minutes(duration_s: int) -> int:
    return 0 if duration_s == 0 else ceil(duration_s / 1800) * 30


class PlanningDayKind(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    TRANSIT_ONLY = "transit_only"


class VisitKind(str, Enum):
    ATTRACTION = "attraction"
    LUNCH = "lunch"
    DINNER = "dinner"
    ATTRACTION_LUNCH = "attraction_lunch"
    ATTRACTION_DINNER = "attraction_dinner"


class BusinessHoursState(str, Enum):
    COMPATIBLE = "compatible"
    CONFLICTS = "conflicts"
    UNKNOWN = "unknown"


class PlanningTool(str, Enum):
    GET_CANDIDATE_EVIDENCE = "get_candidate_evidence"
    GEOCODE_HUB = "geocode_hub"
    CALCULATE_ROUTE_MATRIX = "calculate_route_matrix"
    PLAN_DIRECTIONS = "plan_directions"
    SUBMIT_ITINERARY = "submit_itinerary"
    REQUEST_CANDIDATE_SUPPLEMENT = "request_candidate_supplement"


class PlanningStopReason(str, Enum):
    USER_CLARIFICATION_REQUIRED = "user_clarification_required"
    CANDIDATE_SUPPLY_INSUFFICIENT = "candidate_supply_insufficient"
    SCHEDULE_INFEASIBLE = "schedule_infeasible"
    DIRECTION_BUDGET_EXHAUSTED = "direction_budget_exhausted"
    ITERATION_LIMIT_REACHED = "iteration_limit_reached"
    NO_PROGRESS = "no_progress"
    NO_LEGAL_ACTION = "no_legal_action"
    NO_TERMINAL_SUBMISSION = "no_terminal_submission"


class TransportSemantic(str, Enum):
    WALKING = "walking"
    CYCLING = "cycling"
    PUBLIC_TRANSIT = "public_transit"
    RIDE_HAILING = "ride_hailing"
    PRIVATE_CAR = "private_car"


class PlanningFailureCode(str, Enum):
    INPUT_INVALID = "input_invalid"
    PREDECESSOR_INVALID = "predecessor_invalid"
    OPERATION_PLAN_INVALID = "operation_plan_invalid"
    REQUEST_MISMATCH = "request_mismatch"
    MODEL_CONTRACT_INVALID = "model_contract_invalid"
    ACTION_BATCH_INVALID = "action_batch_invalid"
    POLICY_CONTRACT_INVALID = "policy_contract_invalid"
    ARTIFACT_ERROR = "artifact_error"
    ARTIFACT_CAPACITY_EXCEEDED = "artifact_capacity_exceeded"
    BUDGET_ERROR = "budget_error"
    OBSERVATION_INVALID = "observation_invalid"
    OPERATION_RESULT_INDETERMINATE = "operation_result_indeterminate"
    RUN_CONTEXT_CLOSED = "run_context_closed"
    INTERNAL_INVARIANT_BROKEN = "internal_invariant_broken"


class PlanningIterationOperationIds(_Model):
    schema_version: Literal["1.0"] = "1.0"
    invocation_slot_index: Annotated[int, Field(ge=0, le=5)]
    action_operation_id: OperationId
    planning_state_operation_id: OperationId

    @model_validator(mode="after")
    def _validate_ids(self) -> PlanningIterationOperationIds:
        ids = (self.action_operation_id, self.planning_state_operation_id)
        if any(item.phase is not WorkflowPhase.ITINERARY_PLANNING for item in ids):
            raise ValueError("planning operation ids must use itinerary_planning phase")
        if len({(item.run_id, item.phase, item.logical_step) for item in ids}) != 2:
            raise ValueError("planning operation ids must be unique")
        if ids[0].run_id != ids[1].run_id:
            raise ValueError("planning operation ids must share run")
        return self


class ItineraryPlanningOperationPlan(_Model):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    iteration_slots: Annotated[tuple[PlanningIterationOperationIds, ...], Field(min_length=0, max_length=6)]
    hub_geocode_operation_ids: Annotated[tuple[OperationId, ...], Field(min_length=0, max_length=2)]
    matrix_operation_ids: Annotated[tuple[OperationId, ...], Field(min_length=0, max_length=360)]
    direction_operation_ids: Annotated[tuple[OperationId, ...], Field(min_length=0, max_length=482)]
    terminal_action_operation_id: OperationId
    terminal_successor_operation_id: OperationId

    @model_validator(mode="after")
    def _validate_plan(self) -> ItineraryPlanningOperationPlan:
        slots = self.iteration_slots
        indexes = tuple(slot.invocation_slot_index for slot in slots)
        if indexes and indexes != tuple(range(indexes[0], indexes[0] + len(indexes))):
            raise ValueError("iteration slots must be a contiguous increasing subsequence")
        all_ids: list[OperationId] = [self.terminal_action_operation_id, self.terminal_successor_operation_id]
        for slot in slots:
            all_ids.extend((slot.action_operation_id, slot.planning_state_operation_id))
        all_ids.extend(self.hub_geocode_operation_ids)
        all_ids.extend(self.matrix_operation_ids)
        all_ids.extend(self.direction_operation_ids)
        if any(item.run_id != self.run_id for item in all_ids):
            raise ValueError("all operation ids must use plan run")
        if any(item.phase is not WorkflowPhase.ITINERARY_PLANNING for item in all_ids):
            raise ValueError("all operation ids must use itinerary_planning phase")
        keys = {(item.run_id, item.phase, item.logical_step) for item in all_ids}
        if len(keys) != len(all_ids):
            raise ValueError("operation ids must be globally unique")
        return self


class ItineraryPlanningInitialRequest(_Model):
    schema_version: Literal["1.0"] = "1.0"
    confirmed: ConfirmedTravelRequirement
    hotel_selection_ref: ArtifactRef
    candidate_set_ref: ArtifactRef
    operation_plan: ItineraryPlanningOperationPlan

    @model_validator(mode="after")
    def _validate_initial(self) -> ItineraryPlanningInitialRequest:
        indexes = tuple(item.invocation_slot_index for item in self.operation_plan.iteration_slots)
        if indexes != (0, 1, 2, 3, 4, 5):
            raise ValueError("initial request requires iteration slots 0 through 5")
        _require_ref(self.hotel_selection_ref, "hotel.selection", WorkflowPhase.HOTEL_SELECTION, self.operation_plan.run_id)
        _require_ref(self.candidate_set_ref, "candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH, self.operation_plan.run_id)
        return self


class ItineraryPlanningResumeRequest(_Model):
    schema_version: Literal["2.0"] = "2.0"
    predecessor_planning_ref: ArtifactRef
    supplemented_candidate_set_ref: ArtifactRef
    operation_plan: ItineraryPlanningOperationPlan
    supplemental_operation_plan: CandidateResearchOperationPlan

    @model_validator(mode="after")
    def _validate_resume(self) -> ItineraryPlanningResumeRequest:
        if self.supplemental_operation_plan.run_id != self.operation_plan.run_id:
            raise ValueError("supplemental plan must use the planning run")
        _require_ref(self.predecessor_planning_ref, "itinerary_planning.itinerary", WorkflowPhase.ITINERARY_PLANNING, self.operation_plan.run_id)
        _require_ref(self.supplemented_candidate_set_ref, "candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH, self.operation_plan.run_id)
        if self.predecessor_planning_ref.artifact_id == self.supplemented_candidate_set_ref.artifact_id:
            raise ValueError("resume predecessor and candidate set must be different artifacts")
        return self


def _require_ref(ref: ArtifactRef, artifact_type: str, phase: WorkflowPhase, run_id: UUID) -> None:
    if ref.artifact_type != artifact_type or ref.schema_version != "1.0":
        raise ValueError("artifact reference type/version mismatch")
    if ref.operation_id.phase is not phase or ref.operation_id.run_id != run_id:
        raise ValueError("artifact reference run/phase mismatch")


def _require_itinerary_ref(ref: ArtifactRef | None, *, required: bool = False) -> None:
    if ref is None:
        if required:
            raise ValueError("itinerary reference is required")
        return
    if ref.artifact_type != "itinerary_planning.itinerary" or ref.schema_version != "1.0" or ref.operation_id.phase is not WorkflowPhase.ITINERARY_PLANNING:
        raise ValueError("reference must point to an itinerary planning artifact")


class CandidateLocationRef(_Model):
    schema_version: Literal["1.0"] = "1.0"
    ref_type: Literal["candidate"] = "candidate"
    admission_ordinal: Annotated[int, Field(ge=0, le=127)]
    uid: CandidateUid


class HotelLocationRef(_Model):
    schema_version: Literal["1.0"] = "1.0"
    ref_type: Literal["hotel"] = "hotel"
    hotel_selection_ref_index: Literal[0] = 0


class HubLocationRef(_Model):
    schema_version: Literal["1.0"] = "1.0"
    ref_type: Literal["hub"] = "hub"
    endpoint_kind: Literal["arrival", "departure"]
    geocode_observation_index: Annotated[int, Field(ge=0, le=1)]


PlanningLocationRef: TypeAlias = Annotated[
    CandidateLocationRef | HotelLocationRef | HubLocationRef,
    Field(discriminator="ref_type"),
]


class TimeInterval(_Model):
    schema_version: Literal["1.0"] = "1.0"
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def _validate_interval(self) -> TimeInterval:
        if not _aware_shanghai(self.start_at) or not _aware_shanghai(self.end_at):
            raise ValueError("time interval must use Asia/Shanghai aware datetimes")
        if self.start_at >= self.end_at:
            raise ValueError("time interval must be non-empty")
        if not _grid(self.start_at) or not _grid(self.end_at):
            raise ValueError("time interval must use the thirty-minute grid")
        return self


class RouteTimeInterval(_Model):
    schema_version: Literal["1.0"] = "1.0"
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def _validate_interval(self) -> RouteTimeInterval:
        if not _aware_shanghai(self.start_at) or not _aware_shanghai(self.end_at):
            raise ValueError("route interval must use Asia/Shanghai aware datetimes")
        if self.start_at > self.end_at:
            raise ValueError("route interval must be ordered")
        if self.start_at != self.end_at and (not _grid(self.start_at) or not _grid(self.end_at)):
            raise ValueError("positive route interval must use the thirty-minute grid")
        return self


class DailyTimeRule(_Model):
    schema_version: Literal["1.0"] = "1.0"
    rule_id: StableId
    date: date | None
    interval: TimeInterval
    strength: Literal["hard", "soft"]
    source_field: SourceField
    source_digest: ContentDigest

    @model_validator(mode="after")
    def _date(self) -> DailyTimeRule:
        if self.date is not None and self.interval.start_at.date() != self.date:
            raise ValueError("specific-date rule must use its local date")
        return self


class TimeSensitiveMustAnchor(_Model):
    schema_version: Literal["1.0"] = "1.0"
    anchor_id: StableId
    requirement_group_id: StableId
    date: date
    event_at: datetime
    source: Literal["user_provided", "verified_evidence"]
    source_ref_index: Annotated[int, Field(ge=0, le=127)] | None

    @model_validator(mode="after")
    def _source(self) -> TimeSensitiveMustAnchor:
        if not _aware_shanghai(self.event_at):
            raise ValueError("time-sensitive anchor must use Asia/Shanghai")
        if self.event_at.date() != self.date:
            raise ValueError("anchor date must match event local date")
        if self.source == "user_provided" and self.source_ref_index is not None:
            raise ValueError("user-provided anchor cannot cite evidence")
        if self.source == "verified_evidence" and self.source_ref_index is None:
            raise ValueError("verified anchor must cite evidence")
        return self


class TransportPolicyRule(_Model):
    schema_version: Literal["1.0"] = "1.0"
    rule_id: StableId
    semantic: TransportSemantic
    strength: Literal["hard", "soft"]
    numeric_limit_minutes: Annotated[int, Field(ge=1, le=1440)] | None
    source_field: SourceField
    source_digest: ContentDigest


class EffectivePlanningPolicy(_Model):
    schema_version: Literal["1.0"] = "1.0"
    effective_pace: TravelPace
    daily_time_rules: Annotated[tuple[DailyTimeRule, ...], Field(min_length=0, max_length=64)]
    time_sensitive_must: Annotated[tuple[TimeSensitiveMustAnchor, ...], Field(min_length=0, max_length=100)]
    transport_rules: Annotated[tuple[TransportPolicyRule, ...], Field(min_length=0, max_length=32)]
    locked: Literal[True] = True

    @model_validator(mode="after")
    def _unique_rules(self) -> EffectivePlanningPolicy:
        _unique(tuple(item.rule_id for item in self.daily_time_rules), "daily rule ids")
        _unique(tuple(item.anchor_id for item in self.time_sensitive_must), "anchor ids")
        _unique(tuple(item.rule_id for item in self.transport_rules), "transport rule ids")
        return self


class ScheduledVisit(_Model):
    schema_version: Literal["1.0"] = "1.0"
    visit_id: StableId
    location: CandidateLocationRef
    visit_kind: VisitKind
    interval: TimeInterval
    duration_source: Literal["user_explicit", "planning_estimate"]
    must_group_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=100)]
    time_sensitive_anchor_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=8)]
    business_hours_state: BusinessHoursState
    business_evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=127)], ...], Field(min_length=0, max_length=8)]
    requires_verification: bool

    @model_validator(mode="after")
    def _visit(self) -> ScheduledVisit:
        _unique(self.must_group_ids, "must group ids")
        _unique(self.time_sensitive_anchor_ids, "anchor ids")
        _unique(self.business_evidence_indexes, "business evidence indexes")
        if self.business_hours_state is BusinessHoursState.CONFLICTS:
            raise ValueError("conflicting business hours cannot be scheduled")
        if self.business_hours_state is BusinessHoursState.UNKNOWN and not self.requires_verification:
            raise ValueError("unknown business hours require verification")
        return self


class RouteAlternativeSummary(_Model):
    schema_version: Literal["1.0"] = "1.0"
    alternative_index: Annotated[int, Field(ge=0, le=2)]
    distance_m: Annotated[int, Field(ge=0)]
    duration_s: Annotated[int, Field(ge=0)]
    step_count: Annotated[int, Field(ge=1, le=128)]


class RouteLeg(_Model):
    schema_version: Literal["1.0"] = "1.0"
    leg_id: StableId
    origin: PlanningLocationRef
    destination: PlanningLocationRef
    interval: RouteTimeInterval
    transport_semantic: TransportSemantic
    route_mode: TravelMode
    directions_observation_index: Annotated[int, Field(ge=0, le=481)]
    alternative_index: Annotated[int, Field(ge=0, le=2)]
    distance_m: Annotated[int, Field(ge=0)]
    duration_s: Annotated[int, Field(ge=0)]
    scheduled_minutes: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _route(self) -> RouteLeg:
        expected_mode = {
            TransportSemantic.WALKING: TravelMode.WALKING,
            TransportSemantic.CYCLING: TravelMode.RIDING,
            TransportSemantic.PUBLIC_TRANSIT: TravelMode.TRANSIT,
            TransportSemantic.RIDE_HAILING: TravelMode.DRIVING,
            TransportSemantic.PRIVATE_CAR: TravelMode.DRIVING,
        }[self.transport_semantic]
        if self.route_mode is not expected_mode:
            raise ValueError("transport semantic and route mode mismatch")
        expected = _route_minutes(self.duration_s)
        if self.scheduled_minutes != expected or self.scheduled_minutes % 30 != 0:
            raise ValueError("scheduled route minutes must be the exact thirty-minute ceiling")
        actual = int((self.interval.end_at - self.interval.start_at).total_seconds() // 60)
        if actual != self.scheduled_minutes:
            raise ValueError("route interval length must match scheduled minutes")
        return self


class BoundaryTransfer(_Model):
    schema_version: Literal["1.0"] = "1.0"
    transfer_kind: Literal["arrival", "departure"]
    hub: HubLocationRef
    hotel: HotelLocationRef
    route_leg: RouteLeg
    processing_minutes: Annotated[int, Field(ge=30)]
    approximate_extra_minutes: Literal[0, 30]

    @model_validator(mode="after")
    def _transfer(self) -> BoundaryTransfer:
        leg = self.route_leg
        if self.transfer_kind == "arrival":
            if leg.origin != self.hub or leg.destination != self.hotel:
                raise ValueError("arrival transfer must be hub to hotel")
        else:
            if leg.origin != self.hotel or leg.destination != self.hub:
                raise ValueError("departure transfer must be hotel to hub")
        return self


class BackupOption(_Model):
    schema_version: Literal["1.0"] = "1.0"
    backup_kind: Literal["attraction", "lunch", "dinner"]
    core_visit_id: StableId
    candidate: CandidateLocationRef
    matrix_observation_index: Annotated[int, Field(ge=0, le=359)]
    matrix_mode: MatrixTravelMode
    distance_m: Annotated[int, Field(ge=0, le=3000)]
    business_hours_state: BusinessHoursState
    business_evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=127)], ...], Field(min_length=0, max_length=8)]
    requires_verification: bool

    @model_validator(mode="after")
    def _backup(self) -> BackupOption:
        _unique(self.business_evidence_indexes, "backup evidence indexes")
        if self.business_hours_state is BusinessHoursState.CONFLICTS:
            raise ValueError("conflicting business hours cannot be a backup")
        if self.business_hours_state is BusinessHoursState.UNKNOWN and not self.requires_verification:
            raise ValueError("unknown backup hours require verification")
        return self


class DayPlan(_Model):
    schema_version: Literal["1.0"] = "1.0"
    date: date
    day_kind: PlanningDayKind
    theme: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    open_intervals: Annotated[tuple[TimeInterval, ...], Field(min_length=0, max_length=16)]
    visits: Annotated[tuple[ScheduledVisit, ...], Field(min_length=0, max_length=6)]
    route_legs: Annotated[tuple[RouteLeg, ...], Field(min_length=0, max_length=7)]
    backups: Annotated[tuple[BackupOption, ...], Field(min_length=0, max_length=3)]

    @model_validator(mode="after")
    def _day(self) -> DayPlan:
        if re.search(r"(?:https?://|www\.|[<>`\[\]#])", self.theme, re.IGNORECASE):
            raise ValueError("theme must be plain safe text")
        if self.day_kind is PlanningDayKind.TRANSIT_ONLY:
            if self.visits or self.route_legs or self.backups or self.open_intervals:
                raise ValueError("transit-only day cannot contain sightseeing content")
            return self
        if not self.open_intervals or not self.visits:
            raise ValueError("full and partial days require an open interval and a visit")
        if len(self.route_legs) != len(self.visits) + 1:
            raise ValueError("day route legs must close the hotel loop")
        _unique(tuple(item.visit_id for item in self.visits), "visit ids")
        _unique(tuple(item.location.uid for item in self.visits), "main candidate uids")
        _unique(tuple(item.candidate.uid for item in self.backups), "backup candidate uids")
        if set(item.candidate.uid for item in self.backups) & set(item.location.uid for item in self.visits):
            raise ValueError("backup cannot reuse a main candidate")
        attractions = sum(item.visit_kind in {VisitKind.ATTRACTION, VisitKind.ATTRACTION_LUNCH, VisitKind.ATTRACTION_DINNER} for item in self.visits)
        lunches = sum(item.visit_kind in {VisitKind.LUNCH, VisitKind.ATTRACTION_LUNCH} for item in self.visits)
        dinners = sum(item.visit_kind in {VisitKind.DINNER, VisitKind.ATTRACTION_DINNER} for item in self.visits)
        if attractions > 4 or lunches > 1 or dinners > 1 or len(self.visits) > 6:
            raise ValueError("day role capacity exceeded")
        if any(item.visit_kind is VisitKind.ATTRACTION_LUNCH and item.visit_kind is VisitKind.ATTRACTION_DINNER for item in self.visits):
            raise ValueError("a visit cannot serve two combination meals")
        first, *middle, last = self.route_legs
        if first.origin.ref_type != "hotel" or last.destination.ref_type != "hotel":
            raise ValueError("day route must start and end at hotel")
        expected_refs: list[PlanningLocationRef] = [item.location for item in self.visits]
        if first.destination != expected_refs[0] or last.origin != expected_refs[-1]:
            raise ValueError("day route endpoints do not match visits")
        if first.interval.end_at > self.visits[0].interval.start_at:
            raise ValueError("first route must finish before first visit")
        if last.interval.start_at < self.visits[-1].interval.end_at:
            raise ValueError("return route must start after last visit")
        for route, left_visit, right_visit in zip(self.route_legs[1:-1], self.visits[:-1], self.visits[1:], strict=True):
            if route.origin != left_visit.location or route.destination != right_visit.location:
                raise ValueError("day route legs are not in visit order")
            if route.interval.start_at < left_visit.interval.end_at or route.interval.end_at > right_visit.interval.start_at:
                raise ValueError("intermediate route must fit between adjacent visits")
        for visit in self.visits:
            if not _contains(self.open_intervals, visit.interval.start_at, visit.interval.end_at):
                raise ValueError("visit is outside day open intervals")
        for leg in self.route_legs:
            if leg.interval.start_at != leg.interval.end_at and not _contains(self.open_intervals, leg.interval.start_at, leg.interval.end_at):
                raise ValueError("route is outside day open intervals")
        for left, right in zip(self.visits, self.visits[1:]):
            if left.interval.end_at > right.interval.start_at:
                raise ValueError("visits overlap")
        for left, right in zip(self.route_legs, self.route_legs[1:]):
            if left.interval.end_at > right.interval.start_at:
                raise ValueError("route legs overlap")
        return self


class ItineraryDraft(_Model):
    schema_version: Literal["1.0"] = "1.0"
    requirement_digest: ContentDigest
    hotel_selection_ref: ArtifactRef
    candidate_set_ref: ArtifactRef
    effective_policy: EffectivePlanningPolicy
    arrival_transfer: BoundaryTransfer
    days: Annotated[tuple[DayPlan, ...], Field(min_length=1, max_length=60)]
    departure_transfer: BoundaryTransfer
    upstream_gap_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=128)]

    @model_validator(mode="after")
    def _draft(self) -> ItineraryDraft:
        if self.arrival_transfer.transfer_kind != "arrival" or self.departure_transfer.transfer_kind != "departure":
            raise ValueError("draft must contain one arrival and one departure transfer")
        if self.arrival_transfer.hotel != self.departure_transfer.hotel:
            raise ValueError("boundary transfers must use one hotel reference")
        dates = tuple(item.date for item in self.days)
        if dates != tuple(dates[0] + timedelta(days=index) for index in range(len(dates))):
            raise ValueError("draft days must be consecutive and ordered")
        _unique(dates, "draft dates")
        _unique(self.upstream_gap_ids, "upstream gap ids")
        return self


# Proposal DTOs intentionally remain module-private; they are tool schemas but not
# part of the package facade.
class ProposedDailyTimeRule(_Model):
    schema_version: Literal["1.0"] = "1.0"
    rule_id: StableId
    date: date | None
    start_at: datetime
    end_at: datetime
    strength: Literal["hard", "soft"]
    source_field: SourceField

    @model_validator(mode="after")
    def _interval(self) -> ProposedDailyTimeRule:
        if not _aware_shanghai(self.start_at) or not _aware_shanghai(self.end_at) or self.start_at >= self.end_at:
            raise ValueError("invalid proposed daily interval")
        if not _grid(self.start_at) or not _grid(self.end_at):
            raise ValueError("proposed daily interval must use grid")
        return self


class ProposedTimeSensitiveMustAnchor(_Model):
    schema_version: Literal["1.0"] = "1.0"
    anchor_id: StableId
    requirement_group_id: StableId
    date: date
    event_at: datetime
    source: Literal["user_provided", "verified_evidence"]
    source_handle: Handle | None

    @model_validator(mode="after")
    def _source(self) -> ProposedTimeSensitiveMustAnchor:
        if not _aware_shanghai(self.event_at) or self.event_at.date() != self.date:
            raise ValueError("invalid proposed anchor time")
        if (self.source == "user_provided") != (self.source_handle is None):
            raise ValueError("proposed anchor source handle mismatch")
        return self


class ProposedTransportPolicyRule(_Model):
    schema_version: Literal["1.0"] = "1.0"
    rule_id: StableId
    semantic: TransportSemantic
    strength: Literal["hard", "soft"]
    numeric_limit_minutes: Annotated[int, Field(ge=1, le=1440)] | None
    source_field: SourceField


class ProposedEffectivePlanningPolicy(_Model):
    schema_version: Literal["1.0"] = "1.0"
    effective_pace: TravelPace
    daily_time_rules: Annotated[tuple[ProposedDailyTimeRule, ...], Field(min_length=0, max_length=64)]
    time_sensitive_must: Annotated[tuple[ProposedTimeSensitiveMustAnchor, ...], Field(min_length=0, max_length=100)]
    transport_rules: Annotated[tuple[ProposedTransportPolicyRule, ...], Field(min_length=0, max_length=32)]


class ProposedScheduledVisit(_Model):
    schema_version: Literal["1.0"] = "1.0"
    visit_id: StableId
    candidate_handle: Handle
    visit_kind: VisitKind
    start_at: datetime
    end_at: datetime
    duration_source: Literal["user_explicit", "planning_estimate"]
    must_group_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=100)]
    time_sensitive_anchor_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=8)]
    business_hours_state: BusinessHoursState
    business_evidence_handles: Annotated[tuple[Handle, ...], Field(min_length=0, max_length=8)]

    @model_validator(mode="after")
    def _visit(self) -> ProposedScheduledVisit:
        if not _aware_shanghai(self.start_at) or not _aware_shanghai(self.end_at) or self.start_at >= self.end_at:
            raise ValueError("invalid proposed visit interval")
        if not _grid(self.start_at) or not _grid(self.end_at):
            raise ValueError("proposed visit interval must use grid")
        _unique(self.must_group_ids, "must ids")
        _unique(self.time_sensitive_anchor_ids, "anchor ids")
        _unique(self.business_evidence_handles, "business handles")
        if self.business_hours_state is BusinessHoursState.CONFLICTS:
            raise ValueError("conflicting proposed visit is not legal")
        return self


class ProposedRouteLeg(_Model):
    schema_version: Literal["1.0"] = "1.0"
    leg_id: StableId = Field(
        description="Exact leg_id returned by the Directions observation selected through directions_handle."
    )
    origin_handle: Handle
    destination_handle: Handle
    start_at: datetime
    end_at: datetime
    transport_semantic: TransportSemantic
    route_mode: TravelMode
    directions_handle: Handle = Field(
        description="Handle of the same Directions observation that returned leg_id and the selected alternative."
    )
    alternative_index: Annotated[int, Field(ge=0, le=2)]

    @model_validator(mode="after")
    def _leg(self) -> ProposedRouteLeg:
        if not _aware_shanghai(self.start_at) or not _aware_shanghai(self.end_at) or self.start_at > self.end_at:
            raise ValueError("invalid proposed route interval")
        expected = {
            TransportSemantic.WALKING: TravelMode.WALKING,
            TransportSemantic.CYCLING: TravelMode.RIDING,
            TransportSemantic.PUBLIC_TRANSIT: TravelMode.TRANSIT,
            TransportSemantic.RIDE_HAILING: TravelMode.DRIVING,
            TransportSemantic.PRIVATE_CAR: TravelMode.DRIVING,
        }[self.transport_semantic]
        if self.route_mode is not expected:
            raise ValueError("proposed route mode mismatch")
        return self


class ProposedBoundaryTransfer(_Model):
    schema_version: Literal["1.0"] = "1.0"
    transfer_kind: Literal["arrival", "departure"]
    hub_handle: Handle
    hotel_handle: Handle
    route_leg: ProposedRouteLeg


class ProposedBackupOption(_Model):
    schema_version: Literal["1.0"] = "1.0"
    backup_kind: Literal["attraction", "lunch", "dinner"]
    core_visit_id: StableId
    candidate_handle: Handle
    matrix_handle: Handle
    matrix_mode: MatrixTravelMode


class ProposedDayPlan(_Model):
    schema_version: Literal["1.0"] = "1.0"
    date: date
    day_kind: PlanningDayKind
    theme: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    open_intervals: Annotated[tuple[TimeInterval, ...], Field(min_length=0, max_length=16)]
    visits: Annotated[tuple[ProposedScheduledVisit, ...], Field(min_length=0, max_length=6)]
    route_legs: Annotated[tuple[ProposedRouteLeg, ...], Field(min_length=0, max_length=7)]
    backups: Annotated[tuple[ProposedBackupOption, ...], Field(min_length=0, max_length=3)]


class ProposedItineraryDraft(_Model):
    schema_version: Literal["1.0"] = "1.0"
    arrival_transfer: ProposedBoundaryTransfer
    days: Annotated[tuple[ProposedDayPlan, ...], Field(min_length=1, max_length=60)]
    departure_transfer: ProposedBoundaryTransfer
    upstream_gap_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=128)]


class ProposedCandidateCountTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["candidate_count"] = "candidate_count"
    role: ResearchRole
    current_count: Annotated[int, Field(ge=0)]
    target_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _target(self) -> ProposedCandidateCountTarget:
        if self.current_count >= self.target_count:
            raise ValueError("candidate target must increase supply")
        return self


class ProposedAreaCoverageTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["area_coverage"] = "area_coverage"
    role: ResearchRole
    anchor_handle: Handle
    radius_km: Literal[1, 3, 5, 10]


class ProposedMealCoverageTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["meal_coverage"] = "meal_coverage"
    meal_intent: MealIntent
    anchor_handle: Handle
    radius_km: Literal[1, 3, 5, 10]


class ProposedMustResolutionTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["must_resolution"] = "must_resolution"
    requirement_group_id: StableId
    requirement_item_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]


class ProposedAvoidResolutionTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["avoid_resolution"] = "avoid_resolution"
    requirement_group_id: StableId
    requirement_item_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]
    candidate_handles: Annotated[tuple[Handle, ...], Field(min_length=1, max_length=128)]


class ProposedCandidateDetailsTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["candidate_details"] = "candidate_details"
    candidate_handles: Annotated[tuple[Handle, ...], Field(min_length=1, max_length=128)]


class ProposedReplacementTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["replacement"] = "replacement"
    rejected_candidate_handle: Handle
    role: ResearchRole
    anchor_handle: Handle | None
    radius_km: Literal[1, 3, 5, 10] | None

    @model_validator(mode="after")
    def _anchor(self) -> ProposedReplacementTarget:
        if (self.anchor_handle is None) != (self.radius_km is None):
            raise ValueError("replacement anchor and radius must be paired")
        return self


ProposedSupplementTarget: TypeAlias = Annotated[
    ProposedCandidateCountTarget
    | ProposedAreaCoverageTarget
    | ProposedMealCoverageTarget
    | ProposedMustResolutionTarget
    | ProposedAvoidResolutionTarget
    | ProposedCandidateDetailsTarget
    | ProposedReplacementTarget,
    Field(discriminator="target_type"),
]


class ProposedSupplementGap(_Model):
    schema_version: Literal["1.0"] = "1.0"
    source_gap_type: Literal["candidate_supply", "backup_unavailable"]
    date: date | None
    target: ProposedSupplementTarget
    evidence_handles: Annotated[tuple[Handle, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _date(self) -> ProposedSupplementGap:
        if self.source_gap_type == "backup_unavailable" and self.date is None:
            raise ValueError("backup supplement requires date")
        _unique(self.evidence_handles, "supplement evidence handles")
        return self


class GetCandidateEvidenceArgs(_Model):
    schema_version: Literal["1.0"] = "1.0"
    candidate_handles: Annotated[tuple[Handle, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def _unique_handles(self) -> GetCandidateEvidenceArgs:
        _unique(self.candidate_handles, "candidate handles")
        return self


class GeocodeHubArgs(_Model):
    schema_version: Literal["1.0"] = "1.0"
    endpoint_kind: Literal["arrival", "departure"]


class CalculateRouteMatrixArgs(_Model):
    schema_version: Literal["1.0"] = "1.0"
    date: date
    mode: MatrixTravelMode
    location_handles: Annotated[tuple[Handle, ...], Field(min_length=1, max_length=10)]

    @model_validator(mode="after")
    def _locations(self) -> CalculateRouteMatrixArgs:
        _unique(self.location_handles, "matrix locations")
        return self


class DirectionLegIntent(_Model):
    schema_version: Literal["1.0"] = "1.0"
    leg_id: StableId
    origin_handle: Handle
    destination_handle: Handle
    transport_semantic: TransportSemantic
    route_mode: TravelMode
    alternatives_limit: Annotated[int, Field(ge=1, le=3)]

    @model_validator(mode="after")
    def _mode(self) -> DirectionLegIntent:
        expected = {
            TransportSemantic.WALKING: TravelMode.WALKING,
            TransportSemantic.CYCLING: TravelMode.RIDING,
            TransportSemantic.PUBLIC_TRANSIT: TravelMode.TRANSIT,
            TransportSemantic.RIDE_HAILING: TravelMode.DRIVING,
            TransportSemantic.PRIVATE_CAR: TravelMode.DRIVING,
        }[self.transport_semantic]
        if self.route_mode is not expected:
            raise ValueError("direction semantic and mode mismatch")
        return self


class PlanDirectionsArgs(_Model):
    schema_version: Literal["1.0"] = "1.0"
    legs: Annotated[tuple[DirectionLegIntent, ...], Field(min_length=1, max_length=482)]

    @model_validator(mode="after")
    def _legs(self) -> PlanDirectionsArgs:
        _unique(tuple(item.leg_id for item in self.legs), "direction leg ids")
        _unique(tuple((item.origin_handle, item.destination_handle, item.route_mode, item.alternatives_limit) for item in self.legs), "direction requests")
        return self


class SubmitItineraryArgs(_Model):
    schema_version: Literal["1.0"] = "1.0"
    effective_policy: ProposedEffectivePlanningPolicy
    draft: ProposedItineraryDraft


class RequestCandidateSupplementArgs(_Model):
    schema_version: Literal["1.0"] = "1.0"
    effective_policy: ProposedEffectivePlanningPolicy
    draft: ProposedItineraryDraft | None
    proposed_gaps: Annotated[tuple[ProposedSupplementGap, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def _gaps(self) -> RequestCandidateSupplementArgs:
        if len(self.proposed_gaps) > 32:
            raise ValueError("supplement gap capacity exceeded")
        return self


# Public gap targets and gaps.
class CandidateCountTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["candidate_count"] = "candidate_count"
    role: ResearchRole
    current_count: Annotated[int, Field(ge=0)]
    target_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _target(self) -> CandidateCountTarget:
        if self.current_count >= self.target_count:
            raise ValueError("candidate target must increase supply")
        return self


class AreaCoverageTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["area_coverage"] = "area_coverage"
    role: ResearchRole
    anchor: CandidateLocationRef | HotelLocationRef
    radius_km: Literal[1, 3, 5, 10]


class MealCoverageTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["meal_coverage"] = "meal_coverage"
    meal_intent: MealIntent
    anchor: CandidateLocationRef | HotelLocationRef
    radius_km: Literal[1, 3, 5, 10]


class MustResolutionTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["must_resolution"] = "must_resolution"
    requirement_group_id: StableId
    requirement_item_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]


class AvoidResolutionTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["avoid_resolution"] = "avoid_resolution"
    requirement_group_id: StableId
    requirement_item_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]
    candidate_uids: Annotated[tuple[CandidateUid, ...], Field(min_length=1, max_length=128)]


class CandidateDetailsTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["candidate_details"] = "candidate_details"
    candidate_uids: Annotated[tuple[CandidateUid, ...], Field(min_length=1, max_length=128)]


class ReplacementTarget(_Model):
    schema_version: Literal["1.0"] = "1.0"
    target_type: Literal["replacement"] = "replacement"
    rejected_uid: CandidateUid
    role: ResearchRole
    anchor: CandidateLocationRef | HotelLocationRef | None
    radius_km: Literal[1, 3, 5, 10] | None

    @model_validator(mode="after")
    def _anchor(self) -> ReplacementTarget:
        if (self.anchor is None) != (self.radius_km is None):
            raise ValueError("replacement anchor and radius must be paired")
        return self


CandidateSupplementTarget: TypeAlias = Annotated[
    CandidateCountTarget | AreaCoverageTarget | MealCoverageTarget | MustResolutionTarget
    | AvoidResolutionTarget | CandidateDetailsTarget | ReplacementTarget,
    Field(discriminator="target_type"),
]


class _GapBase(_Model):
    schema_version: Literal["1.0"] = "1.0"
    gap_type: str
    gap_id: StableId
    evidence_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=0, max_length=8)]


class HubResolutionGap(_GapBase):
    gap_type: Literal["hub_resolution"] = "hub_resolution"
    endpoint_kind: Literal["arrival", "departure"]
    status: Literal["ambiguous", "no_result"]
    candidate_count: Annotated[int, Field(ge=0, le=10)]


class DailyTimePolicyClarificationGap(_GapBase):
    gap_type: Literal["daily_time_policy_clarification"] = "daily_time_policy_clarification"
    source_field: SourceField
    reason: Literal["ambiguous", "conflicting", "unsupported_expression"]
    rule_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=16)]


class TimeSensitiveMustUnverifiedGap(_GapBase):
    gap_type: Literal["time_sensitive_must_unverified"] = "time_sensitive_must_unverified"
    requirement_group_id: StableId
    date: date | None
    candidate_uid: CandidateUid | None
    reason: Literal["missing_time", "hard_conflict"]


class MustVisitUnscheduledGap(_GapBase):
    gap_type: Literal["must_visit_unscheduled"] = "must_visit_unscheduled"
    requirement_group_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]
    candidate_uids: Annotated[tuple[CandidateUid, ...], Field(min_length=0, max_length=8)]
    reason: Literal["no_eligible", "capacity", "time", "route", "business_hours", "transport"]


class MealSlotUnfilledGap(_GapBase):
    gap_type: Literal["meal_slot_unfilled"] = "meal_slot_unfilled"
    date: date
    meal: Literal["lunch", "dinner"]
    reason: Literal["no_candidate", "time", "route", "business_hours"]


class DayCompositionGap(_GapBase):
    gap_type: Literal["day_composition"] = "day_composition"
    date: date
    day_kind: PlanningDayKind
    actual_attraction_count: Annotated[int, Field(ge=0, le=4)]
    required_min: Annotated[int, Field(ge=0, le=4)]
    required_max: Annotated[int, Field(ge=0, le=4)]
    reason: Literal["attraction_count", "transit_has_visit"]

    @model_validator(mode="after")
    def _range(self) -> DayCompositionGap:
        if self.required_min > self.required_max:
            raise ValueError("invalid attraction range")
        if self.reason == "attraction_count" and self.required_min <= self.actual_attraction_count <= self.required_max:
            raise ValueError("composition gap must describe an invalid count")
        if self.reason == "transit_has_visit" and self.day_kind is not PlanningDayKind.TRANSIT_ONLY:
            raise ValueError("transit gap requires transit-only day")
        return self


class PaceCapacityGap(_GapBase):
    gap_type: Literal["pace_capacity"] = "pace_capacity"
    date: date
    scope: Literal["day_total", "single_leg"]
    actual_seconds: Annotated[int, Field(ge=0)]
    limit_seconds: Annotated[int, Field(ge=1)]
    leg_id: StableId | None

    @model_validator(mode="after")
    def _leg_scope(self) -> PaceCapacityGap:
        if (self.scope == "single_leg") != (self.leg_id is not None):
            raise ValueError("pace leg id must match scope")
        if self.actual_seconds <= self.limit_seconds:
            raise ValueError("pace gap must exceed its limit")
        return self


class RouteUnavailableGap(_GapBase):
    gap_type: Literal["route_unavailable"] = "route_unavailable"
    date: date
    origin: PlanningLocationRef
    destination: PlanningLocationRef
    route_mode: TravelMode


class ScheduleConflictGap(_GapBase):
    gap_type: Literal["schedule_conflict"] = "schedule_conflict"
    date: date
    visit_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=8)]
    rule_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=8)]
    reason: Literal["overlap", "outside_window", "boundary_transfer", "cross_midnight"]


class TransportConstraintGap(_GapBase):
    gap_type: Literal["transport_constraint"] = "transport_constraint"
    date: date | None
    leg_id: StableId | None
    semantic: TransportSemantic
    rule_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=8)]
    reason: Literal["unsupported_mode", "hard_conflict", "unverified_service"]


class OpeningHoursConflictGap(_GapBase):
    gap_type: Literal["opening_hours_conflict"] = "opening_hours_conflict"
    date: date
    candidate: CandidateLocationRef
    visit_kind: VisitKind


class BackupUnavailableGap(_GapBase):
    gap_type: Literal["backup_unavailable"] = "backup_unavailable"
    date: date
    backup_kind: Literal["attraction", "lunch", "dinner"]
    core_visit_id: StableId
    reason: Literal["no_eligible", "no_nearby", "business_hours"]


class CandidateSupplyGap(_GapBase):
    gap_type: Literal["candidate_supply"] = "candidate_supply"
    date: date | None
    target: CandidateSupplementTarget

    @model_validator(mode="after")
    def _evidence(self) -> CandidateSupplyGap:
        if not self.evidence_refs or any(ref.artifact_type != "candidate_research.place_observation" or ref.schema_version != "1.0" for ref in self.evidence_refs):
            raise ValueError("candidate supply gap requires candidate-research evidence refs")
        return self


class UpstreamResearchGap(_GapBase):
    gap_type: Literal["upstream_research"] = "upstream_research"
    upstream_gap_id: StableId
    relevance: Literal["hard_blocking"]


class PlanningProcessGap(_GapBase):
    gap_type: Literal["planning_process"] = "planning_process"
    process_reason: Literal["direction_budget_exhausted", "iteration_limit_reached", "no_progress", "no_legal_action", "no_terminal_submission"]


PlanningGap: TypeAlias = Annotated[
    HubResolutionGap | DailyTimePolicyClarificationGap | TimeSensitiveMustUnverifiedGap
    | MustVisitUnscheduledGap | MealSlotUnfilledGap | DayCompositionGap | PaceCapacityGap
    | RouteUnavailableGap | ScheduleConflictGap | TransportConstraintGap | OpeningHoursConflictGap
    | BackupUnavailableGap | CandidateSupplyGap | UpstreamResearchGap | PlanningProcessGap,
    Field(discriminator="gap_type"),
]


class ArtifactEvidenceIndex(_Model):
    schema_version: Literal["1.0"]
    directory: Literal["hotel_selection", "candidate_set", "candidate_evidence", "hub_geocode", "matrix", "directions"]
    index: Annotated[int, Field(ge=0)]


class _GapSummaryBase(_Model):
    schema_version: Literal["1.0"] = "1.0"
    summary_type: str
    gap_id: StableId
    evidence_indexes: Annotated[tuple[ArtifactEvidenceIndex, ...], Field(min_length=0, max_length=8)]


# Explicit summary branches keep the persisted discriminator closed while sharing
# the public gap field definitions through generated class bodies.
class HubResolutionGapSummary(_GapSummaryBase):
    summary_type: Literal["hub_resolution"] = "hub_resolution"
    endpoint_kind: Literal["arrival", "departure"]
    status: Literal["ambiguous", "no_result"]
    candidate_count: Annotated[int, Field(ge=0, le=10)]


class DailyTimePolicyClarificationGapSummary(_GapSummaryBase):
    summary_type: Literal["daily_time_policy_clarification"] = "daily_time_policy_clarification"
    source_field: SourceField
    reason: Literal["ambiguous", "conflicting", "unsupported_expression"]
    rule_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=16)]


class TimeSensitiveMustUnverifiedGapSummary(_GapSummaryBase):
    summary_type: Literal["time_sensitive_must_unverified"] = "time_sensitive_must_unverified"
    requirement_group_id: StableId
    date: date | None
    candidate_uid: CandidateUid | None
    reason: Literal["missing_time", "hard_conflict"]


class MustVisitUnscheduledGapSummary(_GapSummaryBase):
    summary_type: Literal["must_visit_unscheduled"] = "must_visit_unscheduled"
    requirement_group_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]
    candidate_uids: Annotated[tuple[CandidateUid, ...], Field(min_length=0, max_length=8)]
    reason: Literal["no_eligible", "capacity", "time", "route", "business_hours", "transport"]


class MealSlotUnfilledGapSummary(_GapSummaryBase):
    summary_type: Literal["meal_slot_unfilled"] = "meal_slot_unfilled"
    date: date
    meal: Literal["lunch", "dinner"]
    reason: Literal["no_candidate", "time", "route", "business_hours"]


class DayCompositionGapSummary(_GapSummaryBase):
    summary_type: Literal["day_composition"] = "day_composition"
    date: date
    day_kind: PlanningDayKind
    actual_attraction_count: Annotated[int, Field(ge=0, le=4)]
    required_min: Annotated[int, Field(ge=0, le=4)]
    required_max: Annotated[int, Field(ge=0, le=4)]
    reason: Literal["attraction_count", "transit_has_visit"]


class PaceCapacityGapSummary(_GapSummaryBase):
    summary_type: Literal["pace_capacity"] = "pace_capacity"
    date: date
    scope: Literal["day_total", "single_leg"]
    actual_seconds: Annotated[int, Field(ge=0)]
    limit_seconds: Annotated[int, Field(ge=1)]
    leg_id: StableId | None


class RouteUnavailableGapSummary(_GapSummaryBase):
    summary_type: Literal["route_unavailable"] = "route_unavailable"
    date: date
    origin: PlanningLocationRef
    destination: PlanningLocationRef
    route_mode: TravelMode


class ScheduleConflictGapSummary(_GapSummaryBase):
    summary_type: Literal["schedule_conflict"] = "schedule_conflict"
    date: date
    visit_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=8)]
    rule_ids: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=8)]
    reason: Literal["overlap", "outside_window", "boundary_transfer", "cross_midnight"]


class TransportConstraintGapSummary(_GapSummaryBase):
    summary_type: Literal["transport_constraint"] = "transport_constraint"
    date: date | None
    leg_id: StableId | None
    semantic: TransportSemantic
    rule_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=8)]
    reason: Literal["unsupported_mode", "hard_conflict", "unverified_service"]


class OpeningHoursConflictGapSummary(_GapSummaryBase):
    summary_type: Literal["opening_hours_conflict"] = "opening_hours_conflict"
    date: date
    candidate: CandidateLocationRef
    visit_kind: VisitKind


class BackupUnavailableGapSummary(_GapSummaryBase):
    summary_type: Literal["backup_unavailable"] = "backup_unavailable"
    date: date
    backup_kind: Literal["attraction", "lunch", "dinner"]
    core_visit_id: StableId
    reason: Literal["no_eligible", "no_nearby", "business_hours"]


class CandidateSupplyGapSummary(_GapSummaryBase):
    summary_type: Literal["candidate_supply"] = "candidate_supply"
    date: date | None
    target: CandidateSupplementTarget


class UpstreamResearchGapSummary(_GapSummaryBase):
    summary_type: Literal["upstream_research"] = "upstream_research"
    upstream_gap_id: StableId
    relevance: Literal["hard_blocking"]


class PlanningProcessGapSummary(_GapSummaryBase):
    summary_type: Literal["planning_process"] = "planning_process"
    process_reason: Literal["direction_budget_exhausted", "iteration_limit_reached", "no_progress", "no_legal_action", "no_terminal_submission"]


PlanningGapSummary: TypeAlias = Annotated[
    HubResolutionGapSummary | DailyTimePolicyClarificationGapSummary | TimeSensitiveMustUnverifiedGapSummary
    | MustVisitUnscheduledGapSummary | MealSlotUnfilledGapSummary | DayCompositionGapSummary
    | PaceCapacityGapSummary | RouteUnavailableGapSummary | ScheduleConflictGapSummary
    | TransportConstraintGapSummary | OpeningHoursConflictGapSummary | BackupUnavailableGapSummary
    | CandidateSupplyGapSummary | UpstreamResearchGapSummary | PlanningProcessGapSummary,
    Field(discriminator="summary_type"),
]


class PlanningBudgetSummary(_Model):
    schema_version: Literal["1.0"] = "1.0"
    iteration_limit: Literal[6] = 6
    iterations_consumed: Annotated[int, Field(ge=0, le=6)]
    direction_call_limit: Annotated[int, Field(ge=2)]
    direction_calls_consumed: Annotated[int, Field(ge=0)]
    budget_snapshot_complete: bool
    semantic_invocation_limit: Literal[12] = 12
    semantic_invocations_consumed: Annotated[int, Field(ge=0, le=12)]
    known_provider_attempts: Annotated[int, Field(ge=0, le=24)]
    provider_attempts_complete: bool
    known_usage: LLMUsage
    usage_complete: bool
    cache_hits: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _budget(self) -> PlanningBudgetSummary:
        if self.direction_calls_consumed > self.direction_call_limit:
            raise ValueError("direction budget exceeded")
        if self.known_provider_attempts < self.semantic_invocations_consumed:
            raise ValueError("provider attempts cannot be below invocations")
        return self


class PlanningSummary(_Model):
    schema_version: Literal["1.0"] = "1.0"
    effective_pace: TravelPace | None
    sightseeing_days: Annotated[int, Field(ge=1, le=60)]
    full_days: Annotated[int, Field(ge=0)]
    partial_days: Annotated[int, Field(ge=0)]
    transit_only_days: Annotated[int, Field(ge=0)]
    attraction_count: Annotated[int, Field(ge=0)]
    lunch_count: Annotated[int, Field(ge=0)]
    dinner_count: Annotated[int, Field(ge=0)]
    unique_main_candidates: Annotated[int, Field(ge=0, le=128)]
    boundary_transfers: Annotated[int, Field(ge=0, le=2)]
    route_legs: Annotated[int, Field(ge=0)]
    backups: Annotated[int, Field(ge=0)]
    unknown_hours: Annotated[int, Field(ge=0)]


class LLMPlanningFailure(_Model):
    schema_version: Literal["1.0"] = "1.0"
    failure_type: Literal["llm"] = "llm"
    error: LLMRuntimeError
    invocation_index: Annotated[int, Field(ge=0, le=11)]
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _flags(self) -> LLMPlanningFailure:
        if self.error.role.value != "itinerary_planning":
            raise ValueError("LLM failure must use the itinerary planning role")
        if self.retryable != self.error.retryable or self.possible_duplicate != self.error.possible_duplicate:
            raise ValueError("LLM failure flags must equal runtime error")
        return self


_MAP_RETRYABLE = {
    BaiduMapErrorCode.RATE_LIMIT_ERROR,
    BaiduMapErrorCode.TIMEOUT_ERROR,
    BaiduMapErrorCode.TRANSPORT_ERROR,
}


class MapPlanningFailure(_Model):
    schema_version: Literal["1.0"] = "1.0"
    failure_type: Literal["map"] = "map"
    capability: Literal["geocoding", "route_matrix", "directions"]
    error_code: BaiduMapErrorCode
    action_ordinal: Annotated[int, Field(ge=0)]
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _flags(self) -> MapPlanningFailure:
        if self.retryable != (self.error_code in _MAP_RETRYABLE) or self.possible_duplicate:
            raise ValueError("map failure flags must use the fixed error table")
        return self


_CONTROL_FLAGS = {
    PlanningFailureCode.ARTIFACT_ERROR: (True, False),
    PlanningFailureCode.BUDGET_ERROR: (True, False),
    PlanningFailureCode.OPERATION_RESULT_INDETERMINATE: (False, True),
}


class ControlPlanningFailure(_Model):
    schema_version: Literal["1.0"] = "1.0"
    failure_type: Literal["control"] = "control"
    code: PlanningFailureCode
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _flags(self) -> ControlPlanningFailure:
        expected = _CONTROL_FLAGS.get(self.code, (False, False))
        # A terminal Artifact write is the one documented exception: the
        # write may have reached storage before the repository reported an
        # error, so it is non-retryable but possibly duplicated.
        if self.code is PlanningFailureCode.ARTIFACT_ERROR and (self.retryable, self.possible_duplicate) == (False, True):
            return self
        if (self.retryable, self.possible_duplicate) != expected:
            raise ValueError("control failure flags must use the fixed error table")
        return self


PlanningFailure: TypeAlias = Annotated[
    LLMPlanningFailure | MapPlanningFailure | ControlPlanningFailure,
    Field(discriminator="failure_type"),
]


class ItineraryPlanningCompleted(_Model):
    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["completed"] = "completed"
    itinerary_ref: ArtifactRef
    summary: PlanningSummary
    budgets: PlanningBudgetSummary

    @model_validator(mode="after")
    def _ref(self) -> ItineraryPlanningCompleted:
        _require_itinerary_ref(self.itinerary_ref, required=True)
        return self


class ItineraryPlanningIncomplete(_Model):
    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["incomplete"] = "incomplete"
    itinerary_ref: ArtifactRef | None
    summary: PlanningSummary
    budgets: PlanningBudgetSummary
    gaps: Annotated[tuple[PlanningGap, ...], Field(min_length=1, max_length=128)]
    stop_reasons: Annotated[tuple[PlanningStopReason, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _stop_order(self) -> ItineraryPlanningIncomplete:
        _require_itinerary_ref(self.itinerary_ref)
        order = {item: index for index, item in enumerate(PlanningStopReason)}
        if tuple(sorted(set(self.stop_reasons), key=order.get)) != self.stop_reasons:
            raise ValueError("stop reasons must be unique and stable ordered")
        return self


class ItineraryPlanningCandidateSupplementRequested(_Model):
    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["candidate_supplement_requested"] = "candidate_supplement_requested"
    planning_ref: ArtifactRef
    candidate_set_ref: ArtifactRef
    gaps: Annotated[tuple[PlanningGap, ...], Field(min_length=1, max_length=32)]
    gap_fingerprint: ContentDigest
    budgets: PlanningBudgetSummary

    @model_validator(mode="after")
    def _refs(self) -> ItineraryPlanningCandidateSupplementRequested:
        _require_itinerary_ref(self.planning_ref, required=True)
        if self.candidate_set_ref.artifact_type != "candidate_research.candidate_set" or self.candidate_set_ref.schema_version != "1.0":
            raise ValueError("supplement outcome must reference candidate set")
        if self.planning_ref.operation_id.run_id != self.candidate_set_ref.operation_id.run_id:
            raise ValueError("supplement outcome refs must share run")
        return self


class ItineraryPlanningFailed(_Model):
    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["failed"] = "failed"
    failure: PlanningFailure
    last_consistent_itinerary_ref: ArtifactRef | None
    failure_artifact_ref: ArtifactRef | None
    budgets: PlanningBudgetSummary | None
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _flags(self) -> ItineraryPlanningFailed:
        _require_itinerary_ref(self.last_consistent_itinerary_ref)
        if self.failure_artifact_ref is not None:
            allowed = {
                "itinerary_planning.model_action",
                "itinerary_planning.hub_geocode_observation",
                "itinerary_planning.route_matrix_observation",
                "itinerary_planning.directions_observation",
                "itinerary_planning.itinerary",
            }
            if self.failure_artifact_ref.artifact_type not in allowed or self.failure_artifact_ref.schema_version != "1.0":
                raise ValueError("failure artifact ref must be a Module 09 artifact")
        if (self.retryable, self.possible_duplicate) != (self.failure.retryable, self.failure.possible_duplicate):
            raise ValueError("top-level failure flags must equal failure")
        return self


ItineraryPlanningOutcome: TypeAlias = Annotated[
    ItineraryPlanningCompleted | ItineraryPlanningIncomplete
    | ItineraryPlanningCandidateSupplementRequested | ItineraryPlanningFailed,
    Field(discriminator="outcome_type"),
]


__all__ = (
    "ITINERARY_PLANNING_SCHEMA_VERSION", "ITINERARY_PLANNING_ITERATION_BUDGET_KEY",
    "ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY", "PlanningDayKind", "VisitKind",
    "BusinessHoursState", "PlanningTool", "PlanningStopReason", "TransportSemantic",
    "PlanningFailureCode", "PlanningIterationOperationIds", "ItineraryPlanningOperationPlan",
    "ItineraryPlanningInitialRequest", "ItineraryPlanningResumeRequest", "CandidateLocationRef",
    "HotelLocationRef", "HubLocationRef", "PlanningLocationRef", "TimeInterval",
    "RouteTimeInterval", "DailyTimeRule", "TimeSensitiveMustAnchor", "TransportPolicyRule",
    "EffectivePlanningPolicy", "ScheduledVisit", "RouteAlternativeSummary", "RouteLeg",
    "BoundaryTransfer", "BackupOption", "DayPlan", "ItineraryDraft", "PlanningGap",
    "PlanningGapSummary", "PlanningBudgetSummary", "PlanningSummary", "LLMPlanningFailure",
    "MapPlanningFailure", "ControlPlanningFailure", "PlanningFailure", "ItineraryPlanningCompleted",
    "ItineraryPlanningIncomplete", "ItineraryPlanningCandidateSupplementRequested",
    "ItineraryPlanningFailed", "ItineraryPlanningOutcome", "GetCandidateEvidenceArgs",
    "GeocodeHubArgs", "CalculateRouteMatrixArgs", "PlanDirectionsArgs", "SubmitItineraryArgs",
    "RequestCandidateSupplementArgs",
)
