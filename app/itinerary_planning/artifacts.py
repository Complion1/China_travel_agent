"""Module 09 的独立 Artifact snapshot 图与显式注册表。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import warnings
from typing import Annotated, Literal, TypeAlias, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from app.baidu_map import (
    BaiduCoordinate,
    BaiduMapErrorCode,
    MapCapability,
    MapProvenance,
    MatrixTravelMode,
    RouteMatrixCell,
    TravelMode,
)
from app.llm_runtime import InvocationMetadata, LLMErrorCode, LLMRuntimeError, LLMUsage, ModelRole
from app.requirements import TravelPace
from app.workflow_contracts import ArtifactRef, ArtifactSchemaRegistration, WorkflowPhase

from .models import (
    ArtifactEvidenceIndex as PublicArtifactEvidenceIndex,
    AreaCoverageTarget,
    AvoidResolutionTarget,
    BackupOption,
    BackupUnavailableGapSummary,
    BoundaryTransfer,
    BusinessHoursState,
    CandidateCountTarget,
    CandidateDetailsTarget,
    CandidateLocationRef,
    CandidateSupplyGapSummary,
    ContentDigest,
    DailyTimePolicyClarificationGapSummary,
    DailyTimeRule,
    DayCompositionGapSummary,
    DayPlan,
    EffectivePlanningPolicy,
    HubLocationRef,
    HubResolutionGapSummary,
    HotelLocationRef,
    ItineraryDraft,
    LLMPlanningFailure,
    MapPlanningFailure,
    MealCoverageTarget,
    MealSlotUnfilledGapSummary,
    MustResolutionTarget,
    MustVisitUnscheduledGapSummary,
    OpeningHoursConflictGapSummary,
    PaceCapacityGapSummary,
    PlanningBudgetSummary,
    PlanningDayKind,
    PlanningFailure,
    PlanningFailureCode,
    PlanningGap,
    PlanningGapSummary,
    PlanningLocationRef,
    PlanningProcessGapSummary,
    PlanningStopReason,
    PlanningSummary,
    ReplacementTarget,
    RouteAlternativeSummary,
    RouteLeg,
    RouteTimeInterval,
    RouteUnavailableGapSummary,
    ScheduleConflictGapSummary,
    ScheduledVisit,
    TimeInterval,
    TimeSensitiveMustAnchor,
    TimeSensitiveMustUnverifiedGapSummary,
    TransportConstraintGapSummary,
    TransportPolicyRule,
    TransportSemantic,
    UpstreamResearchGapSummary,
    VisitKind,
)
from .models import PlanningTool
from app.candidate_research import MealIntent, ResearchRole


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_UTC = timezone.utc
ArtifactStableId = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=128)]
ArtifactCandidateUid = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=512)]
ArtifactDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class _ArtifactModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    @model_validator(mode="after")
    def _reject_unsafe_text_and_duplicate_index_tuples(self):
        def walk(value: object) -> None:
            if isinstance(value, str) and value != value.strip():
                raise ValueError("artifact text must be normalized")
            if isinstance(value, BaseModel):
                walk(value.model_dump(mode="python"))
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, (tuple, list)):
                for item in value:
                    walk(item)

        tree = self.model_dump(mode="python")
        walk(tree)
        for key, value in tree.items():
            if key.endswith("_indexes") and isinstance(value, (tuple, list)):
                try:
                    unique = len(set(value))
                except TypeError:
                    unique = len({repr(item) for item in value})
                if unique != len(value):
                    raise ValueError("artifact index tuples must be unique")
        return self


class ModelCallSummary(_ArtifactModel):
    schema_version: Literal["1.0"]
    call_id: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=256)]
    ordinal: Annotated[int, Field(ge=0, le=70)]
    tool: PlanningTool
    arguments_fingerprint: ArtifactDigest
    handle_count: Annotated[int, Field(ge=0, le=6000)]
    handles_digest: ArtifactDigest
    handle_preview: Annotated[tuple[str, ...], Field(min_length=0, max_length=16)]

    @model_validator(mode="after")
    def _preview(self) -> ModelCallSummary:
        if len(self.handle_preview) != min(16, self.handle_count):
            raise ValueError("handle preview must contain the first sixteen handles")
        return self


class BaiduCoordinateSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    coordinate_system: Literal["bd09ll"]
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class MapProvenanceSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    source: Literal["baidu_maps_mcp"]
    capability: MapCapability
    retrieved_at_utc: datetime
    verified: bool

    @model_validator(mode="after")
    def _utc(self) -> MapProvenanceSnapshot:
        if self.retrieved_at_utc.tzinfo is None or self.retrieved_at_utc.utcoffset() != timedelta(0):
            raise ValueError("map provenance must use UTC")
        return self


class RouteMatrixCellSuccessSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    status: Literal["success"]
    origin_index: Annotated[int, Field(ge=0, le=99)]
    destination_index: Annotated[int, Field(ge=0, le=99)]
    distance_m: Annotated[int, Field(ge=0)]
    duration_s: Annotated[int, Field(ge=0)]


class RouteMatrixCellNoResultSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    status: Literal["no_result"]
    origin_index: Annotated[int, Field(ge=0, le=99)]
    destination_index: Annotated[int, Field(ge=0, le=99)]


RouteMatrixCellSnapshot: TypeAlias = Annotated[
    RouteMatrixCellSuccessSnapshot | RouteMatrixCellNoResultSnapshot,
    Field(discriminator="status"),
]


class CandidateLocationRefSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    ref_type: Literal["candidate"]
    admission_ordinal: Annotated[int, Field(ge=0, le=127)]
    uid: ArtifactCandidateUid


class HotelLocationRefSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    ref_type: Literal["hotel"]
    hotel_selection_ref_index: Literal[0]


class HubLocationRefSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    ref_type: Literal["hub"]
    endpoint_kind: Literal["arrival", "departure"]
    geocode_observation_index: Annotated[int, Field(ge=0, le=1)]


PlanningLocationRefSnapshot: TypeAlias = Annotated[
    CandidateLocationRefSnapshot | HotelLocationRefSnapshot | HubLocationRefSnapshot,
    Field(discriminator="ref_type"),
]


class HubGeocodeCandidateSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    formatted_address: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=512)]
    coordinate: BaiduCoordinateSnapshot
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None


class ArtifactEvidenceIndex(_ArtifactModel):
    schema_version: Literal["1.0"]
    directory: Literal[
        "hotel_selection", "candidate_set", "candidate_evidence",
        "hub_geocode", "matrix", "directions",
    ]
    index: Annotated[int, Field(ge=0)]


class PersistedTimeInterval(_ArtifactModel):
    schema_version: Literal["1.0"]
    start_at_utc: datetime
    end_at_utc: datetime
    timezone: Literal["Asia/Shanghai"]
    local_start_date: date
    local_end_date: date

    @model_validator(mode="after")
    def _utc_and_dates(self) -> PersistedTimeInterval:
        if self.start_at_utc.tzinfo is None or self.start_at_utc.utcoffset() != timedelta(0):
            raise ValueError("persisted start must be UTC")
        if self.end_at_utc.tzinfo is None or self.end_at_utc.utcoffset() != timedelta(0):
            raise ValueError("persisted end must be UTC")
        if self.start_at_utc >= self.end_at_utc:
            raise ValueError("persisted interval must be non-empty")
        if self.start_at_utc.astimezone(_SHANGHAI).date() != self.local_start_date:
            raise ValueError("persisted start date mismatch")
        if self.end_at_utc.astimezone(_SHANGHAI).date() != self.local_end_date:
            raise ValueError("persisted end date mismatch")
        return self


class PersistedRouteTimeInterval(_ArtifactModel):
    schema_version: Literal["1.0"]
    start_at_utc: datetime
    end_at_utc: datetime
    timezone: Literal["Asia/Shanghai"]
    local_start_date: date
    local_end_date: date

    @model_validator(mode="after")
    def _utc_and_dates(self) -> PersistedRouteTimeInterval:
        if self.start_at_utc.tzinfo is None or self.start_at_utc.utcoffset() != timedelta(0):
            raise ValueError("persisted route start must be UTC")
        if self.end_at_utc.tzinfo is None or self.end_at_utc.utcoffset() != timedelta(0):
            raise ValueError("persisted route end must be UTC")
        if self.start_at_utc > self.end_at_utc:
            raise ValueError("persisted route interval must be ordered")
        if self.start_at_utc.astimezone(_SHANGHAI).date() != self.local_start_date:
            raise ValueError("persisted route start date mismatch")
        if self.end_at_utc.astimezone(_SHANGHAI).date() != self.local_end_date:
            raise ValueError("persisted route end date mismatch")
        return self


class DailyTimeRuleSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    rule_id: ArtifactStableId
    date: date | None
    interval: PersistedTimeInterval
    strength: Literal["hard", "soft"]
    source_field: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=256)]
    source_digest: ArtifactDigest


class TimeSensitiveMustAnchorSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    anchor_id: ArtifactStableId
    requirement_group_id: ArtifactStableId
    local_date: date
    event_at_utc: datetime
    timezone: Literal["Asia/Shanghai"]
    source: Literal["user_provided", "verified_evidence"]
    source_ref_index: Annotated[int, Field(ge=0, le=127)] | None

    @model_validator(mode="after")
    def _event(self) -> TimeSensitiveMustAnchorSnapshot:
        if self.event_at_utc.tzinfo is None or self.event_at_utc.utcoffset() != timedelta(0):
            raise ValueError("anchor event must be UTC")
        if self.event_at_utc.astimezone(_SHANGHAI).date() != self.local_date:
            raise ValueError("anchor local date mismatch")
        if (self.source == "user_provided") != (self.source_ref_index is None):
            raise ValueError("anchor evidence index mismatch")
        return self


class TransportPolicyRuleSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    rule_id: ArtifactStableId
    semantic: TransportSemantic
    strength: Literal["hard", "soft"]
    numeric_limit_minutes: Annotated[int, Field(ge=1, le=1440)] | None
    source_field: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=256)]
    source_digest: ArtifactDigest


class EffectivePlanningPolicySnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    effective_pace: TravelPace
    daily_time_rules: Annotated[tuple[DailyTimeRuleSnapshot, ...], Field(min_length=0, max_length=64)]
    time_sensitive_must: Annotated[tuple[TimeSensitiveMustAnchorSnapshot, ...], Field(min_length=0, max_length=100)]
    transport_rules: Annotated[tuple[TransportPolicyRuleSnapshot, ...], Field(min_length=0, max_length=32)]
    locked: Literal[True]

    @model_validator(mode="after")
    def _unique_rules(self) -> EffectivePlanningPolicySnapshot:
        if len({item.rule_id for item in self.daily_time_rules}) != len(self.daily_time_rules):
            raise ValueError("daily rule ids must be unique")
        if len({item.anchor_id for item in self.time_sensitive_must}) != len(self.time_sensitive_must):
            raise ValueError("anchor ids must be unique")
        if len({item.rule_id for item in self.transport_rules}) != len(self.transport_rules):
            raise ValueError("transport rule ids must be unique")
        return self


class ScheduledVisitSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    visit_id: ArtifactStableId
    location: CandidateLocationRefSnapshot
    visit_kind: VisitKind
    interval: PersistedTimeInterval
    duration_source: Literal["user_explicit", "planning_estimate"]
    must_group_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=0, max_length=100)]
    time_sensitive_anchor_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=0, max_length=8)]
    business_hours_state: BusinessHoursState
    business_evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=127)], ...], Field(min_length=0, max_length=8)]
    requires_verification: bool

    @model_validator(mode="after")
    def _visit(self) -> ScheduledVisitSnapshot:
        for values in (self.must_group_ids, self.time_sensitive_anchor_ids, self.business_evidence_indexes):
            if len(set(values)) != len(values):
                raise ValueError("scheduled visit ids/indexes must be unique")
        if self.business_hours_state is BusinessHoursState.CONFLICTS:
            raise ValueError("conflicting business hours cannot be scheduled")
        if self.business_hours_state is BusinessHoursState.UNKNOWN and not self.requires_verification:
            raise ValueError("unknown business hours require verification")
        return self


def _route_minutes(duration_s: int) -> int:
    return 0 if duration_s == 0 else ((duration_s + 1799) // 1800) * 30


class RouteLegSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    leg_id: ArtifactStableId
    origin: PlanningLocationRefSnapshot
    destination: PlanningLocationRefSnapshot
    interval: PersistedRouteTimeInterval
    transport_semantic: TransportSemantic
    route_mode: TravelMode
    directions_observation_index: Annotated[int, Field(ge=0, le=481)]
    alternative_index: Annotated[int, Field(ge=0, le=2)]
    distance_m: Annotated[int, Field(ge=0)]
    duration_s: Annotated[int, Field(ge=0)]
    scheduled_minutes: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _route(self) -> RouteLegSnapshot:
        expected_mode = {
            TransportSemantic.WALKING: TravelMode.WALKING,
            TransportSemantic.CYCLING: TravelMode.RIDING,
            TransportSemantic.PUBLIC_TRANSIT: TravelMode.TRANSIT,
            TransportSemantic.RIDE_HAILING: TravelMode.DRIVING,
            TransportSemantic.PRIVATE_CAR: TravelMode.DRIVING,
        }[self.transport_semantic]
        if self.route_mode is not expected_mode:
            raise ValueError("snapshot route semantic and mode mismatch")
        if self.scheduled_minutes != _route_minutes(self.duration_s):
            raise ValueError("snapshot route minutes are not the exact ceiling")
        actual = int((self.interval.end_at_utc - self.interval.start_at_utc).total_seconds() // 60)
        if actual != self.scheduled_minutes:
            raise ValueError("snapshot route interval length mismatch")
        return self


class BoundaryTransferSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    transfer_kind: Literal["arrival", "departure"]
    hub: HubLocationRefSnapshot
    hotel: HotelLocationRefSnapshot
    route_leg: RouteLegSnapshot
    processing_minutes: Annotated[int, Field(ge=30)]
    approximate_extra_minutes: Literal[0, 30]

    @model_validator(mode="after")
    def _refs(self) -> BoundaryTransferSnapshot:
        if self.transfer_kind == "arrival" and (self.route_leg.origin != self.hub or self.route_leg.destination != self.hotel):
            raise ValueError("arrival snapshot must be hub to hotel")
        if self.transfer_kind == "departure" and (self.route_leg.origin != self.hotel or self.route_leg.destination != self.hub):
            raise ValueError("departure snapshot must be hotel to hub")
        return self


class BackupOptionSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    backup_kind: Literal["attraction", "lunch", "dinner"]
    core_visit_id: ArtifactStableId
    candidate: CandidateLocationRefSnapshot
    matrix_observation_index: Annotated[int, Field(ge=0, le=359)]
    matrix_mode: MatrixTravelMode
    distance_m: Annotated[int, Field(ge=0, le=3000)]
    business_hours_state: BusinessHoursState
    business_evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=127)], ...], Field(min_length=0, max_length=8)]
    requires_verification: bool

    @model_validator(mode="after")
    def _backup(self) -> BackupOptionSnapshot:
        if len(set(self.business_evidence_indexes)) != len(self.business_evidence_indexes):
            raise ValueError("backup evidence indexes must be unique")
        if self.business_hours_state is BusinessHoursState.CONFLICTS:
            raise ValueError("conflicting business hours cannot be a backup")
        if self.business_hours_state is BusinessHoursState.UNKNOWN and not self.requires_verification:
            raise ValueError("unknown backup hours require verification")
        return self


class DayPlanSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    date: date
    day_kind: PlanningDayKind
    theme: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=40)]
    open_intervals: Annotated[tuple[PersistedTimeInterval, ...], Field(min_length=0, max_length=16)]
    visits: Annotated[tuple[ScheduledVisitSnapshot, ...], Field(min_length=0, max_length=6)]
    route_legs: Annotated[tuple[RouteLegSnapshot, ...], Field(min_length=0, max_length=7)]
    backups: Annotated[tuple[BackupOptionSnapshot, ...], Field(min_length=0, max_length=3)]

    @model_validator(mode="after")
    def _day(self) -> DayPlanSnapshot:
        if self.day_kind is PlanningDayKind.TRANSIT_ONLY:
            if self.open_intervals or self.visits or self.route_legs or self.backups:
                raise ValueError("transit-only snapshot cannot contain sightseeing content")
            return self
        if not self.open_intervals or not self.visits or len(self.route_legs) != len(self.visits) + 1:
            raise ValueError("non-transit snapshot must contain an open hotel-loop day")
        if len({item.visit_id for item in self.visits}) != len(self.visits) or len({item.location.uid for item in self.visits}) != len(self.visits):
            raise ValueError("snapshot visit ids and candidate uids must be unique")
        if len({item.candidate.uid for item in self.backups}) != len(self.backups):
            raise ValueError("snapshot backup uids must be unique")
        if {item.candidate.uid for item in self.backups} & {item.location.uid for item in self.visits}:
            raise ValueError("backup cannot reuse a main candidate")
        first, *middle, last = self.route_legs
        if first.origin.ref_type != "hotel" or last.destination.ref_type != "hotel":
            raise ValueError("day route must start and end at hotel")
        if first.destination != self.visits[0].location or last.origin != self.visits[-1].location:
            raise ValueError("day route endpoints do not match visits")
        if first.interval.end_at_utc > self.visits[0].interval.start_at_utc or last.interval.start_at_utc < self.visits[-1].interval.end_at_utc:
            raise ValueError("boundary route does not fit visits")
        for route, left_visit, right_visit in zip(self.route_legs[1:-1], self.visits[:-1], self.visits[1:], strict=True):
            if route.origin != left_visit.location or route.destination != right_visit.location:
                raise ValueError("day route legs are not in visit order")
            if route.interval.start_at_utc < left_visit.interval.end_at_utc or route.interval.end_at_utc > right_visit.interval.start_at_utc:
                raise ValueError("intermediate route must fit between visits")
        for interval in self.open_intervals:
            if interval.local_start_date != self.date or interval.local_end_date not in {self.date, self.date + timedelta(days=1)}:
                raise ValueError("open interval must belong to plan date or its following local date")
        next_date = self.date + timedelta(days=1)
        for interval in (item.interval for item in self.visits):
            if interval.local_start_date != self.date or interval.local_end_date not in {self.date, next_date}:
                raise ValueError("visit interval must belong to plan date or its following local date")
        for interval in (item.interval for item in self.route_legs):
            if interval.local_start_date not in {self.date, next_date} or interval.local_end_date not in {self.date, next_date}:
                raise ValueError("route interval must belong to plan date or its following local date")
        for visit in self.visits:
            if not any(item.start_at_utc <= visit.interval.start_at_utc and visit.interval.end_at_utc <= item.end_at_utc for item in self.open_intervals):
                raise ValueError("visit is outside day open intervals")
        for leg in self.route_legs:
            if leg.interval.start_at_utc != leg.interval.end_at_utc and not any(item.start_at_utc <= leg.interval.start_at_utc and leg.interval.end_at_utc <= item.end_at_utc for item in self.open_intervals):
                raise ValueError("route is outside day open intervals")
        for left, right in zip(self.visits, self.visits[1:]):
            if left.interval.end_at_utc > right.interval.start_at_utc:
                raise ValueError("visits overlap")
        for left, right in zip(self.route_legs, self.route_legs[1:]):
            if left.interval.end_at_utc > right.interval.start_at_utc:
                raise ValueError("route legs overlap")
        return self


class ItineraryDraftSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    requirement_digest: ArtifactDigest
    hotel_selection_ref: ArtifactRef
    candidate_set_ref: ArtifactRef
    effective_policy: EffectivePlanningPolicySnapshot
    arrival_transfer: BoundaryTransferSnapshot
    days: Annotated[tuple[DayPlanSnapshot, ...], Field(min_length=1, max_length=60)]
    departure_transfer: BoundaryTransferSnapshot
    upstream_gap_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=0, max_length=128)]

    @model_validator(mode="after")
    def _draft(self) -> ItineraryDraftSnapshot:
        if self.arrival_transfer.transfer_kind != "arrival" or self.departure_transfer.transfer_kind != "departure":
            raise ValueError("draft must contain one arrival and one departure transfer")
        if self.arrival_transfer.hotel != self.departure_transfer.hotel:
            raise ValueError("boundary transfers must use one hotel reference")
        dates = tuple(item.date for item in self.days)
        if dates != tuple(dates[0] + timedelta(days=index) for index in range(len(dates))):
            raise ValueError("draft days must be consecutive and ordered")
        if len(set(self.upstream_gap_ids)) != len(self.upstream_gap_ids):
            raise ValueError("upstream gap ids must be unique")
        return self


class CandidateCountTargetSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    target_type: Literal["candidate_count"]
    role: ResearchRole
    current_count: Annotated[int, Field(ge=0)]
    target_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _target(self) -> CandidateCountTargetSnapshot:
        if self.current_count >= self.target_count:
            raise ValueError("candidate target must increase supply")
        return self


class AreaCoverageTargetSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    target_type: Literal["area_coverage"]
    role: ResearchRole
    anchor: CandidateLocationRefSnapshot | HotelLocationRefSnapshot
    radius_km: Literal[1, 3, 5, 10]


class MealCoverageTargetSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    target_type: Literal["meal_coverage"]
    meal_intent: MealIntent
    anchor: CandidateLocationRefSnapshot | HotelLocationRefSnapshot
    radius_km: Literal[1, 3, 5, 10]


class MustResolutionTargetSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    target_type: Literal["must_resolution"]
    requirement_group_id: ArtifactStableId
    requirement_item_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=1, max_length=100)]


class AvoidResolutionTargetSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    target_type: Literal["avoid_resolution"]
    requirement_group_id: ArtifactStableId
    requirement_item_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=1, max_length=100)]
    candidate_uids: Annotated[tuple[ArtifactCandidateUid, ...], Field(min_length=1, max_length=128)]


class CandidateDetailsTargetSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    target_type: Literal["candidate_details"]
    candidate_uids: Annotated[tuple[ArtifactCandidateUid, ...], Field(min_length=1, max_length=128)]


class ReplacementTargetSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    target_type: Literal["replacement"]
    rejected_uid: ArtifactCandidateUid
    role: ResearchRole
    anchor: CandidateLocationRefSnapshot | HotelLocationRefSnapshot | None
    radius_km: Literal[1, 3, 5, 10] | None

    @model_validator(mode="after")
    def _anchor(self) -> ReplacementTargetSnapshot:
        if (self.anchor is None) != (self.radius_km is None):
            raise ValueError("replacement anchor and radius must be paired")
        return self


CandidateSupplementTargetSnapshot: TypeAlias = Annotated[
    CandidateCountTargetSnapshot | AreaCoverageTargetSnapshot | MealCoverageTargetSnapshot
    | MustResolutionTargetSnapshot | AvoidResolutionTargetSnapshot | CandidateDetailsTargetSnapshot
    | ReplacementTargetSnapshot,
    Field(discriminator="target_type"),
]


class _ArtifactGapSummaryBase(_ArtifactModel):
    schema_version: Literal["1.0"]
    summary_type: str
    gap_id: ArtifactStableId
    evidence_indexes: Annotated[tuple[ArtifactEvidenceIndex, ...], Field(min_length=0, max_length=8)]


class HubResolutionGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["hub_resolution"]
    endpoint_kind: Literal["arrival", "departure"]
    status: Literal["ambiguous", "no_result"]
    candidate_count: Annotated[int, Field(ge=0, le=10)]


class DailyTimePolicyClarificationGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["daily_time_policy_clarification"]
    source_field: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=256)]
    reason: Literal["ambiguous", "conflicting", "unsupported_expression"]
    rule_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=0, max_length=16)]


class TimeSensitiveMustUnverifiedGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["time_sensitive_must_unverified"]
    requirement_group_id: ArtifactStableId
    date: date | None
    candidate_uid: ArtifactCandidateUid | None
    reason: Literal["missing_time", "hard_conflict"]


class MustVisitUnscheduledGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["must_visit_unscheduled"]
    requirement_group_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=1, max_length=100)]
    candidate_uids: Annotated[tuple[ArtifactCandidateUid, ...], Field(min_length=0, max_length=8)]
    reason: Literal["no_eligible", "capacity", "time", "route", "business_hours", "transport"]


class MealSlotUnfilledGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["meal_slot_unfilled"]
    date: date
    meal: Literal["lunch", "dinner"]
    reason: Literal["no_candidate", "time", "route", "business_hours"]


class DayCompositionGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["day_composition"]
    date: date
    day_kind: PlanningDayKind
    actual_attraction_count: Annotated[int, Field(ge=0, le=4)]
    required_min: Annotated[int, Field(ge=0, le=4)]
    required_max: Annotated[int, Field(ge=0, le=4)]
    reason: Literal["attraction_count", "transit_has_visit"]


class PaceCapacityGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["pace_capacity"]
    date: date
    scope: Literal["day_total", "single_leg"]
    actual_seconds: Annotated[int, Field(ge=0)]
    limit_seconds: Annotated[int, Field(ge=1)]
    leg_id: ArtifactStableId | None


class RouteUnavailableGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["route_unavailable"]
    date: date
    origin: PlanningLocationRefSnapshot
    destination: PlanningLocationRefSnapshot
    route_mode: TravelMode


class ScheduleConflictGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["schedule_conflict"]
    date: date
    visit_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=0, max_length=8)]
    rule_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=0, max_length=8)]
    reason: Literal["overlap", "outside_window", "boundary_transfer", "cross_midnight"]


class TransportConstraintGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["transport_constraint"]
    date: date | None
    leg_id: ArtifactStableId | None
    semantic: TransportSemantic
    rule_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=1, max_length=8)]
    reason: Literal["unsupported_mode", "hard_conflict", "unverified_service"]


class OpeningHoursConflictGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["opening_hours_conflict"]
    date: date
    candidate: CandidateLocationRefSnapshot
    visit_kind: VisitKind


class BackupUnavailableGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["backup_unavailable"]
    date: date
    backup_kind: Literal["attraction", "lunch", "dinner"]
    core_visit_id: ArtifactStableId
    reason: Literal["no_eligible", "no_nearby", "business_hours"]


class CandidateSupplyGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["candidate_supply"]
    date: date | None
    target: CandidateSupplementTargetSnapshot


class UpstreamResearchGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["upstream_research"]
    upstream_gap_id: ArtifactStableId
    relevance: Literal["hard_blocking"]


class PlanningProcessGapSummarySnapshot(_ArtifactGapSummaryBase):
    summary_type: Literal["planning_process"]
    process_reason: Literal[
        "direction_budget_exhausted", "iteration_limit_reached", "no_progress",
        "no_legal_action", "no_terminal_submission",
    ]


PlanningGapSummarySnapshot: TypeAlias = Annotated[
    HubResolutionGapSummarySnapshot | DailyTimePolicyClarificationGapSummarySnapshot
    | TimeSensitiveMustUnverifiedGapSummarySnapshot | MustVisitUnscheduledGapSummarySnapshot
    | MealSlotUnfilledGapSummarySnapshot | DayCompositionGapSummarySnapshot
    | PaceCapacityGapSummarySnapshot | RouteUnavailableGapSummarySnapshot
    | ScheduleConflictGapSummarySnapshot | TransportConstraintGapSummarySnapshot
    | OpeningHoursConflictGapSummarySnapshot | BackupUnavailableGapSummarySnapshot
    | CandidateSupplyGapSummarySnapshot | UpstreamResearchGapSummarySnapshot
    | PlanningProcessGapSummarySnapshot,
    Field(discriminator="summary_type"),
]


class PlanningBudgetSummarySnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    iteration_limit: Literal[6]
    iterations_consumed: Annotated[int, Field(ge=0, le=6)]
    direction_call_limit: Annotated[int, Field(ge=2)]
    direction_calls_consumed: Annotated[int, Field(ge=0)]
    budget_snapshot_complete: bool
    semantic_invocation_limit: Literal[12]
    semantic_invocations_consumed: Annotated[int, Field(ge=0, le=12)]
    known_provider_attempts: Annotated[int, Field(ge=0, le=24)]
    provider_attempts_complete: bool
    known_usage: LLMUsage
    usage_complete: bool
    cache_hits: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _budget(self) -> PlanningBudgetSummarySnapshot:
        if self.direction_calls_consumed > self.direction_call_limit or self.known_provider_attempts < self.semantic_invocations_consumed:
            raise ValueError("invalid planning budget snapshot")
        return self


class PlanningSummarySnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
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


class LLMRuntimeErrorSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    code: LLMErrorCode
    role: ModelRole
    retryable: bool
    possible_duplicate: bool
    provider_status_code: int | None
    retry_after_seconds: float | None
    actual_bytes: int | None
    max_bytes: int | None


class LLMPlanningFailureSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    failure_type: Literal["llm"]
    error: LLMRuntimeErrorSnapshot
    invocation_index: Annotated[int, Field(ge=0, le=11)]
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _flags(self) -> LLMPlanningFailureSnapshot:
        if self.error.role is not ModelRole.ITINERARY_PLANNING or (self.retryable, self.possible_duplicate) != (self.error.retryable, self.error.possible_duplicate):
            raise ValueError("invalid LLM failure flags")
        return self


_MAP_RETRYABLE = {BaiduMapErrorCode.RATE_LIMIT_ERROR, BaiduMapErrorCode.TIMEOUT_ERROR, BaiduMapErrorCode.TRANSPORT_ERROR}


class MapPlanningFailureSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    failure_type: Literal["map"]
    capability: Literal["geocoding", "route_matrix", "directions"]
    error_code: BaiduMapErrorCode
    action_ordinal: Annotated[int, Field(ge=0)]
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _flags(self) -> MapPlanningFailureSnapshot:
        if self.retryable != (self.error_code in _MAP_RETRYABLE) or self.possible_duplicate:
            raise ValueError("invalid map failure flags")
        return self


_CONTROL_FLAGS = {
    PlanningFailureCode.ARTIFACT_ERROR: (True, False),
    PlanningFailureCode.BUDGET_ERROR: (True, False),
    PlanningFailureCode.OPERATION_RESULT_INDETERMINATE: (False, True),
}


class ControlPlanningFailureSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    failure_type: Literal["control"]
    code: PlanningFailureCode
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _flags(self) -> ControlPlanningFailureSnapshot:
        if self.code is PlanningFailureCode.ARTIFACT_ERROR and (self.retryable, self.possible_duplicate) == (False, True):
            return self
        if (self.retryable, self.possible_duplicate) != _CONTROL_FLAGS.get(self.code, (False, False)):
            raise ValueError("invalid control failure flags")
        return self


PlanningFailureSnapshot: TypeAlias = Annotated[
    LLMPlanningFailureSnapshot | MapPlanningFailureSnapshot | ControlPlanningFailureSnapshot,
    Field(discriminator="failure_type"),
]


class PlanningStateSnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    request_kind: Literal["initial", "resume"]
    requirement_digest: ArtifactDigest
    effective_policy: EffectivePlanningPolicySnapshot | None
    draft: ItineraryDraftSnapshot | None
    budgets: PlanningBudgetSummarySnapshot
    iterations_consumed: Annotated[int, Field(ge=0, le=6)]
    no_progress_streak: Annotated[int, Field(ge=0, le=2)]
    terminal_only_available: bool
    terminal_only_consumed: bool
    supplement_fingerprints: Annotated[tuple[ArtifactDigest, ...], Field(min_length=0, max_length=5)]
    pending_call_id: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=256)] | None

    @model_validator(mode="after")
    def _terminal(self) -> PlanningStateSnapshot:
        if self.terminal_only_available and self.terminal_only_consumed:
            raise ValueError("terminal-only state cannot be available and consumed")
        if self.terminal_only_available != (self.iterations_consumed == 6 and not self.terminal_only_consumed):
            raise ValueError("terminal-only availability does not match iteration state")
        return self


class InProgressItineraryRecord(_ArtifactModel):
    schema_version: Literal["1.0"]
    record_type: Literal["in_progress"]
    state: PlanningStateSnapshot
    gaps: Annotated[tuple[PlanningGapSummarySnapshot, ...], Field(min_length=0, max_length=128)]

    @model_validator(mode="after")
    def _pending(self) -> InProgressItineraryRecord:
        if self.state.pending_call_id is not None:
            raise ValueError("in-progress record cannot have pending supplement")
        return self


class AwaitingCandidateSupplementRecord(_ArtifactModel):
    schema_version: Literal["1.0"]
    record_type: Literal["awaiting_candidate_supplement"]
    state: PlanningStateSnapshot
    gaps: Annotated[tuple[PlanningGapSummarySnapshot, ...], Field(min_length=1, max_length=32)]
    gap_fingerprint: ArtifactDigest
    pending_call_id: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=256)]

    @model_validator(mode="after")
    def _pending(self) -> AwaitingCandidateSupplementRecord:
        if self.state.pending_call_id != self.pending_call_id:
            raise ValueError("pending call id must match state")
        return self


class CompletedItineraryRecord(_ArtifactModel):
    schema_version: Literal["1.0"]
    record_type: Literal["completed"]
    draft: ItineraryDraftSnapshot
    summary: PlanningSummarySnapshot
    budgets: PlanningBudgetSummarySnapshot


class IncompleteItineraryRecord(_ArtifactModel):
    schema_version: Literal["1.0"]
    record_type: Literal["incomplete"]
    draft: ItineraryDraftSnapshot | None
    summary: PlanningSummarySnapshot
    budgets: PlanningBudgetSummarySnapshot
    gaps: Annotated[tuple[PlanningGapSummarySnapshot, ...], Field(min_length=1, max_length=128)]
    stop_reasons: Annotated[tuple[PlanningStopReason, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _stop_order(self) -> IncompleteItineraryRecord:
        order = {item: index for index, item in enumerate(PlanningStopReason)}
        if tuple(sorted(set(self.stop_reasons), key=order.__getitem__)) != self.stop_reasons:
            raise ValueError("stop reasons must be unique and stable ordered")
        return self


class FailedItineraryRecord(_ArtifactModel):
    schema_version: Literal["1.0"]
    record_type: Literal["failed"]
    last_consistent_itinerary_ref: ArtifactRef | None
    failure: PlanningFailureSnapshot
    budgets: PlanningBudgetSummarySnapshot | None
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _flags(self) -> FailedItineraryRecord:
        if (self.retryable, self.possible_duplicate) != (self.failure.retryable, self.failure.possible_duplicate):
            raise ValueError("record failure flags must equal failure")
        return self


ItineraryRecord: TypeAlias = Annotated[
    InProgressItineraryRecord | AwaitingCandidateSupplementRecord | CompletedItineraryRecord
    | IncompleteItineraryRecord | FailedItineraryRecord,
    Field(discriminator="record_type"),
]


class ItineraryPlanningModelActionArtifact(_ArtifactModel):
    schema_version: Literal["3.0"]
    request_digest: ArtifactDigest
    invocation_index: Annotated[int, Field(ge=0, le=11)]
    prompt_version: Literal["planning-v3"]
    prompt_hash: ArtifactDigest
    action_kind: Literal["tool_calls", "final_text", "llm_failure"]
    call_summaries: Annotated[tuple[ModelCallSummary, ...], Field(min_length=0, max_length=71)]
    invocation_metadata: InvocationMetadata | None
    disposition: Literal["accepted", "policy_rejected", "controller_rejected", "terminal", "failed"]

    @model_validator(mode="after")
    def _action(self) -> ItineraryPlanningModelActionArtifact:
        if self.action_kind == "tool_calls" and (not self.call_summaries or self.invocation_metadata is None):
            raise ValueError("tool-call action requires calls and metadata")
        if self.action_kind == "final_text" and (self.call_summaries or self.invocation_metadata is None):
            raise ValueError("final-text action requires metadata and no calls")
        if self.action_kind == "llm_failure" and (self.call_summaries or self.invocation_metadata is not None):
            raise ValueError("LLM failure action cannot contain calls or metadata")
        return self


class ItineraryPlanningHubGeocodeObservationArtifact(_ArtifactModel):
    schema_version: Literal["1.0"]
    request_digest: ArtifactDigest
    model_action_ref: ArtifactRef
    action_ordinal: Annotated[int, Field(ge=0, le=70)]
    endpoint_kind: Literal["arrival", "departure"]
    request_fingerprint: ArtifactDigest
    observation_status: Literal["success", "ambiguous", "no_result", "failure", "not_issued"]
    success_candidate: HubGeocodeCandidateSnapshot | None
    ambiguous_candidates: Annotated[tuple[HubGeocodeCandidateSnapshot, ...], Field(min_length=0, max_length=10)]
    provenance: MapProvenanceSnapshot | None
    map_failure: MapPlanningFailureSnapshot | None

    @model_validator(mode="after")
    def _observation(self) -> ItineraryPlanningHubGeocodeObservationArtifact:
        if self.model_action_ref.artifact_type != "itinerary_planning.model_action" or self.model_action_ref.schema_version != "3.0":
            raise ValueError("observation must reference a model action")
        if self.observation_status == "success":
            if self.success_candidate is None or self.ambiguous_candidates or self.provenance is None or self.provenance.capability is not MapCapability.GEOCODING or not self.provenance.verified or self.map_failure is not None:
                raise ValueError("invalid geocode success branch")
        elif self.observation_status == "ambiguous":
            if not 2 <= len(self.ambiguous_candidates) <= 10 or self.success_candidate is not None or self.provenance is None or self.provenance.capability is not MapCapability.GEOCODING or not self.provenance.verified or self.map_failure is not None:
                raise ValueError("invalid geocode ambiguous branch")
        elif self.observation_status == "no_result":
            if self.success_candidate is not None or self.ambiguous_candidates or self.provenance is None or self.provenance.capability is not MapCapability.GEOCODING or self.map_failure is not None:
                raise ValueError("invalid geocode no-result branch")
        elif self.observation_status == "failure":
            if self.success_candidate is not None or self.ambiguous_candidates or self.provenance is not None or self.map_failure is None:
                raise ValueError("invalid geocode failure branch")
        elif self.success_candidate is not None or self.ambiguous_candidates or self.provenance is not None or self.map_failure is not None:
            raise ValueError("invalid geocode not-issued branch")
        return self


class ItineraryPlanningRouteMatrixObservationArtifact(_ArtifactModel):
    schema_version: Literal["1.0"]
    request_digest: ArtifactDigest
    model_action_ref: ArtifactRef
    action_ordinal: Annotated[int, Field(ge=0, le=70)]
    date: date
    mode: MatrixTravelMode
    request_fingerprint: ArtifactDigest
    ordered_locations: Annotated[tuple[PlanningLocationRefSnapshot, ...], Field(min_length=1, max_length=10)]
    cells: Annotated[tuple[RouteMatrixCellSnapshot, ...], Field(min_length=0, max_length=100)]
    observation_status: Literal["success", "no_result", "failure", "not_issued"]
    provenance: MapProvenanceSnapshot | None
    map_failure: MapPlanningFailureSnapshot | None

    @model_validator(mode="after")
    def _observation(self) -> ItineraryPlanningRouteMatrixObservationArtifact:
        if self.model_action_ref.artifact_type != "itinerary_planning.model_action" or self.model_action_ref.schema_version != "3.0":
            raise ValueError("observation must reference a model action")
        actual = tuple((cell.origin_index, cell.destination_index) for cell in self.cells)
        if self.observation_status == "success":
            expected = tuple((i, j) for i in range(len(self.ordered_locations)) for j in range(len(self.ordered_locations)))
            if actual != expected or not any(cell.status == "success" for cell in self.cells) or self.provenance is None or self.provenance.capability is not MapCapability.ROUTE_MATRIX or not self.provenance.verified or self.map_failure is not None:
                raise ValueError("matrix success must contain ordered square")
        elif self.observation_status == "no_result":
            if self.cells or self.provenance is None or self.provenance.capability is not MapCapability.ROUTE_MATRIX or self.map_failure is not None:
                raise ValueError("invalid matrix no-result branch")
        elif self.observation_status == "failure":
            if self.cells or self.provenance is not None or self.map_failure is None:
                raise ValueError("invalid matrix failure branch")
        elif self.cells or self.provenance is not None or self.map_failure is not None:
            raise ValueError("invalid matrix not-issued branch")
        return self


class RouteAlternativeSummarySnapshot(_ArtifactModel):
    schema_version: Literal["1.0"]
    alternative_index: Annotated[int, Field(ge=0, le=2)]
    distance_m: Annotated[int, Field(ge=0)]
    duration_s: Annotated[int, Field(ge=0)]
    step_count: Annotated[int, Field(ge=1, le=128)]


class ItineraryPlanningDirectionsObservationArtifact(_ArtifactModel):
    schema_version: Literal["1.0"]
    request_digest: ArtifactDigest
    model_action_ref: ArtifactRef
    action_ordinal: Annotated[int, Field(ge=0, le=70)]
    leg_ordinal: Annotated[int, Field(ge=0, le=481)]
    leg_fingerprint: ArtifactDigest
    origin: PlanningLocationRefSnapshot
    destination: PlanningLocationRefSnapshot
    transport_semantic: TransportSemantic
    route_mode: TravelMode
    alternatives_limit: Annotated[int, Field(ge=1, le=3)]
    observation_status: Literal["success", "no_result", "failure", "not_issued"]
    alternatives: Annotated[tuple[RouteAlternativeSummarySnapshot, ...], Field(min_length=0, max_length=3)]
    provenance: MapProvenanceSnapshot | None
    map_failure: MapPlanningFailureSnapshot | None

    @model_validator(mode="after")
    def _observation(self) -> ItineraryPlanningDirectionsObservationArtifact:
        if self.model_action_ref.artifact_type != "itinerary_planning.model_action" or self.model_action_ref.schema_version != "3.0":
            raise ValueError("observation must reference a model action")
        if tuple(item.alternative_index for item in self.alternatives) != tuple(range(len(self.alternatives))):
            raise ValueError("directions alternatives must keep provider order")
        if self.observation_status == "success":
            if not self.alternatives or len(self.alternatives) > self.alternatives_limit or self.provenance is None or self.provenance.capability is not MapCapability.DIRECTIONS or not self.provenance.verified or self.map_failure is not None:
                raise ValueError("invalid directions success branch")
        elif self.observation_status == "no_result":
            if self.alternatives or self.provenance is None or self.provenance.capability is not MapCapability.DIRECTIONS or self.map_failure is not None:
                raise ValueError("invalid directions no-result branch")
        elif self.observation_status == "failure":
            if self.alternatives or self.provenance is not None or self.map_failure is None:
                raise ValueError("invalid directions failure branch")
        elif self.alternatives or self.provenance is not None or self.map_failure is not None:
            raise ValueError("invalid directions not-issued branch")
        return self


class ArtifactRefDirectories(_ArtifactModel):
    schema_version: Literal["1.0"]
    hotel_selection_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=1, max_length=1)]
    candidate_set_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=1, max_length=6)]
    model_action_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=0, max_length=12)]
    hub_geocode_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=0, max_length=2)]
    matrix_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=0, max_length=360)]
    directions_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=0, max_length=482)]
    candidate_evidence_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=0, max_length=128)]

    @model_validator(mode="after")
    def _directories(self) -> ArtifactRefDirectories:
        groups = {
            "hotel_selection": self.hotel_selection_refs,
            "candidate_set": self.candidate_set_refs,
            "model_action": self.model_action_refs,
            "hub_geocode": self.hub_geocode_refs,
            "matrix": self.matrix_refs,
            "directions": self.directions_refs,
            "candidate_evidence": self.candidate_evidence_refs,
        }
        expected = {
            "hotel_selection": ("hotel.selection", WorkflowPhase.HOTEL_SELECTION),
            "candidate_set": ("candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH),
            "model_action": ("itinerary_planning.model_action", WorkflowPhase.ITINERARY_PLANNING),
            "hub_geocode": ("itinerary_planning.hub_geocode_observation", WorkflowPhase.ITINERARY_PLANNING),
            "matrix": ("itinerary_planning.route_matrix_observation", WorkflowPhase.ITINERARY_PLANNING),
            "directions": ("itinerary_planning.directions_observation", WorkflowPhase.ITINERARY_PLANNING),
            "candidate_evidence": ("candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH),
        }
        run_id = self.hotel_selection_refs[0].operation_id.run_id
        all_refs: list[ArtifactRef] = []
        for name, refs in groups.items():
            if len({ref.artifact_id for ref in refs}) != len(refs):
                raise ValueError(f"{name} refs must be unique")
            artifact_type, phase = expected[name]
            for ref in refs:
                expected_schema_version = "3.0" if name == "model_action" else "1.0"
                if ref.schema_version != expected_schema_version or ref.artifact_type != artifact_type or ref.operation_id.phase is not phase or ref.operation_id.run_id != run_id:
                    raise ValueError(f"{name} ref type/version/run mismatch")
            all_refs.extend(refs)
        if len({ref.artifact_id for ref in all_refs}) != len(all_refs):
            raise ValueError("artifact refs must be globally unique across directories")
        return self


def _refs_for_directory(directories: ArtifactRefDirectories, directory: str) -> tuple[ArtifactRef, ...]:
    return cast(tuple[ArtifactRef, ...], getattr(directories, f"{directory}_refs"))


def _validate_ref(ref: ArtifactRef, *, artifact_type: str, run_id) -> None:
    expected_schema_version = "3.0" if artifact_type == "itinerary_planning.model_action" else "1.0"
    if ref.schema_version != expected_schema_version or ref.artifact_type != artifact_type or ref.operation_id.run_id != run_id:
        raise ValueError("artifact reference mismatch")


def _validate_indexes(value: object, directories: ArtifactRefDirectories) -> None:
    if isinstance(value, ArtifactEvidenceIndex):
        if value.index >= len(_refs_for_directory(directories, value.directory)):
            raise ValueError("evidence index is outside its named directory")
    elif isinstance(value, HubLocationRefSnapshot) and value.geocode_observation_index >= len(directories.hub_geocode_refs):
        raise ValueError("hub geocode index is outside its directory")
    elif isinstance(value, RouteLegSnapshot) and value.directions_observation_index >= len(directories.directions_refs):
        raise ValueError("directions index is outside its directory")
    elif isinstance(value, BackupOptionSnapshot) and value.matrix_observation_index >= len(directories.matrix_refs):
        raise ValueError("matrix index is outside its directory")
    if isinstance(value, BaseModel):
        for item in value.__dict__.values():
            _validate_indexes(item, directories)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _validate_indexes(item, directories)


class ItineraryPlanningArtifact(_ArtifactModel):
    schema_version: Literal["1.0"]
    request_digest: ArtifactDigest
    parent_planning_ref: ArtifactRef | None
    directories: ArtifactRefDirectories
    record: ItineraryRecord

    @model_validator(mode="after")
    def _artifact(self) -> ItineraryPlanningArtifact:
        run_id = self.directories.hotel_selection_refs[0].operation_id.run_id
        if self.parent_planning_ref is not None:
            _validate_ref(self.parent_planning_ref, artifact_type="itinerary_planning.itinerary", run_id=run_id)
        _validate_indexes(self.record, self.directories)

        def check_draft(draft: ItineraryDraftSnapshot) -> None:
            if draft.hotel_selection_ref not in self.directories.hotel_selection_refs:
                raise ValueError("draft hotel ref is not in hotel directory")
            if draft.candidate_set_ref not in self.directories.candidate_set_refs:
                raise ValueError("draft candidate set ref is not in candidate directory")

        if isinstance(self.record, (CompletedItineraryRecord, IncompleteItineraryRecord)) and self.record.draft is not None:
            check_draft(self.record.draft)
        if isinstance(self.record, (InProgressItineraryRecord, AwaitingCandidateSupplementRecord)) and self.record.state.draft is not None:
            check_draft(self.record.state.draft)
        if isinstance(self.record, FailedItineraryRecord) and self.record.last_consistent_itinerary_ref is not None:
            _validate_ref(self.record.last_consistent_itinerary_ref, artifact_type="itinerary_planning.itinerary", run_id=run_id)
        return self


# Explicit public/upstream <-> Artifact-local conversions.  Every helper starts
# with a fresh strict JSON validation and constructs the local/public peer anew.
def _fresh_public(model: BaseModel, model_type: type[BaseModel]):
    """Rebuild a public DTO only after a strict Python-tree preflight.

    JSON serialisation alone can normalise forged tuple/list and other model
    instances before validation.  The first validation therefore happens on
    the original Python tree; the JSON round trip then gives callers a fresh
    instance with the same canonical boundary semantics.
    """
    if type(model) is not model_type:
        raise TypeError("model type mismatch")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            tree = model.model_dump(
                mode="python", exclude_none=False, by_alias=False,
                exclude_computed_fields=True,
            )
            first = TypeAdapter(model_type).validate_python(tree, strict=True)
            second = TypeAdapter(model_type).validate_json(
                first.model_dump_json(exclude_none=False), strict=True
            )
    except Exception:
        raise TypeError("model failed fresh strict validation") from None
    return second


def _to_location_snapshot(value: PlanningLocationRef) -> PlanningLocationRefSnapshot:
    public = _fresh_public(value, type(value))
    return TypeAdapter(PlanningLocationRefSnapshot).validate_json(public.model_dump_json(), strict=True)


def _from_location_snapshot(value: PlanningLocationRefSnapshot) -> PlanningLocationRef:
    data = value.model_dump(mode="python")
    if value.ref_type == "candidate":
        return _fresh_public(CandidateLocationRef.model_validate(data, strict=True), CandidateLocationRef)
    if value.ref_type == "hotel":
        return _fresh_public(HotelLocationRef.model_validate(data, strict=True), HotelLocationRef)
    return _fresh_public(HubLocationRef.model_validate(data, strict=True), HubLocationRef)


def _to_coordinate_snapshot(value: BaiduCoordinate) -> BaiduCoordinateSnapshot:
    public = _fresh_public(value, BaiduCoordinate)
    return BaiduCoordinateSnapshot(
        schema_version="1.0", coordinate_system=public.coordinate_system.value,
        latitude=public.latitude, longitude=public.longitude,
    )


def _from_coordinate_snapshot(value: BaiduCoordinateSnapshot) -> BaiduCoordinate:
    return _fresh_public(BaiduCoordinate.model_validate({
        "schema_version": "1.0", "coordinate_system": value.coordinate_system,
        "latitude": value.latitude, "longitude": value.longitude,
    }, strict=True), BaiduCoordinate)


def _to_provenance_snapshot(value: MapProvenance) -> MapProvenanceSnapshot:
    public = _fresh_public(value, MapProvenance)
    return MapProvenanceSnapshot(
        schema_version="1.0", source=public.source, capability=public.capability,
        retrieved_at_utc=public.retrieved_at.astimezone(_UTC), verified=public.verified,
    )


def _from_provenance_snapshot(value: MapProvenanceSnapshot) -> MapProvenance:
    return _fresh_public(MapProvenance.model_validate({
        "schema_version": "1.0", "source": value.source, "capability": value.capability,
        "retrieved_at": value.retrieved_at_utc, "verified": value.verified,
    }, strict=True), MapProvenance)


def _to_matrix_cell_snapshot(value: RouteMatrixCell) -> RouteMatrixCellSnapshot:
    public = _fresh_public(cast(BaseModel, value), type(cast(BaseModel, value)))
    return TypeAdapter(RouteMatrixCellSnapshot).validate_json(public.model_dump_json(), strict=True)


def _from_matrix_cell_snapshot(value: RouteMatrixCellSnapshot) -> RouteMatrixCell:
    from app.baidu_map import RouteMatrixCellNoResult, RouteMatrixCellSuccess

    model_type = RouteMatrixCellSuccess if value.status == "success" else RouteMatrixCellNoResult
    data = value.model_dump(mode="python")
    return _fresh_public(model_type.model_validate(data, strict=True), model_type)


def _to_route_alternative_snapshot(value: RouteAlternativeSummary) -> RouteAlternativeSummarySnapshot:
    public = _fresh_public(value, RouteAlternativeSummary)
    return RouteAlternativeSummarySnapshot(
        schema_version="1.0", alternative_index=public.alternative_index,
        distance_m=public.distance_m, duration_s=public.duration_s, step_count=public.step_count,
    )


def _from_route_alternative_snapshot(value: RouteAlternativeSummarySnapshot) -> RouteAlternativeSummary:
    return _fresh_public(RouteAlternativeSummary.model_validate(value.model_dump(mode="python"), strict=True), RouteAlternativeSummary)


def _to_persisted_interval(value: TimeInterval) -> PersistedTimeInterval:
    public = _fresh_public(value, TimeInterval)
    return PersistedTimeInterval(
        schema_version="1.0", start_at_utc=public.start_at.astimezone(_UTC), end_at_utc=public.end_at.astimezone(_UTC),
        timezone="Asia/Shanghai", local_start_date=public.start_at.date(), local_end_date=public.end_at.date(),
    )


def _from_persisted_interval(value: PersistedTimeInterval) -> TimeInterval:
    return _fresh_public(TimeInterval.model_validate({
        "schema_version": "1.0", "start_at": value.start_at_utc.astimezone(_SHANGHAI), "end_at": value.end_at_utc.astimezone(_SHANGHAI),
    }, strict=True), TimeInterval)


def _to_persisted_route_interval(value: RouteTimeInterval) -> PersistedRouteTimeInterval:
    public = _fresh_public(value, RouteTimeInterval)
    return PersistedRouteTimeInterval(
        schema_version="1.0", start_at_utc=public.start_at.astimezone(_UTC), end_at_utc=public.end_at.astimezone(_UTC),
        timezone="Asia/Shanghai", local_start_date=public.start_at.date(), local_end_date=public.end_at.date(),
    )


def _from_persisted_route_interval(value: PersistedRouteTimeInterval) -> RouteTimeInterval:
    return _fresh_public(RouteTimeInterval.model_validate({
        "schema_version": "1.0", "start_at": value.start_at_utc.astimezone(_SHANGHAI), "end_at": value.end_at_utc.astimezone(_SHANGHAI),
    }, strict=True), RouteTimeInterval)


def _to_daily_rule_snapshot(value: DailyTimeRule) -> DailyTimeRuleSnapshot:
    public = _fresh_public(value, DailyTimeRule)
    return DailyTimeRuleSnapshot(
        schema_version="1.0", rule_id=public.rule_id, date=public.date, interval=_to_persisted_interval(public.interval),
        strength=public.strength, source_field=public.source_field, source_digest=public.source_digest,
    )


def _from_daily_rule_snapshot(value: DailyTimeRuleSnapshot) -> DailyTimeRule:
    return DailyTimeRule.model_validate({
        "schema_version": "1.0", "rule_id": value.rule_id, "date": value.date, "interval": _from_persisted_interval(value.interval),
        "strength": value.strength, "source_field": value.source_field, "source_digest": value.source_digest,
    }, strict=True)


def _to_anchor_snapshot(value: TimeSensitiveMustAnchor) -> TimeSensitiveMustAnchorSnapshot:
    public = _fresh_public(value, TimeSensitiveMustAnchor)
    return TimeSensitiveMustAnchorSnapshot(
        schema_version="1.0", anchor_id=public.anchor_id, requirement_group_id=public.requirement_group_id,
        local_date=public.date, event_at_utc=public.event_at.astimezone(_UTC), timezone="Asia/Shanghai",
        source=public.source, source_ref_index=public.source_ref_index,
    )


def _from_anchor_snapshot(value: TimeSensitiveMustAnchorSnapshot) -> TimeSensitiveMustAnchor:
    return TimeSensitiveMustAnchor.model_validate({
        "schema_version": "1.0", "anchor_id": value.anchor_id, "requirement_group_id": value.requirement_group_id,
        "date": value.local_date, "event_at": value.event_at_utc.astimezone(_SHANGHAI), "source": value.source,
        "source_ref_index": value.source_ref_index,
    }, strict=True)


def _to_transport_rule_snapshot(value: TransportPolicyRule) -> TransportPolicyRuleSnapshot:
    public = _fresh_public(value, TransportPolicyRule)
    return TransportPolicyRuleSnapshot(
        schema_version="1.0", rule_id=public.rule_id, semantic=public.semantic, strength=public.strength,
        numeric_limit_minutes=public.numeric_limit_minutes, source_field=public.source_field, source_digest=public.source_digest,
    )


def _from_transport_rule_snapshot(value: TransportPolicyRuleSnapshot) -> TransportPolicyRule:
    return TransportPolicyRule.model_validate({
        "schema_version": "1.0", "rule_id": value.rule_id, "semantic": value.semantic, "strength": value.strength,
        "numeric_limit_minutes": value.numeric_limit_minutes, "source_field": value.source_field, "source_digest": value.source_digest,
    }, strict=True)


def _to_policy_snapshot(value: EffectivePlanningPolicy) -> EffectivePlanningPolicySnapshot:
    public = _fresh_public(value, EffectivePlanningPolicy)
    return EffectivePlanningPolicySnapshot(
        schema_version="1.0", effective_pace=public.effective_pace,
        daily_time_rules=tuple(_to_daily_rule_snapshot(item) for item in public.daily_time_rules),
        time_sensitive_must=tuple(_to_anchor_snapshot(item) for item in public.time_sensitive_must),
        transport_rules=tuple(_to_transport_rule_snapshot(item) for item in public.transport_rules), locked=True,
    )


def _from_policy_snapshot(value: EffectivePlanningPolicySnapshot) -> EffectivePlanningPolicy:
    return EffectivePlanningPolicy.model_validate({
        "schema_version": "1.0", "effective_pace": value.effective_pace,
        "daily_time_rules": tuple(_from_daily_rule_snapshot(item) for item in value.daily_time_rules),
        "time_sensitive_must": tuple(_from_anchor_snapshot(item) for item in value.time_sensitive_must),
        "transport_rules": tuple(_from_transport_rule_snapshot(item) for item in value.transport_rules), "locked": True,
    }, strict=True)


def _to_visit_snapshot(value: ScheduledVisit) -> ScheduledVisitSnapshot:
    public = _fresh_public(value, ScheduledVisit)
    return ScheduledVisitSnapshot(
        schema_version="1.0", visit_id=public.visit_id, location=_to_location_snapshot(public.location), visit_kind=public.visit_kind,
        interval=_to_persisted_interval(public.interval), duration_source=public.duration_source, must_group_ids=public.must_group_ids,
        time_sensitive_anchor_ids=public.time_sensitive_anchor_ids, business_hours_state=public.business_hours_state,
        business_evidence_indexes=public.business_evidence_indexes, requires_verification=public.requires_verification,
    )


def _from_visit_snapshot(value: ScheduledVisitSnapshot) -> ScheduledVisit:
    return ScheduledVisit.model_validate({
        "schema_version": "1.0", "visit_id": value.visit_id, "location": _from_location_snapshot(value.location),
        "visit_kind": value.visit_kind, "interval": _from_persisted_interval(value.interval), "duration_source": value.duration_source,
        "must_group_ids": value.must_group_ids, "time_sensitive_anchor_ids": value.time_sensitive_anchor_ids,
        "business_hours_state": value.business_hours_state, "business_evidence_indexes": value.business_evidence_indexes,
        "requires_verification": value.requires_verification,
    }, strict=True)


def _to_route_snapshot(value: RouteLeg) -> RouteLegSnapshot:
    public = _fresh_public(value, RouteLeg)
    return RouteLegSnapshot(
        schema_version="1.0", leg_id=public.leg_id, origin=_to_location_snapshot(public.origin), destination=_to_location_snapshot(public.destination),
        interval=_to_persisted_route_interval(public.interval), transport_semantic=public.transport_semantic, route_mode=public.route_mode,
        directions_observation_index=public.directions_observation_index, alternative_index=public.alternative_index,
        distance_m=public.distance_m, duration_s=public.duration_s, scheduled_minutes=public.scheduled_minutes,
    )


def _from_route_snapshot(value: RouteLegSnapshot) -> RouteLeg:
    return RouteLeg.model_validate({
        "schema_version": "1.0", "leg_id": value.leg_id, "origin": _from_location_snapshot(value.origin), "destination": _from_location_snapshot(value.destination),
        "interval": _from_persisted_route_interval(value.interval), "transport_semantic": value.transport_semantic, "route_mode": value.route_mode,
        "directions_observation_index": value.directions_observation_index, "alternative_index": value.alternative_index,
        "distance_m": value.distance_m, "duration_s": value.duration_s, "scheduled_minutes": value.scheduled_minutes,
    }, strict=True)


def _to_transfer_snapshot(value: BoundaryTransfer) -> BoundaryTransferSnapshot:
    public = _fresh_public(value, BoundaryTransfer)
    return BoundaryTransferSnapshot(
        schema_version="1.0", transfer_kind=public.transfer_kind,
        hub=cast(HubLocationRefSnapshot, _to_location_snapshot(public.hub)), hotel=cast(HotelLocationRefSnapshot, _to_location_snapshot(public.hotel)),
        route_leg=_to_route_snapshot(public.route_leg), processing_minutes=public.processing_minutes,
        approximate_extra_minutes=public.approximate_extra_minutes,
    )


def _from_transfer_snapshot(value: BoundaryTransferSnapshot) -> BoundaryTransfer:
    return BoundaryTransfer.model_validate({
        "schema_version": "1.0", "transfer_kind": value.transfer_kind, "hub": _from_location_snapshot(value.hub), "hotel": _from_location_snapshot(value.hotel),
        "route_leg": _from_route_snapshot(value.route_leg), "processing_minutes": value.processing_minutes,
        "approximate_extra_minutes": value.approximate_extra_minutes,
    }, strict=True)


def _to_backup_snapshot(value: BackupOption) -> BackupOptionSnapshot:
    public = _fresh_public(value, BackupOption)
    return BackupOptionSnapshot(
        schema_version="1.0", backup_kind=public.backup_kind, core_visit_id=public.core_visit_id,
        candidate=_to_location_snapshot(public.candidate), matrix_observation_index=public.matrix_observation_index,
        matrix_mode=public.matrix_mode, distance_m=public.distance_m, business_hours_state=public.business_hours_state,
        business_evidence_indexes=public.business_evidence_indexes, requires_verification=public.requires_verification,
    )


def _from_backup_snapshot(value: BackupOptionSnapshot) -> BackupOption:
    return BackupOption.model_validate({
        "schema_version": "1.0", "backup_kind": value.backup_kind, "core_visit_id": value.core_visit_id,
        "candidate": _from_location_snapshot(value.candidate), "matrix_observation_index": value.matrix_observation_index,
        "matrix_mode": value.matrix_mode, "distance_m": value.distance_m, "business_hours_state": value.business_hours_state,
        "business_evidence_indexes": value.business_evidence_indexes, "requires_verification": value.requires_verification,
    }, strict=True)


def _to_day_snapshot(value: DayPlan) -> DayPlanSnapshot:
    public = _fresh_public(value, DayPlan)
    return DayPlanSnapshot(
        schema_version="1.0", date=public.date, day_kind=public.day_kind, theme=public.theme,
        open_intervals=tuple(_to_persisted_interval(item) for item in public.open_intervals),
        visits=tuple(_to_visit_snapshot(item) for item in public.visits), route_legs=tuple(_to_route_snapshot(item) for item in public.route_legs),
        backups=tuple(_to_backup_snapshot(item) for item in public.backups),
    )


def _from_day_snapshot(value: DayPlanSnapshot) -> DayPlan:
    return DayPlan.model_validate({
        "schema_version": "1.0", "date": value.date, "day_kind": value.day_kind, "theme": value.theme,
        "open_intervals": tuple(_from_persisted_interval(item) for item in value.open_intervals),
        "visits": tuple(_from_visit_snapshot(item) for item in value.visits), "route_legs": tuple(_from_route_snapshot(item) for item in value.route_legs),
        "backups": tuple(_from_backup_snapshot(item) for item in value.backups),
    }, strict=True)


def _to_draft_snapshot(value: ItineraryDraft) -> ItineraryDraftSnapshot:
    public = _fresh_public(value, ItineraryDraft)
    return ItineraryDraftSnapshot(
        schema_version="1.0", requirement_digest=public.requirement_digest, hotel_selection_ref=public.hotel_selection_ref,
        candidate_set_ref=public.candidate_set_ref, effective_policy=_to_policy_snapshot(public.effective_policy),
        arrival_transfer=_to_transfer_snapshot(public.arrival_transfer), days=tuple(_to_day_snapshot(item) for item in public.days),
        departure_transfer=_to_transfer_snapshot(public.departure_transfer), upstream_gap_ids=public.upstream_gap_ids,
    )


def _from_draft_snapshot(value: ItineraryDraftSnapshot) -> ItineraryDraft:
    return ItineraryDraft.model_validate({
        "schema_version": "1.0", "requirement_digest": value.requirement_digest, "hotel_selection_ref": value.hotel_selection_ref,
        "candidate_set_ref": value.candidate_set_ref, "effective_policy": _from_policy_snapshot(value.effective_policy),
        "arrival_transfer": _from_transfer_snapshot(value.arrival_transfer), "days": tuple(_from_day_snapshot(item) for item in value.days),
        "departure_transfer": _from_transfer_snapshot(value.departure_transfer), "upstream_gap_ids": value.upstream_gap_ids,
    }, strict=True)


def _to_budget_snapshot(value: PlanningBudgetSummary) -> PlanningBudgetSummarySnapshot:
    public = _fresh_public(value, PlanningBudgetSummary)
    usage = LLMUsage.model_validate_json(public.known_usage.model_dump_json())
    return PlanningBudgetSummarySnapshot(
        schema_version="1.0", iteration_limit=6, iterations_consumed=public.iterations_consumed,
        direction_call_limit=public.direction_call_limit, direction_calls_consumed=public.direction_calls_consumed,
        budget_snapshot_complete=public.budget_snapshot_complete, semantic_invocation_limit=12,
        semantic_invocations_consumed=public.semantic_invocations_consumed, known_provider_attempts=public.known_provider_attempts,
        provider_attempts_complete=public.provider_attempts_complete, known_usage=usage, usage_complete=public.usage_complete,
        cache_hits=public.cache_hits,
    )


def _from_budget_snapshot(value: PlanningBudgetSummarySnapshot) -> PlanningBudgetSummary:
    usage = LLMUsage.model_validate_json(value.known_usage.model_dump_json())
    return PlanningBudgetSummary.model_validate({
        "schema_version": "1.0", "iteration_limit": 6, "iterations_consumed": value.iterations_consumed,
        "direction_call_limit": value.direction_call_limit, "direction_calls_consumed": value.direction_calls_consumed,
        "budget_snapshot_complete": value.budget_snapshot_complete, "semantic_invocation_limit": 12,
        "semantic_invocations_consumed": value.semantic_invocations_consumed, "known_provider_attempts": value.known_provider_attempts,
        "provider_attempts_complete": value.provider_attempts_complete, "known_usage": usage, "usage_complete": value.usage_complete,
        "cache_hits": value.cache_hits,
    }, strict=True)


def _to_summary_snapshot(value: PlanningSummary) -> PlanningSummarySnapshot:
    public = _fresh_public(value, PlanningSummary)
    return PlanningSummarySnapshot.model_validate(public.model_dump(mode="python"), strict=True)


def _from_summary_snapshot(value: PlanningSummarySnapshot) -> PlanningSummary:
    return _fresh_public(PlanningSummary.model_validate(value.model_dump(mode="python"), strict=True), PlanningSummary)


def _to_target_snapshot(value: object) -> CandidateSupplementTargetSnapshot:
    public = _fresh_public(cast(BaseModel, value), type(cast(BaseModel, value)))
    data = dict(public.model_dump(mode="python"))
    if hasattr(public, "anchor") and public.anchor is not None:
        data["anchor"] = _to_location_snapshot(cast(PlanningLocationRef, public.anchor))
    return TypeAdapter(CandidateSupplementTargetSnapshot).validate_python(data, strict=True)


def _from_target_snapshot(value: CandidateSupplementTargetSnapshot):
    data = dict(cast(BaseModel, value).model_dump(mode="python"))
    if hasattr(value, "anchor") and value.anchor is not None:
        data["anchor"] = _from_location_snapshot(cast(PlanningLocationRefSnapshot, value.anchor))
    target_model = {
        "candidate_count": CandidateCountTarget, "area_coverage": AreaCoverageTarget, "meal_coverage": MealCoverageTarget,
        "must_resolution": MustResolutionTarget, "avoid_resolution": AvoidResolutionTarget, "candidate_details": CandidateDetailsTarget,
        "replacement": ReplacementTarget,
    }[value.target_type]
    return _fresh_public(target_model.model_validate(data, strict=True), target_model)


def _to_gap_summary_snapshot(value: PlanningGapSummary) -> PlanningGapSummarySnapshot:
    public = _fresh_public(cast(BaseModel, value), type(cast(BaseModel, value)))
    data = dict(public.model_dump(mode="python"))
    data["evidence_indexes"] = tuple(ArtifactEvidenceIndex(schema_version="1.0", directory=item.directory, index=item.index) for item in public.evidence_indexes)
    if hasattr(public, "origin"):
        data["origin"] = _to_location_snapshot(public.origin)
        data["destination"] = _to_location_snapshot(public.destination)
    if hasattr(public, "candidate"):
        data["candidate"] = _to_location_snapshot(public.candidate)
    if hasattr(public, "target"):
        data["target"] = _to_target_snapshot(public.target)
    return TypeAdapter(PlanningGapSummarySnapshot).validate_python(data, strict=True)


def _from_gap_summary_snapshot(
    value: PlanningGapSummarySnapshot,
    directories: ArtifactRefDirectories | None = None,
) -> PlanningGap:
    data = dict(cast(BaseModel, value).model_dump(mode="python"))
    data["gap_type"] = data.pop("summary_type")
    directory_map = {} if directories is None else {
        "hotel_selection": directories.hotel_selection_refs,
        "candidate_set": directories.candidate_set_refs,
        "candidate_evidence": directories.candidate_evidence_refs,
        "hub_geocode": directories.hub_geocode_refs,
        "matrix": directories.matrix_refs,
        "directions": directories.directions_refs,
    }
    data["evidence_refs"] = tuple(
        directory_map[item.directory][item.index]
        for item in value.evidence_indexes
    )
    data.pop("evidence_indexes", None)
    if hasattr(value, "origin"):
        data["origin"] = _from_location_snapshot(value.origin)
        data["destination"] = _from_location_snapshot(value.destination)
    if hasattr(value, "candidate"):
        data["candidate"] = _from_location_snapshot(value.candidate)
    if hasattr(value, "target"):
        data["target"] = _from_target_snapshot(value.target)
    return TypeAdapter(PlanningGap).validate_python(data, strict=True)


def _to_failure_snapshot(value: PlanningFailure):
    public = _fresh_public(cast(BaseModel, value), type(cast(BaseModel, value)))
    if isinstance(public, LLMPlanningFailure):
        error = public.error
        return LLMPlanningFailureSnapshot(
            schema_version="1.0", failure_type="llm",
            error=LLMRuntimeErrorSnapshot(
                schema_version="1.0", code=error.code, role=error.role, retryable=error.retryable,
                possible_duplicate=error.possible_duplicate, provider_status_code=error.provider_status_code,
                retry_after_seconds=error.retry_after_seconds, actual_bytes=error.actual_bytes, max_bytes=error.max_bytes,
            ), invocation_index=public.invocation_index, retryable=public.retryable, possible_duplicate=public.possible_duplicate,
        )
    if isinstance(public, MapPlanningFailure):
        return MapPlanningFailureSnapshot(
            schema_version="1.0", failure_type="map", capability=public.capability, error_code=public.error_code,
            action_ordinal=public.action_ordinal, retryable=public.retryable, possible_duplicate=public.possible_duplicate,
        )
    return ControlPlanningFailureSnapshot(
        schema_version="1.0", failure_type="control", code=public.code, retryable=public.retryable, possible_duplicate=public.possible_duplicate,
    )


def _from_failure_snapshot(value: PlanningFailureSnapshot) -> PlanningFailure:
    if isinstance(value, LLMPlanningFailureSnapshot):
        error = value.error
        public_error = LLMRuntimeError.model_validate({
            "code": error.code, "role": error.role, "retryable": error.retryable,
            "possible_duplicate": error.possible_duplicate, "provider_status_code": error.provider_status_code,
            "retry_after_seconds": error.retry_after_seconds, "actual_bytes": error.actual_bytes,
            "max_bytes": error.max_bytes,
        }, strict=True)
        return LLMPlanningFailure.model_validate({
            "schema_version": "1.0", "failure_type": "llm", "error": public_error,
            "invocation_index": value.invocation_index, "retryable": value.retryable,
            "possible_duplicate": value.possible_duplicate,
        }, strict=True)
    if isinstance(value, MapPlanningFailureSnapshot):
        return MapPlanningFailure.model_validate(value.model_dump(mode="python"), strict=True)
    from .models import ControlPlanningFailure

    return ControlPlanningFailure.model_validate(value.model_dump(mode="python"), strict=True)


ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS: tuple[ArtifactSchemaRegistration, ...] = (
    ArtifactSchemaRegistration(artifact_type="itinerary_planning.model_action", schema_version="3.0", model=ItineraryPlanningModelActionArtifact),
    ArtifactSchemaRegistration(artifact_type="itinerary_planning.hub_geocode_observation", schema_version="1.0", model=ItineraryPlanningHubGeocodeObservationArtifact),
    ArtifactSchemaRegistration(artifact_type="itinerary_planning.route_matrix_observation", schema_version="1.0", model=ItineraryPlanningRouteMatrixObservationArtifact),
    ArtifactSchemaRegistration(artifact_type="itinerary_planning.directions_observation", schema_version="1.0", model=ItineraryPlanningDirectionsObservationArtifact),
    ArtifactSchemaRegistration(artifact_type="itinerary_planning.itinerary", schema_version="1.0", model=ItineraryPlanningArtifact),
)


__all__ = (
    "ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS", "ItineraryPlanningModelActionArtifact",
    "ItineraryPlanningHubGeocodeObservationArtifact", "ItineraryPlanningRouteMatrixObservationArtifact",
    "ItineraryPlanningDirectionsObservationArtifact", "ItineraryPlanningArtifact",
)


for _model in tuple(value for value in globals().values() if isinstance(value, type) and issubclass(value, BaseModel) and value is not BaseModel):
    _model.model_rebuild()
del _model
