"""Module 08 受控候选研究循环。

The service deliberately keeps all provider objects at the boundary.  Persisted
artifacts are built from the local snapshot DTOs and the public outcome is rebuilt
from the terminal artifact, which makes replay independent from the private run
context.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from app.requirements import interest_selection_error
from .context import RequirementContext, RequirementContextError, project_requirements

from app.baidu_map import (
    BaiduMapAdapter,
    BaiduMapError,
    BaiduMapErrorCode,
    BaiduCoordinate,
    CitySearchArea,
    MapCapability,
    MapProvenance,
    NearbySearchArea,
    PlaceDetailsNoResult,
    PlaceDetails,
    PlaceDetailsRequest,
    PlaceDetailsSuccess,
    PlaceSearchNoResult,
    PlaceSummary,
    PlaceSearchRequest,
    PlaceSearchSuccess,
)
from app.hotel_selection import HotelSelectedDecision, HotelSelectionArtifact
from app.llm_runtime import (
    InteractionCallIdentity, interaction_call,
    DeepSeekRuntime,
    InvocationMetadata,
    LLMErrorCode,
    LLMInvocationFailure,
    LLMRuntimeError,
    ModelRole,
    PromptEnvelope,
    StructuredInvocationSuccess,
)
from app.requirements import InterestTag
from app.workflow_contracts import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactRef,
    ArtifactRepository,
    ArtifactWriteRequest,
    BudgetConsumed,
    BudgetConsumeRequest,
    BudgetExceeded,
    ExecutionBudgetSnapshot,
    ExecutionBudgetManager,
    OperationId,
    WorkflowPhase,
)
from app.workflow_contracts.errors import (
    ArtifactRepositoryError,
    ArtifactRepositoryUsageError,
    ExecutionBudgetError,
)

from .artifacts import (
    ActionDispositionSnapshot,
    ArtifactAreaCoverageMissingSummary,
    ArtifactCandidateCoreSnapshot,
    ArtifactCandidateDetailsStateSnapshot,
    ArtifactCandidateRecordSnapshot,
    ArtifactCandidateRecordV2Snapshot,
    ArtifactMealAssessmentSnapshot,
    ArtifactCandidateCountShortfallSummary,
    ArtifactCandidateDetailsMissingSummary,
    ArtifactControlResearchFailureSnapshot,
    ArtifactCoordinateSnapshot,
    ArtifactEvidenceHandleSnapshot,
    ArtifactInterestCoverageMissingSummary,
    ArtifactInterestJudgmentUpdateResolved,
    ArtifactInvocationMetadataSnapshot,
    ArtifactKnownLLMUsageSnapshot,
    ArtifactLLMErrorSnapshot,
    ArtifactLLMResearchFailureSnapshot,
    ArtifactMapResearchFailureSnapshot,
    ArtifactMealJudgmentUpdateResolved,
    ArtifactMealCoverageMissingSummary,
    CorrectableMealCountShortfallSummary as ArtifactMealCountShortfallSummary,
    ArtifactPlaceDetailsSnapshot,
    ArtifactPlaceSummarySnapshot,
    ArtifactProvenanceSnapshot,
    ArtifactMustVisitResolutionMissingSummary,
    ArtifactAvoidResolutionMissingSummary,
    ArtifactReplacementCandidateNeededSummary,
    ArtifactResearchAnchorSnapshot,
    CorrectableResearchBudgetSnapshot as ArtifactResearchBudgetSummarySnapshot,
    ArtifactResearchCountSummarySnapshot,
    ArtifactResearchCoverageSummarySnapshot,
    ArtifactResearchGapSummarySnapshot,
    ArtifactRequirementGroupState,
    ArtifactRequirementVerdictOverrideState,
    ArtifactRequirementMatchSnapshot,
    ArtifactRoleAssessmentSnapshot,
    ArtifactRoleJudgmentUpdateResolved,
    CandidateResearchArtifact,
    CandidateResearchModelActionArtifact,
    CandidateResearchModelJudgmentArtifact,
    CandidateResearchPlaceObservationArtifact,
    CandidateResearchInvocationAttemptArtifact, AttemptActionResult, AttemptJudgmentResult,
    AttemptControlFailure, CorrectableActionCorrectionState,
    candidate_artifact_model, action_artifact_model, judgment_artifact_model,
    CandidateSetStateSnapshot,
    CorrectableCandidateSetStateSnapshot as QualifiedCandidateSetStateSnapshot,
    _candidate_set_state_complete,
    CorrectableCompletedCandidateSetRecord as CompletedCandidateSetRecord,
    CorrectableFailedCandidateSetRecord as FailedCandidateSetRecord,
    CorrectableIncompleteCandidateSetRecord as IncompleteCandidateSetRecord,
    CorrectableInProgressCandidateSetRecord as InProgressCandidateSetRecord,
    JudgmentPatchSnapshot,
    JudgmentPatchModelSnapshot,
    ModelActionAcceptedRecord,
    ModelActionControllerRejectedRecord,
    ModelActionLLMFailureRecord,
    ModelActionPolicyRejectedRecord,
    ModelJudgmentAcceptedRecord,
    ModelJudgmentControllerRejectedRecord,
    ModelJudgmentLLMFailureRecord,
    OutputRecoveryAttempt,
    unwrap_model_record,
    NoUsefulActionProposedSnapshot,
    PlaceDetailsNoResultObservation,
    PlaceDetailsSuccessObservation,
    PlaceMapFailureObservation,
    PlaceNotIssuedObservation,
    PlaceObservationRecord,
    PlaceSearchNoResultObservation,
    PlaceSearchSuccessObservation,
    PlaceUnverifiedResultObservation,
    ProposedActionBatchSnapshot,
    ProposedDetailsActionSnapshot,
    ProposedSearchActionSnapshot,
    SearchCoverageRecord,
    SearchCoverageState,
    _gap_related_observation_indexes,
    _artifact_write_request,
)
from .errors import CandidateResearchConfigurationError
from .purpose import candidate_admitted_purposes
from .recovery import CorrectionFeedback, POLICY_CODES, OUTPUT_CODES, correction_stop
from .fingerprints import (
    action_fingerprint,
    candidate_handle,
    candidate_core_equal,
    canonical_json_bytes,
    content_digest,
    details_action_fingerprint,
    invocation_request_digest,
    objective_fingerprint,
    requirement_group_id,
    requirement_item_id,
    supplemental_gap_fingerprint,
    search_action_fingerprint,
)
from .judgments import JudgmentEvidenceMismatch, evidence_handle_indexes, validate_judgment_patch
from .models import (
    ActionModelOutcome,
    CandidateResearchCompleted,
    CandidateResearchControlFailureCode,
    CandidateResearchFailed,
    CandidateResearchInitialRequest,
    CandidateResearchIncomplete,
    CandidateResearchOutcome,
    CandidateResearchStopReason,
    CandidateResearchFailure,
    CandidateResearchSupplementalRequest,
    CandidateResearchOperationPlan,
    CandidateState,
    ControlResearchFailure,
    EvidenceHandle,
    InterestJudgmentUpdate,
    JudgmentPatch,
    KnownLLMUsage,
    LLMResearchFailure,
    MapResearchFailure,
    MatchVerdict,
    MealIntent,
    MealJudgmentUpdate,
    NoUsefulActionProposed,
    ProposedAction,
    ProposedActionBatch,
    ProposedDetailsAction,
    ProposedSearchAction,
    ResearchBudgetSummary,
    ResearchCoverageSummary,
    ResearchIterationOperationIds,
    ResearchMode,
    ResearchInvocationKind,
    ResearchObjectiveKind,
    ResearchRole,
    ResearchTool,
    ResearchCountSummary,
    RequirementReasonCode,
    RoleJudgmentUpdate,
    RoleReasonCode,
    RoleVerdict,
    SearchAreaMode,
    SupplementalGap,
    AvoidResolutionMissingGap,
)
from .policy import ResearchObjective, compute_stop_reasons, derive_candidate_state, deterministic_objectives, objective_priority, role_target
from .prompt_loader import (
    ACTION_PROMPT_CONTENT_DIGEST,
    ACTION_PROMPT_VERSION,
    OBSERVATION_PROMPT_CONTENT_DIGEST,
    OBSERVATION_PROMPT_VERSION,
    load_action_prompt,
    load_observation_prompt,
)
from .prompting import build_action_prompt, build_observation_prompt
from .qualification import (
    AssessmentReceipt, JudgmentJob, CandidateEvidenceWindow, QualificationState, ResearchStep,
    JudgmentRequiredMissing,
    evidence_digest, validate_required_judgments, record_assessments,
    candidate_observation, observation_context,
    validate_job_coverage, supplied_judgments,
    build_judgment_context, job_has_pending_judgments,
    judgment_key, qualification_resolution_keys,
)


class _ModelOperationPort(Protocol):
    @property
    def semantic_invocations_consumed(self) -> int: ...

    async def ainvoke_structured(self, prompt: PromptEnvelope, schema: type[BaseModel]): ...

    def close(self) -> None: ...


class _RuntimePort(Protocol):
    def start_operation(self, role: ModelRole) -> _ModelOperationPort: ...


class _ActionModelEnvelope(BaseModel):
    """Object-root schema required by the shared structured-output runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    output: ActionModelOutcome


def _filter_search_coverage(
    search: SearchCoverageState,
    eligible_ordinals: Sequence[int],
) -> SearchCoverageState:
    """Remove candidates that later ceased to be eligible from saved coverage."""
    eligible = set(eligible_ordinals)
    records = tuple(
        SearchCoverageRecord(
            **{
                **_python_model(record),
                "eligible_candidate_ordinals": tuple(
                    ordinal
                    for ordinal in record.eligible_candidate_ordinals
                    if ordinal in eligible
                ),
            }
        )
        for record in search.records
    )
    return SearchCoverageState(
        records=records,
        initial_city_attraction_search_done=search.initial_city_attraction_search_done,
        initial_hotel_restaurant_nearby_done=search.initial_hotel_restaurant_nearby_done,
    )


@dataclass(slots=True)
class _Run:
    run_id: UUID
    request_digest: str
    mode: ResearchMode
    hotel_ref: ArtifactRef
    confirmed_digest: str
    destination: str
    effective_interests: tuple[InterestTag, ...]
    interests_frozen: bool
    agent_decides_interests: bool
    operation: _ModelOperationPort
    state: CandidateSetStateSnapshot
    action_refs: list[ArtifactRef] = field(default_factory=list)
    judgment_refs: list[ArtifactRef] = field(default_factory=list)
    observation_refs: list[ArtifactRef] = field(default_factory=list)
    fingerprints: dict[str, ArtifactRef] = field(default_factory=dict)
    fingerprint_trees: dict[str, object] = field(default_factory=dict)
    semantic_invocations: int = 0
    provider_attempts: int = 0
    provider_attempts_complete: bool = True
    usage_complete: bool = True
    resolved_progress: set[str] = field(default_factory=set)
    usage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    closed: bool = False
    predecessor_ref: ArtifactRef | None = None
    last_consistent_ref: ArtifactRef | None = None
    indeterminate: bool = False
    requirement_specs: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    requirement_context: tuple[RequirementContext, ...] = ()
    corrections: tuple = ()
    rejected_operations: set[OperationId] = field(default_factory=set)
    named_searches: set[str] = field(default_factory=set)
    objective_history: dict = field(default_factory=dict)
    objective_directory: dict[str, ResearchObjective] = field(default_factory=dict)
    gap_fingerprints: set[str] = field(default_factory=set)
    supply_search_results: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    last_judgment_patch: JudgmentPatch | None = None
    last_judgment_metadata: ArtifactInvocationMetadataSnapshot | None = None
    supplemental_baseline_uids: frozenset[str] = frozenset()
    replayed_place_refs: dict[OperationId, ArtifactRef] = field(default_factory=dict)
    action_interaction: InteractionCallIdentity | None = None
    output_recoveries_used: int = 0
    action_validation_feedback: dict | None = None
    action_recoveries: tuple = ()
    invocation_attempt_refs: list[ArtifactRef] = field(default_factory=list)
    action_attempt_refs: list[ArtifactRef] = field(default_factory=list)
    judgment_attempt_refs: list[ArtifactRef] = field(default_factory=list)
    call_info: dict = field(default_factory=dict)
    active_slot: ResearchIterationOperationIds | None = None
    correction_stop_reason: CandidateResearchStopReason | None = None
    judgment_recoveries: tuple[OutputRecoveryAttempt, ...] = ()
    judgment_recoverable: bool = False
    judgment_recovery_code: str = "judgment_evidence_mismatch"
    assessments: tuple[AssessmentReceipt, ...] = ()
    steps: tuple[ResearchStep, ...] = ()
    judgment_job: JudgmentJob | None = None
    judgment_payload: dict | None = None
    active_iteration: int | None = None
    active_action_ref: ArtifactRef | None = None
    active_judgment_ref: ArtifactRef | None = None
    active_observation_start: int = 0
    active_step_kind: str = "search_and_judge"
    observation_cache: dict[ArtifactRef, CandidateResearchPlaceObservationArtifact] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _MapResult:
    action: ProposedAction
    outcome: object | None
    error: BaiduMapError | None
    contract_failure: bool = False


@dataclass(frozen=True, slots=True)
class _PolicyReplay:
    """Persisted rejection re-enters the same bounded correction reducer."""
    output: object
    rule: str


@dataclass(frozen=True, slots=True)
class _JudgmentReplayIndeterminate:
    """Marker for a found judgment artifact that cannot be proven replayable."""


class _OperationReplayIndeterminate(RuntimeError):
    """Internal signal for same-operation artifacts whose content conflicts."""


class _ResearchReplayMismatch(ValueError):
    """A supplied predecessor or replay identity disagrees with recorded facts."""


def _json_model(model: BaseModel) -> dict[str, object]:
    tree = model.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)
    if type(tree) is not dict:
        raise ValueError("model must encode as object")
    return tree


def _python_model(model: BaseModel) -> dict[str, object]:
    tree = model.model_dump(mode="python", exclude_none=False, by_alias=False, exclude_computed_fields=True)
    if type(tree) is not dict:
        raise ValueError("model must encode as object")
    return tree


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CandidateResearchService:
    """Initial and supplemental candidate research implementation."""

    def __init__(
        self,
        *,
        runtime: DeepSeekRuntime,
        map_adapter: BaiduMapAdapter,
        artifact_repository: ArtifactRepository,
        budget_manager: ExecutionBudgetManager,
    ) -> None:
        if not callable(getattr(runtime, "start_operation", None)):
            raise CandidateResearchConfigurationError()
        if not callable(getattr(map_adapter, "search_places", None)) or not callable(getattr(map_adapter, "get_place_details", None)):
            raise CandidateResearchConfigurationError()
        if not callable(getattr(artifact_repository, "put", None)) or not callable(getattr(artifact_repository, "lookup", None)):
            raise CandidateResearchConfigurationError()
        if not callable(getattr(budget_manager, "consume", None)):
            raise CandidateResearchConfigurationError()
        self._runtime = runtime
        self._map_adapter = map_adapter
        self._repository = artifact_repository
        self._budget = budget_manager
        self._runs: dict[UUID, _Run] = {}
        self._closed_run_ids: set[UUID] = set()
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._flights: dict[tuple[UUID, OperationId], asyncio.Future[CandidateResearchOutcome]] = {}
        self._flight_digests: dict[tuple[UUID, OperationId], str] = {}

    async def research_initial(self, request: CandidateResearchInitialRequest) -> CandidateResearchOutcome:
        return await self._research(request, ResearchMode.INITIAL)

    async def research_supplemental(self, request: CandidateResearchSupplementalRequest) -> CandidateResearchOutcome:
        return await self._research(request, ResearchMode.SUPPLEMENTAL)

    async def close_run(self, run_id: UUID) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            if not run.closed:
                run.closed = True
                run.operation.close()
                self._runs.pop(run_id, None)
                self._closed_run_ids.add(run_id)

    async def aclose(self) -> None:
        for run_id in tuple(self._runs):
            await self.close_run(run_id)

    async def _research(
        self,
        request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest,
        mode: ResearchMode,
    ) -> CandidateResearchOutcome:
        if not isinstance(request, (CandidateResearchInitialRequest, CandidateResearchSupplementalRequest)):
            return self._zero_failed(CandidateResearchControlFailureCode.INVALID_INPUT)
        try:
            request = TypeAdapter(type(request)).validate_json(json.dumps(_json_model(request), ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
            digest = invocation_request_digest(request)
        except (AttributeError, ValidationError, ValueError, TypeError):
            return self._zero_failed(CandidateResearchControlFailureCode.INVALID_INPUT)
        terminal_id = request.operation_plan.terminal_successor_operation_id
        key = (request.operation_plan.run_id, terminal_id)
        try:
            terminal = await self._terminal_replay(request, digest)
        except ArtifactRepositoryError:
            return await self._read_failure(request, CandidateResearchControlFailureCode.ARTIFACT_ERROR)
        except (RuntimeError, TypeError, ValueError):
            return await self._read_failure(request, CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
        if terminal is not None:
            return terminal
        existing_run = self._runs.get(request.operation_plan.run_id)
        if existing_run is not None:
            if existing_run.indeterminate:
                return await self._indeterminate(request.operation_plan.run_id)
            if existing_run.closed:
                return self._zero_failed(CandidateResearchControlFailureCode.RUN_CONTEXT_MISSING)
        elif request.operation_plan.run_id in self._closed_run_ids:
            return self._zero_failed(CandidateResearchControlFailureCode.RUN_CONTEXT_MISSING)
        existing = self._flights.get(key)
        if existing is not None:
            if self._flight_digests[key] != digest:
                return await self._indeterminate(request.operation_plan.run_id)
            return await asyncio.shield(existing)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[CandidateResearchOutcome] = loop.create_future()
        self._flights[key] = future
        self._flight_digests[key] = digest
        try:
            lock = self._locks.setdefault(request.operation_plan.run_id, asyncio.Lock())
            async with lock:
                terminal = await self._terminal_replay(request, digest)
                if terminal is not None:
                    result = terminal
                else:
                    result = await self._run_locked(request, mode, digest)
            future.set_result(result)
            return result
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        except CandidateResearchConfigurationError:
            if not future.done():
                future.set_exception(CandidateResearchConfigurationError())
            raise
        except _OperationReplayIndeterminate:
            result = await self._indeterminate(request.operation_plan.run_id)
            if not future.done():
                future.set_result(result)
            return result
        except _ResearchReplayMismatch:
            result = await self._active_failed(request, CandidateResearchControlFailureCode.REPLAY_MISMATCH)
            if not future.done():
                future.set_result(result)
            return result
        except RequirementContextError:
            result = await self._active_failed(request, CandidateResearchControlFailureCode.CONTEXT_CONTRACT_ERROR)
            if not future.done():
                future.set_result(result)
            return result
        except (ValidationError, ValueError):
            existing_state = self._runs.get(request.operation_plan.run_id)
            code = CandidateResearchControlFailureCode.STATE_INVARIANT_FAILED if existing_state is not None else CandidateResearchControlFailureCode.INVALID_INPUT
            result = await self._active_failed(request, code)
            if not future.done():
                future.set_result(result)
            return result
        except ArtifactRepositoryError:
            result = await self._active_failed(request, CandidateResearchControlFailureCode.ARTIFACT_ERROR)
            if not future.done():
                future.set_result(result)
            return result
        except ExecutionBudgetError:
            result = await self._active_failed(request, CandidateResearchControlFailureCode.BUDGET_ERROR)
            if not future.done():
                future.set_result(result)
            return result
        except (RuntimeError, TypeError, AttributeError):
            result = await self._active_failed(request, CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
            if not future.done():
                future.set_result(result)
            return result
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            result = await self._active_failed(request, CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
            if not future.done():
                future.set_result(result)
            return result
        finally:
            self._flights.pop(key, None)
            self._flight_digests.pop(key, None)

    async def _terminal_replay(
        self,
        request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest,
        digest: str,
    ) -> CandidateResearchOutcome | None:
        lookup = await self._repository.lookup(operation_id=request.operation_plan.terminal_successor_operation_id)
        if isinstance(lookup, ArtifactAbsent):
            return None
        if not isinstance(lookup, ArtifactFound):
            raise RuntimeError("terminal artifact lookup contract violation")
        if (
            lookup.ref.artifact_type != "candidate_research.candidate_set"
            or lookup.ref.schema_version != "5.0"
            or lookup.ref.operation_id != request.operation_plan.terminal_successor_operation_id
        ):
            return await self._indeterminate(request.operation_plan.run_id)
        try:
            read = await self._repository.get(lookup.ref, expected_model=CandidateResearchArtifact)
            artifact = read.content
        except ArtifactRepositoryError:
            return await self._indeterminate(request.operation_plan.run_id)
        expected_mode = ResearchMode.SUPPLEMENTAL if isinstance(request, CandidateResearchSupplementalRequest) else ResearchMode.INITIAL
        expected_predecessor = request.predecessor_candidate_set_ref if isinstance(request, CandidateResearchSupplementalRequest) else None
        if (
            read.ref != lookup.ref
            or artifact.run_id != request.operation_plan.run_id
            or artifact.mode is not expected_mode
            or artifact.predecessor_ref != expected_predecessor
            or artifact.invocation_request_digest != digest
            or _digest(_json_model(artifact)) != lookup.ref.content_hash
        ):
            return await self._indeterminate(request.operation_plan.run_id)
        try:
            await self._validate_terminal_refs(artifact)
        except (ArtifactRepositoryError, RuntimeError, TypeError, ValueError, ValidationError):
            return await self._indeterminate(request.operation_plan.run_id)
        return self._outcome_from_record(lookup.ref, artifact.record)

    async def _run_locked(
        self,
        request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest,
        mode: ResearchMode,
        digest: str,
    ) -> CandidateResearchOutcome:
        try:
            load_action_prompt()
            load_observation_prompt()
        except CandidateResearchConfigurationError:
            return await self._active_failed(request, CandidateResearchControlFailureCode.PROMPT_RESOURCE_INVALID)
        if mode is ResearchMode.SUPPLEMENTAL and request.operation_plan.run_id not in self._runs:
            return await self._active_failed(request, CandidateResearchControlFailureCode.RUN_CONTEXT_MISSING)
        if request.operation_plan.run_id in self._closed_run_ids and request.operation_plan.run_id not in self._runs:
            return await self._active_failed(request, CandidateResearchControlFailureCode.RUN_CONTEXT_MISSING)
        run = await self._load_or_create_run(request, mode, digest)
        project_requirements(run.requirement_specs, run.requirement_context)
        slots = request.operation_plan.iteration_slots
        if run.closed:
            return await self._indeterminate(run.run_id)
        if mode is ResearchMode.SUPPLEMENTAL and isinstance(request, CandidateResearchSupplementalRequest):
            # A supplemental request is a no-op when every requested gap is
            # already satisfied. Validate the required slot cardinality while
            # avoiding all model, map and budget side effects.
            try:
                remaining_snapshot = await self._budget.get_snapshot(key=self._iteration_key())
            except ExecutionBudgetError:
                return await self._active_failed(request, CandidateResearchControlFailureCode.BUDGET_ERROR)
            if not self._valid_budget_snapshot(remaining_snapshot, run.run_id, self._iteration_key()):
                return await self._active_failed(request, CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
            if len(slots) != remaining_snapshot.remaining:
                return await self._active_failed(request, CandidateResearchControlFailureCode.OPERATION_PLAN_INVALID)
            if all(self._gap_satisfied(run, gap) for gap in request.gaps):
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="no_op")
        if self._is_complete(run, request):
            transition = "no_op" if mode is ResearchMode.SUPPLEMENTAL else "normal"
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind=transition)
        if mode is ResearchMode.SUPPLEMENTAL:
            if run.predecessor_ref is None:
                return await self._indeterminate(run.run_id)
            try:
                remaining = (await self._budget.get_snapshot(key=self._iteration_key())).remaining
            except ExecutionBudgetError:
                return await self._active_failed(request, CandidateResearchControlFailureCode.BUDGET_ERROR)
            if len(slots) != remaining:
                return await self._active_failed(request, CandidateResearchControlFailureCode.OPERATION_PLAN_INVALID)
        objectives = self._objectives(run, request)
        for local_slot, slot in enumerate(slots):
            if self._is_complete(run, request):
                transition = "no_op" if mode is ResearchMode.SUPPLEMENTAL else "normal"
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind=transition)
            pending = await self._pending_judgment(run, slot)
            if pending is not None and run.semantic_invocations < 12:
                result = await self._judge_existing(run, request, slot, local_slot, pending)
            elif not objectives or run.semantic_invocations >= 11:
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
            else:
                result = await self._iteration(run, request, slot, local_slot, objectives)
            if result is not None:
                return result
            if self._is_complete(run, request):
                transition = "no_op" if mode is ResearchMode.SUPPLEMENTAL else "normal"
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind=transition)
            objectives = self._objectives(run, request)
            executable_judgment = run.semantic_invocations < 12 and await self._pending_judgment(run) is not None
            if run.state.iteration_no_progress_streak >= 2 and not executable_judgment and not (
                objectives and self._pending_correction(run, objectives[0]) is not None
            ):
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
        return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")

    async def _pending_judgment(self, run, slot=None):
        if slot is not None:
            action = await self._repository.lookup(operation_id=slot.action_operation_id)
            if isinstance(action, ArtifactFound):
                return None
            judgment = await self._repository.lookup(operation_id=slot.judgment_operation_id)
            if isinstance(judgment, ArtifactFound):
                read = await self._repository.get(judgment.ref, expected_model=CandidateResearchModelJudgmentArtifact)
                if read.ref != judgment.ref or _digest(_json_model(read.content)) != judgment.ref.content_hash:
                    raise _OperationReplayIndeterminate()
                if read.content.job.step_kind == "judge_existing":
                    payload = await self._judgment_context(run, tuple(run.observation_refs),
                        current_iteration_index=read.content.iteration_index, saved_job=read.content.job)
                    return read.content.job, payload
                return None
        if not run.state.candidates:
            return None
        payload = await self._judgment_context(run, tuple(run.observation_refs),
            current_iteration_index=min(5, run.state.budgets.iterations_consumed), step_kind="judge_existing")
        return (run.judgment_job, payload) if run.judgment_job.required else None

    async def _judge_existing(self, run, request, slot, local_slot, prepared):
        existing = await self._repository.lookup(operation_id=slot.judgment_operation_id)
        iteration = await self._consume_iteration(run, slot.judgment_operation_id)
        if iteration == -2:
            if not isinstance(existing, ArtifactFound):
                return await self._indeterminate(run.run_id)
            iteration = prepared[0].iteration_index
        elif isinstance(existing, ArtifactFound):
            return await self._indeterminate(run.run_id)
        if iteration is None:
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
        if iteration < 0:
            code = {-1: CandidateResearchControlFailureCode.BUDGET_ERROR,
                    -3: CandidateResearchControlFailureCode.REPLAY_MISMATCH}.get(
                        iteration, CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
            return await self._failed(run, request, ControlResearchFailure(code=code), None)
        self._start_step(run, iteration, "judge_existing")
        run.active_slot = slot
        before_state, before_assessments = run.state, run.assessments
        job, payload = prepared
        prepared = job.model_copy(update={"iteration_index": iteration}), payload
        result = await self._execute_judgment(run, request, slot, iteration, before_state, prepared)
        if result is not None:
            return result
        before_keys = {judgment_key(item) for item in before_assessments}
        required_keys = {judgment_key(item) for item in prepared[0].required}
        progressed = bool(({judgment_key(item) for item in run.assessments} - before_keys) & required_keys)
        run.resolved_progress.update(qualification_resolution_keys(before_state))
        newly_resolved = qualification_resolution_keys(run.state) - run.resolved_progress
        run.resolved_progress.update(newly_resolved)
        progressed = progressed or bool(newly_resolved)
        run.state = self._refresh_state(run.state,
            no_progress=0 if progressed else min(2, run.state.iteration_no_progress_streak + 1))
        if local_slot < len(request.operation_plan.iteration_slots) - 1:
            await self._write_in_progress(run, request, slot)
        return None

    async def _execute_judgment(self, run, request, slot, iteration, rollback_state, prepared=None):
        result = await self._judgment(run, request, slot, iteration, prepared)
        if isinstance(result, _JudgmentReplayIndeterminate):
            return await self._indeterminate(run.run_id)
        if result is None:
            return None
        if run.correction_stop_reason is not None:
            await self._write_judgment_failure(run, request, slot, iteration, result)
            run.state = self._state_with(run, no_progress=min(2, run.state.iteration_no_progress_streak + 1))
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
        run.state = QualifiedCandidateSetStateSnapshot(**{**_python_model(rollback_state),
                                                 "observation_refs": tuple(run.observation_refs)})
        auditable = isinstance(result, LLMResearchFailure) or result.code in {
            CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID,
            CandidateResearchControlFailureCode.JUDGMENT_REQUIRED_MISSING,
            CandidateResearchControlFailureCode.STATE_INVARIANT_FAILED}
        ref = await self._write_judgment_failure(run, request, slot, iteration, result) if auditable else None
        return await self._failed(run, request, result, ref)

    async def _load_or_create_run(
        self,
        request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest,
        mode: ResearchMode,
        digest: str,
    ) -> _Run:
        run_id = request.operation_plan.run_id
        current = self._runs.get(run_id)
        if current is not None:
            if current.request_digest == digest and current.mode is mode:
                return current
            if mode is ResearchMode.SUPPLEMENTAL and isinstance(request, CandidateResearchSupplementalRequest) and request.predecessor_candidate_set_ref.operation_id.run_id == run_id:
                if current.last_consistent_ref is not None and request.predecessor_candidate_set_ref != current.last_consistent_ref:
                    raise _ResearchReplayMismatch("supplemental predecessor is not current head")
                try:
                    predecessor_state = await self._read_predecessor_state(request.predecessor_candidate_set_ref, run_id)
                    self._validate_supplemental_evidence(request, predecessor_state, current.requirement_specs)
                except ValueError as error:
                    raise _ResearchReplayMismatch("predecessor contract mismatch") from error
                for gap in request.gaps:
                    fingerprint = supplemental_gap_fingerprint(
                        current.confirmed_digest,
                        current.hotel_ref.content_hash,
                        gap,
                    )
                    if fingerprint in current.gap_fingerprints:
                        raise _ResearchReplayMismatch("supplemental gap fingerprint already consumed")
                    current.gap_fingerprints.add(fingerprint)
                current.request_digest = digest
                current.mode = ResearchMode.SUPPLEMENTAL
                current.predecessor_ref = request.predecessor_candidate_set_ref
                current.state = predecessor_state
                current.last_consistent_ref = request.predecessor_candidate_set_ref
                current.supplemental_baseline_uids = frozenset(item.core.uid for item in predecessor_state.candidates)
                return current
            raise _ResearchReplayMismatch("run request conflict")
        if mode is ResearchMode.INITIAL:
            confirmed = request.confirmed
            preferences = confirmed.requirement.preferences
            supplied = preferences.interests
            if interest_selection_error(preferences) is not None:
                raise ValueError("invalid interest selection")
            if not supplied:
                if not preferences.agent_decides_interests:
                    raise ValueError("effective interests are not determined")
                effective = tuple(InterestTag)
            else:
                effective = tuple(supplied)
                if not 1 <= len(effective) <= 4 or len(set(effective)) != len(effective):
                    raise ValueError("invalid interests")
                effective = tuple(sorted(effective, key=lambda item: list(InterestTag).index(item)))
            hotel_ref = request.hotel_selection_ref
            destination = confirmed.requirement.destination
            if destination is None or not destination.strip():
                raise ValueError("destination is required")
            if (
                hotel_ref.artifact_type != "hotel.selection"
                or hotel_ref.schema_version != "1.0"
                or hotel_ref.operation_id.phase is not WorkflowPhase.HOTEL_SELECTION
                or hotel_ref.operation_id.run_id != run_id
            ):
                raise ValueError("hotel selection ref contract mismatch")
            try:
                selected = await self._repository.get(hotel_ref, expected_model=HotelSelectionArtifact)
            except ArtifactRepositoryError:
                raise ValueError("hotel selection artifact unavailable") from None
            if (
                selected.ref != hotel_ref
                or _digest(_json_model(selected.content)) != hotel_ref.content_hash
                or selected.content.schema_version != "1.0"
            ):
                raise ValueError("hotel selection artifact integrity mismatch")
            if not isinstance(selected.content.decision, HotelSelectedDecision):
                raise ValueError("hotel selection must be selected")
            hotel = selected.content.decision.selected_candidate
            state = self._empty_state(request, hotel_ref, effective, confirmed.confirmation_summary_digest)
            specs = self._requirement_specs(confirmed, self._safe_digest(confirmed.confirmation_summary_digest, confirmed))
            names = tuple(dict.fromkeys(preferences.must_visit_places)) + tuple(dict.fromkeys(preferences.avoid_places))
            if len(names) != len(specs):
                raise ValueError("requirement context incomplete")
            directory = tuple(RequirementContext(kind, group_id, item_ids, name)
                              for (kind, group_id, item_ids), name in zip(specs, names, strict=True))
            project_requirements(specs, directory)
            operation = self._runtime.start_operation(ModelRole.CANDIDATE_RESEARCH)
            run = _Run(
                run_id=run_id, request_digest=digest, mode=mode, hotel_ref=hotel_ref,
                confirmed_digest=self._safe_digest(confirmed.confirmation_summary_digest, confirmed),
                destination=destination.strip(), effective_interests=effective,
                interests_frozen=bool(supplied) or not preferences.agent_decides_interests,
                agent_decides_interests=preferences.agent_decides_interests and not supplied,
                operation=operation, state=state,
                requirement_specs=specs, requirement_context=directory,
            )
            run.fingerprint_trees["hotel"] = hotel
            self._runs[run_id] = run
            return run
        predecessor = request.predecessor_candidate_set_ref
        read = await self._read_candidate_set(predecessor, run_id)
        if not isinstance(read.content.record, (InProgressCandidateSetRecord, CompletedCandidateSetRecord, IncompleteCandidateSetRecord)):
            raise ValueError("predecessor is not a candidate set")
        state = read.content.record.state
        await self._validate_state_refs(state, run_id, require_verified_observations=True)
        self._validate_supplemental_evidence(request, state)
        hotel_ref = state.hotel_selection_ref
        operation = self._runtime.start_operation(ModelRole.CANDIDATE_RESEARCH)
        run = _Run(
            run_id=run_id, request_digest=digest, mode=mode, hotel_ref=hotel_ref,
            confirmed_digest=read.content.record.state.confirmed_requirement_digest,
            destination="", effective_interests=tuple(state.effective_interests),
            interests_frozen=True, agent_decides_interests=False,
            operation=operation, state=state, predecessor_ref=predecessor, last_consistent_ref=predecessor,
            supplemental_baseline_uids=frozenset(item.core.uid for item in state.candidates),
        )
        run.semantic_invocations = state.budgets.semantic_invocations_consumed
        run.output_recoveries_used = state.budgets.corrections_consumed
        run.invocation_attempt_refs = list(read.content.invocation_attempt_refs)
        run.provider_attempts = state.budgets.known_provider_attempts
        run.provider_attempts_complete = state.budgets.provider_attempts_complete
        run.usage_complete = state.budgets.usage_complete
        for name in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            run.usage[name] = getattr(state.budgets.known_usage, name)
        run.action_refs.extend(state.model_action_refs)
        run.judgment_refs.extend(state.model_judgment_refs)
        for ref in state.observation_refs:
            run.observation_refs.append(ref)
            try:
                evidence = await self._repository.get(ref, expected_model=CandidateResearchPlaceObservationArtifact)
            except ArtifactRepositoryError:
                raise ValueError("observation directory is not replayable") from None
            run.fingerprints[evidence.content.action_fingerprint] = ref
        if isinstance(request, CandidateResearchSupplementalRequest):
            for gap in request.gaps:
                fingerprint = supplemental_gap_fingerprint(run.confirmed_digest, run.hotel_ref.content_hash, gap)
                if fingerprint in run.gap_fingerprints:
                    raise ValueError("supplemental gap fingerprint already consumed")
                run.gap_fingerprints.add(fingerprint)
        # Supplemental requests inherit the immutable requirement groups from
        # the predecessor; no requirement text is copied into the artifact.
        run.requirement_specs = tuple(
            (group.requirement_kind, group.group_id, tuple(group.requirement_item_ids))
            for group in state.requirement_group_states
        )
        self._runs[run_id] = run
        return run

    async def _read_candidate_set(self, ref: ArtifactRef, run_id: UUID):
        if (
            ref.artifact_type != "candidate_research.candidate_set"
            or ref.schema_version != "5.0"
            or ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH
            or ref.operation_id.run_id != run_id
        ):
            raise ValueError("candidate set ref contract mismatch")
        try:
            read = await self._repository.get(ref, expected_model=CandidateResearchArtifact)
        except ArtifactRepositoryError:
            raise ValueError("candidate set artifact unavailable") from None
        if (
            read.ref != ref
            or read.content.run_id != run_id
            or _digest(_json_model(read.content)) != ref.content_hash
        ):
            raise ValueError("candidate set artifact integrity mismatch")
        return read

    async def _read_predecessor_state(self, ref: ArtifactRef, run_id: UUID) -> CandidateSetStateSnapshot:
        read = await self._read_candidate_set(ref, run_id)
        if not isinstance(read.content.record, (InProgressCandidateSetRecord, CompletedCandidateSetRecord, IncompleteCandidateSetRecord)):
            raise ValueError("predecessor is not a candidate set")
        state = read.content.record.state
        await self._validate_state_refs(state, run_id, require_verified_observations=True)
        await self._validate_qualification_history(read.content)
        return state

    async def _validate_state_refs(
        self,
        state: CandidateSetStateSnapshot,
        run_id: UUID,
        *,
        expected_mode: ResearchMode | None = None,
        require_verified_observations: bool = False,
    ) -> None:
        """Re-read state directories and verify every nested evidence link."""
        observation_refs = set(state.observation_refs)
        action_refs = set(state.model_action_refs)
        judgment_refs = set(state.model_judgment_refs)
        for refs, expected_type, expected_model in (
            (state.model_action_refs, "candidate_research.model_action", CandidateResearchModelActionArtifact),
            (state.model_judgment_refs, "candidate_research.model_judgment", CandidateResearchModelJudgmentArtifact),
            (state.observation_refs, "candidate_research.place_observation", CandidateResearchPlaceObservationArtifact),
        ):
            for item_ref in refs:
                if (
                    item_ref.artifact_type != expected_type
                    or item_ref.schema_version != ({"candidate_research.model_action": "3.0", "candidate_research.model_judgment": "4.0"}.get(expected_type, "1.0"))
                    or item_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH
                    or item_ref.operation_id.run_id != run_id
                ):
                    raise ValueError("state artifact ref contract mismatch")
                try:
                    item_read = await self._repository.get(item_ref, expected_model=expected_model)
                except ArtifactRepositoryError:
                    raise ValueError("state artifact unavailable") from None
                content = item_read.content
                if (
                    item_read.ref != item_ref
                    or content.run_id != run_id
                    or (expected_mode is not None and content.mode is not expected_mode)
                    or _digest(_json_model(content)) != item_ref.content_hash
                ):
                    raise ValueError("state artifact integrity mismatch")
                model_record = unwrap_model_record(content.result)
                if isinstance(content, CandidateResearchModelActionArtifact) and isinstance(model_record, ModelActionAcceptedRecord):
                    if any(
                        disposition.reused_observation_ref is not None
                        and disposition.reused_observation_ref not in observation_refs
                        for disposition in model_record.action_dispositions
                    ):
                        raise ValueError("action reused observation is outside state directory")
                if isinstance(content, CandidateResearchModelJudgmentArtifact) and any(
                    evidence_ref not in observation_refs for evidence_ref in content.evidence_refs
                ):
                    raise ValueError("judgment evidence is outside state directory")
                if (
                    require_verified_observations
                    and isinstance(content, CandidateResearchPlaceObservationArtifact)
                    and not isinstance(
                        content.result,
                        (
                            PlaceSearchSuccessObservation,
                            PlaceSearchNoResultObservation,
                            PlaceDetailsSuccessObservation,
                            PlaceDetailsNoResultObservation,
                        ),
                    )
                ):
                    raise ValueError("supplemental evidence must be verified place observation")
        if action_refs & judgment_refs or action_refs & observation_refs or judgment_refs & observation_refs:
            raise ValueError("state artifact directories overlap")

    async def _validate_qualification_history(self, artifact: CandidateResearchArtifact) -> None:
        """研究重放与下游格式化使用同一只读来源核验。"""
        from .qualification import validate_qualification_sources
        state = getattr(artifact.record, "state", None)
        sources = {}
        if state is not None:
            for refs, model in (
                (state.observation_refs, CandidateResearchPlaceObservationArtifact),
                (state.model_action_refs, CandidateResearchModelActionArtifact),
                (state.model_judgment_refs, CandidateResearchModelJudgmentArtifact),
            ):
                for ref in refs:
                    sources[ref.artifact_id] = await self._repository.get(ref, expected_model=model)
        for ref in getattr(artifact, "invocation_attempt_refs", ()):
            sources[ref.artifact_id] = await self._repository.get(ref, expected_model=CandidateResearchInvocationAttemptArtifact)
        validate_qualification_sources(artifact, sources)
    async def _validate_terminal_refs(self, artifact: CandidateResearchArtifact) -> None:
        """Re-read terminal-linked artifacts so replay cannot trust stale refs."""
        record = artifact.record
        state = getattr(record, "state", None)
        if state is not None:
            # Supplemental terminal states legitimately contain the predecessor
            # directories (initial mode) plus newly produced supplemental refs;
            # the artifact mode alone cannot be imposed on every nested entry.
            await self._validate_state_refs(state, artifact.run_id)
        await self._validate_qualification_history(artifact)
        if artifact.predecessor_ref is not None:
            predecessor = await self._read_candidate_set(artifact.predecessor_ref, artifact.run_id)
            predecessor_state = getattr(predecessor.content.record, "state", None)
            if predecessor_state is None:
                raise ValueError("terminal predecessor has no state")
            await self._validate_state_refs(predecessor_state, artifact.run_id)
            await self._validate_qualification_history(predecessor.content)

        async def verify(ref: ArtifactRef, expected_type: str, expected_model: type[BaseModel]) -> BaseModel:
            if (
                ref.artifact_type != expected_type
                or ref.schema_version != ("5.0" if expected_type == "candidate_research.candidate_set" else {"candidate_research.model_action": "3.0", "candidate_research.model_judgment": "4.0"}.get(expected_type, "1.0"))
                or ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH
                or ref.operation_id.run_id != artifact.run_id
            ):
                raise ValueError("terminal linked ref contract mismatch")
            try:
                read = await self._repository.get(ref, expected_model=expected_model)
            except ArtifactRepositoryError:
                raise ValueError("terminal linked artifact unavailable") from None
            if read.ref != ref or _digest(_json_model(read.content)) != ref.content_hash:
                raise ValueError("terminal linked artifact integrity mismatch")
            return read.content

        if not isinstance(record, FailedCandidateSetRecord):
            return
        if record.last_consistent_candidate_set_ref is not None:
            await verify(
                record.last_consistent_candidate_set_ref,
                "candidate_research.candidate_set",
                CandidateResearchArtifact,
            )
        failure_ref = record.failure_artifact_ref
        if isinstance(record.failure, ArtifactLLMResearchFailureSnapshot):
            expected_type = "candidate_research.model_action" if record.failure.invocation_kind is ResearchInvocationKind.ACTION else "candidate_research.model_judgment"
            expected_model = CandidateResearchModelActionArtifact if record.failure.invocation_kind is ResearchInvocationKind.ACTION else CandidateResearchModelJudgmentArtifact
            if failure_ref is None:
                raise ValueError("llm terminal failure ref missing")
            await verify(failure_ref, expected_type, expected_model)
            if state is not None:
                directory = state.model_action_refs if expected_type.endswith("action") else state.model_judgment_refs
                if failure_ref not in directory:
                    raise ValueError("llm terminal failure ref is outside state directory")
        elif isinstance(record.failure, ArtifactMapResearchFailureSnapshot):
            if failure_ref is None or failure_ref != record.failure.observation_ref:
                raise ValueError("map terminal failure ref mismatch")
            await verify(failure_ref, "candidate_research.place_observation", CandidateResearchPlaceObservationArtifact)
            if state is not None and failure_ref not in state.observation_refs:
                raise ValueError("map terminal failure ref is outside state directory")
        else:
            expected_type = {
                CandidateResearchControlFailureCode.ACTION_BATCH_INVALID: "candidate_research.model_action",
                CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID: "candidate_research.model_judgment",
                CandidateResearchControlFailureCode.UNVERIFIED_RESULT: "candidate_research.place_observation",
            }.get(record.failure.code)
            if expected_type is None:
                if failure_ref is not None:
                    generic = {
                        "candidate_research.model_action": CandidateResearchModelActionArtifact,
                        "candidate_research.model_judgment": CandidateResearchModelJudgmentArtifact,
                        "candidate_research.place_observation": CandidateResearchPlaceObservationArtifact,
                    }.get(failure_ref.artifact_type)
                    if generic is None:
                        raise ValueError("generic control terminal failure ref type invalid")
                    await verify(failure_ref, failure_ref.artifact_type, generic)
                    if state is not None and failure_ref not in (
                        state.model_action_refs + state.model_judgment_refs + state.observation_refs
                    ):
                        raise ValueError("generic control terminal failure ref is outside state directory")
            else:
                if failure_ref is None:
                    raise ValueError("control terminal failure ref missing")
                expected_model = {
                    "candidate_research.model_action": CandidateResearchModelActionArtifact,
                    "candidate_research.model_judgment": CandidateResearchModelJudgmentArtifact,
                    "candidate_research.place_observation": CandidateResearchPlaceObservationArtifact,
                }[expected_type]
                await verify(failure_ref, expected_type, expected_model)
                if state is not None:
                    directory = state.model_action_refs if expected_type.endswith("action") else state.model_judgment_refs if expected_type.endswith("judgment") else state.observation_refs
                    if failure_ref not in directory:
                        raise ValueError("control terminal failure ref is outside state directory")

    @staticmethod
    def _validate_supplemental_evidence(
        request: CandidateResearchSupplementalRequest,
        state: CandidateSetStateSnapshot,
        requirement_specs: tuple = (),
    ) -> None:
        observation_refs = set(state.observation_refs)
        by_ref = {ref: index for index, ref in enumerate(state.observation_refs)}
        for gap in request.gaps:
            if gap.evidence_refs == (request.predecessor_candidate_set_ref,) and request.schema_version == "2.0":
                if gap.gap_type in {"candidate_count_shortfall", "meal_count_shortfall"}:
                    purposes = tuple(candidate_admitted_purposes(item) for item in state.candidates)
                    if gap.gap_type == "meal_count_shortfall":
                        count = sum(gap.meal_intent.value in value for value in purposes)
                    elif gap.role is ResearchRole.ATTRACTION:
                        count = sum("attraction" in value for value in purposes)
                    else:
                        count = sum(any(meal in value for meal in ("lunch", "dinner")) for value in purposes)
                    if gap.current_count != count or gap.target_count <= count:
                        raise ValueError("state quota does not match admitted predecessor supply")
                elif gap.gap_type == "must_visit_resolution_missing":
                    group = next((item for item in state.requirement_group_states if item.group_id == gap.requirement_group_id), None)
                    # With no candidates there is no judgment group to inspect.
                    # The same-process frozen request directory still owns identity.
                    empty_known_group = (group is None and not state.candidates
                        and ("must_visit", gap.requirement_group_id, gap.requirement_item_ids) in requirement_specs)
                    if not empty_known_group and (group is None or group.requirement_kind != "must_visit" or group.requirement_item_ids != gap.requirement_item_ids):
                        raise ValueError("state must gap target mismatch")
                    if CandidateResearchService._gap_satisfied(state, gap):
                        raise ValueError("state must gap is already resolved")
                else:
                    raise ValueError("unsupported state proof target")
                continue
            if any(ref not in observation_refs for ref in gap.evidence_refs):
                raise ValueError("supplemental evidence ref is not in predecessor directory")
            summary = CandidateResearchService._gap_summary(gap, tuple())
            related = _gap_related_observation_indexes(state, summary)
            indexes = {by_ref[ref] for ref in gap.evidence_refs}
            if not related or not indexes.issubset(related):
                raise ValueError("supplemental evidence is unrelated to typed gap target")
            if gap.gap_type == "must_visit_resolution_missing":
                group = next((item for item in state.requirement_group_states if item.group_id == gap.requirement_group_id), None)
                if group is None or group.requirement_kind != "must_visit" or tuple(group.requirement_item_ids) != tuple(gap.requirement_item_ids):
                    raise ValueError("must gap group/item target mismatch")
            elif isinstance(gap, AvoidResolutionMissingGap):
                group = next((item for item in state.requirement_group_states if item.group_id == gap.requirement_group_id), None)
                if group is None or group.requirement_kind != "avoid" or tuple(group.requirement_item_ids) != tuple(gap.requirement_item_ids):
                    raise ValueError("avoid gap group/item target mismatch")
                expected = tuple(
                    candidate.core.uid for candidate in state.candidates
                    if candidate.admission_ordinal in group.evaluated_candidate_ordinals
                    and next((match.verdict for match in candidate.avoid_matches if match.requirement_group_id == group.group_id), group.default_verdict)
                    in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED}
                )
                if tuple(gap.candidate_uids) != expected:
                    raise ValueError("avoid gap candidate target mismatch")

    @staticmethod
    def _requirement_specs(confirmed: BaseModel, confirmed_digest: str) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        requirement = getattr(confirmed, "requirement")
        preferences = requirement.preferences
        directory: dict[str, str] = {}
        specs: list[tuple[str, str, tuple[str, ...]]] = []
        for field_name, values in (("must_visit", preferences.must_visit_places), ("avoid", preferences.avoid_places)):
            groups: dict[str, list[tuple[int, str]]] = {}
            for index, text in enumerate(values):
                item_id = requirement_item_id(confirmed_digest, field_name, index, text, directory=directory)
                groups.setdefault(text, []).append((index, item_id))
            for text, items in groups.items():
                item_ids = tuple(item_id for _, item_id in items)
                group_id = requirement_group_id(confirmed_digest, field_name, item_ids, items[0][0], text, directory=directory)
                specs.append((field_name, group_id, item_ids))
        return tuple(specs)

    @staticmethod
    def _safe_digest(value: str, model: BaseModel) -> str:
        if isinstance(value, str) and len(value) == 71 and value.startswith("sha256:"):
            return value
        return content_digest(model)

    def _empty_state(
        self,
        request: CandidateResearchInitialRequest,
        hotel_ref: ArtifactRef,
        interests: tuple[InterestTag, ...],
        confirmed_digest: str,
    ) -> CandidateSetStateSnapshot:
        zero_usage = ArtifactResearchBudgetSummarySnapshot(
            iteration_limit=6, iterations_consumed=0, place_call_limit=48,
            place_calls_consumed=0, semantic_invocation_limit=12,
            semantic_invocations_consumed=0, known_provider_attempts=0,
            provider_attempts_complete=True,
            known_usage=ArtifactKnownLLMUsageSnapshot(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0),
            usage_complete=True, correction_limit=2, corrections_consumed=0,
        )
        counts = ArtifactResearchCountSummarySnapshot(
            attraction_count=0, attraction_target=role_target(ResearchRole.ATTRACTION, self._sightseeing_days(request)),
            restaurant_count=0, restaurant_target=role_target(ResearchRole.RESTAURANT, self._sightseeing_days(request)),
            unique_eligible_count=0, admitted_slots=0, candidate_capacity=128,
        )
        coverage = ArtifactResearchCoverageSummarySnapshot(
            effective_interests=interests, covered_interests=(), covered_meals=(), must_item_count=0,
            resolved_must_item_count=0, details_success_count=0, details_target=0,
            initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False,
        )
        return QualifiedCandidateSetStateSnapshot(
            confirmed_requirement_digest=self._safe_digest(confirmed_digest, request.confirmed),
            hotel_selection_ref=hotel_ref, effective_interests=interests,
            model_action_refs=(), model_judgment_refs=(), observation_refs=(), candidates=(),
            requirement_group_states=(), search_coverage=SearchCoverageState(records=(), initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False),
            counts=counts, coverage=coverage, budgets=zero_usage, pending_gaps=(), iteration_no_progress_streak=0,
        )

    @staticmethod
    def _sightseeing_days(request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest) -> int:
        if isinstance(request, CandidateResearchInitialRequest):
            days = request.confirmed.requirement.time_window.sightseeing_days
            if days is None:
                raise ValueError("sightseeing days are required")
            return days
        return 1

    def _objectives(
        self,
        run: _Run,
        request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest,
    ) -> tuple[ResearchObjective, ...]:
        directory: dict[str, str] = {}
        objectives: list[ResearchObjective] = []
        if isinstance(request, CandidateResearchSupplementalRequest):
            for gap in request.gaps:
                if self._gap_satisfied(run, gap):
                    continue
                kind = ResearchObjectiveKind.SUPPLEMENTAL_GAP
                oid = objective_fingerprint(
                    kind=kind, gap_id=gap.gap_id, requirement_group_id=getattr(gap, "requirement_group_id", None), role=getattr(gap, "role", None),
                    interest=getattr(gap, "interest", None), meal_intent=getattr(gap, "meal_intent", None),
                    anchor=getattr(gap, "anchor", None), radius_km=getattr(gap, "radius_km", None),
                    candidate_uids=tuple(getattr(gap, "candidate_uids", ())), confirmed_requirement_digest=run.confirmed_digest,
                    hotel_selection_content_hash=run.hotel_ref.content_hash, predecessor_candidate_set_content_hash=run.predecessor_ref.content_hash if run.predecessor_ref is not None else None, directory=directory,
                )
                objectives.append(ResearchObjective(
                    objective_id=oid, kind=kind, gap_id=gap.gap_id, gap_type=gap.gap_type, rejected_uid=getattr(gap, "rejected_uid", None), requirement_group_id=getattr(gap, "requirement_group_id", None),
                    role=getattr(gap, "role", None), interest=getattr(gap, "interest", None), meal_intent=getattr(gap, "meal_intent", None),
                    anchor=getattr(gap, "anchor", None), radius_km=getattr(gap, "radius_km", None), candidate_uids=tuple(getattr(gap, "candidate_uids", ())),
                ))
        # Requirement resolution is a first-class objective.  Keep the frozen
        # group identity and ordered item IDs visible to the model; avoid
        # objectives additionally carry the currently unresolved UID target.
        for requirement_kind, group_id, item_ids in run.requirement_specs:
            group = next((item for item in run.state.requirement_group_states if item.group_id == group_id), None)
            unresolved_uids: tuple[str, ...] = ()
            if group is not None:
                unresolved: list[str] = []
                for candidate in run.state.candidates:
                    if requirement_kind == "avoid" and candidate.state in {CandidateState.EXCLUDED, CandidateState.CONFLICTED}:
                        continue
                    if candidate.admission_ordinal not in group.evaluated_candidate_ordinals:
                        continue
                    matches = candidate.must_matches if requirement_kind == "must_visit" else candidate.avoid_matches
                    match = next((item for item in matches if item.requirement_group_id == group_id), None)
                    verdict = match.verdict if match is not None else group.default_verdict
                    if verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED}:
                        unresolved.append(candidate.core.uid)
                if requirement_kind == "must_visit":
                    blocked = tuple(candidate.core.uid for candidate in run.state.candidates
                        if candidate.state not in {CandidateState.EXCLUDED, CandidateState.CONFLICTED}
                        and not candidate_admitted_purposes(candidate)
                        and any(match.requirement_group_id == group_id and match.verdict is MatchVerdict.CONFIRMED
                                for match in candidate.must_matches))
                    unresolved_uids = tuple(dict.fromkeys((*blocked, *unresolved)))
                else:
                    unresolved_uids = tuple(unresolved)
            if requirement_kind == "must_visit":
                satisfied = group is not None and any(
                    candidate_admitted_purposes(candidate)
                    and any(match.requirement_group_id == group_id and match.verdict is MatchVerdict.CONFIRMED for match in candidate.must_matches)
                    for candidate in run.state.candidates
                )
                if satisfied:
                    continue
                kind = ResearchObjectiveKind.MUST_VISIT_RESOLUTION
            else:
                if not unresolved_uids or not self._supply_qualification_needed(run.state):
                    continue
                kind = ResearchObjectiveKind.AVOID_RESOLUTION
            oid = objective_fingerprint(
                kind=kind, gap_id=None, requirement_group_id=group_id, role=None, interest=None,
                meal_intent=None, anchor=None, radius_km=None, candidate_uids=unresolved_uids,
                confirmed_requirement_digest=run.confirmed_digest,
                hotel_selection_content_hash=run.hotel_ref.content_hash,
                predecessor_candidate_set_content_hash=run.predecessor_ref.content_hash if run.predecessor_ref is not None else None,
                directory=directory,
            )
            objectives.append(ResearchObjective(
                objective_id=oid, kind=kind, gap_id=None, requirement_group_id=group_id,
                role=None, interest=None, meal_intent=None, anchor=None, radius_km=None,
                candidate_uids=unresolved_uids,
            ))
        city_needed = not run.state.search_coverage.initial_city_attraction_search_done
        hotel_needed = not run.state.search_coverage.initial_hotel_restaurant_nearby_done
        if city_needed:
            objectives.append(self._objective(run, ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, role=ResearchRole.ATTRACTION))
        if hotel_needed:
            objectives.append(self._objective(run, ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY, role=ResearchRole.RESTAURANT, anchor={"anchor_type": "hotel"}, radius_km=3))
        for meal in MealIntent:
            if meal not in run.state.coverage.covered_meals:
                kind = ResearchObjectiveKind.MEAL_LUNCH if meal is MealIntent.LUNCH else ResearchObjectiveKind.MEAL_DINNER
                objectives.append(self._objective(run, kind, role=ResearchRole.RESTAURANT, meal_intent=meal))
        for interest in run.effective_interests:
            if interest not in run.state.coverage.covered_interests:
                objectives.append(self._objective(run, ResearchObjectiveKind.INTEREST_COVERAGE, interest=interest))
        if run.state.counts.attraction_count < run.state.counts.attraction_target:
            objectives.append(self._objective(run, ResearchObjectiveKind.ATTRACTION_COUNT, role=ResearchRole.ATTRACTION))
        if run.state.counts.restaurant_count < run.state.counts.restaurant_target:
            objectives.append(self._objective(run, ResearchObjectiveKind.RESTAURANT_COUNT, role=ResearchRole.RESTAURANT))
        missing_details = tuple(item.core.uid for item in run.state.candidates
            if candidate_admitted_purposes(item) and item.details.status == "not_requested")
        if missing_details:
            objectives.append(self._objective(run, ResearchObjectiveKind.DETAILS_MATURITY, candidate_uids=missing_details[:128]))
        ordered = deterministic_objectives(item for item in objectives if self._allowed_objective_actions(run, item))
        if ordered and self._pending_correction(run, ordered[0]) is not None:
            ordered = ordered[:1]
        run.objective_directory = {item.objective_id: item for item in ordered}
        run.objective_history.update(run.objective_directory)
        return ordered

    def _objective(self, run: _Run, kind: ResearchObjectiveKind, **values: object) -> ResearchObjective:
        directory: dict[str, str] = {}
        role = values.get("role")
        interest = values.get("interest")
        meal = values.get("meal_intent")
        anchor = values.get("anchor")
        requirement_group_id = values.get("requirement_group_id")
        candidate_uids = tuple(values.get("candidate_uids", ()))
        if isinstance(anchor, dict):
            from .models import HotelAnchorRef
            anchor = HotelAnchorRef()
        oid = objective_fingerprint(
            kind=kind, gap_id=None, requirement_group_id=requirement_group_id if isinstance(requirement_group_id, str) else None, role=role if isinstance(role, ResearchRole) else None,
            interest=interest if isinstance(interest, InterestTag) else None, meal_intent=meal if isinstance(meal, MealIntent) else None,
            anchor=anchor, radius_km=values.get("radius_km") if isinstance(values.get("radius_km"), int) else None,
            candidate_uids=candidate_uids, confirmed_requirement_digest=run.confirmed_digest,
            hotel_selection_content_hash=run.hotel_ref.content_hash, predecessor_candidate_set_content_hash=None, directory=directory,
        )
        return ResearchObjective(objective_id=oid, kind=kind, gap_id=None, requirement_group_id=requirement_group_id if isinstance(requirement_group_id, str) else None,
                                 role=role if isinstance(role, ResearchRole) else None,
                                 interest=interest if isinstance(interest, InterestTag) else None,
                                 meal_intent=meal if isinstance(meal, MealIntent) else None, anchor=anchor,
                                 radius_km=values.get("radius_km") if isinstance(values.get("radius_km"), int) else None,
                                 candidate_uids=candidate_uids)

    async def _iteration(
        self,
        run: _Run,
        request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest,
        slot: ResearchIterationOperationIds,
        local_slot: int,
        objectives: tuple[ResearchObjective, ...],
    ) -> CandidateResearchOutcome | None:
        async def persist_intermediate_if_needed() -> None:
            if local_slot < len(request.operation_plan.iteration_slots) - 1:
                await self._write_in_progress(run, request, slot)

        run.active_slot = slot
        replay_ref: ArtifactRef | None = None
        action_lookup = await self._repository.lookup(operation_id=slot.action_operation_id)
        if isinstance(action_lookup, ArtifactFound):
            replay = await self._replay_action(run, slot.action_operation_id)
            if replay is None:
                return await self._indeterminate(run.run_id)
            consumed = await self._consume_iteration(run, slot.action_operation_id)
            if consumed != -2:
                return await self._indeterminate(run.run_id)
            iteration_index, action_outcome, replay_ref = replay
            self._remember_action_ref(run, replay_ref)
        elif isinstance(action_lookup, ArtifactAbsent):
            iteration_index = await self._consume_iteration(run, slot.action_operation_id)
            if iteration_index == -2:
                replay = await self._replay_action(run, slot.action_operation_id)
                if replay is None:
                    return await self._indeterminate(run.run_id)
                iteration_index, action_outcome, replay_ref = replay
                self._remember_action_ref(run, replay_ref)
            else:
                action_outcome = None
        else:
            raise RuntimeError("action artifact lookup contract violation")
        if iteration_index == -1:
            return await self._failed(
                run,
                request,
                ControlResearchFailure(code=CandidateResearchControlFailureCode.BUDGET_ERROR),
                None,
            )
        if iteration_index == -3:
            return await self._failed(
                run,
                request,
                ControlResearchFailure(code=CandidateResearchControlFailureCode.REPLAY_MISMATCH),
                None,
            )
        if iteration_index == -4:
            return await self._failed(
                run,
                request,
                ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR),
                None,
            )
        if iteration_index is None:
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
        self._start_step(run, iteration_index, "search_and_judge")
        run.active_slot = slot
        if replay_ref is not None:
            run.active_action_ref = replay_ref
            saved_action = await self._repository.get(replay_ref, expected_model=CandidateResearchModelActionArtifact)
            await self._restore_attempt_chain(run, saved_action.content, "action")
        if action_outcome is None and run.semantic_invocations >= 12:
            return await self._failed(
                run,
                request,
                ControlResearchFailure(code=CandidateResearchControlFailureCode.BUDGET_ERROR),
                None,
            )
        while True:
            if action_outcome is None:
                prompt = build_action_prompt(self._action_context(run, objectives))
                action_outcome = await self._invoke_action(run, prompt)
            if isinstance(action_outcome, tuple) and isinstance(action_outcome[0], _PolicyReplay):
                replay = action_outcome[0]
                return await self._reject_action(run, request, slot, iteration_index, local_slot,
                    objectives, replay.output, action_outcome[1], replay.rule, replay_ref=replay_ref)
            if isinstance(action_outcome, LLMResearchFailure):
                action_ref = replay_ref or await self._write_action_artifact(run, request, slot, iteration_index, action_outcome, None)
                if run.correction_stop_reason is not None:
                    run.state = self._state_with(run, no_progress=min(2, run.state.iteration_no_progress_streak + 1))
                    return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
                return await self._failed(run, request, action_outcome, action_ref)
            if isinstance(action_outcome, ControlResearchFailure):
                if replay_ref is not None:
                    return await self._failed(run, request, action_outcome, replay_ref)
                await self._write_control_attempt(run, "action", action_outcome)
                return await self._failed(run, request, action_outcome, None)
            output, metadata = action_outcome
            if isinstance(output, (ProposedActionBatch, NoUsefulActionProposed)):
                try:
                    self._freeze_interests(run, output)
                except (TypeError, ValueError):
                    action_record = ModelActionControllerRejectedRecord(
                        record_type="controller_rejected",
                        output=self._action_output_snapshot(output),
                        metadata=metadata,
                        code="action_batch_invalid",
                    )
                    action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
                    self._remember_action_ref(run, action_ref)
                    return await self._failed(
                        run,
                        request,
                        ControlResearchFailure(code=CandidateResearchControlFailureCode.ACTION_BATCH_INVALID),
                        action_ref,
                    )
            if isinstance(output, NoUsefulActionProposed):
                if objectives and (not await self._no_useful_action_is_legal(run, output, objectives)
                        or self._pending_correction(run, objectives[0]) is not None):
                    rejected = await self._reject_action(run, request, slot, iteration_index, local_slot,
                        objectives, output, metadata, "no_useful_action_invalid")
                    if rejected is None:
                        action_outcome = None
                        continue
                    return rejected
                action_record = ModelActionAcceptedRecord(record_type="accepted", output=self._action_output_snapshot(output), metadata=metadata, action_dispositions=())
                action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
                self._remember_action_ref(run, action_ref)
                run.state = self._state_with(run, no_progress=min(2, run.state.iteration_no_progress_streak + 1))
                if run.state.iteration_no_progress_streak >= 2:
                    return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
                await persist_intermediate_if_needed()
                return None
            from .correction import bind_first_named_search
            actions = bind_first_named_search(output.actions, objectives, run.requirement_context, run.named_searches)
            output = ProposedActionBatch(actions=tuple(actions), effective_interests=output.effective_interests)
            validation = self._validate_actions(run, actions, objectives, slot)
            if validation in {"objective_parameters", "duplicate", "details_already_requested"} and objectives:
                rejected = await self._reject_action(run, request, slot, iteration_index, local_slot,
                    objectives, output, metadata, validation)
                if rejected is None:
                    action_outcome = None
                    continue
                return rejected
            if validation is not None:
                action_record = ModelActionControllerRejectedRecord(record_type="controller_rejected", output=self._action_output_snapshot(output), metadata=metadata, code="action_batch_invalid")
                action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
                self._remember_action_ref(run, action_ref)
                failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.ACTION_BATCH_INVALID)
                return await self._failed(run, request, failure, action_ref)
            highest = objectives[0].objective_id if objectives else None
            if highest is not None and actions[0].objective_id != highest:
                rejected = await self._reject_action(run, request, slot, iteration_index, local_slot,
                    objectives, output, metadata, "highest_priority_objective_ignored")
                if rejected is None:
                    action_outcome = None
                    continue
                return rejected
            break
        if objectives:
            from dataclasses import replace
            from .correction import target_fingerprint
            target = target_fingerprint(objectives[0])
            run.corrections = tuple(replace(item, active=False) if item.target == target else item for item in run.corrections)
        dispositions: list[ActionDispositionSnapshot] = []
        fresh_actions: list[ProposedAction] = []
        for action in actions:
            fp = action_fingerprint(action, run.destination)
            previous = run.fingerprints.get(fp)
            if previous is not None:
                dispositions.append(ActionDispositionSnapshot(action_ordinal=action.ordinal, action_fingerprint=fp, disposition="duplicate_action", reused_observation_ref=previous))
                objective = run.objective_directory[action.objective_id]
                if isinstance(action, ProposedSearchAction) and objective.requirement_group_id:
                    run.named_searches.add(objective.requirement_group_id)
            else:
                fresh_actions.append(action)
                dispositions.append(ActionDispositionSnapshot(action_ordinal=action.ordinal, action_fingerprint=fp, disposition="accepted", reused_observation_ref=None))
        remaining_place = await self._place_remaining(run)
        if remaining_place < 0:
            failure = ControlResearchFailure(code=(CandidateResearchControlFailureCode.BUDGET_ERROR
                if remaining_place == -1 else CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR))
            await self._write_control_attempt(run, "action", failure)
            return await self._failed(run, request, failure, None)
        if run.semantic_invocations >= 12:
            dispositions = tuple(ActionDispositionSnapshot(action_ordinal=action.ordinal,
                action_fingerprint=action_fingerprint(action, run.destination),
                disposition="not_admitted_budget_exhausted", reused_observation_ref=None) for action in actions)
            action_record = ModelActionAcceptedRecord(record_type="accepted", output=self._action_output_snapshot(output),
                                                      metadata=metadata, action_dispositions=dispositions)
            action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
            self._remember_action_ref(run, action_ref)
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
        allowed_fresh = fresh_actions[:remaining_place]
        allowed_ids = {id(item) for item in allowed_fresh}
        for position, action in enumerate(actions):
            if action in fresh_actions and id(action) not in allowed_ids:
                dispositions[position] = ActionDispositionSnapshot(action_ordinal=action.ordinal, action_fingerprint=dispositions[position].action_fingerprint, disposition="not_admitted_budget_exhausted", reused_observation_ref=None)
        async def publish_action():
            record = ModelActionAcceptedRecord(record_type="accepted", output=self._action_output_snapshot(output),
                metadata=metadata, action_dispositions=tuple(dispositions))
            ref = await self._write_model_action(run, request, slot, iteration_index, record)
            self._remember_action_ref(run, ref)
            return ref

        if not fresh_actions:
            await publish_action()
            run.state = self._state_with(run, no_progress=min(2, run.state.iteration_no_progress_streak + 1))
            await persist_intermediate_if_needed()
            return None
        place_actions = allowed_fresh[:len(slot.place_observation_operation_ids)]
        if not place_actions:
            await publish_action()
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
        reservation = await self._reserve_places(run, slot, place_actions)
        if reservation[0] == -2:
            await self._write_control_attempt(run, "action", ControlResearchFailure(
                code=CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE))
            return await self._indeterminate(run.run_id)
        admitted = {item.ordinal for item in place_actions[:reservation[1]]}
        if reservation[0] != 1:
            for position, action in enumerate(actions):
                if action in fresh_actions and action.ordinal not in admitted:
                    dispositions[position] = ActionDispositionSnapshot(action_ordinal=action.ordinal,
                        action_fingerprint=dispositions[position].action_fingerprint,
                        disposition="not_admitted_budget_exhausted", reused_observation_ref=None)
        action_ref = await publish_action()
        if reservation[0] != 1:
            for action, operation_id in zip(place_actions[:reservation[1]], slot.place_observation_operation_ids):
                try:
                    ref = await self._write_not_issued(run, slot, iteration_index, action, operation_id)
                except ArtifactRepositoryError:
                    return await self._failed(run, request, ControlResearchFailure(code=CandidateResearchControlFailureCode.ARTIFACT_ERROR), action_ref)
                run.observation_refs.append(ref)
            if reservation[0] in {-1, -3}:
                code = (CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR if reservation[0] == -1
                        else CandidateResearchControlFailureCode.BUDGET_ERROR)
                return await self._failed(run, request, ControlResearchFailure(code=code), action_ref)
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
        for action in place_actions:
            objective = run.objective_directory[action.objective_id]
            if isinstance(action, ProposedSearchAction) and objective.requirement_group_id:
                run.named_searches.add(objective.requirement_group_id)
        replayed_results: dict[OperationId, _MapResult] = {}
        fresh_place_actions: list[ProposedAction] = []
        for action, operation_id in zip(place_actions, slot.place_observation_operation_ids):
            if operation_id in run.replayed_place_refs:
                replayed = await self._replay_observation(run, operation_id, action, run.replayed_place_refs[operation_id])
                if replayed is None:
                    return await self._indeterminate(run.run_id)
                replayed_results[operation_id] = replayed
            else:
                fresh_place_actions.append(action)
        new_observation = False
        primary_failure: CandidateResearchFailure | None = None
        primary_failure_ref: ArtifactRef | None = None
        pre_wave_state = run.state
        pre_iteration_state = run.state
        fresh_operation_ids = {id(action): operation_id for action, operation_id in zip(place_actions, slot.place_observation_operation_ids) if operation_id not in run.replayed_place_refs}
        persisted_fresh: set[OperationId] = set()

        async def persist_wave(wave_results: tuple[_MapResult, ...]) -> None:
            nonlocal new_observation, primary_failure, primary_failure_ref
            for mapped in wave_results:
                operation_id = fresh_operation_ids.get(id(mapped.action))
                if operation_id is None:
                    continue
                if mapped.contract_failure:
                    if run.action_interaction is not None:
                        await run.action_interaction.record_tool_result(tool_name=("search_places" if isinstance(mapped.action, ProposedSearchAction) else "get_place_details"),
                            ordinal=mapped.action.ordinal, outcome="failure", feedback='{"status":"failure","code":"dependency_contract_error"}')
                    if primary_failure is None:
                        primary_failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
                    continue
                try:
                    obs, failure = await self._persist_observation(run, request, slot, iteration_index, mapped.action, operation_id, mapped)
                except ArtifactRepositoryError:
                    if primary_failure is None or isinstance(primary_failure, MapResearchFailure):
                        primary_failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.ARTIFACT_ERROR)
                        primary_failure_ref = None
                    continue
                persisted_fresh.add(operation_id)
                already_in_state = obs in run.state.observation_refs
                if obs not in run.observation_refs:
                    run.observation_refs.append(obs)
                run.state = QualifiedCandidateSetStateSnapshot(**{**_python_model(run.state), "observation_refs": tuple(run.observation_refs)})
                run.fingerprints[action_fingerprint(mapped.action, run.destination)] = obs
                new_observation = new_observation or (not already_in_state and isinstance(mapped.outcome, (PlaceSearchSuccess, PlaceSearchNoResult, PlaceDetailsSuccess, PlaceDetailsNoResult)))
                if failure is not None and primary_failure is None:
                    primary_failure = failure
                    primary_failure_ref = obs
                if already_in_state:
                    continue
                if isinstance(mapped.action, ProposedDetailsAction) and isinstance(mapped.outcome, PlaceDetailsSuccess):
                    run.state = self._add_details(run.state, mapped.outcome, len(run.observation_refs) - 1)
                elif isinstance(mapped.action, ProposedDetailsAction) and isinstance(mapped.outcome, PlaceDetailsNoResult):
                    run.state = self._mark_details_no_result(run.state, mapped.action.uid, len(run.observation_refs) - 1)
                elif isinstance(mapped.action, ProposedSearchAction) and isinstance(mapped.outcome, PlaceSearchNoResult):
                    run.state = self._record_search_no_result(run, mapped.action, len(run.observation_refs) - 1)
                elif isinstance(mapped.action, ProposedSearchAction) and isinstance(mapped.outcome, PlaceSearchSuccess):
                    run.state = self._add_search(run, mapped.action, mapped.outcome, len(run.observation_refs) - 1)
                if isinstance(mapped.outcome, PlaceUnverifiedResultObservation) and primary_failure is None:
                    primary_failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.UNVERIFIED_RESULT)
                    primary_failure_ref = obs

        fresh_results = await self._map_wave(fresh_place_actions, run, on_wave=persist_wave) if fresh_place_actions else ()
        if run.action_interaction is not None:
            issued_actions = {id(item.action) for item in fresh_results}
            for action in fresh_place_actions:
                if id(action) not in issued_actions:
                    await run.action_interaction.record_tool_result(tool_name=("search_places" if isinstance(action, ProposedSearchAction) else "get_place_details"),
                        ordinal=action.ordinal, outcome="not_issued", feedback='{"status":"not_issued","reason":"previous_wave_failed"}')
        fresh_iter = iter(fresh_results)
        map_results_list: list[_MapResult] = []
        for action, operation_id in zip(place_actions, slot.place_observation_operation_ids):
            replayed = replayed_results.get(operation_id)
            if replayed is not None:
                map_results_list.append(replayed)
                continue
            try:
                map_results_list.append(next(fresh_iter))
            except StopIteration:
                map_results_list.append(_MapResult(action, None, None, True))
        map_results = tuple(map_results_list)
        for action, operation_id, mapped in zip(place_actions, slot.place_observation_operation_ids, map_results):
            if operation_id not in run.replayed_place_refs:
                if operation_id not in persisted_fresh and mapped.contract_failure and primary_failure is None:
                    primary_failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
                continue
            if mapped.contract_failure:
                if primary_failure is None:
                    primary_failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
                continue
            try:
                if operation_id in run.replayed_place_refs:
                    obs = run.replayed_place_refs[operation_id]
                    failure = None
                else:
                    obs, failure = await self._persist_observation(run, request, slot, iteration_index, action, operation_id, mapped)
            except ArtifactRepositoryError:
                if primary_failure is None or isinstance(primary_failure, MapResearchFailure):
                    primary_failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.ARTIFACT_ERROR)
                    primary_failure_ref = None
                continue
            already_in_state = obs in run.state.observation_refs
            if obs not in run.observation_refs:
                run.observation_refs.append(obs)
            run.state = QualifiedCandidateSetStateSnapshot(**{**_python_model(run.state), "observation_refs": tuple(run.observation_refs)})
            run.fingerprints[action_fingerprint(action, run.destination)] = obs
            new_observation = new_observation or (not already_in_state and isinstance(mapped.outcome, (PlaceSearchSuccess, PlaceSearchNoResult, PlaceDetailsSuccess, PlaceDetailsNoResult)))
            if failure is not None and primary_failure is None:
                primary_failure = failure
                primary_failure_ref = obs
            if run.action_interaction is not None and operation_id in run.replayed_place_refs:
                await run.action_interaction.record_tool_result(tool_name=("search_places" if isinstance(action, ProposedSearchAction) else "get_place_details"),
                    ordinal=action.ordinal, outcome="cached", feedback=json.dumps({"status": "cached", "observation_ref": obs.model_dump(mode="json")}), observation_ref_json=obs.model_dump_json())
            if already_in_state:
                continue
            if isinstance(action, ProposedDetailsAction) and isinstance(mapped.outcome, PlaceDetailsSuccess):
                run.state = self._add_details(run.state, mapped.outcome, len(run.observation_refs) - 1)
            elif isinstance(action, ProposedDetailsAction) and isinstance(mapped.outcome, PlaceDetailsNoResult):
                run.state = self._mark_details_no_result(run.state, action.uid, len(run.observation_refs) - 1)
            elif isinstance(action, ProposedSearchAction) and isinstance(mapped.outcome, PlaceSearchNoResult):
                run.state = self._record_search_no_result(run, action, len(run.observation_refs) - 1)
            elif isinstance(action, ProposedSearchAction) and isinstance(mapped.outcome, PlaceSearchSuccess):
                run.state = self._add_search(run, action, mapped.outcome, len(run.observation_refs) - 1)
            if isinstance(mapped.outcome, (PlaceUnverifiedResultObservation,)) and primary_failure is None:
                primary_failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.UNVERIFIED_RESULT)
                primary_failure_ref = obs
        if primary_failure is not None:
            # A partially successful wave is evidence-only: the aggregate
            # candidate state must not advance when any sibling is technical
            # failure or unverified.  Keep the new observation directory so
            # the failed terminal remains replayable.
            run.state = QualifiedCandidateSetStateSnapshot(**{**_python_model(pre_wave_state), "observation_refs": tuple(run.observation_refs)})
            failure_ref = primary_failure_ref
            return await self._failed(run, request, primary_failure, failure_ref)
        if new_observation:
            failure = await self._execute_judgment(run, request, slot, iteration_index, pre_wave_state)
            if failure is not None:
                return failure
        progressed = self._state_progressed(pre_iteration_state, run.state, objectives[0] if objectives else None, run.objective_history, run.resolved_progress)
        run.state = self._refresh_state(run.state, no_progress=0 if progressed else min(2, run.state.iteration_no_progress_streak + 1))
        if local_slot < len(request.operation_plan.iteration_slots) - 1:
            await self._write_in_progress(run, request, slot)
        return None

    @staticmethod
    def _correction_snapshot(run):
        from dataclasses import asdict
        return tuple(CorrectableActionCorrectionState(**asdict(item)) for item in run.corrections)

    @staticmethod
    def _start_step(run, iteration_index, kind):
        run.active_iteration = iteration_index
        run.action_attempt_refs = []
        run.judgment_attempt_refs = []
        run.call_info = {}
        run.correction_stop_reason = None
        run.action_validation_feedback = None
        run.active_step_kind = kind
        run.active_action_ref = None
        run.active_judgment_ref = None
        run.active_observation_start = len(run.observation_refs)

    @staticmethod
    def _qualification_snapshot(run):
        if run.active_iteration is not None:
            step = ResearchStep(step_kind=run.active_step_kind, iteration_index=run.active_iteration,
                action_ref=run.active_action_ref, judgment_ref=run.active_judgment_ref,
                observation_refs=tuple(run.observation_refs[run.active_observation_start:]))
            previous = {item.iteration_index: item for item in run.steps}
            prior = previous.get(step.iteration_index)
            if prior is not None:
                if step.step_kind != prior.step_kind:
                    raise ValueError("research step kind cannot change during replay")
                step = ResearchStep(**{**step.model_dump(mode="python"),
                    "action_ref": step.action_ref or prior.action_ref,
                    "judgment_ref": step.judgment_ref or prior.judgment_ref,
                    "observation_refs": tuple(dict.fromkeys((*prior.observation_refs, *step.observation_refs)))})
            previous[step.iteration_index] = step
            run.steps = tuple(previous[index] for index in sorted(previous))
        return QualificationState(assessments=run.assessments, steps=run.steps)

    @staticmethod
    def _pending_correction(run, objective):
        from .correction import target_fingerprint, evidence_fingerprint
        target, evidence = target_fingerprint(objective), evidence_fingerprint(run.state, objective, run.objective_history)
        return next((item for item in reversed(run.corrections)
                     if item.active and item.target == target and item.evidence == evidence), None)

    async def _reject_action(self, run, request, slot, iteration_index, local_slot,
                             objectives, output, metadata, rule, *, replay_ref=None):
        from .correction import record_rejection
        output = TypeAdapter(ActionModelOutcome).validate_json(output.model_dump_json(), strict=True)
        if replay_ref is None:
            run.corrections = record_rejection(run.corrections, run.state, objectives[0],
                getattr(output, "actions", ()), run.destination, rule, run.objective_history)
            record = ModelActionPolicyRejectedRecord(record_type="policy_rejected",
                output=self._action_output_snapshot(output), metadata=metadata, reason_code=rule)
            run.action_validation_feedback = self._action_error_feedback(run, slot, output, objectives, rule)
            await self._write_attempt(run, "action", record, recovery_code=rule)
            await self._record_action_feedback(run, record)
        stopped = correction_stop(used=run.output_recoveries_used, semantic_used=run.semantic_invocations, kind="action")
        if stopped is None:
            if replay_ref is not None:
                return await self._indeterminate(run.run_id)
            return None
        run.correction_stop_reason = stopped
        if replay_ref is None:
            ref = await self._write_model_action(run, request, slot, iteration_index, record)
            self._remember_action_ref(run, ref)
        run.state = self._state_with(run, no_progress=min(2, run.state.iteration_no_progress_streak + 1))
        return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")

    def _action_error_feedback(self, run, slot, output, objectives, rule):
        by_id = {item.objective_id: item for item in objectives}
        details = []
        actions = getattr(output, "actions", ())
        fingerprints = [action_fingerprint(item, run.destination) for item in actions]
        for action, fingerprint in zip(actions, fingerprints):
            objective = by_id[action.objective_id]
            error = self._validate_actions(run, (action.model_copy(update={"ordinal": 0}),), objectives, slot)
            if fingerprints.count(fingerprint) > 1:
                error = "duplicate"
            if action.ordinal == 0 and action.objective_id != objectives[0].objective_id:
                error = "highest_priority_objective_ignored"
            details.append({
                "ordinal": action.ordinal, "objective_id": action.objective_id,
                "error_code": error,
                "action": action.model_dump(mode="json"),
                "allowed_actions": CandidateResearchService._allowed_objective_actions(run, objective),
                "allowed_detail_uids": CandidateResearchService._objective_detail_uids(run, objective),
                "objective": objective.model_dump(mode="json"),
                "duplicate_in_batch": fingerprints.count(fingerprint) > 1,
            })
        return {"code": rule, "required_first_objective_id": objectives[0].objective_id,
                "invalid_action_ordinals": tuple(item["ordinal"] for item in details if item["error_code"]),
                "rejected_actions": details,
                "required": "Submit a complete corrected batch. Keep only actions allowed by their objectives; no action in this batch has been executed."}

    @staticmethod
    def _action_context(run: _Run, objectives: Sequence[ResearchObjective]) -> dict[str, object]:
        """Build the typed, normalized Action context without raw payloads."""
        requirements = []
        groups = {group.group_id: group for group in run.state.requirement_group_states}
        for requirement in project_requirements(run.requirement_specs, run.requirement_context):
            kind = requirement["requirement_kind"]
            group_id = requirement["requirement_group_id"]
            group = groups.get(group_id)
            unresolved = []
            if group is not None:
                for candidate in run.state.candidates:
                    if candidate.admission_ordinal not in group.evaluated_candidate_ordinals:
                        continue
                    matches = candidate.must_matches if kind == "must_visit" else candidate.avoid_matches
                    match = next((item for item in matches if item.requirement_group_id == group_id), None)
                    verdict = match.verdict if match is not None else group.default_verdict
                    if verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED}:
                        unresolved.append({"handle": candidate_handle(candidate.admission_ordinal), "uid": candidate.core.uid})
            requirements.append({
                **requirement,
                "unresolved_candidates": tuple(unresolved),
            })
        candidates = []
        for candidate in run.state.candidates:
            candidates.append({
                "handle": candidate_handle(candidate.admission_ordinal),
                "uid": candidate.core.uid,
                "name": candidate.core.name,
                "address": candidate.core.address,
                "coordinate": _json_model(candidate.core.coordinate),
                "provider_category": candidate.core.provider_category,
                "state": candidate.state.value,
                "roles": tuple({"role": role.role.value, "verdict": role.verdict.value} for role in candidate.roles),
                "interests": tuple(item.value for item in candidate.interests),
                "meal_intents": tuple(item.value for item in candidate.meal_intents),
                "details_status": candidate.details.status,
            })
        return {
            "action_validation_feedback": getattr(run, "action_validation_feedback", None),
            "correction": (CandidateResearchService._pending_correction(run, objectives[0]).project(objectives[0], CandidateResearchService._allowed_objective_actions(run, objectives[0]))
                if objectives and CandidateResearchService._pending_correction(run, objectives[0]) is not None else None),
            "required_objective_id": objectives[0].objective_id if objectives else None,
            "destination": run.destination,
            "effective_interests": tuple(item.value for item in run.effective_interests),
            "interests_locked": run.interests_frozen,
            "agent_may_select_interests": run.agent_decides_interests,
            "objectives": tuple({**_json_model(item),
                "allowed_actions": CandidateResearchService._allowed_objective_actions(run, item),
                "detail_uids": CandidateResearchService._objective_detail_uids(run, item)} for item in objectives),
            "requirements": tuple(requirements),
            "candidates": tuple(candidates),
        }

    @staticmethod
    def _state_progressed(before: CandidateSetStateSnapshot, after: CandidateSetStateSnapshot, objective=None, history=None, resolved_progress=None) -> bool:
        if objective is not None:
            from .correction import evidence_fingerprint, resolution_keys
            # Relevant new facts can justify another action; unrelated pool growth
            # and alternating conclusions over unchanged facts cannot.
            seen = resolved_progress if resolved_progress is not None else set()
            seen.update(resolution_keys(before, objective))
            newly_resolved = resolution_keys(after, objective) - seen
            seen.update(newly_resolved)
            return bool(newly_resolved) or evidence_fingerprint(before, objective, history) != evidence_fingerprint(after, objective, history)
        before_eligible = {item.core.uid for item in before.candidates if item.state is CandidateState.ELIGIBLE}
        after_eligible = {item.core.uid for item in after.candidates if item.state is CandidateState.ELIGIBLE}
        return bool(
            after_eligible - before_eligible
            or after.coverage.details_success_count > before.coverage.details_success_count
            or len(after.coverage.covered_interests) > len(before.coverage.covered_interests)
            or len(after.coverage.covered_meals) > len(before.coverage.covered_meals)
            or after.coverage.resolved_must_item_count > before.coverage.resolved_must_item_count
            or sum(item.state is CandidateState.CONFLICTED for item in after.candidates) < sum(item.state is CandidateState.CONFLICTED for item in before.candidates)
            or CandidateResearchService._requirement_verdicts(before) != CandidateResearchService._requirement_verdicts(after)
        )

    @staticmethod
    def _requirement_verdicts(state: CandidateSetStateSnapshot) -> tuple[tuple[str, tuple[tuple[int, str], ...]], ...]:
        """Return only semantic must/avoid verdict state for progress checks."""
        groups: list[tuple[str, tuple[tuple[int, str], ...]]] = []
        for group in state.requirement_group_states:
            overrides = {item.candidate_ordinal: item.verdict.value for item in group.exceptions}
            groups.append(
                (
                    group.group_id,
                    tuple(
                        (ordinal, overrides.get(ordinal, group.default_verdict.value))
                        for ordinal in group.evaluated_candidate_ordinals
                    ),
                )
            )
        return tuple(groups)

    @staticmethod
    def _freeze_interests(run: _Run, output: ProposedActionBatch | NoUsefulActionProposed) -> None:
        selected = getattr(output, "effective_interests", None)
        if run.interests_frozen:
            if selected is not None:
                raise ValueError("effective interests may only be selected by the first initial action")
            return
        if not run.agent_decides_interests:
            raise ValueError("effective interests are not selectable")
        effective = tuple(InterestTag) if selected is None else tuple(selected)
        if not 2 <= len(effective) <= 4 or len(set(effective)) != len(effective) or effective != tuple(sorted(effective, key=lambda item: list(InterestTag).index(item))):
            raise ValueError("effective interests must be unique and enum ordered")
        run.effective_interests = effective
        coverage = ArtifactResearchCoverageSummarySnapshot(
            **{
                **_python_model(run.state.coverage),
                "effective_interests": effective,
                "covered_interests": tuple(item for item in run.state.coverage.covered_interests if item in effective),
            }
        )
        run.state = QualifiedCandidateSetStateSnapshot(**{**_python_model(run.state), "effective_interests": effective, "coverage": coverage})
        run.interests_frozen = True

    async def _no_useful_action_is_legal(
        self,
        run: _Run,
        output: NoUsefulActionProposed,
        objectives: Sequence[ResearchObjective],
    ) -> bool:
        if output.reason_code == "prior_no_progress":
            return run.state.iteration_no_progress_streak >= 1
        if output.reason_code == "insufficient_remaining_budget":
            remaining = await self._place_remaining(run)
            return remaining == 0
        if output.reason_code == "no_legal_action":
            return bool(objectives) and not any(self._allowed_objective_actions(run, item) for item in objectives)
        return False

    @staticmethod
    def _objective_detail_uids(run, objective):
        blocked = {item.core.uid for item in run.state.candidates
            if item.state not in {CandidateState.EXCLUDED, CandidateState.CONFLICTED}
            and not candidate_admitted_purposes(item) and any(match.requirement_group_id == objective.requirement_group_id
                and match.verdict is MatchVerdict.CONFIRMED for match in item.must_matches)}
        if (objective.requirement_group_id is not None
                and objective.requirement_group_id not in run.named_searches and not blocked):
            return ()
        if objective.kind in {ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY}:
            return ()
        candidates = tuple(item for item in run.state.candidates
            if (not objective.candidate_uids or item.core.uid in objective.candidate_uids)
            and item.details.status == "not_requested")
        if objective.requirement_group_id:
            candidates = tuple(item for item in candidates if any(
                match.requirement_group_id == objective.requirement_group_id
                and (match.verdict in {MatchVerdict.UNRESOLVED, MatchVerdict.AMBIGUOUS}
                     or item.core.uid in blocked)
                for match in (*item.must_matches, *item.avoid_matches)))
        if objective.kind is ResearchObjectiveKind.DETAILS_MATURITY:
            candidates = tuple(item for item in candidates if item.state is CandidateState.ELIGIBLE)
        return tuple(item.core.uid for item in candidates)

    @staticmethod
    def _allowed_objective_actions(run, objective):
        detail_only = objective.kind is ResearchObjectiveKind.DETAILS_MATURITY or objective.gap_type == "candidate_details_missing"
        if objective.requirement_group_id and any(item.details.status == "not_requested"
                and item.state not in {CandidateState.EXCLUDED, CandidateState.CONFLICTED}
                and not candidate_admitted_purposes(item) and any(match.requirement_group_id == objective.requirement_group_id
                    and match.verdict is MatchVerdict.CONFIRMED for match in item.must_matches)
                for item in run.state.candidates):
            detail_only = True
        allowed = ["get_place_details"] if CandidateResearchService._objective_detail_uids(run, objective) else []
        anchor_valid = (getattr(objective.anchor, "anchor_type", None) != "candidate"
            or any(item.core.uid == objective.anchor.uid for item in run.state.candidates))
        if not detail_only and anchor_valid:
            allowed.insert(0, "search_places")
        return tuple(allowed)

    async def _consume_iteration(self, run: _Run, operation_id: OperationId) -> int | None:
        try:
            result = await self._budget.consume(BudgetConsumeRequest(operation_id=operation_id, key=self._iteration_key(), amount=1))
        except ExecutionBudgetError:
            return -1
        if isinstance(result, BudgetExceeded):
            if result.operation_id != operation_id or result.key != self._iteration_key() or result.amount != 1:
                return -3
            if not self._valid_budget_snapshot(result.snapshot, run.run_id, self._iteration_key()):
                return -4
            return None
        if not isinstance(result, BudgetConsumed):
            return -4
        if result.operation_id != operation_id or result.key != self._iteration_key() or result.amount != 1:
            return -3
        if not self._valid_budget_snapshot(result.snapshot, run.run_id, self._iteration_key()):
            return -4
        if result.disposition == "replayed":
            return -2
        return result.snapshot.consumed - 1

    async def _replay_action(
        self,
        run: _Run,
        operation_id: OperationId,
    ) -> tuple[int, tuple[object, ArtifactInvocationMetadataSnapshot] | LLMResearchFailure | ControlResearchFailure, ArtifactRef] | None:
        lookup = await self._repository.lookup(operation_id=operation_id)
        if isinstance(lookup, ArtifactAbsent):
            return None
        if not isinstance(lookup, ArtifactFound):
            raise RuntimeError("action artifact lookup contract violation")
        ref = lookup.ref
        if ref.artifact_type != "candidate_research.model_action" or ref.schema_version != "3.0" or ref.operation_id != operation_id or ref.operation_id.run_id != run.run_id:
            return None
        try:
            read = await self._repository.get(ref, expected_model=CandidateResearchModelActionArtifact)
        except ArtifactRepositoryError:
            return None
        artifact = read.content
        if (
            read.ref != ref
            or artifact.run_id != run.run_id
            or artifact.mode is not run.mode
            or artifact.input_digest != run.request_digest
            or _digest(_json_model(artifact)) != ref.content_hash
        ):
            return None
        record = unwrap_model_record(artifact.result)
        await self._restore_attempt_chain(run, artifact, "action")
        if isinstance(record, ModelActionLLMFailureRecord):
            error = self._public_llm_error(record.error)
            return artifact.iteration_index, LLMResearchFailure(invocation_kind=ResearchInvocationKind.ACTION, error=error), ref
        if isinstance(record, ModelActionControllerRejectedRecord):
            return artifact.iteration_index, ControlResearchFailure(code=CandidateResearchControlFailureCode.ACTION_BATCH_INVALID), ref
        if isinstance(record, ModelActionPolicyRejectedRecord):
            return artifact.iteration_index, (_PolicyReplay(record.output, record.reason_code), record.metadata), ref
        try:
            output = TypeAdapter(ActionModelOutcome).validate_json(
                json.dumps(_python_model(record.output), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                strict=True,
            )
        except (ValidationError, TypeError, ValueError):
            return None
        return artifact.iteration_index, (output, record.metadata), ref

    async def _place_remaining(self, run: _Run) -> int | None:
        try:
            snapshot = await self._budget.get_snapshot(key=self._place_key())
        except ExecutionBudgetError:
            return -1
        except (AttributeError, TypeError, ValueError):
            return -2
        if not self._valid_budget_snapshot(snapshot, run.run_id, self._place_key()):
            return -2
        return snapshot.remaining

    async def _reserve_places(self, run: _Run, slot: ResearchIterationOperationIds, actions: Sequence[ProposedAction]) -> tuple[int, int]:
        applied = 0
        for operation_id in slot.place_observation_operation_ids[: len(actions)]:
            existing = await self._repository.lookup(operation_id=operation_id)
            if not isinstance(existing, (ArtifactAbsent, ArtifactFound)):
                return -1, applied
            existing_ref = None
            if isinstance(existing, ArtifactFound):
                existing_ref = existing.ref
                if (
                    existing_ref.artifact_type != "candidate_research.place_observation"
                    or existing_ref.schema_version != "1.0"
                    or existing_ref.operation_id != operation_id
                    or existing_ref.operation_id.run_id != run.run_id
                ):
                    return -2, applied
                try:
                    existing_read = await self._repository.get(existing_ref, expected_model=CandidateResearchPlaceObservationArtifact)
                except ArtifactRepositoryError:
                    return -2, applied
                if existing_read.ref != existing_ref or _digest(_json_model(existing_read.content)) != existing_ref.content_hash:
                    return -2, applied
            try:
                result = await self._budget.consume(BudgetConsumeRequest(operation_id=operation_id, key=self._place_key(), amount=1))
            except asyncio.CancelledError:
                raise
            except ExecutionBudgetError:
                return -3, applied
            if isinstance(result, BudgetExceeded):
                if result.operation_id != operation_id or result.key != self._place_key() or result.amount != 1:
                    return -1, applied
                if not self._valid_budget_snapshot(result.snapshot, run.run_id, self._place_key()):
                    return -1, applied
                return 0, applied
            if not isinstance(result, BudgetConsumed):
                return -1, applied
            if result.operation_id != operation_id or result.key != self._place_key() or result.amount != 1:
                return -1, applied
            if not self._valid_budget_snapshot(result.snapshot, run.run_id, self._place_key()):
                return -1, applied
            if result.disposition == "replayed":
                if existing_ref is None:
                    lookup = await self._repository.lookup(operation_id=operation_id)
                else:
                    lookup = ArtifactFound(ref=existing_ref)
                if not isinstance(lookup, ArtifactFound):
                    return -2, applied
                ref = lookup.ref
                if ref.artifact_type != "candidate_research.place_observation" or ref.schema_version != "1.0" or ref.operation_id != operation_id or ref.operation_id.run_id != slot.action_operation_id.run_id:
                    return -2, applied
                try:
                    await self._repository.get(ref, expected_model=CandidateResearchPlaceObservationArtifact)
                except ArtifactRepositoryError:
                    return -2, applied
                run.replayed_place_refs[operation_id] = ref
                applied += 1
                continue
            if existing_ref is not None:
                return -2, applied
            applied += 1
        return 1, applied

    @staticmethod
    def _reserve_output_recovery(run, failure, *, judgment=False):
        code = None
        if (isinstance(failure, LLMResearchFailure) and not failure.error.possible_duplicate
                and failure.error.code.value in OUTPUT_CODES):
            code = failure.error.code.value
        elif (judgment and run.judgment_recoverable and isinstance(failure, ControlResearchFailure)
                and failure.code in {CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID,
                                     CandidateResearchControlFailureCode.JUDGMENT_REQUIRED_MISSING}
                and run.last_judgment_metadata is not None and not run.last_judgment_metadata.possible_duplicate):
            code = run.judgment_recovery_code
        if code is None:
            return None
        stopped = correction_stop(used=run.output_recoveries_used, semantic_used=run.semantic_invocations,
                                  kind="judgment" if judgment else "action")
        if stopped is not None:
            run.correction_stop_reason = stopped
            return None
        return CorrectionFeedback(code=code, recovery_number=run.output_recoveries_used + 1)

    @staticmethod
    def _account_replayed_failure(run, record):
        if (isinstance(record, (ModelActionLLMFailureRecord, ModelJudgmentLLMFailureRecord))
                and CandidateResearchService._semantic_cost(record.error.code)):
            # The final failure has no public metadata, just like earlier
            # failed attempts. Replaying it cannot make unknown costs complete.
            run.provider_attempts_complete = False
            run.usage_complete = False

    async def _invoke_action(self, run: _Run, prompt: PromptEnvelope):
        from .correction import recovery_prompt
        original = prompt
        while True:
            result = await self._invoke_action_once(run, prompt)
            if not isinstance(result, LLMResearchFailure):
                return result
            code = result.error.code.value if not result.error.possible_duplicate and result.error.code.value in OUTPUT_CODES else None
            record = ModelActionLLMFailureRecord(record_type="llm_failure", error=ArtifactLLMErrorSnapshot(**_python_model(result.error)))
            await self._write_attempt(run, "action", record, recovery_code=code)
            feedback = self._reserve_output_recovery(run, result)
            if feedback is None:
                return result
            prompt = recovery_prompt(original, code=feedback.code, recovery_number=feedback.recovery_number)

    async def _call_model(self, run, kind, prompt, schema):
        slot = run.active_slot
        if slot is None:
            raise RuntimeError("model invocation has no active research slot")
        refs = run.action_attempt_refs if kind == "action" else run.judgment_attempt_refs
        ids = slot.action_attempt_operation_ids if kind == "action" else slot.judgment_attempt_operation_ids
        if len(refs) >= len(ids):
            raise RuntimeError("invocation attempt plan exhausted")
        if refs and correction_stop(used=run.output_recoveries_used,
                semantic_used=run.semantic_invocations, kind=kind) is not None:
            raise _OperationReplayIndeterminate()
        if run.semantic_invocations >= 12:
            raise _OperationReplayIndeterminate()
        prior = await self._repository.lookup(operation_id=ids[len(refs)])
        if not isinstance(prior, ArtifactAbsent):
            run.indeterminate = True
            raise _OperationReplayIndeterminate()
        correction = bool(refs)
        info = dict(semantic_index=run.semantic_invocations, semantic_cost=None,
                    is_correction=correction, correction_number=run.output_recoveries_used + 1 if correction else None,
                    attempt_index=len(refs), trigger_ref=refs[-1] if refs else None,
                    persisted=None, feedback_recorded=False)
        run.call_info[kind] = info
        before = getattr(run.operation, "semantic_invocations_consumed", None)
        result = None
        cancelled = False
        try:
            with interaction_call("research_" + kind) as identity:
                result = await run.operation.ainvoke_structured(prompt, schema)
            return result, identity
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            after = getattr(run.operation, "semantic_invocations_consumed", None)
            counter_conflict = type(before) is int and type(after) is int and after - before not in (0, 1)
            if type(before) is int and type(after) is int:
                cost = None if counter_conflict else after - before
            elif isinstance(result, LLMInvocationFailure):
                cost = self._semantic_cost(result.error.code)
            elif isinstance(result, StructuredInvocationSuccess):
                cost = 1
            else:
                cost = None
            expected_cost = (1 if isinstance(result, StructuredInvocationSuccess)
                             else self._semantic_cost(result.error.code) if isinstance(result, LLMInvocationFailure) else None)
            counter_conflict = counter_conflict or expected_cost is not None and cost != expected_cost
            if counter_conflict:
                cost = None
            info["semantic_cost"] = cost
            if cost is not None:
                run.semantic_invocations += cost
            if correction and cost == 1:
                run.output_recoveries_used += 1
            if cost is None:
                run.provider_attempts_complete = run.usage_complete = False
            elif cost and not isinstance(result, StructuredInvocationSuccess):
                run.provider_attempts_complete = run.usage_complete = False
            if isinstance(result, StructuredInvocationSuccess) and cost == 1:
                info["metadata"] = self._metadata(result.metadata)
            if not cancelled and counter_conflict:
                raise _OperationReplayIndeterminate()

    @staticmethod
    def _model_contract_failure(run, kind):
        info = run.call_info.get(kind)
        if info is not None and info["semantic_cost"] != 0 and info.get("metadata") is None:
            run.provider_attempts_complete = run.usage_complete = False
        uncertain = info is not None and info["semantic_cost"] is None
        code = (CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE if uncertain
                else CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
        return ControlResearchFailure(code=code)

    async def _write_attempt(self, run, kind, record=None, *, recovery_code=None, control=None):
        info = run.call_info.get(kind)
        if info is None:
            return None
        slot = run.active_slot
        refs = run.action_attempt_refs if kind == "action" else run.judgment_attempt_refs
        ids = slot.action_attempt_operation_ids if kind == "action" else slot.judgment_attempt_operation_ids
        if info["semantic_cost"] is None:
            result = AttemptControlFailure(result_type="indeterminate",
                code=CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE, metadata=info.get("metadata"))
            recovery_code = None
        elif control is not None:
            result = AttemptControlFailure(result_type="control_failure", code=control.code, metadata=info.get("metadata"))
        elif kind == "action":
            result = AttemptActionResult(result_type="action_result", record=record)
        else:
            result = AttemptJudgmentResult(result_type="judgment_result", record=record)
        if info["persisted"] is not None:
            if info["saved_result"] != result:
                raise _OperationReplayIndeterminate()
            return info["persisted"]
        artifact = CandidateResearchInvocationAttemptArtifact(
            schema_version="1.0", run_id=run.run_id, mode=run.mode, kind=kind,
            main_operation_id=slot.action_operation_id if kind == "action" else slot.judgment_operation_id,
            invocation_slot_index=slot.invocation_slot_index, iteration_index=run.active_iteration,
            attempt_index=info["attempt_index"], semantic_index=info["semantic_index"],
            semantic_cost=info["semantic_cost"], is_correction=info["is_correction"],
            correction_number=info["correction_number"], trigger_ref=info["trigger_ref"],
            recovery_code=recovery_code, result=result)
        ref = await self._put(run, ids[info["attempt_index"]], "candidate_research.invocation_attempt", artifact)
        refs.append(ref)
        run.invocation_attempt_refs.append(ref)
        info["persisted"] = ref
        info["saved_result"] = result
        return ref

    async def _write_control_attempt(self, run, kind, failure):
        return await self._write_attempt(run, kind, control=failure)

    async def _restore_attempt_chain(self, run, artifact, kind):
        slot = run.active_slot
        ids = slot.action_attempt_operation_ids if kind == "action" else slot.judgment_attempt_operation_ids
        main_id = slot.action_operation_id if kind == "action" else slot.judgment_operation_id
        restored, previous = [], None
        for index, ref in enumerate(artifact.attempt_refs):
            saved = await self._repository.get(ref, expected_model=CandidateResearchInvocationAttemptArtifact)
            attempt = CandidateResearchInvocationAttemptArtifact.model_validate_json(saved.content.model_dump_json(), strict=True)
            if (saved.ref != ref or content_digest(attempt) != ref.content_hash
                    or len(canonical_json_bytes(attempt)) != ref.size_bytes or ref.operation_id != ids[index]
                    or attempt.main_operation_id != main_id or attempt.run_id != run.run_id
                    or attempt.mode != artifact.mode or attempt.kind != kind
                    or attempt.iteration_index != artifact.iteration_index
                    or attempt.invocation_slot_index != artifact.invocation_slot_index
                    or attempt.attempt_index != index or attempt.trigger_ref != (restored[-1] if restored else None)
                    or previous is not None and previous.recovery_code is None):
                raise _OperationReplayIndeterminate()
            restored.append(ref)
            previous = attempt
            if ref in run.invocation_attempt_refs:
                continue
            if attempt.semantic_cost is None or attempt.semantic_index != run.semantic_invocations:
                raise _OperationReplayIndeterminate()
            if attempt.is_correction and attempt.correction_number != run.output_recoveries_used + 1:
                raise _OperationReplayIndeterminate()
            run.invocation_attempt_refs.append(ref)
            record = getattr(attempt.result, "record", None)
            if attempt.semantic_cost == 1:
                self._account_replayed_metadata(run, attempt.semantic_index, getattr(record, "metadata", None))
                if attempt.is_correction:
                    run.output_recoveries_used += 1
                if record is not None:
                    self._account_replayed_failure(run, record)
        if (previous is None or previous.result.result_type != kind + "_result"
                or previous.result.record != artifact.result
                or artifact.invocation_index != max(0, previous.semantic_index + previous.semantic_cost - 1)):
            raise _OperationReplayIndeterminate()
        if kind == "action":
            run.action_attempt_refs = restored
        else:
            run.judgment_attempt_refs = restored
        if previous.recovery_code is not None:
            stopped = correction_stop(used=run.output_recoveries_used,
                semantic_used=run.semantic_invocations, kind=kind)
            if stopped is None:
                raise _OperationReplayIndeterminate()
            run.correction_stop_reason = stopped
        return previous

    @staticmethod
    def _judgment_failure_record(run, failure):
        if isinstance(failure, LLMResearchFailure):
            return ModelJudgmentLLMFailureRecord(record_type="llm_failure", error=ArtifactLLMErrorSnapshot(**_python_model(failure.error)))
        if (run.last_judgment_patch is not None and run.last_judgment_metadata is not None
                and failure.code in {CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID,
                                     CandidateResearchControlFailureCode.JUDGMENT_REQUIRED_MISSING,
                                     CandidateResearchControlFailureCode.STATE_INVARIANT_FAILED}):
            return ModelJudgmentControllerRejectedRecord(record_type="controller_rejected",
                proposed_patch=JudgmentPatchModelSnapshot.model_validate(_python_model(run.last_judgment_patch), strict=True),
                metadata=run.last_judgment_metadata, code=failure.code.value)
        return None

    async def _invoke_action_once(self, run: _Run, prompt: PromptEnvelope) -> tuple[object, ArtifactInvocationMetadataSnapshot] | LLMResearchFailure | ControlResearchFailure:
        admitted_before = getattr(run.operation, "semantic_invocations_consumed", None)
        try:
            from .correction import bound_action_schema
            highest = next(iter(run.objective_directory.values()), None)
            result, identity = await self._call_model(run, "action", prompt, bound_action_schema(_ActionModelEnvelope, highest,
                self._allowed_objective_actions(run, highest) if highest else (),
                self._objective_detail_uids(run, highest) if highest else ()))
            run.action_interaction = identity
        except _OperationReplayIndeterminate:
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE)
        except (RuntimeError, TypeError, ValueError, AttributeError):
            return self._model_contract_failure(run, "action")
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            return self._model_contract_failure(run, "action")
        if isinstance(result, LLMInvocationFailure):
            cost = self._semantic_cost(result.error.code)
            if cost:
                run.provider_attempts_complete = False
                run.usage_complete = False
            return LLMResearchFailure(invocation_kind=ResearchInvocationKind.ACTION, error=result.error)
        if not isinstance(result, StructuredInvocationSuccess):
            return self._model_contract_failure(run, "action")
        metadata = self._metadata(result.metadata)
        run.provider_attempts += result.metadata.provider_attempts
        self._add_usage(run, result.metadata)
        try:
            raw_output = (
                result.value.output
                if isinstance(result.value, _ActionModelEnvelope)
                else result.value
            )
            fresh = TypeAdapter(ActionModelOutcome).validate_json(json.dumps(_json_model(raw_output), ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
        except (ValidationError, TypeError, ValueError):
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
        if isinstance(fresh, ProposedActionBatch):
            output = fresh
        else:
            output = fresh
        return output, metadata

    @staticmethod
    def _semantic_cost(code: LLMErrorCode) -> int:
        if code in {LLMErrorCode.SCHEMA_UNSUPPORTED, LLMErrorCode.INPUT_TOO_LARGE, LLMErrorCode.QUOTA_EXHAUSTED}:
            return 0
        return 1

    @staticmethod
    def _metadata(value: InvocationMetadata) -> ArtifactInvocationMetadataSnapshot:
        return ArtifactInvocationMetadataSnapshot(**_python_model(value))

    @staticmethod
    def _add_usage(run: _Run, metadata: InvocationMetadata) -> None:
        usage = metadata.usage
        for name in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            run.usage[name] = run.usage.get(name, 0) + int(getattr(usage, name))

    @staticmethod
    def _action_snapshot(value: ProposedActionBatch) -> ProposedActionBatchSnapshot:
        actions = []
        for action in value.actions:
            if isinstance(action, ProposedSearchAction):
                actions.append(ProposedSearchActionSnapshot(
                    action_type="search_places", ordinal=action.ordinal, objective_id=action.objective_id,
                    query=action.query, tag=action.tag, area_mode=action.area_mode,
                    anchor=None if action.anchor is None else CandidateResearchService._anchor_snapshot(action.anchor),
                    radius_km=action.radius_km, interest=action.interest, meal_intent=action.meal_intent,
                ))
            else:
                actions.append(ProposedDetailsActionSnapshot(action_type="get_place_details", ordinal=action.ordinal, objective_id=action.objective_id, uid=action.uid))
        return ProposedActionBatchSnapshot(outcome_type="proposed_actions", effective_interests=value.effective_interests, actions=tuple(actions))

    @staticmethod
    def _action_output_snapshot(value: ProposedActionBatch | NoUsefulActionProposed) -> ProposedActionBatchSnapshot | NoUsefulActionProposedSnapshot:
        if isinstance(value, ProposedActionBatch):
            return CandidateResearchService._action_snapshot(value)
        return NoUsefulActionProposedSnapshot(outcome_type="no_useful_action", reason_code=value.reason_code)

    def _validate_actions(self, run: _Run, actions: Sequence[ProposedAction], objectives: Sequence[ResearchObjective], slot: ResearchIterationOperationIds) -> str | None:
        by_id = {item.objective_id: item for item in objectives}
        if len(actions) > 8 or len(actions) > len(slot.place_observation_operation_ids):
            return "capacity"
        known_uids = {item.core.uid for item in run.state.candidates}
        if tuple(actions[index].ordinal for index in range(len(actions))) != tuple(range(len(actions))):
            return "ordinal"
        for action in actions:
            if action.objective_id not in by_id:
                return "objective"
            if isinstance(action, ProposedDetailsAction) and action.uid not in known_uids:
                return "uid"
            if (isinstance(action, ProposedSearchAction) and getattr(action.anchor, "anchor_type", None) == "candidate"
                    and action.anchor.uid not in known_uids):
                return "anchor"
        fingerprints = tuple(action_fingerprint(action, run.destination) for action in actions)
        if len(set(fingerprints)) != len(fingerprints):
            return "duplicate"
        for action in actions:
            objective = by_id.get(action.objective_id)
            if objective is None:
                return "objective"
            if action.action_type not in self._allowed_objective_actions(run, objective):
                return "objective_parameters"
            if isinstance(action, ProposedSearchAction):
                if action.area_mode is SearchAreaMode.CITY:
                    if action.anchor is not None or action.radius_km is not None:
                        return "area"
                elif action.anchor is None or action.radius_km is None:
                    return "area"
                if action.area_mode is SearchAreaMode.NEARBY and getattr(action.anchor, "anchor_type", None) == "candidate" and action.anchor.uid not in known_uids:
                    return "anchor"
                if objective.kind is ResearchObjectiveKind.INITIAL_CITY_ATTRACTION and action.area_mode is not SearchAreaMode.CITY:
                    return "objective_parameters"
                if objective.kind is ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY and (action.area_mode is not SearchAreaMode.NEARBY or getattr(action.anchor, "anchor_type", None) != "hotel"):
                    return "objective_parameters"
                if objective.kind is ResearchObjectiveKind.SUPPLEMENTAL_GAP and objective.anchor is not None:
                    if action.area_mode is not SearchAreaMode.NEARBY or action.radius_km != objective.radius_km or getattr(action.anchor, "anchor_type", None) != getattr(objective.anchor, "anchor_type", None) or (getattr(action.anchor, "uid", None) != getattr(objective.anchor, "uid", None)):
                        return "objective_parameters"
                if objective.interest is not None and action.interest is not objective.interest:
                    return "objective_parameters"
                if objective.meal_intent is not None and action.meal_intent is not objective.meal_intent:
                    return "objective_parameters"
                if action.interest is not None and objective.interest is None:
                    return "objective_parameters"
                if action.meal_intent is not None and objective.meal_intent is None:
                    return "objective_parameters"
            else:
                if action.uid not in known_uids:
                    return "uid"
                if action.uid not in self._objective_detail_uids(run, objective):
                    return "objective_parameters"
                candidate = next(item for item in run.state.candidates if item.core.uid == action.uid)
                if candidate.details.status != "not_requested":
                    return "details_already_requested"
        return None

    async def _map_wave(self, actions: Sequence[ProposedAction], run: _Run, on_wave=None) -> tuple[_MapResult, ...]:
        results: list[_MapResult] = []
        for start in range(0, len(actions), 3):
            wave = actions[start : start + 3]
            tasks = [asyncio.create_task(self._map_one(action, run)) for action in wave]
            values: dict[int, _MapResult] = {}
            pending: set[asyncio.Task[object]] = set(tasks)
            task_to_index = {task: index for index, task in enumerate(tasks)}
            try:
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        index = task_to_index[task]
                        action = wave[index]
                        try:
                            value = task.result()
                        except asyncio.CancelledError:
                            for sibling in pending:
                                sibling.cancel()
                            await asyncio.gather(*pending, return_exceptions=True)
                            raise
                        except BaiduMapError as error:
                            values[index] = _MapResult(action, None, error)
                            continue
                        except BaseException:
                            values[index] = _MapResult(action, None, None, True)
                            continue
                        if isinstance(value, asyncio.CancelledError):
                            for sibling in pending:
                                sibling.cancel()
                            await asyncio.gather(*pending, return_exceptions=True)
                            raise value
                        if isinstance(value, _MapResult):
                            values[index] = value
                        else:
                            values[index] = _MapResult(action, None, None, True)
            except asyncio.CancelledError:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                raise
            ordered_wave = tuple(values[index] for index in range(len(wave)))
            results.extend(ordered_wave)
            if on_wave is not None:
                await on_wave(ordered_wave)
            if any(value.error is not None or value.contract_failure for value in ordered_wave):
                break
        return tuple(results)

    async def _map_one(self, action: ProposedAction, run: _Run) -> _MapResult:
        try:
            if isinstance(action, ProposedSearchAction):
                if action.area_mode is SearchAreaMode.CITY:
                    area = CitySearchArea(region=run.destination)
                else:
                    anchor = action.anchor
                    if getattr(anchor, "anchor_type", None) == "hotel":
                        hotel = run.fingerprint_trees.get("hotel")
                        area = NearbySearchArea(center=BaiduCoordinate(latitude=hotel.coordinate.latitude, longitude=hotel.coordinate.longitude), radius_m=action.radius_km * 1000)
                    else:
                        candidate = next(item for item in run.state.candidates if item.core.uid == anchor.uid)
                        area = NearbySearchArea(center=BaiduCoordinate(latitude=candidate.core.coordinate.latitude, longitude=candidate.core.coordinate.longitude), radius_m=action.radius_km * 1000)
                outcome = await self._map_adapter.search_places(PlaceSearchRequest(query=action.query, tag=action.tag, area=area, result_limit=50))
            else:
                outcome = await self._map_adapter.get_place_details(PlaceDetailsRequest(uid=action.uid))
            if not isinstance(outcome, (PlaceSearchSuccess, PlaceSearchNoResult, PlaceDetailsSuccess, PlaceDetailsNoResult)):
                return _MapResult(action, None, None, True)
            return _MapResult(action, outcome, None)
        except asyncio.CancelledError:
            raise
        except BaiduMapError as error:
            expected = MapCapability.PLACE_SEARCH if isinstance(action, ProposedSearchAction) else MapCapability.PLACE_DETAILS
            if error.capability is not None and error.capability is not expected:
                return _MapResult(action, None, None, True)
            return _MapResult(action, None, error)
        except (RuntimeError, TypeError, ValueError):
            return _MapResult(action, None, None, True)

    async def _replay_observation(
        self,
        run: _Run,
        operation_id: OperationId,
        action: ProposedAction,
        ref: ArtifactRef,
    ) -> _MapResult | None:
        try:
            read = await self._repository.get(ref, expected_model=CandidateResearchPlaceObservationArtifact)
        except ArtifactRepositoryError:
            return None
        artifact = read.content
        expected_tool = ResearchTool.SEARCH_PLACES if isinstance(action, ProposedSearchAction) else ResearchTool.GET_PLACE_DETAILS
        if (
            read.ref != ref
            or artifact.run_id != run.run_id
            or artifact.mode is not run.mode
            or artifact.action_ordinal != action.ordinal
            or artifact.tool is not expected_tool
            or artifact.action_fingerprint != action_fingerprint(action, run.destination)
            or _digest(_json_model(artifact)) != ref.content_hash
        ):
            return None
        result = artifact.result
        provenance = None
        if isinstance(result, (PlaceSearchSuccessObservation, PlaceSearchNoResultObservation)):
            provenance = MapProvenance(
                capability=MapCapability.PLACE_SEARCH,
                retrieved_at=result.provenance.retrieved_at,
                verified=True,
            )
            if isinstance(result, PlaceSearchNoResultObservation):
                outcome: object = PlaceSearchNoResult(provenance=provenance)
            else:
                places = tuple(
                    PlaceSummary(
                        uid=item.uid,
                        name=item.name,
                        address=item.address,
                        coordinate=BaiduCoordinate(latitude=item.coordinate.latitude, longitude=item.coordinate.longitude),
                        category=item.provider_category,
                    )
                    for item in result.places
                )
                outcome = PlaceSearchSuccess(places=places, provenance=provenance)
            return _MapResult(action, outcome, None)
        if isinstance(result, (PlaceDetailsSuccessObservation, PlaceDetailsNoResultObservation)):
            provenance = MapProvenance(
                capability=MapCapability.PLACE_DETAILS,
                retrieved_at=result.provenance.retrieved_at,
                verified=True,
            )
            if isinstance(result, PlaceDetailsNoResultObservation):
                outcome = PlaceDetailsNoResult(provenance=provenance)
            else:
                details = result.details
                outcome = PlaceDetailsSuccess(
                    details=PlaceDetails(
                        uid=details.uid,
                        name=details.name,
                        address=details.address,
                        coordinate=BaiduCoordinate(latitude=details.coordinate.latitude, longitude=details.coordinate.longitude),
                        category=details.provider_category,
                        brand=details.brand,
                        telephone=details.telephone,
                        rating=details.rating,
                        business_hours=details.business_hours,
                    ),
                    provenance=provenance,
                )
            return _MapResult(action, outcome, None)
        return _MapResult(action, None, None, True)

    async def _persist_observation(
        self,
        run: _Run,
        request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest,
        slot: ResearchIterationOperationIds,
        iteration_index: int,
        action: ProposedAction,
        operation_id: OperationId,
        mapped: _MapResult,
    ) -> tuple[ArtifactRef, CandidateResearchFailure | None]:
        fp = action_fingerprint(action, run.destination)
        tool = ResearchTool.SEARCH_PLACES if isinstance(action, ProposedSearchAction) else ResearchTool.GET_PLACE_DETAILS
        expected_capability = MapCapability.PLACE_SEARCH if isinstance(action, ProposedSearchAction) else MapCapability.PLACE_DETAILS
        failure: CandidateResearchFailure | None = None
        if mapped.error is not None:
            obs: PlaceObservationRecord = PlaceMapFailureObservation(record_type="map_failure", code=mapped.error.code, capability=expected_capability)
        elif mapped.outcome is None:
            obs = PlaceMapFailureObservation(record_type="map_failure", code=BaiduMapErrorCode.PROTOCOL_ERROR, capability=expected_capability)
        elif isinstance(mapped.outcome, (PlaceSearchSuccess, PlaceSearchNoResult, PlaceDetailsSuccess, PlaceDetailsNoResult)) and mapped.outcome.provenance.verified is not True:
            obs = PlaceUnverifiedResultObservation(record_type="unverified_result", capability=expected_capability)
            failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.UNVERIFIED_RESULT)
        elif not isinstance(mapped.outcome, (PlaceSearchSuccess, PlaceSearchNoResult, PlaceDetailsSuccess, PlaceDetailsNoResult)):
            obs = PlaceMapFailureObservation(record_type="map_failure", code=BaiduMapErrorCode.PROTOCOL_ERROR, capability=expected_capability)
        else:
            obs = self._observation_snapshot(mapped.outcome, expected_capability, run)
        artifact = CandidateResearchPlaceObservationArtifact(
            schema_version="1.0", run_id=run.run_id, mode=run.mode,
            invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index,
            action_ordinal=action.ordinal, tool=tool, action_fingerprint=fp, result=obs,
        )
        ref = await self._put(run, operation_id, "candidate_research.place_observation", artifact)
        if run.action_interaction is not None:
            status = "failure" if isinstance(obs, (PlaceMapFailureObservation, PlaceUnverifiedResultObservation)) else "no_result" if isinstance(obs, (PlaceSearchNoResultObservation, PlaceDetailsNoResultObservation)) else "success"
            await run.action_interaction.record_tool_result(tool_name=tool.value, ordinal=action.ordinal, outcome=status,
                feedback=artifact.model_dump_json(), observation_ref_json=ref.model_dump_json())
        if mapped.error is not None:
            failure = MapResearchFailure(tool=tool, action_ordinal=action.ordinal, code=mapped.error.code, capability=expected_capability, observation_ref=ref)
        elif isinstance(obs, PlaceMapFailureObservation) and failure is None:
            failure = MapResearchFailure(tool=tool, action_ordinal=action.ordinal, code=obs.code, capability=expected_capability, observation_ref=ref)
        return ref, failure

    async def _write_not_issued(
        self,
        run: _Run,
        slot: ResearchIterationOperationIds,
        iteration_index: int,
        action: ProposedAction,
        operation_id: OperationId,
    ) -> ArtifactRef:
        tool = ResearchTool.SEARCH_PLACES if isinstance(action, ProposedSearchAction) else ResearchTool.GET_PLACE_DETAILS
        artifact = CandidateResearchPlaceObservationArtifact(
            schema_version="1.0", run_id=run.run_id, mode=run.mode,
            invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index,
            action_ordinal=action.ordinal, tool=tool,
            action_fingerprint=action_fingerprint(action, run.destination),
            result=PlaceNotIssuedObservation(record_type="not_issued", reason_code="reservation_barrier_failed"),
        )
        ref = await self._put(run, operation_id, "candidate_research.place_observation", artifact)
        if run.action_interaction is not None:
            await run.action_interaction.record_tool_result(tool_name=tool.value, ordinal=action.ordinal, outcome="not_issued",
                feedback=artifact.model_dump_json(), observation_ref_json=ref.model_dump_json())
        return ref

    @staticmethod
    def _observation_snapshot(outcome: object, capability: MapCapability, run: _Run) -> PlaceObservationRecord:
        if isinstance(outcome, PlaceSearchSuccess):
            places = tuple(ArtifactPlaceSummarySnapshot(uid=p.uid, name=p.name, address=p.address, coordinate=ArtifactCoordinateSnapshot(latitude=p.coordinate.latitude, longitude=p.coordinate.longitude, coordinate_system="bd09ll"), provider_category=p.category) for p in outcome.places)
            known = {c.core.uid for c in run.state.candidates}
            dispositions_list: list[dict[str, object]] = []
            admitted = len(run.state.candidates)
            for i, place in enumerate(places):
                if place.uid in known:
                    disposition = "existing_uid"
                elif admitted >= 128:
                    disposition = "not_admitted_candidate_capacity"
                else:
                    disposition = "admitted_new"
                    known.add(place.uid)
                    admitted += 1
                dispositions_list.append({"result_ordinal": i, "uid": place.uid, "disposition": disposition})
            dispositions = tuple(dispositions_list)
            return PlaceSearchSuccessObservation(record_type="search_success", places=places, admission_dispositions=dispositions, provenance=ArtifactProvenanceSnapshot(source="baidu_maps_mcp", capability=capability, retrieved_at=outcome.provenance.retrieved_at, verified=True))
        if isinstance(outcome, PlaceSearchNoResult):
            return PlaceSearchNoResultObservation(record_type="search_no_result", provenance=ArtifactProvenanceSnapshot(source="baidu_maps_mcp", capability=capability, retrieved_at=outcome.provenance.retrieved_at, verified=True))
        if isinstance(outcome, PlaceDetailsSuccess):
            p = outcome.details
            details = ArtifactPlaceDetailsSnapshot(uid=p.uid, name=p.name, address=p.address, coordinate=ArtifactCoordinateSnapshot(latitude=p.coordinate.latitude, longitude=p.coordinate.longitude, coordinate_system="bd09ll"), provider_category=p.category, brand=p.brand, telephone=p.telephone, rating=p.rating, business_hours=p.business_hours)
            return PlaceDetailsSuccessObservation(record_type="details_success", details=details, provenance=ArtifactProvenanceSnapshot(source="baidu_maps_mcp", capability=capability, retrieved_at=outcome.provenance.retrieved_at, verified=True))
        if isinstance(outcome, PlaceDetailsNoResult):
            return PlaceDetailsNoResultObservation(record_type="details_no_result", provenance=ArtifactProvenanceSnapshot(source="baidu_maps_mcp", capability=capability, retrieved_at=outcome.provenance.retrieved_at, verified=True))
        return PlaceUnverifiedResultObservation(record_type="unverified_result", capability=capability)

    @staticmethod
    def _public_judgment_patch(snapshot: JudgmentPatchSnapshot) -> JudgmentPatch:
        def evidence(indexes: Sequence[int]) -> list[dict[str, str]]:
            return [{"handle_id": f"e:{index}"} for index in indexes]

        tree = {
            "outcome_type": "judgment_patch",
            "role_updates": [
                {
                    "candidate_handle": f"c:{item.candidate_ordinal}",
                    "role": item.role.value,
                    "verdict": item.verdict.value,
                    "reason_code": item.reason_code.value,
                    "evidence_handles": evidence(item.evidence_indexes),
                }
                for item in snapshot.role_updates
            ],
            "interest_updates": [
                {
                    "candidate_handle": f"c:{item.candidate_ordinal}",
                    "interest": item.interest.value,
                    "verdict": item.verdict.value,
                    "reason_code": item.reason_code,
                    "evidence_handles": evidence(item.evidence_indexes),
                }
                for item in snapshot.interest_updates
            ],
            "meal_updates": [
                {
                    "candidate_handle": f"c:{item.candidate_ordinal}",
                    "meal_intent": item.meal_intent.value,
                    "verdict": item.verdict.value,
                    "reason_code": item.reason_code,
                    "evidence_handles": evidence(item.evidence_indexes),
                }
                for item in snapshot.meal_updates
            ],
            "requirement_group_updates": [
                {
                    "requirement_group_id": group.group_id,
                    "requirement_kind": group.requirement_kind,
                    "evaluated_candidate_handles": [f"c:{ordinal}" for ordinal in group.evaluated_candidate_ordinals],
                    "default_verdict": group.default_verdict.value,
                    "default_reason_code": group.default_reason_code.value,
                    "default_evidence_handles": evidence(group.default_evidence_indexes),
                    "exceptions": [
                        {
                            "candidate_handle": f"c:{item.candidate_ordinal}",
                            "verdict": item.verdict.value,
                            "reason_code": item.reason_code.value,
                            "evidence_handles": evidence(item.evidence_indexes),
                        }
                        for item in group.exceptions
                    ],
                }
                for group in snapshot.requirement_group_updates
            ],
        }
        return TypeAdapter(JudgmentPatch).validate_json(
            json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            strict=True,
        )

    @staticmethod
    def _account_replayed_metadata(run: _Run, invocation_index: int, metadata: ArtifactInvocationMetadataSnapshot | None) -> None:
        run.semantic_invocations = max(run.semantic_invocations, invocation_index + 1)
        if metadata is None:
            return
        run.provider_attempts += metadata.provider_attempts
        for name in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            run.usage[name] = run.usage.get(name, 0) + getattr(metadata.usage, name)

    async def _judgment(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, slot: ResearchIterationOperationIds, iteration_index: int, prepared=None) -> LLMResearchFailure | ControlResearchFailure | _JudgmentReplayIndeterminate | None:
        run.judgment_recoveries = ()
        run.active_slot = slot
        existing = await self._repository.lookup(operation_id=slot.judgment_operation_id)
        saved_job = None
        if isinstance(existing, ArtifactFound):
            try:
                saved = await self._repository.get(existing.ref, expected_model=CandidateResearchModelJudgmentArtifact)
                if saved.ref != existing.ref or _digest(_json_model(saved.content)) != existing.ref.content_hash:
                    return _JudgmentReplayIndeterminate()
                saved_job = saved.content.job
            except ArtifactRepositoryError:
                return _JudgmentReplayIndeterminate()
        try:
            if prepared is not None and saved_job is None:
                run.judgment_job, run.judgment_payload = prepared
            else:
                run.judgment_payload = await self._judgment_context(run, tuple(run.observation_refs), current_iteration_index=iteration_index, saved_job=saved_job)
        except ArtifactRepositoryError:
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.ARTIFACT_ERROR)
        if not isinstance(existing, ArtifactAbsent):
            # A persisted outcome is final for this operation, including failure.
            return await self._judgment_once(run, request, slot, iteration_index)
        if run.judgment_job.candidate_ordinals and not job_has_pending_judgments(run.judgment_job, run.state.candidates):
            return None
        feedback = None
        while True:
            run.judgment_recoverable = False
            result = await self._judgment_once(run, request, slot, iteration_index, feedback)
            if result is None or isinstance(result, _JudgmentReplayIndeterminate):
                return result
            record = self._judgment_failure_record(run, result)
            if record is not None:
                code = (run.judgment_recovery_code if run.judgment_recoverable
                        and run.last_judgment_metadata is not None and not run.last_judgment_metadata.possible_duplicate else
                        result.error.code.value if isinstance(result, LLMResearchFailure)
                        and not result.error.possible_duplicate and result.error.code.value in OUTPUT_CODES else None)
                await self._write_attempt(run, "judgment", record, recovery_code=code)
            else:
                await self._write_control_attempt(run, "judgment", result)
            feedback = self._reserve_output_recovery(run, result, judgment=True)
            if feedback is None:
                return result

    async def _judgment_once(self, run, request, slot, iteration_index, recovery_feedback=None):
        prompt_payload = run.judgment_payload
        if prompt_payload is None or run.judgment_job is None:
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.CONTEXT_CONTRACT_ERROR)
        visible_candidates = tuple(item["handle"] for item in prompt_payload["candidates"])
        def candidate_evidence_directory() -> dict[str, tuple[str, ...]]:
            return {
                candidate["handle"]: tuple(candidate["evidence_handles"])
                for candidate in prompt_payload["candidates"]
            }

        existing = await self._repository.lookup(operation_id=slot.judgment_operation_id)
        if isinstance(existing, ArtifactFound):
            ref = existing.ref
            if (
                ref.artifact_type != "candidate_research.model_judgment"
                or ref.schema_version != "4.0"
                or ref.operation_id != slot.judgment_operation_id
                or ref.operation_id.run_id != run.run_id
            ):
                return _JudgmentReplayIndeterminate()
            try:
                read = await self._repository.get(ref, expected_model=CandidateResearchModelJudgmentArtifact)
            except ArtifactRepositoryError:
                return _JudgmentReplayIndeterminate()
            artifact = read.content
            if (
                read.ref != ref
                or artifact.run_id != run.run_id
                or artifact.mode is not run.mode
                or artifact.iteration_index != iteration_index
                or artifact.job != run.judgment_job
                or tuple(artifact.evidence_refs) != tuple(run.observation_refs)
                or _digest(_json_model(artifact)) != ref.content_hash
            ):
                return _JudgmentReplayIndeterminate()
            record = unwrap_model_record(artifact.result)
            await self._restore_attempt_chain(run, artifact, "judgment")
            self._remember_judgment_ref(run, ref)
            if isinstance(record, ModelJudgmentLLMFailureRecord):
                return LLMResearchFailure(invocation_kind=ResearchInvocationKind.JUDGMENT, error=self._public_llm_error(record.error))
            if isinstance(record, ModelJudgmentControllerRejectedRecord):
                try:
                    run.last_judgment_patch = TypeAdapter(JudgmentPatch).validate_json(
                        json.dumps(_json_model(record.proposed_patch), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                        strict=True,
                    )
                except (ValidationError, TypeError, ValueError):
                    return _JudgmentReplayIndeterminate()
                run.last_judgment_metadata = record.metadata
                return ControlResearchFailure(code=CandidateResearchControlFailureCode(record.code))
            if not isinstance(record, ModelJudgmentAcceptedRecord):
                return _JudgmentReplayIndeterminate()
            try:
                patch = self._public_judgment_patch(record.patch)
                visible_evidence = tuple(item["handle"] for item in prompt_payload["evidence"])
                patch = validate_judgment_patch(
                    patch=patch,
                    candidate_handles=visible_candidates,
                    evidence_handles=visible_evidence,
                    candidate_evidence_handles=candidate_evidence_directory(),
                )
                validate_required_judgments(run.judgment_job, run.state.candidates, patch)
                proposed_state = self._apply_patch(run.state, patch)
                proposed_assessments = record_assessments(run.assessments, run.judgment_job, patch)
                QualificationState(assessments=proposed_assessments, steps=self._qualification_snapshot(run).steps)
            except (ValidationError, TypeError, ValueError):
                return _JudgmentReplayIndeterminate()
            run.last_judgment_patch = patch
            run.last_judgment_metadata = record.metadata
            run.assessments = proposed_assessments
            run.state = proposed_state
            return None
        if not isinstance(existing, ArtifactAbsent):
            return _JudgmentReplayIndeterminate()
        if run.semantic_invocations >= 12:
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.BUDGET_ERROR)
        refs = tuple(run.observation_refs)
        prompt = build_observation_prompt(prompt_payload)
        if recovery_feedback is not None:
            from .correction import recovery_prompt
            prompt = recovery_prompt(prompt, code=recovery_feedback.code,
                                     recovery_number=recovery_feedback.recovery_number)
        admitted_before = getattr(run.operation, "semantic_invocations_consumed", None)
        try:
            result, identity = await self._call_model(run, "judgment", prompt, JudgmentPatch)
        except _OperationReplayIndeterminate:
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE)
        except (RuntimeError, TypeError, ValueError, AttributeError):
            return self._model_contract_failure(run, "judgment")
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            return self._model_contract_failure(run, "judgment")
        if isinstance(result, LLMInvocationFailure):
            cost = self._semantic_cost(result.error.code)
            if cost:
                run.provider_attempts_complete = False
                run.usage_complete = False
            return LLMResearchFailure(invocation_kind=ResearchInvocationKind.JUDGMENT, error=result.error)
        if not isinstance(result, StructuredInvocationSuccess):
            return self._model_contract_failure(run, "judgment")
        run.provider_attempts += result.metadata.provider_attempts
        self._add_usage(run, result.metadata)
        run.last_judgment_metadata = self._metadata(result.metadata)
        run.last_judgment_patch = None
        try:
            patch = TypeAdapter(JudgmentPatch).validate_json(json.dumps(_json_model(result.value), ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
            run.last_judgment_patch = patch
            known_groups = {group.group_id: group.requirement_kind for group in run.state.requirement_group_states}
            if any(known_groups.get(item.requirement_group_id) != item.requirement_kind for item in patch.requirement_group_updates):
                raise ValueError("unknown requirement group")
            visible_evidence = tuple(item["handle"] for item in prompt_payload["evidence"])
            patch = validate_judgment_patch(
                patch=patch,
                candidate_handles=visible_candidates,
                evidence_handles=visible_evidence,
                candidate_evidence_handles=candidate_evidence_directory(),
            )
            validate_required_judgments(run.judgment_job, run.state.candidates, patch)
        except JudgmentRequiredMissing:
            run.judgment_recoverable = True
            run.judgment_recovery_code = "judgment_required_missing"
            await identity.record_validation(stage="policy", passed=False, code="judgment_required_missing")
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.JUDGMENT_REQUIRED_MISSING)
        except JudgmentEvidenceMismatch:
            run.judgment_recoverable = True
            run.judgment_recovery_code = "judgment_evidence_mismatch"
            await identity.record_validation(stage="policy", passed=False, code="judgment_evidence_mismatch")
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID)
        except (ValidationError, ValueError, TypeError):
            if run.last_judgment_patch is None:
                return ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
            await identity.record_validation(stage="policy", passed=False, code="judgment_patch_invalid")
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID)
        await identity.record_validation(stage="policy", passed=True)
        from .purpose import normalise_meal_update
        evidence_by_handle = {item["handle"]: item for item in prompt_payload["evidence"]}
        search_by_observation = {record.observation_index: record for record in run.state.search_coverage.records}
        for handle in {ref.handle_id for update in patch.meal_updates for ref in update.evidence_handles}:
            if handle in evidence_by_handle:
                continue
            index = int(handle.split(":")[1])
            ref = refs[index]
            read = await self._repository.get(ref, expected_model=CandidateResearchPlaceObservationArtifact)
            if read.ref != ref or read.content.run_id != run.run_id or _digest(_json_model(read.content)) != ref.content_hash:
                raise ArtifactRepositoryError()
            search = search_by_observation.get(index)
            observation_result = read.content.result
            evidence_by_handle[handle] = {"accepted_search": None if search is None else {
                "meal_intent": None if search.meal_intent is None else search.meal_intent.value},
                "result": {"record_type": observation_result.record_type, "details": {
                    "business_hours": ("present",) if observation_result.record_type == "details_success" and observation_result.details.business_hours else ()}}}
        patch = patch.model_copy(update={"meal_updates": tuple(normalise_meal_update(item, evidence_by_handle) for item in patch.meal_updates)})
        try:
            proposed_state = self._apply_patch(run.state, patch)
            proposed_assessments = record_assessments(run.assessments, run.judgment_job, patch)
            QualificationState(assessments=proposed_assessments, steps=self._qualification_snapshot(run).steps)
        except (ValidationError, ValueError, TypeError, KeyError):
            await identity.record_validation(stage="controller", passed=False, code="state_invariant_failed")
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.STATE_INVARIANT_FAILED)
        resolved = self._resolve_patch(patch, refs, run.state.requirement_group_states)
        record = ModelJudgmentAcceptedRecord(record_type="accepted", patch=resolved, metadata=self._metadata(result.metadata))
        await self._write_attempt(run, "judgment", record)
        artifact = CandidateResearchModelJudgmentArtifact(schema_version="4.0", job=run.judgment_job, run_id=run.run_id, mode=run.mode, invocation_index=run.semantic_invocations - 1, invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index, prompt_version=OBSERVATION_PROMPT_VERSION, prompt_sha256=OBSERVATION_PROMPT_CONTENT_DIGEST, evidence_refs=refs, result=record, attempt_refs=tuple(run.judgment_attempt_refs))
        ref = await self._put(run, slot.judgment_operation_id, "candidate_research.model_judgment", artifact)
        self._remember_judgment_ref(run, ref)
        run.assessments = proposed_assessments
        run.state = QualifiedCandidateSetStateSnapshot(**{**_python_model(proposed_state), "model_judgment_refs": tuple(run.judgment_refs)})
        return None

    async def _observation_contexts(self, run: _Run, refs: Sequence[ArtifactRef]):
        observations, iterations = {}, {}
        search_by_index = {item.observation_index: item for item in run.state.search_coverage.records}
        for index, ref in enumerate(refs):
            artifact = run.observation_cache.get(ref)
            if artifact is None:
                read = await self._repository.get(ref, expected_model=CandidateResearchPlaceObservationArtifact)
                if read.ref != ref or read.content.run_id != run.run_id or _digest(_json_model(read.content)) != ref.content_hash:
                    raise ArtifactRepositoryError()
                artifact = read.content
                run.observation_cache[ref] = artifact
            search = search_by_index.get(index)
            objective = run.objective_history.get(search.objective_id) if search else None
            observations[index] = observation_context(index, artifact, search,
                objective.requirement_group_id if objective else None)
            iterations[index] = artifact.iteration_index
        return observations, iterations

    async def _judgment_context(
        self, run: _Run, refs: Sequence[ArtifactRef], *, current_iteration_index: int,
        selected_ordinals: Sequence[int] | None = None, step_kind: str = "search_and_judge",
        saved_job: JudgmentJob | None = None,
    ) -> dict[str, object]:
        observations, iterations = await self._observation_contexts(run, refs)
        job, payload = build_judgment_context(run.state, observations,
            project_requirements(run.requirement_specs, run.requirement_context), run.assessments,
            iteration_index=current_iteration_index,
            current_indexes=tuple(index for index, iteration in iterations.items() if iteration == current_iteration_index),
            selected_ordinals=selected_ordinals, step_kind=step_kind, saved_job=saved_job)
        run.judgment_job = job
        return payload

    def _resolve_patch(
        self,
        patch: JudgmentPatch,
        refs: Sequence[ArtifactRef],
        groups: Sequence[ArtifactRequirementGroupState],
    ) -> JudgmentPatchSnapshot:
        def indexes(values: Sequence[EvidenceHandle]) -> tuple[int, ...]:
            return evidence_handle_indexes(values, tuple(f"e:{index}" for index in range(len(refs))))
        roles = tuple(ArtifactRoleJudgmentUpdateResolved(candidate_ordinal=int(item.candidate_handle[2:]), role=item.role, verdict=item.verdict, reason_code=item.reason_code, evidence_indexes=indexes(item.evidence_handles)) for item in patch.role_updates)
        interests = tuple(ArtifactInterestJudgmentUpdateResolved(candidate_ordinal=int(item.candidate_handle[2:]), interest=item.interest, verdict=item.verdict, reason_code=item.reason_code, evidence_indexes=indexes(item.evidence_handles)) for item in patch.interest_updates)
        meals = tuple(ArtifactMealJudgmentUpdateResolved(candidate_ordinal=int(item.candidate_handle[2:]), meal_intent=item.meal_intent, verdict=item.verdict, reason_code=item.reason_code, evidence_indexes=indexes(item.evidence_handles)) for item in patch.meal_updates)
        group_by_id = {group.group_id: group for group in groups}
        resolved_groups = []
        for update in patch.requirement_group_updates:
            current = group_by_id.get(update.requirement_group_id)
            if current is None:
                raise ValueError("unknown requirement group")
            exceptions = tuple(
                ArtifactRequirementVerdictOverrideState(
                    candidate_ordinal=int(item.candidate_handle[2:]),
                    verdict=item.verdict,
                    reason_code=item.reason_code,
                    evidence_indexes=indexes(item.evidence_handles),
                )
                for item in update.exceptions
            )
            resolved_groups.append(
                ArtifactRequirementGroupState(
                    group_id=update.requirement_group_id,
                    requirement_kind=update.requirement_kind,
                    requirement_item_ids=current.requirement_item_ids,
                    evaluated_candidate_ordinals=tuple(int(handle[2:]) for handle in update.evaluated_candidate_handles),
                    default_verdict=update.default_verdict,
                    default_reason_code=update.default_reason_code,
                    default_evidence_indexes=indexes(update.default_evidence_handles),
                    exceptions=exceptions,
                )
            )
        return JudgmentPatchSnapshot(
            outcome_type="judgment_patch",
            role_updates=roles,
            interest_updates=interests,
            meal_updates=meals,
            requirement_group_updates=tuple(resolved_groups),
        )

    def _apply_patch(self, state: CandidateSetStateSnapshot, patch: JudgmentPatch) -> CandidateSetStateSnapshot:
        from .judgments import merge_requirement_group_state
        candidates = list(state.candidates)
        for update in patch.role_updates:
            ordinal = int(update.candidate_handle[2:])
            if ordinal >= len(candidates):
                continue
            item = candidates[ordinal]
            roles = [role for role in item.roles if role.role is not update.role]
            roles.append(ArtifactRoleAssessmentSnapshot(role=update.role, verdict=update.verdict, reason_code=update.reason_code, evidence_indexes=tuple(int(h.handle_id[2:]) for h in update.evidence_handles)))
            roles.sort(key=lambda x: (0 if x.role is ResearchRole.ATTRACTION else 1))
            candidates[ordinal] = self._replace_candidate(item, roles=tuple(roles))
        for update in patch.interest_updates:
            ordinal = int(update.candidate_handle[2:])
            if ordinal >= len(candidates):
                continue
            item = candidates[ordinal]
            interests = tuple(sorted(set((*item.interests, update.interest)) if update.verdict is MatchVerdict.CONFIRMED else set(item.interests) - {update.interest}, key=lambda x: list(InterestTag).index(x)))
            candidates[ordinal] = self._replace_candidate(item, interests=interests)
        for update in patch.meal_updates:
            ordinal = int(update.candidate_handle[2:])
            if ordinal >= len(candidates):
                continue
            item = candidates[ordinal]
            from .purpose import apply_meal_assessment
            incoming = ArtifactMealAssessmentSnapshot(meal_intent=update.meal_intent, verdict=update.verdict,
                reason_code=update.reason_code.value, evidence_indexes=tuple(int(h.handle_id[2:]) for h in update.evidence_handles))
            if isinstance(item, ArtifactCandidateRecordV2Snapshot):
                assessments = tuple(apply_meal_assessment(old, incoming) if old.meal_intent is update.meal_intent else old
                                    for old in item.meal_assessments)
                meals = tuple(old.meal_intent for old in assessments if old.verdict is MatchVerdict.CONFIRMED)
                candidates[ordinal] = self._replace_candidate(item, meal_intents=meals, meal_assessments=assessments)
            else:
                raise ValueError("legacy candidate cannot be updated in a new research run")
        groups = {group.group_id: group for group in state.requirement_group_states}
        visible_evidence = tuple(f"e:{index}" for index in range(len(state.observation_refs)))
        for update in patch.requirement_group_updates:
            evaluated = tuple(sorted(int(handle[2:]) for handle in update.evaluated_candidate_handles))
            exceptions = tuple(
                ArtifactRequirementVerdictOverrideState(
                    candidate_ordinal=int(item.candidate_handle[2:]),
                    verdict=item.verdict,
                    reason_code=item.reason_code,
                    evidence_indexes=evidence_handle_indexes(item.evidence_handles, visible_evidence),
                )
                for item in update.exceptions
            )
            incoming_group = ArtifactRequirementGroupState(
                group_id=update.requirement_group_id,
                requirement_kind=update.requirement_kind,
                requirement_item_ids=next((item.requirement_item_ids for item in groups.values() if item.group_id == update.requirement_group_id), (update.requirement_group_id,)),
                evaluated_candidate_ordinals=evaluated,
                default_verdict=update.default_verdict,
                default_reason_code=update.default_reason_code,
                default_evidence_indexes=evidence_handle_indexes(update.default_evidence_handles, visible_evidence),
                exceptions=exceptions,
            )
            groups[update.requirement_group_id] = merge_requirement_group_state(
                groups[update.requirement_group_id], incoming_group)
        if patch.requirement_group_updates:
            updated_scopes = {update.requirement_group_id: set(update.evaluated_candidate_handles)
                              for update in patch.requirement_group_updates}
            group_order = {key: index for index, key in enumerate(groups)}
            for ordinal, item in enumerate(candidates):
                for group in groups.values():
                    if candidate_handle(ordinal) not in updated_scopes.get(group.group_id, ()):
                        continue
                    override = next((value for value in group.exceptions if value.candidate_ordinal == ordinal), None)
                    verdict = override.verdict if override is not None else group.default_verdict
                    reason = override.reason_code if override is not None else group.default_reason_code
                    evidence = override.evidence_indexes if override is not None else group.default_evidence_indexes
                    match = ArtifactRequirementMatchSnapshot(requirement_group_id=group.group_id, verdict=verdict, reason_code=reason, evidence_indexes=evidence)
                    if group.requirement_kind == "must_visit":
                        item = self._replace_candidate(item, must_matches=tuple(sorted((*[value for value in item.must_matches if value.requirement_group_id != group.group_id], match), key=lambda value: group_order[value.requirement_group_id])))
                    else:
                        item = self._replace_candidate(item, avoid_matches=tuple(sorted((*[value for value in item.avoid_matches if value.requirement_group_id != group.group_id], match), key=lambda value: group_order[value.requirement_group_id])))
                candidates[ordinal] = item
        return self._state_from_candidates(
            state,
            tuple(candidates),
            requirement_groups=tuple(groups.values()),
            clear_pending_gaps=True,
        )

    @staticmethod
    def _replace_candidate(item: ArtifactCandidateRecordSnapshot, **changes: object) -> ArtifactCandidateRecordSnapshot:
        tree = _python_model(item)
        tree.update(changes)
        roles = changes.get("roles", item.roles)
        must_matches = changes.get("must_matches", item.must_matches)
        avoid_matches = changes.get("avoid_matches", item.avoid_matches)
        supported_role = any(value.verdict is RoleVerdict.SUPPORTED for value in roles)
        unresolved_role = any(value.verdict is RoleVerdict.UNRESOLVED for value in roles)
        all_roles_contradicted = all(value.verdict is RoleVerdict.CONTRADICTED for value in roles)
        tree["state"] = derive_candidate_state(
            core_conflicted=any(value.reason_code is RoleReasonCode.EVIDENCE_CONFLICTED for value in roles),
            confirmed_must=any(value.verdict is MatchVerdict.CONFIRMED for value in must_matches),
            confirmed_avoid=any(value.verdict is MatchVerdict.CONFIRMED for value in avoid_matches),
            avoid_unresolved=any(value.verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED} for value in avoid_matches),
            supported_role=supported_role,
            unresolved_role=unresolved_role,
            all_roles_contradicted=all_roles_contradicted,
        )
        return type(item)(**tree)

    def _add_search(self, run: _Run, action: ProposedSearchAction, outcome: PlaceSearchSuccess, index: int) -> CandidateSetStateSnapshot:
        candidates = list(run.state.candidates)
        known = {item.core.uid for item in candidates}
        before_count = len(candidates)
        for place in outcome.places:
            existing_index = next((position for position, item in enumerate(candidates) if item.core.uid == place.uid), None)
            if existing_index is not None:
                existing = candidates[existing_index]
                indexes = existing.observation_indexes if index in existing.observation_indexes else tuple((*existing.observation_indexes, index))
                incoming = ArtifactCandidateCoreSnapshot(
                    uid=place.uid, name=place.name, address=place.address,
                    coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=place.coordinate.latitude, longitude=place.coordinate.longitude),
                    provider_category=place.category,
                )
                if not self._core_equal(existing.core, incoming):
                    roles = tuple(
                        ArtifactRoleAssessmentSnapshot(
                            role=role.role, verdict=role.verdict,
                            reason_code=RoleReasonCode.EVIDENCE_CONFLICTED,
                            evidence_indexes=role.evidence_indexes if index in role.evidence_indexes else tuple((*role.evidence_indexes, index)),
                        )
                        for role in existing.roles
                    )
                    candidates[existing_index] = self._replace_candidate(existing, roles=roles, observation_indexes=indexes)
                else:
                    candidates[existing_index] = self._replace_candidate(existing, observation_indexes=indexes)
                continue
            if len(candidates) >= 128:
                continue
            roles = (
                ArtifactRoleAssessmentSnapshot(role=ResearchRole.ATTRACTION, verdict=RoleVerdict.UNRESOLVED, reason_code=RoleReasonCode.EVIDENCE_INSUFFICIENT, evidence_indexes=(index,)),
                ArtifactRoleAssessmentSnapshot(role=ResearchRole.RESTAURANT, verdict=RoleVerdict.UNRESOLVED, reason_code=RoleReasonCode.EVIDENCE_INSUFFICIENT, evidence_indexes=(index,)),
            )
            candidate = ArtifactCandidateRecordV2Snapshot(schema_version="2.0", meal_assessments=tuple(ArtifactMealAssessmentSnapshot(meal_intent=meal, verdict=MatchVerdict.UNRESOLVED, reason_code="evidence_insufficient", evidence_indexes=(index,)) for meal in MealIntent), admission_ordinal=len(candidates), core=ArtifactCandidateCoreSnapshot(uid=place.uid, name=place.name, address=place.address, coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=place.coordinate.latitude, longitude=place.coordinate.longitude), provider_category=place.category), state=CandidateState.WITHHELD, roles=roles, interests=(), meal_intents=(), must_matches=(), avoid_matches=(), details=ArtifactCandidateDetailsStateSnapshot(status="not_requested", observation_index=None), observation_indexes=(index,), dietary_restrictions="not_evaluated", mobility_constraints="not_evaluated", budget_fit="not_evaluated")
            candidates.append(candidate)
            known.add(place.uid)
        objective = run.objective_directory.get(action.objective_id)
        objective_kind = objective.kind if objective is not None else (ResearchObjectiveKind.INITIAL_CITY_ATTRACTION if action.area_mode is SearchAreaMode.CITY else ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY)
        objective_role = objective.role if objective is not None else (ResearchRole.ATTRACTION if objective_kind is ResearchObjectiveKind.INITIAL_CITY_ATTRACTION else ResearchRole.RESTAURANT if objective_kind is ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY else None)
        if objective_kind in {ResearchObjectiveKind.ATTRACTION_COUNT, ResearchObjectiveKind.RESTAURANT_COUNT, ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY}:
            run.supply_search_results[objective_kind.value] = (True, len(candidates) > before_count)
        records = list(run.state.search_coverage.records)
        eligible = tuple(item.admission_ordinal for item in candidates if item.state is CandidateState.ELIGIBLE)
        records.append(SearchCoverageRecord(objective_id=action.objective_id, objective_kind=objective_kind, action_fingerprint=action_fingerprint(action, run.destination), observation_index=index, role=objective_role, interest=action.interest, meal_intent=action.meal_intent, eligible_candidate_ordinals=eligible))
        coverage = run.state.search_coverage
        search = SearchCoverageState(records=tuple(records), initial_city_attraction_search_done=any(item.objective_kind is ResearchObjectiveKind.INITIAL_CITY_ATTRACTION for item in records), initial_hotel_restaurant_nearby_done=any(item.objective_kind is ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY for item in records))
        state = self._state_from_candidates(run.state, tuple(candidates), search=search)
        state = self._ensure_requirement_groups(run, state, index)
        return state

    @staticmethod
    def _core_equal(left: ArtifactCandidateCoreSnapshot, right: ArtifactCandidateCoreSnapshot) -> bool:
        return candidate_core_equal(left, right)

    def _record_search_no_result(self, run: _Run, action: ProposedSearchAction, index: int) -> CandidateSetStateSnapshot:
        objective = run.objective_directory.get(action.objective_id)
        objective_kind = objective.kind if objective is not None else (ResearchObjectiveKind.INITIAL_CITY_ATTRACTION if action.area_mode is SearchAreaMode.CITY else ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY)
        objective_role = objective.role if objective is not None else (ResearchRole.ATTRACTION if objective_kind is ResearchObjectiveKind.INITIAL_CITY_ATTRACTION else ResearchRole.RESTAURANT if objective_kind is ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY else None)
        run.supply_search_results[objective_kind.value] = (True, False)
        records = list(run.state.search_coverage.records)
        records.append(SearchCoverageRecord(objective_id=action.objective_id, objective_kind=objective_kind, action_fingerprint=action_fingerprint(action, run.destination), observation_index=index, role=objective_role, interest=action.interest, meal_intent=action.meal_intent, eligible_candidate_ordinals=tuple(item.admission_ordinal for item in run.state.candidates if item.state is CandidateState.ELIGIBLE)))
        search = SearchCoverageState(records=tuple(records), initial_city_attraction_search_done=any(item.objective_kind is ResearchObjectiveKind.INITIAL_CITY_ATTRACTION for item in records), initial_hotel_restaurant_nearby_done=any(item.objective_kind is ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY for item in records))
        return self._state_from_candidates(run.state, run.state.candidates, search=search)

    def _ensure_requirement_groups(self, run: _Run, state: CandidateSetStateSnapshot, evidence_index: int) -> CandidateSetStateSnapshot:
        if not run.requirement_specs or not state.candidates:
            return state
        groups = {group.group_id: group for group in state.requirement_group_states}
        candidates = list(state.candidates)
        from .judgments import merge_requirement_group_state
        for kind, group_id, item_ids in run.requirement_specs:
            current = groups.get(group_id)
            missing = tuple(item for item in candidates if current is None or item.admission_ordinal not in current.evaluated_candidate_ordinals)
            if missing:
                default_evidence = missing[0].observation_indexes[:1]
                incoming = ArtifactRequirementGroupState(group_id=group_id, requirement_kind=kind,
                    requirement_item_ids=item_ids, evaluated_candidate_ordinals=tuple(item.admission_ordinal for item in missing),
                    default_verdict=MatchVerdict.UNRESOLVED, default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT,
                    default_evidence_indexes=default_evidence,
                    exceptions=tuple(ArtifactRequirementVerdictOverrideState(candidate_ordinal=item.admission_ordinal,
                        verdict=MatchVerdict.UNRESOLVED, reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT,
                        evidence_indexes=item.observation_indexes[:1]) for item in missing
                        if item.observation_indexes[:1] != default_evidence))
                current = incoming if current is None else merge_requirement_group_state(current, incoming)
            groups[group_id] = current
        for ordinal, item in enumerate(candidates):
            for kind, group_id, _ in run.requirement_specs:
                group = groups[group_id]
                existing_matches = item.must_matches if kind == "must_visit" else item.avoid_matches
                existing_match = next((value for value in existing_matches if value.requirement_group_id == group_id), None)
                verdict = existing_match.verdict if existing_match is not None else next((override.verdict for override in group.exceptions if override.candidate_ordinal == ordinal), group.default_verdict)
                reason = existing_match.reason_code if existing_match is not None else next((override.reason_code for override in group.exceptions if override.candidate_ordinal == ordinal), group.default_reason_code)
                evidence = existing_match.evidence_indexes if existing_match is not None else next((override.evidence_indexes for override in group.exceptions if override.candidate_ordinal == ordinal), group.default_evidence_indexes)
                if not set(evidence).issubset(item.observation_indexes):
                    raise ValueError("requirement evidence ownership mismatch")
                match = ArtifactRequirementMatchSnapshot(requirement_group_id=group_id, verdict=verdict, reason_code=reason, evidence_indexes=evidence)
                if kind == "must_visit":
                    matches = [value for value in item.must_matches if value.requirement_group_id != group_id]
                    matches.append(match)
                    item = self._replace_candidate(item, must_matches=tuple(matches))
                else:
                    matches = [value for value in item.avoid_matches if value.requirement_group_id != group_id]
                    matches.append(match)
                    item = self._replace_candidate(item, avoid_matches=tuple(matches))
            candidates[ordinal] = item
        return self._state_from_candidates(
            state,
            tuple(candidates),
            requirement_groups=tuple(groups.values()),
            clear_pending_gaps=True,
        )

    def _add_details(self, state: CandidateSetStateSnapshot, outcome: PlaceDetailsSuccess, index: int) -> CandidateSetStateSnapshot:
        candidates = list(state.candidates)
        for ordinal, item in enumerate(candidates):
            if item.core.uid != outcome.details.uid:
                continue
            indexes = item.observation_indexes if index in item.observation_indexes else tuple((*item.observation_indexes, index))
            incoming = ArtifactCandidateCoreSnapshot(
                uid=outcome.details.uid, name=outcome.details.name, address=outcome.details.address,
                coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=outcome.details.coordinate.latitude, longitude=outcome.details.coordinate.longitude),
                provider_category=outcome.details.category,
            )
            if not self._core_equal(item.core, incoming):
                roles = tuple(
                    ArtifactRoleAssessmentSnapshot(
                        role=role.role, verdict=role.verdict, reason_code=RoleReasonCode.EVIDENCE_CONFLICTED,
                        evidence_indexes=role.evidence_indexes if index in role.evidence_indexes else tuple((*role.evidence_indexes, index)),
                    ) for role in item.roles
                )
                candidates[ordinal] = self._replace_candidate(item, roles=roles, observation_indexes=indexes, details={"status": "success", "observation_index": index})
            else:
                candidates[ordinal] = self._replace_candidate(item, details={"status": "success", "observation_index": index}, observation_indexes=indexes)
                break
        return self._state_from_candidates(state, tuple(candidates))

    def _mark_details_no_result(self, state: CandidateSetStateSnapshot, uid: str, index: int) -> CandidateSetStateSnapshot:
        candidates = list(state.candidates)
        for ordinal, item in enumerate(candidates):
            if item.core.uid == uid and item.state in {CandidateState.ELIGIBLE, CandidateState.WITHHELD}:
                indexes = item.observation_indexes if index in item.observation_indexes else tuple((*item.observation_indexes, index))
                candidates[ordinal] = self._replace_candidate(item, details={"status": "no_result", "observation_index": index}, observation_indexes=indexes)
                break
        return self._state_from_candidates(state, tuple(candidates))

    @staticmethod
    def _must_coverage_counts(
        requirement_groups: Sequence[ArtifactRequirementGroupState],
        candidates: Sequence[ArtifactCandidateRecordSnapshot],
    ) -> tuple[int, int]:
        must_item_count = sum(
            len(group.requirement_item_ids)
            for group in requirement_groups
            if group.requirement_kind == "must_visit"
        )
        candidates_by_ordinal = {candidate.admission_ordinal: candidate for candidate in candidates}
        resolved_must_item_count = 0
        for group in requirement_groups:
            if group.requirement_kind != "must_visit":
                continue
            overrides = {item.candidate_ordinal: item.verdict for item in group.exceptions}
            for ordinal in group.evaluated_candidate_ordinals:
                candidate = candidates_by_ordinal.get(ordinal)
                group_verdict = overrides.get(ordinal, group.default_verdict)
                if (
                    candidate is not None
                    and candidate_admitted_purposes(candidate)
                    and group_verdict is MatchVerdict.CONFIRMED
                    and any(
                        match.requirement_group_id == group.group_id
                        and match.verdict is MatchVerdict.CONFIRMED
                        for match in candidate.must_matches
                    )
                ):
                    resolved_must_item_count += len(group.requirement_item_ids)
                    break
        return must_item_count, resolved_must_item_count

    def _state_from_candidates(
        self,
        state: CandidateSetStateSnapshot,
        candidates: tuple[ArtifactCandidateRecordSnapshot, ...],
        search: SearchCoverageState | None = None,
        *,
        requirement_groups: tuple[ArtifactRequirementGroupState, ...] | None = None,
        clear_pending_gaps: bool = False,
    ) -> CandidateSetStateSnapshot:
        purposes = {item.admission_ordinal: candidate_admitted_purposes(item) for item in candidates}
        usable = tuple(item for item in candidates if purposes[item.admission_ordinal])
        counts = ArtifactResearchCountSummarySnapshot(
            attraction_count=sum("attraction" in purposes[item.admission_ordinal] for item in usable),
            attraction_target=state.counts.attraction_target,
            restaurant_count=sum(any(meal.value in purposes[item.admission_ordinal] for meal in MealIntent)
                                 for item in usable),
            restaurant_target=state.counts.restaurant_target, unique_eligible_count=len(usable),
            admitted_slots=len(candidates), candidate_capacity=128,
        )
        search = _filter_search_coverage(search or state.search_coverage, tuple(item.admission_ordinal for item in usable))
        requirement_groups = requirement_groups if requirement_groups is not None else state.requirement_group_states
        covered_interests = tuple(interest for interest in state.effective_interests
            if any("attraction" in purposes[item.admission_ordinal] and interest in item.interests for item in usable)
            and any(record.interest is interest for record in search.records))
        covered_meals = tuple(meal for meal in MealIntent
            if any(meal.value in purposes[item.admission_ordinal] for item in usable)
            and any(record.meal_intent is meal for record in search.records))
        must_item_count, resolved_must_item_count = self._must_coverage_counts(requirement_groups, candidates)
        coverage = ArtifactResearchCoverageSummarySnapshot(
            effective_interests=state.effective_interests, covered_interests=covered_interests, covered_meals=covered_meals,
            must_item_count=must_item_count, resolved_must_item_count=resolved_must_item_count,
            details_success_count=sum(item.details.status == "success" for item in usable),
            details_target=(len(usable) + 2) // 3,
            initial_city_attraction_search_done=search.initial_city_attraction_search_done,
            initial_hotel_restaurant_nearby_done=search.initial_hotel_restaurant_nearby_done,
        )
        run = self._runs.get(state.hotel_selection_ref.operation_id.run_id)
        return QualifiedCandidateSetStateSnapshot(
            confirmed_requirement_digest=state.confirmed_requirement_digest, hotel_selection_ref=state.hotel_selection_ref,
            effective_interests=state.effective_interests,
            model_action_refs=tuple(run.action_refs) if run else state.model_action_refs,
            model_judgment_refs=tuple(run.judgment_refs) if run else state.model_judgment_refs,
            observation_refs=state.observation_refs, candidates=candidates,
            requirement_group_states=requirement_groups, search_coverage=search, counts=counts, coverage=coverage,
            budgets=state.budgets, pending_gaps=() if clear_pending_gaps else state.pending_gaps,
            iteration_no_progress_streak=state.iteration_no_progress_streak,
        )


    def _refresh_state(self, state: CandidateSetStateSnapshot, *, no_progress: int) -> CandidateSetStateSnapshot:
        return QualifiedCandidateSetStateSnapshot(**{**_python_model(state), "iteration_no_progress_streak": no_progress})

    def _state_with(self, run: _Run, *, no_progress: int) -> CandidateSetStateSnapshot:
        run.state = self._refresh_state(run.state, no_progress=no_progress)
        return run.state

    async def _write_in_progress(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, slot: ResearchIterationOperationIds) -> ArtifactRef:
        existing = await self._repository.lookup(operation_id=slot.candidate_set_operation_id)
        if isinstance(existing, ArtifactFound):
            ref = existing.ref
            if (
                ref.artifact_type != "candidate_research.candidate_set"
                or ref.schema_version != "5.0"
                or ref.operation_id != slot.candidate_set_operation_id
                or ref.operation_id.run_id != run.run_id
            ):
                raise _OperationReplayIndeterminate()
            try:
                read = await self._repository.get(ref, expected_model=CandidateResearchArtifact)
            except ArtifactRepositoryError as error:
                raise _OperationReplayIndeterminate() from error
            if (
                read.ref != ref
                or read.content.run_id != run.run_id
                or read.content.mode is not run.mode
                or not isinstance(read.content.record, InProgressCandidateSetRecord)
                or _digest(_json_model(read.content)) != ref.content_hash
            ):
                raise _OperationReplayIndeterminate()
            run.last_consistent_ref = ref
            return ref
        if not isinstance(existing, ArtifactAbsent):
            raise ArtifactRepositoryUsageError()
        run.state = await self._state_budget(run)
        base_ref = run.predecessor_ref if run.mode is ResearchMode.SUPPLEMENTAL else None
        artifact = CandidateResearchArtifact(schema_version="5.0", invocation_attempt_refs=tuple(run.invocation_attempt_refs), correction_state=self._correction_snapshot(run), qualification_state=self._qualification_snapshot(run), run_id=run.run_id, mode=run.mode, predecessor_ref=base_ref, invocation_request_digest=run.request_digest, record=InProgressCandidateSetRecord(record_type="in_progress", state=run.state))
        ref = await self._put(run, slot.candidate_set_operation_id, "candidate_research.candidate_set", artifact)
        run.last_consistent_ref = ref
        return ref

    async def _finish(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, terminal_id: OperationId, *, transition_kind: str) -> CandidateResearchOutcome:
        run.state = await self._state_budget(run)
        completed = self._is_complete(run, request)
        if completed:
            record = CompletedCandidateSetRecord(record_type="completed", transition_kind=transition_kind, state=run.state, counts=run.state.counts, coverage=run.state.coverage, budgets=run.state.budgets)
        else:
            gaps = self._gaps(run, request)
            if not gaps:
                return await self._failed(run, request, ControlResearchFailure(code=CandidateResearchControlFailureCode.ARTIFACT_ERROR), None)
            has_unmet_count = run.state.counts.attraction_count < run.state.counts.attraction_target or run.state.counts.restaurant_count < run.state.counts.restaurant_target
            supply_entries = tuple(self._supply_entry(run, role) for role in (ResearchRole.ATTRACTION, ResearchRole.RESTAURANT) if self._supply_entry(run, role) is not None)
            stop = compute_stop_reasons(
                admitted_slots=run.state.counts.admitted_slots,
                has_unmet_new_candidate_goal=has_unmet_count,
                place_remaining=run.state.budgets.place_call_limit - run.state.budgets.place_calls_consumed,
                has_place_progress_goal=bool(gaps),
                iteration_remaining=run.state.budgets.iteration_limit - run.state.budgets.iterations_consumed,
                completed=False,
                no_progress_streak=run.state.iteration_no_progress_streak,
                has_unmet_goal=bool(gaps),
                legal_action_count=self._legal_action_count(run, request),
                supply_gaps=supply_entries,
            )
            if run.semantic_invocations >= 12 or (
                    run.semantic_invocations == 11 and await self._pending_judgment(run) is None):
                stop += (CandidateResearchStopReason.SEMANTIC_BUDGET_INSUFFICIENT,)
            if run.correction_stop_reason is not None and run.correction_stop_reason not in stop:
                stop += (run.correction_stop_reason,)
            stop = tuple(sorted(set(stop), key=lambda item: list(CandidateResearchStopReason).index(item)))
            if not stop:
                return await self._failed(
                    run,
                    request,
                    ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR),
                    None,
                )
            record = IncompleteCandidateSetRecord(record_type="incomplete", transition_kind=transition_kind, state=self._state_pending(run.state, gaps), counts=run.state.counts, coverage=run.state.coverage, budgets=run.state.budgets, gaps=gaps, stop_reasons=stop)
            run.state = record.state
        base_ref = run.predecessor_ref if run.mode is ResearchMode.SUPPLEMENTAL else None
        artifact = CandidateResearchArtifact(schema_version="5.0", invocation_attempt_refs=tuple(run.invocation_attempt_refs), correction_state=self._correction_snapshot(run), qualification_state=self._qualification_snapshot(run), run_id=run.run_id, mode=run.mode, predecessor_ref=base_ref, invocation_request_digest=run.request_digest, record=record)
        await self._validate_qualification_history(artifact)
        try:
            ref = await self._put(run, terminal_id, "candidate_research.candidate_set", artifact)
        except ArtifactRepositoryError:
            return self._memory_persistence_failure(run)
        run.last_consistent_ref = ref
        try:
            read = await self._repository.get(ref, expected_model=CandidateResearchArtifact)
        except ArtifactRepositoryError:
            return self._memory_persistence_failure(run)
        if read.ref != ref or read.content.schema_version != "5.0" or read.content.run_id != run.run_id or read.content.invocation_request_digest != run.request_digest:
            return self._memory_persistence_failure(run)
        return self._outcome_from_record(ref, read.content.record)

    def _legal_action_count(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest) -> int:
        objectives = self._objectives(run, request)
        return min(8, sum(bool(self._allowed_objective_actions(run, item)) for item in objectives))

    async def _failed(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, failure: LLMResearchFailure | MapResearchFailure | ControlResearchFailure, failure_ref: ArtifactRef | None) -> CandidateResearchFailed:
        run.state = await self._state_budget(run)
        snapshot_failure = self._failure_snapshot(failure)
        record = FailedCandidateSetRecord(record_type="failed", state=run.state, failure=snapshot_failure, last_consistent_candidate_set_ref=run.last_consistent_ref, failure_artifact_ref=failure_ref, budgets=run.state.budgets, retryable=self._retryable(failure), possible_duplicate=self._duplicate(failure))
        base_ref = run.predecessor_ref if run.mode is ResearchMode.SUPPLEMENTAL else None
        artifact = CandidateResearchArtifact(schema_version="5.0", invocation_attempt_refs=tuple(run.invocation_attempt_refs), correction_state=self._correction_snapshot(run), qualification_state=self._qualification_snapshot(run), run_id=run.run_id, mode=run.mode, predecessor_ref=base_ref, invocation_request_digest=run.request_digest, record=record)
        try:
            ref = await self._put(run, request.operation_plan.terminal_successor_operation_id, "candidate_research.candidate_set", artifact)
        except ArtifactRepositoryError:
            return self._memory_persistence_failure(run)
        run.last_consistent_ref = ref
        try:
            read = await self._repository.get(ref, expected_model=CandidateResearchArtifact)
        except ArtifactRepositoryError:
            return self._memory_persistence_failure(run)
        if read.ref != ref or read.content.schema_version != "5.0" or read.content.run_id != run.run_id or read.content.invocation_request_digest != run.request_digest:
            return self._memory_persistence_failure(run)
        return self._outcome_from_record(ref, read.content.record)

    async def _write_action_artifact(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, slot: ResearchIterationOperationIds, iteration_index: int, failure: LLMResearchFailure, _: object) -> ArtifactRef:
        record = ModelActionLLMFailureRecord(record_type="llm_failure", error=ArtifactLLMErrorSnapshot(**_python_model(failure.error)))
        await self._write_attempt(run, "action", record)
        artifact = CandidateResearchModelActionArtifact(schema_version="3.0", run_id=run.run_id, mode=run.mode, invocation_index=max(0, run.semantic_invocations - 1), invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index, prompt_version=ACTION_PROMPT_VERSION, prompt_sha256=ACTION_PROMPT_CONTENT_DIGEST, input_digest=run.request_digest, result=record, attempt_refs=tuple(run.action_attempt_refs))
        ref = await self._put(run, slot.action_operation_id, "candidate_research.model_action", artifact)
        self._remember_action_ref(run, ref)
        return ref

    async def _write_model_action(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, slot: ResearchIterationOperationIds, iteration_index: int, record: object) -> ArtifactRef:
        await self._write_attempt(run, "action", record)
        artifact = CandidateResearchModelActionArtifact(schema_version="3.0", run_id=run.run_id, mode=run.mode, invocation_index=run.semantic_invocations - 1, invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index, prompt_version=ACTION_PROMPT_VERSION, prompt_sha256=ACTION_PROMPT_CONTENT_DIGEST, input_digest=run.request_digest, result=record, attempt_refs=tuple(run.action_attempt_refs))
        ref = await self._put(run, slot.action_operation_id, "candidate_research.model_action", artifact)
        await self._record_action_feedback(run, record)
        return ref

    async def _record_action_feedback(self, run, record):
        if run.action_interaction is not None and not run.call_info.get("action", {}).get("feedback_recorded", False):
            accepted = record.record_type == "accepted"
            code = None if accepted else getattr(record, "code", getattr(record, "reason_code", "action_failed"))
            await run.action_interaction.record_validation(stage="policy" if record.record_type == "policy_rejected" else "batch", passed=accepted, code=code)
            actions = getattr(getattr(record, "output", None), "actions", ())
            dispositions = {item.action_ordinal: item for item in getattr(record, "action_dispositions", ())}
            for action in actions:
                disposition = dispositions.get(action.ordinal)
                if not accepted or (disposition is not None and disposition.disposition != "accepted"):
                    cached = accepted and disposition.disposition == "duplicate_action"
                    reused = disposition.reused_observation_ref if cached else None
                    await run.action_interaction.record_tool_result(tool_name=action.action_type, ordinal=action.ordinal,
                        outcome="cached" if cached else "not_issued" if accepted else "rejected",
                        feedback=json.dumps({"status": disposition.disposition if accepted else record.record_type, "code": code}),
                        observation_ref_json=None if reused is None else reused.model_dump_json())
            run.call_info["action"]["feedback_recorded"] = True

    async def _write_judgment_failure(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, slot: ResearchIterationOperationIds, iteration_index: int, failure: LLMResearchFailure | ControlResearchFailure) -> ArtifactRef:
        if isinstance(failure, LLMResearchFailure):
            record = ModelJudgmentLLMFailureRecord(record_type="llm_failure", error=ArtifactLLMErrorSnapshot(**_python_model(failure.error)))
        else:
            if run.last_judgment_patch is None or run.last_judgment_metadata is None:
                raise ArtifactRepositoryError()
            proposed = JudgmentPatchModelSnapshot.model_validate(
                _python_model(run.last_judgment_patch), strict=True
            )
            record = ModelJudgmentControllerRejectedRecord(
                record_type="controller_rejected",
                proposed_patch=proposed,
                metadata=run.last_judgment_metadata,
                code=failure.code.value,
            )
        await self._write_attempt(run, "judgment", record)
        artifact = CandidateResearchModelJudgmentArtifact(schema_version="4.0", job=run.judgment_job, run_id=run.run_id, mode=run.mode, invocation_index=max(0, run.semantic_invocations - 1), invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index, prompt_version=OBSERVATION_PROMPT_VERSION, prompt_sha256=OBSERVATION_PROMPT_CONTENT_DIGEST, evidence_refs=tuple(run.observation_refs), result=record, attempt_refs=tuple(run.judgment_attempt_refs))
        ref = await self._put(run, slot.judgment_operation_id, "candidate_research.model_judgment", artifact)
        self._remember_judgment_ref(run, ref)
        return ref

    async def _put(self, run: _Run, operation_id: OperationId, artifact_type: str, artifact: BaseModel) -> ArtifactRef:
        request = _artifact_write_request(operation_id=operation_id, artifact_type=artifact_type, artifact=artifact)
        existing = await self._repository.lookup(operation_id=operation_id)
        if isinstance(existing, ArtifactFound):
            ref = existing.ref
            if (
                ref.operation_id != operation_id
                or ref.operation_id.run_id != run.run_id
                or ref.artifact_type != artifact_type
                or ref.schema_version != artifact.schema_version
            ):
                raise _OperationReplayIndeterminate()
            expected_models: dict[str, type[BaseModel]] = {
                "candidate_research.model_action": CandidateResearchModelActionArtifact,
                "candidate_research.model_judgment": CandidateResearchModelJudgmentArtifact,
                "candidate_research.place_observation": CandidateResearchPlaceObservationArtifact,
                "candidate_research.candidate_set": CandidateResearchArtifact,
                "candidate_research.invocation_attempt": CandidateResearchInvocationAttemptArtifact,
            }
            expected = expected_models.get(artifact_type)
            if expected is None:
                raise ArtifactRepositoryUsageError()
            try:
                read = await self._repository.get(ref, expected_model=expected)
            except ArtifactRepositoryError as error:
                raise _OperationReplayIndeterminate() from error
            if read.ref != ref or _digest(_json_model(read.content)) != _digest(request.content):
                raise _OperationReplayIndeterminate()
            return ref
        if not isinstance(existing, ArtifactAbsent):
            raise ArtifactRepositoryUsageError()
        try:
            return await self._repository.put(request)
        except ArtifactRepositoryError as error:
            # Resolve an ambiguous write using the exact operation and content;
            # never retry the supplier or the write here.
            try:
                observed = await self._repository.lookup(operation_id=operation_id)
                if isinstance(observed, ArtifactFound):
                    ref = observed.ref
                    read = await self._repository.get(ref, expected_model=type(artifact))
                    if (ref.operation_id == operation_id and ref.artifact_type == artifact_type
                            and ref.schema_version == artifact.schema_version and read.ref == ref
                            and ref.content_hash == _digest(request.content)
                            and _digest(_json_model(read.content)) == ref.content_hash):
                        return ref
            except ArtifactRepositoryError:
                pass
            run.indeterminate = True
            raise _OperationReplayIndeterminate() from error

    @staticmethod
    def _remember_action_ref(run: _Run, ref: ArtifactRef) -> None:
        run.active_action_ref = ref
        if ref not in run.action_refs:
            run.action_refs.append(ref)

    @staticmethod
    def _remember_judgment_ref(run: _Run, ref: ArtifactRef) -> None:
        run.active_judgment_ref = ref
        if ref not in run.judgment_refs:
            run.judgment_refs.append(ref)

    async def _state_budget(self, run: _Run) -> CandidateSetStateSnapshot:
        try:
            iteration = await self._budget.get_snapshot(key=self._iteration_key())
            places = await self._budget.get_snapshot(key=self._place_key())
        except ExecutionBudgetError:
            raise
        except (AttributeError, TypeError, ValueError):
            raise RuntimeError("budget snapshot contract violation") from None
        if not self._valid_budget_snapshot(iteration, run.run_id, self._iteration_key()) or not self._valid_budget_snapshot(places, run.run_id, self._place_key()):
            raise RuntimeError("budget snapshot contract violation")
        iteration_consumed = iteration.consumed
        place_consumed = places.consumed
        budget = ArtifactResearchBudgetSummarySnapshot(iteration_limit=6, iterations_consumed=iteration_consumed, place_call_limit=48, place_calls_consumed=place_consumed, semantic_invocation_limit=12, semantic_invocations_consumed=run.semantic_invocations, known_provider_attempts=run.provider_attempts, provider_attempts_complete=run.provider_attempts_complete, known_usage=ArtifactKnownLLMUsageSnapshot(prompt_tokens=run.usage.get("prompt_tokens", 0), completion_tokens=run.usage.get("completion_tokens", 0), total_tokens=run.usage.get("total_tokens", 0), reasoning_tokens=run.usage.get("reasoning_tokens", 0), prompt_cache_hit_tokens=run.usage.get("prompt_cache_hit_tokens", 0), prompt_cache_miss_tokens=run.usage.get("prompt_cache_miss_tokens", 0)), usage_complete=run.usage_complete, correction_limit=2, corrections_consumed=run.output_recoveries_used)
        return QualifiedCandidateSetStateSnapshot(**{**_python_model(run.state), "budgets": budget, "model_action_refs": tuple(run.action_refs), "model_judgment_refs": tuple(run.judgment_refs), "observation_refs": tuple(run.observation_refs)})

    @staticmethod
    def _valid_budget_snapshot(snapshot: object, run_id: UUID, key: object) -> bool:
        return (
            isinstance(snapshot, ExecutionBudgetSnapshot)
            and snapshot.run_id == run_id
            and snapshot.key == key
            and snapshot.remaining == snapshot.limit - snapshot.consumed
        )

    def _iteration_key(self):
        from .models import CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY
        return CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY

    def _place_key(self):
        from .models import CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY
        return CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY

    @staticmethod
    def _is_complete(run: _Run | CandidateSetStateSnapshot, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest) -> bool:
        state = run if isinstance(run, CandidateSetStateSnapshot) else run.state
        if isinstance(request, CandidateResearchSupplementalRequest) and not all(
                CandidateResearchService._gap_satisfied(run, gap) for gap in request.gaps):
            return False
        return _candidate_set_state_complete(state, require_all_avoid=False) and not state.pending_gaps

    @staticmethod
    def _supply_qualification_needed(state: CandidateSetStateSnapshot) -> bool:
        return (
            state.counts.attraction_count < state.counts.attraction_target
            or state.counts.restaurant_count < state.counts.restaurant_target
            or state.coverage.covered_interests != state.effective_interests
            or state.coverage.covered_meals != tuple(MealIntent)
            or state.coverage.resolved_must_item_count < state.coverage.must_item_count
        )

    def _gaps(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest) -> tuple[object, ...]:
        if isinstance(request, CandidateResearchSupplementalRequest):
            result: list[object] = []
            for gap in request.gaps:
                if self._gap_satisfied(run, gap):
                    continue
                if getattr(gap, "gap_type") == "avoid_resolution_missing":
                    group_id = getattr(gap, "requirement_group_id")
                    group = next((item for item in run.state.requirement_group_states if item.group_id == group_id and item.requirement_kind == "avoid"), None)
                    if group is not None:
                        unresolved_uids = tuple(
                            item.core.uid
                            for item in run.state.candidates
                            if item.admission_ordinal in group.evaluated_candidate_ordinals
                            and next((match.verdict for match in item.avoid_matches if match.requirement_group_id == group_id), group.default_verdict)
                            in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED}
                        )
                        if unresolved_uids:
                            result.append(ArtifactAvoidResolutionMissingSummary(
                                gap_type="avoid_resolution_missing",
                                gap_id=f"gap:avoid:{group_id}",
                                evidence_indexes=self._related_gap_indexes(run, gap),
                                requirement_group_id=group_id,
                                requirement_item_ids=tuple(getattr(gap, "requirement_item_ids")),
                                candidate_uids=unresolved_uids,
                            ))
                            continue
                result.append(self._gap_summary(gap, self._related_gap_indexes(run, gap)))
            if result:
                return tuple(result)
            # Once every requested supplemental gap is satisfied, retain a
            # replayable terminal by deriving any remaining global gaps from
            # the current state; a satisfied historical target must not be
            # reintroduced into pending_gaps.
        result = []
        if run.state.counts.attraction_count < run.state.counts.attraction_target:
            result.append(ArtifactCandidateCountShortfallSummary(
                gap_type="candidate_count_shortfall", gap_id="gap:attraction",
                evidence_indexes=self._related_gap_indexes(run, "candidate_count_shortfall", role=ResearchRole.ATTRACTION),
                role=ResearchRole.ATTRACTION, current_count=run.state.counts.attraction_count,
                target_count=run.state.counts.attraction_target,
            ))
        if run.state.counts.restaurant_count < run.state.counts.restaurant_target:
            result.append(ArtifactCandidateCountShortfallSummary(
                gap_type="candidate_count_shortfall", gap_id="gap:restaurant",
                evidence_indexes=self._related_gap_indexes(run, "candidate_count_shortfall", role=ResearchRole.RESTAURANT),
                role=ResearchRole.RESTAURANT, current_count=run.state.counts.restaurant_count,
                target_count=run.state.counts.restaurant_target,
            ))
        for interest in run.effective_interests:
            if interest not in run.state.coverage.covered_interests:
                result.append(ArtifactInterestCoverageMissingSummary(
                    gap_type="interest_coverage_missing", gap_id=f"gap:interest:{interest.value}",
                    evidence_indexes=self._related_gap_indexes(run, "interest_coverage_missing", interest=interest), interest=interest,
                ))
        for meal in MealIntent:
            if meal not in run.state.coverage.covered_meals:
                result.append(ArtifactMealCoverageMissingSummary(
                    gap_type="meal_coverage_missing", gap_id=f"gap:meal:{meal.value}",
                    evidence_indexes=self._related_gap_indexes(run, "meal_coverage_missing", meal_intent=meal), meal_intent=meal,
                    anchor=ArtifactResearchAnchorSnapshot(anchor_type="hotel", uid=None), radius_km=3,
                ))
        if not run.state.coverage.initial_city_attraction_search_done:
            result.append(ArtifactAreaCoverageMissingSummary(
                gap_type="area_coverage_missing", gap_id="gap:initial-city-attraction",
                evidence_indexes=self._related_gap_indexes(run, "area_coverage_missing", role=ResearchRole.ATTRACTION), role=ResearchRole.ATTRACTION,
                anchor=ArtifactResearchAnchorSnapshot(anchor_type="hotel", uid=None), radius_km=3,
            ))
        if not run.state.coverage.initial_hotel_restaurant_nearby_done:
            result.append(ArtifactAreaCoverageMissingSummary(
                gap_type="area_coverage_missing", gap_id="gap:initial-hotel-restaurant",
                evidence_indexes=self._related_gap_indexes(run, "area_coverage_missing", role=ResearchRole.RESTAURANT), role=ResearchRole.RESTAURANT,
                anchor=ArtifactResearchAnchorSnapshot(anchor_type="hotel", uid=None), radius_km=3,
            ))
        missing_details = tuple(
            item.core.uid
            for item in run.state.candidates
            if candidate_admitted_purposes(item) and item.details.status != "success"
        ) if run.state.coverage.details_success_count < run.state.coverage.details_target else ()
        if missing_details:
            result.append(ArtifactCandidateDetailsMissingSummary(
                gap_type="candidate_details_missing", gap_id="gap:details",
                evidence_indexes=self._related_gap_indexes(run, "candidate_details_missing", candidate_uids=missing_details[:128]), candidate_uids=missing_details[:128],
            ))
        for kind, group_id, item_ids in run.requirement_specs:
            group = next((value for value in run.state.requirement_group_states if value.group_id == group_id), None)
            if kind == "must_visit":
                resolved = group is not None and any(candidate_admitted_purposes(item) and any(match.requirement_group_id == group_id and match.verdict is MatchVerdict.CONFIRMED for match in item.must_matches) for item in run.state.candidates)
                if not resolved:
                    result.append(ArtifactMustVisitResolutionMissingSummary(
                        gap_type="must_visit_resolution_missing", gap_id=f"gap:must:{group_id}",
                        evidence_indexes=self._related_gap_indexes(run, "must_visit_resolution_missing", requirement_group_id=group_id),
                        requirement_group_id=group_id, requirement_item_ids=item_ids,
                    ))
            elif group is not None and self._supply_qualification_needed(run.state):
                unresolved_uids = tuple(item.core.uid for item in run.state.candidates if item.admission_ordinal in group.evaluated_candidate_ordinals and (next((match.verdict for match in item.avoid_matches if match.requirement_group_id == group_id), group.default_verdict) in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED}))
                if unresolved_uids:
                    result.append(ArtifactAvoidResolutionMissingSummary(
                        gap_type="avoid_resolution_missing", gap_id=f"gap:avoid:{group_id}",
                        evidence_indexes=self._related_gap_indexes(run, "avoid_resolution_missing", requirement_group_id=group_id),
                        requirement_group_id=group_id, requirement_item_ids=item_ids, candidate_uids=unresolved_uids,
                    ))
        return tuple(result)

    def _related_gap_indexes(self, run: _Run, gap: object, **fields: object) -> tuple[int, ...]:
        """Derive related evidence indexes; never reuse an unrelated last index."""
        state = run.state
        related: set[int] = set()
        gap_type = gap if isinstance(gap, str) else getattr(gap, "gap_type")
        def value(name: str, default: object = None) -> object:
            return getattr(gap, name, fields.get(name, default))
        if gap_type == "candidate_count_shortfall":
            kinds = ({ResearchObjectiveKind.ATTRACTION_COUNT, ResearchObjectiveKind.INITIAL_CITY_ATTRACTION}
                     if value("role") is ResearchRole.ATTRACTION else {ResearchObjectiveKind.RESTAURANT_COUNT, ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY})
            related.update(record.observation_index for record in state.search_coverage.records if record.objective_kind in kinds)
        elif gap_type == "interest_coverage_missing":
            interest = value("interest")
            related.update(record.observation_index for record in state.search_coverage.records if record.interest is interest)
            related.update(index for candidate in state.candidates if interest in candidate.interests for index in candidate.observation_indexes)
        elif gap_type == "meal_coverage_missing":
            meal = value("meal_intent")
            related.update(record.observation_index for record in state.search_coverage.records if record.meal_intent is meal)
            related.update(index for candidate in state.candidates if meal in candidate.meal_intents for index in candidate.observation_indexes)
        elif gap_type == "area_coverage_missing":
            related.update(record.observation_index for record in state.search_coverage.records if record.role is value("role"))
        elif gap_type in {"must_visit_resolution_missing", "avoid_resolution_missing"}:
            group = next((item for item in state.requirement_group_states if item.group_id == value("requirement_group_id")), None)
            if group is not None:
                related.update(group.default_evidence_indexes)
                related.update(index for item in group.exceptions for index in item.evidence_indexes)
                matches_name = "must_matches" if gap_type == "must_visit_resolution_missing" else "avoid_matches"
                related.update(index for candidate in state.candidates for match in getattr(candidate, matches_name) if match.requirement_group_id == group.group_id for index in match.evidence_indexes)
        elif gap_type == "candidate_details_missing":
            wanted = set(value("candidate_uids", ()))
            related.update(index for candidate in state.candidates if candidate.core.uid in wanted for index in candidate.observation_indexes)
        elif gap_type == "replacement_candidate_needed":
            related.update(record.observation_index for record in state.search_coverage.records if record.role is value("role"))
        return tuple(index for index in sorted(related) if 0 <= index < len(state.observation_refs))[:8]

    @staticmethod
    def _gap_summary(gap: object, evidence_indexes: tuple[int, ...]) -> object:
        gap_type = getattr(gap, "gap_type")
        common = {"gap_type": gap_type, "gap_id": getattr(gap, "gap_id"), "evidence_indexes": evidence_indexes}
        if gap_type == "candidate_count_shortfall":
            return ArtifactCandidateCountShortfallSummary(**common, role=getattr(gap, "role"), current_count=getattr(gap, "current_count"), target_count=getattr(gap, "target_count"))
        if gap_type == "meal_count_shortfall":
            return ArtifactMealCountShortfallSummary(**common, meal_intent=gap.meal_intent,
                current_count=gap.current_count, target_count=gap.target_count,
                anchor=CandidateResearchService._anchor_snapshot(gap.anchor), radius_km=gap.radius_km,
                source_candidate_set_ref=gap.evidence_refs[0])
        if gap_type == "interest_coverage_missing":
            return ArtifactInterestCoverageMissingSummary(**common, interest=getattr(gap, "interest"))
        if gap_type == "area_coverage_missing":
            anchor = getattr(gap, "anchor")
            return ArtifactAreaCoverageMissingSummary(**common, role=getattr(gap, "role"), anchor=CandidateResearchService._anchor_snapshot(anchor), radius_km=getattr(gap, "radius_km"))
        if gap_type == "meal_coverage_missing":
            anchor = getattr(gap, "anchor")
            return ArtifactMealCoverageMissingSummary(**common, meal_intent=getattr(gap, "meal_intent"), anchor=CandidateResearchService._anchor_snapshot(anchor), radius_km=getattr(gap, "radius_km"))
        if gap_type == "must_visit_resolution_missing":
            return ArtifactMustVisitResolutionMissingSummary(**common, requirement_group_id=getattr(gap, "requirement_group_id"), requirement_item_ids=getattr(gap, "requirement_item_ids"))
        if gap_type == "avoid_resolution_missing":
            return ArtifactAvoidResolutionMissingSummary(**common, requirement_group_id=getattr(gap, "requirement_group_id"), requirement_item_ids=getattr(gap, "requirement_item_ids"), candidate_uids=getattr(gap, "candidate_uids"))
        if gap_type == "candidate_details_missing":
            return ArtifactCandidateDetailsMissingSummary(**common, candidate_uids=getattr(gap, "candidate_uids"))
        return ArtifactReplacementCandidateNeededSummary(**common, rejected_uid=getattr(gap, "rejected_uid"), role=getattr(gap, "role"), anchor=None if getattr(gap, "anchor") is None else CandidateResearchService._anchor_snapshot(getattr(gap, "anchor")), radius_km=getattr(gap, "radius_km"))

    @staticmethod
    def _anchor_snapshot(anchor: object) -> ArtifactResearchAnchorSnapshot:
        if getattr(anchor, "anchor_type", None) == "hotel":
            return ArtifactResearchAnchorSnapshot(anchor_type="hotel", uid=None)
        return ArtifactResearchAnchorSnapshot(anchor_type="candidate", uid=getattr(anchor, "uid"))

    @staticmethod
    def _gap_satisfied(run: _Run | CandidateSetStateSnapshot, gap: object) -> bool:
        state = run if isinstance(run, CandidateSetStateSnapshot) else run.state
        gap_type = getattr(gap, "gap_type")
        if gap_type == "meal_count_shortfall":
            return sum(gap.meal_intent.value in candidate_admitted_purposes(item) for item in state.candidates) >= gap.target_count
        if gap_type == "candidate_count_shortfall":
            purpose = "attraction" if getattr(gap, "role") is ResearchRole.ATTRACTION else "restaurant"
            count = sum("attraction" in candidate_admitted_purposes(item) if purpose == "attraction" else
                any(meal in candidate_admitted_purposes(item) for meal in ("lunch", "dinner")) for item in state.candidates)
            return count >= getattr(gap, "target_count")
        if gap_type == "interest_coverage_missing":
            interest = getattr(gap, "interest")
            return interest in state.coverage.covered_interests or (
                any(record.interest is interest for record in state.search_coverage.records)
                and any("attraction" in candidate_admitted_purposes(item) and interest in item.interests for item in state.candidates)
            )
        if gap_type == "area_coverage_missing":
            role = getattr(gap, "role")
            return any(record.role is role for record in state.search_coverage.records)
        if gap_type == "meal_coverage_missing":
            meal = getattr(gap, "meal_intent")
            return any(meal.value in candidate_admitted_purposes(item) for item in state.candidates)
        if gap_type == "candidate_details_missing":
            by_uid = {item.core.uid: item for item in state.candidates}
            return all(by_uid.get(uid) is not None and by_uid[uid].details.status == "success" for uid in getattr(gap, "candidate_uids"))
        if gap_type == "must_visit_resolution_missing":
            group = next((item for item in state.requirement_group_states if item.group_id == getattr(gap, "requirement_group_id")), None)
            return group is not None and any(candidate_admitted_purposes(item) and any(match.requirement_group_id == group.group_id and match.verdict is MatchVerdict.CONFIRMED for match in item.must_matches) for item in state.candidates)
        if gap_type == "avoid_resolution_missing":
            group_id = getattr(gap, "requirement_group_id")
            group = next((item for item in state.requirement_group_states if item.group_id == group_id and item.requirement_kind == "avoid"), None)
            target_items = tuple(getattr(gap, "requirement_item_ids"))
            target = tuple(getattr(gap, "candidate_uids"))
            if group is None or group.requirement_item_ids != target_items:
                return False
            by_uid = {item.core.uid: item for item in state.candidates}
            if any(uid not in by_uid for uid in target):
                return False
            target_ordinals = {by_uid[uid].admission_ordinal for uid in target}
            evaluated_ordinals = set(group.evaluated_candidate_ordinals)
            if not target_ordinals.issubset(evaluated_ordinals):
                return False
            for item in state.candidates:
                if item.admission_ordinal not in target_ordinals:
                    continue
                match = next((value for value in item.avoid_matches if value.requirement_group_id == group_id), None)
                if match is None or match.verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED}:
                    return False
                expected_verdict = next((override.verdict for override in group.exceptions if override.candidate_ordinal == item.admission_ordinal), group.default_verdict)
                if match.verdict is not expected_verdict:
                    return False
                if match.verdict not in {MatchVerdict.NO_MATCH, MatchVerdict.CONFIRMED}:
                    return False
                if not match.evidence_indexes or any(index < 0 or index >= len(state.observation_refs) for index in match.evidence_indexes):
                    return False
                if match.verdict is MatchVerdict.CONFIRMED and item.state is not CandidateState.EXCLUDED:
                    return False
            return True
        if gap_type == "replacement_candidate_needed":
            if isinstance(run, CandidateSetStateSnapshot):
                return False  # Replacement requires the actual supplemental baseline.
            return any(
                item.core.uid != getattr(gap, "rejected_uid")
                and item.core.uid not in run.supplemental_baseline_uids
                and ("attraction" in candidate_admitted_purposes(item)
                     if getattr(gap, "role") is ResearchRole.ATTRACTION else
                     any(meal in candidate_admitted_purposes(item) for meal in ("lunch", "dinner")))
                for item in state.candidates
            )
        return False

    @staticmethod
    def _state_pending(state: CandidateSetStateSnapshot, gaps: Sequence[object]) -> CandidateSetStateSnapshot:
        return QualifiedCandidateSetStateSnapshot(**{**_python_model(state), "pending_gaps": tuple(gaps)})

    @staticmethod
    def _supply_entry(run: _Run, role: ResearchRole) -> tuple[bool, bool] | None:
        keys = (ResearchObjectiveKind.ATTRACTION_COUNT.value, ResearchObjectiveKind.INITIAL_CITY_ATTRACTION.value) if role is ResearchRole.ATTRACTION else (ResearchObjectiveKind.RESTAURANT_COUNT.value, ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY.value)
        values = [run.supply_search_results[key] for key in keys if key in run.supply_search_results]
        if not values:
            return None
        return any(item[0] for item in values), any(item[1] for item in values)

    def _failure_snapshot(self, failure: LLMResearchFailure | MapResearchFailure | ControlResearchFailure):
        if isinstance(failure, LLMResearchFailure):
            return ArtifactLLMResearchFailureSnapshot(failure_type="llm_failure", invocation_kind=failure.invocation_kind, error=ArtifactLLMErrorSnapshot(**_python_model(failure.error)))
        if isinstance(failure, MapResearchFailure):
            return ArtifactMapResearchFailureSnapshot(failure_type="map_failure", tool=failure.tool, action_ordinal=failure.action_ordinal, code=failure.code, capability=failure.capability, observation_ref=failure.observation_ref)
        return ArtifactControlResearchFailureSnapshot(failure_type="control_failure", code=failure.code)

    @staticmethod
    def _retryable(failure: LLMResearchFailure | MapResearchFailure | ControlResearchFailure) -> bool:
        if isinstance(failure, LLMResearchFailure):
            return failure.error.retryable
        if isinstance(failure, MapResearchFailure):
            return failure.code in {BaiduMapErrorCode.RATE_LIMIT_ERROR, BaiduMapErrorCode.TIMEOUT_ERROR, BaiduMapErrorCode.TRANSPORT_ERROR, BaiduMapErrorCode.SESSION_ERROR}
        return False

    @staticmethod
    def _duplicate(failure: LLMResearchFailure | MapResearchFailure | ControlResearchFailure) -> bool:
        if isinstance(failure, LLMResearchFailure):
            return failure.error.possible_duplicate
        if isinstance(failure, MapResearchFailure):
            return failure.code in {BaiduMapErrorCode.TIMEOUT_ERROR, BaiduMapErrorCode.TRANSPORT_ERROR}
        return failure.code in {CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE, CandidateResearchControlFailureCode.PERSISTENCE_ERROR}

    def _memory_persistence_failure(self, run: _Run) -> CandidateResearchFailed:
        run.indeterminate = True
        failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.PERSISTENCE_ERROR)
        return CandidateResearchFailed(failure=failure, last_consistent_candidate_set_ref=run.last_consistent_ref, failure_artifact_ref=None, budgets=self._public_budget(run.state.budgets), retryable=False, possible_duplicate=True)

    async def _active_failed(self, request, code) -> CandidateResearchFailed:
        """Preserve known consumption when failure happens after a run was started."""
        run = self._runs.get(request.operation_plan.run_id)
        if run is None:
            return self._zero_failed(code)
        if code is CandidateResearchControlFailureCode.REPLAY_MISMATCH:
            return await self._read_failure(request, code)
        try:
            return await self._failed(run, request, ControlResearchFailure(code=code), None)
        except _OperationReplayIndeterminate:
            return await self._indeterminate(run.run_id)
        except (ArtifactRepositoryError, ExecutionBudgetError,
                RuntimeError, ValueError, TypeError, AttributeError):
            # A failed terminal write cannot authorize replaying external work.
            return await self._read_failure(request, code)

    async def _read_failure(self, request, code) -> CandidateResearchFailed:
        if request.operation_plan.run_id not in self._runs:
            return self._zero_failed(code)
        # Read failures cannot authorize a new terminal write or provider call.
        known = await self._indeterminate(request.operation_plan.run_id)
        failure = ControlResearchFailure(code=code)
        return CandidateResearchFailed(failure=failure,
            last_consistent_candidate_set_ref=known.last_consistent_candidate_set_ref,
            failure_artifact_ref=None, budgets=known.budgets,
            retryable=False, possible_duplicate=self._duplicate(failure))

    async def _indeterminate(self, run_id: UUID) -> CandidateResearchFailed:
        run = self._runs.get(run_id)
        if run is not None:
            run.indeterminate = True
            try:
                run.state = await self._state_budget(run)
            except (ExecutionBudgetError, RuntimeError, ValueError, TypeError):
                run.provider_attempts_complete = False
                run.usage_complete = False
                budget = _python_model(run.state.budgets)
                known = {name: run.usage.get(name, 0) for name in ArtifactKnownLLMUsageSnapshot.model_fields}
                budget.update(semantic_invocations_consumed=run.semantic_invocations,
                    known_provider_attempts=run.provider_attempts, provider_attempts_complete=False,
                    known_usage=ArtifactKnownLLMUsageSnapshot(**known), usage_complete=False,
                    correction_limit=2, corrections_consumed=run.output_recoveries_used)
                run.state = QualifiedCandidateSetStateSnapshot(**{**_python_model(run.state),
                    "budgets": ArtifactResearchBudgetSummarySnapshot(**budget)})
        budgets = self._public_budget(run.state.budgets) if run is not None else ResearchBudgetSummary(iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0, semantic_invocation_limit=12, semantic_invocations_consumed=0, known_provider_attempts=0, provider_attempts_complete=True, known_usage=KnownLLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0), usage_complete=True)
        return CandidateResearchFailed(failure=ControlResearchFailure(code=CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE), last_consistent_candidate_set_ref=run.last_consistent_ref if run else None, failure_artifact_ref=None, budgets=budgets, retryable=False, possible_duplicate=True)

    @staticmethod
    def _zero_failed(code: CandidateResearchControlFailureCode) -> CandidateResearchFailed:
        return CandidateResearchFailed(
            failure=ControlResearchFailure(code=code),
            last_consistent_candidate_set_ref=None,
            failure_artifact_ref=None,
            budgets=ResearchBudgetSummary(
                iteration_limit=6, iterations_consumed=0, place_call_limit=48,
                place_calls_consumed=0, semantic_invocation_limit=12,
                semantic_invocations_consumed=0, known_provider_attempts=0,
                provider_attempts_complete=True,
                known_usage=KnownLLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0),
                usage_complete=True,
            ),
            retryable=False,
            possible_duplicate=code in {CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE, CandidateResearchControlFailureCode.PERSISTENCE_ERROR},
        )

    @staticmethod
    def _public_budget(value: ArtifactResearchBudgetSummarySnapshot) -> ResearchBudgetSummary:
        from .models import ResearchBudgetSummary
        return ResearchBudgetSummary(**_json_model(value))

    def _outcome_from_record(self, ref: ArtifactRef, record: object) -> CandidateResearchOutcome:
        if isinstance(record, CompletedCandidateSetRecord):
            return CandidateResearchCompleted(candidate_set_ref=ref, counts=self._public_counts(record.counts), coverage=self._public_coverage(record.coverage), budgets=self._public_budget(record.budgets))
        if isinstance(record, IncompleteCandidateSetRecord):
            from .models import ResearchGapSummary
            gaps = tuple(self._public_gap(item, ResearchGapSummary) for item in record.gaps)
            return CandidateResearchIncomplete(candidate_set_ref=ref, counts=self._public_counts(record.counts), coverage=self._public_coverage(record.coverage), budgets=self._public_budget(record.budgets), gaps=gaps, stop_reasons=record.stop_reasons)
        if isinstance(record, FailedCandidateSetRecord):
            failure = self._public_failure(record.failure)
            return CandidateResearchFailed(failure=failure, last_consistent_candidate_set_ref=record.last_consistent_candidate_set_ref, failure_artifact_ref=record.failure_artifact_ref, budgets=self._public_budget(record.budgets), retryable=record.retryable, possible_duplicate=record.possible_duplicate)
        raise ValueError("non-terminal record")

    @staticmethod
    def _public_failure(failure_snapshot: object) -> CandidateResearchFailure:
        """Rebuild the public failure DTO from its artifact-local snapshot.

        Artifact snapshots intentionally use JSON-facing literals for provider
        roles, while the public contract keeps the strict runtime enum.  The
        conversion is therefore explicit instead of relying on a non-strict
        union validation round-trip.
        """
        if isinstance(failure_snapshot, ArtifactLLMResearchFailureSnapshot):
            return LLMResearchFailure(
                invocation_kind=failure_snapshot.invocation_kind,
                error=CandidateResearchService._public_llm_error(failure_snapshot.error),
            )
        if isinstance(failure_snapshot, ArtifactMapResearchFailureSnapshot):
            return MapResearchFailure(
                tool=failure_snapshot.tool,
                action_ordinal=failure_snapshot.action_ordinal,
                code=failure_snapshot.code,
                capability=failure_snapshot.capability,
                observation_ref=failure_snapshot.observation_ref,
            )
        if isinstance(failure_snapshot, ArtifactControlResearchFailureSnapshot):
            return ControlResearchFailure(code=failure_snapshot.code)
        raise ValueError("unknown artifact failure snapshot")

    @staticmethod
    def _public_llm_error(error: ArtifactLLMErrorSnapshot) -> LLMRuntimeError:
        return LLMRuntimeError(
            code=error.code,
            role=ModelRole(error.role),
            retryable=error.retryable,
            possible_duplicate=error.possible_duplicate,
            provider_status_code=error.provider_status_code,
            retry_after_seconds=error.retry_after_seconds,
            actual_bytes=error.actual_bytes,
            max_bytes=error.max_bytes,
        )

    @staticmethod
    def _public_gap(snapshot: object, gap_type: object) -> object:
        tree = _python_model(snapshot)
        anchor = tree.get("anchor")
        if isinstance(anchor, dict) and anchor.get("anchor_type") == "hotel":
            anchor.pop("uid", None)
        return TypeAdapter(gap_type).validate_python(tree, strict=True)

    @staticmethod
    def _public_counts(value: ArtifactResearchCountSummarySnapshot) -> ResearchCountSummary:
        from .models import ResearchCountSummary
        return ResearchCountSummary(**_python_model(value))

    @staticmethod
    def _public_coverage(value: ArtifactResearchCoverageSummarySnapshot) -> ResearchCoverageSummary:
        from .models import ResearchCoverageSummary
        return ResearchCoverageSummary(**_python_model(value))


__all__ = ("CandidateResearchService",)
