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
    ArtifactPlaceDetailsSnapshot,
    ArtifactPlaceSummarySnapshot,
    ArtifactProvenanceSnapshot,
    ArtifactMustVisitResolutionMissingSummary,
    ArtifactAvoidResolutionMissingSummary,
    ArtifactReplacementCandidateNeededSummary,
    ArtifactResearchAnchorSnapshot,
    ArtifactResearchBudgetSummarySnapshot,
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
    CandidateSetStateSnapshot,
    CompletedCandidateSetRecord,
    FailedCandidateSetRecord,
    IncompleteCandidateSetRecord,
    InProgressCandidateSetRecord,
    JudgmentPatchSnapshot,
    JudgmentPatchModelSnapshot,
    ModelActionAcceptedRecord,
    ModelActionControllerRejectedRecord,
    ModelActionLLMFailureRecord,
    ModelActionPolicyRejectedRecord,
    ModelJudgmentAcceptedRecord,
    ModelJudgmentControllerRejectedRecord,
    ModelJudgmentLLMFailureRecord,
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
from .fingerprints import (
    action_fingerprint,
    candidate_handle,
    content_digest,
    details_action_fingerprint,
    invocation_request_digest,
    objective_fingerprint,
    requirement_group_id,
    requirement_item_id,
    supplemental_gap_fingerprint,
    search_action_fingerprint,
)
from .judgments import evidence_handle_indexes, validate_judgment_patch
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


class _ModelOperationPort(Protocol):
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
    usage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    closed: bool = False
    predecessor_ref: ArtifactRef | None = None
    last_consistent_ref: ArtifactRef | None = None
    indeterminate: bool = False
    requirement_specs: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    objective_directory: dict[str, ResearchObjective] = field(default_factory=dict)
    gap_fingerprints: set[str] = field(default_factory=set)
    supply_search_results: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    last_judgment_patch: JudgmentPatch | None = None
    last_judgment_metadata: ArtifactInvocationMetadataSnapshot | None = None
    supplemental_baseline_uids: frozenset[str] = frozenset()
    replayed_place_refs: dict[OperationId, ArtifactRef] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _MapResult:
    action: ProposedAction
    outcome: object | None
    error: BaiduMapError | None
    contract_failure: bool = False


@dataclass(frozen=True, slots=True)
class _PolicyReplay:
    """Marker for a persisted policy rejection that must replay as no-progress."""


@dataclass(frozen=True, slots=True)
class _JudgmentReplayIndeterminate:
    """Marker for a found judgment artifact that cannot be proven replayable."""


class _OperationReplayIndeterminate(RuntimeError):
    """Internal signal for same-operation artifacts whose content conflicts."""


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
            return self._zero_failed(CandidateResearchControlFailureCode.ARTIFACT_ERROR)
        except (RuntimeError, TypeError, ValueError):
            return self._zero_failed(CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
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
        except (ValidationError, ValueError):
            existing_state = self._runs.get(request.operation_plan.run_id)
            code = CandidateResearchControlFailureCode.REPLAY_MISMATCH if existing_state is not None else CandidateResearchControlFailureCode.INVALID_INPUT
            result = self._zero_failed(code)
            if not future.done():
                future.set_result(result)
            return result
        except ArtifactRepositoryError:
            result = self._zero_failed(CandidateResearchControlFailureCode.ARTIFACT_ERROR)
            if not future.done():
                future.set_result(result)
            return result
        except ExecutionBudgetError:
            result = self._zero_failed(CandidateResearchControlFailureCode.BUDGET_ERROR)
            if not future.done():
                future.set_result(result)
            return result
        except (RuntimeError, TypeError, AttributeError):
            result = self._zero_failed(CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
            if not future.done():
                future.set_result(result)
            return result
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            result = self._zero_failed(CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
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
            or lookup.ref.schema_version != "1.0"
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
            return self._zero_failed(CandidateResearchControlFailureCode.PROMPT_RESOURCE_INVALID)
        if mode is ResearchMode.SUPPLEMENTAL and request.operation_plan.run_id not in self._runs:
            return self._zero_failed(CandidateResearchControlFailureCode.RUN_CONTEXT_MISSING)
        if request.operation_plan.run_id in self._closed_run_ids and request.operation_plan.run_id not in self._runs:
            return self._zero_failed(CandidateResearchControlFailureCode.RUN_CONTEXT_MISSING)
        run = await self._load_or_create_run(request, mode, digest)
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
                return self._zero_failed(CandidateResearchControlFailureCode.BUDGET_ERROR)
            if not self._valid_budget_snapshot(remaining_snapshot, run.run_id, self._iteration_key()):
                return self._zero_failed(CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
            if len(slots) != remaining_snapshot.remaining:
                return self._zero_failed(CandidateResearchControlFailureCode.OPERATION_PLAN_INVALID)
            if all(self._gap_satisfied(run, gap) for gap in request.gaps):
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="no_op")
        if self._is_complete(run.state, request):
            transition = "no_op" if mode is ResearchMode.SUPPLEMENTAL else "normal"
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind=transition)
        if mode is ResearchMode.SUPPLEMENTAL:
            if run.predecessor_ref is None:
                return await self._indeterminate(run.run_id)
            try:
                remaining = (await self._budget.get_snapshot(key=self._iteration_key())).remaining
            except ExecutionBudgetError:
                return self._zero_failed(CandidateResearchControlFailureCode.BUDGET_ERROR)
            if len(slots) != remaining:
                return self._zero_failed(CandidateResearchControlFailureCode.OPERATION_PLAN_INVALID)
        objectives = self._objectives(run, request)
        for local_slot, slot in enumerate(slots):
            if self._is_complete(run.state, request):
                transition = "no_op" if mode is ResearchMode.SUPPLEMENTAL else "normal"
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind=transition)
            if not objectives:
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
            result = await self._iteration(run, request, slot, local_slot, objectives)
            if result is not None:
                return result
            if self._is_complete(run.state, request):
                transition = "no_op" if mode is ResearchMode.SUPPLEMENTAL else "normal"
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind=transition)
            objectives = self._objectives(run, request)
        return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")

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
                    raise ValueError("supplemental predecessor is not current head")
                predecessor_state = await self._read_predecessor_state(request.predecessor_candidate_set_ref, run_id)
                self._validate_supplemental_evidence(request, predecessor_state)
                for gap in request.gaps:
                    fingerprint = supplemental_gap_fingerprint(
                        current.confirmed_digest,
                        current.hotel_ref.content_hash,
                        gap,
                    )
                    if fingerprint in current.gap_fingerprints:
                        raise ValueError("supplemental gap fingerprint already consumed")
                    current.gap_fingerprints.add(fingerprint)
                current.request_digest = digest
                current.mode = ResearchMode.SUPPLEMENTAL
                current.predecessor_ref = request.predecessor_candidate_set_ref
                current.state = predecessor_state
                current.last_consistent_ref = request.predecessor_candidate_set_ref
                current.supplemental_baseline_uids = frozenset(item.core.uid for item in predecessor_state.candidates)
                return current
            raise ValueError("run request conflict")
        if mode is ResearchMode.INITIAL:
            confirmed = request.confirmed
            preferences = confirmed.requirement.preferences
            supplied = preferences.interests
            if supplied is None:
                if not preferences.agent_decides_interests:
                    raise ValueError("effective interests are not determined")
                effective = tuple(InterestTag)
            else:
                effective = tuple(supplied)
                if not 2 <= len(effective) <= 4 or len(set(effective)) != len(effective):
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
            operation = self._runtime.start_operation(ModelRole.CANDIDATE_RESEARCH)
            run = _Run(
                run_id=run_id, request_digest=digest, mode=mode, hotel_ref=hotel_ref,
                confirmed_digest=self._safe_digest(confirmed.confirmation_summary_digest, confirmed),
                destination=destination.strip(), effective_interests=effective,
                interests_frozen=supplied is not None or not preferences.agent_decides_interests,
                agent_decides_interests=preferences.agent_decides_interests and supplied is None,
                operation=operation, state=state,
                requirement_specs=self._requirement_specs(confirmed, self._safe_digest(confirmed.confirmation_summary_digest, confirmed)),
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
            or ref.schema_version != "1.0"
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
                    or item_ref.schema_version != "1.0"
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
                if isinstance(content, CandidateResearchModelActionArtifact) and isinstance(content.result, ModelActionAcceptedRecord):
                    if any(
                        disposition.reused_observation_ref is not None
                        and disposition.reused_observation_ref not in observation_refs
                        for disposition in content.result.action_dispositions
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

    async def _validate_terminal_refs(self, artifact: CandidateResearchArtifact) -> None:
        """Re-read terminal-linked artifacts so replay cannot trust stale refs."""
        record = artifact.record
        state = getattr(record, "state", None)
        if state is not None:
            # Supplemental terminal states legitimately contain the predecessor
            # directories (initial mode) plus newly produced supplemental refs;
            # the artifact mode alone cannot be imposed on every nested entry.
            await self._validate_state_refs(state, artifact.run_id)
        if artifact.predecessor_ref is not None:
            predecessor = await self._read_candidate_set(artifact.predecessor_ref, artifact.run_id)
            predecessor_state = getattr(predecessor.content.record, "state", None)
            if predecessor_state is None:
                raise ValueError("terminal predecessor has no state")
            await self._validate_state_refs(predecessor_state, artifact.run_id)

        async def verify(ref: ArtifactRef, expected_type: str, expected_model: type[BaseModel]) -> BaseModel:
            if (
                ref.artifact_type != expected_type
                or ref.schema_version != "1.0"
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
    ) -> None:
        observation_refs = set(state.observation_refs)
        by_ref = {ref: index for index, ref in enumerate(state.observation_refs)}
        for gap in request.gaps:
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
            usage_complete=True,
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
        return CandidateSetStateSnapshot(
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
                    objective_id=oid, kind=kind, gap_id=gap.gap_id, requirement_group_id=getattr(gap, "requirement_group_id", None),
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
                    if candidate.admission_ordinal not in group.evaluated_candidate_ordinals:
                        continue
                    matches = candidate.must_matches if requirement_kind == "must_visit" else candidate.avoid_matches
                    match = next((item for item in matches if item.requirement_group_id == group_id), None)
                    verdict = match.verdict if match is not None else group.default_verdict
                    if verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED}:
                        unresolved.append(candidate.core.uid)
                unresolved_uids = tuple(unresolved)
            if requirement_kind == "must_visit":
                satisfied = group is not None and any(
                    candidate.state is CandidateState.ELIGIBLE
                    and any(match.requirement_group_id == group_id and match.verdict is MatchVerdict.CONFIRMED for match in candidate.must_matches)
                    for candidate in run.state.candidates
                )
                if satisfied:
                    continue
                kind = ResearchObjectiveKind.MUST_VISIT_RESOLUTION
            else:
                if not unresolved_uids:
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
        missing_details = tuple(item.core.uid for item in run.state.candidates if item.state is CandidateState.ELIGIBLE and item.details.status == "not_requested")
        if missing_details:
            objectives.append(self._objective(run, ResearchObjectiveKind.DETAILS_MATURITY, candidate_uids=missing_details[:128]))
        ordered = deterministic_objectives(objectives)
        run.objective_directory = {item.objective_id: item for item in ordered}
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
        if run.semantic_invocations >= 12:
            return await self._failed(
                run,
                request,
                ControlResearchFailure(code=CandidateResearchControlFailureCode.BUDGET_ERROR),
                None,
            )
        if action_outcome is None:
            prompt = build_action_prompt(self._action_context(run, objectives))
            action_outcome = await self._invoke_action(run, prompt)
        if isinstance(action_outcome, tuple) and isinstance(action_outcome[0], _PolicyReplay):
            run.state = self._state_with(run, no_progress=min(2, run.state.iteration_no_progress_streak + 1))
            if run.state.iteration_no_progress_streak >= 2:
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
            await persist_intermediate_if_needed()
            return None
        if isinstance(action_outcome, LLMResearchFailure):
            action_ref = await self._write_action_artifact(run, request, slot, iteration_index, action_outcome, None)
            return await self._failed(run, request, action_outcome, action_ref)
        if isinstance(action_outcome, ControlResearchFailure):
            if replay_ref is not None:
                return await self._failed(run, request, action_outcome, replay_ref)
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
            if not await self._no_useful_action_is_legal(run, output, objectives):
                action_record = ModelActionControllerRejectedRecord(record_type="controller_rejected", output=self._action_output_snapshot(output), metadata=metadata, code="action_batch_invalid")
                action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
                self._remember_action_ref(run, action_ref)
                return await self._failed(run, request, ControlResearchFailure(code=CandidateResearchControlFailureCode.ACTION_BATCH_INVALID), action_ref)
            action_record = ModelActionAcceptedRecord(record_type="accepted", output=self._action_output_snapshot(output), metadata=metadata, action_dispositions=())
            action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
            self._remember_action_ref(run, action_ref)
            run.state = self._state_with(run, no_progress=min(2, run.state.iteration_no_progress_streak + 1))
            if run.state.iteration_no_progress_streak >= 2:
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
            await persist_intermediate_if_needed()
            return None
        actions = output.actions
        validation = self._validate_actions(run, actions, objectives, slot)
        if validation is not None:
            action_record = ModelActionControllerRejectedRecord(record_type="controller_rejected", output=self._action_output_snapshot(output), metadata=metadata, code="action_batch_invalid")
            action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
            self._remember_action_ref(run, action_ref)
            failure = ControlResearchFailure(code=CandidateResearchControlFailureCode.ACTION_BATCH_INVALID)
            return await self._failed(run, request, failure, action_ref)
        highest = objectives[0].objective_id if objectives else None
        if highest is not None and not any(item.objective_id == highest for item in actions):
            action_record = ModelActionPolicyRejectedRecord(record_type="policy_rejected", output=self._action_output_snapshot(output), metadata=metadata, reason_code="highest_priority_objective_ignored")
            action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
            self._remember_action_ref(run, action_ref)
            run.state = self._state_with(run, no_progress=min(2, run.state.iteration_no_progress_streak + 1))
            if run.state.iteration_no_progress_streak >= 2:
                return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
            await persist_intermediate_if_needed()
            return None
        dispositions: list[ActionDispositionSnapshot] = []
        fresh_actions: list[ProposedAction] = []
        for action in actions:
            fp = action_fingerprint(action, run.destination)
            previous = run.fingerprints.get(fp)
            if previous is not None:
                dispositions.append(ActionDispositionSnapshot(action_ordinal=action.ordinal, action_fingerprint=fp, disposition="duplicate_action", reused_observation_ref=previous))
            else:
                fresh_actions.append(action)
                dispositions.append(ActionDispositionSnapshot(action_ordinal=action.ordinal, action_fingerprint=fp, disposition="accepted", reused_observation_ref=None))
        remaining_place = await self._place_remaining(run)
        if remaining_place < 0:
            action_record = ModelActionAcceptedRecord(record_type="accepted", output=self._action_output_snapshot(output), metadata=metadata, action_dispositions=tuple(dispositions))
            action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
            self._remember_action_ref(run, action_ref)
            return await self._failed(
                run,
                request,
                ControlResearchFailure(
                    code=(
                        CandidateResearchControlFailureCode.BUDGET_ERROR
                        if remaining_place == -1
                        else CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR
                    )
                ),
                action_ref,
            )
        allowed_fresh = fresh_actions[:remaining_place]
        allowed_ids = {id(item) for item in allowed_fresh}
        for position, action in enumerate(actions):
            if action in fresh_actions and id(action) not in allowed_ids:
                dispositions[position] = ActionDispositionSnapshot(action_ordinal=action.ordinal, action_fingerprint=dispositions[position].action_fingerprint, disposition="not_admitted_budget_exhausted", reused_observation_ref=None)
        action_record = ModelActionAcceptedRecord(record_type="accepted", output=self._action_output_snapshot(output), metadata=metadata, action_dispositions=tuple(dispositions))
        action_ref = await self._write_model_action(run, request, slot, iteration_index, action_record)
        self._remember_action_ref(run, action_ref)
        if not fresh_actions:
            run.state = self._state_with(run, no_progress=min(2, run.state.iteration_no_progress_streak + 1))
            await persist_intermediate_if_needed()
            return None
        place_actions = allowed_fresh[: len(slot.place_observation_operation_ids)]
        if not place_actions:
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
        reservation = await self._reserve_places(run, slot, place_actions)
        if reservation[0] == -2:
            return await self._indeterminate(run.run_id)
        if reservation[0] == -1:
            for action, operation_id in zip(place_actions[: reservation[1]], slot.place_observation_operation_ids):
                try:
                    ref = await self._write_not_issued(run, slot, iteration_index, action, operation_id)
                except ArtifactRepositoryError:
                    return await self._failed(run, request, ControlResearchFailure(code=CandidateResearchControlFailureCode.ARTIFACT_ERROR), action_ref)
                run.observation_refs.append(ref)
            return await self._failed(run, request, ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR), action_ref)
        if reservation[0] == -3:
            for action, operation_id in zip(place_actions[: reservation[1]], slot.place_observation_operation_ids):
                try:
                    ref = await self._write_not_issued(run, slot, iteration_index, action, operation_id)
                except ArtifactRepositoryError:
                    return await self._failed(run, request, ControlResearchFailure(code=CandidateResearchControlFailureCode.ARTIFACT_ERROR), action_ref)
                run.observation_refs.append(ref)
            return await self._failed(run, request, ControlResearchFailure(code=CandidateResearchControlFailureCode.BUDGET_ERROR), action_ref)
        if reservation[0] == 0:
            return await self._finish(run, request, request.operation_plan.terminal_successor_operation_id, transition_kind="normal")
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
                run.state = CandidateSetStateSnapshot(**{**_python_model(run.state), "observation_refs": tuple(run.observation_refs)})
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
            run.state = CandidateSetStateSnapshot(**{**_python_model(run.state), "observation_refs": tuple(run.observation_refs)})
            run.fingerprints[action_fingerprint(action, run.destination)] = obs
            new_observation = new_observation or (not already_in_state and isinstance(mapped.outcome, (PlaceSearchSuccess, PlaceSearchNoResult, PlaceDetailsSuccess, PlaceDetailsNoResult)))
            if failure is not None and primary_failure is None:
                primary_failure = failure
                primary_failure_ref = obs
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
            run.state = CandidateSetStateSnapshot(**{**_python_model(pre_wave_state), "observation_refs": tuple(run.observation_refs)})
            failure_ref = primary_failure_ref
            return await self._failed(run, request, primary_failure, failure_ref)
        if new_observation:
            judgment = await self._judgment(run, request, slot, iteration_index)
            if isinstance(judgment, _JudgmentReplayIndeterminate):
                return await self._indeterminate(run.run_id)
            if isinstance(judgment, LLMResearchFailure):
                run.state = CandidateSetStateSnapshot(**{**_python_model(pre_wave_state), "observation_refs": tuple(run.observation_refs)})
                ref = await self._write_judgment_failure(run, request, slot, iteration_index, judgment)
                return await self._failed(run, request, judgment, ref)
            if isinstance(judgment, ControlResearchFailure):
                run.state = CandidateSetStateSnapshot(**{**_python_model(pre_wave_state), "observation_refs": tuple(run.observation_refs)})
                if judgment.code is not CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID:
                    return await self._failed(run, request, judgment, None)
                ref = await self._write_judgment_failure(run, request, slot, iteration_index, judgment)
                return await self._failed(run, request, judgment, ref)
        progressed = self._state_progressed(pre_iteration_state, run.state)
        run.state = self._refresh_state(run.state, no_progress=0 if progressed else min(2, run.state.iteration_no_progress_streak + 1))
        if local_slot < len(request.operation_plan.iteration_slots) - 1:
            await self._write_in_progress(run, request, slot)
        return None

    @staticmethod
    def _action_context(run: _Run, objectives: Sequence[ResearchObjective]) -> dict[str, object]:
        """Build the typed, normalized Action context without raw payloads."""
        requirements = []
        groups = {group.group_id: group for group in run.state.requirement_group_states}
        for kind, group_id, item_ids in run.requirement_specs:
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
                "requirement_kind": kind,
                "requirement_group_id": group_id,
                "requirement_item_ids": item_ids,
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
            "destination": run.destination,
            "effective_interests": tuple(item.value for item in run.effective_interests),
            "interests_locked": run.interests_frozen,
            "agent_may_select_interests": run.agent_decides_interests,
            "objectives": tuple(_json_model(item) for item in objectives),
            "requirements": tuple(requirements),
            "candidates": tuple(candidates),
        }

    @staticmethod
    def _state_progressed(before: CandidateSetStateSnapshot, after: CandidateSetStateSnapshot) -> bool:
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
        run.state = CandidateSetStateSnapshot(**{**_python_model(run.state), "effective_interests": effective, "coverage": coverage})
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
            return remaining == 0 or not any(objective.kind in {ResearchObjectiveKind.ATTRACTION_COUNT, ResearchObjectiveKind.RESTAURANT_COUNT, ResearchObjectiveKind.DETAILS_MATURITY, ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY, ResearchObjectiveKind.MEAL_LUNCH, ResearchObjectiveKind.MEAL_DINNER, ResearchObjectiveKind.INTEREST_COVERAGE} for objective in objectives)
        if output.reason_code == "no_legal_action":
            if not objectives:
                return False
            for objective in objectives:
                if objective.kind in {ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY, ResearchObjectiveKind.MEAL_LUNCH, ResearchObjectiveKind.MEAL_DINNER, ResearchObjectiveKind.INTEREST_COVERAGE}:
                    return False
                if objective.kind in {ResearchObjectiveKind.ATTRACTION_COUNT, ResearchObjectiveKind.RESTAURANT_COUNT} and run.state.counts.admitted_slots < 128:
                    return False
                if objective.kind is ResearchObjectiveKind.DETAILS_MATURITY and any(item.details.status == "not_requested" for item in run.state.candidates if item.state is CandidateState.ELIGIBLE):
                    return False
            return True
        return False

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
        if ref.artifact_type != "candidate_research.model_action" or ref.schema_version != "1.0" or ref.operation_id != operation_id or ref.operation_id.run_id != run.run_id:
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
        record = artifact.result
        if ref not in run.action_refs:
            self._account_replayed_metadata(run, artifact.invocation_index, getattr(record, "metadata", None))
        if isinstance(record, ModelActionLLMFailureRecord):
            error = self._public_llm_error(record.error)
            return artifact.iteration_index, LLMResearchFailure(invocation_kind=ResearchInvocationKind.ACTION, error=error), ref
        if isinstance(record, ModelActionControllerRejectedRecord):
            return artifact.iteration_index, ControlResearchFailure(code=CandidateResearchControlFailureCode.ACTION_BATCH_INVALID), ref
        if isinstance(record, ModelActionPolicyRejectedRecord):
            return artifact.iteration_index, (_PolicyReplay(), record.metadata), ref
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

    async def _invoke_action(self, run: _Run, prompt: PromptEnvelope) -> tuple[object, ArtifactInvocationMetadataSnapshot] | LLMResearchFailure | ControlResearchFailure:
        try:
            result = await run.operation.ainvoke_structured(prompt, _ActionModelEnvelope)
        except (RuntimeError, TypeError, ValueError, AttributeError):
            run.semantic_invocations += 1
            run.provider_attempts_complete = False
            run.usage_complete = False
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            run.semantic_invocations += 1
            run.provider_attempts_complete = False
            run.usage_complete = False
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
        if isinstance(result, LLMInvocationFailure):
            cost = self._semantic_cost(result.error.code)
            run.semantic_invocations += cost
            if cost:
                run.provider_attempts_complete = False
                run.usage_complete = False
            return LLMResearchFailure(invocation_kind=ResearchInvocationKind.ACTION, error=result.error)
        if not isinstance(result, StructuredInvocationSuccess):
            run.provider_attempts_complete = False
            run.usage_complete = False
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
        run.semantic_invocations += 1
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
        fingerprints = tuple(action_fingerprint(action, run.destination) for action in actions)
        if len(set(fingerprints)) != len(fingerprints):
            return "duplicate"
        known_uids = {item.core.uid for item in run.state.candidates}
        if tuple(actions[index].ordinal for index in range(len(actions))) != tuple(range(len(actions))):
            return "ordinal"
        for action in actions:
            objective = by_id.get(action.objective_id)
            if objective is None:
                return "objective"
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
                if objective.kind is not ResearchObjectiveKind.DETAILS_MATURITY and objective.candidate_uids and action.uid not in objective.candidate_uids:
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
        return await self._put(run, operation_id, "candidate_research.place_observation", artifact)

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

    async def _judgment(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, slot: ResearchIterationOperationIds, iteration_index: int) -> LLMResearchFailure | ControlResearchFailure | _JudgmentReplayIndeterminate | None:
        def candidate_evidence_directory() -> dict[str, tuple[str, ...]]:
            return {
                candidate_handle(item.admission_ordinal): tuple(
                    f"e:{index}" for index in item.observation_indexes
                )
                for item in run.state.candidates
            }

        existing = await self._repository.lookup(operation_id=slot.judgment_operation_id)
        if isinstance(existing, ArtifactFound):
            ref = existing.ref
            if (
                ref.artifact_type != "candidate_research.model_judgment"
                or ref.schema_version != "1.0"
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
                or _digest(_json_model(artifact)) != ref.content_hash
            ):
                return _JudgmentReplayIndeterminate()
            record = artifact.result
            if ref not in run.judgment_refs:
                self._account_replayed_metadata(run, artifact.invocation_index, getattr(record, "metadata", None))
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
                return ControlResearchFailure(code=CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID)
            if not isinstance(record, ModelJudgmentAcceptedRecord):
                return _JudgmentReplayIndeterminate()
            try:
                patch = self._public_judgment_patch(record.patch)
                visible_candidates = tuple(candidate_handle(item.admission_ordinal) for item in run.state.candidates)
                visible_evidence = tuple(f"e:{index}" for index in range(len(run.observation_refs)))
                patch = validate_judgment_patch(
                    patch=patch,
                    candidate_handles=visible_candidates,
                    evidence_handles=visible_evidence,
                    candidate_evidence_handles=candidate_evidence_directory(),
                )
            except (ValidationError, TypeError, ValueError):
                return _JudgmentReplayIndeterminate()
            run.last_judgment_patch = patch
            run.last_judgment_metadata = record.metadata
            run.state = self._apply_patch(run.state, patch)
            return None
        if not isinstance(existing, ArtifactAbsent):
            return _JudgmentReplayIndeterminate()
        if run.semantic_invocations >= 12:
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.BUDGET_ERROR)
        refs = tuple(run.observation_refs)
        try:
            prompt_payload = await self._judgment_context(
                run, refs, current_iteration_index=iteration_index
            )
        except ArtifactRepositoryError:
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.ARTIFACT_ERROR)
        prompt = build_observation_prompt(prompt_payload)
        try:
            result = await run.operation.ainvoke_structured(prompt, JudgmentPatch)
        except (RuntimeError, TypeError, ValueError, AttributeError):
            run.semantic_invocations += 1
            run.provider_attempts_complete = False
            run.usage_complete = False
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            run.semantic_invocations += 1
            run.provider_attempts_complete = False
            run.usage_complete = False
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
        if isinstance(result, LLMInvocationFailure):
            cost = self._semantic_cost(result.error.code)
            run.semantic_invocations += cost
            if cost:
                run.provider_attempts_complete = False
                run.usage_complete = False
            return LLMResearchFailure(invocation_kind=ResearchInvocationKind.JUDGMENT, error=result.error)
        if not isinstance(result, StructuredInvocationSuccess):
            run.provider_attempts_complete = False
            run.usage_complete = False
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
        run.semantic_invocations += 1
        run.provider_attempts += result.metadata.provider_attempts
        self._add_usage(run, result.metadata)
        run.last_judgment_metadata = self._metadata(result.metadata)
        run.last_judgment_patch = None
        try:
            patch = TypeAdapter(JudgmentPatch).validate_json(json.dumps(_json_model(result.value), ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
            run.last_judgment_patch = patch
            visible_candidates = tuple(candidate_handle(item.admission_ordinal) for item in run.state.candidates)
            visible_evidence = tuple(f"e:{index}" for index in range(len(refs)))
            patch = validate_judgment_patch(
                patch=patch,
                candidate_handles=visible_candidates,
                evidence_handles=visible_evidence,
                candidate_evidence_handles=candidate_evidence_directory(),
            )
            known_groups = {group.group_id for group in run.state.requirement_group_states}
            if any(item.requirement_group_id not in known_groups for item in patch.requirement_group_updates):
                raise ValueError("unknown requirement group")
        except (ValidationError, ValueError, TypeError):
            if run.last_judgment_patch is None:
                return ControlResearchFailure(code=CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR)
            return ControlResearchFailure(code=CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID)
        resolved = self._resolve_patch(patch, refs, run.state.requirement_group_states)
        artifact = CandidateResearchModelJudgmentArtifact(schema_version="1.0", run_id=run.run_id, mode=run.mode, invocation_index=run.semantic_invocations - 1, invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index, prompt_version=OBSERVATION_PROMPT_VERSION, prompt_sha256=OBSERVATION_PROMPT_CONTENT_DIGEST, evidence_refs=refs, result=ModelJudgmentAcceptedRecord(record_type="accepted", patch=resolved, metadata=self._metadata(result.metadata)))
        ref = await self._put(run, slot.judgment_operation_id, "candidate_research.model_judgment", artifact)
        self._remember_judgment_ref(run, ref)
        run.state = self._apply_patch(run.state, patch)
        return None

    async def _judgment_context(
        self,
        run: _Run,
        refs: Sequence[ArtifactRef],
        *,
        current_iteration_index: int,
    ) -> dict[str, object]:
        """Expose only normalized observation facts and candidate context."""
        evidence: list[dict[str, object]] = []
        current_evidence_handles: list[str] = []
        search_by_observation = {
            record.observation_index: record for record in run.state.search_coverage.records
        }
        for index, ref in enumerate(refs):
            read = await self._repository.get(ref, expected_model=CandidateResearchPlaceObservationArtifact)
            if read.ref != ref or read.content.run_id != run.run_id or _digest(_json_model(read.content)) != ref.content_hash:
                raise ArtifactRepositoryError()
            handle = f"e:{index}"
            if read.content.iteration_index != current_iteration_index:
                continue
            current_evidence_handles.append(handle)
            search = search_by_observation.get(index)
            accepted_search = None
            if search is not None:
                accepted_search = {
                    "objective_kind": search.objective_kind.value,
                    "role": (
                        search.role.value
                        if search.role is not None
                        else ResearchRole.ATTRACTION.value
                        if search.objective_kind is ResearchObjectiveKind.INTEREST_COVERAGE
                        else None
                    ),
                    "interest": search.interest.value if search.interest is not None else None,
                    "meal_intent": search.meal_intent.value if search.meal_intent is not None else None,
                }
            evidence.append({
                "handle": handle,
                "action_ordinal": read.content.action_ordinal,
                "tool": read.content.tool.value,
                "action_fingerprint": read.content.action_fingerprint,
                "accepted_search": accepted_search,
                "result": _json_model(read.content.result),
            })
        candidates = tuple({
            "handle": candidate_handle(item.admission_ordinal),
            "uid": item.core.uid,
            "name": item.core.name,
            "address": item.core.address,
            "coordinate": _json_model(item.core.coordinate),
            "provider_category": item.core.provider_category,
            "evidence_handles": tuple(f"e:{index}" for index in item.observation_indexes),
            "state": item.state.value,
            "roles": tuple({"role": role.role.value, "verdict": role.verdict.value} for role in item.roles),
            "interests": tuple(value.value for value in item.interests),
            "meal_intents": tuple(value.value for value in item.meal_intents),
            "details": {"status": item.details.status, "observation_index": item.details.observation_index},
        } for item in run.state.candidates)
        requirements = tuple({
            "requirement_kind": kind,
            "requirement_group_id": group_id,
            "requirement_item_ids": item_ids,
        } for kind, group_id, item_ids in run.requirement_specs)
        return {
            "candidates": candidates,
            "requirements": requirements,
            "evidence": tuple(evidence),
            "current_evidence_handles": tuple(current_evidence_handles),
        }

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
            meals = tuple(sorted(set((*item.meal_intents, update.meal_intent)) if update.verdict is MatchVerdict.CONFIRMED else set(item.meal_intents) - {update.meal_intent}, key=lambda x: list(MealIntent).index(x)))
            candidates[ordinal] = self._replace_candidate(item, meal_intents=meals)
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
            groups[update.requirement_group_id] = ArtifactRequirementGroupState(
                group_id=update.requirement_group_id,
                requirement_kind=update.requirement_kind,
                requirement_item_ids=next((item.requirement_item_ids for item in groups.values() if item.group_id == update.requirement_group_id), (update.requirement_group_id,)),
                evaluated_candidate_ordinals=evaluated,
                default_verdict=update.default_verdict,
                default_reason_code=update.default_reason_code,
                default_evidence_indexes=evidence_handle_indexes(update.default_evidence_handles, visible_evidence),
                exceptions=exceptions,
            )
        if patch.requirement_group_updates:
            for ordinal, item in enumerate(candidates):
                for group in groups.values():
                    if ordinal not in group.evaluated_candidate_ordinals:
                        continue
                    override = next((value for value in group.exceptions if value.candidate_ordinal == ordinal), None)
                    verdict = override.verdict if override is not None else group.default_verdict
                    reason = override.reason_code if override is not None else group.default_reason_code
                    evidence = override.evidence_indexes if override is not None else group.default_evidence_indexes
                    match = ArtifactRequirementMatchSnapshot(requirement_group_id=group.group_id, verdict=verdict, reason_code=reason, evidence_indexes=evidence)
                    if group.requirement_kind == "must_visit":
                        item = self._replace_candidate(item, must_matches=tuple((*[value for value in item.must_matches if value.requirement_group_id != group.group_id], match)))
                    else:
                        item = self._replace_candidate(item, avoid_matches=tuple((*[value for value in item.avoid_matches if value.requirement_group_id != group.group_id], match)))
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
        return ArtifactCandidateRecordSnapshot(**tree)

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
            candidate = ArtifactCandidateRecordSnapshot(schema_version="1.0", admission_ordinal=len(candidates), core=ArtifactCandidateCoreSnapshot(uid=place.uid, name=place.name, address=place.address, coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=place.coordinate.latitude, longitude=place.coordinate.longitude), provider_category=place.category), state=CandidateState.WITHHELD, roles=roles, interests=(), meal_intents=(), must_matches=(), avoid_matches=(), details=ArtifactCandidateDetailsStateSnapshot(status="not_requested", observation_index=None), observation_indexes=(index,), dietary_restrictions="not_evaluated", mobility_constraints="not_evaluated", budget_fit="not_evaluated")
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
        def provider_coordinate(value: float) -> str:
            # Baidu place-details currently returns six decimal places while
            # place-search may return additional binary-float precision.
            return format(value, ".6f")

        return (
            left.uid == right.uid
            and left.name == right.name
            and left.address == right.address
            and left.coordinate.coordinate_system == right.coordinate.coordinate_system
            and provider_coordinate(left.coordinate.latitude) == provider_coordinate(right.coordinate.latitude)
            and provider_coordinate(left.coordinate.longitude) == provider_coordinate(right.coordinate.longitude)
            and left.provider_category == right.provider_category
        )

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
        if not run.requirement_specs:
            return state
        groups = {group.group_id: group for group in state.requirement_group_states}
        candidates = list(state.candidates)
        for kind, group_id, item_ids in run.requirement_specs:
            current = groups.get(group_id)
            if current is None:
                current = ArtifactRequirementGroupState(group_id=group_id, requirement_kind=kind, requirement_item_ids=item_ids, evaluated_candidate_ordinals=tuple(item.admission_ordinal for item in candidates), default_verdict=MatchVerdict.UNRESOLVED, default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT, default_evidence_indexes=(evidence_index,), exceptions=())
            else:
                current = ArtifactRequirementGroupState(**{**_python_model(current), "evaluated_candidate_ordinals": tuple(item.admission_ordinal for item in candidates)})
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
                    evidence = (evidence_index,)
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
            if item.core.uid == uid and item.state is CandidateState.ELIGIBLE:
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
                    and candidate.state is CandidateState.ELIGIBLE
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
        eligible = tuple(item for item in candidates if item.state is CandidateState.ELIGIBLE)
        counts = ArtifactResearchCountSummarySnapshot(attraction_count=sum(any(role.role is ResearchRole.ATTRACTION and role.verdict is RoleVerdict.SUPPORTED for role in item.roles) for item in eligible), attraction_target=state.counts.attraction_target, restaurant_count=sum(any(role.role is ResearchRole.RESTAURANT and role.verdict is RoleVerdict.SUPPORTED for role in item.roles) for item in eligible), restaurant_target=state.counts.restaurant_target, unique_eligible_count=len(eligible), admitted_slots=len(candidates), candidate_capacity=128)
        search = _filter_search_coverage(
            search or state.search_coverage,
            tuple(item.admission_ordinal for item in eligible),
        )
        requirement_groups = requirement_groups if requirement_groups is not None else state.requirement_group_states
        covered_interests = tuple(item for item in state.effective_interests if any(item in candidate.interests for candidate in eligible) and any(record.interest is item for record in search.records))
        covered_meals = tuple(item for item in MealIntent if any(item in candidate.meal_intents for candidate in eligible) and any(record.meal_intent is item for record in search.records))
        must_item_count, resolved_must_item_count = self._must_coverage_counts(requirement_groups, candidates)
        coverage = ArtifactResearchCoverageSummarySnapshot(effective_interests=state.effective_interests, covered_interests=covered_interests, covered_meals=covered_meals, must_item_count=must_item_count, resolved_must_item_count=resolved_must_item_count, details_success_count=sum(item.details.status == "success" and item.state is CandidateState.ELIGIBLE for item in candidates), details_target=(len(eligible) + 2) // 3, initial_city_attraction_search_done=search.initial_city_attraction_search_done, initial_hotel_restaurant_nearby_done=search.initial_hotel_restaurant_nearby_done)
        return CandidateSetStateSnapshot(confirmed_requirement_digest=state.confirmed_requirement_digest, hotel_selection_ref=state.hotel_selection_ref, effective_interests=state.effective_interests, model_action_refs=tuple(self._runs[state.hotel_selection_ref.operation_id.run_id].action_refs) if state.hotel_selection_ref.operation_id.run_id in self._runs else state.model_action_refs, model_judgment_refs=tuple(self._runs[state.hotel_selection_ref.operation_id.run_id].judgment_refs) if state.hotel_selection_ref.operation_id.run_id in self._runs else state.model_judgment_refs, observation_refs=state.observation_refs, candidates=candidates, requirement_group_states=requirement_groups, search_coverage=search, counts=counts, coverage=coverage, budgets=state.budgets, pending_gaps=() if clear_pending_gaps else state.pending_gaps, iteration_no_progress_streak=state.iteration_no_progress_streak)

    def _refresh_state(self, state: CandidateSetStateSnapshot, *, no_progress: int) -> CandidateSetStateSnapshot:
        return CandidateSetStateSnapshot(**{**_python_model(state), "iteration_no_progress_streak": no_progress})

    def _state_with(self, run: _Run, *, no_progress: int) -> CandidateSetStateSnapshot:
        run.state = self._refresh_state(run.state, no_progress=no_progress)
        return run.state

    async def _write_in_progress(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, slot: ResearchIterationOperationIds) -> ArtifactRef:
        existing = await self._repository.lookup(operation_id=slot.candidate_set_operation_id)
        if isinstance(existing, ArtifactFound):
            ref = existing.ref
            if (
                ref.artifact_type != "candidate_research.candidate_set"
                or ref.schema_version != "1.0"
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
        artifact = CandidateResearchArtifact(schema_version="1.0", run_id=run.run_id, mode=run.mode, predecessor_ref=base_ref, invocation_request_digest=run.request_digest, record=InProgressCandidateSetRecord(record_type="in_progress", state=run.state))
        ref = await self._put(run, slot.candidate_set_operation_id, "candidate_research.candidate_set", artifact)
        run.last_consistent_ref = ref
        return ref

    async def _finish(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, terminal_id: OperationId, *, transition_kind: str) -> CandidateResearchOutcome:
        run.state = await self._state_budget(run)
        completed = self._is_complete(run.state, request)
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
        artifact = CandidateResearchArtifact(schema_version="1.0", run_id=run.run_id, mode=run.mode, predecessor_ref=base_ref, invocation_request_digest=run.request_digest, record=record)
        try:
            ref = await self._put(run, terminal_id, "candidate_research.candidate_set", artifact)
        except ArtifactRepositoryError:
            return self._memory_persistence_failure(run)
        run.last_consistent_ref = ref
        try:
            read = await self._repository.get(ref, expected_model=CandidateResearchArtifact)
        except ArtifactRepositoryError:
            return self._memory_persistence_failure(run)
        if read.ref != ref or read.content.schema_version != "1.0" or read.content.run_id != run.run_id or read.content.invocation_request_digest != run.request_digest:
            return self._memory_persistence_failure(run)
        return self._outcome_from_record(ref, read.content.record)

    def _legal_action_count(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest) -> int:
        objectives = self._objectives(run, request)
        if run.state.counts.admitted_slots >= 128 and (
            run.state.counts.attraction_count < run.state.counts.attraction_target
            or run.state.counts.restaurant_count < run.state.counts.restaurant_target
        ):
            return 0
        known_uids = {item.core.uid for item in run.state.candidates}
        legal = 0
        for objective in objectives:
            if objective.kind is ResearchObjectiveKind.DETAILS_MATURITY:
                if any(item.state is CandidateState.ELIGIBLE and item.details.status == "not_requested" for item in run.state.candidates):
                    legal += 1
            elif objective.kind is ResearchObjectiveKind.SUPPLEMENTAL_GAP and objective.anchor is not None and getattr(objective.anchor, "anchor_type", None) == "candidate" and getattr(objective.anchor, "uid", None) not in known_uids:
                continue
            else:
                legal += 1
        return min(8, legal)

    async def _failed(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, failure: LLMResearchFailure | MapResearchFailure | ControlResearchFailure, failure_ref: ArtifactRef | None) -> CandidateResearchFailed:
        run.state = await self._state_budget(run)
        snapshot_failure = self._failure_snapshot(failure)
        record = FailedCandidateSetRecord(record_type="failed", state=run.state, failure=snapshot_failure, last_consistent_candidate_set_ref=run.last_consistent_ref, failure_artifact_ref=failure_ref, budgets=run.state.budgets, retryable=self._retryable(failure), possible_duplicate=self._duplicate(failure))
        base_ref = run.predecessor_ref if run.mode is ResearchMode.SUPPLEMENTAL else None
        artifact = CandidateResearchArtifact(schema_version="1.0", run_id=run.run_id, mode=run.mode, predecessor_ref=base_ref, invocation_request_digest=run.request_digest, record=record)
        try:
            ref = await self._put(run, request.operation_plan.terminal_successor_operation_id, "candidate_research.candidate_set", artifact)
        except ArtifactRepositoryError:
            return self._memory_persistence_failure(run)
        run.last_consistent_ref = ref
        try:
            read = await self._repository.get(ref, expected_model=CandidateResearchArtifact)
        except ArtifactRepositoryError:
            return self._memory_persistence_failure(run)
        if read.ref != ref or read.content.schema_version != "1.0" or read.content.run_id != run.run_id or read.content.invocation_request_digest != run.request_digest:
            return self._memory_persistence_failure(run)
        return self._outcome_from_record(ref, read.content.record)

    async def _write_action_artifact(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, slot: ResearchIterationOperationIds, iteration_index: int, failure: LLMResearchFailure, _: object) -> ArtifactRef:
        artifact = CandidateResearchModelActionArtifact(schema_version="1.0", run_id=run.run_id, mode=run.mode, invocation_index=max(0, run.semantic_invocations - 1), invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index, prompt_version=ACTION_PROMPT_VERSION, prompt_sha256=ACTION_PROMPT_CONTENT_DIGEST, input_digest=run.request_digest, result=ModelActionLLMFailureRecord(record_type="llm_failure", error=ArtifactLLMErrorSnapshot(**_python_model(failure.error))))
        ref = await self._put(run, slot.action_operation_id, "candidate_research.model_action", artifact)
        self._remember_action_ref(run, ref)
        return ref

    async def _write_model_action(self, run: _Run, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest, slot: ResearchIterationOperationIds, iteration_index: int, record: object) -> ArtifactRef:
        artifact = CandidateResearchModelActionArtifact(schema_version="1.0", run_id=run.run_id, mode=run.mode, invocation_index=run.semantic_invocations - 1, invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index, prompt_version=ACTION_PROMPT_VERSION, prompt_sha256=ACTION_PROMPT_CONTENT_DIGEST, input_digest=run.request_digest, result=record)
        return await self._put(run, slot.action_operation_id, "candidate_research.model_action", artifact)

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
                code="judgment_patch_invalid",
            )
        artifact = CandidateResearchModelJudgmentArtifact(schema_version="1.0", run_id=run.run_id, mode=run.mode, invocation_index=max(0, run.semantic_invocations - 1), invocation_slot_index=slot.invocation_slot_index, iteration_index=iteration_index, prompt_version=OBSERVATION_PROMPT_VERSION, prompt_sha256=OBSERVATION_PROMPT_CONTENT_DIGEST, evidence_refs=tuple(run.observation_refs), result=record)
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
                or ref.schema_version != "1.0"
            ):
                raise _OperationReplayIndeterminate()
            expected_models: dict[str, type[BaseModel]] = {
                "candidate_research.model_action": CandidateResearchModelActionArtifact,
                "candidate_research.model_judgment": CandidateResearchModelJudgmentArtifact,
                "candidate_research.place_observation": CandidateResearchPlaceObservationArtifact,
                "candidate_research.candidate_set": CandidateResearchArtifact,
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
        return await self._repository.put(request)

    @staticmethod
    def _remember_action_ref(run: _Run, ref: ArtifactRef) -> None:
        if ref not in run.action_refs:
            run.action_refs.append(ref)

    @staticmethod
    def _remember_judgment_ref(run: _Run, ref: ArtifactRef) -> None:
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
        budget = ArtifactResearchBudgetSummarySnapshot(iteration_limit=6, iterations_consumed=iteration_consumed, place_call_limit=48, place_calls_consumed=place_consumed, semantic_invocation_limit=12, semantic_invocations_consumed=run.semantic_invocations, known_provider_attempts=run.provider_attempts, provider_attempts_complete=run.provider_attempts_complete, known_usage=ArtifactKnownLLMUsageSnapshot(prompt_tokens=run.usage.get("prompt_tokens", 0), completion_tokens=run.usage.get("completion_tokens", 0), total_tokens=run.usage.get("total_tokens", 0), reasoning_tokens=run.usage.get("reasoning_tokens", 0), prompt_cache_hit_tokens=run.usage.get("prompt_cache_hit_tokens", 0), prompt_cache_miss_tokens=run.usage.get("prompt_cache_miss_tokens", 0)), usage_complete=run.usage_complete)
        return CandidateSetStateSnapshot(**{**_python_model(run.state), "budgets": budget, "model_action_refs": tuple(run.action_refs), "model_judgment_refs": tuple(run.judgment_refs), "observation_refs": tuple(run.observation_refs)})

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
    def _is_complete(state: CandidateSetStateSnapshot, request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest) -> bool:
        if not state.coverage.initial_city_attraction_search_done or not state.coverage.initial_hotel_restaurant_nearby_done:
            return False
        if state.coverage.covered_interests != state.effective_interests or state.coverage.covered_meals != tuple(MealIntent):
            return False
        if state.counts.attraction_count < state.counts.attraction_target or state.counts.restaurant_count < state.counts.restaurant_target:
            return False
        if state.coverage.details_success_count < state.coverage.details_target:
            return False
        by_ordinal = {item.admission_ordinal: item for item in state.candidates}
        for group in state.requirement_group_states:
            overrides = {item.candidate_ordinal: item.verdict for item in group.exceptions}
            effective = {ordinal: overrides.get(ordinal, group.default_verdict) for ordinal in group.evaluated_candidate_ordinals}
            if group.requirement_kind == "must_visit":
                if not any(by_ordinal[ordinal].state is CandidateState.ELIGIBLE and any(match.requirement_group_id == group.group_id and match.verdict is MatchVerdict.CONFIRMED for match in by_ordinal[ordinal].must_matches) for ordinal in group.evaluated_candidate_ordinals):
                    return False
            elif any(verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED} for verdict in effective.values()):
                return False
            elif set(group.evaluated_candidate_ordinals) != set(by_ordinal) or any(verdict is MatchVerdict.CONFIRMED and by_ordinal[ordinal].state is not CandidateState.EXCLUDED for ordinal, verdict in effective.items()):
                return False
        return not state.pending_gaps

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
            if item.state is CandidateState.ELIGIBLE and item.details.status != "success"
        ) if run.state.coverage.details_success_count < run.state.coverage.details_target else ()
        if missing_details:
            result.append(ArtifactCandidateDetailsMissingSummary(
                gap_type="candidate_details_missing", gap_id="gap:details",
                evidence_indexes=self._related_gap_indexes(run, "candidate_details_missing", candidate_uids=missing_details[:128]), candidate_uids=missing_details[:128],
            ))
        for kind, group_id, item_ids in run.requirement_specs:
            group = next((value for value in run.state.requirement_group_states if value.group_id == group_id), None)
            if kind == "must_visit":
                resolved = group is not None and any(item.state is CandidateState.ELIGIBLE and any(match.requirement_group_id == group_id and match.verdict is MatchVerdict.CONFIRMED for match in item.must_matches) for item in run.state.candidates)
                if not resolved:
                    result.append(ArtifactMustVisitResolutionMissingSummary(
                        gap_type="must_visit_resolution_missing", gap_id=f"gap:must:{group_id}",
                        evidence_indexes=self._related_gap_indexes(run, "must_visit_resolution_missing", requirement_group_id=group_id),
                        requirement_group_id=group_id, requirement_item_ids=item_ids,
                    ))
            elif group is not None:
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
    def _gap_satisfied(run: _Run, gap: object) -> bool:
        state = run.state
        gap_type = getattr(gap, "gap_type")
        if gap_type == "candidate_count_shortfall":
            count = state.counts.attraction_count if getattr(gap, "role") is ResearchRole.ATTRACTION else state.counts.restaurant_count
            return count >= getattr(gap, "target_count")
        if gap_type == "interest_coverage_missing":
            interest = getattr(gap, "interest")
            return interest in state.coverage.covered_interests or (
                any(record.interest is interest for record in state.search_coverage.records)
                and any(item.state is CandidateState.ELIGIBLE and interest in item.interests for item in state.candidates)
            )
        if gap_type == "area_coverage_missing":
            role = getattr(gap, "role")
            return any(record.role is role for record in state.search_coverage.records)
        if gap_type == "meal_coverage_missing":
            meal = getattr(gap, "meal_intent")
            return meal in state.coverage.covered_meals or (
                any(record.meal_intent is meal for record in state.search_coverage.records)
                and any(item.state is CandidateState.ELIGIBLE and meal in item.meal_intents for item in state.candidates)
            )
        if gap_type == "candidate_details_missing":
            by_uid = {item.core.uid: item for item in state.candidates}
            return all(by_uid.get(uid) is not None and by_uid[uid].details.status == "success" for uid in getattr(gap, "candidate_uids"))
        if gap_type == "must_visit_resolution_missing":
            group = next((item for item in state.requirement_group_states if item.group_id == getattr(gap, "requirement_group_id")), None)
            return group is not None and any(item.state is CandidateState.ELIGIBLE and any(match.requirement_group_id == group.group_id and match.verdict is MatchVerdict.CONFIRMED for match in item.must_matches) for item in state.candidates)
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
                if item.admission_ordinal not in evaluated_ordinals:
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
            return any(
                item.core.uid != getattr(gap, "rejected_uid")
                and item.state is CandidateState.ELIGIBLE
                and item.core.uid not in run.supplemental_baseline_uids
                for item in state.candidates
            )
        return False

    @staticmethod
    def _state_pending(state: CandidateSetStateSnapshot, gaps: Sequence[object]) -> CandidateSetStateSnapshot:
        return CandidateSetStateSnapshot(**{**_python_model(state), "pending_gaps": tuple(gaps)})

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

    async def _indeterminate(self, run_id: UUID) -> CandidateResearchFailed:
        run = self._runs.get(run_id)
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
