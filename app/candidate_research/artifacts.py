"""Module 08 的可持久化 Artifact snapshot 类型图。"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
from typing import Annotated, ClassVar, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.baidu_map import BaiduMapErrorCode, MapCapability
from app.llm_runtime import LLMErrorCode, ModelRole
from app.requirements import InterestTag
from app.workflow_contracts import ArtifactRef, ArtifactSchemaRegistration, ArtifactType, ArtifactWriteRequest, OperationId, WorkflowPhase

from .models import (
    ActionFingerprint, CandidateResearchControlFailureCode, CandidateResearchStopReason,
    CandidateState, ContentDigest, MealIntent, MatchVerdict, ResearchInvocationKind,
    ResearchMode, ResearchObjectiveKind, ResearchRole, ResearchTool, RoleReasonCode,
    RoleVerdict, RequirementReasonCode, SearchAreaMode,
    CANDIDATE_RESEARCH_SCHEMA_VERSION,
)


class ArtifactSnapshotModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    @model_validator(mode="after")
    def _text_is_normalized(self):
        # Artifact-local text is already normalized at the persistence boundary;
        # reject (never silently strip) any leading/trailing whitespace in the
        # complete nested snapshot tree.
        def walk(value):
            if isinstance(value, str):
                if value != value.strip():
                    raise ValueError("artifact text must be normalized")
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, (tuple, list)):
                for item in value:
                    walk(item)

        payload = self.model_dump(mode="python")
        walk(payload)
        for key, value in payload.items():
            if key.endswith("_indexes") and isinstance(value, (tuple, list)) and len(set(value)) != len(value):
                raise ValueError("index tuples must be unique")
        return self


ArtifactText = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=512)]
ArtifactStableId = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=128)]


class ArtifactProvenanceSnapshot(ArtifactSnapshotModel):
    source: Literal["baidu_maps_mcp"]
    capability: MapCapability
    retrieved_at: datetime
    verified: Literal[True]

    @model_validator(mode="after")
    def _utc(self) -> "ArtifactProvenanceSnapshot":
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() != timedelta(0):
            raise ValueError("retrieved_at must be UTC")
        return self


class ArtifactInvocationMetadataSnapshot(ArtifactSnapshotModel):
    role: Literal["candidate_research"]
    model_name: Literal["deepseek-v4-flash"]
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter", "insufficient_system_resource"]
    provider_attempts: Annotated[int, Field(ge=1, le=2)]
    possible_duplicate: bool
    usage: "ArtifactKnownLLMUsageSnapshot"


class ArtifactLLMErrorSnapshot(ArtifactSnapshotModel):
    code: LLMErrorCode
    role: Literal["candidate_research"]
    retryable: bool
    possible_duplicate: bool
    provider_status_code: int | None
    retry_after_seconds: Annotated[float, Field(allow_inf_nan=False)] | None
    actual_bytes: int | None
    max_bytes: int | None


class ArtifactCoordinateSnapshot(ArtifactSnapshotModel):
    coordinate_system: Literal["bd09ll"]
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


class ArtifactPlaceSummarySnapshot(ArtifactSnapshotModel):
    uid: ArtifactText
    name: ArtifactText
    address: ArtifactText
    coordinate: ArtifactCoordinateSnapshot
    provider_category: ArtifactText | None


class ArtifactPlaceDetailsSnapshot(ArtifactSnapshotModel):
    uid: ArtifactText
    name: ArtifactText
    address: ArtifactText
    coordinate: ArtifactCoordinateSnapshot
    provider_category: ArtifactText | None
    brand: ArtifactText | None
    telephone: ArtifactText | None
    rating: Annotated[float, Field(ge=0, le=5, allow_inf_nan=False)] | None
    business_hours: Annotated[tuple[ArtifactText, ...], Field(min_length=0, max_length=16)]


class ActionDispositionSnapshot(ArtifactSnapshotModel):
    action_ordinal: Annotated[int, Field(ge=0, le=7)]
    action_fingerprint: ActionFingerprint
    disposition: Literal["accepted", "duplicate_action", "not_admitted_budget_exhausted"]
    reused_observation_ref: ArtifactRef | None

    @model_validator(mode="after")
    def _reuse(self) -> "ActionDispositionSnapshot":
        if (self.disposition == "duplicate_action") != (self.reused_observation_ref is not None):
            raise ValueError("duplicate action must carry reused observation ref")
        if self.reused_observation_ref is not None and (
            self.reused_observation_ref.artifact_type != "candidate_research.place_observation"
            or self.reused_observation_ref.schema_version != "1.0"
            or self.reused_observation_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH
        ):
            raise ValueError("reused observation ref type/version mismatch")
        return self


class ArtifactResearchAnchorSnapshot(ArtifactSnapshotModel):
    anchor_type: Literal["hotel", "candidate"]
    uid: Annotated[str, Field(min_length=1, max_length=512)] | None

    @model_validator(mode="after")
    def _anchor(self) -> "ArtifactResearchAnchorSnapshot":
        if (self.anchor_type == "candidate") != (self.uid is not None):
            raise ValueError("anchor uid mismatch")
        if self.uid is not None and self.uid != self.uid.strip():
            raise ValueError("anchor uid must be normalized")
        return self


class ProposedSearchActionSnapshot(ArtifactSnapshotModel):
    action_type: Literal["search_places"]
    ordinal: Annotated[int, Field(ge=0, le=7)]
    objective_id: ArtifactStableId
    query: Annotated[str, Field(min_length=1, max_length=512)]
    tag: Annotated[str, Field(min_length=1, max_length=512)] | None
    area_mode: SearchAreaMode
    anchor: ArtifactResearchAnchorSnapshot | None
    radius_km: Literal[1, 3, 5, 10] | None
    interest: InterestTag | None
    meal_intent: MealIntent | None

    @model_validator(mode="after")
    def _area(self) -> "ProposedSearchActionSnapshot":
        if self.query != self.query.strip() or (self.tag is not None and self.tag != self.tag.strip()):
            raise ValueError("query/tag must be normalized")
        if self.area_mode is SearchAreaMode.CITY and (self.anchor is not None or self.radius_km is not None):
            raise ValueError("city search cannot have nearby fields")
        if self.area_mode is SearchAreaMode.NEARBY and (self.anchor is None or self.radius_km is None):
            raise ValueError("nearby search requires anchor and radius")
        return self


class ProposedDetailsActionSnapshot(ArtifactSnapshotModel):
    action_type: Literal["get_place_details"]
    ordinal: Annotated[int, Field(ge=0, le=7)]
    objective_id: ArtifactStableId
    uid: Annotated[str, Field(min_length=1, max_length=512)]


ProposedActionSnapshot: TypeAlias = Annotated[
    ProposedSearchActionSnapshot | ProposedDetailsActionSnapshot,
    Field(discriminator="action_type"),
]


class ProposedActionBatchSnapshot(ArtifactSnapshotModel):
    outcome_type: Literal["proposed_actions"]
    effective_interests: Annotated[tuple[InterestTag, ...], Field(min_length=2, max_length=4)] | None
    actions: Annotated[tuple[ProposedActionSnapshot, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _interests_order(self) -> "ProposedActionBatchSnapshot":
        if self.effective_interests is not None and (
            len(set(self.effective_interests)) != len(self.effective_interests)
            or tuple(self.effective_interests) != tuple(sorted(self.effective_interests, key=lambda item: list(InterestTag).index(item)))
        ):
            raise ValueError("effective interests must be enum ordered")
        if tuple(item.ordinal for item in self.actions) != tuple(range(len(self.actions))):
            raise ValueError("actions must be ordinal")
        return self


class NoUsefulActionProposedSnapshot(ArtifactSnapshotModel):
    outcome_type: Literal["no_useful_action"]
    reason_code: Literal["prior_no_progress", "no_legal_action", "insufficient_remaining_budget"]


ActionModelOutcomeSnapshot: TypeAlias = Annotated[
    ProposedActionBatchSnapshot | NoUsefulActionProposedSnapshot,
    Field(discriminator="outcome_type"),
]


class ModelActionAcceptedRecord(ArtifactSnapshotModel):
    record_type: Literal["accepted"]
    output: ActionModelOutcomeSnapshot
    metadata: ArtifactInvocationMetadataSnapshot
    action_dispositions: Annotated[tuple[ActionDispositionSnapshot, ...], Field(min_length=0, max_length=8)]

    @model_validator(mode="after")
    def _dispositions(self) -> "ModelActionAcceptedRecord":
        if self.output.outcome_type == "proposed_actions" and len(self.action_dispositions) != len(self.output.actions or ()):
            raise ValueError("action dispositions must align with output actions")
        if self.output.outcome_type == "no_useful_action" and self.action_dispositions:
            raise ValueError("no-useful-action output cannot have dispositions")
        if tuple(item.action_ordinal for item in self.action_dispositions) != tuple(range(len(self.action_dispositions))):
            raise ValueError("action dispositions must be ordinal")
        return self


class ModelActionPolicyRejectedRecord(ArtifactSnapshotModel):
    record_type: Literal["policy_rejected"]
    output: ActionModelOutcomeSnapshot
    metadata: ArtifactInvocationMetadataSnapshot
    reason_code: Literal["highest_priority_objective_ignored", "objective_parameters", "duplicate", "details_already_requested", "no_useful_action_invalid"]

    @model_validator(mode="after")
    def _output_is_legal_batch(self) -> "ModelActionPolicyRejectedRecord":
        if not isinstance(self.output, ProposedActionBatchSnapshot) and self.reason_code != "no_useful_action_invalid":
            raise ValueError("policy rejection requires a contract-valid proposed action batch")
        return self


class ModelActionControllerRejectedRecord(ArtifactSnapshotModel):
    record_type: Literal["controller_rejected"]
    output: ActionModelOutcomeSnapshot
    metadata: ArtifactInvocationMetadataSnapshot
    code: Literal["action_batch_invalid"]


class ModelActionLLMFailureRecord(ArtifactSnapshotModel):
    record_type: Literal["llm_failure"]
    error: ArtifactLLMErrorSnapshot


ModelActionRecord: TypeAlias = Annotated[
    ModelActionAcceptedRecord | ModelActionPolicyRejectedRecord | ModelActionControllerRejectedRecord | ModelActionLLMFailureRecord,
    Field(discriminator="record_type"),
]


class OutputRecoveryAttempt(ArtifactSnapshotModel):
    recovery_number: Annotated[int, Field(ge=1, le=2)]
    invocation_index: Annotated[int, Field(ge=0, le=10)]
    code: Literal["structured_output_invalid", "output_truncated", "judgment_evidence_mismatch", "judgment_required_missing"]
    metadata: ArtifactInvocationMetadataSnapshot | None

    @model_validator(mode="after")
    def _metadata_boundary(self):
        if (self.code in {"judgment_evidence_mismatch", "judgment_required_missing"}) != (self.metadata is not None):
            raise ValueError("recovery metadata must match public failure boundary")
        return self


class ModelActionRecoveredRecord(ArtifactSnapshotModel):
    record_type: Literal["recovered"]
    attempts: Annotated[tuple[OutputRecoveryAttempt, ...], Field(min_length=1, max_length=2)]
    final: ModelActionRecord


def unwrap_model_record(record):
    return record.final if record.record_type == "recovered" else record


def _validate_recovery_record(record, schema_version, invocation_index, *, action=False):
    if record.record_type != "recovered":
        return
    if schema_version not in {"2.0", "3.0"}:
        raise ValueError("legacy records cannot contain output recovery")
    attempts = record.attempts
    numbers = tuple(item.recovery_number for item in attempts)
    indexes = tuple(item.invocation_index for item in attempts)
    if numbers != tuple(range(numbers[0], numbers[0] + len(numbers))):
        raise ValueError("recovery numbers must be consecutive")
    if indexes != tuple(range(indexes[0], indexes[0] + len(indexes))):
        raise ValueError("recovery invocation indexes must be consecutive")
    final = record.final
    no_cost = (final.record_type == "llm_failure" and final.error.code in {
        LLMErrorCode.SCHEMA_UNSUPPORTED, LLMErrorCode.INPUT_TOO_LARGE, LLMErrorCode.QUOTA_EXHAUSTED})
    if invocation_index != indexes[-1] + (0 if no_cost else 1):
        raise ValueError("final invocation must follow recovery attempts")
    if any(item.code == "judgment_required_missing" for item in attempts) and schema_version != "3.0":
        raise ValueError("required judgment recovery requires schema 3")
    if action and any(item.code in {"judgment_evidence_mismatch", "judgment_required_missing"} for item in attempts):
        raise ValueError("action cannot contain judgment recovery")


class ArtifactEvidenceHandleSnapshot(ArtifactSnapshotModel):
    handle_id: ArtifactStableId

    @model_validator(mode="after")
    def _handle(self) -> "ArtifactEvidenceHandleSnapshot":
        if re.fullmatch(r"e:(?:[0-9]|[1-3][0-9]|4[0-7])", self.handle_id) is None:
            raise ValueError("invalid evidence handle")
        return self


class ArtifactRoleJudgmentUpdateSnapshot(ArtifactSnapshotModel):
    candidate_handle: ArtifactStableId
    role: ResearchRole
    verdict: RoleVerdict
    reason_code: RoleReasonCode
    evidence_handles: Annotated[tuple[ArtifactEvidenceHandleSnapshot, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _candidate(self) -> "ArtifactRoleJudgmentUpdateSnapshot":
        if re.fullmatch(r"c:(?:[0-9]|[1-9][0-9]|1[01][0-9]|12[0-7])", self.candidate_handle) is None:
            raise ValueError("invalid candidate handle")
        return self


class ArtifactInterestJudgmentUpdateSnapshot(ArtifactSnapshotModel):
    candidate_handle: ArtifactStableId
    interest: InterestTag
    verdict: MatchVerdict
    reason_code: Literal["targeted_search", "name_or_category_match", "details_match", "evidence_insufficient", "evidence_conflicted"]
    evidence_handles: Annotated[tuple[ArtifactEvidenceHandleSnapshot, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _candidate(self) -> "ArtifactInterestJudgmentUpdateSnapshot":
        if re.fullmatch(r"c:(?:[0-9]|[1-9][0-9]|1[01][0-9]|12[0-7])", self.candidate_handle) is None:
            raise ValueError("invalid candidate handle")
        return self


class ArtifactMealJudgmentUpdateSnapshot(ArtifactSnapshotModel):
    candidate_handle: ArtifactStableId
    meal_intent: MealIntent
    verdict: MatchVerdict
    reason_code: Literal["targeted_meal_search", "business_hours_supports", "business_hours_conflicts", "business_hours_unknown", "evidence_insufficient"]
    evidence_handles: Annotated[tuple[ArtifactEvidenceHandleSnapshot, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _candidate(self) -> "ArtifactMealJudgmentUpdateSnapshot":
        if re.fullmatch(r"c:(?:[0-9]|[1-9][0-9]|1[01][0-9]|12[0-7])", self.candidate_handle) is None:
            raise ValueError("invalid candidate handle")
        return self


class ArtifactRequirementVerdictExceptionSnapshot(ArtifactSnapshotModel):
    candidate_handle: ArtifactStableId
    verdict: MatchVerdict
    reason_code: RequirementReasonCode
    evidence_handles: Annotated[tuple[ArtifactEvidenceHandleSnapshot, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def _candidate(self) -> "ArtifactRequirementVerdictExceptionSnapshot":
        if re.fullmatch(r"c:(?:[0-9]|[1-9][0-9]|1[01][0-9]|12[0-7])", self.candidate_handle) is None:
            raise ValueError("invalid candidate handle")
        return self


class ArtifactRequirementGroupJudgmentUpdateSnapshot(ArtifactSnapshotModel):
    requirement_group_id: ArtifactStableId
    requirement_kind: Literal["must_visit", "avoid"]
    evaluated_candidate_handles: Annotated[tuple[ArtifactStableId, ...], Field(min_length=0, max_length=128)]
    default_verdict: MatchVerdict
    default_reason_code: RequirementReasonCode
    default_evidence_handles: Annotated[tuple[ArtifactEvidenceHandleSnapshot, ...], Field(min_length=1, max_length=8)]
    exceptions: Annotated[tuple[ArtifactRequirementVerdictExceptionSnapshot, ...], Field(min_length=0, max_length=128)]

    @model_validator(mode="after")
    def _semantic(self) -> "ArtifactRequirementGroupJudgmentUpdateSnapshot":
        if len(set(self.evaluated_candidate_handles)) != len(self.evaluated_candidate_handles):
            raise ValueError("evaluated candidate handles must be unique")
        if any(re.fullmatch(r"c:(?:[0-9]|[1-9][0-9]|1[01][0-9]|12[0-7])", item) is None for item in self.evaluated_candidate_handles):
            raise ValueError("invalid evaluated candidate handles")
        exception_handles = tuple(item.candidate_handle for item in self.exceptions)
        if len(set(exception_handles)) != len(exception_handles) or not set(exception_handles).issubset(self.evaluated_candidate_handles):
            raise ValueError("exceptions must refer to evaluated candidates")
        if not self.evaluated_candidate_handles and self.default_verdict is not MatchVerdict.UNRESOLVED:
            raise ValueError("empty evaluation must remain unresolved")
        return self


class JudgmentPatchModelSnapshot(ArtifactSnapshotModel):
    outcome_type: Literal["judgment_patch"]
    role_updates: Annotated[tuple[ArtifactRoleJudgmentUpdateSnapshot, ...], Field(min_length=0, max_length=256)]
    interest_updates: Annotated[tuple[ArtifactInterestJudgmentUpdateSnapshot, ...], Field(min_length=0, max_length=512)]
    meal_updates: Annotated[tuple[ArtifactMealJudgmentUpdateSnapshot, ...], Field(min_length=0, max_length=256)]
    requirement_group_updates: Annotated[tuple[ArtifactRequirementGroupJudgmentUpdateSnapshot, ...], Field(min_length=0, max_length=200)]

    @model_validator(mode="after")
    def _unique_semantic_keys(self) -> "JudgmentPatchModelSnapshot":
        keys = (
            [(item.candidate_handle, item.role) for item in self.role_updates],
            [(item.candidate_handle, item.interest) for item in self.interest_updates],
            [(item.candidate_handle, item.meal_intent) for item in self.meal_updates],
            [item.requirement_group_id for item in self.requirement_group_updates],
        )
        if any(len(set(group)) != len(group) for group in keys):
            raise ValueError("judgment patch semantic keys must be unique")
        return self


class ArtifactRoleJudgmentUpdateResolved(ArtifactSnapshotModel):
    candidate_ordinal: Annotated[int, Field(ge=0, le=127)]
    role: ResearchRole
    verdict: RoleVerdict
    reason_code: RoleReasonCode
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=8)]


class ArtifactInterestJudgmentUpdateResolved(ArtifactSnapshotModel):
    candidate_ordinal: Annotated[int, Field(ge=0, le=127)]
    interest: InterestTag
    verdict: MatchVerdict
    reason_code: Literal["targeted_search", "name_or_category_match", "details_match", "evidence_insufficient", "evidence_conflicted"]
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=8)]


class ArtifactMealJudgmentUpdateResolved(ArtifactSnapshotModel):
    candidate_ordinal: Annotated[int, Field(ge=0, le=127)]
    meal_intent: MealIntent
    verdict: MatchVerdict
    reason_code: Literal["targeted_meal_search", "business_hours_supports", "business_hours_conflicts", "business_hours_unknown", "evidence_insufficient"]
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=8)]


class ArtifactRequirementVerdictOverrideState(ArtifactSnapshotModel):
    candidate_ordinal: Annotated[int, Field(ge=0, le=127)]
    verdict: MatchVerdict
    reason_code: RequirementReasonCode
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=8)]


class ArtifactRequirementGroupState(ArtifactSnapshotModel):
    group_id: ArtifactStableId
    requirement_kind: Literal["must_visit", "avoid"]
    requirement_item_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=1, max_length=100)]
    evaluated_candidate_ordinals: Annotated[tuple[int, ...], Field(min_length=0, max_length=128)]
    default_verdict: MatchVerdict
    default_reason_code: RequirementReasonCode
    default_evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=8)]
    exceptions: Annotated[tuple[ArtifactRequirementVerdictOverrideState, ...], Field(min_length=0, max_length=128)]

    @model_validator(mode="after")
    def _semantic(self) -> "ArtifactRequirementGroupState":
        if len(set(self.requirement_item_ids)) != len(self.requirement_item_ids):
            raise ValueError("requirement item ids must be unique")
        if not self.evaluated_candidate_ordinals and self.default_verdict is not MatchVerdict.UNRESOLVED:
            raise ValueError("empty evaluation must remain unresolved")
        exception_ordinals = tuple(item.candidate_ordinal for item in self.exceptions)
        if len(set(exception_ordinals)) != len(exception_ordinals) or not set(exception_ordinals).issubset(self.evaluated_candidate_ordinals):
            raise ValueError("exceptions must refer to evaluated candidates")
        return self


class JudgmentPatchSnapshot(ArtifactSnapshotModel):
    outcome_type: Literal["judgment_patch"]
    role_updates: Annotated[tuple[ArtifactRoleJudgmentUpdateResolved, ...], Field(min_length=0, max_length=256)]
    interest_updates: Annotated[tuple[ArtifactInterestJudgmentUpdateResolved, ...], Field(min_length=0, max_length=512)]
    meal_updates: Annotated[tuple[ArtifactMealJudgmentUpdateResolved, ...], Field(min_length=0, max_length=256)]
    requirement_group_updates: Annotated[tuple[ArtifactRequirementGroupState, ...], Field(min_length=0, max_length=200)]

    @model_validator(mode="after")
    def _unique_semantic_keys(self) -> "JudgmentPatchSnapshot":
        keys = (
            [(item.candidate_ordinal, item.role) for item in self.role_updates],
            [(item.candidate_ordinal, item.interest) for item in self.interest_updates],
            [(item.candidate_ordinal, item.meal_intent) for item in self.meal_updates],
            [item.group_id for item in self.requirement_group_updates],
        )
        if any(len(set(group)) != len(group) for group in keys):
            raise ValueError("resolved judgment semantic keys must be unique")
        for update in (*self.role_updates, *self.interest_updates, *self.meal_updates):
            if len(set(update.evidence_indexes)) != len(update.evidence_indexes):
                raise ValueError("judgment evidence indexes must be unique")
        for group in self.requirement_group_updates:
            if len(set(group.default_evidence_indexes)) != len(group.default_evidence_indexes):
                raise ValueError("default evidence indexes must be unique")
            if any(len(set(item.evidence_indexes)) != len(item.evidence_indexes) for item in group.exceptions):
                raise ValueError("override evidence indexes must be unique")
        return self


class ModelJudgmentAcceptedRecord(ArtifactSnapshotModel):
    record_type: Literal["accepted"]
    patch: JudgmentPatchSnapshot
    metadata: ArtifactInvocationMetadataSnapshot


class ModelJudgmentControllerRejectedRecord(ArtifactSnapshotModel):
    record_type: Literal["controller_rejected"]
    proposed_patch: JudgmentPatchModelSnapshot
    metadata: ArtifactInvocationMetadataSnapshot
    code: Literal["judgment_patch_invalid", "judgment_required_missing", "state_invariant_failed"]


class ModelJudgmentLLMFailureRecord(ArtifactSnapshotModel):
    record_type: Literal["llm_failure"]
    error: ArtifactLLMErrorSnapshot


ModelJudgmentRecord: TypeAlias = Annotated[
    ModelJudgmentAcceptedRecord | ModelJudgmentControllerRejectedRecord | ModelJudgmentLLMFailureRecord,
    Field(discriminator="record_type"),
]


class ModelJudgmentRecoveredRecord(ArtifactSnapshotModel):
    record_type: Literal["recovered"]
    attempts: Annotated[tuple[OutputRecoveryAttempt, ...], Field(min_length=1, max_length=2)]
    final: ModelJudgmentRecord


class PlaceAdmissionDispositionSnapshot(ArtifactSnapshotModel):
    result_ordinal: Annotated[int, Field(ge=0, le=49)]
    uid: Annotated[str, Field(min_length=1, max_length=512)]
    disposition: Literal["admitted_new", "existing_uid", "not_admitted_candidate_capacity"]


class PlaceSearchSuccessObservation(ArtifactSnapshotModel):
    record_type: Literal["search_success"]
    places: Annotated[tuple[ArtifactPlaceSummarySnapshot, ...], Field(min_length=1, max_length=50)]
    admission_dispositions: Annotated[tuple[PlaceAdmissionDispositionSnapshot, ...], Field(min_length=1, max_length=50)]
    provenance: ArtifactProvenanceSnapshot

    @model_validator(mode="after")
    def _admissions(self) -> "PlaceSearchSuccessObservation":
        if len(self.places) != len(self.admission_dispositions):
            raise ValueError("admission dispositions must align with places")
        for index, item in enumerate(self.admission_dispositions):
            if item.result_ordinal != index or item.uid != self.places[index].uid:
                raise ValueError("admission disposition alignment mismatch")
        if self.provenance.capability is not MapCapability.PLACE_SEARCH:
            raise ValueError("search provenance capability mismatch")
        return self


class PlaceSearchNoResultObservation(ArtifactSnapshotModel):
    record_type: Literal["search_no_result"]
    provenance: ArtifactProvenanceSnapshot

    @model_validator(mode="after")
    def _capability(self) -> "PlaceSearchNoResultObservation":
        if self.provenance.capability is not MapCapability.PLACE_SEARCH:
            raise ValueError("search provenance capability mismatch")
        return self


class PlaceDetailsSuccessObservation(ArtifactSnapshotModel):
    record_type: Literal["details_success"]
    details: ArtifactPlaceDetailsSnapshot
    provenance: ArtifactProvenanceSnapshot

    @model_validator(mode="after")
    def _capability(self) -> "PlaceDetailsSuccessObservation":
        if self.provenance.capability is not MapCapability.PLACE_DETAILS:
            raise ValueError("details provenance capability mismatch")
        if self.details.uid.strip() == "":
            raise ValueError("details uid required")
        return self


class PlaceDetailsNoResultObservation(ArtifactSnapshotModel):
    record_type: Literal["details_no_result"]
    provenance: ArtifactProvenanceSnapshot

    @model_validator(mode="after")
    def _capability(self) -> "PlaceDetailsNoResultObservation":
        if self.provenance.capability is not MapCapability.PLACE_DETAILS:
            raise ValueError("details provenance capability mismatch")
        return self


class PlaceMapFailureObservation(ArtifactSnapshotModel):
    record_type: Literal["map_failure"]
    code: BaiduMapErrorCode
    capability: MapCapability


class PlaceUnverifiedResultObservation(ArtifactSnapshotModel):
    record_type: Literal["unverified_result"]
    capability: MapCapability


class PlaceNotIssuedObservation(ArtifactSnapshotModel):
    record_type: Literal["not_issued"]
    reason_code: Literal["reservation_barrier_failed"]


PlaceObservationRecord: TypeAlias = Annotated[
    PlaceSearchSuccessObservation | PlaceSearchNoResultObservation | PlaceDetailsSuccessObservation
    | PlaceDetailsNoResultObservation | PlaceMapFailureObservation | PlaceUnverifiedResultObservation | PlaceNotIssuedObservation,
    Field(discriminator="record_type"),
]


class ArtifactCandidateCoreSnapshot(ArtifactSnapshotModel):
    uid: Annotated[str, Field(min_length=1, max_length=512)]
    name: Annotated[str, Field(min_length=1, max_length=512)]
    address: Annotated[str, Field(min_length=1, max_length=512)]
    coordinate: ArtifactCoordinateSnapshot
    provider_category: Annotated[str, Field(min_length=1, max_length=512)] | None


class ArtifactRoleAssessmentSnapshot(ArtifactSnapshotModel):
    role: ResearchRole
    verdict: RoleVerdict
    reason_code: RoleReasonCode
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=48)]


class ArtifactRequirementMatchSnapshot(ArtifactSnapshotModel):
    requirement_group_id: ArtifactStableId
    verdict: MatchVerdict
    reason_code: RequirementReasonCode
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=48)]


class ArtifactCandidateDetailsStateSnapshot(ArtifactSnapshotModel):
    status: Literal["not_requested", "success", "no_result"]
    observation_index: Annotated[int, Field(ge=0, le=47)] | None

    @model_validator(mode="after")
    def _details(self) -> "ArtifactCandidateDetailsStateSnapshot":
        if (self.status == "not_requested") != (self.observation_index is None):
            raise ValueError("details status/index mismatch")
        return self


def _derive_artifact_candidate_state(candidate: "ArtifactCandidateRecordSnapshot") -> CandidateState:
    core_conflicted = any(item.reason_code is RoleReasonCode.EVIDENCE_CONFLICTED for item in candidate.roles)
    confirmed_must = any(item.verdict is MatchVerdict.CONFIRMED for item in candidate.must_matches)
    confirmed_avoid = any(item.verdict is MatchVerdict.CONFIRMED for item in candidate.avoid_matches)
    avoid_unresolved = any(item.verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED} for item in candidate.avoid_matches)
    supported_role = any(item.verdict is RoleVerdict.SUPPORTED for item in candidate.roles)
    unresolved_role = any(item.verdict is RoleVerdict.UNRESOLVED for item in candidate.roles)
    all_roles_contradicted = all(item.verdict is RoleVerdict.CONTRADICTED for item in candidate.roles)
    if (supported_role and all_roles_contradicted) or (unresolved_role and all_roles_contradicted) or (not supported_role and not unresolved_role and not all_roles_contradicted):
        raise ValueError("candidate role predicates are impossible")
    if core_conflicted or (confirmed_must and confirmed_avoid):
        return CandidateState.CONFLICTED
    if confirmed_avoid or all_roles_contradicted:
        return CandidateState.EXCLUDED
    if avoid_unresolved or (not supported_role and unresolved_role):
        return CandidateState.WITHHELD
    return CandidateState.ELIGIBLE


class ArtifactCandidateRecordSnapshot(ArtifactSnapshotModel):
    schema_version: Literal["1.0"]
    admission_ordinal: Annotated[int, Field(ge=0, le=127)]
    core: ArtifactCandidateCoreSnapshot
    state: CandidateState
    roles: Annotated[tuple[ArtifactRoleAssessmentSnapshot, ...], Field(min_length=2, max_length=2)]
    interests: Annotated[tuple[InterestTag, ...], Field(min_length=0, max_length=4)]
    meal_intents: Annotated[tuple[MealIntent, ...], Field(min_length=0, max_length=2)]
    must_matches: Annotated[tuple[ArtifactRequirementMatchSnapshot, ...], Field(min_length=0, max_length=100)]
    avoid_matches: Annotated[tuple[ArtifactRequirementMatchSnapshot, ...], Field(min_length=0, max_length=100)]
    details: ArtifactCandidateDetailsStateSnapshot
    observation_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=48)]
    dietary_restrictions: Literal["not_evaluated"]
    mobility_constraints: Literal["not_evaluated"]
    budget_fit: Literal["not_evaluated"]

    @model_validator(mode="after")
    def _semantic_state(self) -> "ArtifactCandidateRecordSnapshot":
        if tuple(item.role for item in self.roles) != (ResearchRole.ATTRACTION, ResearchRole.RESTAURANT):
            raise ValueError("roles must be attraction then restaurant")
        if len(set(self.interests)) != len(self.interests) or tuple(self.interests) != tuple(sorted(self.interests, key=lambda item: list(InterestTag).index(item))):
            raise ValueError("interests must be enum ordered")
        if len(set(self.meal_intents)) != len(self.meal_intents) or tuple(self.meal_intents) != tuple(sorted(self.meal_intents, key=lambda item: list(MealIntent).index(item))):
            raise ValueError("meal intents must be enum ordered")
        if len(set(self.observation_indexes)) != len(self.observation_indexes):
            raise ValueError("observation indexes must be unique")
        if any(len(set(item.evidence_indexes)) != len(item.evidence_indexes) for item in (*self.roles, *self.must_matches, *self.avoid_matches)):
            raise ValueError("evidence indexes must be unique within each judgment")
        own_observations = set(self.observation_indexes)
        if self.details.observation_index is not None and self.details.observation_index not in own_observations:
            raise ValueError("details observation must be in candidate observation directory")
        if any(index not in own_observations for item in (*self.roles, *self.must_matches, *self.avoid_matches) for index in item.evidence_indexes):
            raise ValueError("candidate evidence index must be in candidate observation directory")
        if len({item.requirement_group_id for item in (*self.must_matches, *self.avoid_matches)}) != len(self.must_matches) + len(self.avoid_matches):
            raise ValueError("requirement match groups must be unique within candidate")
        if self.state is not _derive_artifact_candidate_state(self):
            raise ValueError("candidate state is not derivable from evidence")
        return self


class ArtifactMealAssessmentSnapshot(ArtifactSnapshotModel):
    meal_intent: MealIntent
    verdict: MatchVerdict
    reason_code: Literal["targeted_meal_search", "business_hours_supports", "business_hours_conflicts", "business_hours_unknown", "evidence_insufficient"]
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=1, max_length=8)]


class ArtifactCandidateRecordV2Snapshot(ArtifactCandidateRecordSnapshot):
    schema_version: Literal["2.0"]
    meal_assessments: Annotated[tuple[ArtifactMealAssessmentSnapshot, ...], Field(min_length=2, max_length=2)]

    @model_validator(mode="after")
    def _purpose_evidence(self):
        from .purpose import validate_meal_assessments
        validate_meal_assessments(self)
        return self


class ArtifactKnownLLMUsageSnapshot(ArtifactSnapshotModel):
    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]
    reasoning_tokens: Annotated[int, Field(ge=0)]
    prompt_cache_hit_tokens: Annotated[int, Field(ge=0)]
    prompt_cache_miss_tokens: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _usage(self) -> "ArtifactKnownLLMUsageSnapshot":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens or self.prompt_cache_hit_tokens + self.prompt_cache_miss_tokens != self.prompt_tokens or self.reasoning_tokens > self.completion_tokens:
            raise ValueError("usage mismatch")
        return self


class ArtifactResearchCountSummarySnapshot(ArtifactSnapshotModel):
    attraction_count: Annotated[int, Field(ge=0)]
    attraction_target: Annotated[int, Field(ge=1, le=60)]
    restaurant_count: Annotated[int, Field(ge=0)]
    restaurant_target: Annotated[int, Field(ge=1, le=40)]
    unique_eligible_count: Annotated[int, Field(ge=0, le=128)]
    admitted_slots: Annotated[int, Field(ge=0, le=128)]
    candidate_capacity: Literal[128]

    @model_validator(mode="after")
    def _counts(self) -> "ArtifactResearchCountSummarySnapshot":
        if self.attraction_count > self.unique_eligible_count or self.restaurant_count > self.unique_eligible_count:
            raise ValueError("role counts exceed eligible count")
        if self.unique_eligible_count > self.admitted_slots:
            raise ValueError("eligible count exceeds admitted slots")
        return self


class ArtifactResearchCoverageSummarySnapshot(ArtifactSnapshotModel):
    effective_interests: Annotated[tuple[InterestTag, ...], Field(min_length=1, max_length=4)]
    covered_interests: Annotated[tuple[InterestTag, ...], Field(min_length=0, max_length=4)]
    covered_meals: Annotated[tuple[MealIntent, ...], Field(min_length=0, max_length=2)]
    must_item_count: Annotated[int, Field(ge=0, le=100)]
    resolved_must_item_count: Annotated[int, Field(ge=0, le=100)]
    details_success_count: Annotated[int, Field(ge=0, le=128)]
    details_target: Annotated[int, Field(ge=0, le=128)]
    initial_city_attraction_search_done: bool
    initial_hotel_restaurant_nearby_done: bool

    @model_validator(mode="after")
    def _coverage_order(self) -> "ArtifactResearchCoverageSummarySnapshot":
        if len(set(self.effective_interests)) != len(self.effective_interests) or tuple(self.effective_interests) != tuple(sorted(self.effective_interests, key=lambda item: list(InterestTag).index(item))):
            raise ValueError("effective interests must be unique and enum ordered")
        if len(set(self.covered_interests)) != len(self.covered_interests) or tuple(self.covered_interests) != tuple(sorted(self.covered_interests, key=lambda item: list(InterestTag).index(item))):
            raise ValueError("covered interests must be unique and enum ordered")
        if len(set(self.covered_meals)) != len(self.covered_meals) or tuple(self.covered_meals) != tuple(sorted(self.covered_meals, key=lambda item: list(MealIntent).index(item))):
            raise ValueError("covered meals must be unique and enum ordered")
        if not set(self.covered_interests).issubset(self.effective_interests):
            raise ValueError("covered interests must be effective interests")
        if self.resolved_must_item_count > self.must_item_count:
            raise ValueError("resolved must count exceeds total")
        return self


class ArtifactResearchBudgetSummarySnapshot(ArtifactSnapshotModel):
    allow_unknown_admission: ClassVar[bool] = False
    iteration_limit: Literal[6]
    iterations_consumed: Annotated[int, Field(ge=0, le=6)]
    place_call_limit: Literal[48]
    place_calls_consumed: Annotated[int, Field(ge=0, le=48)]
    semantic_invocation_limit: Literal[12]
    semantic_invocations_consumed: Annotated[int, Field(ge=0, le=12)]
    known_provider_attempts: Annotated[int, Field(ge=0, le=24)]
    provider_attempts_complete: bool
    known_usage: ArtifactKnownLLMUsageSnapshot
    usage_complete: bool

    @model_validator(mode="after")
    def _budget_consistency(self) -> "ArtifactResearchBudgetSummarySnapshot":
        if self.iterations_consumed > self.iteration_limit or self.place_calls_consumed > self.place_call_limit or self.semantic_invocations_consumed > self.semantic_invocation_limit:
            raise ValueError("budget counters exceed limits")
        usage = self.known_usage
        if self.known_provider_attempts > self.semantic_invocations_consumed * 2:
            raise ValueError("provider attempts exceed semantic invocation bound")
        if self.provider_attempts_complete and self.known_provider_attempts < self.semantic_invocations_consumed:
            raise ValueError("complete provider attempts must cover semantic invocations")
        if self.semantic_invocations_consumed == 0:
            if self.known_provider_attempts != 0 or (not self.allow_unknown_admission and (not self.provider_attempts_complete or not self.usage_complete)):
                raise ValueError("zero semantic calls require complete zero usage")
            if any(getattr(usage, name) != 0 for name in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")):
                raise ValueError("zero semantic calls require zero usage")
        return self


class ArtifactCandidateCountShortfallSummary(ArtifactSnapshotModel):
    gap_type: Literal["candidate_count_shortfall"]
    gap_id: ArtifactStableId
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=0, max_length=8)]
    role: ResearchRole
    current_count: Annotated[int, Field(ge=0)]
    target_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _shortfall(self) -> "ArtifactCandidateCountShortfallSummary":
        if self.current_count >= self.target_count:
            raise ValueError("count gap requires current_count < target_count")
        return self


class ArtifactInterestCoverageMissingSummary(ArtifactSnapshotModel):
    gap_type: Literal["interest_coverage_missing"]
    gap_id: ArtifactStableId
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=0, max_length=8)]
    interest: InterestTag


class ArtifactAreaCoverageMissingSummary(ArtifactSnapshotModel):
    gap_type: Literal["area_coverage_missing"]
    gap_id: ArtifactStableId
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=0, max_length=8)]
    role: ResearchRole
    anchor: ArtifactResearchAnchorSnapshot
    radius_km: Literal[1, 3, 5, 10]


class ArtifactMealCoverageMissingSummary(ArtifactSnapshotModel):
    gap_type: Literal["meal_coverage_missing"]
    gap_id: ArtifactStableId
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=0, max_length=8)]
    meal_intent: MealIntent
    anchor: ArtifactResearchAnchorSnapshot
    radius_km: Literal[1, 3, 5, 10]


class ArtifactMealCountShortfallSummary(ArtifactMealCoverageMissingSummary):
    source_versions: ClassVar[tuple[str, ...]] = ("2.0", "3.0", "4.0")
    gap_type: Literal["meal_count_shortfall"]
    current_count: Annotated[int, Field(ge=0)]
    target_count: Annotated[int, Field(ge=1)]
    source_candidate_set_ref: ArtifactRef

    @model_validator(mode="after")
    def _source(self):
        ref = self.source_candidate_set_ref
        if ref.artifact_type != "candidate_research.candidate_set" or ref.schema_version not in self.source_versions or self.evidence_indexes:
            raise ValueError("meal quota uses predecessor state proof, not observation indexes")
        if self.current_count >= self.target_count:
            raise ValueError("meal quota already satisfied")
        return self


class ArtifactMustVisitResolutionMissingSummary(ArtifactSnapshotModel):
    gap_type: Literal["must_visit_resolution_missing"]
    gap_id: ArtifactStableId
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=0, max_length=8)]
    requirement_group_id: ArtifactStableId
    requirement_item_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=1, max_length=100)]


class ArtifactAvoidResolutionMissingSummary(ArtifactSnapshotModel):
    gap_type: Literal["avoid_resolution_missing"]
    gap_id: ArtifactStableId
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=0, max_length=8)]
    requirement_group_id: ArtifactStableId
    requirement_item_ids: Annotated[tuple[ArtifactStableId, ...], Field(min_length=1, max_length=100)]
    candidate_uids: Annotated[tuple[ArtifactText, ...], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def _target_unique(self) -> "ArtifactAvoidResolutionMissingSummary":
        if len(set(self.requirement_item_ids)) != len(self.requirement_item_ids):
            raise ValueError("avoid requirement item ids must be unique")
        if len(set(self.candidate_uids)) != len(self.candidate_uids):
            raise ValueError("avoid candidate uids must be unique")
        return self


class ArtifactCandidateDetailsMissingSummary(ArtifactSnapshotModel):
    gap_type: Literal["candidate_details_missing"]
    gap_id: ArtifactStableId
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=0, max_length=8)]
    candidate_uids: Annotated[tuple[ArtifactText, ...], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def _unique_uids(self) -> "ArtifactCandidateDetailsMissingSummary":
        if len(set(self.candidate_uids)) != len(self.candidate_uids):
            raise ValueError("candidate uids must be unique")
        return self


class ArtifactReplacementCandidateNeededSummary(ArtifactSnapshotModel):
    gap_type: Literal["replacement_candidate_needed"]
    gap_id: ArtifactStableId
    evidence_indexes: Annotated[tuple[Annotated[int, Field(ge=0, le=47)], ...], Field(min_length=0, max_length=8)]
    rejected_uid: ArtifactText
    role: ResearchRole
    anchor: ArtifactResearchAnchorSnapshot | None
    radius_km: Literal[1, 3, 5, 10] | None

    @model_validator(mode="after")
    def _anchor_pair(self) -> "ArtifactReplacementCandidateNeededSummary":
        if (self.anchor is None) != (self.radius_km is None):
            raise ValueError("anchor and radius_km must be both present or absent")
        return self


ArtifactResearchGapSummarySnapshot: TypeAlias = Annotated[
    ArtifactCandidateCountShortfallSummary | ArtifactInterestCoverageMissingSummary | ArtifactAreaCoverageMissingSummary
    | ArtifactMealCoverageMissingSummary | ArtifactMealCountShortfallSummary | ArtifactMustVisitResolutionMissingSummary | ArtifactAvoidResolutionMissingSummary | ArtifactCandidateDetailsMissingSummary
    | ArtifactReplacementCandidateNeededSummary,
    Field(discriminator="gap_type"),
]


class SearchCoverageRecord(ArtifactSnapshotModel):
    objective_id: ArtifactStableId
    objective_kind: ResearchObjectiveKind
    action_fingerprint: ActionFingerprint
    observation_index: Annotated[int, Field(ge=0, le=47)]
    role: ResearchRole | None
    interest: InterestTag | None
    meal_intent: MealIntent | None
    eligible_candidate_ordinals: Annotated[
        tuple[Annotated[int, Field(ge=0, le=127)], ...], Field(min_length=0, max_length=128)
    ]


class SearchCoverageState(ArtifactSnapshotModel):
    records: Annotated[tuple[SearchCoverageRecord, ...], Field(min_length=0, max_length=48)]
    initial_city_attraction_search_done: bool
    initial_hotel_restaurant_nearby_done: bool

    @model_validator(mode="after")
    def _records(self) -> "SearchCoverageState":
        if tuple(item.observation_index for item in self.records) != tuple(sorted(item.observation_index for item in self.records)):
            raise ValueError("search coverage records must preserve observation order")
        if len({item.action_fingerprint for item in self.records}) != len(self.records):
            raise ValueError("search coverage action fingerprints must be unique")
        if any(tuple(sorted(item.eligible_candidate_ordinals)) != item.eligible_candidate_ordinals or len(set(item.eligible_candidate_ordinals)) != len(item.eligible_candidate_ordinals) for item in self.records):
            raise ValueError("search coverage candidate ordinals must be ordered and unique")
        return self


class CandidateSetStateSnapshot(ArtifactSnapshotModel):
    qualified_supply: ClassVar[bool] = False
    action_versions: ClassVar[tuple[str, ...]] = ("1.0", "2.0")
    judgment_versions: ClassVar[tuple[str, ...]] = ("1.0", "2.0", "3.0")
    confirmed_requirement_digest: ContentDigest
    hotel_selection_ref: ArtifactRef
    effective_interests: Annotated[tuple[InterestTag, ...], Field(min_length=1, max_length=4)]
    model_action_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=0, max_length=6)]
    model_judgment_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=0, max_length=6)]
    observation_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=0, max_length=48)]
    candidates: Annotated[tuple[Annotated[ArtifactCandidateRecordSnapshot | ArtifactCandidateRecordV2Snapshot, Field(discriminator="schema_version")], ...], Field(min_length=0, max_length=128)]
    requirement_group_states: Annotated[tuple[ArtifactRequirementGroupState, ...], Field(min_length=0, max_length=200)]
    search_coverage: SearchCoverageState
    counts: ArtifactResearchCountSummarySnapshot
    coverage: ArtifactResearchCoverageSummarySnapshot
    budgets: ArtifactResearchBudgetSummarySnapshot
    pending_gaps: Annotated[tuple[ArtifactResearchGapSummarySnapshot, ...], Field(min_length=0, max_length=128)]
    iteration_no_progress_streak: Annotated[int, Field(ge=0, le=2)]

    @model_validator(mode="after")
    def _directories_and_derived_state(self) -> "CandidateSetStateSnapshot":
        if self.hotel_selection_ref.artifact_type != "hotel.selection" or self.hotel_selection_ref.schema_version != "1.0":
            raise ValueError("invalid hotel selection ref")
        ref_groups = (
            (self.model_action_refs, "candidate_research.model_action"),
            (self.model_judgment_refs, "candidate_research.model_judgment"),
            (self.observation_refs, "candidate_research.place_observation"),
        )
        all_refs = (self.hotel_selection_ref, *(ref for refs, _ in ref_groups for ref in refs))
        run_ids = {ref.operation_id.run_id for ref in all_refs}
        if len(run_ids) != 1:
            raise ValueError("artifact refs must share run id")
        if len({ref.artifact_id for ref in all_refs}) != len(all_refs):
            raise ValueError("artifact refs must be unique")
        if len({(ref.operation_id.run_id, ref.operation_id.phase, ref.operation_id.logical_step) for ref in all_refs}) != len(all_refs):
            raise ValueError("artifact ref operation ids must be unique")
        for refs, expected_type in ref_groups:
            for ref in refs:
                if ref.artifact_type != expected_type or ref.schema_version not in (self.judgment_versions if expected_type == "candidate_research.model_judgment" else self.action_versions if expected_type == "candidate_research.model_action" else ("1.0",)):
                    raise ValueError("artifact ref directory type/version mismatch")
                if ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
                    raise ValueError("candidate research refs must use candidate_research phase")
            if tuple(ref.created_at for ref in refs) != tuple(sorted(ref.created_at for ref in refs)):
                raise ValueError("artifact ref directories must preserve creation order")
            if len({ref.operation_id.logical_step for ref in refs}) != len(refs):
                raise ValueError("artifact ref operation steps must be unique")
        if self.model_judgment_refs and not self.observation_refs:
            raise ValueError("judgment refs require observation refs")
        if self.hotel_selection_ref.operation_id.phase is not WorkflowPhase.HOTEL_SELECTION:
            raise ValueError("hotel selection ref must use hotel_selection phase")
        ordinals = tuple(item.admission_ordinal for item in self.candidates)
        if ordinals != tuple(range(len(ordinals))):
            raise ValueError("candidate admission ordinals must be contiguous")
        if len({item.core.uid for item in self.candidates}) != len(self.candidates):
            raise ValueError("candidate UIDs must be unique")
        if any(index >= len(self.observation_refs) for candidate in self.candidates for index in candidate.observation_indexes):
            raise ValueError("candidate observation index is outside ref directory")
        from .purpose import candidate_admitted_purposes
        purposes = {item.admission_ordinal: candidate_admitted_purposes(item) for item in self.candidates}
        eligible = tuple(item for item in self.candidates if (bool(purposes[item.admission_ordinal])
                         if self.qualified_supply else item.state is CandidateState.ELIGIBLE))
        attraction_count = sum("attraction" in purposes[item.admission_ordinal] if self.qualified_supply else
            any(role.role is ResearchRole.ATTRACTION and role.verdict is RoleVerdict.SUPPORTED for role in item.roles) for item in eligible)
        restaurant_count = sum(any(meal.value in purposes[item.admission_ordinal] for meal in MealIntent) if self.qualified_supply else
            any(role.role is ResearchRole.RESTAURANT and role.verdict is RoleVerdict.SUPPORTED for role in item.roles) for item in eligible)
        details_success_count = sum(item.details.status == "success" for item in eligible)
        if self.counts.admitted_slots != len(self.candidates) or self.counts.unique_eligible_count != len(eligible):
            raise ValueError("candidate count summary mismatch")
        if self.counts.attraction_count != attraction_count or self.counts.restaurant_count != restaurant_count:
            raise ValueError("role count summary mismatch")
        if self.coverage.details_success_count != details_success_count:
            raise ValueError("details coverage mismatch")
        expected_details_target = (self.counts.unique_eligible_count + 2) // 3
        if self.coverage.details_target != expected_details_target:
            raise ValueError("details target mismatch")
        coverage_records = self.search_coverage.records
        searched_interests = {item.interest for item in coverage_records if item.objective_kind is ResearchObjectiveKind.INTEREST_COVERAGE and item.interest is not None}
        searched_meals = {item.meal_intent for item in coverage_records if item.objective_kind in {ResearchObjectiveKind.MEAL_LUNCH, ResearchObjectiveKind.MEAL_DINNER} and item.meal_intent is not None}
        covered_interests = tuple(item for item in self.effective_interests if item in searched_interests and any(
            item in candidate.interests and (not self.qualified_supply or "attraction" in purposes[candidate.admission_ordinal]) for candidate in eligible))
        covered_meals = tuple(item for item in MealIntent if item in searched_meals and any(
            item.value in purposes[candidate.admission_ordinal] if self.qualified_supply else item in candidate.meal_intents for candidate in eligible))
        if self.coverage.effective_interests != self.effective_interests:
            raise ValueError("effective interests summary mismatch")
        derived_initial_city = any(item.objective_kind is ResearchObjectiveKind.INITIAL_CITY_ATTRACTION for item in coverage_records)
        derived_initial_hotel = any(item.objective_kind is ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY for item in coverage_records)
        if (self.search_coverage.initial_city_attraction_search_done, self.search_coverage.initial_hotel_restaurant_nearby_done) != (derived_initial_city, derived_initial_hotel):
            raise ValueError("initial search flags must derive from search records")
        if (self.coverage.initial_city_attraction_search_done, self.coverage.initial_hotel_restaurant_nearby_done) != (derived_initial_city, derived_initial_hotel):
            raise ValueError("initial search coverage mismatch")
        if self.coverage.covered_interests != covered_interests or self.coverage.covered_meals != covered_meals:
            raise ValueError("coverage summary mismatch")
        if len({item.group_id for item in self.requirement_group_states}) != len(self.requirement_group_states):
            raise ValueError("requirement groups must be unique")
        groups_by_id = {item.group_id: item for item in self.requirement_group_states}
        for candidate in self.candidates:
            for match in candidate.must_matches:
                group = groups_by_id.get(match.requirement_group_id)
                if group is None or group.requirement_kind != "must_visit":
                    raise ValueError("candidate must match has no matching must group")
                if candidate.admission_ordinal not in group.evaluated_candidate_ordinals:
                    raise ValueError("candidate must match is not evaluated by its group")
                override = next((item.verdict for item in group.exceptions if item.candidate_ordinal == candidate.admission_ordinal), group.default_verdict)
                if match.verdict is not override:
                    raise ValueError("candidate must match disagrees with group state")
            for match in candidate.avoid_matches:
                group = groups_by_id.get(match.requirement_group_id)
                if group is None or group.requirement_kind != "avoid":
                    raise ValueError("candidate avoid match has no matching avoid group")
                if candidate.admission_ordinal not in group.evaluated_candidate_ordinals:
                    raise ValueError("candidate avoid match is not evaluated by its group")
                override = next((item.verdict for item in group.exceptions if item.candidate_ordinal == candidate.admission_ordinal), group.default_verdict)
                if match.verdict is not override:
                    raise ValueError("candidate avoid match disagrees with group state")
        for group in self.requirement_group_states:
            if tuple(sorted(group.evaluated_candidate_ordinals)) != group.evaluated_candidate_ordinals or len(set(group.evaluated_candidate_ordinals)) != len(group.evaluated_candidate_ordinals):
                raise ValueError("evaluated candidate ordinals must be ordered and unique")
            if any(item < 0 or item >= len(self.candidates) for item in group.evaluated_candidate_ordinals):
                raise ValueError("evaluated candidate ordinal outside candidate directory")
            if any(index >= len(self.observation_refs) for index in group.default_evidence_indexes):
                raise ValueError("group default evidence index outside ref directory")
            if any(index >= len(self.observation_refs) for item in group.exceptions for index in item.evidence_indexes):
                raise ValueError("group exception evidence index outside ref directory")
        if len({item.action_fingerprint for item in coverage_records}) != len(coverage_records):
            raise ValueError("search coverage action fingerprints must be unique")
        if any(item.observation_index >= len(self.observation_refs) for item in coverage_records):
            raise ValueError("search coverage observation outside ref directory")
        if any(tuple(sorted(item.eligible_candidate_ordinals)) != item.eligible_candidate_ordinals or len(set(item.eligible_candidate_ordinals)) != len(item.eligible_candidate_ordinals) for item in coverage_records):
            raise ValueError("search coverage candidate ordinals must be ordered and unique")
        if any(any(ordinal < 0 or ordinal >= len(self.candidates) for ordinal in item.eligible_candidate_ordinals) for item in coverage_records):
            raise ValueError("search coverage candidate ordinal outside candidate directory")
        if any(any(self.candidates[ordinal].state is not CandidateState.ELIGIBLE for ordinal in item.eligible_candidate_ordinals) for item in coverage_records):
            raise ValueError("search coverage may only reference eligible candidates")
        if any(index >= len(self.observation_refs) for gap in self.pending_gaps for index in gap.evidence_indexes):
            raise ValueError("pending gap evidence index outside ref directory")

        # A terminal gap summary may intentionally have no evidence when the
        # controller can prove the business gap from state alone.  Conversely,
        # once a related verified Place Observation exists, every summary must
        # point at one to avoid fabricating or misattributing evidence.  The
        # relation is structural (coverage record, candidate evidence, or
        # requirement-group evidence), never text similarity.
        for gap in self.pending_gaps:
            related = _gap_related_observation_indexes(self, gap)
            indexes = tuple(gap.evidence_indexes)
            if indexes and not set(indexes).issubset(related):
                raise ValueError("gap evidence indexes are unrelated to target")
            if not indexes and related:
                raise ValueError("empty gap evidence is only valid without related observations")
        for gap in self.pending_gaps:
            if isinstance(gap, ArtifactAvoidResolutionMissingSummary):
                group = next((item for item in self.requirement_group_states if item.group_id == gap.requirement_group_id), None)
                if group is None or group.requirement_kind != "avoid" or tuple(group.requirement_item_ids) != tuple(gap.requirement_item_ids):
                    raise ValueError("avoid gap group/items do not match state")
                expected_uids: list[str] = []
                for candidate in self.candidates:
                    if candidate.admission_ordinal not in group.evaluated_candidate_ordinals:
                        continue
                    match = next((item for item in candidate.avoid_matches if item.requirement_group_id == group.group_id), None)
                    verdict = match.verdict if match is not None else group.default_verdict
                    if verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED}:
                        expected_uids.append(candidate.core.uid)
                if tuple(gap.candidate_uids) != tuple(expected_uids):
                    raise ValueError("avoid gap candidate target does not match unresolved state")
        must_item_count = sum(len(group.requirement_item_ids) for group in self.requirement_group_states if group.requirement_kind == "must_visit")

        def effective_group_verdicts(group: ArtifactRequirementGroupState) -> tuple[tuple[int, MatchVerdict], ...]:
            overrides = {item.candidate_ordinal: item.verdict for item in group.exceptions}
            return tuple((ordinal, overrides.get(ordinal, group.default_verdict)) for ordinal in group.evaluated_candidate_ordinals)

        resolved_must_item_count = 0
        for group in self.requirement_group_states:
            if group.requirement_kind != "must_visit":
                continue
            effective = effective_group_verdicts(group)
            resolved = any(
                (bool(purposes[ordinal]) if self.qualified_supply else self.candidates[ordinal].state is CandidateState.ELIGIBLE)
                and any(match.requirement_group_id == group.group_id and match.verdict is MatchVerdict.CONFIRMED for match in self.candidates[ordinal].must_matches)
                for ordinal, _ in effective
            )
            if resolved:
                resolved_must_item_count += len(group.requirement_item_ids)
        if self.coverage.must_item_count != must_item_count or self.coverage.resolved_must_item_count != resolved_must_item_count:
            raise ValueError("must coverage summary mismatch")
        return self


class QualifiedCandidateSetStateSnapshot(CandidateSetStateSnapshot):
    """版本4按实际可用用途验证统计，历史状态保留原字段和验证语义。"""
    qualified_supply: ClassVar[bool] = True


def _gap_related_observation_indexes(
    state: CandidateSetStateSnapshot,
    gap: ArtifactResearchGapSummarySnapshot,
) -> set[int]:
    """Return structurally related Place Observation indexes for a gap."""
    gap_type = gap.gap_type
    records = state.search_coverage.records
    related: set[int] = set()
    if gap_type == "candidate_count_shortfall":
        kinds = (
            {ResearchObjectiveKind.ATTRACTION_COUNT, ResearchObjectiveKind.INITIAL_CITY_ATTRACTION}
            if gap.role is ResearchRole.ATTRACTION
            else {ResearchObjectiveKind.RESTAURANT_COUNT, ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY}
        )
        related.update(item.observation_index for item in records if item.objective_kind in kinds)
    elif gap_type == "interest_coverage_missing":
        related.update(item.observation_index for item in records if item.interest is gap.interest)
        related.update(index for candidate in state.candidates for index in candidate.observation_indexes if gap.interest in candidate.interests)
    elif gap_type == "meal_coverage_missing":
        related.update(item.observation_index for item in records if item.meal_intent is gap.meal_intent)
        related.update(index for candidate in state.candidates for index in candidate.observation_indexes if gap.meal_intent in candidate.meal_intents)
    elif gap_type == "area_coverage_missing":
        related.update(item.observation_index for item in records if item.role is gap.role)
    elif gap_type in {"must_visit_resolution_missing", "avoid_resolution_missing"}:
        group_id = gap.requirement_group_id
        group = next((item for item in state.requirement_group_states if item.group_id == group_id), None)
        if group is not None:
            related.update(group.default_evidence_indexes)
            related.update(index for item in group.exceptions for index in item.evidence_indexes)
            matches = "must_matches" if gap_type == "must_visit_resolution_missing" else "avoid_matches"
            related.update(index for candidate in state.candidates for match in getattr(candidate, matches) if match.requirement_group_id == group_id for index in match.evidence_indexes)
    elif gap_type == "candidate_details_missing":
        wanted = set(gap.candidate_uids)
        related.update(index for candidate in state.candidates if candidate.core.uid in wanted for index in candidate.observation_indexes)
    elif gap_type == "replacement_candidate_needed":
        related.update(item.observation_index for item in records if item.role is gap.role)
    return {index for index in related if 0 <= index < len(state.observation_refs)}


def _candidate_set_state_complete(state: CandidateSetStateSnapshot, *, require_all_avoid: bool = True) -> bool:
    """Derive the terminal completion gate from immutable candidate-set state."""
    if not state.search_coverage.initial_city_attraction_search_done or not state.search_coverage.initial_hotel_restaurant_nearby_done:
        return False
    if state.coverage.covered_interests != state.effective_interests:
        return False
    if state.coverage.covered_meals != tuple(MealIntent):
        return False
    if state.counts.attraction_count < state.counts.attraction_target or state.counts.restaurant_count < state.counts.restaurant_target:
        return False
    if state.coverage.details_success_count < state.coverage.details_target:
        return False
    if state.qualified_supply:
        return state.coverage.resolved_must_item_count == state.coverage.must_item_count
    by_ordinal = {candidate.admission_ordinal: candidate for candidate in state.candidates}
    for group in state.requirement_group_states:
        overrides = {item.candidate_ordinal: item for item in group.exceptions}
        effective = {
            ordinal: overrides.get(ordinal).verdict if ordinal in overrides else group.default_verdict
            for ordinal in group.evaluated_candidate_ordinals
        }
        if group.requirement_kind == "must_visit":
            if not any(
                by_ordinal[ordinal].state is CandidateState.ELIGIBLE
                and any(match.requirement_group_id == group.group_id and match.verdict is MatchVerdict.CONFIRMED for match in by_ordinal[ordinal].must_matches)
                for ordinal in group.evaluated_candidate_ordinals
            ):
                return False
        elif require_all_avoid and any(verdict in {MatchVerdict.AMBIGUOUS, MatchVerdict.UNRESOLVED} for verdict in effective.values()):
            return False
        else:
            if set(group.evaluated_candidate_ordinals) != set(by_ordinal):
                return False
            allowed = {CandidateState.EXCLUDED} if require_all_avoid else {CandidateState.EXCLUDED, CandidateState.CONFLICTED}
            if any(verdict is MatchVerdict.CONFIRMED and by_ordinal[ordinal].state not in allowed for ordinal, verdict in effective.items()):
                return False
    return True


class InProgressCandidateSetRecord(ArtifactSnapshotModel):
    record_type: Literal["in_progress"]
    state: CandidateSetStateSnapshot


class CompletedCandidateSetRecord(ArtifactSnapshotModel):
    record_type: Literal["completed"]
    transition_kind: Literal["normal", "no_op"]
    state: CandidateSetStateSnapshot
    counts: ArtifactResearchCountSummarySnapshot
    coverage: ArtifactResearchCoverageSummarySnapshot
    budgets: ArtifactResearchBudgetSummarySnapshot

    @model_validator(mode="after")
    def _same_summary(self) -> "CompletedCandidateSetRecord":
        if self.counts != self.state.counts or self.coverage != self.state.coverage or self.budgets != self.state.budgets:
            raise ValueError("terminal summaries must equal state summaries")
        if self.state.pending_gaps:
            raise ValueError("completed state must have no pending gaps")
        if not _candidate_set_state_complete(self.state, require_all_avoid=False):
            raise ValueError("completed state does not satisfy all completion gates")
        return self


class IncompleteCandidateSetRecord(ArtifactSnapshotModel):
    record_type: Literal["incomplete"]
    transition_kind: Literal["normal", "no_op"]
    state: CandidateSetStateSnapshot
    counts: ArtifactResearchCountSummarySnapshot
    coverage: ArtifactResearchCoverageSummarySnapshot
    budgets: ArtifactResearchBudgetSummarySnapshot
    gaps: Annotated[tuple[ArtifactResearchGapSummarySnapshot, ...], Field(min_length=1, max_length=128)]
    stop_reasons: Annotated[tuple[CandidateResearchStopReason, ...], Field(min_length=1, max_length=7)]

    @model_validator(mode="after")
    def _same_summary(self) -> "IncompleteCandidateSetRecord":
        if self.counts != self.state.counts or self.coverage != self.state.coverage or self.budgets != self.state.budgets:
            raise ValueError("terminal summaries must equal state summaries")
        if not self.gaps or self.state.pending_gaps != self.gaps:
            raise ValueError("incomplete gaps must equal pending gaps")
        if len(set(self.stop_reasons)) != len(self.stop_reasons):
            raise ValueError("stop reasons must be unique")
        if tuple(self.stop_reasons) != tuple(sorted(self.stop_reasons, key=lambda item: list(CandidateResearchStopReason).index(item))):
            raise ValueError("stop reasons must follow design order")
        return self


class ArtifactLLMResearchFailureSnapshot(ArtifactSnapshotModel):
    failure_type: Literal["llm_failure"]
    invocation_kind: ResearchInvocationKind
    error: ArtifactLLMErrorSnapshot


class ArtifactMapResearchFailureSnapshot(ArtifactSnapshotModel):
    failure_type: Literal["map_failure"]
    tool: ResearchTool
    action_ordinal: Annotated[int, Field(ge=0, le=7)]
    code: BaiduMapErrorCode
    capability: MapCapability
    observation_ref: ArtifactRef

    @model_validator(mode="after")
    def _map_contract(self) -> "ArtifactMapResearchFailureSnapshot":
        expected = MapCapability.PLACE_SEARCH if self.tool is ResearchTool.SEARCH_PLACES else MapCapability.PLACE_DETAILS
        if self.capability is not expected or self.observation_ref.artifact_type != "candidate_research.place_observation" or self.observation_ref.schema_version != "1.0" or self.observation_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
            raise ValueError("map failure capability/ref mismatch")
        return self


class ArtifactControlResearchFailureSnapshot(ArtifactSnapshotModel):
    failure_type: Literal["control_failure"]
    code: CandidateResearchControlFailureCode


CandidateResearchFailureSnapshot: TypeAlias = Annotated[
    ArtifactLLMResearchFailureSnapshot | ArtifactMapResearchFailureSnapshot | ArtifactControlResearchFailureSnapshot,
    Field(discriminator="failure_type"),
]


class FailedCandidateSetRecord(ArtifactSnapshotModel):
    candidate_versions: ClassVar[tuple[str, ...]] = ("1.0", "2.0", "3.0", "4.0")
    action_versions: ClassVar[tuple[str, ...]] = ("1.0", "2.0")
    judgment_versions: ClassVar[tuple[str, ...]] = ("1.0", "2.0", "3.0")
    record_type: Literal["failed"]
    state: CandidateSetStateSnapshot | None
    failure: CandidateResearchFailureSnapshot
    last_consistent_candidate_set_ref: ArtifactRef | None
    failure_artifact_ref: ArtifactRef | None
    budgets: ArtifactResearchBudgetSummarySnapshot
    retryable: bool
    possible_duplicate: bool

    @model_validator(mode="after")
    def _failure_flags(self) -> "FailedCandidateSetRecord":
        if isinstance(self.failure, ArtifactLLMResearchFailureSnapshot):
            expected = (self.failure.error.retryable, self.failure.error.possible_duplicate)
        elif isinstance(self.failure, ArtifactMapResearchFailureSnapshot):
            retryable_codes = {BaiduMapErrorCode.RATE_LIMIT_ERROR, BaiduMapErrorCode.TIMEOUT_ERROR, BaiduMapErrorCode.TRANSPORT_ERROR, BaiduMapErrorCode.SESSION_ERROR}
            duplicate_codes = {BaiduMapErrorCode.TIMEOUT_ERROR, BaiduMapErrorCode.TRANSPORT_ERROR}
            expected = (self.failure.code in retryable_codes, self.failure.code in duplicate_codes)
        else:
            expected = (False, self.failure.code in {CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE, CandidateResearchControlFailureCode.PERSISTENCE_ERROR})
        if (self.retryable, self.possible_duplicate) != expected:
            raise ValueError("failure flags mismatch")
        if self.state is not None and self.budgets != self.state.budgets:
            raise ValueError("failed record budget must equal state budget")
        if self.state is not None:
            state_refs = (
                self.state.hotel_selection_ref,
                *self.state.model_action_refs,
                *self.state.model_judgment_refs,
                *self.state.observation_refs,
            )
            state_runs = {ref.operation_id.run_id for ref in state_refs}
            for ref in (self.last_consistent_candidate_set_ref, self.failure_artifact_ref):
                if ref is not None and state_runs and ref.operation_id.run_id not in state_runs:
                    raise ValueError("failed ref run differs from state directory")
        for ref in (self.last_consistent_candidate_set_ref, self.failure_artifact_ref):
            versions = (self.candidate_versions if ref is not None and ref.artifact_type == "candidate_research.candidate_set"
                        else self.judgment_versions if ref is not None and ref.artifact_type == "candidate_research.model_judgment"
                        else self.action_versions if ref is not None and ref.artifact_type == "candidate_research.model_action" else ("1.0",))
            if ref is not None and (ref.schema_version not in versions or ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH):
                raise ValueError("failure ref schema mismatch")
        failure_refs = [
            ref for ref in (self.last_consistent_candidate_set_ref, self.failure_artifact_ref)
            if ref is not None
        ]
        if isinstance(self.failure, ArtifactMapResearchFailureSnapshot):
            failure_refs.append(self.failure.observation_ref)
        if len({ref.operation_id.run_id for ref in failure_refs}) > 1:
            raise ValueError("failure refs must share run")
        if self.last_consistent_candidate_set_ref is not None and self.last_consistent_candidate_set_ref.artifact_type != "candidate_research.candidate_set":
            raise ValueError("last consistent ref must be candidate set")
        if isinstance(self.failure, ArtifactLLMResearchFailureSnapshot):
            expected_type = "candidate_research.model_action" if self.failure.invocation_kind is ResearchInvocationKind.ACTION else "candidate_research.model_judgment"
            if self.failure_artifact_ref is None or self.failure_artifact_ref.artifact_type != expected_type:
                raise ValueError("llm failure artifact ref must match invocation kind")
        elif isinstance(self.failure, ArtifactMapResearchFailureSnapshot):
            if self.failure_artifact_ref is None or self.failure_artifact_ref != self.failure.observation_ref:
                raise ValueError("map failure artifact ref must equal observation ref")
        else:
            if self.failure_artifact_ref is not None and self.failure_artifact_ref.artifact_type not in {
                "candidate_research.model_action", "candidate_research.model_judgment", "candidate_research.place_observation",
            }:
                raise ValueError("control failure artifact ref type invalid")
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
            if self.state is not None and self.failure_artifact_ref is not None:
                if expected_type is None:
                    directories = self.state.model_action_refs + self.state.model_judgment_refs + self.state.observation_refs
                elif expected_type == "candidate_research.model_action":
                    directories = self.state.model_action_refs
                elif expected_type == "candidate_research.model_judgment":
                    directories = self.state.model_judgment_refs
                else:
                    directories = self.state.observation_refs
                if self.failure_artifact_ref not in directories:
                    raise ValueError("control failure artifact ref must be in state directory")
        return self


CandidateSetRecord: TypeAlias = Annotated[
    InProgressCandidateSetRecord | CompletedCandidateSetRecord | IncompleteCandidateSetRecord | FailedCandidateSetRecord,
    Field(discriminator="record_type"),
]


class QualifiedInProgressCandidateSetRecord(InProgressCandidateSetRecord):
    state: QualifiedCandidateSetStateSnapshot


class QualifiedCompletedCandidateSetRecord(CompletedCandidateSetRecord):
    state: QualifiedCandidateSetStateSnapshot


class QualifiedIncompleteCandidateSetRecord(IncompleteCandidateSetRecord):
    state: QualifiedCandidateSetStateSnapshot


class QualifiedFailedCandidateSetRecord(FailedCandidateSetRecord):
    state: QualifiedCandidateSetStateSnapshot | None


QualifiedCandidateSetRecord: TypeAlias = Annotated[
    QualifiedInProgressCandidateSetRecord | QualifiedCompletedCandidateSetRecord |
    QualifiedIncompleteCandidateSetRecord | QualifiedFailedCandidateSetRecord,
    Field(discriminator="record_type"),
]


class CandidateResearchModelActionArtifact(ArtifactSnapshotModel):
    schema_version: Literal["1.0", "2.0"]
    run_id: UUID
    mode: ResearchMode
    invocation_index: Annotated[int, Field(ge=0, le=11)]
    invocation_slot_index: Annotated[int, Field(ge=0, le=5)]
    iteration_index: Annotated[int, Field(ge=0, le=5)]
    prompt_version: Literal["action-v1", "action-v2"]
    prompt_sha256: ContentDigest
    input_digest: ContentDigest
    result: Annotated[ModelActionRecord | ModelActionRecoveredRecord, Field(discriminator="record_type")]

    @model_validator(mode="after")
    def _identity(self) -> "CandidateResearchModelActionArtifact":
        _validate_recovery_record(self.result, self.schema_version, self.invocation_index, action=True)
        record = unwrap_model_record(self.result)
        if (self.schema_version == "2.0") != (self.prompt_version == "action-v2"):
            raise ValueError("action prompt/schema version mismatch")
        if self.schema_version == "1.0" and isinstance(self.result, ModelActionPolicyRejectedRecord):
            if self.result.reason_code != "highest_priority_objective_ignored" or not isinstance(self.result.output, ProposedActionBatchSnapshot):
                raise ValueError("legacy policy record changed")
        if self.prompt_sha256 != self.prompt_sha256.lower() or len(self.prompt_sha256) != 71:
            raise ValueError("prompt hash must be sha256 content digest")
        if self.input_digest != self.input_digest.lower() or len(self.input_digest) != 71:
            raise ValueError("input digest must be sha256 content digest")
        if isinstance(record, ModelActionAcceptedRecord):
            if any(item.reused_observation_ref is not None and (item.reused_observation_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH or item.reused_observation_ref.operation_id.run_id != self.run_id) for item in record.action_dispositions):
                raise ValueError("reused observation ref run mismatch")
        return self


class CandidateResearchLegacyModelJudgmentArtifact(ArtifactSnapshotModel):
    schema_version: Literal["1.0", "2.0"]
    run_id: UUID
    mode: ResearchMode
    invocation_index: Annotated[int, Field(ge=0, le=11)]
    invocation_slot_index: Annotated[int, Field(ge=0, le=5)]
    iteration_index: Annotated[int, Field(ge=0, le=5)]
    prompt_version: Literal["observation-v1", "observation-v2", "observation-v3"]
    prompt_sha256: ContentDigest
    evidence_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=1, max_length=48)]
    result: Annotated[ModelJudgmentRecord | ModelJudgmentRecoveredRecord, Field(discriminator="record_type")]

    @model_validator(mode="after")
    def _evidence_directory(self) -> "CandidateResearchModelJudgmentArtifact":
        _validate_recovery_record(self.result, self.schema_version, self.invocation_index)
        record = unwrap_model_record(self.result)
        if (self.schema_version == "2.0") != (self.prompt_version == "observation-v3"):
            raise ValueError("judgment prompt/schema version mismatch")
        if self.schema_version == "1.0" and isinstance(self.result, ModelJudgmentControllerRejectedRecord) and self.result.code != "judgment_patch_invalid":
            raise ValueError("legacy judgment cannot contain new controller code")
        if self.schema_version == "2.0" and record.record_type == "controller_rejected" and record.code == "judgment_required_missing":
            raise ValueError("required judgment code requires schema 3")
        if any(ref.artifact_type != "candidate_research.place_observation" or ref.schema_version not in ({"1.0", "2.0", "3.0", "4.0"} if ref.artifact_type == "candidate_research.candidate_set" else {"1.0", "2.0", "3.0"} if ref.artifact_type == "candidate_research.model_judgment" else {"1.0", "2.0"} if ref.artifact_type == "candidate_research.model_action" else {"1.0"}) or ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH or ref.operation_id.run_id != self.run_id for ref in self.evidence_refs):
            raise ValueError("judgment evidence refs must be place observations from same run")
        if len({ref.artifact_id for ref in self.evidence_refs}) != len(self.evidence_refs):
            raise ValueError("judgment evidence refs must be unique")
        if len({(ref.operation_id.run_id, ref.operation_id.phase, ref.operation_id.logical_step) for ref in self.evidence_refs}) != len(self.evidence_refs):
            raise ValueError("judgment evidence operation ids must be unique")
        if tuple(ref.created_at for ref in self.evidence_refs) != tuple(sorted(ref.created_at for ref in self.evidence_refs)):
            raise ValueError("judgment evidence refs must preserve creation order")
        def evidence_index(handle_id: str) -> int:
            match = re.fullmatch(r"e:(\d+)", handle_id)
            if match is None:
                raise ValueError("invalid evidence handle")
            return int(match.group(1))

        def check_indexes(indexes: tuple[int, ...]) -> None:
            if any(index >= len(self.evidence_refs) for index in indexes):
                raise ValueError("judgment evidence index outside ref directory")

        if isinstance(record, ModelJudgmentAcceptedRecord):
            patch = record.patch
            for update in (*patch.role_updates, *patch.interest_updates, *patch.meal_updates):
                check_indexes(update.evidence_indexes)
            for group in patch.requirement_group_updates:
                check_indexes(group.default_evidence_indexes)
                for exception in group.exceptions:
                    check_indexes(exception.evidence_indexes)
        elif isinstance(record, ModelJudgmentControllerRejectedRecord) and self.schema_version == "1.0":
            # V2 rejected patches are audit records of invalid proposals, never
            # accepted evidence. Preserve the historical v1 reader contract.
            patch = record.proposed_patch
            for update in (*patch.role_updates, *patch.interest_updates, *patch.meal_updates):
                for handle in update.evidence_handles:
                    if evidence_index(handle.handle_id) >= len(self.evidence_refs):
                        raise ValueError("controller judgment evidence handle outside ref directory")
            for group in patch.requirement_group_updates:
                for handle in group.default_evidence_handles:
                    if evidence_index(handle.handle_id) >= len(self.evidence_refs):
                        raise ValueError("controller judgment evidence handle outside ref directory")
                for exception in group.exceptions:
                    for handle in exception.evidence_handles:
                        if evidence_index(handle.handle_id) >= len(self.evidence_refs):
                            raise ValueError("controller judgment evidence handle outside ref directory")
        return self


class CandidateResearchModelJudgmentArtifact(CandidateResearchLegacyModelJudgmentArtifact):
    schema_version: Literal["3.0"]
    prompt_version: Literal["observation-v4"]
    job: "JudgmentJob"

    @model_validator(mode="after")
    def _job_directory(self):
        if self.job.iteration_index != self.iteration_index:
            raise ValueError("judgment job must match artifact iteration")
        windows = {item.candidate_ordinal: set(item.evidence_indexes)
                   for item in self.job.evidence_windows}
        if any(index >= len(self.evidence_refs) for indexes in windows.values() for index in indexes):
            raise ValueError("judgment job evidence must belong to artifact directory")
        record = unwrap_model_record(self.result)
        if not isinstance(record, ModelJudgmentAcceptedRecord):
            return self

        def check(ordinal, indexes):
            if ordinal not in windows or not set(indexes).issubset(windows[ordinal]):
                raise ValueError("accepted judgment must use its candidate evidence window")

        patch = record.patch
        for update in (*patch.role_updates, *patch.interest_updates, *patch.meal_updates):
            check(update.candidate_ordinal, update.evidence_indexes)
        for group in patch.requirement_group_updates:
            exceptions = {item.candidate_ordinal: item for item in group.exceptions}
            for ordinal in group.evaluated_candidate_ordinals:
                exception = exceptions.get(ordinal)
                check(ordinal, group.default_evidence_indexes if exception is None else exception.evidence_indexes)
        if resolved_judgment_key_count(patch) > 256:
            raise ValueError("accepted judgment exceeds job key capacity")
        validate_job_coverage(self.job, supplied_resolved_judgments(patch))
        return self


class CandidateResearchPlaceObservationArtifact(ArtifactSnapshotModel):
    schema_version: Literal["1.0"]
    run_id: UUID
    mode: ResearchMode
    invocation_slot_index: Annotated[int, Field(ge=0, le=5)]
    iteration_index: Annotated[int, Field(ge=0, le=5)]
    action_ordinal: Annotated[int, Field(ge=0, le=7)]
    tool: ResearchTool
    action_fingerprint: ActionFingerprint
    result: PlaceObservationRecord

    @model_validator(mode="after")
    def _verified_result(self) -> "CandidateResearchPlaceObservationArtifact":
        expected = MapCapability.PLACE_SEARCH if self.tool is ResearchTool.SEARCH_PLACES else MapCapability.PLACE_DETAILS
        if isinstance(self.result, (PlaceSearchSuccessObservation, PlaceSearchNoResultObservation, PlaceDetailsSuccessObservation, PlaceDetailsNoResultObservation)):
            if self.result.provenance.verified is not True or self.result.provenance.capability is not expected:
                raise ValueError("persisted map result capability/verification mismatch")
        if isinstance(self.result, (PlaceMapFailureObservation, PlaceUnverifiedResultObservation)) and self.result.capability is not expected:
            raise ValueError("observation failure capability mismatch")
        return self


class ArtifactActionCorrectionState(ArtifactSnapshotModel):
    target: ContentDigest
    evidence: ContentDigest
    plan: ContentDigest
    rule: Literal["highest_priority_objective_ignored", "objective_parameters", "duplicate", "details_already_requested", "no_useful_action_invalid"]
    attempts: Annotated[int, Field(ge=1, le=2)]
    active: bool


class CandidateResearchLegacyArtifact(ArtifactSnapshotModel):
    schema_version: Literal["1.0", "2.0"]
    run_id: UUID
    mode: ResearchMode
    predecessor_ref: ArtifactRef | None
    invocation_request_digest: ContentDigest
    record: CandidateSetRecord
    @model_validator(mode="after")
    def _run_and_predecessor(self) -> "CandidateResearchArtifact":
        if self.schema_version not in {"4.0", "5.0"} and CandidateResearchStopReason.SEMANTIC_BUDGET_INSUFFICIENT in getattr(self.record, "stop_reasons", ()):
            raise ValueError("semantic budget stop requires candidate set version 4")
        if self.schema_version != "5.0" and CandidateResearchStopReason.CORRECTION_LIMIT_REACHED in getattr(self.record, "stop_reasons", ()):
            raise ValueError("correction stop requires candidate set version 5")
        if self.schema_version not in {"3.0", "4.0", "5.0"} and isinstance(self.record, CompletedCandidateSetRecord) and not _candidate_set_state_complete(self.record.state):
            raise ValueError("legacy completion gate failed")
        if self.predecessor_ref is None:
            if self.mode is not ResearchMode.INITIAL:
                raise ValueError("supplemental artifact requires predecessor")
        else:
            if self.mode is not ResearchMode.SUPPLEMENTAL:
                raise ValueError("initial artifact must not have predecessor")
            if self.predecessor_ref.artifact_type != "candidate_research.candidate_set" or self.predecessor_ref.schema_version != self.schema_version or self.predecessor_ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
                raise ValueError("invalid predecessor candidate set ref")
            if self.predecessor_ref.operation_id.run_id != self.run_id:
                raise ValueError("predecessor ref run mismatch")
        state = getattr(self.record, "state", None)
        for gap in (*getattr(self.record, "gaps", ()), *getattr(state, "pending_gaps", ())):
            if isinstance(gap, ArtifactMealCountShortfallSummary) and (self.schema_version not in {"2.0", "3.0", "4.0", "5.0"} or gap.source_candidate_set_ref != self.predecessor_ref):
                raise ValueError("meal quota proof must reference exact v2 predecessor")
        if state is not None:
            if self.schema_version not in {"3.0", "4.0", "5.0"} and any(ref.schema_version != "1.0" for ref in (*state.model_action_refs, *state.model_judgment_refs)):
                raise ValueError("legacy candidate set has incompatible model refs")
            if self.schema_version not in {"3.0", "4.0", "5.0"} and len(state.effective_interests) < 2:
                raise ValueError("legacy candidate sets require at least two interests")
            if any(item.schema_version != ("1.0" if self.schema_version == "1.0" else "2.0") for item in state.candidates):
                raise ValueError("candidate version must match candidate set")
            refs = (state.hotel_selection_ref, *(item for group in (state.model_action_refs, state.model_judgment_refs, state.observation_refs) for item in group))
            if any(ref.operation_id.run_id != self.run_id for ref in refs):
                raise ValueError("nested artifact ref run mismatch")
        if isinstance(self.record, FailedCandidateSetRecord):
            failure = self.record.failure
            if isinstance(failure, ArtifactMapResearchFailureSnapshot) and failure.observation_ref.operation_id.run_id != self.run_id:
                raise ValueError("failure observation ref run mismatch")
            for ref in (self.record.last_consistent_candidate_set_ref, self.record.failure_artifact_ref):
                if ref is not None and ref.operation_id.run_id != self.run_id:
                    raise ValueError("failure ref run mismatch")
            if self.record.failure_artifact_ref is not None and self.record.failure_artifact_ref.artifact_type not in {
                "candidate_research.model_action", "candidate_research.model_judgment", "candidate_research.place_observation",
            }:
                raise ValueError("failure artifact ref type mismatch")
        return self


class CandidateResearchV3Artifact(CandidateResearchLegacyArtifact):
    schema_version: Literal["3.0"]
    correction_state: Annotated[tuple[ArtifactActionCorrectionState, ...], Field(max_length=6)]

    @model_validator(mode="after")
    def _correction_keys(self) -> "CandidateResearchArtifact":
        if len({(item.target, item.evidence) for item in self.correction_state}) != len(self.correction_state):
            raise ValueError("correction targets must be unique")
        state = getattr(self.record, "state", None)
        if self.schema_version == "3.0" and state is not None and any(
                ref.schema_version != "2.0" for ref in (*state.model_action_refs, *state.model_judgment_refs)):
            raise ValueError("v3 candidate model references require version 2")
        return self


class CandidateResearchArtifact(CandidateResearchV3Artifact):
    schema_version: Literal["4.0"]
    record: QualifiedCandidateSetRecord
    qualification_state: "QualificationState"
    action_version: ClassVar[str] = "2.0"
    judgment_version: ClassVar[str] = "3.0"

    @model_validator(mode="after")
    def _qualification(self):
        state = getattr(self.record, "state", None)
        if state is None:
            if self.qualification_state.assessments or self.qualification_state.steps:
                raise ValueError("empty failed state cannot have qualification history")
            return self
        if (any(ref.schema_version != self.action_version for ref in state.model_action_refs)
                or any(ref.schema_version != self.judgment_version for ref in state.model_judgment_refs)):
            raise ValueError("current candidate model reference versions incompatible")
        candidates = {item.admission_ordinal: item for item in state.candidates}
        groups = {item.group_id: item.requirement_kind for item in state.requirement_group_states}
        avoid_groups = {group for group, kind in groups.items() if kind == "avoid"}
        if any({item.requirement_group_id for item in candidate.avoid_matches} != avoid_groups
               for candidate in state.candidates):
            raise ValueError("current candidate must retain the complete avoid directory")
        for receipt in self.qualification_state.assessments:
            candidate = candidates.get(receipt.candidate_ordinal)
            if candidate is None or not set(receipt.evidence_indexes).issubset(candidate.observation_indexes):
                raise ValueError("assessment history must use candidate-owned evidence")
            if receipt.kind in {"must_visit", "avoid"} and groups.get(receipt.target) != receipt.kind:
                raise ValueError("assessment requirement must belong to state directory")
        for step in self.qualification_state.steps:
            if (step.action_ref is not None and step.action_ref not in state.model_action_refs
                    or step.judgment_ref is not None and step.judgment_ref not in state.model_judgment_refs
                    or not set(step.observation_refs).issubset(state.observation_refs)):
                raise ValueError("research step references must belong to candidate state")
        return self


from .qualification import (
    JudgmentJob, QualificationState, resolved_judgment_key_count,
    validate_job_coverage, supplied_resolved_judgments,
)
CandidateResearchModelJudgmentArtifact.model_rebuild()
CandidateResearchArtifact.model_rebuild()


CandidateResearchV4Artifact = CandidateResearchArtifact
CandidateResearchV2ModelActionArtifact = CandidateResearchModelActionArtifact
CandidateResearchV3ModelJudgmentArtifact = CandidateResearchModelJudgmentArtifact


class CorrectableResearchBudgetSnapshot(ArtifactResearchBudgetSummarySnapshot):
    allow_unknown_admission: ClassVar[bool] = True
    correction_limit: Literal[2]
    corrections_consumed: Annotated[int, Field(ge=0, le=2)]

    @model_validator(mode="after")
    def _paid_correction_bound(self):
        if self.corrections_consumed > self.semantic_invocations_consumed:
            raise ValueError("paid corrections cannot exceed admitted semantic invocations")
        return self


class CorrectableMealCountShortfallSummary(ArtifactMealCountShortfallSummary):
    source_versions: ClassVar[tuple[str, ...]] = ("5.0",)


CorrectableResearchGapSummary: TypeAlias = Annotated[
    ArtifactCandidateCountShortfallSummary | ArtifactInterestCoverageMissingSummary | ArtifactAreaCoverageMissingSummary
    | ArtifactMealCoverageMissingSummary | CorrectableMealCountShortfallSummary | ArtifactMustVisitResolutionMissingSummary
    | ArtifactAvoidResolutionMissingSummary | ArtifactCandidateDetailsMissingSummary | ArtifactReplacementCandidateNeededSummary,
    Field(discriminator="gap_type"),
]


class CorrectableCandidateSetStateSnapshot(QualifiedCandidateSetStateSnapshot):
    action_versions: ClassVar[tuple[str, ...]] = ("3.0",)
    judgment_versions: ClassVar[tuple[str, ...]] = ("4.0",)
    budgets: CorrectableResearchBudgetSnapshot
    pending_gaps: Annotated[tuple[CorrectableResearchGapSummary, ...], Field(max_length=128)]


class CorrectableInProgressCandidateSetRecord(QualifiedInProgressCandidateSetRecord):
    state: CorrectableCandidateSetStateSnapshot


class CorrectableCompletedCandidateSetRecord(QualifiedCompletedCandidateSetRecord):
    state: CorrectableCandidateSetStateSnapshot
    budgets: CorrectableResearchBudgetSnapshot


class CorrectableIncompleteCandidateSetRecord(QualifiedIncompleteCandidateSetRecord):
    state: CorrectableCandidateSetStateSnapshot
    budgets: CorrectableResearchBudgetSnapshot
    gaps: Annotated[tuple[CorrectableResearchGapSummary, ...], Field(min_length=1, max_length=128)]
    stop_reasons: Annotated[tuple[CandidateResearchStopReason, ...], Field(min_length=1, max_length=8)]


class CorrectableFailedCandidateSetRecord(QualifiedFailedCandidateSetRecord):
    candidate_versions: ClassVar[tuple[str, ...]] = ("5.0",)
    action_versions: ClassVar[tuple[str, ...]] = ("3.0",)
    judgment_versions: ClassVar[tuple[str, ...]] = ("4.0",)
    state: CorrectableCandidateSetStateSnapshot | None
    budgets: CorrectableResearchBudgetSnapshot


class AttemptActionResult(ArtifactSnapshotModel):
    result_type: Literal["action_result"]
    record: ModelActionRecord


class AttemptJudgmentResult(ArtifactSnapshotModel):
    result_type: Literal["judgment_result"]
    record: ModelJudgmentRecord


class AttemptControlFailure(ArtifactSnapshotModel):
    result_type: Literal["control_failure", "indeterminate"]
    code: CandidateResearchControlFailureCode
    metadata: ArtifactInvocationMetadataSnapshot | None


class CandidateResearchInvocationAttemptArtifact(ArtifactSnapshotModel):
    schema_version: Literal["1.0"]
    run_id: UUID
    mode: ResearchMode
    main_operation_id: OperationId
    kind: Literal["action", "judgment"]
    invocation_slot_index: Annotated[int, Field(ge=0, le=5)]
    iteration_index: Annotated[int, Field(ge=0, le=5)]
    attempt_index: Annotated[int, Field(ge=0, le=2)]
    semantic_index: Annotated[int, Field(ge=0, le=12)]
    semantic_cost: Literal[0, 1] | None
    is_correction: bool
    correction_number: Annotated[int, Field(ge=1, le=2)] | None
    trigger_ref: ArtifactRef | None
    recovery_code: Literal["highest_priority_objective_ignored", "objective_parameters", "duplicate",
                           "details_already_requested", "no_useful_action_invalid", "structured_output_invalid",
                           "output_truncated", "judgment_evidence_mismatch", "judgment_required_missing"] | None
    result: Annotated[AttemptActionResult | AttemptJudgmentResult | AttemptControlFailure,
                      Field(discriminator="result_type")]

    @model_validator(mode="after")
    def _attempt_identity(self):
        if self.main_operation_id.run_id != self.run_id or self.main_operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
            raise ValueError("attempt must belong to its research run")
        if self.is_correction != (self.attempt_index > 0):
            raise ValueError("attempt index must identify correction")
        if self.is_correction != (self.correction_number is not None and self.trigger_ref is not None):
            raise ValueError("correction must retain its trigger and number")
        if not self.is_correction and (self.trigger_ref is not None or self.correction_number is not None):
            raise ValueError("original invocation cannot have a correction trigger")
        if self.trigger_ref is not None:
            ref = self.trigger_ref
            if ref.artifact_type != "candidate_research.invocation_attempt" or ref.schema_version != "1.0" or ref.operation_id.run_id != self.run_id or ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
                raise ValueError("correction trigger identity mismatch")
        if self.semantic_cost is None and self.result.result_type != "indeterminate":
            raise ValueError("unknown invocation cost requires an indeterminate result")
        if self.semantic_cost == 1 and self.semantic_index >= 12:
            raise ValueError("admitted invocation exceeds model budget")
        if self.result.result_type in {"action_result", "judgment_result"}:
            if self.result.result_type != self.kind + "_result":
                raise ValueError("attempt result kind mismatch")
            record = self.result.record
            metadata = getattr(record, "metadata", None)
        else:
            metadata = self.result.metadata
        if metadata is not None and self.semantic_cost != 1:
            raise ValueError("model success metadata requires an admitted invocation")
        return self


def _main_attempt_refs(artifact):
    refs = artifact.attempt_refs
    if len(set(refs)) != len(refs):
        raise ValueError("main invocation attempt references must be unique")
    if any(ref.artifact_type != "candidate_research.invocation_attempt" or ref.schema_version != "1.0"
           or ref.operation_id.run_id != artifact.run_id or ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH
           for ref in refs):
        raise ValueError("main invocation attempt reference identity mismatch")


class CandidateResearchModelActionArtifact(CandidateResearchV2ModelActionArtifact):
    schema_version: Literal["3.0"]
    prompt_version: Literal["action-v2"]
    attempt_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=1, max_length=3)]
    result: ModelActionRecord

    @model_validator(mode="after")
    def _identity(self):
        data = self.model_dump(mode="json", exclude={"attempt_refs"})
        data["schema_version"] = "2.0"
        CandidateResearchV2ModelActionArtifact.model_validate_json(json.dumps(data), strict=True)
        _main_attempt_refs(self)
        return self


class CandidateResearchModelJudgmentArtifact(CandidateResearchV3ModelJudgmentArtifact):
    schema_version: Literal["4.0"]
    attempt_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=1, max_length=3)]
    result: ModelJudgmentRecord

    @model_validator(mode="after")
    def _evidence_directory(self):
        data = self.model_dump(mode="json", exclude={"attempt_refs", "job"})
        data["schema_version"] = "3.0"
        data["job"] = self.job.model_dump(mode="json")
        CandidateResearchV3ModelJudgmentArtifact.model_validate_json(json.dumps(data), strict=True)
        _main_attempt_refs(self)
        return self


class CorrectableActionCorrectionState(ArtifactActionCorrectionState):
    attempts: Annotated[int, Field(ge=1, le=12)]


class CandidateResearchArtifact(CandidateResearchV4Artifact):
    schema_version: Literal["5.0"]
    action_version: ClassVar[str] = "3.0"
    judgment_version: ClassVar[str] = "4.0"
    record: Annotated[CorrectableInProgressCandidateSetRecord | CorrectableCompletedCandidateSetRecord |
                      CorrectableIncompleteCandidateSetRecord | CorrectableFailedCandidateSetRecord,
                      Field(discriminator="record_type")]
    correction_state: Annotated[tuple[CorrectableActionCorrectionState, ...], Field(max_length=6)]
    invocation_attempt_refs: Annotated[tuple[ArtifactRef, ...], Field(max_length=14)]

    @model_validator(mode="after")
    def _attempt_directory(self):
        refs = self.invocation_attempt_refs
        if len(set(refs)) != len(refs) or any(ref.artifact_type != "candidate_research.invocation_attempt"
                or ref.schema_version != "1.0" or ref.operation_id.run_id != self.run_id
                or ref.operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH for ref in refs):
            raise ValueError("candidate invocation attempt directory mismatch")
        return self


def candidate_artifact_model(version: str):
    models = {"1.0": CandidateResearchLegacyArtifact, "2.0": CandidateResearchLegacyArtifact,
              "3.0": CandidateResearchV3Artifact, "4.0": CandidateResearchV4Artifact,
              "5.0": CandidateResearchArtifact}
    if version not in models:
        raise ValueError("unsupported candidate artifact version")
    return models[version]


def action_artifact_model(version: str):
    if version in {"1.0", "2.0"}:
        return CandidateResearchV2ModelActionArtifact
    if version == "3.0":
        return CandidateResearchModelActionArtifact
    raise ValueError("unsupported research action version")


def judgment_artifact_model(version: str):
    if version in {"1.0", "2.0"}:
        return CandidateResearchLegacyModelJudgmentArtifact
    if version == "3.0":
        return CandidateResearchV3ModelJudgmentArtifact
    if version == "4.0":
        return CandidateResearchModelJudgmentArtifact
    raise ValueError("unsupported research judgment version")


CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS: tuple[ArtifactSchemaRegistration, ...] = (
    ArtifactSchemaRegistration(artifact_type="candidate_research.model_action", schema_version="1.0", model=CandidateResearchV2ModelActionArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.model_action", schema_version="2.0", model=CandidateResearchV2ModelActionArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.model_action", schema_version="3.0", model=CandidateResearchModelActionArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.model_judgment", schema_version="1.0", model=CandidateResearchLegacyModelJudgmentArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.model_judgment", schema_version="2.0", model=CandidateResearchLegacyModelJudgmentArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.model_judgment", schema_version="3.0", model=CandidateResearchV3ModelJudgmentArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.model_judgment", schema_version="4.0", model=CandidateResearchModelJudgmentArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.invocation_attempt", schema_version="1.0", model=CandidateResearchInvocationAttemptArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.place_observation", schema_version="1.0", model=CandidateResearchPlaceObservationArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.candidate_set", schema_version="1.0", model=CandidateResearchLegacyArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.candidate_set", schema_version="2.0", model=CandidateResearchLegacyArtifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.candidate_set", schema_version="3.0", model=CandidateResearchV3Artifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.candidate_set", schema_version="4.0", model=CandidateResearchV4Artifact),
    ArtifactSchemaRegistration(artifact_type="candidate_research.candidate_set", schema_version="5.0", model=CandidateResearchArtifact),
)

# Resolve the two forward references before the registry walks the graph.
ArtifactInvocationMetadataSnapshot.model_rebuild()
ProposedSearchActionSnapshot.model_rebuild()


def _artifact_write_request(*, operation_id: OperationId, artifact_type: ArtifactType, artifact: ArtifactSnapshotModel) -> ArtifactWriteRequest:
    expected = {
        "candidate_research.model_action": CandidateResearchV2ModelActionArtifact,
        "candidate_research.model_judgment": CandidateResearchLegacyModelJudgmentArtifact,
        "candidate_research.place_observation": CandidateResearchPlaceObservationArtifact,
        "candidate_research.candidate_set": CandidateResearchLegacyArtifact,
        "candidate_research.invocation_attempt": CandidateResearchInvocationAttemptArtifact,
    }.get(artifact_type)
    if expected is None or not isinstance(artifact, expected):
        from .errors import CandidateResearchUsageError
        raise CandidateResearchUsageError()
    if operation_id.phase is not WorkflowPhase.CANDIDATE_RESEARCH:
        raise ValueError("artifact operation must use candidate_research phase")
    if operation_id.run_id != artifact.run_id:
        raise ValueError("artifact operation run mismatch")
    content = artifact.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)
    if type(content) is not dict:
        raise ValueError("artifact content must be object")
    return ArtifactWriteRequest(operation_id=operation_id, artifact_type=artifact_type, schema_version=artifact.schema_version, content=content)


__all__ = (
    "CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS", "ArtifactSnapshotModel", "ArtifactProvenanceSnapshot",
    "ArtifactInvocationMetadataSnapshot", "ArtifactLLMErrorSnapshot", "ArtifactCoordinateSnapshot",
    "ArtifactPlaceSummarySnapshot", "ArtifactPlaceDetailsSnapshot", "ActionDispositionSnapshot",
    "ActionModelOutcomeSnapshot", "ProposedActionBatchSnapshot", "NoUsefulActionProposedSnapshot",
    "ProposedSearchActionSnapshot", "ProposedDetailsActionSnapshot",
    "ProposedActionSnapshot", "ModelActionAcceptedRecord", "ModelActionPolicyRejectedRecord",
    "ModelActionControllerRejectedRecord", "ModelActionLLMFailureRecord", "ModelActionRecord",
    "ArtifactEvidenceHandleSnapshot", "ArtifactRoleJudgmentUpdateSnapshot", "ArtifactInterestJudgmentUpdateSnapshot",
    "ArtifactMealJudgmentUpdateSnapshot", "ArtifactRequirementVerdictExceptionSnapshot",
    "ArtifactRequirementGroupJudgmentUpdateSnapshot", "JudgmentPatchModelSnapshot", "JudgmentPatchSnapshot",
    "ModelJudgmentAcceptedRecord", "ModelJudgmentControllerRejectedRecord", "ModelJudgmentLLMFailureRecord",
    "ModelJudgmentRecord", "PlaceAdmissionDispositionSnapshot", "PlaceSearchSuccessObservation",
    "PlaceSearchNoResultObservation", "PlaceDetailsSuccessObservation", "PlaceDetailsNoResultObservation",
    "PlaceMapFailureObservation", "PlaceUnverifiedResultObservation", "PlaceNotIssuedObservation",
    "PlaceObservationRecord", "CandidateSetStateSnapshot", "SearchCoverageRecord", "SearchCoverageState",
    "InProgressCandidateSetRecord", "CompletedCandidateSetRecord", "IncompleteCandidateSetRecord",
    "FailedCandidateSetRecord", "CandidateSetRecord", "CandidateResearchModelActionArtifact",
    "CandidateResearchModelJudgmentArtifact", "CandidateResearchPlaceObservationArtifact",
    "CandidateResearchArtifact", "CandidateResearchLegacyArtifact",
)
