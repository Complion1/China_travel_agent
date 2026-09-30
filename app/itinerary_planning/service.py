"""Module 09 规划服务。

本模块是 Module 09 的唯一编排入口。它只依赖上游公共 facade；模型负责
语义选择，服务负责顺序、预算、Artifact、生命周期和确定性业务 Gate。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, TypeAdapter, ValidationError
from app.llm_runtime.diagnostics import error_context, record_error, locations, validation_details

import app.candidate_research as research

from app.baidu_map import (
    BaiduMapAdapter,
    BaiduCoordinate,
    BaiduMapError,
    BaiduMapErrorCode,
    MapCapability,
    MatrixTravelMode,
    TravelMode,
)
from app.candidate_research import (
    CandidateRecord,
    CandidateResearchArtifact,
    CandidateState,
    MealIntent,
    MatchVerdict,
    ResearchMode,
    ResearchRole,
)
from app.hotel_selection import HotelSelectedDecision, HotelSelectionArtifact
from app.llm_runtime import (
    DeepSeekRuntime,
    InvocationMetadata,
    LLMErrorCode,
    LLMInvocationFailure,
    LLMRuntimeError,
    LLMUsage,
    ModelRole,
    PromptEnvelope,
    ToolCallsReady,
    ToolResult,
    ToolTurnCompleted,
    ValidatedToolCall,
)
from app.requirements import ConfirmedTravelRequirement, HubType, TravelPace
from app.workflow_contracts import (
    ArtifactAbsent,
    ArtifactCapacityExceededError,
    ArtifactFound,
    ArtifactRef,
    ArtifactRepositoryError,
    ArtifactWriteRequest,
    ArtifactRepository,
    BudgetConsumed,
    BudgetConsumeRequest,
    BudgetExceeded,
    ExecutionBudgetKey,
    ExecutionBudgetManager,
    OperationId,
    WorkflowPhase,
)

from .artifacts import (
    ArtifactEvidenceIndex,
    ArtifactRefDirectories,
    AwaitingCandidateSupplementRecord,
    CompletedItineraryRecord,
    FailedItineraryRecord,
    InProgressItineraryRecord,
    IncompleteItineraryRecord,
    ItineraryPlanningArtifact,
    ItineraryPlanningDirectionsObservationArtifact,
    ItineraryPlanningHubGeocodeObservationArtifact,
    ItineraryPlanningModelActionArtifact,
    ItineraryPlanningRouteMatrixObservationArtifact,
    PlanningStateSnapshot,
    _from_budget_snapshot,
    _from_draft_snapshot,
    _from_failure_snapshot,
    _from_gap_summary_snapshot,
    _from_summary_snapshot,
    _to_budget_snapshot,
    _to_draft_snapshot,
    _to_failure_snapshot,
    _to_gap_summary_snapshot,
    _to_summary_snapshot,
    _to_location_snapshot,
    _to_route_alternative_snapshot,
)
from .errors import ItineraryPlanningConfigurationError, ItineraryPlanningUsageError
from .fingerprints import (
    _canonical_hash,
    artifact_ref_payload,
    fingerprint_candidate_handle,
    fingerprint_gap,
    fingerprint_planning_context,
    fingerprint_request_initial,
    fingerprint_request_resume,
    fingerprint_terminal_record,
)
from .map_execution import (
    DirectionsObservation,
    HubEndpointQuery,
    LocationBinding,
    MapExecutor,
    MatrixObservation,
    GeocodeObservation,
    direction_matches,
    selected_direction_summary,
)
from .models import (
    AreaCoverageTarget,
    ArtifactEvidenceIndex as PublicArtifactEvidenceIndex,
    BackupUnavailableGap,
    BoundaryTransfer,
    BusinessHoursState,
    CandidateCountTarget,
    CandidateDetailsTarget,
    CandidateLocationRef,
    CandidateSupplyGap,
    ControlPlanningFailure,
    DayPlan,
    EffectivePlanningPolicy,
    HotelLocationRef,
    HubLocationRef,
    HubResolutionGap,
    ItineraryDraft,
    ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY,
    ItineraryPlanningInitialRequest,
    ItineraryPlanningOperationPlan,
    ItineraryPlanningResumeRequest,
    ItineraryPlanningOutcome,
    ItineraryPlanningCompleted,
    ItineraryPlanningCandidateSupplementRequested,
    ItineraryPlanningFailed,
    ItineraryPlanningIncomplete,
    LLMPlanningFailure,
    MapPlanningFailure,
    MealCoverageTarget,
    PlanningFailure,
    PlanningFailureCode,
    PlanningGap,
    PlanningGapSummary,
    PlanningProcessGap,
    PlanningProcessGapSummary,
    PlanningStopReason,
    PlanningSummary,
    PlanningDayKind,
    ProposedCandidateCountTarget,
    ProposedAreaCoverageTarget,
    ProposedMealCoverageTarget,
    ProposedMustResolutionTarget,
    ProposedAvoidResolutionTarget,
    ProposedCandidateDetailsTarget,
    ProposedReplacementTarget,
    ProposedEffectivePlanningPolicy,
    ProposedItineraryDraft,
    RequestCandidateSupplementArgs,
    SubmitItineraryArgs,
    VisitKind,
    RouteLeg,
    ScheduledVisit,
    BackupOption,
    TimeInterval,
    RouteTimeInterval,
    PlanningTool,
)
from .policy import (
    _BackupSlotsMismatch,
    _PolicyBusinessRejected,
    _PolicyLockedMismatch,
    candidate_is_eligible,
    candidate_uid_is_allowed,
    effective_policy_from_proposal,
    lock_effective_pace,
    order_stop_reasons,
    route_mode_for_semantic,
    route_scheduled_minutes,
    attraction_bounds,
    validate_density,
    validate_meal_slots,
    validate_visit_duration,
    validate_route_capacity,
    validate_route_leg_capacity,
    validate_business_state,
    validate_backup_options,
    validate_day_policy,
    validate_effective_policy,
    validate_unique_main_and_backup_uids,
    boundary_processing_minutes,
    default_open_interval,
)
from .prompt_loader import PLANNING_PROMPT_VERSION, load_planning_prompt
from app.llm_runtime import InteractionCallIdentity, interaction_call
from .artifacts import PlanningRejectionSnapshot
from .prompting import HandleDirectory, make_planning_prompt
from .tooling import (
    _ARGS_BY_TOOL,
    PLANNING_TOOL_CONTRACTS,
    ToolBatchError,
    ToolDispatcher,
    ToolDispatchResult,
    ToolObservation,
    _CandidateEvidenceCandidate,
    _CandidateEvidenceObservation,
    tool_results,
)


_TERMINAL_TOOLS = {PlanningTool.SUBMIT_ITINERARY.value, PlanningTool.REQUEST_CANDIDATE_SUPPLEMENT.value}
_MAP_CODES_RETRYABLE = {
    BaiduMapErrorCode.RATE_LIMIT_ERROR,
    BaiduMapErrorCode.TIMEOUT_ERROR,
    BaiduMapErrorCode.TRANSPORT_ERROR,
}


class _Indeterminate(Exception):
    __slots__ = ()


class _ArtifactWriteIndeterminate(_Indeterminate):
    __slots__ = ()


class _BusinessRejected(Exception):
    __slots__ = ("reason", "field")

    def __init__(self, reason: str = "business_gate_rejected", field: str = "draft") -> None:
        self.reason = reason
        self.field = field


class _BackupSlotsRejected(_BusinessRejected):
    __slots__ = ("day_no", "slots")

    def __init__(self, day_no: int, slots: _BackupSlotsMismatch) -> None:
        super().__init__("backup_slots_mismatch")
        self.day_no = day_no
        self.slots = slots


def _check_business(check, *args, field: str = "draft", **kwargs) -> None:
    """Expose a stable check name/location, never exception text or user data."""
    try:
        check(*args, **kwargs)
    except (TypeError, ValueError):
        raise _BusinessRejected(f"{check.__name__}_rejected", field) from None


def _supplement_request(artifact: ItineraryPlanningArtifact, plan: research.CandidateResearchOperationPlan) -> research.CandidateResearchSupplementalRequest:
    """Reconstruct the original request from the awaiting snapshot (D §15.1)."""
    record = artifact.record
    if not isinstance(record, AwaitingCandidateSupplementRecord):
        raise ValueError("awaiting record required")
    gaps = tuple(_from_gap_summary_snapshot(item, artifact.directories) for item in record.gaps)

    def anchor(value):
        if isinstance(value, HotelLocationRef):
            return research.HotelAnchorRef()
        if isinstance(value, CandidateLocationRef):
            return research.CandidateAnchorRef(uid=value.uid)
        if value is None:
            return None
        raise ValueError("invalid supplement anchor")

    types = {
        "candidate_count": research.CandidateCountShortfallGap,
        "area_coverage": research.AreaCoverageMissingGap,
        "meal_coverage": research.MealCoverageMissingGap,
        "must_resolution": research.MustVisitResolutionMissingGap,
        "avoid_resolution": research.AvoidResolutionMissingGap,
        "candidate_details": research.CandidateDetailsMissingGap,
        "replacement": research.ReplacementCandidateNeededGap,
    }
    mapped = []
    for gap in gaps:
        common = {"gap_id": gap.gap_id, "evidence_refs": gap.evidence_refs}
        if isinstance(gap, CandidateSupplyGap):
            target = gap.target
            values = target.model_dump(mode="python", exclude={"schema_version", "target_type"})
            if "anchor" in values:
                values["anchor"] = anchor(target.anchor)
            mapped.append(types[target.target_type](**common, **values))
        elif isinstance(gap, BackupUnavailableGap):
            if record.state.draft is None:
                raise ValueError("backup source draft absent")
            draft = _from_draft_snapshot(record.state.draft)
            day = next(item for item in draft.days if item.date == gap.date)
            core = next(item for item in day.visits if item.visit_id == gap.core_visit_id)
            values = {"anchor": anchor(core.location), "radius_km": 3}
            if gap.backup_kind == "attraction":
                mapped.append(research.AreaCoverageMissingGap(**common, **values, role=ResearchRole.ATTRACTION))
            else:
                mapped.append(research.MealCoverageMissingGap(**common, **values, meal_intent=MealIntent(gap.backup_kind)))
        else:
            raise ValueError("unmapped supplemental gap")
    request = research.CandidateResearchSupplementalRequest(
        predecessor_candidate_set_ref=artifact.directories.candidate_set_refs[-1],
        gaps=tuple(mapped), operation_plan=plan,
    )
    return cast(research.CandidateResearchSupplementalRequest, _fresh(request, research.CandidateResearchSupplementalRequest))


def _supplement_request_digest(request: research.CandidateResearchSupplementalRequest) -> str:
    payload = request.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _fresh(model: BaseModel, model_type: type[BaseModel]) -> BaseModel:
    if type(model) is not model_type:
        raise ValueError("strict model type mismatch")
    try:
        tree = model.model_dump(mode="python", exclude_none=False, by_alias=False, exclude_computed_fields=True)
        first = TypeAdapter(model_type).validate_python(tree, strict=True)
        return TypeAdapter(model_type).validate_json(first.model_dump_json(exclude_none=False), strict=True)
    except Exception:
        raise ValueError("strict model validation failed") from None


def _handles(value: object) -> tuple[str, ...]:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", exclude_none=False)
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            if key.endswith("_handle") and type(item) is str:
                result.append(item)
            elif key.endswith("_handles") and isinstance(item, (tuple, list)):
                result.extend(x for x in item if type(x) is str)
            else:
                result.extend(_handles(item))
        return tuple(result)
    if isinstance(value, (tuple, list)):
        result: list[str] = []
        for item in value:
            result.extend(_handles(item))
        return tuple(result)
    return ()


def _zero_usage() -> LLMUsage:
    return LLMUsage(
        prompt_tokens=0, completion_tokens=0, total_tokens=0,
        reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0,
    )


def _add_usage(left: LLMUsage, right: LLMUsage) -> LLMUsage:
    return LLMUsage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        completion_tokens=left.completion_tokens + right.completion_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        reasoning_tokens=left.reasoning_tokens + right.reasoning_tokens,
        prompt_cache_hit_tokens=left.prompt_cache_hit_tokens + right.prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=left.prompt_cache_miss_tokens + right.prompt_cache_miss_tokens,
    )


def _confirmed_digest(confirmed: ConfirmedTravelRequirement) -> str:
    supplied = confirmed.confirmation_summary_digest
    if type(supplied) is str and len(supplied) == 71 and supplied.startswith("sha256:"):
        return supplied
    return _canonical_hash(confirmed.model_dump(mode="json", exclude_none=False))


def _validate_supported_confirmed_requirement(
    confirmed: ConfirmedTravelRequirement,
) -> None:
    """Validate the Module 01 structured fields consumed by Module 09."""

    requirement = confirmed.requirement
    window = requirement.time_window
    arrival = window.arrival
    departure = window.departure
    required = (
        requirement.destination,
        window.sightseeing_start_date,
        window.sightseeing_end_date,
        window.sightseeing_days,
        arrival.hub_name,
        arrival.hub_type,
        arrival.local_datetime,
        departure.hub_name,
        departure.hub_type,
        departure.local_datetime,
    )
    if any(value is None for value in required):
        raise ValueError("confirmed planning requirement is incomplete")

    days = window.sightseeing_days
    if type(days) is not int or not 1 <= days <= 60:
        raise ValueError("confirmed sightseeing days are invalid")
    try:
        start_date = date.fromisoformat(cast(str, window.sightseeing_start_date))
        end_date = date.fromisoformat(cast(str, window.sightseeing_end_date))
        arrival_at = datetime.strptime(cast(str, arrival.local_datetime), "%Y-%m-%d %H:%M")
        departure_at = datetime.strptime(cast(str, departure.local_datetime), "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("confirmed planning dates are invalid") from None
    if end_date < start_date or (end_date - start_date).days + 1 != days:
        raise ValueError("confirmed sightseeing range is inconsistent")
    if arrival_at.date() > start_date or departure_at.date() < end_date:
        raise ValueError("confirmed boundary times do not cover sightseeing dates")

    preferences = requirement.preferences
    if preferences.pace is None:
        if not preferences.agent_decides_pace:
            raise ValueError("confirmed pace is missing")
    elif preferences.agent_decides_pace:
        raise ValueError("confirmed pace choice is inconsistent")
    if preferences.interests is None:
        if not preferences.agent_decides_interests:
            raise ValueError("confirmed interests are missing")
    elif preferences.agent_decides_interests:
        raise ValueError("confirmed interest choice is inconsistent")


def _requirement_stable_id(prefix: str, tree: dict[str, object]) -> str:
    digest = _canonical_hash(tree)
    return f"{prefix}:{digest[7:23]}"


def _confirmed_must_requirement_groups(confirmed: ConfirmedTravelRequirement) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Rebuild the Module 08 B.1/B.2 must-item/group identity in Module 09."""
    confirmed_digest = _confirmed_digest(confirmed)
    grouped: list[dict[str, object]] = []
    by_text: dict[str, dict[str, object]] = {}
    must_visit_places = confirmed.requirement.preferences.must_visit_places
    for index, text in enumerate(must_visit_places):
        if type(text) is not str:
            raise ValueError("confirmed must item text is not a string")
        item_id = _requirement_stable_id(
            f"reqitem:must_visit:{index}",
            {
                "confirmed_requirement_digest": confirmed_digest,
                "field": "must_visit",
                "index": index,
                "text": text,
            },
        )
        group = by_text.get(text)
        if group is None:
            group = {"first_index": index, "text": text, "item_ids": [item_id]}
            by_text[text] = group
            grouped.append(group)
        else:
            cast(list[str], group["item_ids"]).append(item_id)
    result = []
    for group in grouped:
        item_ids = tuple(cast(list[str], group["item_ids"]))
        group_id = _requirement_stable_id(
            f"reqgroup:must_visit:{group['first_index']}",
            {
                "confirmed_requirement_digest": confirmed_digest,
                "field": "must_visit",
                "item_ids": list(item_ids),
                "text": group["text"],
            },
        )
        result.append((group_id, item_ids))
    return tuple(result)


def _artifact_content_hash(content: BaseModel) -> str:
    encoded = json.dumps(
        content.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True)
class _Run:
    run_id: UUID
    request_digest: str
    request_kind: str
    confirmed: ConfirmedTravelRequirement
    hotel_ref: ArtifactRef
    candidate_ref: ArtifactRef
    plan: ItineraryPlanningOperationPlan
    candidates: tuple[CandidateRecord, ...]
    directory: HandleDirectory
    map_executor: MapExecutor
    operation: object
    candidate_evidence_refs: tuple[ArtifactRef, ...]
    requirement_group_states: tuple[Any, ...]
    context_digest: str
    action_refs: list[ArtifactRef] = field(default_factory=list)
    hub_refs: list[ArtifactRef] = field(default_factory=list)
    matrix_refs: list[ArtifactRef] = field(default_factory=list)
    directions_refs: list[ArtifactRef] = field(default_factory=list)
    candidate_set_refs: list[ArtifactRef] = field(default_factory=list)
    direction_observations: list[DirectionsObservation] = field(default_factory=list)
    matrix_observations: list[MatrixObservation] = field(default_factory=list)
    policy: EffectivePlanningPolicy | None = None
    draft: ItineraryDraft | None = None
    last_planning_ref: ArtifactRef | None = None
    current_stage: str = "preparation"
    preparation_used: int = 0
    transport_used: int = 0
    submit_repair_used: int = 0
    supplement_request_used: int = 0
    last_rejection: PlanningRejectionSnapshot | None = None
    budget_complete: bool = True
    provider_attempts_complete: bool = True
    usage_complete: bool = True
    no_progress: int = 0
    invocation_count: int = 0
    provider_attempts: int = 0
    usage: LLMUsage = field(default_factory=_zero_usage)
    direction_cursor: int = 0
    hub_cursor: int = 0
    matrix_cursor: int = 0
    supplement_fingerprints: list[str] = field(default_factory=list)
    research_remaining: int = 0
    place_remaining: int = 0
    pending_supplement_gaps: tuple[PlanningGap, ...] = ()
    supplement_rejection_reason: str | None = None
    pending_call_id: str | None = None
    closed: bool = False
    resumed: bool = False
    observed_evidence_handles: set[str] = field(default_factory=set)
    pending_gaps: list[PlanningGap] = field(default_factory=list)
    cache_hits: int = 0
    candidate_context_pending: bool = True
    business_hours: dict[str, tuple[int, tuple[str, ...]]] = field(default_factory=dict)


class ItineraryPlanningService:
    """端到端规划服务；注入依赖由调用方所有，本类只关闭自己创建的 operation。"""

    def __init__(
        self,
        *,
        runtime: DeepSeekRuntime,
        map_adapter: BaiduMapAdapter,
        artifact_repository: ArtifactRepository,
        budget_manager: ExecutionBudgetManager,
    ) -> None:
        try:
            prompt = load_planning_prompt()
            if prompt.version != PLANNING_PROMPT_VERSION or not prompt.sha256.startswith("sha256:"):
                raise ItineraryPlanningConfigurationError()
        except ItineraryPlanningConfigurationError:
            raise
        except Exception:
            raise ItineraryPlanningConfigurationError() from None
        required = (
            (runtime, "start_operation"),
            (map_adapter, "geocode"),
            (map_adapter, "calculate_route_matrix"),
            (map_adapter, "plan_directions"),
            (artifact_repository, "lookup"),
            (artifact_repository, "get"),
            (artifact_repository, "put"),
            (budget_manager, "consume"),
            (budget_manager, "get_snapshot"),
        )
        if any(not callable(getattr(owner, name, None)) for owner, name in required):
            raise ItineraryPlanningConfigurationError()
        self._runtime = runtime
        self._map_adapter = map_adapter
        self._repository = artifact_repository
        self._budget = budget_manager
        self._runs: dict[UUID, _Run] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}
        # A terminal OperationId is only a single-flight key together with the
        # request digest.  Reusing the id with another request must never make
        # the waiter inherit an unrelated result.
        self._flights: dict[tuple[OperationId, str], asyncio.Future[ItineraryPlanningOutcome]] = {}
        self._closed_run_ids: set[UUID] = set()
        self._issued_requests: dict[OperationId, str] = {}
        self._closed = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for run_id in tuple(self._runs):
            lock = self._locks.setdefault(run_id, asyncio.Lock())
            async with lock:
                run = self._runs.pop(run_id, None)
                if run is None:
                    continue
                run.closed = True
                close = getattr(run.operation, "close", None)
                if callable(close):
                    close()

    async def close_run(self, run_id: UUID) -> None:
        if type(run_id) is not UUID:
            raise ValueError("run id must be UUID")
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            run = self._runs.pop(run_id, None)
            if run is None:
                return
            run.closed = True
            self._closed_run_ids.add(run_id)
            close = getattr(run.operation, "close", None)
            if callable(close):
                close()

    async def plan_initial(self, request: ItineraryPlanningInitialRequest) -> ItineraryPlanningOutcome:
        if self._closed:
            raise ItineraryPlanningUsageError()
        try:
            fresh = cast(ItineraryPlanningInitialRequest, _fresh(request, ItineraryPlanningInitialRequest))
        except Exception:
            raise ItineraryPlanningUsageError() from None
        digest = fingerprint_request_initial(fresh)
        return await self._public_call(fresh.operation_plan, digest, lambda: self._initial_locked(fresh, digest))

    async def plan_resume(self, request: ItineraryPlanningResumeRequest) -> ItineraryPlanningOutcome:
        if self._closed:
            raise ItineraryPlanningUsageError()
        try:
            fresh = cast(ItineraryPlanningResumeRequest, _fresh(request, ItineraryPlanningResumeRequest))
        except Exception:
            raise ItineraryPlanningUsageError() from None
        digest = fingerprint_request_resume(fresh)
        return await self._public_call(fresh.operation_plan, digest, lambda: self._resume_locked(fresh, digest), resume=True)

    async def _public_call(self, plan: ItineraryPlanningOperationPlan, digest: str, work, *, resume: bool = False) -> ItineraryPlanningOutcome:
        if self._closed:
            return self._failed(None, None, PlanningFailureCode.RUN_CONTEXT_CLOSED)
        terminal_id = plan.terminal_successor_operation_id
        flight_key = (terminal_id, digest)
        existing = self._flights.get(flight_key)
        if existing is not None:
            return await asyncio.shield(existing)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ItineraryPlanningOutcome] = loop.create_future()
        self._flights[flight_key] = future
        lock = self._locks.setdefault(plan.run_id, asyncio.Lock())
        try:
            async with lock:
                replay = await self._terminal_replay(plan, digest)
                if replay is not None:
                    result = replay
                elif plan.run_id in self._closed_run_ids:
                    result = self._failed(None, None, PlanningFailureCode.RUN_CONTEXT_CLOSED)
                else:
                    issued_digest = self._issued_requests.get(terminal_id)
                    audit = None if issued_digest is not None and issued_digest != digest else await self._audit_missing_terminal(plan, check_budget=not resume, request_digest=digest)
                    if issued_digest is not None and issued_digest != digest:
                        result = self._failed(None, None, PlanningFailureCode.REQUEST_MISMATCH)
                    elif audit is not None:
                        result = audit
                    elif issued_digest is not None:
                        code = PlanningFailureCode.OPERATION_RESULT_INDETERMINATE if issued_digest == digest else PlanningFailureCode.REQUEST_MISMATCH
                        run = self._runs.get(plan.run_id)
                        result = self._failed(None if run is None else run.last_planning_ref, None if run is None else await self._budget_summary(run), code)
                    else:
                        result = await work()
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        except Exception:
            result = self._failed(None, None, PlanningFailureCode.INTERNAL_INVARIANT_BROKEN)
            if not future.done():
                future.set_result(result)
            return result
        finally:
            self._flights.pop(flight_key, None)

    async def _terminal_replay(self, plan: ItineraryPlanningOperationPlan, digest: str) -> ItineraryPlanningOutcome | None:
        try:
            lookup = await self._repository.lookup(operation_id=plan.terminal_successor_operation_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._failed(None, None, PlanningFailureCode.ARTIFACT_ERROR)
        if isinstance(lookup, ArtifactAbsent):
            return None
        if not isinstance(lookup, ArtifactFound):
            return self._failed(None, None, PlanningFailureCode.ARTIFACT_ERROR)
        if (
            lookup.ref.operation_id != plan.terminal_successor_operation_id
            or lookup.ref.artifact_type != "itinerary_planning.itinerary"
            or lookup.ref.schema_version != "2.0"
        ):
            return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
        try:
            read = await self._repository.get(lookup.ref, expected_model=ItineraryPlanningArtifact)
            artifact = cast(ItineraryPlanningArtifact, _fresh(read.content, ItineraryPlanningArtifact))
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
        if read.ref != lookup.ref or lookup.ref.content_hash != _artifact_content_hash(artifact):
            return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
        if artifact.request_digest != digest or artifact.directories.hotel_selection_refs[0].operation_id.run_id != plan.run_id:
            return self._failed(None, None, PlanningFailureCode.REQUEST_MISMATCH)
        return self._outcome_from_terminal(lookup.ref, artifact)

    async def _audit_missing_terminal(self, plan: ItineraryPlanningOperationPlan, *, check_budget: bool = True, request_digest: str | None = None) -> ItineraryPlanningOutcome | None:
        """Before any new model/map/budget/write side effect, audit a missing terminal.

        A plan artifact or consumed budget proves that an earlier execution was
        issued but its terminal result is absent.  That state is not safe to
        replay or resend, so it is surfaced as indeterminate.  The only safe
        continuation is the all-absent, zero-consumption case.
        """
        operation_ids = (
            *((slot.planning_state_operation_id, "itinerary_planning.itinerary", ItineraryPlanningArtifact) for slot in plan.invocation_slots),
            *((slot.action_operation_id, "itinerary_planning.model_action", ItineraryPlanningModelActionArtifact) for slot in plan.invocation_slots),
            *((item, "itinerary_planning.hub_geocode_observation", ItineraryPlanningHubGeocodeObservationArtifact) for item in plan.hub_geocode_operation_ids),
            *((item, "itinerary_planning.route_matrix_observation", ItineraryPlanningRouteMatrixObservationArtifact) for item in plan.matrix_operation_ids),
            *((item, "itinerary_planning.directions_observation", ItineraryPlanningDirectionsObservationArtifact) for item in plan.direction_operation_ids),
        )
        try:
            found = False
            for operation_id, artifact_type, expected in operation_ids:
                lookup = await self._repository.lookup(operation_id=operation_id)
                if isinstance(lookup, ArtifactFound):
                    expected_schema_version = "5.0" if artifact_type == "itinerary_planning.model_action" else "2.0" if artifact_type == "itinerary_planning.itinerary" else "1.0"
                    if lookup.ref.operation_id != operation_id or lookup.ref.schema_version != expected_schema_version or lookup.ref.artifact_type != artifact_type:
                        return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
                    read = await self._repository.get(lookup.ref, expected_model=expected)
                    content = _fresh(read.content, expected)
                    if read.ref != lookup.ref or _artifact_content_hash(content) != lookup.ref.content_hash:
                        return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
                    if request_digest is not None and content.request_digest != request_digest:
                        return self._failed(None, None, PlanningFailureCode.REQUEST_MISMATCH)
                    found = True
                elif not isinstance(lookup, ArtifactAbsent):
                    return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
            if found:
                return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
            if not check_budget:
                return None
            iteration = await self._budget.get_snapshot(key=ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY)
            direction = await self._budget.get_snapshot(key=self._direction_key_for_plan(plan))
            resume_offset = (
                plan.invocation_slots[0].invocation_slot_index
                if plan.invocation_slots
                else 17
            )
            iteration_consumed = getattr(iteration, "consumed", 0) or 0
            # A resume plan intentionally inherits the already consumed
            # prefix.  Only consumption beyond that prefix is evidence of a
            # partially issued resume operation.
            consumed = iteration_consumed > resume_offset
            if resume_offset == 0:
                consumed = consumed or bool(getattr(direction, "consumed", 0))
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
        if found or consumed:
            return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
        return None

    @staticmethod
    def _direction_key_for_plan(plan: ItineraryPlanningOperationPlan) -> ExecutionBudgetKey:
        from .models import ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY
        return ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY

    async def _initial_locked(self, request: ItineraryPlanningInitialRequest, digest: str) -> ItineraryPlanningOutcome:
        if request.operation_plan.run_id in self._runs:
            return self._failed(None, None, PlanningFailureCode.REQUEST_MISMATCH)
        try:
            confirmed = cast(ConfirmedTravelRequirement, _fresh(request.confirmed, ConfirmedTravelRequirement))
            _validate_supported_confirmed_requirement(confirmed)
        except (TypeError, ValueError, AttributeError):
            return self._failed(None, None, PlanningFailureCode.INPUT_INVALID)
        try:
            hotel = await self._read_upstream(request.hotel_selection_ref, HotelSelectionArtifact, "hotel.selection")
            candidate_artifact = await self._read_upstream(request.candidate_set_ref, CandidateResearchArtifact, "candidate_research.candidate_set")
            state = getattr(candidate_artifact.record, "state", None)
            if (
                getattr(candidate_artifact.record, "record_type", None) not in {"completed", "incomplete"}
                or state is None
                or state.confirmed_requirement_digest != _confirmed_digest(confirmed)
                or state.hotel_selection_ref != request.hotel_selection_ref
            ):
                return self._failed(None, None, PlanningFailureCode.PREDECESSOR_INVALID)
            candidates, evidence_refs, requirement_group_states = self._candidate_snapshot(candidate_artifact, confirmed=confirmed)
            if not candidates:
                return self._failed(None, None, PlanningFailureCode.INPUT_INVALID)
            if not any(candidate_is_eligible(item) for item in candidates):
                return self._failed(None, None, PlanningFailureCode.INPUT_INVALID)
            run = self._make_run(request_kind="initial", request_digest=digest, confirmed=confirmed, request=request, hotel=hotel, candidates=candidates, evidence_refs=evidence_refs, requirement_group_states=requirement_group_states)
            run.research_remaining = candidate_artifact.record.budgets.iteration_limit - candidate_artifact.record.budgets.iterations_consumed
            run.place_remaining = candidate_artifact.record.budgets.place_call_limit - candidate_artifact.record.budgets.place_calls_consumed
            self._runs[run.run_id] = run
            return await self._drive(run, first=True)
        except asyncio.CancelledError:
            raise
        except _Indeterminate:
            return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
        except ArtifactCapacityExceededError:
            return self._failed(None, None, PlanningFailureCode.ARTIFACT_CAPACITY_EXCEEDED)
        except (ArtifactRepositoryError, KeyError, TypeError, ValueError, AttributeError):
            return self._failed(None, None, PlanningFailureCode.PREDECESSOR_INVALID)

    async def _resume_locked(self, request: ItineraryPlanningResumeRequest, digest: str) -> ItineraryPlanningOutcome:
        saved_budgets = None
        run = self._runs.get(request.operation_plan.run_id)
        if run is None or run.closed:
            return self._failed(None, None, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
        if run.request_kind not in {"initial", "resume"} or run.pending_call_id is None:
            return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.PREDECESSOR_INVALID)
        try:
            predecessor = await self._read_upstream(request.predecessor_planning_ref, ItineraryPlanningArtifact, "itinerary_planning.itinerary")
            if not isinstance(predecessor.record, AwaitingCandidateSupplementRecord):
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.PREDECESSOR_INVALID)
            saved_budgets = _from_budget_snapshot(predecessor.record.state.budgets)
            if (
                not run.supplement_fingerprints
                or predecessor.record.gap_fingerprint != run.supplement_fingerprints[-1]
            ):
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.PREDECESSOR_INVALID)
            if request.predecessor_planning_ref != run.last_planning_ref:
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.REQUEST_MISMATCH)
            if predecessor.request_digest != run.request_digest or predecessor.record.pending_call_id != run.pending_call_id:
                return self._failed(run.last_planning_ref, _from_budget_snapshot(predecessor.record.state.budgets), PlanningFailureCode.PREDECESSOR_INVALID)
            original_request = _supplement_request(predecessor, request.supplemental_operation_plan)
            supplemented = await self._read_upstream(request.supplemented_candidate_set_ref, CandidateResearchArtifact, "candidate_research.candidate_set")
            if (
                supplemented.mode is not ResearchMode.SUPPLEMENTAL
                or getattr(supplemented.record, "record_type", None) not in {"completed", "incomplete"}
            ):
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.PREDECESSOR_INVALID)
            if (
                supplemented.predecessor_ref != original_request.predecessor_candidate_set_ref
                or request.supplemented_candidate_set_ref.operation_id != request.supplemental_operation_plan.terminal_successor_operation_id
                or supplemented.invocation_request_digest != _supplement_request_digest(original_request)
            ):
                return self._failed(run.last_planning_ref, _from_budget_snapshot(predecessor.record.state.budgets), PlanningFailureCode.REQUEST_MISMATCH)
            supplemented_state = getattr(supplemented.record, "state", None)
            if (
                supplemented_state is None
                or supplemented_state.confirmed_requirement_digest != _confirmed_digest(run.confirmed)
                or supplemented_state.hotel_selection_ref != run.hotel_ref
            ):
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.REQUEST_MISMATCH)
            candidates, evidence_refs, requirement_group_states = self._candidate_snapshot(supplemented, confirmed=run.confirmed)
            if not any(candidate_is_eligible(item) for item in candidates):
                return self._failed(run.last_planning_ref, _from_budget_snapshot(predecessor.record.state.budgets), PlanningFailureCode.INPUT_INVALID)
            if tuple(evidence_refs[:len(run.candidate_evidence_refs)]) != run.candidate_evidence_refs:
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.REQUEST_MISMATCH)
            old_candidates = run.candidates
            if len(candidates) < len(old_candidates):
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.REQUEST_MISMATCH)
            if any(
                old.admission_ordinal != new.admission_ordinal or old.core.uid != new.core.uid
                for old, new in zip(old_candidates, candidates, strict=False)
            ):
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.REQUEST_MISMATCH)
            changed_old_candidate = any(old != new for old, new in zip(old_candidates, candidates, strict=False))
            expected = tuple(range(run.invocation_count, 17))
            supplied = tuple(item.invocation_slot_index for item in request.operation_plan.invocation_slots)
            if supplied != expected:
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.OPERATION_PLAN_INVALID)
            if predecessor.request_digest != run.request_digest or predecessor.record.pending_call_id != run.pending_call_id:
                return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.REQUEST_MISMATCH)
            audit = await self._audit_missing_terminal(request.operation_plan, request_digest=digest)
            if audit is not None:
                return audit
            business_hours = await self._business_hours_for(candidates, evidence_refs)
            run.candidates = candidates
            run.research_remaining = supplemented.record.budgets.iteration_limit - supplemented.record.budgets.iterations_consumed
            run.place_remaining = supplemented.record.budgets.place_call_limit - supplemented.record.budgets.place_calls_consumed
            run.candidate_ref = request.supplemented_candidate_set_ref
            run.candidate_set_refs.append(request.supplemented_candidate_set_ref)
            run.candidate_evidence_refs = evidence_refs
            run.requirement_group_states = requirement_group_states
            self._add_candidate_handles(run, candidates, append_only=True)
            self._add_candidate_evidence_handles(run, evidence_refs)
            run.business_hours = business_hours
            run.candidate_context_pending = True
            if run.draft is not None:
                run.draft = run.draft.model_copy(update={"candidate_set_ref": request.supplemented_candidate_set_ref})
                if changed_old_candidate:
                    self._revalidate_saved_prefix(run)
            run.plan = request.operation_plan
            # Resume plans carry only the remaining operation-id prefixes; the
            # already persisted observations stay in the run cache/directories.
            run.hub_cursor = 0
            run.matrix_cursor = 0
            run.direction_cursor = 0
            run.request_kind = "resume"
            run.resumed = True
            # All artifacts emitted after this point belong to this exact
            # resume request, including action/observation/terminal records.
            run.request_digest = digest
            result = await self._continue_after_supplement(run, predecessor.record.state)
            return result
        except asyncio.CancelledError:
            raise
        except _Indeterminate:
            return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
        except ArtifactCapacityExceededError:
            return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.ARTIFACT_CAPACITY_EXCEEDED)
        except (ArtifactRepositoryError, KeyError, TypeError, ValueError, AttributeError):
            return self._failed(run.last_planning_ref, saved_budgets, PlanningFailureCode.PREDECESSOR_INVALID)

    def _make_run(self, *, request_kind: str, request_digest: str, confirmed: ConfirmedTravelRequirement, request: Any, hotel: HotelSelectionArtifact, candidates: tuple[CandidateRecord, ...], evidence_refs: tuple[ArtifactRef, ...], requirement_group_states: tuple[Any, ...]) -> _Run:
        if not isinstance(hotel.decision, HotelSelectedDecision):
            raise ValueError("hotel selection is not selected")
        destination = confirmed.requirement.destination
        days = confirmed.requirement.time_window.sightseeing_days
        if destination is None or days is None:
            raise ValueError("confirmed planning window is incomplete")
        hotel_ref = HotelLocationRef()
        directory = HandleDirectory()
        hotel_handle = directory.add_location(
            hotel_ref,
            BaiduCoordinate(latitude=hotel.decision.selected_candidate.coordinate.latitude, longitude=hotel.decision.selected_candidate.coordinate.longitude),
            label=hotel.decision.selected_candidate.name,
            handle="hotel-handle",
        )
        self._add_candidate_handles_to(directory, candidates)
        arrival = confirmed.requirement.time_window.arrival
        departure = confirmed.requirement.time_window.departure
        hub_queries: list[HubEndpointQuery] = []
        for kind, value in (("arrival", arrival), ("departure", departure)):
            if value.hub_name:
                hub_queries.append(HubEndpointQuery(kind, value.hub_name, destination, _canonical_hash(value.hub_name)))
                directory.add(kind="hub_query", value=value.hub_name, label=value.hub_name, handle=f"hub-query-{kind}")
        context = fingerprint_planning_context(
            request.operation_plan.run_id,
            _confirmed_digest(confirmed),
            request.hotel_selection_ref,
            _canonical_hash(arrival.hub_name) if arrival.hub_name else _canonical_hash(None),
            _canonical_hash(departure.hub_name) if departure.hub_name else _canonical_hash(None),
        )
        for index, ref in enumerate(evidence_refs):
            directory.add(kind="candidate_evidence", value=ref, handle=f"evidence-handle-{index:04d}")
        executor = MapExecutor(adapter=self._map_adapter, budget_manager=self._budget, context_digest=context, hub_queries=hub_queries)
        operation = self._runtime.start_operation(ModelRole.ITINERARY_PLANNING)
        run = _Run(
            run_id=request.operation_plan.run_id, request_digest=request_digest, request_kind=request_kind,
            confirmed=confirmed, hotel_ref=request.hotel_selection_ref, candidate_ref=request.candidate_set_ref,
            plan=request.operation_plan, candidates=candidates, directory=directory, map_executor=executor,
            operation=operation, candidate_evidence_refs=evidence_refs, requirement_group_states=requirement_group_states, context_digest=context,
            candidate_set_refs=[request.candidate_set_ref],
        )
        return run

    def _add_candidate_handles_to(self, directory: HandleDirectory, candidates: tuple[CandidateRecord, ...]) -> None:
        for item in candidates:
            ref = CandidateLocationRef(admission_ordinal=item.admission_ordinal, uid=item.core.uid)
            coordinate = BaiduCoordinate(latitude=item.core.coordinate.latitude, longitude=item.core.coordinate.longitude)
            directory.add_location(ref, coordinate, label=item.core.name, handle=f"candidate-handle-{item.admission_ordinal:04d}")

    def _add_candidate_handles(self, run: _Run, candidates: tuple[CandidateRecord, ...], *, append_only: bool) -> None:
        existing = {
            handle: run.directory.require(handle)
            for handle, kind, _ in run.directory.snapshot()
            if kind == "candidate"
        }
        known = {
            cast(CandidateLocationRef, entry.value).uid
            for entry in existing.values()
            if isinstance(entry.value, CandidateLocationRef)
        }
        # Map fingerprints already include endpoint coordinates.  A successor
        # must not evict unrelated route facts from this unchanged context.
        for item in candidates:
            if item.core.uid in known:
                # The public handle remains stable, while its current typed
                # location binding follows the supplemented candidate record.
                # HandleDirectory intentionally exposes no mutating update
                # API, so replace only the private run-local entry here.
                handle = f"candidate-handle-{item.admission_ordinal:04d}"
                current = run.directory.require(handle, kind="candidate")
                ref = CandidateLocationRef(admission_ordinal=item.admission_ordinal, uid=item.core.uid)
                coordinate = BaiduCoordinate(latitude=item.core.coordinate.latitude, longitude=item.core.coordinate.longitude)
                if current.value != ref or current.coordinate != coordinate or current.label != item.core.name:
                    entries = object.__getattribute__(run.directory, "_entries")
                    entries[handle] = type(current)(handle, "candidate", ref, coordinate, item.core.name)
                continue
            ref = CandidateLocationRef(admission_ordinal=item.admission_ordinal, uid=item.core.uid)
            run.directory.add_location(ref, BaiduCoordinate(latitude=item.core.coordinate.latitude, longitude=item.core.coordinate.longitude), label=item.core.name, handle=f"candidate-handle-{item.admission_ordinal:04d}")

    def _add_candidate_evidence_handles(self, run: _Run, refs: tuple[ArtifactRef, ...]) -> None:
        """Keep evidence handles globally indexed across an initial/resume run."""
        for index, ref in enumerate(refs):
            handle = f"evidence-handle-{index:04d}"
            if handle not in run.directory:
                run.directory.add(kind="candidate_evidence", value=ref, handle=handle)
                continue
            current = run.directory.require(handle, kind="candidate_evidence")
            if current.value != ref:
                entries = object.__getattribute__(run.directory, "_entries")
                entries[handle] = type(current)(handle, "candidate_evidence", ref, None, current.label)

    @staticmethod
    def _invocation_slot(run: _Run, invocation_index: int):
        for slot in run.plan.invocation_slots:
            if slot.invocation_slot_index == invocation_index:
                return slot
        raise ToolBatchError("operation_plan_invalid")

    def _candidate_snapshot(
        self,
        artifact: CandidateResearchArtifact,
        *,
        confirmed: ConfirmedTravelRequirement,
    ) -> tuple[tuple[CandidateRecord, ...], tuple[ArtifactRef, ...], tuple[Any, ...]]:
        state = getattr(artifact.record, "state", None)
        if state is None or not hasattr(state, "candidates"):
            raise ValueError("candidate set has no recoverable state")
        candidates = tuple(
            cast(CandidateRecord, _fresh(CandidateRecord.model_validate_json(item.model_dump_json(exclude_none=False), strict=True), CandidateRecord))
            for item in state.candidates
        )
        if tuple(item.admission_ordinal for item in candidates) != tuple(range(len(candidates))):
            raise ValueError("candidate ordinals are not contiguous")
        if len({item.core.uid for item in candidates}) != len(candidates):
            raise ValueError("candidate uid is duplicated")
        refs = tuple(state.observation_refs)
        requirement_group_states = tuple(state.requirement_group_states)
        expected_must_groups = _confirmed_must_requirement_groups(confirmed)
        actual_must_groups = tuple(
            (group.group_id, tuple(group.requirement_item_ids))
            for group in requirement_group_states
            if group.requirement_kind == "must_visit"
        )
        if actual_must_groups != expected_must_groups:
            raise ValueError("candidate set must requirement coverage is incomplete or forged")
        all_item_ids = [item_id for group in requirement_group_states for item_id in group.requirement_item_ids]
        if len(all_item_ids) != len(set(all_item_ids)):
            raise ValueError("candidate set requirement item is duplicated across groups")
        if any(
            group.requirement_kind == "avoid"
            and any(item_id.startswith("reqitem:must_visit:") for item_id in group.requirement_item_ids)
            for group in requirement_group_states
        ):
            raise ValueError("candidate set must item is placed in an avoid group")
        return candidates, refs, requirement_group_states

    async def _read_upstream(self, ref: ArtifactRef, expected: type[BaseModel], artifact_type: str) -> BaseModel:
        if type(ref) is not ArtifactRef or ref.artifact_type != artifact_type or ref.schema_version != ("2.0" if artifact_type == "itinerary_planning.itinerary" else "1.0"):
            raise ValueError("upstream reference mismatch")
        lookup = await self._repository.lookup(operation_id=ref.operation_id)
        if not isinstance(lookup, ArtifactFound) or lookup.ref != ref:
            raise ValueError("upstream reference is absent or changed")
        result = await self._repository.get(ref, expected_model=expected)
        fresh = _fresh(result.content, expected)
        encoded = json.dumps(fresh.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if result.ref != ref or ref.content_hash != "sha256:" + hashlib.sha256(encoded).hexdigest() or ref.size_bytes != len(encoded):
            raise _Indeterminate()
        content_run_id = getattr(fresh, "run_id", None)
        if content_run_id is not None and content_run_id != ref.operation_id.run_id:
            raise ValueError("upstream artifact run mismatch")
        return fresh

    def _directory_model(self, run: _Run) -> ArtifactRefDirectories:
        return ArtifactRefDirectories(
            schema_version="1.0",
            hotel_selection_refs=(run.hotel_ref,), candidate_set_refs=tuple(run.candidate_set_refs),
            model_action_refs=tuple(run.action_refs), hub_geocode_refs=tuple(run.hub_refs),
            matrix_refs=tuple(run.matrix_refs), directions_refs=tuple(run.directions_refs),
            candidate_evidence_refs=tuple(run.candidate_evidence_refs),
        )

    async def _persist(self, *, operation_id: OperationId, artifact_type: str, content: BaseModel, expected: type[BaseModel]) -> tuple[ArtifactRef, BaseModel]:
        if operation_id.phase is not WorkflowPhase.ITINERARY_PLANNING:
            raise ValueError("artifact operation phase mismatch")
        if type(content) is not expected:
            raise ValueError("artifact model mismatch")
        artifact_schema_version = "5.0" if artifact_type == "itinerary_planning.model_action" else "2.0" if artifact_type == "itinerary_planning.itinerary" else "1.0"
        request = ArtifactWriteRequest(
            operation_id=operation_id, artifact_type=artifact_type, schema_version=artifact_schema_version,
            content=content.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True),
        )
        lookup = await self._repository.lookup(operation_id=operation_id)
        if isinstance(lookup, ArtifactFound):
            if lookup.ref.operation_id != operation_id or lookup.ref.artifact_type != artifact_type or lookup.ref.schema_version != artifact_schema_version:
                raise _Indeterminate()
            read = await self._repository.get(lookup.ref, expected_model=expected)
            existing = _fresh(read.content, expected)
            if lookup.ref.content_hash != _artifact_content_hash(existing):
                raise _Indeterminate()
            if _canonical_hash(existing.model_dump(mode="python", exclude_none=False)) != _canonical_hash(content.model_dump(mode="python", exclude_none=False)):
                raise _Indeterminate()
            return lookup.ref, existing
        if not isinstance(lookup, ArtifactAbsent):
            raise _Indeterminate()
        try:
            ref = await self._repository.put(request)
        except asyncio.CancelledError:
            raise
        except ArtifactCapacityExceededError:
            raise
        except Exception as error:
            # A write may have reached durable storage before the exception.
            check = await self._repository.lookup(operation_id=operation_id)
            if isinstance(check, ArtifactFound):
                raise _ArtifactWriteIndeterminate() from None
            if isinstance(error, ArtifactRepositoryError):
                raise
            raise _ArtifactWriteIndeterminate() from None
        if (
            type(ref) is not ArtifactRef
            or ref.operation_id != operation_id
            or ref.artifact_type != artifact_type
            or ref.schema_version != artifact_schema_version
            or ref.content_hash != _artifact_content_hash(content)
        ):
            raise _Indeterminate()
        read = await self._repository.get(ref, expected_model=expected)
        if read.ref != ref:
            raise _Indeterminate()
        fresh = _fresh(read.content, expected)
        if ref.content_hash != _artifact_content_hash(fresh):
            raise _Indeterminate()
        return ref, fresh

    def _action_summary(self, call: ValidatedToolCall, ordinal: int) -> Any:
        from .artifacts import ModelCallSummary
        args = call.arguments.model_dump(mode="python", exclude_none=False)
        handles = _handles(args)
        if len(handles) > 6000:
            raise ToolBatchError("action_batch_invalid")
        return ModelCallSummary(
            schema_version="1.0", call_id=call.call_id, ordinal=ordinal,
            tool=PlanningTool(call.tool_name), arguments_fingerprint=_canonical_hash(args),
            handle_count=len(handles), handles_digest=_canonical_hash(handles), handle_preview=handles[:16],
        )

    async def _persist_action(self, run: _Run, calls: tuple[ValidatedToolCall, ...], metadata: InvocationMetadata | None, kind: str, disposition: str, request_stage: str, budget_category: str) -> ArtifactRef:
        operation_id = self._invocation_slot(run, run.invocation_count - 1).action_operation_id
        action = ItineraryPlanningModelActionArtifact(
            schema_version="5.0", request_digest=run.request_digest, invocation_index=run.invocation_count - 1,
            request_stage=request_stage, budget_category=budget_category,
            prompt_version=PLANNING_PROMPT_VERSION, prompt_hash=load_planning_prompt().sha256, action_kind=kind,
            call_summaries=tuple(self._action_summary(call, i) for i, call in enumerate(calls)),
            invocation_metadata=metadata, disposition=disposition,
        )
        ref, _ = await self._persist(operation_id=operation_id, artifact_type="itinerary_planning.model_action", content=action, expected=ItineraryPlanningModelActionArtifact)
        if not any(existing.artifact_id == ref.artifact_id for existing in run.action_refs):
            run.action_refs.append(ref)
        return ref

    def _hub_snapshot(self, result: GeocodeObservation) -> Any:
        from .artifacts import BaiduCoordinateSnapshot, HubGeocodeCandidateSnapshot, MapProvenanceSnapshot, MapPlanningFailureSnapshot
        success = None
        ambiguous: tuple[Any, ...] = ()
        provenance = None
        failure = None
        if result.status == "success":
            success = HubGeocodeCandidateSnapshot(
                schema_version="1.0", formatted_address=result.result.formatted_address,
                coordinate=BaiduCoordinateSnapshot(schema_version="1.0", coordinate_system="bd09ll", latitude=result.result.coordinate.latitude, longitude=result.result.coordinate.longitude),
                confidence=result.result.confidence,
            )
        elif result.status == "ambiguous":
            ambiguous = tuple(HubGeocodeCandidateSnapshot(schema_version="1.0", formatted_address=item.formatted_address, coordinate=BaiduCoordinateSnapshot(schema_version="1.0", coordinate_system="bd09ll", latitude=item.coordinate.latitude, longitude=item.coordinate.longitude), confidence=item.confidence) for item in result.candidates)
        if result.provenance is not None:
            provenance = MapProvenanceSnapshot(schema_version="1.0", source="baidu_maps_mcp", capability=result.provenance.capability, retrieved_at_utc=result.provenance.retrieved_at, verified=result.provenance.verified)
        if result.status == "failure":
            failure = MapPlanningFailureSnapshot(schema_version="1.0", failure_type="map", capability="geocoding", error_code=result.error_code, action_ordinal=result.action_ordinal, retryable=result.error_code in _MAP_CODES_RETRYABLE, possible_duplicate=False)
        return success, ambiguous, provenance, failure

    async def _persist_observations(self, run: _Run, action_ref: ArtifactRef, dispatch: ToolDispatchResult, calls: tuple[ValidatedToolCall, ...]) -> dict[int, list[ArtifactRef]]:
        refs_by_call = {index: [] for index in range(len(calls))}
        ordinals = {call.call_id: index for index, call in enumerate(calls)}
        for observation in dispatch.observations:
            ordinal = ordinals[observation.call_id]
            payload = observation.payload
            if isinstance(payload, GeocodeObservation):
                op = run.plan.hub_geocode_operation_ids[run.hub_cursor]
                run.hub_cursor += 1
                success, ambiguous, provenance, failure = self._hub_snapshot(payload)
                local = ItineraryPlanningHubGeocodeObservationArtifact(
                    schema_version="1.0", request_digest=run.request_digest, model_action_ref=action_ref,
                    action_ordinal=ordinal, endpoint_kind=payload.endpoint_kind, request_fingerprint=payload.request_fingerprint,
                    observation_status=payload.status, success_candidate=success, ambiguous_candidates=ambiguous,
                    provenance=provenance, map_failure=failure,
                )
                ref, _ = await self._persist(operation_id=op, artifact_type="itinerary_planning.hub_geocode_observation", content=local, expected=ItineraryPlanningHubGeocodeObservationArtifact)
                run.hub_refs.append(ref)
                refs_by_call[ordinal].append(ref)
                run.pending_gaps[:] = [gap for gap in run.pending_gaps if not (isinstance(gap, HubResolutionGap) and gap.endpoint_kind == payload.endpoint_kind)]
                if payload.status in {"no_result", "ambiguous"}:
                    run.pending_gaps.append(HubResolutionGap(schema_version="1.0", gap_id=f"hub-{payload.endpoint_kind}-unresolved",
                        evidence_refs=(ref,), endpoint_kind=payload.endpoint_kind, status=payload.status, candidate_count=len(payload.candidates)))
                if payload.status == "success":
                    location = HubLocationRef(endpoint_kind=payload.endpoint_kind, geocode_observation_index=len(run.hub_refs) - 1)
                    handle = f"hub-location-{payload.endpoint_kind}"
                    coordinate = BaiduCoordinate(latitude=payload.result.coordinate.latitude, longitude=payload.result.coordinate.longitude)
                    if handle in run.directory:
                        current = run.directory.require_location(handle)
                        entries = object.__getattribute__(run.directory, "_entries")
                        entries[handle] = type(current)(handle, "hub", location, coordinate, payload.result.formatted_address)
                    else:
                        run.directory.add_location(location, coordinate, label=payload.result.formatted_address, handle=handle)
            elif isinstance(payload, MatrixObservation):
                op = run.plan.matrix_operation_ids[run.matrix_cursor]
                run.matrix_cursor += 1
                from .artifacts import ItineraryPlanningRouteMatrixObservationArtifact, RouteMatrixCellSuccessSnapshot, RouteMatrixCellNoResultSnapshot, MapProvenanceSnapshot
                cells = tuple(RouteMatrixCellSuccessSnapshot(schema_version="1.0", status="success", origin_index=item.origin_index, destination_index=item.destination_index, distance_m=item.distance_m, duration_s=item.duration_s) if getattr(item, "status", None) == "success" else RouteMatrixCellNoResultSnapshot(schema_version="1.0", status="no_result", origin_index=item.origin_index, destination_index=item.destination_index) for item in payload.cells)
                provenance = None if payload.provenance is None else MapProvenanceSnapshot(schema_version="1.0", source="baidu_maps_mcp", capability=payload.provenance.capability, retrieved_at_utc=payload.provenance.retrieved_at, verified=payload.provenance.verified)
                from .artifacts import MapPlanningFailureSnapshot
                failure = None if payload.status != "failure" else MapPlanningFailureSnapshot(schema_version="1.0", failure_type="map", capability="route_matrix", error_code=payload.error_code, action_ordinal=payload.action_ordinal, retryable=payload.error_code in _MAP_CODES_RETRYABLE, possible_duplicate=False)
                local = ItineraryPlanningRouteMatrixObservationArtifact(schema_version="1.0", request_digest=run.request_digest, model_action_ref=action_ref, action_ordinal=ordinal, date=payload.matrix_date, mode=payload.mode, request_fingerprint=payload.request_fingerprint, ordered_locations=tuple(_to_location_snapshot(item.location) for item in payload.ordered_locations), cells=cells, observation_status=payload.status, provenance=provenance, map_failure=failure)
                ref, _ = await self._persist(operation_id=op, artifact_type="itinerary_planning.route_matrix_observation", content=local, expected=ItineraryPlanningRouteMatrixObservationArtifact)
                run.matrix_refs.append(ref)
                refs_by_call[ordinal].append(ref)
                run.matrix_observations.append(payload)
                run.directory.add(kind="matrix", value=payload, handle=f"matrix-handle-{len(run.matrix_refs) - 1:04d}")
            elif isinstance(payload, type(None)):
                continue
            elif hasattr(payload, "observations"):
                from .artifacts import ItineraryPlanningDirectionsObservationArtifact, MapProvenanceSnapshot, MapPlanningFailureSnapshot
                batch = payload
                for item in batch.observations:
                    if item.status == "failure":
                        failure = MapPlanningFailureSnapshot(schema_version="1.0", failure_type="map", capability="directions", error_code=item.error_code, action_ordinal=ordinal, retryable=item.error_code in _MAP_CODES_RETRYABLE, possible_duplicate=False)
                    else:
                        failure = None
                    provenance = None if item.provenance is None else MapProvenanceSnapshot(schema_version="1.0", source="baidu_maps_mcp", capability=item.provenance.capability, retrieved_at_utc=item.provenance.retrieved_at, verified=item.provenance.verified)
                    local = ItineraryPlanningDirectionsObservationArtifact(schema_version="1.0", request_digest=run.request_digest, model_action_ref=action_ref, action_ordinal=ordinal, leg_ordinal=item.leg_ordinal, leg_fingerprint=item.leg_fingerprint, origin=_to_location_snapshot(item.origin.location), destination=_to_location_snapshot(item.destination.location), transport_semantic=item.transport_semantic, route_mode=item.route_mode, alternatives_limit=item.alternatives_limit, observation_status=item.status, alternatives=tuple(_to_route_alternative_snapshot(x) for x in item.alternatives), provenance=provenance, map_failure=failure)
                    ref, _ = await self._persist(operation_id=item.operation_id, artifact_type="itinerary_planning.directions_observation", content=local, expected=ItineraryPlanningDirectionsObservationArtifact)
                    run.directions_refs.append(ref)
                    refs_by_call[ordinal].append(ref)
                    run.direction_observations.append(item)
                    run.directory.add(kind="directions", value=item, handle=item.directions_handle)
        return refs_by_call

    def _hubs_ready(self, run: _Run) -> bool:
        return all(f"hub-location-{endpoint}" in run.directory for endpoint in ("arrival", "departure"))

    def _advance_stage(self, run: _Run) -> PlanningStopReason | None:
        if run.current_stage == "preparation":
            if self._hubs_ready(run):
                run.current_stage, run.no_progress = "transport", 0
            elif run.preparation_used >= 2 or run.hub_cursor >= len(run.plan.hub_geocode_operation_ids):
                return PlanningStopReason.ITERATION_LIMIT_REACHED if run.preparation_used >= 2 else PlanningStopReason.NO_LEGAL_ACTION
        if run.current_stage == "transport" and run.transport_used >= 5:
            run.current_stage, run.no_progress = "submit_repair", 0
        if run.submit_repair_used >= 5 or run.invocation_count >= 17:
            return PlanningStopReason.ITERATION_LIMIT_REACHED
        return None

    def _effective_tools(self, run: _Run):
        if run.current_stage == "preparation":
            names = {"get_candidate_evidence", "geocode_hub"}
        elif run.current_stage == "transport":
            names = {"get_candidate_evidence", "calculate_route_matrix", "plan_directions", "submit_itinerary"}
        elif run.last_rejection is None or run.submit_repair_used >= 4:
            names = {"submit_itinerary"}
        else:
            names = {"get_candidate_evidence", "calculate_route_matrix", "plan_directions", "submit_itinerary"}
        if run.supplement_request_used < 5 and run.invocation_count < 16 and run.research_remaining > 0 and run.place_remaining > 0 and not (run.current_stage == "submit_repair" and (run.last_rejection is None or run.submit_repair_used >= 4)):
            names.add("request_candidate_supplement")
        return tuple(tool for tool in PLANNING_TOOL_CONTRACTS if tool.name in names)

    async def _admit_invocation(self, run: _Run, identity: InteractionCallIdentity) -> None:
        if identity.logical_call_index != run.invocation_count:
            raise _Indeterminate()
        slot = self._invocation_slot(run, run.invocation_count)
        try:
            result = await self._budget.consume(BudgetConsumeRequest(operation_id=slot.action_operation_id,
                key=ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY, amount=1))
        except BaseException:
            run.budget_complete = False
            raise
        if not isinstance(result, BudgetConsumed):
            run.budget_complete = False
            raise _Indeterminate()
        if (result.operation_id != slot.action_operation_id or result.key != ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY
            or result.amount != 1 or result.disposition != "applied" or result.snapshot.run_id != run.run_id
            or result.snapshot.limit != 17 or result.snapshot.consumed != run.invocation_count + 1):
            run.budget_complete = False
            raise _Indeterminate()
        run.invocation_count += 1

    def _classify_invocation(self, run: _Run, request_stage: str, calls=(), *, legal=False) -> str:
        category = request_stage
        if legal and len(calls) == 1:
            if calls[0].tool_name == "submit_itinerary":
                category = "submit_repair"
                run.current_stage, run.no_progress = "submit_repair", 0
            elif calls[0].tool_name == "request_candidate_supplement":
                category = "supplement_request"
        field_name = category + "_used"
        setattr(run, field_name, getattr(run, field_name) + 1)
        return category

    def _rejection_progress(self, run: _Run, args: SubmitItineraryArgs, rejected: _BusinessRejected) -> bool:
        from .artifacts import PlanningRejectionSnapshot
        def business(value):
            if isinstance(value, BaseModel):
                value = value.model_dump(mode="python", exclude_none=False)
            if isinstance(value, dict):
                return {key: business(item) for key, item in value.items() if key not in {"theme", "notes", "description", "explanation", "rationale"}}
            if isinstance(value, (tuple, list)):
                return tuple(business(item) for item in value)
            return value
        evidence = []
        for handle in dict.fromkeys(_handles(args)):
            if handle in run.directory:
                entry = run.directory.require(handle)
                if entry.kind == "candidate":
                    candidate = next((item for item in run.candidates if item.core.uid == entry.value.uid), None)
                    evidence.append((handle, candidate, run.business_hours.get(entry.value.uid)))
                elif entry.kind == "directions":
                    value = entry.value
                    evidence.append((handle, value.origin.location, value.destination.location, value.transport_semantic,
                        value.route_mode, value.status, tuple(_to_route_alternative_snapshot(item) for item in value.alternatives)))
                elif entry.kind == "matrix":
                    value = entry.value
                    evidence.append((handle, value.matrix_date, value.mode, value.status,
                        tuple(item.location for item in value.ordered_locations), value.cells))
                elif entry.kind in {"candidate_evidence", "hub"}:
                    evidence.append((handle, entry.value, entry.coordinate))
        current = PlanningRejectionSnapshot(reason=rejected.reason, field=rejected.field,
            business_fingerprint=_canonical_hash(business(args)), relevant_evidence_fingerprint=_canonical_hash(tuple(evidence)))
        repeated = run.last_rejection == current
        run.last_rejection = current
        return not repeated

    async def _drive(self, run: _Run, *, first: bool, pending_results: tuple[ToolResult, ...] = ()) -> ItineraryPlanningOutcome:
        with error_context(run.plan.run_id):
            return await self._drive_with_diagnostics(run, first=first, pending_results=pending_results)

    async def _drive_with_diagnostics(self, run: _Run, *, first: bool, pending_results: tuple[ToolResult, ...] = ()) -> ItineraryPlanningOutcome:
        while True:
            if run.closed:
                return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.RUN_CONTEXT_CLOSED)
            stop = self._advance_stage(run)
            if stop is not None:
                return await self._finish_incomplete(run, stop)
            await self._load_business_hours(run)
            request_stage = run.current_stage
            effective_tools = self._effective_tools(run)
            prompt = self._prompt(run)
            continuation_state = self._planning_state(run, include_candidates=run.candidate_context_pending)
            run.candidate_context_pending = False
            admitted_before = run.invocation_count
            with interaction_call("planning") as identity:
                try:
                    self._issued_requests[run.plan.terminal_successor_operation_id] = run.request_digest
                    if first:
                        response = await run.operation.ainvoke_tools(prompt, effective_tools,
                            admission_callback=lambda call: self._admit_invocation(run, call), request_stage=request_stage)
                        first = False
                    else:
                        pending_results = tuple(ToolResult(call_id=item.call_id, content=json.dumps({
                            "tool_result": json.loads(item.content),
                            **({"planning_state": continuation_state} if index == len(pending_results) - 1 else {}),
                        }, ensure_ascii=False, separators=(",", ":"))) for index, item in enumerate(pending_results))
                        response = await run.operation.acontinue_tools(pending_results, tools=effective_tools,
                            admission_callback=lambda call: self._admit_invocation(run, call), request_stage=request_stage)
                        pending_results = ()
                        run.pending_call_id = None
                except asyncio.CancelledError:
                    run.budget_complete = False
                    raise
                except Exception:
                    if run.invocation_count > admitted_before:
                        self._classify_invocation(run, request_stage)
                    run.provider_attempts_complete = run.usage_complete = False
                    return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
                admitted = run.invocation_count == admitted_before + 1
                try:
                    response = self._fresh_tool_outcome(response)
                except (TypeError, ValueError):
                    if admitted:
                        category = self._classify_invocation(run, request_stage)
                        await identity.record_validation(stage="tool_schema", passed=False, code="model_contract_invalid", budget_category=category)
                        await self._persist_action(run, (), None, "llm_failure", "failed", request_stage, category)
                    return await self._finish_control_failed(run, PlanningFailureCode.MODEL_CONTRACT_INVALID)
                if isinstance(response, LLMInvocationFailure):
                    run.provider_attempts_complete = run.usage_complete = False
                    if admitted:
                        category = self._classify_invocation(run, request_stage)
                        await identity.record_validation(stage="controller", passed=False, code=response.error.code.value, budget_category=category)
                        await self._persist_action(run, (), None, "llm_failure", "failed", request_stage, category)
                    failure = LLMPlanningFailure(schema_version="1.0", failure_type="llm", error=response.error,
                        invocation_index=max(0, run.invocation_count - 1), retryable=response.error.retryable, possible_duplicate=response.error.possible_duplicate)
                    return await self._finish_failed(run, failure)
                if not admitted:
                    return await self._finish_control_failed(run, PlanningFailureCode.MODEL_CONTRACT_INVALID)
                if isinstance(response, (ToolCallsReady, ToolTurnCompleted)):
                    run.provider_attempts += response.metadata.provider_attempts
                    run.usage = _add_usage(run.usage, response.metadata.usage)
                if isinstance(response, ToolTurnCompleted):
                    category = self._classify_invocation(run, request_stage)
                    await identity.record_validation(stage="controller", passed=False, code="no_terminal_submission", budget_category=category)
                    await self._persist_action(run, (), response.metadata, "final_text", "failed", request_stage, category)
                    return await self._finish_incomplete(run, PlanningStopReason.NO_TERMINAL_SUBMISSION)
                if not isinstance(response, ToolCallsReady) or not response.calls:
                    category = self._classify_invocation(run, request_stage)
                    await identity.record_validation(stage="controller", passed=False, code="model_contract_invalid", budget_category=category)
                    await self._persist_action(run, (), None, "llm_failure", "failed", request_stage, category)
                    return await self._finish_control_failed(run, PlanningFailureCode.MODEL_CONTRACT_INVALID)
                calls = tuple(response.calls)
                try:
                    self._validate_batch(run, calls, effective_tools=effective_tools)
                except ToolBatchError as error:
                    category = self._classify_invocation(run, request_stage)
                    await identity.record_validation(stage="batch", passed=False, code=error.code, budget_category=category)
                    try:
                        await self._persist_action(run, calls, response.metadata, "tool_calls", "controller_rejected", request_stage, category)
                    except (TypeError, ValueError, ToolBatchError):
                        await self._persist_action(run, (), None, "llm_failure", "failed", request_stage, category)
                    record_error("planning_batch_rejected", reason=error.code, invocation=run.invocation_count,
                        location=locations(error),
                        call_count=len(calls), tools=[call.tool_name if call.tool_name in _ARGS_BY_TOOL else "unknown_tool" for call in calls])
                    return await self._finish_control_failed(run,
                        PlanningFailureCode.MODEL_CONTRACT_INVALID if error.code == "unknown_tool" else PlanningFailureCode.ACTION_BATCH_INVALID)
                category = self._classify_invocation(run, request_stage, calls, legal=True)
                await identity.record_validation(stage="batch", passed=True, budget_category=category)
                try:
                    action_ref = await self._persist_action(run, calls, response.metadata, "tool_calls", "accepted", request_stage, category)
                except (TypeError, ValueError, ToolBatchError):
                    return await self._finish_control_failed(run, PlanningFailureCode.MODEL_CONTRACT_INVALID)
                except Exception:
                    return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.ARTIFACT_ERROR)
                if calls[0].tool_name == "submit_itinerary":
                    try:
                        result = await self._submit(run, cast(SubmitItineraryArgs, calls[0].arguments), action_ref)
                        if not isinstance(result, ItineraryPlanningCompleted):
                            await identity.record_validation(stage="submit", passed=False, code="submit_completion_failed", budget_category=category)
                            await identity.record_tool_result(tool_name="submit_itinerary", provider_tool_call_id=calls[0].call_id, ordinal=0,
                                outcome="failure", feedback=json.dumps({"status": result.outcome_type, "code": result.failure.code.value if hasattr(result, "failure") and hasattr(result.failure, "code") else "submit_completion_failed"}))
                            return result
                        await identity.record_validation(stage="submit", passed=True, budget_category=category)
                        await identity.record_tool_result(tool_name="submit_itinerary", provider_tool_call_id=calls[0].call_id,
                            ordinal=0, outcome="success", feedback=json.dumps({"status": result.outcome_type}), observation_ref_json=result.itinerary_ref.model_dump_json())
                        return result
                    except _BusinessRejected as rejected:
                        record_error("planning_submit_rejected", reason=rejected.reason, field=rejected.field, invocation=run.invocation_count,
                            request_stage=request_stage, budget_category=category, location=locations(rejected))
                        args = cast(SubmitItineraryArgs, calls[0].arguments)
                        if not rejected.reason.startswith("structured_demo_deferred"):
                            self._record_rejected_prefix(run, args)
                        progressed = self._rejection_progress(run, args, rejected)
                        feedback = {"status": "rejected", "code": "policy_rejected", "reason": rejected.reason, "field": rejected.field}
                        if isinstance(rejected, _BackupSlotsRejected):
                            feedback.update(day_no=rejected.day_no, expected_backup_kinds=list(rejected.slots.expected), actual_backup_kinds=list(rejected.slots.actual))
                        await identity.record_validation(stage="submit", passed=False, code=rejected.reason, field=rejected.field, budget_category=category)
                        pending_results = (ToolResult(call_id=calls[0].call_id, content=json.dumps(feedback, separators=(",", ":"))),)
                        await identity.record_tool_result(tool_name="submit_itinerary", provider_tool_call_id=calls[0].call_id, ordinal=0, outcome="rejected", feedback=pending_results[0].content)
                        await self._persist_state(run)
                        if not progressed:
                            return await self._finish_incomplete(run, PlanningStopReason.NO_PROGRESS)
                        if run.submit_repair_used >= 5:
                            return await self._finish_incomplete(run, PlanningStopReason.ITERATION_LIMIT_REACHED)
                        continue
                if calls[0].tool_name == "request_candidate_supplement":
                    try:
                        result = await self._supplement(run, cast(RequestCandidateSupplementArgs, calls[0].arguments), action_ref, calls[0].call_id)
                    except _BusinessRejected as rejected:
                        await identity.record_validation(stage="policy", passed=False, code=rejected.reason, field=rejected.field, budget_category=category)
                        await identity.record_tool_result(tool_name="request_candidate_supplement", provider_tool_call_id=calls[0].call_id,
                            ordinal=0, outcome="rejected", feedback=json.dumps({"status": "rejected", "reason": rejected.reason, "field": rejected.field}))
                        return await self._finish_incomplete(run, PlanningStopReason.CANDIDATE_SUPPLY_INSUFFICIENT)
                    except (ValueError, ArtifactRepositoryError, _Indeterminate):
                        return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
                    await identity.record_tool_result(tool_name="request_candidate_supplement", provider_tool_call_id=calls[0].call_id, ordinal=0,
                        outcome="success" if result is not None else "rejected", feedback=json.dumps({"status": "awaiting_candidate_supplement" if result is not None else "rejected", "reason": None if result is not None else run.supplement_rejection_reason}),
                        observation_ref_json=None if result is None else result.planning_ref.model_dump_json())
                    if result is not None:
                        return result
                    return await self._finish_incomplete(run, PlanningStopReason.NO_LEGAL_ACTION if run.supplement_rejection_reason == "no_legal_action" else PlanningStopReason.CANDIDATE_SUPPLY_INSUFFICIENT)
                try:
                    dispatcher = ToolDispatcher(directory=run.directory, map_executor=run.map_executor,
                        evidence_reader=lambda handles: self._read_evidence(run, handles), operation_ids=run.plan.direction_operation_ids[run.direction_cursor:])
                    dispatch = await dispatcher.dispatch(calls, sightseeing_days=self._days(run), iteration_index=run.invocation_count - 1, direction_budget_key=self._direction_key(run))
                    run.cache_hits += dispatch.cache_hits
                    refs_by_call = await self._persist_observations(run, action_ref, dispatch, calls)
                    pending_results = tool_results(dispatch, directory=run.directory)
                    observed = {item.call_id: item for item in dispatch.observations}
                    feedbacks = {item.call_id: item for item in pending_results}
                    def recorded_outcome(payload):
                        status = getattr(payload, "status", "success")
                        return "cached" if getattr(payload, "cache_hit", False) else "not_issued" if status in {"not_issued", "not_admitted_budget_exhausted", "reservation_barrier_failed"} else "no_result" if status == "no_result" else "failure" if status == "failure" else "rejected" if status == "policy_rejected" else "success"
                    for ordinal, call in enumerate(calls):
                        observation = observed.get(call.call_id)
                        if observation is None:
                            await identity.record_tool_result(tool_name=call.tool_name, provider_tool_call_id=call.call_id, ordinal=ordinal,
                                outcome="not_issued", feedback='{"status":"not_issued","reason":"batch_admission_refused"}')
                            continue
                        refs = refs_by_call[ordinal]
                        payload = observation.payload
                        feedback = feedbacks[call.call_id]
                        legs = getattr(payload, "observations", ())
                        await identity.record_tool_result(tool_name=call.tool_name, provider_tool_call_id=call.call_id, ordinal=ordinal,
                            outcome=recorded_outcome(payload), feedback=feedback.content,
                            observation_ref_json=refs[0].model_dump_json() if len(refs) == 1 and not legs else None)
                        for ref, leg in zip(refs, legs, strict=True) if legs else ():
                            await identity.record_tool_result(tool_name=call.tool_name, provider_tool_call_id=call.call_id, ordinal=ordinal,
                                outcome=recorded_outcome(leg), feedback=json.dumps({"status": leg.status, "leg_ordinal": leg.leg_ordinal,
                                    "observation_ref": ref.model_dump(mode="json")}), observation_ref_json=ref.model_dump_json())
                    if any(getattr(item.payload, "status", None) == "not_admitted_budget_exhausted" for item in dispatch.observations):
                        return await self._finish_incomplete(run, PlanningStopReason.DIRECTION_BUDGET_EXHAUSTED)
                    if any(getattr(item.payload, "status", None) == "reservation_barrier_failed" for item in dispatch.observations):
                        return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
                    map_failure = self._map_failure_from_dispatch(dispatch)
                    if map_failure is not None:
                        return await self._finish_failed(run, map_failure)
                    if run.current_stage != "submit_repair":
                        run.no_progress = 0 if self._dispatch_made_progress(run, dispatch) else run.no_progress + 1
                    run.direction_cursor += sum(len(call.arguments.legs) for call in calls if call.tool_name == "plan_directions")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.OBSERVATION_INVALID)
                stop = self._advance_stage(run)
                await self._persist_state(run)
                if stop is not None:
                    return await self._finish_incomplete(run, stop)
                if run.current_stage != "submit_repair" and run.no_progress >= 2:
                    return await self._finish_incomplete(run, PlanningStopReason.NO_PROGRESS)

    def _fresh_tool_outcome(self, value: object) -> object:
        if isinstance(value, ToolCallsReady):
            calls = []
            for call in value.calls:
                schema = _ARGS_BY_TOOL.get(call.tool_name)
                arguments = call.arguments
                if schema is not None:
                    arguments = schema.model_validate_json(
                        call.arguments.model_dump_json(exclude_none=False),
                        strict=True,
                    )
                calls.append(ValidatedToolCall(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    arguments=arguments,
                ))
            return ToolCallsReady(
                calls=tuple(calls),
                metadata=InvocationMetadata.model_validate_json(
                    value.metadata.model_dump_json(exclude_none=False),
                    strict=True,
                ),
            )
        if isinstance(value, ToolTurnCompleted):
            return ToolTurnCompleted.model_validate_json(value.model_dump_json(exclude_none=False), strict=True)
        if isinstance(value, LLMInvocationFailure):
            return LLMInvocationFailure.model_validate_json(value.model_dump_json(exclude_none=False), strict=True)
        return value

    def _validate_batch(self, run: _Run, calls: tuple[ValidatedToolCall, ...], *, effective_tools) -> None:
        if not calls:
            raise ToolBatchError("action_batch_invalid")
        dispatcher = ToolDispatcher(directory=run.directory)
        dispatcher.validate(calls, sightseeing_days=self._days(run))
        if any(call.tool_name not in {tool.name for tool in effective_tools} for call in calls):
            raise ToolBatchError("unknown_tool")
        remaining_hubs = len(run.plan.hub_geocode_operation_ids) - run.hub_cursor
        remaining_matrix = len(run.plan.matrix_operation_ids) - run.matrix_cursor
        remaining_directions = len(run.plan.direction_operation_ids) - run.direction_cursor
        geocode_count = sum(call.tool_name == PlanningTool.GEOCODE_HUB.value for call in calls)
        matrix_count = sum(call.tool_name == PlanningTool.CALCULATE_ROUTE_MATRIX.value for call in calls)
        direction_count = sum(len(call.arguments.legs) for call in calls if call.tool_name == PlanningTool.PLAN_DIRECTIONS.value)
        if geocode_count > remaining_hubs or matrix_count > remaining_matrix or direction_count > remaining_directions:
            raise ToolBatchError("operation_plan_invalid")

    def _days(self, run: _Run) -> int:
        value = run.confirmed.requirement.time_window.sightseeing_days
        if value is None:
            raise ValueError("sightseeing days missing")
        return value

    def _direction_key(self, run: _Run) -> ExecutionBudgetKey:
        from .models import ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY
        return ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY

    def _map_failure_from_dispatch(self, dispatch: ToolDispatchResult) -> MapPlanningFailure | None:
        for outer_ordinal, observation in enumerate(dispatch.observations):
            payload = observation.payload
            capability: str | None = None
            error_code: BaiduMapErrorCode | None = None
            action_ordinal = outer_ordinal
            if isinstance(payload, GeocodeObservation) and payload.status == "failure":
                capability = "geocoding"
                error_code = payload.error_code
                action_ordinal = payload.action_ordinal
            elif isinstance(payload, MatrixObservation) and payload.status == "failure":
                capability = "route_matrix"
                error_code = payload.error_code
                action_ordinal = payload.action_ordinal
            elif hasattr(payload, "observations"):
                for item in payload.observations:
                    if item.status == "failure":
                        capability = "directions"
                        error_code = item.error_code
                        action_ordinal = outer_ordinal
                        break
            if capability is not None and isinstance(error_code, BaiduMapErrorCode):
                return MapPlanningFailure(
                    schema_version="1.0",
                    failure_type="map",
                    capability=capability,
                    error_code=error_code,
                    action_ordinal=action_ordinal,
                    retryable=error_code in _MAP_CODES_RETRYABLE,
                    possible_duplicate=False,
                )
        return None

    def _read_evidence(self, run: _Run, handles: tuple[str, ...]) -> _CandidateEvidenceObservation:
        values: list[_CandidateEvidenceCandidate] = []
        by_handle = {f"candidate-handle-{item.admission_ordinal:04d}": item for item in run.candidates}
        for handle in handles:
            candidate = by_handle.get(handle)
            if candidate is None:
                return _CandidateEvidenceObservation(status="no_result", candidates=(), evidence_handles=(), reason_code=None)
            evidence_handles = tuple(
                f"evidence-handle-{index:04d}"
                for index in candidate.observation_indexes
                if 0 <= index < len(run.candidate_evidence_refs)
            )
            values.append(_CandidateEvidenceCandidate(
                candidate_handle=handle,
                state=candidate.state.value,
                role_facts=tuple({"role": x.role.value, "verdict": x.verdict.value} for x in candidate.roles),
                interest_facts=tuple({"interest": item.value, "verdict": "confirmed"} for item in candidate.interests),
                meal_facts=tuple({"meal": item.value, "verdict": "confirmed"} for item in candidate.meal_intents),
                requirement_facts=tuple(
                    {"requirement_kind": kind, "requirement_group_id": item.requirement_group_id, "verdict": item.verdict.value}
                    for kind, matches in (("must_visit", candidate.must_matches), ("avoid", candidate.avoid_matches))
                    for item in matches
                ),
                details_status=candidate.details.status,
                evidence_handles=evidence_handles,
                business_hours_facts=() if candidate.core.uid not in run.business_hours else ({
                    "evidence_handle": f"evidence-handle-{run.business_hours[candidate.core.uid][0]:04d}",
                    "business_hours": run.business_hours[candidate.core.uid][1],
                },),
            ))
        union = tuple(dict.fromkeys(handle for item in values for handle in item.evidence_handles))
        return _CandidateEvidenceObservation(status="success", candidates=tuple(values), evidence_handles=union, reason_code=None)

    def _dispatch_made_progress(self, run: _Run, dispatch: ToolDispatchResult) -> bool:
        progress = False
        for observation in dispatch.observations:
            payload = observation.payload
            if isinstance(payload, _CandidateEvidenceObservation):
                fresh = tuple(handle for handle in payload.evidence_handles if handle not in run.observed_evidence_handles)
                run.observed_evidence_handles.update(payload.evidence_handles)
                progress = progress or bool(fresh)
            elif isinstance(payload, (GeocodeObservation, MatrixObservation)):
                progress = progress or not payload.cache_hit
            elif hasattr(payload, "observations"):
                progress = progress or any(not item.cache_hit for item in payload.observations)
        return progress

    def _planning_state(
        self, run: _Run, *, include_candidates: bool
    ) -> dict[str, object]:
        return {
            "current_stage": run.current_stage,
            "model_invocations_consumed": run.invocation_count,
            "preparation_remaining": max(0, 2 - run.preparation_used),
            "transport_remaining": max(0, 5 - run.transport_used),
            "submit_repair_remaining": max(0, 5 - run.submit_repair_used),
            "supplement_request_remaining": max(0, 5 - run.supplement_request_used),
            "effective_tools": [tool.name for tool in self._effective_tools(run)],
            "matrix_slots_total": len(run.plan.matrix_operation_ids),
            "remaining_matrix_slots": max(
                0, len(run.plan.matrix_operation_ids) - run.matrix_cursor
            ),
            "direction_slots_total": len(run.plan.direction_operation_ids),
            "remaining_direction_slots": max(
                0, len(run.plan.direction_operation_ids) - run.direction_cursor
            ),
            "no_progress_streak": run.no_progress,
            "accepted_supplements": len(run.supplement_fingerprints),
            "effective_policy": None if run.policy is None else run.policy.model_dump(mode="json"),
            "draft": None if run.draft is None else run.draft.model_dump(mode="json", exclude={"hotel_selection_ref", "candidate_set_ref"}),
            "gaps": [item.model_dump(mode="json") for item in self._current_gaps(run)],
            "candidates": [
                self._read_evidence(
                    run, (f"candidate-handle-{item.admission_ordinal:04d}",)
                ).model_dump(mode="json")
                for item in run.candidates
            ] if include_candidates else [],
        }

    def _prompt(self, run: _Run) -> PromptEnvelope:
        objectives = ("先处理当前 typed 缺口，再取得必要的路线证据，最后提交完整行程或请求候选补充。",)
        prompt = make_planning_prompt(run.confirmed, planning_context_digest=run.context_digest, directory=run.directory, objectives=objectives)
        state = self._planning_state(
            run, include_candidates=run.candidate_context_pending
        )
        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return PromptEnvelope(system_text=prompt.system_text, user_text=prompt.user_text + "\nBEGIN_TYPED_PLANNING_STATE\n" + encoded + "\nEND_TYPED_PLANNING_STATE")

    async def _persist_state(self, run: _Run) -> ArtifactRef:
        from .artifacts import InProgressItineraryRecord
        state = await self._state_snapshot(run, pending=None)
        gaps = tuple(_to_gap_summary_snapshot(item) for item in self._current_gaps(run))
        record = InProgressItineraryRecord(schema_version="1.0", record_type="in_progress", state=state, gaps=gaps)
        artifact = ItineraryPlanningArtifact(schema_version="2.0", request_digest=run.request_digest, parent_planning_ref=run.last_planning_ref, directories=self._directory_model(run), record=record)
        slot = self._invocation_slot(run, run.invocation_count - 1)
        ref, _ = await self._persist(operation_id=slot.planning_state_operation_id, artifact_type="itinerary_planning.itinerary", content=artifact, expected=ItineraryPlanningArtifact)
        run.last_planning_ref = ref
        return ref

    async def _state_snapshot(self, run: _Run, *, pending: str | None) -> PlanningStateSnapshot:
        return PlanningStateSnapshot(schema_version="2.0", request_kind="resume" if run.resumed else "initial",
            requirement_digest=_confirmed_digest(run.confirmed), effective_policy=None if run.policy is None else self._policy_snapshot(run.policy),
            draft=None if run.draft is None else _to_draft_snapshot(run.draft), budgets=_to_budget_snapshot(await self._budget_summary(run)),
            current_stage=run.current_stage, model_invocations_consumed=run.invocation_count, preparation_used=run.preparation_used,
            transport_used=run.transport_used, submit_repair_used=run.submit_repair_used, supplement_request_used=run.supplement_request_used,
            no_progress_streak=run.no_progress, last_rejection=run.last_rejection, supplement_fingerprints=tuple(run.supplement_fingerprints), pending_call_id=pending)

    def _policy_snapshot(self, policy: EffectivePlanningPolicy) -> Any:
        from .artifacts import _to_policy_snapshot
        return _to_policy_snapshot(policy)

    def _current_gaps(self, run: _Run) -> tuple[PlanningGapSummary, ...]:
        reason = "no_terminal_submission"
        if run.no_progress >= 2:
            reason = "no_progress"
        elif run.invocation_count >= 17 or run.submit_repair_used >= 5:
            reason = "iteration_limit_reached"
        gap = PlanningProcessGapSummary(schema_version="1.0", summary_type="planning_process", gap_id=f"planning-{reason}", evidence_indexes=(), process_reason=reason)
        return (*tuple(self._gap_summary_from_gap(run, item) for item in run.pending_gaps), gap)

    @staticmethod
    def _lock_pace_after_policy_rejection(run: _Run, proposal: Any) -> None:
        """Lock only a legal pace; never persist a deferred text-derived rule."""
        if run.policy is not None:
            return
        try:
            run.policy = lock_effective_pace(proposal, confirmed=run.confirmed)
        except (TypeError, ValueError, _PolicyBusinessRejected):
            return

    async def _submit(self, run: _Run, args: SubmitItineraryArgs, action_ref: ArtifactRef) -> ItineraryPlanningOutcome:
        try:
            policy = effective_policy_from_proposal(args.effective_policy, confirmed=run.confirmed, locked_policy=run.policy)
        except _PolicyLockedMismatch:
            raise _BusinessRejected("locked_policy_rejected") from None
        except _PolicyBusinessRejected as error:
            self._lock_pace_after_policy_rejection(run, args.effective_policy)
            raise _BusinessRejected({"policy source field is not confirmed": "policy_source_unconfirmed", "model pace conflicts with confirmed pace": "pace_conflicts_with_requirement", "structured_demo_deferred_policy_rule_rejected": "structured_demo_deferred_policy_rule_rejected"}.get(str(error), "policy_invalid"), "effective_policy") from None
        if run.policy is None:
            run.policy = policy
        try:
            draft = self._materialize_draft(run, args.draft, policy)
            self._validate_draft(run, draft)
        except _BusinessRejected:
            raise
        except (TypeError, ValueError, KeyError, StopIteration) as error:
            record_error("planning_proposal_invalid", tool="submit_itinerary",
                         errors=validation_details(error, ItineraryDraft) if isinstance(error, ValidationError) else [],
                         location=locations(error), invocation=run.invocation_count)
            raise _BusinessRejected("proposal_structure_or_reference_invalid", "draft") from None
        run.draft = draft
        return await self._finish_completed(run)

    async def _load_business_hours(self, run: _Run) -> None:
        run.business_hours = await self._business_hours_for(run.candidates, run.candidate_evidence_refs)

    async def _business_hours_for(self, candidates: tuple[CandidateRecord, ...], evidence_refs: tuple[ArtifactRef, ...]) -> dict[str, tuple[int, tuple[str, ...]]]:
        facts = {}
        for candidate in candidates:
            index = candidate.details.observation_index
            if candidate.details.status != "success" or index is None:
                continue
            ref = evidence_refs[index]
            observation = await self._read_upstream(ref, research.CandidateResearchPlaceObservationArtifact, "candidate_research.place_observation")
            result = observation.result
            if result.record_type == "details_success" and result.details.uid == candidate.core.uid and result.provenance.verified:
                if result.details.business_hours:
                    facts[candidate.core.uid] = (index, result.details.business_hours)
        return facts

    def _validate_business_evidence(self, run: _Run, uid: str, state: BusinessHoursState, indexes: tuple[int, ...]) -> None:
        if state is not BusinessHoursState.COMPATIBLE:
            return
        fact = run.business_hours.get(uid)
        if fact is None or fact[0] not in indexes:
            raise _BusinessRejected("business_hours_unverified")

    def _location(self, run: _Run, handle: str) -> tuple[Any, str]:
        entry = run.directory.require_location(handle)
        return cast(Any, entry.value), handle

    @staticmethod
    def _transfer_dates(transfer: BoundaryTransfer) -> set[date]:
        start = transfer.route_leg.interval.start_at.date()
        end = transfer.route_leg.interval.end_at.date()
        if end < start:
            return set()
        return {start + timedelta(days=index) for index in range((end - start).days + 1)}

    def _day_kind_from_boundaries(self, draft: ItineraryDraft, day: DayPlan) -> PlanningDayKind | None:
        """Derive the only legal kind from actual boundary occupancy and visits."""
        touched = day.date in (
            self._transfer_dates(draft.arrival_transfer)
            | self._transfer_dates(draft.departure_transfer)
        )
        if not touched:
            day_start = datetime.combine(day.date, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai"))
            day_end = day_start + timedelta(days=1)
            for previous in draft.days:
                if previous.date >= day.date:
                    continue
                occupied = (*previous.visits, *previous.route_legs)
                if any(item.interval.start_at < day_end and item.interval.end_at > day_start for item in occupied):
                    touched = True
                    break
        if not day.visits:
            return PlanningDayKind.TRANSIT_ONLY if touched else None
        return PlanningDayKind.PARTIAL if touched else PlanningDayKind.FULL

    @staticmethod
    def _interval_belongs_to_day(interval: TimeInterval | RouteTimeInterval, day_date: date) -> bool:
        next_date = day_date + timedelta(days=1)
        if isinstance(interval, RouteTimeInterval):
            return interval.start_at.date() in {day_date, next_date} and interval.end_at.date() in {day_date, next_date}
        return interval.start_at.date() == day_date and interval.end_at.date() in {day_date, next_date}

    def _materialize_draft(self, run: _Run, proposal: ProposedItineraryDraft, policy: EffectivePlanningPolicy) -> ItineraryDraft:
        for proposed_day in proposal.days:
            main_handles = tuple(
                visit.candidate_handle for visit in proposed_day.visits
            )
            backup_handles = tuple(
                backup.candidate_handle for backup in proposed_day.backups
            )
            if len(main_handles) != len(set(main_handles)):
                raise _BusinessRejected("main_candidate_duplicate")
            if len(backup_handles) != len(set(backup_handles)):
                raise _BusinessRejected("backup_candidate_duplicate")
            if set(main_handles) & set(backup_handles):
                raise _BusinessRejected("backup_reuses_main_candidate")

        def route(value: Any, field: str) -> RouteLeg:
            origin = cast(Any, run.directory.require_location(value.origin_handle).value)
            destination = cast(Any, run.directory.require_location(value.destination_handle).value)
            entry = run.directory.require(value.directions_handle, kind="directions")
            observation = cast(DirectionsObservation, entry.value)
            selected = selected_direction_summary(observation, value.alternative_index)
            directions_index = next(index for index, ref in enumerate(run.directions_refs) if ref.operation_id == observation.operation_id)
            if observation.leg_id != value.leg_id:
                raise _BusinessRejected("route_leg_binding_mismatch")
            minutes = route_scheduled_minutes(selected.duration_s)
            end_at = value.start_at + timedelta(minutes=minutes)
            if value.end_at is not None and value.end_at != end_at:
                raise _BusinessRejected("route_end_time_mismatch", field + ".end_at")
            return RouteLeg(schema_version="1.0", leg_id=value.leg_id, origin=origin, destination=destination, interval=RouteTimeInterval(start_at=value.start_at, end_at=end_at), transport_semantic=value.transport_semantic, route_mode=value.route_mode, directions_observation_index=directions_index, alternative_index=value.alternative_index, distance_m=selected.distance_m, duration_s=selected.duration_s, scheduled_minutes=minutes)

        def visit(value: Any) -> ScheduledVisit:
            location = cast(CandidateLocationRef, run.directory.require_location(value.candidate_handle).value)
            indexes = tuple(
                run.candidate_evidence_refs.index(run.directory.require(handle, kind="candidate_evidence").value)
                for handle in value.business_evidence_handles
            )
            if value.business_hours_state is BusinessHoursState.COMPATIBLE and not indexes:
                raise ValueError("compatible business hours require cited candidate evidence")
            return ScheduledVisit(schema_version="1.0", visit_id=value.visit_id, location=location, visit_kind=value.visit_kind, interval=TimeInterval(start_at=value.start_at, end_at=value.end_at), duration_source=value.duration_source, must_group_ids=value.must_group_ids, time_sensitive_anchor_ids=value.time_sensitive_anchor_ids, business_hours_state=value.business_hours_state, business_evidence_indexes=indexes, requires_verification=value.business_hours_state.value == "unknown")

        def day(value: Any, day_index: int) -> DayPlan:
            def backup(item: Any) -> BackupOption:
                matrix = cast(MatrixObservation, run.directory.require(item.matrix_handle, kind="matrix").value)
                if item.matrix_mode is not MatrixTravelMode.WALKING or matrix.mode is not MatrixTravelMode.WALKING or matrix.status != "success":
                    raise ValueError("backup requires a successful walking matrix")
                expected_cells = len(matrix.ordered_locations) ** 2
                if len(matrix.cells) != expected_cells or len({(cell.origin_index, cell.destination_index) for cell in matrix.cells}) != expected_cells:
                    raise ValueError("backup matrix must be a complete ordered square")
                if matrix.provenance is None or matrix.provenance.capability is not MapCapability.ROUTE_MATRIX or matrix.provenance.verified is not True:
                    raise ValueError("backup matrix provenance is not verified")
                matrix_index = next(i for i, obs in enumerate(run.matrix_observations) if obs.request_fingerprint == matrix.request_fingerprint)
                candidate = cast(CandidateLocationRef, run.directory.require_location(item.candidate_handle).value)
                core = next(
                    cast(CandidateLocationRef, run.directory.require_location(visit.candidate_handle).value)
                    for visit in value.visits
                    if visit.visit_id == item.core_visit_id
                )
                origin_index = next(i for i, location in enumerate(matrix.ordered_locations) if location.location == core)
                destination_index = next(i for i, location in enumerate(matrix.ordered_locations) if location.location == candidate)
                cell = next(
                    (
                        cell for cell in matrix.cells
                        if getattr(cell, "status", None) == "success"
                        and cell.origin_index == origin_index
                        and cell.destination_index == destination_index
                    ),
                    None,
                )
                if cell is None:
                    raise ValueError("backup candidate has no matching matrix cell")
                if cell.distance_m > 3000:
                    raise _BusinessRejected("backup_distance_exceeded")
                record = next(item for item in run.candidates if item.core.uid == candidate.uid)
                business_indexes = tuple(record.observation_indexes)
                return BackupOption(schema_version="1.0", backup_kind=item.backup_kind, core_visit_id=item.core_visit_id, candidate=candidate, matrix_observation_index=matrix_index, matrix_mode=item.matrix_mode, distance_m=cell.distance_m, business_hours_state=BusinessHoursState.UNKNOWN, business_evidence_indexes=business_indexes, requires_verification=True)
            backups = tuple(backup(item) for item in value.backups)
            return DayPlan(schema_version="1.0", date=value.date, day_kind=value.day_kind, theme=value.theme, open_intervals=value.open_intervals, visits=tuple(visit(item) for item in value.visits), route_legs=tuple(route(item, f"draft.days[{day_index}].route_legs[{index}]") for index, item in enumerate(value.route_legs)), backups=backups)

        arrival_route = route(proposal.arrival_transfer.route_leg, "draft.arrival_transfer.route_leg")
        departure_route = route(proposal.departure_transfer.route_leg, "draft.departure_transfer.route_leg")
        arrival_hub = cast(HubLocationRef, run.directory.require_location(proposal.arrival_transfer.hub_handle).value)
        departure_hub = cast(HubLocationRef, run.directory.require_location(proposal.departure_transfer.hub_handle).value)
        hotel = cast(HotelLocationRef, run.directory.require_location(proposal.arrival_transfer.hotel_handle).value)
        arrival_info = run.confirmed.requirement.time_window.arrival
        departure_info = run.confirmed.requirement.time_window.departure
        arrival_minutes = boundary_processing_minutes(arrival_info.hub_type or HubType.RAILWAY_STATION, "arrival", is_approximate=arrival_info.is_approximate)
        departure_minutes = boundary_processing_minutes(departure_info.hub_type or HubType.RAILWAY_STATION, "departure", is_approximate=departure_info.is_approximate)
        return ItineraryDraft(schema_version="1.0", requirement_digest=_confirmed_digest(run.confirmed), hotel_selection_ref=run.hotel_ref, candidate_set_ref=run.candidate_ref, effective_policy=policy, arrival_transfer=BoundaryTransfer(schema_version="1.0", transfer_kind="arrival", hub=arrival_hub, hotel=hotel, route_leg=arrival_route, processing_minutes=arrival_minutes, approximate_extra_minutes=30 if arrival_info.is_approximate else 0), days=tuple(day(item, index) for index, item in enumerate(proposal.days)), departure_transfer=BoundaryTransfer(schema_version="1.0", transfer_kind="departure", hub=departure_hub, hotel=hotel, route_leg=departure_route, processing_minutes=departure_minutes, approximate_extra_minutes=30 if departure_info.is_approximate else 0), upstream_gap_ids=proposal.upstream_gap_ids)

    def _validate_draft(self, run: _Run, draft: ItineraryDraft, *, allow_prefix: bool = False, allow_missing_backups: bool = False) -> None:
        _check_business(validate_effective_policy, draft.effective_policy, field="effective_policy")
        if draft.effective_policy.daily_time_rules or draft.effective_policy.time_sensitive_must or draft.effective_policy.transport_rules:
            raise _BusinessRejected("structured_demo_deferred_policy_rule_rejected")
        if draft.requirement_digest != _confirmed_digest(run.confirmed) or draft.hotel_selection_ref != run.hotel_ref or draft.candidate_set_ref != run.candidate_ref:
            raise _BusinessRejected()
        window = run.confirmed.requirement.time_window
        if window.sightseeing_start_date is None or window.sightseeing_end_date is None or window.sightseeing_days is None:
            raise _BusinessRejected()
        try:
            start_date = date.fromisoformat(window.sightseeing_start_date)
            end_date = date.fromisoformat(window.sightseeing_end_date)
        except (TypeError, ValueError):
            raise _BusinessRejected() from None
        expected_dates = tuple(start_date + timedelta(days=index) for index in range(window.sightseeing_days))
        draft_dates = tuple(item.date for item in draft.days)
        if end_date != expected_dates[-1] or (draft_dates != expected_dates and not (allow_prefix and draft_dates == expected_dates[:len(draft_dates)])):
            raise _BusinessRejected()
        records = {item.core.uid: item for item in run.candidates}
        all_main: list[CandidateLocationRef] = []
        all_backup: list[CandidateLocationRef] = []
        all_visits: list[ScheduledVisit] = []
        seen_leg_ids: set[str] = set()

        def validate_route(leg: RouteLeg, field: str) -> None:
            if leg.leg_id in seen_leg_ids:
                raise _BusinessRejected("route_leg_duplicate", field + ".leg_id")
            seen_leg_ids.add(leg.leg_id)
            if not 0 <= leg.directions_observation_index < len(run.direction_observations):
                raise _BusinessRejected()
            obs = run.direction_observations[leg.directions_observation_index]
            if obs.leg_id != leg.leg_id:
                raise _BusinessRejected("route_leg_binding_mismatch", field + ".directions_handle")
            if not direction_matches(
                obs,
                origin=LocationBinding("check", leg.origin, self._coordinate(run, leg.origin), None),
                destination=LocationBinding("check", leg.destination, self._coordinate(run, leg.destination), None),
                route_mode=leg.route_mode,
                alternative_index=leg.alternative_index,
            ):
                raise _BusinessRejected()
            selected = obs.alternatives[leg.alternative_index]
            if (leg.distance_m, leg.duration_s) != (selected.distance_m, selected.duration_s):
                raise _BusinessRejected("route_evidence_mismatch", field + ".directions_handle")
            if not 0 <= leg.directions_observation_index < len(run.directions_refs):
                raise _BusinessRejected()
            ref = run.directions_refs[leg.directions_observation_index]
            if ref.operation_id != obs.operation_id or ref.operation_id.run_id != run.run_id:
                raise _BusinessRejected()

        if draft.arrival_transfer.hub.endpoint_kind != "arrival" or draft.departure_transfer.hub.endpoint_kind != "departure":
            raise _BusinessRejected()
        if draft.arrival_transfer.route_leg.origin != draft.arrival_transfer.hub or draft.arrival_transfer.route_leg.destination != draft.arrival_transfer.hotel:
            raise _BusinessRejected()
        if draft.departure_transfer.route_leg.origin != draft.departure_transfer.hotel or draft.departure_transfer.route_leg.destination != draft.departure_transfer.hub:
            raise _BusinessRejected()
        if draft.arrival_transfer.hotel != draft.departure_transfer.hotel:
            raise _BusinessRejected()
        validate_route(draft.arrival_transfer.route_leg, "draft.arrival_transfer.route_leg")
        validate_route(draft.departure_transfer.route_leg, "draft.departure_transfer.route_leg")
        try:
            validate_route_leg_capacity(draft.arrival_transfer.route_leg, draft.effective_policy.effective_pace)
            validate_route_leg_capacity(draft.departure_transfer.route_leg, draft.effective_policy.effective_pace)
        except ValueError:
            raise _BusinessRejected() from None

        def endpoint_datetime(value: object) -> datetime | None:
            if value is None:
                return None
            try:
                parsed = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                raise _BusinessRejected() from None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return parsed.astimezone(ZoneInfo("Asia/Shanghai"))

        arrival_at = endpoint_datetime(window.arrival.local_datetime)
        departure_at = endpoint_datetime(window.departure.local_datetime)
        arrival_minimum = None if arrival_at is None else arrival_at + timedelta(minutes=draft.arrival_transfer.processing_minutes + draft.arrival_transfer.approximate_extra_minutes)
        departure_latest = None if departure_at is None else departure_at - timedelta(minutes=draft.departure_transfer.processing_minutes + draft.departure_transfer.approximate_extra_minutes)
        arrival_expected = boundary_processing_minutes(
            window.arrival.hub_type or HubType.RAILWAY_STATION,
            "arrival",
            is_approximate=window.arrival.is_approximate,
        )
        departure_expected = boundary_processing_minutes(
            window.departure.hub_type or HubType.RAILWAY_STATION,
            "departure",
            is_approximate=window.departure.is_approximate,
        )
        if (
            draft.arrival_transfer.processing_minutes != arrival_expected
            or draft.departure_transfer.processing_minutes != departure_expected
            or draft.arrival_transfer.approximate_extra_minutes != (30 if window.arrival.is_approximate else 0)
            or draft.departure_transfer.approximate_extra_minutes != (30 if window.departure.is_approximate else 0)
        ):
            raise _BusinessRejected()
        if arrival_minimum is not None and draft.arrival_transfer.route_leg.interval.start_at < arrival_minimum:
            raise _BusinessRejected("arrival_processing_overlap", "draft.arrival_transfer.route_leg.start_at")
        if departure_latest is not None and draft.departure_transfer.route_leg.interval.end_at > departure_latest:
            raise _BusinessRejected("departure_processing_overlap", "draft.departure_transfer.route_leg.start_at")
        for day_index, day in enumerate(draft.days):
            derived_kind = self._day_kind_from_boundaries(draft, day)
            prefix_tail = allow_prefix and day_index == len(draft.days) - 1
            if derived_kind is None:
                raise _BusinessRejected("empty_day_without_transfer", "day_kind")
            if day.day_kind is not derived_kind and not (prefix_tail and day.day_kind is PlanningDayKind.PARTIAL):
                raise _BusinessRejected("day_kind_mismatch", "day_kind")
            day_field = f"draft.days[{day_index}]"
            _check_business(validate_density, day, draft.effective_policy.effective_pace, field=day_field + ".visits")
            _check_business(validate_meal_slots, day, time_sensitive_must=draft.effective_policy.time_sensitive_must, field=day_field + ".visits")
            if draft.effective_policy.time_sensitive_must:
                raise _BusinessRejected("structured_demo_deferred_policy_rule_rejected")
            _check_business(validate_route_capacity, day, draft.effective_policy.effective_pace, field=day_field + ".route_legs")
            # Missing backups are legal in every mode; supplied entries remain
            # subject to the same checks, including when retaining a prefix.
            try:
                validate_backup_options(day)
            except _BackupSlotsMismatch as mismatch:
                raise _BackupSlotsRejected(day_index + 1, mismatch) from None
            except ValueError:
                raise _BusinessRejected("backup_options_invalid", day_field + ".backups") from None
            for visit_index, item in enumerate(day.visits):
                if item.time_sensitive_anchor_ids:
                    raise _BusinessRejected("structured_demo_deferred_policy_rule_rejected")
                try:
                    validate_visit_duration(item)
                except ValueError:
                    raise _BusinessRejected("structured_demo_deferred_duration_rejected", f"{day_field}.visits[{visit_index}].interval") from None
                _check_business(validate_business_state, item, field=f"{day_field}.visits[{visit_index}].business_hours_state")
            if any(
                not self._interval_belongs_to_day(interval, day.date)
                for interval in (
                    *day.open_intervals,
                    *(visit.interval for visit in day.visits),
                    *(leg.interval for leg in day.route_legs),
                )
            ):
                raise _BusinessRejected()
            for visit in day.visits:
                record = records.get(visit.location.uid)
                role = ResearchRole.RESTAURANT if visit.visit_kind in {VisitKind.LUNCH, VisitKind.DINNER} else ResearchRole.ATTRACTION
                if record is None or not candidate_uid_is_allowed(record, role):
                    raise _BusinessRejected("candidate_role_not_allowed", "visits.candidate_handle")
                if not candidate_is_eligible(record, role):
                    raise _BusinessRejected("candidate_not_eligible", "visits.candidate_handle")
                if visit.business_hours_state is BusinessHoursState.COMPATIBLE:
                    self._validate_business_evidence(run, visit.location.uid, visit.business_hours_state, visit.business_evidence_indexes)
                    if (
                        not visit.business_evidence_indexes
                        or not set(visit.business_evidence_indexes).issubset(set(record.observation_indexes))
                    ):
                        raise _BusinessRejected()
                if visit.visit_kind in {VisitKind.ATTRACTION_LUNCH, VisitKind.ATTRACTION_DINNER}:
                    if not candidate_is_eligible(record, ResearchRole.RESTAURANT):
                        raise _BusinessRejected()
                    required_meal = MealIntent.LUNCH if visit.visit_kind is VisitKind.ATTRACTION_LUNCH else MealIntent.DINNER
                    if required_meal not in record.meal_intents:
                        raise _BusinessRejected()
                elif visit.visit_kind is VisitKind.LUNCH and MealIntent.LUNCH not in record.meal_intents:
                    raise _BusinessRejected("meal_intent_mismatch")
                elif visit.visit_kind is VisitKind.DINNER and MealIntent.DINNER not in record.meal_intents:
                    raise _BusinessRejected("meal_intent_mismatch")
                all_main.append(visit.location)
                all_visits.append(visit)
            for backup in day.backups:
                record = records.get(backup.candidate.uid)
                role = ResearchRole.ATTRACTION if backup.backup_kind == "attraction" else ResearchRole.RESTAURANT
                if record is None or not candidate_uid_is_allowed(record, role):
                    raise _BusinessRejected("candidate_role_not_allowed", day_field + ".backups.candidate_handle")
                if (
                    backup.backup_kind == "lunch" and MealIntent.LUNCH not in record.meal_intents
                ) or (
                    backup.backup_kind == "dinner" and MealIntent.DINNER not in record.meal_intents
                ):
                    raise _BusinessRejected("backup_meal_intent_mismatch")
                if not 0 <= backup.matrix_observation_index < len(run.matrix_observations):
                    raise _BusinessRejected()
                matrix = run.matrix_observations[backup.matrix_observation_index]
                if (
                    matrix.matrix_date != day.date
                    or matrix.mode is not MatrixTravelMode.WALKING
                    or matrix.status != "success"
                    or matrix.provenance is None
                    or matrix.provenance.capability is not MapCapability.ROUTE_MATRIX
                    or matrix.provenance.verified is not True
                ):
                    raise _BusinessRejected()
                core = next((item for item in day.visits if item.visit_id == backup.core_visit_id), None)
                if core is None:
                    raise _BusinessRejected()
                core_is_role = (
                    role is ResearchRole.ATTRACTION
                    and core.visit_kind in {VisitKind.ATTRACTION, VisitKind.ATTRACTION_LUNCH, VisitKind.ATTRACTION_DINNER}
                ) or (
                    role is ResearchRole.RESTAURANT
                    and ((backup.backup_kind == "lunch" and core.visit_kind in {VisitKind.LUNCH, VisitKind.ATTRACTION_LUNCH})
                         or (backup.backup_kind == "dinner" and core.visit_kind in {VisitKind.DINNER, VisitKind.ATTRACTION_DINNER}))
                )
                if not core_is_role or not set(backup.business_evidence_indexes).issubset(set(record.observation_indexes)):
                    raise _BusinessRejected()
                if any(
                    index < 0 or index >= len(run.candidate_evidence_refs)
                    for index in backup.business_evidence_indexes
                ):
                    raise _BusinessRejected()
                current_core = self._coordinate(run, core.location)
                current_candidate = self._coordinate(run, backup.candidate)
                if not any(
                    binding.location == core.location and binding.coordinate == current_core
                    for binding in matrix.ordered_locations
                ) or not any(
                    binding.location == backup.candidate and binding.coordinate == current_candidate
                    for binding in matrix.ordered_locations
                ):
                    raise _BusinessRejected()
                if backup.business_hours_state is BusinessHoursState.COMPATIBLE and not backup.business_evidence_indexes:
                    raise _BusinessRejected()
                self._validate_business_evidence(run, backup.candidate.uid, backup.business_hours_state, backup.business_evidence_indexes)
                all_backup.append(backup.candidate)
            for leg_index, leg in enumerate(day.route_legs):
                validate_route(leg, f"{day_field}.route_legs[{leg_index}]")

            default_interval = default_open_interval(day.date)
            lower = default_interval.start_at
            upper = default_interval.end_at
            if any(interval.start_at < lower or interval.end_at > upper for interval in (*day.open_intervals, *(visit.interval for visit in day.visits), *(leg.interval for leg in day.route_legs))):
                raise _BusinessRejected("default_daily_window")

        if draft.days:
            first_day, last_day = draft.days[0], draft.days[-1]
            if arrival_at is not None and arrival_at.date() in {item.date for item in draft.days} and arrival_at.date() != first_day.date:
                raise _BusinessRejected()
            if departure_at is not None and departure_at.date() in {item.date for item in draft.days} and departure_at.date() != last_day.date:
                raise _BusinessRejected()
            arrival_end = draft.arrival_transfer.route_leg.interval.end_at
            departure_start = draft.departure_transfer.route_leg.interval.start_at
            if arrival_end > departure_start:
                raise _BusinessRejected("boundary_transfers_overlap", "draft.arrival_transfer")
            previous_return = arrival_end
            for day in draft.days:
                if not day.route_legs:
                    continue
                outbound = day.route_legs[0].interval.start_at
                returned = day.route_legs[-1].interval.end_at
                if outbound < previous_return or returned > departure_start:
                    raise _BusinessRejected("day_route_boundary_overlap", "draft.days.route_legs")
                previous_return = returned

        must_groups = {
            group.group_id
            for group in run.requirement_group_states
            if group.requirement_kind == "must_visit"
        }
        scheduled_must = {group_id for visit in all_visits for group_id in visit.must_group_ids}
        if not allow_prefix and not must_groups.issubset(scheduled_must):
            raise _BusinessRejected("must_visit_unscheduled", "draft.days.visits")
        for visit in all_visits:
            record = records[visit.location.uid]
            confirmed = {match.requirement_group_id for match in record.must_matches if match.verdict is MatchVerdict.CONFIRMED}
            if not set(visit.must_group_ids).issubset(confirmed):
                raise _BusinessRejected("must_visit_evidence_mismatch", "draft.days.visits.must_group_ids")
        _check_business(validate_unique_main_and_backup_uids, all_main, all_backup, field="draft.days")

    def _record_rejected_prefix(self, run: _Run, args: SubmitItineraryArgs) -> bool:
        """Retain only a fully materialized, independently validated prefix."""
        try:
            policy = effective_policy_from_proposal(
                args.effective_policy,
                confirmed=run.confirmed,
                source_handle_indexes={f"evidence-handle-{i:04d}": i for i in range(len(run.candidate_evidence_refs))},
                locked_policy=run.policy,
            )
        except _PolicyBusinessRejected:
            # There is no valid proposed policy from which to derive a new
            # prefix. A previously locked policy may still validate an
            # independently safe prefix, but the unsupported anchor itself
            # can never authorize that prefix.
            if run.policy is None:
                return False
            policy = run.policy
        for count in range(len(args.draft.days), 0, -1):
            try:
                candidate_proposal = args.draft.model_copy(update={"days": args.draft.days[:count]})
                prefix = self._materialize_draft(run, candidate_proposal, policy)
                self._validate_draft(run, prefix, allow_prefix=True, allow_missing_backups=True)
            except (TypeError, ValueError, KeyError, StopIteration, _BusinessRejected):
                continue
            return self._retain_prefix(run, prefix)
        # If a day is only partially materializable, keep the longest ordered
        # visit prefix.  The tail is explicitly partial so its snapshot cannot
        # claim full-day density or meal completion.
        for day_index in range(len(args.draft.days) - 1, -1, -1):
            original = args.draft.days[day_index]
            for count in range(len(original.visits), 0, -1):
                try:
                    tail = original.model_copy(update={
                        "day_kind": PlanningDayKind.PARTIAL,
                        "visits": original.visits[:count],
                        "route_legs": original.route_legs[:count + 1],
                        "backups": (),
                    })
                    proposal_days = args.draft.days[:day_index] + (tail,)
                    prefix = self._materialize_draft(run, args.draft.model_copy(update={"days": proposal_days}), policy)
                    self._validate_draft(run, prefix, allow_prefix=True, allow_missing_backups=True)
                except (TypeError, ValueError, KeyError, StopIteration, _BusinessRejected):
                    continue
                return self._retain_prefix(run, prefix)

        def add_composition_gap(day: Any, actual: int, minimum: int, maximum: int, reason: str) -> None:
            from .models import DayCompositionGap
            gap_id = f"composition-{day.date.isoformat()}"
            if not any(item.gap_id == gap_id for item in run.pending_gaps):
                run.pending_gaps.append(DayCompositionGap(
                    schema_version="1.0", gap_id=gap_id, evidence_refs=(), date=day.date,
                    day_kind=day.day_kind, actual_attraction_count=actual,
                    required_min=minimum, required_max=maximum, reason=reason,
                ))

        for day in args.draft.days:
            actual = sum(
                item.visit_kind in {VisitKind.ATTRACTION, VisitKind.ATTRACTION_LUNCH, VisitKind.ATTRACTION_DINNER}
                for item in day.visits
            )
            if day.day_kind is PlanningDayKind.TRANSIT_ONLY and (day.visits or day.route_legs or day.backups or day.open_intervals):
                add_composition_gap(day, actual, 0, 0, "transit_has_visit")
                continue
            minimum, maximum = attraction_bounds(
                run.policy.effective_pace if run.policy is not None else TravelPace.NORMAL,
                day.day_kind,
            )
            if not minimum <= actual <= maximum:
                add_composition_gap(day, actual, minimum, maximum, "attraction_count")
        return False

    def _retain_prefix(self, run: _Run, prefix: ItineraryDraft) -> bool:
        def strength(draft):
            if draft is None:
                return (0, 0, 0)
            visits = tuple(visit for day in draft.days for visit in day.visits)
            return (len(visits), len({g for visit in visits for g in visit.must_group_ids}), sum(len(day.backups) for day in draft.days))
        if run.draft is not None:
            old_strength, new_strength = strength(run.draft), strength(prefix)
            if any(new < old for old, new in zip(old_strength, new_strength, strict=True)) or old_strength == new_strength:
                return False
        run.draft = prefix
        run.pending_gaps = [gap for gap in run.pending_gaps if not isinstance(gap, BackupUnavailableGap)]
        return True

    def _revalidate_saved_prefix(self, run: _Run) -> None:
        original = run.draft
        if original is None:
            return
        try:
            self._validate_draft(run, original, allow_prefix=True, allow_missing_backups=True)
            return
        except (ValueError, _BusinessRejected):
            pass
        run.draft = None
        for index in range(len(original.days) - 1, -1, -1):
            day = original.days[index]
            for count in range(len(day.visits), 0, -1):
                visits = day.visits[:count]
                # Only reuse a return leg already present in the saved route;
                # never manufacture a connection to close a truncated day.
                legs = day.route_legs[:count + 1]
                try:
                    tail = day.model_copy(update={"visits": visits, "route_legs": legs, "backups": (), "day_kind": PlanningDayKind.PARTIAL})
                    prefix = original.model_copy(update={"days": original.days[:index] + (tail,)})
                    prefix = cast(ItineraryDraft, _fresh(prefix, ItineraryDraft))
                    self._validate_draft(run, prefix, allow_prefix=True, allow_missing_backups=True)
                except (TypeError, ValueError, _BusinessRejected):
                    continue
                self._retain_prefix(run, prefix)
                return

    def _coordinate(self, run: _Run, ref: Any) -> BaiduCoordinate:
        for handle, kind, _ in run.directory.snapshot():
            entry = run.directory.require(handle)
            if entry.value == ref and entry.coordinate is not None:
                return entry.coordinate
        raise _BusinessRejected()

    async def _supplement(self, run: _Run, args: RequestCandidateSupplementArgs, action_ref: ArtifactRef, call_id: str) -> ItineraryPlanningOutcome | None:
        run.supplement_rejection_reason = None
        if any(gap.source_gap_type == "backup_unavailable" for gap in args.proposed_gaps):
            # Optional recommendations cannot create a mandatory research cycle.
            raise _BusinessRejected("optional_backups_do_not_require_supplement")
        if run.research_remaining <= 0 or run.place_remaining <= 0:
            raise _BusinessRejected("candidate_research_budget_exhausted")
        if len(run.supplement_fingerprints) >= 5 or run.invocation_count >= 17:
            # A supplement limit alone does not prove that map work or a
            # submission is impossible.  Only the exhausted, route-free state
            # below has no remaining way to establish the mandatory routes.
            if (
                run.draft is None
                and not run.directions_refs
                and not run.plan.direction_operation_ids[run.direction_cursor:]
                and not run.plan.hub_geocode_operation_ids[run.hub_cursor:]
                and not run.plan.matrix_operation_ids[run.matrix_cursor:]
            ):
                run.supplement_rejection_reason = "no_legal_action"
            return None
        try:
            policy = effective_policy_from_proposal(args.effective_policy, confirmed=run.confirmed, locked_policy=run.policy)
        except _PolicyLockedMismatch:
            raise _BusinessRejected("locked_policy_rejected") from None
        except _PolicyBusinessRejected as error:
            self._lock_pace_after_policy_rejection(run, args.effective_policy)
            raise _BusinessRejected({"policy source field is not confirmed": "policy_source_unconfirmed", "model pace conflicts with confirmed pace": "pace_conflicts_with_requirement", "structured_demo_deferred_policy_rule_rejected": "structured_demo_deferred_policy_rule_rejected"}.get(str(error), "policy_invalid"), "effective_policy") from None
        if run.policy is None:
            # The first structurally valid supplement fixes policy before any
            # draft/business validation, exactly like a first submit.
            run.policy = policy
        proposed_draft = None
        if args.draft is not None:
            try:
                proposed_draft = self._materialize_draft(run, args.draft, policy)
                self._validate_draft(run, proposed_draft, allow_prefix=True, allow_missing_backups=True)
            except (TypeError, ValueError, KeyError, StopIteration):
                return None
            if run.draft is not None and proposed_draft != run.draft:
                return None
        elif run.draft is not None:
            # A saved longest-consistent prefix must be carried explicitly
            # across the Module 13 supplement boundary; draft=None is only
            # legal when no prefix exists to preserve.
            return None
        public_gaps: list[PlanningGap] = []
        gap_fingerprints: list[str] = []
        for proposed in args.proposed_gaps:
            evidence_refs = tuple(run.directory.require(handle, kind="candidate_evidence").value for handle in proposed.evidence_handles)
            if not evidence_refs or any(not isinstance(ref, ArtifactRef) or ref.artifact_type != "candidate_research.place_observation" for ref in evidence_refs):
                return None
            target = self._target(run, proposed.target)
            gap_digest = fingerprint_gap(run.context_digest, proposed.source_gap_type, target, evidence_refs)
            gap_fingerprints.append(gap_digest)
            gap_id = f"supplement-{gap_digest[7:23]}"
            if proposed.source_gap_type == "candidate_supply":
                gap = CandidateSupplyGap(schema_version="1.0", gap_id=gap_id, date=proposed.date, target=target, evidence_refs=evidence_refs)
            else:
                if proposed.date is None or proposed_draft is None:
                    return None
                anchor = getattr(target, "anchor", None)
                if not isinstance(anchor, CandidateLocationRef):
                    return None
                matching = tuple(
                    visit for day in proposed_draft.days if day.date == proposed.date for visit in day.visits
                    if visit.location == anchor
                )
                if len(matching) != 1:
                    return None
                if isinstance(target, AreaCoverageTarget):
                    backup_kind = "attraction"
                elif isinstance(target, MealCoverageTarget):
                    backup_kind = target.meal_intent.value
                else:
                    return None
                gap = BackupUnavailableGap(
                    schema_version="1.0", gap_id=gap_id, date=proposed.date,
                    backup_kind=backup_kind, core_visit_id=matching[0].visit_id,
                    reason="no_nearby", evidence_refs=evidence_refs,
                )
            public_gaps.append(gap)
        if not public_gaps:
            return None
        fp = _canonical_hash(tuple(gap_fingerprints))
        if fp in run.supplement_fingerprints:
            return None
        run.supplement_fingerprints.append(fp)
        run.pending_supplement_gaps = tuple(public_gaps)
        run.pending_call_id = call_id
        if proposed_draft is not None:
            run.draft = proposed_draft
        state = await self._state_snapshot(run, pending=run.pending_call_id)
        summaries = tuple(_to_gap_summary_snapshot(self._gap_summary_from_gap(run, gap)) for gap in public_gaps)
        record = AwaitingCandidateSupplementRecord(schema_version="1.0", record_type="awaiting_candidate_supplement", state=state, gaps=summaries, gap_fingerprint=fp, pending_call_id=run.pending_call_id)
        return await self._persist_terminal(run, record)

    def _target(self, run: _Run, target: Any) -> Any:
        data = target.model_dump(mode="python")
        if isinstance(target, ProposedCandidateCountTarget):
            return CandidateCountTarget(**data)
        if isinstance(target, ProposedAreaCoverageTarget):
            data["anchor"] = run.directory.require_location(target.anchor_handle).value
            data.pop("anchor_handle", None)
            return AreaCoverageTarget(**data)
        if isinstance(target, ProposedMealCoverageTarget):
            data["anchor"] = run.directory.require_location(target.anchor_handle).value
            data.pop("anchor_handle", None)
            return MealCoverageTarget(**data)
        if isinstance(target, ProposedMustResolutionTarget):
            from .models import MustResolutionTarget
            return MustResolutionTarget(**data)
        if isinstance(target, ProposedAvoidResolutionTarget):
            from .models import AvoidResolutionTarget
            data["candidate_uids"] = tuple(run.directory.require_location(handle).value.uid for handle in target.candidate_handles)
            data.pop("candidate_handles", None)
            return AvoidResolutionTarget(**data)
        if isinstance(target, ProposedCandidateDetailsTarget):
            return CandidateDetailsTarget(candidate_uids=tuple(run.directory.require_location(handle).value.uid for handle in target.candidate_handles))
        if isinstance(target, ProposedReplacementTarget):
            from .models import ReplacementTarget
            data["rejected_uid"] = run.directory.require_location(target.rejected_candidate_handle).value.uid
            data["anchor"] = None if target.anchor_handle is None else run.directory.require_location(target.anchor_handle).value
            data.pop("rejected_candidate_handle", None); data.pop("anchor_handle", None)
            return ReplacementTarget(**data)
        raise ValueError("unsupported supplement target")

    def _gap_summary_from_gap(self, run: _Run, gap: PlanningGap) -> Any:
        directories = {"candidate_evidence": run.candidate_evidence_refs, "hub_geocode": run.hub_refs,
            "directions": run.directions_refs, "matrix": run.matrix_refs, "model_action": run.action_refs,
            "candidate_set": run.candidate_set_refs, "hotel_selection": [run.hotel_ref]}
        evidence_indexes = tuple(PublicArtifactEvidenceIndex(schema_version="1.0", directory=name, index=refs.index(ref))
            for ref in gap.evidence_refs for name, refs in directories.items() if ref in refs)
        if len(evidence_indexes) != len(gap.evidence_refs):
            raise ValueError("gap evidence is outside the run directory")
        data = gap.model_dump(mode="python", exclude_none=False)
        data["summary_type"] = data.pop("gap_type")
        data.pop("evidence_refs", None)
        data["evidence_indexes"] = evidence_indexes
        return TypeAdapter(PlanningGapSummary).validate_python(data, strict=True)

    async def _continue_after_supplement(self, run: _Run, state: PlanningStateSnapshot) -> ItineraryPlanningOutcome:
        if (state.model_invocations_consumed != run.invocation_count or state.pending_call_id is None or
            state.current_stage != run.current_stage or state.preparation_used != run.preparation_used or
            state.transport_used != run.transport_used or state.submit_repair_used != run.submit_repair_used or
            state.supplement_request_used != run.supplement_request_used or state.last_rejection != run.last_rejection):
            return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.REQUEST_MISMATCH)
        pending = (ToolResult(call_id=state.pending_call_id, content=json.dumps({"status": "candidate_supplemented", "candidate_count": len(run.candidates)}, separators=(",", ":"))),)
        return await self._drive(run, first=False, pending_results=pending)

    async def _finish_completed(self, run: _Run) -> ItineraryPlanningOutcome:
        summary = self._summary(run, run.draft)
        budgets = await self._budget_summary(run)
        record = CompletedItineraryRecord(schema_version="1.0", record_type="completed", draft=_to_draft_snapshot(cast(ItineraryDraft, run.draft)), summary=_to_summary_snapshot(summary), budgets=_to_budget_snapshot(budgets))
        return await self._persist_terminal(run, record)

    async def _finish_incomplete(self, run: _Run, reason: PlanningStopReason) -> ItineraryPlanningOutcome:
        record_error("planning_incomplete", reason=reason.value, invocation=run.invocation_count)
        summary = self._summary(run, run.draft)
        budgets = await self._budget_summary(run)
        process_gap = self._gap_summary_from_process(reason)
        gap_values: list[Any] = [*(self._gap_summary_from_gap(run, gap) for gap in run.pending_gaps), process_gap]
        summaries = tuple(_to_gap_summary_snapshot(item) for item in gap_values)
        reasons = [reason]
        if any(isinstance(item, (CandidateSupplyGap, BackupUnavailableGap)) for item in run.pending_gaps):
            reasons.append(PlanningStopReason.CANDIDATE_SUPPLY_INSUFFICIENT)
        if any(item.gap_type in {"schedule_conflict", "route_unavailable", "pace_capacity", "opening_hours_conflict", "transport_constraint"} for item in run.pending_gaps):
            reasons.append(PlanningStopReason.SCHEDULE_INFEASIBLE)
        record = IncompleteItineraryRecord(schema_version="1.0", record_type="incomplete", draft=None if run.draft is None else _to_draft_snapshot(run.draft), summary=_to_summary_snapshot(summary), budgets=_to_budget_snapshot(budgets), gaps=summaries, stop_reasons=order_stop_reasons(reasons))
        return await self._persist_terminal(run, record)

    async def _finish_failed(self, run: _Run, failure: PlanningFailure) -> ItineraryPlanningOutcome:
        budgets = await self._budget_summary(run)
        record = FailedItineraryRecord(schema_version="1.0", record_type="failed", last_consistent_itinerary_ref=run.last_planning_ref, failure=_to_failure_snapshot(failure), budgets=_to_budget_snapshot(budgets), retryable=failure.retryable, possible_duplicate=failure.possible_duplicate)
        return await self._persist_terminal(run, record)

    async def _finish_control_failed(self, run: _Run, code: PlanningFailureCode) -> ItineraryPlanningOutcome:
        expected = {
            PlanningFailureCode.ARTIFACT_ERROR: (True, False),
            PlanningFailureCode.BUDGET_ERROR: (True, False),
            PlanningFailureCode.OPERATION_RESULT_INDETERMINATE: (False, True),
        }.get(code, (False, False))
        failure = ControlPlanningFailure(
            schema_version="1.0", failure_type="control", code=code,
            retryable=expected[0], possible_duplicate=expected[1],
        )
        return await self._finish_failed(run, failure)

    async def _persist_terminal(self, run: _Run, record: Any) -> ItineraryPlanningOutcome:
        artifact = ItineraryPlanningArtifact(schema_version="2.0", request_digest=run.request_digest, parent_planning_ref=run.last_planning_ref, directories=self._directory_model(run), record=record)
        try:
            ref, fresh = await self._persist(operation_id=run.plan.terminal_successor_operation_id, artifact_type="itinerary_planning.itinerary", content=artifact, expected=ItineraryPlanningArtifact)
        except ArtifactCapacityExceededError:
            return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.ARTIFACT_CAPACITY_EXCEEDED)
        except _ArtifactWriteIndeterminate:
            return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.ARTIFACT_ERROR, possible_duplicate=True)
        except _Indeterminate:
            return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.OPERATION_RESULT_INDETERMINATE)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._failed(run.last_planning_ref, await self._budget_summary(run), PlanningFailureCode.ARTIFACT_ERROR, possible_duplicate=True)
        run.last_planning_ref = ref
        return self._outcome_from_terminal(ref, cast(ItineraryPlanningArtifact, fresh))

    def _gap_summary_from_process(self, reason: PlanningStopReason) -> Any:
        return PlanningProcessGapSummary(schema_version="1.0", summary_type="planning_process", gap_id=f"process-{reason.value}", evidence_indexes=(), process_reason={PlanningStopReason.DIRECTION_BUDGET_EXHAUSTED: "direction_budget_exhausted", PlanningStopReason.ITERATION_LIMIT_REACHED: "iteration_limit_reached", PlanningStopReason.NO_PROGRESS: "no_progress", PlanningStopReason.NO_LEGAL_ACTION: "no_legal_action", PlanningStopReason.NO_TERMINAL_SUBMISSION: "no_terminal_submission"}.get(reason, "no_terminal_submission"))

    def _summary(self, run: _Run, draft: ItineraryDraft | None) -> PlanningSummary:
        days = draft.days if draft is not None else ()
        attractions = lunches = dinners = routes = backups = 0
        unique: set[str] = set()
        full = partial = transit = 0
        unknown_hours = 0
        for day in days:
            a = sum(item.visit_kind in {VisitKind.ATTRACTION, VisitKind.ATTRACTION_LUNCH, VisitKind.ATTRACTION_DINNER} for item in day.visits)
            l = sum(item.visit_kind in {VisitKind.LUNCH, VisitKind.ATTRACTION_LUNCH} for item in day.visits)
            d = sum(item.visit_kind in {VisitKind.DINNER, VisitKind.ATTRACTION_DINNER} for item in day.visits)
            attractions += a; lunches += l; dinners += d; routes += len(day.route_legs); backups += len(day.backups); unique.update(item.location.uid for item in day.visits)
            unknown_hours += sum(item.business_hours_state is BusinessHoursState.UNKNOWN for item in day.visits)
            unknown_hours += sum(item.business_hours_state is BusinessHoursState.UNKNOWN for item in day.backups)
            if day.day_kind.value == "full": full += 1
            elif day.day_kind.value == "partial": partial += 1
            else: transit += 1
        return PlanningSummary(schema_version="1.0", effective_pace=None if run.policy is None else run.policy.effective_pace, sightseeing_days=self._days(run), full_days=full, partial_days=partial, transit_only_days=transit, attraction_count=attractions, lunch_count=lunches, dinner_count=dinners, unique_main_candidates=len(unique), boundary_transfers=2 if draft is not None else 0, route_legs=routes + (2 if draft is not None else 0), backups=backups, unknown_hours=unknown_hours)

    async def _budget_summary(self, run: _Run) -> Any:
        from .models import PlanningBudgetSummary
        direction_limit = 8 * self._days(run) + 2
        direction_consumed = 0
        complete = run.budget_complete
        try:
            snapshot = await self._budget.get_snapshot(key=self._direction_key(run))
            direction_limit, direction_consumed = snapshot.limit, snapshot.consumed
            total = await self._budget.get_snapshot(key=ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY)
            complete = complete and total.limit == 17 and total.consumed == run.invocation_count
        except Exception:
            complete = False
        return PlanningBudgetSummary(schema_version="2.0", model_invocation_limit=17, model_invocations_consumed=run.invocation_count,
            preparation_limit=2, preparation_used=run.preparation_used, transport_limit=5, transport_used=run.transport_used,
            submit_repair_limit=5, submit_repair_used=run.submit_repair_used, supplement_request_limit=5, supplement_request_used=run.supplement_request_used,
            direction_call_limit=direction_limit, direction_calls_consumed=direction_consumed, budget_snapshot_complete=complete,
            semantic_invocation_limit=17, semantic_invocations_consumed=run.invocation_count, known_provider_attempts=run.provider_attempts,
            provider_attempts_complete=run.provider_attempts_complete, known_usage=run.usage, usage_complete=run.usage_complete, cache_hits=run.cache_hits)

    def _failed(self, last_ref: ArtifactRef | None, budgets: Any, code: PlanningFailureCode, *, possible_duplicate: bool = False) -> ItineraryPlanningFailed:
        retryable = code in {PlanningFailureCode.ARTIFACT_ERROR, PlanningFailureCode.BUDGET_ERROR} and not possible_duplicate
        if code is PlanningFailureCode.OPERATION_RESULT_INDETERMINATE:
            possible_duplicate = True; retryable = False
        if code is PlanningFailureCode.ARTIFACT_ERROR and possible_duplicate:
            retryable = False
        failure = ControlPlanningFailure(schema_version="1.0", failure_type="control", code=code, retryable=retryable, possible_duplicate=possible_duplicate)
        return ItineraryPlanningFailed(schema_version="2.0", outcome_type="failed", failure=failure, last_consistent_itinerary_ref=last_ref, failure_artifact_ref=None, budgets=budgets, retryable=retryable, possible_duplicate=possible_duplicate)

    def _outcome_from_terminal(self, ref: ArtifactRef, artifact: ItineraryPlanningArtifact) -> ItineraryPlanningOutcome:
        record = artifact.record
        if isinstance(record, CompletedItineraryRecord):
            return ItineraryPlanningCompleted(schema_version="2.0", outcome_type="completed", itinerary_ref=ref, summary=_from_summary_snapshot(record.summary), budgets=_from_budget_snapshot(record.budgets))
        if isinstance(record, IncompleteItineraryRecord):
            return ItineraryPlanningIncomplete(schema_version="2.0", outcome_type="incomplete", itinerary_ref=ref if record.draft is not None else None, summary=_from_summary_snapshot(record.summary), budgets=_from_budget_snapshot(record.budgets), gaps=tuple(_from_gap_summary_snapshot(item, artifact.directories) for item in record.gaps), stop_reasons=record.stop_reasons)
        if isinstance(record, AwaitingCandidateSupplementRecord):
            return ItineraryPlanningCandidateSupplementRequested(schema_version="2.0", outcome_type="candidate_supplement_requested", planning_ref=ref, candidate_set_ref=artifact.directories.candidate_set_refs[-1], gaps=tuple(_from_gap_summary_snapshot(item, artifact.directories) for item in record.gaps), gap_fingerprint=record.gap_fingerprint, budgets=_from_budget_snapshot(record.state.budgets))
        failure = _from_failure_snapshot(record.failure)
        return ItineraryPlanningFailed(schema_version="2.0", outcome_type="failed", failure=failure, last_consistent_itinerary_ref=record.last_consistent_itinerary_ref, failure_artifact_ref=ref, budgets=None if record.budgets is None else _from_budget_snapshot(record.budgets), retryable=record.retryable, possible_duplicate=record.possible_duplicate)


__all__ = ("ItineraryPlanningService",)
