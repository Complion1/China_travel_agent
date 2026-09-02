"""Module 08 候选研究循环的严格公共 DTO。"""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.baidu_map import BaiduMapErrorCode, MapCapability
from app.llm_runtime import LLMErrorCode, LLMRuntimeError, ModelRole
from app.requirements import ConfirmedTravelRequirement, InterestTag
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase, ExecutionBudgetKey, ExecutionBudgetScope


CANDIDATE_RESEARCH_SCHEMA_VERSION: Literal["1.0"] = "1.0"
CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY = ExecutionBudgetKey(scope=ExecutionBudgetScope.PHASE, name="research.iteration", phase=WorkflowPhase.CANDIDATE_RESEARCH)
CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY = ExecutionBudgetKey(scope=ExecutionBudgetScope.PHASE, name="research.place_call", phase=WorkflowPhase.CANDIDATE_RESEARCH)

BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
StableId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
ContentDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ActionFingerprint = ContentDigest
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class ResearchRole(str, Enum):
    ATTRACTION = "attraction"
    RESTAURANT = "restaurant"


class RoleVerdict(str, Enum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNRESOLVED = "unresolved"


class CandidateState(str, Enum):
    ELIGIBLE = "eligible"
    WITHHELD = "withheld"
    EXCLUDED = "excluded"
    CONFLICTED = "conflicted"


class MatchVerdict(str, Enum):
    CONFIRMED = "confirmed"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class MealIntent(str, Enum):
    LUNCH = "lunch"
    DINNER = "dinner"


class ResearchInvocationKind(str, Enum):
    ACTION = "action"
    JUDGMENT = "judgment"


class ResearchTool(str, Enum):
    SEARCH_PLACES = "search_places"
    GET_PLACE_DETAILS = "get_place_details"


class SearchAreaMode(str, Enum):
    CITY = "city"
    NEARBY = "nearby"


class ResearchMode(str, Enum):
    INITIAL = "initial"
    SUPPLEMENTAL = "supplemental"


class ResearchObjectiveKind(str, Enum):
    SUPPLEMENTAL_GAP = "supplemental_gap"
    MUST_VISIT_RESOLUTION = "must_visit_resolution"
    AVOID_RESOLUTION = "avoid_resolution"
    INITIAL_CITY_ATTRACTION = "initial_city_attraction"
    INITIAL_HOTEL_RESTAURANT_NEARBY = "initial_hotel_restaurant_nearby"
    MEAL_LUNCH = "meal_lunch"
    MEAL_DINNER = "meal_dinner"
    INTEREST_COVERAGE = "interest_coverage"
    ATTRACTION_COUNT = "attraction_count"
    RESTAURANT_COUNT = "restaurant_count"
    DETAILS_MATURITY = "details_maturity"
    DIVERSITY = "diversity"


class CandidateResearchStopReason(str, Enum):
    CANDIDATE_CAPACITY_REACHED = "candidate_capacity_reached"
    PLACE_BUDGET_EXHAUSTED = "place_budget_exhausted"
    ITERATION_LIMIT_REACHED = "iteration_limit_reached"
    NO_PROGRESS = "no_progress"
    NO_LEGAL_ACTION = "no_legal_action"
    USEFUL_SUPPLY_NOT_FOUND = "useful_supply_not_found"


class RoleReasonCode(str, Enum):
    NAME_SEMANTICS = "name_semantics"
    PROVIDER_CATEGORY_SUPPORTS = "provider_category_supports"
    PROVIDER_CATEGORY_CONTRADICTS = "provider_category_contradicts"
    DETAILS_SUPPORT = "details_support"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    EVIDENCE_CONFLICTED = "evidence_conflicted"


class InterestReasonCode(str, Enum):
    TARGETED_SEARCH = "targeted_search"
    NAME_OR_CATEGORY_MATCH = "name_or_category_match"
    DETAILS_MATCH = "details_match"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    EVIDENCE_CONFLICTED = "evidence_conflicted"


class MealReasonCode(str, Enum):
    TARGETED_MEAL_SEARCH = "targeted_meal_search"
    BUSINESS_HOURS_SUPPORTS = "business_hours_supports"
    BUSINESS_HOURS_CONFLICTS = "business_hours_conflicts"
    BUSINESS_HOURS_UNKNOWN = "business_hours_unknown"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"


class RequirementReasonCode(str, Enum):
    EXACT_NAME_MATCH = "exact_name_match"
    SEMANTIC_NAME_MATCH = "semantic_name_match"
    ADDRESS_DISAMBIGUATED = "address_disambiguated"
    DETAILS_DISAMBIGUATED = "details_disambiguated"
    CANDIDATE_OUT_OF_SCOPE = "candidate_out_of_scope"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    EVIDENCE_CONFLICTED = "evidence_conflicted"


class CandidateResearchControlFailureCode(str, Enum):
    OPERATION_RESULT_INDETERMINATE = "operation_result_indeterminate"
    REPLAY_MISMATCH = "replay_mismatch"
    INVALID_INPUT = "invalid_input"
    OPERATION_PLAN_INVALID = "operation_plan_invalid"
    PROMPT_RESOURCE_INVALID = "prompt_resource_invalid"
    ACTION_BATCH_INVALID = "action_batch_invalid"
    JUDGMENT_PATCH_INVALID = "judgment_patch_invalid"
    ARTIFACT_TOO_LARGE = "artifact_too_large"
    ARTIFACT_CAPACITY = "artifact_capacity"
    ARTIFACT_ERROR = "artifact_error"
    BUDGET_ERROR = "budget_error"
    RUN_CONTEXT_MISSING = "run_context_missing"
    RUN_CLOSED = "run_closed"
    UNVERIFIED_RESULT = "unverified_result"
    PERSISTENCE_ERROR = "persistence_error"
    DEPENDENCY_CONTRACT_ERROR = "dependency_contract_error"
    SERVICE_USAGE_ERROR = "service_usage_error"


class _PublicModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class HotelAnchorRef(_PublicModel):
    anchor_type: Literal["hotel"] = "hotel"


class CandidateAnchorRef(_PublicModel):
    anchor_type: Literal["candidate"] = "candidate"
    uid: BoundedText


ResearchAnchorRef: TypeAlias = Annotated[
    HotelAnchorRef | CandidateAnchorRef, Field(discriminator="anchor_type")
]


class ResearchIterationOperationIds(_PublicModel):
    schema_version: Literal["1.0"] = "1.0"
    invocation_slot_index: Annotated[int, Field(ge=0, le=5)]
    action_operation_id: OperationId
    judgment_operation_id: OperationId
    candidate_set_operation_id: OperationId
    place_observation_operation_ids: Annotated[tuple[OperationId, ...], Field(min_length=0, max_length=8)]

    @model_validator(mode="after")
    def _validate_ids(self) -> "ResearchIterationOperationIds":
        all_ids = (self.action_operation_id, self.judgment_operation_id, self.candidate_set_operation_id, *self.place_observation_operation_ids)
        if any(item.phase is not WorkflowPhase.CANDIDATE_RESEARCH for item in all_ids):
            raise ValueError("all operation ids must be candidate_research phase")
        if len({(item.run_id, item.phase, item.logical_step) for item in all_ids}) != len(all_ids):
            raise ValueError("operation ids must be unique")
        if len({item.run_id for item in all_ids}) != 1:
            raise ValueError("operation ids must share run")
        return self


class CandidateResearchOperationPlan(_PublicModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    iteration_slots: Annotated[tuple[ResearchIterationOperationIds, ...], Field(min_length=0, max_length=6)]
    terminal_successor_operation_id: OperationId

    @model_validator(mode="after")
    def _validate_plan(self) -> "CandidateResearchOperationPlan":
        indexes = tuple(item.invocation_slot_index for item in self.iteration_slots)
        if indexes != tuple(range(len(indexes))):
            raise ValueError("iteration slots must be contiguous")
        all_ids = [self.terminal_successor_operation_id]
        for item in self.iteration_slots:
            all_ids.extend((item.action_operation_id, item.judgment_operation_id, item.candidate_set_operation_id, *item.place_observation_operation_ids))
        if any(item.run_id != self.run_id for item in all_ids):
            raise ValueError("operation ids must use plan run")
        if len({(item.run_id, item.phase, item.logical_step) for item in all_ids}) != len(all_ids):
            raise ValueError("plan operation ids must be globally unique")
        if self.terminal_successor_operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
            raise ValueError("terminal operation must be candidate_research phase")
        return self


class CandidateResearchInitialRequest(_PublicModel):
    schema_version: Literal["1.0"] = "1.0"
    confirmed: ConfirmedTravelRequirement
    hotel_selection_ref: ArtifactRef
    operation_plan: CandidateResearchOperationPlan

    @model_validator(mode="after")
    def _hotel_ref(self) -> "CandidateResearchInitialRequest":
        if len(self.operation_plan.iteration_slots) != 6:
            raise ValueError("initial request requires exactly six iteration slots")
        if self.hotel_selection_ref.artifact_type != "hotel.selection" or self.hotel_selection_ref.schema_version != "1.0" or self.hotel_selection_ref.operation_id.phase is not WorkflowPhase.HOTEL_SELECTION:
            raise ValueError("initial request must reference hotel selection artifact")
        if self.hotel_selection_ref.operation_id.run_id != self.operation_plan.run_id:
            raise ValueError("hotel selection ref run must match operation plan")
        return self


class CandidateResearchSupplementalRequest(_PublicModel):
    schema_version: Literal["1.0"] = "1.0"
    predecessor_candidate_set_ref: ArtifactRef
    gaps: Annotated[tuple["CandidateResearchGap", ...], Field(min_length=1, max_length=128)]
    operation_plan: CandidateResearchOperationPlan

    @model_validator(mode="after")
    def _predecessor_ref(self) -> "CandidateResearchSupplementalRequest":
        if self.predecessor_candidate_set_ref.artifact_type != "candidate_research.candidate_set" or self.predecessor_candidate_set_ref.schema_version != "1.0" or self.predecessor_candidate_set_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
            raise ValueError("supplemental request must reference candidate set artifact")
        if self.predecessor_candidate_set_ref.operation_id.run_id != self.operation_plan.run_id:
            raise ValueError("predecessor ref run must match operation plan")
        if any(ref.artifact_type != "candidate_research.place_observation" or ref.schema_version != "1.0" or ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH or ref.operation_id.run_id != self.operation_plan.run_id for gap in self.gaps for ref in gap.evidence_refs):
            raise ValueError("gap evidence refs must be same-run place observations")
        if any(len({ref.artifact_id for ref in gap.evidence_refs}) != len(gap.evidence_refs) for gap in self.gaps):
            raise ValueError("gap evidence refs must be unique per gap")
        return self


class _GapBase(_PublicModel):
    schema_version: Literal["1.0"] = "1.0"
    gap_type: str
    gap_id: StableId
    evidence_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=1, max_length=8)]


class CandidateCountShortfallGap(_GapBase):
    gap_type: Literal["candidate_count_shortfall"] = "candidate_count_shortfall"
    role: ResearchRole
    current_count: Annotated[int, Field(ge=0)]
    target_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _shortfall(self) -> "CandidateCountShortfallGap":
        if self.current_count >= self.target_count:
            raise ValueError("count gap requires current_count < target_count")
        return self


class InterestCoverageMissingGap(_GapBase):
    gap_type: Literal["interest_coverage_missing"] = "interest_coverage_missing"
    interest: InterestTag


class AreaCoverageMissingGap(_GapBase):
    gap_type: Literal["area_coverage_missing"] = "area_coverage_missing"
    role: ResearchRole
    anchor: ResearchAnchorRef
    radius_km: Literal[1, 3, 5, 10]


class MealCoverageMissingGap(_GapBase):
    gap_type: Literal["meal_coverage_missing"] = "meal_coverage_missing"
    meal_intent: MealIntent
    anchor: ResearchAnchorRef
    radius_km: Literal[1, 3, 5, 10]


class MustVisitResolutionMissingGap(_GapBase):
    gap_type: Literal["must_visit_resolution_missing"] = "must_visit_resolution_missing"
    requirement_group_id: StableId
    requirement_item_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]


class AvoidResolutionMissingGap(_GapBase):
    """A typed avoid group whose admitted candidates still need resolution."""

    gap_type: Literal["avoid_resolution_missing"] = "avoid_resolution_missing"
    requirement_group_id: StableId
    requirement_item_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]
    candidate_uids: Annotated[tuple[BoundedText, ...], Field(min_length=1, max_length=128)]

    @field_validator("requirement_item_ids", "candidate_uids")
    @classmethod
    def _unique_target(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("typed gap target values must be unique")
        return value


class CandidateDetailsMissingGap(_GapBase):
    gap_type: Literal["candidate_details_missing"] = "candidate_details_missing"
    candidate_uids: Annotated[tuple[BoundedText, ...], Field(min_length=1, max_length=128)]

    @field_validator("candidate_uids")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("candidate uids must be unique")
        return value


class ReplacementCandidateNeededGap(_GapBase):
    gap_type: Literal["replacement_candidate_needed"] = "replacement_candidate_needed"
    rejected_uid: BoundedText
    role: ResearchRole
    anchor: ResearchAnchorRef | None = None
    radius_km: Literal[1, 3, 5, 10] | None = None

    @model_validator(mode="after")
    def _anchor_pair(self) -> "ReplacementCandidateNeededGap":
        if (self.anchor is None) != (self.radius_km is None):
            raise ValueError("anchor and radius_km must be both present or absent")
        return self


CandidateResearchGap: TypeAlias = Annotated[
    CandidateCountShortfallGap | InterestCoverageMissingGap | AreaCoverageMissingGap
    | MealCoverageMissingGap | MustVisitResolutionMissingGap | AvoidResolutionMissingGap | CandidateDetailsMissingGap
    | ReplacementCandidateNeededGap,
    Field(discriminator="gap_type"),
]
# Internal compatibility alias used by the full Module 08 design; the phase-1/2
# package facade intentionally keeps this helper out of its public name list.
SupplementalGap: TypeAlias = CandidateResearchGap


class ProposedSearchAction(_PublicModel):
    action_type: Literal["search_places"] = "search_places"
    ordinal: Annotated[int, Field(ge=0, le=7)]
    objective_id: StableId
    query: BoundedText
    tag: BoundedText | None = None
    area_mode: SearchAreaMode
    anchor: ResearchAnchorRef | None = None
    radius_km: Literal[1, 3, 5, 10] | None = None
    interest: InterestTag | None = None
    meal_intent: MealIntent | None = None

    @model_validator(mode="after")
    def _area(self) -> "ProposedSearchAction":
        if self.area_mode is SearchAreaMode.CITY and (self.anchor is not None or self.radius_km is not None):
            raise ValueError("city search cannot have anchor/radius")
        if self.area_mode is SearchAreaMode.NEARBY and (self.anchor is None or self.radius_km is None):
            raise ValueError("nearby search requires anchor/radius")
        return self


class ProposedDetailsAction(_PublicModel):
    action_type: Literal["get_place_details"] = "get_place_details"
    ordinal: Annotated[int, Field(ge=0, le=7)]
    objective_id: StableId
    uid: BoundedText


ProposedAction: TypeAlias = Annotated[ProposedSearchAction | ProposedDetailsAction, Field(discriminator="action_type")]


class ProposedActionBatch(_PublicModel):
    outcome_type: Literal["proposed_actions"] = "proposed_actions"
    effective_interests: Annotated[tuple[InterestTag, ...], Field(min_length=2, max_length=4)] | None = None
    actions: Annotated[tuple[ProposedAction, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _ordinals(self) -> "ProposedActionBatch":
        if tuple(action.ordinal for action in self.actions) != tuple(range(len(self.actions))):
            raise ValueError("action ordinals must be contiguous")
        return self


class NoUsefulActionProposed(_PublicModel):
    outcome_type: Literal["no_useful_action"] = "no_useful_action"
    reason_code: Literal["prior_no_progress", "no_legal_action", "insufficient_remaining_budget"]


ActionModelOutcome: TypeAlias = Annotated[ProposedActionBatch | NoUsefulActionProposed, Field(discriminator="outcome_type")]


class EvidenceHandle(_PublicModel):
    handle_id: StableId


class RoleJudgmentUpdate(_PublicModel):
    candidate_handle: StableId
    role: ResearchRole
    verdict: RoleVerdict
    reason_code: RoleReasonCode
    evidence_handles: Annotated[tuple[EvidenceHandle, ...], Field(min_length=1, max_length=8)]


class InterestJudgmentUpdate(_PublicModel):
    candidate_handle: StableId
    interest: InterestTag
    verdict: MatchVerdict
    reason_code: InterestReasonCode
    evidence_handles: Annotated[tuple[EvidenceHandle, ...], Field(min_length=1, max_length=8)]


class MealJudgmentUpdate(_PublicModel):
    candidate_handle: StableId
    meal_intent: MealIntent
    verdict: MatchVerdict
    reason_code: MealReasonCode
    evidence_handles: Annotated[tuple[EvidenceHandle, ...], Field(min_length=1, max_length=8)]


class RequirementVerdictException(_PublicModel):
    candidate_handle: StableId
    verdict: MatchVerdict
    reason_code: RequirementReasonCode
    evidence_handles: Annotated[tuple[EvidenceHandle, ...], Field(min_length=1, max_length=8)]


class RequirementGroupJudgmentUpdate(_PublicModel):
    requirement_group_id: StableId
    requirement_kind: Literal["must_visit", "avoid"]
    evaluated_candidate_handles: Annotated[tuple[StableId, ...], Field(min_length=0, max_length=128)]
    default_verdict: MatchVerdict
    default_reason_code: RequirementReasonCode
    default_evidence_handles: Annotated[tuple[EvidenceHandle, ...], Field(min_length=1, max_length=8)]
    exceptions: Annotated[tuple[RequirementVerdictException, ...], Field(min_length=0, max_length=128)]

    @model_validator(mode="after")
    def _semantic(self) -> "RequirementGroupJudgmentUpdate":
        evaluated = set(self.evaluated_candidate_handles)
        if len(evaluated) != len(self.evaluated_candidate_handles):
            raise ValueError("evaluated handles must be unique")
        exception_handles = tuple(item.candidate_handle for item in self.exceptions)
        if len(set(exception_handles)) != len(exception_handles) or not set(exception_handles).issubset(evaluated):
            raise ValueError("exceptions must refer to evaluated candidates")
        if not evaluated and self.default_verdict is not MatchVerdict.UNRESOLVED:
            raise ValueError("empty evaluation must remain unresolved")
        return self


class JudgmentPatch(_PublicModel):
    outcome_type: Literal["judgment_patch"] = "judgment_patch"
    role_updates: Annotated[tuple[RoleJudgmentUpdate, ...], Field(min_length=0, max_length=256)]
    interest_updates: Annotated[tuple[InterestJudgmentUpdate, ...], Field(min_length=0, max_length=512)]
    meal_updates: Annotated[tuple[MealJudgmentUpdate, ...], Field(min_length=0, max_length=256)]
    requirement_group_updates: Annotated[tuple[RequirementGroupJudgmentUpdate, ...], Field(min_length=0, max_length=200)]

    @model_validator(mode="after")
    def _unique(self) -> "JudgmentPatch":
        keys = [
            [(item.candidate_handle, item.role) for item in self.role_updates],
            [(item.candidate_handle, item.interest) for item in self.interest_updates],
            [(item.candidate_handle, item.meal_intent) for item in self.meal_updates],
            [item.requirement_group_id for item in self.requirement_group_updates],
        ]
        if any(len(set(branch)) != len(branch) for branch in keys):
            raise ValueError("judgment update keys must be unique")
        return self


class ArtifactCoordinateSnapshot(_PublicModel):
    coordinate_system: Literal["bd09ll"]
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


class CandidateCore(_PublicModel):
    uid: BoundedText
    name: BoundedText
    address: BoundedText
    coordinate: ArtifactCoordinateSnapshot
    provider_category: BoundedText | None


class RoleAssessment(_PublicModel):
    role: ResearchRole
    verdict: RoleVerdict
    reason_code: RoleReasonCode
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=48)]


class RequirementMatchState(_PublicModel):
    requirement_group_id: StableId
    verdict: MatchVerdict
    reason_code: RequirementReasonCode
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=48)]


class CandidateDetailsState(_PublicModel):
    status: Literal["not_requested", "success", "no_result"]
    observation_index: Annotated[int, Field(ge=0, le=47)] | None

    @model_validator(mode="after")
    def _observation(self) -> "CandidateDetailsState":
        if self.status == "not_requested" and self.observation_index is not None:
            raise ValueError("not_requested details must not have observation index")
        if self.status != "not_requested" and self.observation_index is None:
            raise ValueError("requested details require observation index")
        return self


class CandidateRecord(_PublicModel):
    schema_version: Literal["1.0"]
    admission_ordinal: Annotated[int, Field(ge=0, le=127)]
    core: CandidateCore
    state: CandidateState
    roles: Annotated[tuple[RoleAssessment, ...], Field(min_length=2, max_length=2)]
    interests: Annotated[tuple[InterestTag, ...], Field(min_length=0, max_length=4)]
    meal_intents: Annotated[tuple[MealIntent, ...], Field(min_length=0, max_length=2)]
    must_matches: Annotated[tuple[RequirementMatchState, ...], Field(min_length=0, max_length=100)]
    avoid_matches: Annotated[tuple[RequirementMatchState, ...], Field(min_length=0, max_length=100)]
    details: CandidateDetailsState
    observation_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=48)]
    dietary_restrictions: Literal["not_evaluated"]
    mobility_constraints: Literal["not_evaluated"]
    budget_fit: Literal["not_evaluated"]

    @model_validator(mode="after")
    def _roles(self) -> "CandidateRecord":
        if tuple(item.role for item in self.roles) != (ResearchRole.ATTRACTION, ResearchRole.RESTAURANT):
            raise ValueError("roles must be attraction then restaurant")
        if len(set(self.interests)) != len(self.interests) or tuple(self.interests) != tuple(sorted(self.interests, key=lambda item: list(InterestTag).index(item))):
            raise ValueError("interests must be unique and enum ordered")
        if len(set(self.meal_intents)) != len(self.meal_intents) or tuple(self.meal_intents) != tuple(sorted(self.meal_intents, key=lambda item: list(MealIntent).index(item))):
            raise ValueError("meal intents must be unique and enum ordered")
        if len(set(self.observation_indexes)) != len(self.observation_indexes):
            raise ValueError("observation indexes must be unique")
        if any(len(set(item.evidence_indexes)) != len(item.evidence_indexes) for item in (*self.roles, *self.must_matches, *self.avoid_matches)):
            raise ValueError("evidence indexes must be unique within each judgment")
        if self.details.observation_index is not None and self.details.observation_index not in self.observation_indexes:
            raise ValueError("details observation must be in candidate evidence indexes")
        core_conflicted = any(item.reason_code is RoleReasonCode.EVIDENCE_CONFLICTED for item in self.roles)
        confirmed_must = any(item.verdict is MatchVerdict.CONFIRMED for item in self.must_matches)
        confirmed_avoid = any(item.verdict is MatchVerdict.CONFIRMED for item in self.avoid_matches)
        avoid_unresolved = any(item.verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED} for item in self.avoid_matches)
        supported_role = any(item.verdict is RoleVerdict.SUPPORTED for item in self.roles)
        unresolved_role = any(item.verdict is RoleVerdict.UNRESOLVED for item in self.roles)
        all_roles_contradicted = all(item.verdict is RoleVerdict.CONTRADICTED for item in self.roles)
        if (supported_role and all_roles_contradicted) or (unresolved_role and all_roles_contradicted) or (not supported_role and not unresolved_role and not all_roles_contradicted):
            raise ValueError("candidate role predicates are impossible")
        if core_conflicted or (confirmed_must and confirmed_avoid):
            expected_state = CandidateState.CONFLICTED
        elif confirmed_avoid or all_roles_contradicted:
            expected_state = CandidateState.EXCLUDED
        elif avoid_unresolved or (not supported_role and unresolved_role):
            expected_state = CandidateState.WITHHELD
        else:
            expected_state = CandidateState.ELIGIBLE
        if self.state is not expected_state:
            raise ValueError("candidate state is not derivable from evidence")
        if len({item.requirement_group_id for item in (*self.must_matches, *self.avoid_matches)}) != len(self.must_matches) + len(self.avoid_matches):
            raise ValueError("requirement match groups must be unique within candidate")
        return self


class KnownLLMUsage(_PublicModel):
    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    reasoning_tokens: Annotated[int, Field(ge=0)]
    prompt_cache_hit_tokens: Annotated[int, Field(ge=0)]
    prompt_cache_miss_tokens: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _consistency(self) -> "KnownLLMUsage":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total token mismatch")
        if self.prompt_cache_hit_tokens + self.prompt_cache_miss_tokens != self.prompt_tokens:
            raise ValueError("cache token mismatch")
        if self.reasoning_tokens > self.completion_tokens:
            raise ValueError("reasoning tokens exceed completion")
        return self


class ResearchCountSummary(_PublicModel):
    attraction_count: Annotated[int, Field(ge=0)]
    attraction_target: Annotated[int, Field(ge=1, le=60)]
    restaurant_count: Annotated[int, Field(ge=0)]
    restaurant_target: Annotated[int, Field(ge=1, le=40)]
    unique_eligible_count: Annotated[int, Field(ge=0, le=128)]
    admitted_slots: Annotated[int, Field(ge=0, le=128)]
    candidate_capacity: Literal[128]

    @model_validator(mode="after")
    def _relationships(self) -> "ResearchCountSummary":
        if self.attraction_count > self.unique_eligible_count or self.restaurant_count > self.unique_eligible_count or self.unique_eligible_count > self.admitted_slots:
            raise ValueError("candidate summary counts are inconsistent")
        return self


class ResearchCoverageSummary(_PublicModel):
    effective_interests: Annotated[tuple[InterestTag, ...], Field(min_length=2, max_length=4)]
    covered_interests: Annotated[tuple[InterestTag, ...], Field(min_length=0, max_length=4)]
    covered_meals: Annotated[tuple[MealIntent, ...], Field(min_length=0, max_length=2)]
    must_item_count: Annotated[int, Field(ge=0, le=100)]
    resolved_must_item_count: Annotated[int, Field(ge=0, le=100)]
    details_success_count: Annotated[int, Field(ge=0, le=128)]
    details_target: Annotated[int, Field(ge=0, le=128)]
    initial_city_attraction_search_done: bool
    initial_hotel_restaurant_nearby_done: bool

    @model_validator(mode="after")
    def _coverage_order(self) -> "ResearchCoverageSummary":
        if len(set(self.effective_interests)) != len(self.effective_interests) or tuple(self.effective_interests) != tuple(sorted(self.effective_interests, key=lambda item: list(InterestTag).index(item))):
            raise ValueError("effective interests must be unique and enum ordered")
        if len(set(self.covered_interests)) != len(self.covered_interests) or tuple(self.covered_interests) != tuple(sorted(self.covered_interests, key=lambda item: list(InterestTag).index(item))):
            raise ValueError("covered interests must be unique and enum ordered")
        if len(set(self.covered_meals)) != len(self.covered_meals) or tuple(self.covered_meals) != tuple(sorted(self.covered_meals, key=lambda item: list(MealIntent).index(item))):
            raise ValueError("covered meals must be unique and enum ordered")
        if not set(self.covered_interests).issubset(self.effective_interests):
            raise ValueError("covered interests must be effective interests")
        if self.resolved_must_item_count > self.must_item_count or self.details_success_count > 128:
            raise ValueError("coverage counts are inconsistent")
        return self


class ResearchBudgetSummary(_PublicModel):
    iteration_limit: Literal[6]
    iterations_consumed: Annotated[int, Field(ge=0, le=6)]
    place_call_limit: Literal[48]
    place_calls_consumed: Annotated[int, Field(ge=0, le=48)]
    semantic_invocation_limit: Literal[12]
    semantic_invocations_consumed: Annotated[int, Field(ge=0, le=12)]
    known_provider_attempts: Annotated[int, Field(ge=0, le=24)]
    provider_attempts_complete: bool
    known_usage: KnownLLMUsage
    usage_complete: bool

    @model_validator(mode="after")
    def _budget_consistency(self) -> "ResearchBudgetSummary":
        if self.iterations_consumed > self.iteration_limit or self.place_calls_consumed > self.place_call_limit or self.semantic_invocations_consumed > self.semantic_invocation_limit:
            raise ValueError("budget counters exceed frozen limits")
        usage = self.known_usage
        if self.known_provider_attempts > self.semantic_invocations_consumed * 2:
            raise ValueError("provider attempts exceed semantic invocation bound")
        if self.provider_attempts_complete and self.known_provider_attempts < self.semantic_invocations_consumed:
            raise ValueError("complete provider attempts must cover semantic invocations")
        if self.semantic_invocations_consumed == 0:
            if self.known_provider_attempts != 0 or not self.provider_attempts_complete or not self.usage_complete:
                raise ValueError("zero semantic calls require complete zero usage")
            if any(getattr(usage, name) != 0 for name in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")):
                raise ValueError("zero semantic calls require zero usage")
        return self


class _SummaryGapBase(_PublicModel):
    gap_type: str
    gap_id: StableId
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=0, max_length=8)]

    @field_validator("evidence_indexes")
    @classmethod
    def _unique_evidence(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(value)) != len(value):
            raise ValueError("gap evidence indexes must be unique")
        return value


class CandidateCountShortfallSummary(_SummaryGapBase):
    gap_type: Literal["candidate_count_shortfall"] = "candidate_count_shortfall"
    role: ResearchRole
    current_count: Annotated[int, Field(ge=0)]
    target_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _shortfall(self) -> "CandidateCountShortfallSummary":
        if self.current_count >= self.target_count:
            raise ValueError("count gap requires current_count < target_count")
        return self


class InterestCoverageMissingSummary(_SummaryGapBase):
    gap_type: Literal["interest_coverage_missing"] = "interest_coverage_missing"
    interest: InterestTag


class AreaCoverageMissingSummary(_SummaryGapBase):
    gap_type: Literal["area_coverage_missing"] = "area_coverage_missing"
    role: ResearchRole
    anchor: ResearchAnchorRef
    radius_km: Literal[1, 3, 5, 10]


class MealCoverageMissingSummary(_SummaryGapBase):
    gap_type: Literal["meal_coverage_missing"] = "meal_coverage_missing"
    meal_intent: MealIntent
    anchor: ResearchAnchorRef
    radius_km: Literal[1, 3, 5, 10]


class MustVisitResolutionMissingSummary(_SummaryGapBase):
    gap_type: Literal["must_visit_resolution_missing"] = "must_visit_resolution_missing"
    requirement_group_id: StableId
    requirement_item_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]


class AvoidResolutionMissingSummary(_SummaryGapBase):
    gap_type: Literal["avoid_resolution_missing"] = "avoid_resolution_missing"
    requirement_group_id: StableId
    requirement_item_ids: Annotated[tuple[StableId, ...], Field(min_length=1, max_length=100)]
    candidate_uids: Annotated[tuple[BoundedText, ...], Field(min_length=1, max_length=128)]

    @field_validator("requirement_item_ids", "candidate_uids")
    @classmethod
    def _unique_target(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("typed gap target values must be unique")
        return value


class CandidateDetailsMissingSummary(_SummaryGapBase):
    gap_type: Literal["candidate_details_missing"] = "candidate_details_missing"
    candidate_uids: Annotated[tuple[BoundedText, ...], Field(min_length=1, max_length=128)]

    @field_validator("candidate_uids")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("candidate uids must be unique")
        return value


class ReplacementCandidateNeededSummary(_SummaryGapBase):
    gap_type: Literal["replacement_candidate_needed"] = "replacement_candidate_needed"
    rejected_uid: BoundedText
    role: ResearchRole
    anchor: ResearchAnchorRef | None
    radius_km: Literal[1, 3, 5, 10] | None

    @model_validator(mode="after")
    def _anchor_pair(self) -> "ReplacementCandidateNeededSummary":
        if (self.anchor is None) != (self.radius_km is None):
            raise ValueError("anchor and radius_km must be both present or absent")
        return self


ResearchGapSummary: TypeAlias = Annotated[
    CandidateCountShortfallSummary | InterestCoverageMissingSummary | AreaCoverageMissingSummary
    | MealCoverageMissingSummary | MustVisitResolutionMissingSummary | AvoidResolutionMissingSummary | CandidateDetailsMissingSummary
    | ReplacementCandidateNeededSummary,
    Field(discriminator="gap_type"),
]


class LLMResearchFailure(_PublicModel):
    failure_type: Literal["llm_failure"] = "llm_failure"
    invocation_kind: ResearchInvocationKind
    error: LLMRuntimeError

    @model_validator(mode="after")
    def _role(self) -> "LLMResearchFailure":
        if self.error.role is not ModelRole.CANDIDATE_RESEARCH:
            raise ValueError("llm failure role must be candidate research")
        return self


class MapResearchFailure(_PublicModel):
    failure_type: Literal["map_failure"] = "map_failure"
    tool: ResearchTool
    action_ordinal: Annotated[int, Field(ge=0, le=7)]
    code: BaiduMapErrorCode
    capability: MapCapability
    observation_ref: ArtifactRef

    @model_validator(mode="after")
    def _capability(self) -> "MapResearchFailure":
        expected = MapCapability.PLACE_SEARCH if self.tool is ResearchTool.SEARCH_PLACES else MapCapability.PLACE_DETAILS
        if self.capability is not expected or self.observation_ref.artifact_type != "candidate_research.place_observation" or self.observation_ref.schema_version != "1.0" or self.observation_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
            raise ValueError("map failure capability mismatch")
        return self


class ControlResearchFailure(_PublicModel):
    failure_type: Literal["control_failure"] = "control_failure"
    code: CandidateResearchControlFailureCode


CandidateResearchFailure: TypeAlias = Annotated[
    LLMResearchFailure | MapResearchFailure | ControlResearchFailure,
    Field(discriminator="failure_type"),
]


def _validate_outcome_summary_relationships(
    counts: ResearchCountSummary, coverage: ResearchCoverageSummary,
) -> None:
    if coverage.details_success_count > counts.unique_eligible_count:
        raise ValueError("details success count cannot exceed unique eligible count")
    expected_details_target = math.ceil(counts.unique_eligible_count / 3)
    if coverage.details_target != expected_details_target:
        raise ValueError("details target must match unique eligible count")


class CandidateResearchCompleted(_PublicModel):
    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["completed"] = "completed"
    candidate_set_ref: ArtifactRef
    counts: ResearchCountSummary
    coverage: ResearchCoverageSummary
    budgets: ResearchBudgetSummary

    @model_validator(mode="after")
    def _completed_gate(self) -> "CandidateResearchCompleted":
        if self.candidate_set_ref.artifact_type != "candidate_research.candidate_set" or self.candidate_set_ref.schema_version != "1.0" or self.candidate_set_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
            raise ValueError("candidate_set_ref must reference candidate research candidate set")
        _validate_outcome_summary_relationships(self.counts, self.coverage)
        if self.counts.attraction_count < self.counts.attraction_target or self.counts.restaurant_count < self.counts.restaurant_target:
            raise ValueError("completed outcome requires role count targets")
        if self.coverage.covered_interests != self.coverage.effective_interests:
            raise ValueError("completed outcome requires all effective interests covered")
        if self.coverage.covered_meals != tuple(MealIntent):
            raise ValueError("completed outcome requires lunch and dinner coverage")
        if self.coverage.resolved_must_item_count != self.coverage.must_item_count:
            raise ValueError("completed outcome requires all must items resolved")
        if self.coverage.details_success_count < self.coverage.details_target:
            raise ValueError("completed outcome requires mature candidate details")
        if not self.coverage.initial_city_attraction_search_done or not self.coverage.initial_hotel_restaurant_nearby_done:
            raise ValueError("completed outcome requires both initial searches")
        return self


class CandidateResearchIncomplete(_PublicModel):
    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["incomplete"] = "incomplete"
    candidate_set_ref: ArtifactRef
    counts: ResearchCountSummary
    coverage: ResearchCoverageSummary
    budgets: ResearchBudgetSummary
    gaps: Annotated[tuple[ResearchGapSummary, ...], Field(min_length=1, max_length=128)]
    stop_reasons: Annotated[tuple[CandidateResearchStopReason, ...], Field(min_length=1, max_length=6)]

    @field_validator("stop_reasons")
    @classmethod
    def _stop_unique(cls, value: tuple[CandidateResearchStopReason, ...]) -> tuple[CandidateResearchStopReason, ...]:
        if len(set(value)) != len(value):
            raise ValueError("stop reasons must be unique")
        if tuple(value) != tuple(sorted(value, key=lambda item: list(CandidateResearchStopReason).index(item))):
            raise ValueError("stop reasons must follow design order")
        return value

    @model_validator(mode="after")
    def _incomplete_summary_relationships(self) -> "CandidateResearchIncomplete":
        if self.candidate_set_ref.artifact_type != "candidate_research.candidate_set" or self.candidate_set_ref.schema_version != "1.0" or self.candidate_set_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
            raise ValueError("candidate_set_ref must reference candidate research candidate set")
        _validate_outcome_summary_relationships(self.counts, self.coverage)
        return self


class CandidateResearchFailed(_PublicModel):
    schema_version: Literal["1.0"] = "1.0"
    outcome_type: Literal["failed"] = "failed"
    failure: CandidateResearchFailure
    last_consistent_candidate_set_ref: ArtifactRef | None = None
    failure_artifact_ref: ArtifactRef | None = None
    budgets: ResearchBudgetSummary
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _failure_flags(self) -> "CandidateResearchFailed":
        if isinstance(self.failure, LLMResearchFailure):
            if (self.retryable, self.possible_duplicate) != (self.failure.error.retryable, self.failure.error.possible_duplicate):
                raise ValueError("failure flags must mirror llm error")
        elif isinstance(self.failure, ControlResearchFailure):
            expected_dup = self.failure.code in {
                CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE,
                CandidateResearchControlFailureCode.PERSISTENCE_ERROR,
            }
            if self.retryable or self.possible_duplicate != expected_dup:
                raise ValueError("control failure flags invalid")
        elif isinstance(self.failure, MapResearchFailure):
            retryable_codes = {
                BaiduMapErrorCode.RATE_LIMIT_ERROR,
                BaiduMapErrorCode.TIMEOUT_ERROR,
                BaiduMapErrorCode.TRANSPORT_ERROR,
                BaiduMapErrorCode.SESSION_ERROR,
            }
            duplicate_codes = {BaiduMapErrorCode.TIMEOUT_ERROR, BaiduMapErrorCode.TRANSPORT_ERROR}
            if (self.retryable, self.possible_duplicate) != (
                self.failure.code in retryable_codes,
                self.failure.code in duplicate_codes,
            ):
                raise ValueError("map failure flags invalid")
        for ref in (self.last_consistent_candidate_set_ref, self.failure_artifact_ref):
            if ref is not None and (ref.schema_version != "1.0" or ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH):
                raise ValueError("failure refs must use schema 1.0")
        failure_refs = [
            ref for ref in (self.last_consistent_candidate_set_ref, self.failure_artifact_ref)
            if ref is not None
        ]
        if isinstance(self.failure, MapResearchFailure):
            failure_refs.append(self.failure.observation_ref)
        failure_ref_runs = {ref.operation_id.run_id for ref in failure_refs}
        if len(failure_ref_runs) > 1:
            raise ValueError("failure refs must share run")
        if self.last_consistent_candidate_set_ref is not None and self.last_consistent_candidate_set_ref.artifact_type != "candidate_research.candidate_set":
            raise ValueError("last consistent ref must be candidate set")
        if self.failure_artifact_ref is not None and self.failure_artifact_ref.artifact_type not in {
            "candidate_research.model_action", "candidate_research.model_judgment", "candidate_research.place_observation",
        }:
            raise ValueError("failure artifact ref type invalid")
        if isinstance(self.failure, ControlResearchFailure):
            expected_type = {
                CandidateResearchControlFailureCode.ACTION_BATCH_INVALID: "candidate_research.model_action",
                CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID: "candidate_research.model_judgment",
                CandidateResearchControlFailureCode.UNVERIFIED_RESULT: "candidate_research.place_observation",
            }.get(self.failure.code)
            if expected_type is not None and (
                self.failure_artifact_ref is None
                or self.failure_artifact_ref.artifact_type != expected_type
            ):
                raise ValueError("control failure artifact ref must match failure code")
        if isinstance(self.failure, LLMResearchFailure):
            expected_type = "candidate_research.model_action" if self.failure.invocation_kind is ResearchInvocationKind.ACTION else "candidate_research.model_judgment"
            if self.failure_artifact_ref is None or self.failure_artifact_ref.artifact_type != expected_type:
                raise ValueError("llm failure artifact ref must match invocation kind")
        if isinstance(self.failure, MapResearchFailure):
            if self.failure.observation_ref.artifact_type != "candidate_research.place_observation" or self.failure.observation_ref.schema_version != "1.0" or self.failure.observation_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
                raise ValueError("map failure observation ref must be place observation")
            if self.failure_artifact_ref is None or self.failure_artifact_ref != self.failure.observation_ref:
                raise ValueError("map failure artifact ref must equal observation ref")
        return self


CandidateResearchOutcome: TypeAlias = Annotated[
    CandidateResearchCompleted | CandidateResearchIncomplete | CandidateResearchFailed,
    Field(discriminator="outcome_type"),
]


class CandidateResearchError(Exception):
    __slots__ = ("_code",)

    def __init__(self, code: CandidateResearchControlFailureCode = CandidateResearchControlFailureCode.SERVICE_USAGE_ERROR) -> None:
        if not isinstance(code, CandidateResearchControlFailureCode):
            raise TypeError("candidate research error code must be typed")
        object.__setattr__(self, "_code", code)
        # Marker exceptions intentionally carry no message/payload.  The code is
        # exposed only through the immutable typed property below.
        Exception.__init__(self)

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return type(self).__name__

    def add_note(self, note: str) -> None:
        raise AttributeError("candidate research errors do not carry notes")

    def __getattribute__(self, name: str) -> object:
        if name in {"__dict__", "__notes__"}:
            raise AttributeError("candidate research errors do not expose mutable attributes")
        if name in {"__cause__", "__context__", "__traceback__"}:
            # Do not expose implementation traceback/chaining details through
            # the typed marker object.
            return None
        return super().__getattribute__(name)

    @property
    def code(self) -> CandidateResearchControlFailureCode:
        return self._code

    def __setattr__(self, name: str, value: object) -> None:
        # Python's exception machinery must be able to attach traceback/cause
        # metadata; the public error code itself remains immutable.
        if name in {"__traceback__", "__cause__", "__context__", "__suppress_context__"}:
            object.__setattr__(self, name, value)
            return
        raise AttributeError("candidate research errors are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("candidate research errors are immutable")


class CandidateResearchConfigurationError(CandidateResearchError):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(CandidateResearchControlFailureCode.PROMPT_RESOURCE_INVALID)


class CandidateResearchUsageError(CandidateResearchError):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(CandidateResearchControlFailureCode.SERVICE_USAGE_ERROR)


# The supplemental request is declared before the gap union for readable field order.
CandidateResearchSupplementalRequest.model_rebuild()


__all__ = (
    "CANDIDATE_RESEARCH_SCHEMA_VERSION", "CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY",
    "CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY",
    "ResearchRole", "RoleVerdict", "CandidateState", "MatchVerdict", "MealIntent",
    "ResearchInvocationKind", "ResearchTool", "SearchAreaMode", "ResearchMode", "ResearchObjectiveKind",
    "CandidateResearchStopReason", "RoleReasonCode", "InterestReasonCode", "MealReasonCode",
    "RequirementReasonCode", "CandidateResearchControlFailureCode", "HotelAnchorRef", "CandidateAnchorRef",
    "ResearchAnchorRef", "ResearchIterationOperationIds", "CandidateResearchOperationPlan",
    "CandidateResearchInitialRequest", "CandidateResearchSupplementalRequest", "CandidateCountShortfallGap",
    "InterestCoverageMissingGap", "AreaCoverageMissingGap", "MealCoverageMissingGap",
    "MustVisitResolutionMissingGap", "AvoidResolutionMissingGap", "CandidateDetailsMissingGap", "ReplacementCandidateNeededGap",
    "CandidateResearchGap", "SupplementalGap", "ProposedSearchAction", "ProposedDetailsAction", "ProposedAction",
    "ProposedActionBatch", "NoUsefulActionProposed", "ActionModelOutcome", "EvidenceHandle",
    "RoleJudgmentUpdate", "InterestJudgmentUpdate", "MealJudgmentUpdate", "RequirementVerdictException",
    "RequirementGroupJudgmentUpdate", "JudgmentPatch", "ArtifactCoordinateSnapshot", "CandidateCore",
    "RoleAssessment", "RequirementMatchState", "CandidateDetailsState", "CandidateRecord", "KnownLLMUsage",
    "ResearchCountSummary", "ResearchCoverageSummary", "ResearchBudgetSummary", "ResearchGapSummary",
    "CandidateCountShortfallSummary", "InterestCoverageMissingSummary", "AreaCoverageMissingSummary",
    "MealCoverageMissingSummary", "MustVisitResolutionMissingSummary", "AvoidResolutionMissingSummary", "CandidateDetailsMissingSummary",
    "ReplacementCandidateNeededSummary", "LLMResearchFailure", "MapResearchFailure", "ControlResearchFailure",
    "CandidateResearchFailure", "CandidateResearchCompleted", "CandidateResearchIncomplete",
    "CandidateResearchFailed", "CandidateResearchOutcome", "CandidateResearchError",
    "CandidateResearchConfigurationError", "CandidateResearchUsageError",
)
