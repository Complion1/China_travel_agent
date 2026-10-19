import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.candidate_research import CandidateResearchUsageError
from app.candidate_research.artifacts import (
    CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS, ActionModelOutcomeSnapshot,
    ArtifactInvocationMetadataSnapshot, ArtifactKnownLLMUsageSnapshot,
    ActionDispositionSnapshot, CandidateResearchV2ModelActionArtifact as CandidateResearchModelActionArtifact,
    CandidateResearchLegacyModelJudgmentArtifact as CandidateResearchModelJudgmentArtifact, ModelJudgmentAcceptedRecord,
    ModelJudgmentControllerRejectedRecord, JudgmentPatchSnapshot,
    ModelActionAcceptedRecord, ModelActionPolicyRejectedRecord, NoUsefulActionProposedSnapshot,
    ProposedActionBatchSnapshot, ProposedSearchActionSnapshot, _artifact_write_request,
    CandidateSetStateSnapshot, SearchCoverageState, ArtifactResearchCountSummarySnapshot,
    ArtifactResearchCoverageSummarySnapshot, ArtifactResearchBudgetSummarySnapshot,
    ArtifactPlaceSummarySnapshot, ArtifactCoordinateSnapshot, ProposedDetailsActionSnapshot,
    ArtifactCandidateRecordSnapshot, ArtifactCandidateCoreSnapshot,
    ArtifactCandidateDetailsStateSnapshot, ArtifactRoleAssessmentSnapshot,
    ArtifactEvidenceHandleSnapshot, ArtifactRoleJudgmentUpdateSnapshot,
    ArtifactRoleJudgmentUpdateResolved, JudgmentPatchModelSnapshot,
    ArtifactResearchAnchorSnapshot, ArtifactRequirementGroupJudgmentUpdateSnapshot,
    ArtifactRequirementVerdictExceptionSnapshot,
    FailedCandidateSetRecord, ArtifactMapResearchFailureSnapshot,
    CompletedCandidateSetRecord, CandidateResearchLegacyArtifact as CandidateResearchArtifact, InProgressCandidateSetRecord,
    ArtifactCandidateDetailsMissingSummary, ArtifactRequirementGroupState,
    ArtifactAvoidResolutionMissingSummary, SearchCoverageRecord,
)
from app.candidate_research import ResearchMode, SearchAreaMode, ResearchRole, RoleVerdict, RoleReasonCode, RequirementReasonCode, ResearchTool, CandidateState, ResearchObjectiveKind, MatchVerdict, MealIntent
from app.baidu_map import BaiduMapErrorCode, MapCapability
from app.requirements import InterestTag
from app.workflow_contracts import ArtifactRef, ArtifactSchemaRegistry, OperationId, WorkflowPhase


def _metadata():
    return ArtifactInvocationMetadataSnapshot(
        role="candidate_research", model_name="deepseek-v4-flash", finish_reason="stop",
        provider_attempts=1, possible_duplicate=False,
        usage=ArtifactKnownLLMUsageSnapshot(
            prompt_tokens=1, completion_tokens=1, total_tokens=2,
            reasoning_tokens=0, prompt_cache_hit_tokens=1, prompt_cache_miss_tokens=0,
        ),
    )


def _ref(run_id, artifact_type, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="ref"):
    return ArtifactRef(
        artifact_id=uuid4(), artifact_type=artifact_type, schema_version="1.0",
        content_hash="sha256:" + "d" * 64,
        operation_id=OperationId(run_id=run_id, phase=phase, logical_step=logical_step),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )


def _minimal_state(run_id):
    hotel = _ref(run_id, "hotel.selection", WorkflowPhase.HOTEL_SELECTION, "hotel")
    zero_usage = ArtifactKnownLLMUsageSnapshot(
        prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0,
        prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0,
    )
    return CandidateSetStateSnapshot(
        confirmed_requirement_digest="sha256:" + "e" * 64,
        hotel_selection_ref=hotel, effective_interests=(InterestTag.CULTURE, InterestTag.NATURE),
        model_action_refs=(), model_judgment_refs=(), observation_refs=(), candidates=(),
        requirement_group_states=(),
        search_coverage=SearchCoverageState(records=(), initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False),
        counts=ArtifactResearchCountSummarySnapshot(
            attraction_count=0, attraction_target=6, restaurant_count=0, restaurant_target=4,
            unique_eligible_count=0, admitted_slots=0, candidate_capacity=128,
        ),
        coverage=ArtifactResearchCoverageSummarySnapshot(
            effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), covered_interests=(), covered_meals=(),
            must_item_count=0, resolved_must_item_count=0, details_success_count=0, details_target=0,
            initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False,
        ),
        budgets=ArtifactResearchBudgetSummarySnapshot(
            iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0,
            semantic_invocation_limit=12, semantic_invocations_consumed=0, known_provider_attempts=0,
            provider_attempts_complete=True, known_usage=zero_usage, usage_complete=True,
        ),
        pending_gaps=(), iteration_no_progress_streak=0,
    )


_CONTROL_FAILURE_REF_CASES = (
    (
        "action_batch_invalid",
        "candidate_research.model_action",
        ("candidate_research.model_judgment", "candidate_research.place_observation"),
    ),
    (
        "judgment_patch_invalid",
        "candidate_research.model_judgment",
        ("candidate_research.model_action", "candidate_research.place_observation"),
    ),
    (
        "unverified_result",
        "candidate_research.place_observation",
        ("candidate_research.model_action", "candidate_research.model_judgment"),
    ),
)


def _failed_record_payload(
    run_id,
    code,
    failure_artifact_type,
    *,
    include_last_consistent_ref=False,
):
    return {
        "record_type": "failed",
        "state": None,
        "failure": {"failure_type": "control_failure", "code": code},
        "last_consistent_candidate_set_ref": (
            json.loads(
                _ref(run_id, "candidate_research.candidate_set", logical_step="last").model_dump_json()
            )
            if include_last_consistent_ref
            else None
        ),
        "failure_artifact_ref": (
            json.loads(
                _ref(run_id, failure_artifact_type, logical_step="failure").model_dump_json()
            )
            if failure_artifact_type is not None
            else None
        ),
        "budgets": json.loads(_minimal_state(run_id).budgets.model_dump_json()),
        "retryable": False,
        "possible_duplicate": False,
    }


def _action_artifact():
    run_id = uuid4()
    action = ProposedSearchActionSnapshot(
        action_type="search_places", ordinal=0, objective_id="objective:x", query="museum",
        tag=None, area_mode=SearchAreaMode.CITY, anchor=None, radius_km=None, interest=None, meal_intent=None,
    )
    output = ProposedActionBatchSnapshot(outcome_type="proposed_actions", effective_interests=None, actions=(action,))
    record = ModelActionAcceptedRecord(
        record_type="accepted", output=output, metadata=_metadata(),
        action_dispositions=(ActionDispositionSnapshot(action_ordinal=0, action_fingerprint="sha256:" + "a" * 64, disposition="accepted", reused_observation_ref=None),),
    )
    return CandidateResearchModelActionArtifact(
        schema_version="1.0", run_id=run_id, mode=ResearchMode.INITIAL, invocation_index=0,
        invocation_slot_index=0, iteration_index=0, prompt_version="action-v1",
        prompt_sha256="sha256:" + "b" * 64, input_digest="sha256:" + "c" * 64, result=record,
    ), run_id


def test_registrations_and_type_graph_are_strict():
    assert tuple((item.artifact_type, item.schema_version) for item in CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS) == (
        *(("candidate_research.model_action", v) for v in ("1.0", "2.0", "3.0")),
        *(("candidate_research.model_judgment", v) for v in ("1.0", "2.0", "3.0", "4.0")),
        ("candidate_research.invocation_attempt", "1.0"),
        ("candidate_research.place_observation", "1.0"),
        *(("candidate_research.candidate_set", v) for v in ("1.0", "2.0", "3.0", "4.0", "5.0")),
    )
    ArtifactSchemaRegistry(CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS)
    assert ActionModelOutcomeSnapshot is not NoUsefulActionProposedSnapshot


def test_artifact_snapshot_round_trip_and_write_request_type_guard():
    artifact, run_id = _action_artifact()
    payload = artifact.model_dump_json(exclude_none=False, by_alias=False, exclude_computed_fields=True)
    assert CandidateResearchModelActionArtifact.model_validate_json(payload, strict=True) == artifact
    request = _artifact_write_request(operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="action"), artifact_type="candidate_research.model_action", artifact=artifact)
    assert request.artifact_type == "candidate_research.model_action"
    with pytest.raises(CandidateResearchUsageError):
        _artifact_write_request(operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="observation"), artifact_type="candidate_research.place_observation", artifact=artifact)


def test_artifact_write_request_rejects_wrong_phase_for_fresh_operation():
    artifact, run_id = _action_artifact()
    with pytest.raises(ValueError):
        _artifact_write_request(
            operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="action"),
            artifact_type="candidate_research.model_action", artifact=artifact,
        )


def test_policy_rejected_record_rejects_no_useful_action_after_json_round_trip():
    batch = ProposedActionBatchSnapshot(
        outcome_type="proposed_actions", effective_interests=None,
        actions=(ProposedSearchActionSnapshot(
            action_type="search_places", ordinal=0, objective_id="objective:x", query="museum",
            tag=None, area_mode=SearchAreaMode.CITY, anchor=None, radius_km=None, interest=None, meal_intent=None,
        ),),
    )
    record = ModelActionPolicyRejectedRecord(
        record_type="policy_rejected", output=batch, metadata=_metadata(), reason_code="highest_priority_objective_ignored",
    )
    payload = json.loads(record.model_dump_json())
    payload["output"] = {"outcome_type": "no_useful_action", "reason_code": "no_legal_action"}
    with pytest.raises(ValidationError):
        ModelActionPolicyRejectedRecord.model_validate_json(json.dumps(payload))


def test_supplemental_predecessor_and_gap_evidence_refs_require_type_phase_and_run_after_json_round_trip():
    run_id = uuid4()
    predecessor = _ref(run_id, "candidate_research.candidate_set")
    observation = _ref(run_id, "candidate_research.place_observation", logical_step="observation")
    from app.candidate_research import CandidateCountShortfallGap, CandidateResearchOperationPlan, CandidateResearchSupplementalRequest, ResearchRole
    request = CandidateResearchSupplementalRequest(
        predecessor_candidate_set_ref=predecessor,
        gaps=(CandidateCountShortfallGap(
            gap_id="gap-1", evidence_refs=(observation,), role=ResearchRole.ATTRACTION,
            current_count=0, target_count=1,
        ),),
        operation_plan=CandidateResearchOperationPlan(
            run_id=run_id, iteration_slots=(), terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="terminal"),
        ),
    )
    payload = json.loads(request.model_dump_json())
    payload["predecessor_candidate_set_ref"]["operation_id"]["phase"] = "hotel_selection"
    with pytest.raises(ValidationError):
        CandidateResearchSupplementalRequest.model_validate_json(json.dumps(payload))
    payload = json.loads(request.model_dump_json())
    payload["gaps"][0]["evidence_refs"][0]["artifact_type"] = "candidate_research.model_action"
    with pytest.raises(ValidationError):
        CandidateResearchSupplementalRequest.model_validate_json(json.dumps(payload))

    artifact = CandidateResearchArtifact(
        schema_version="1.0", run_id=run_id, mode=ResearchMode.SUPPLEMENTAL,
        predecessor_ref=predecessor, invocation_request_digest="sha256:" + "f" * 64,
        record=InProgressCandidateSetRecord(record_type="in_progress", state=_minimal_state(run_id)),
    )
    payload = json.loads(artifact.model_dump_json())
    payload["predecessor_ref"]["operation_id"]["phase"] = "hotel_selection"
    with pytest.raises(ValidationError):
        CandidateResearchArtifact.model_validate_json(json.dumps(payload))


def test_artifact_outcome_is_discriminated_not_nullable_superset():
    with pytest.raises(ValidationError):
        ProposedActionBatchSnapshot(outcome_type="proposed_actions", effective_interests=None, actions=())


def test_search_coverage_negative_and_out_of_directory_ordinals_reject_fresh_json():
    record = SearchCoverageRecord(
        objective_id="objective:x", objective_kind=ResearchObjectiveKind.INITIAL_CITY_ATTRACTION,
        action_fingerprint="sha256:" + "a" * 64, observation_index=0,
        role=ResearchRole.ATTRACTION, interest=None, meal_intent=None,
        eligible_candidate_ordinals=(),
    )
    payload = json.loads(record.model_dump_json())
    payload["eligible_candidate_ordinals"] = [-1]
    with pytest.raises(ValidationError):
        SearchCoverageRecord.model_validate_json(json.dumps(payload))

    run_id = uuid4()
    state = _minimal_state(run_id)
    observation = _ref(run_id, "candidate_research.place_observation", logical_step="observation")
    payload = json.loads(state.model_dump_json())
    payload["observation_refs"] = [json.loads(observation.model_dump_json())]
    payload["search_coverage"]["records"] = [{**json.loads(record.model_dump_json()), "eligible_candidate_ordinals": [0]}]
    with pytest.raises(ValidationError):
        CandidateSetStateSnapshot.model_validate_json(json.dumps(payload))


def test_artifact_local_details_gap_uids_remain_unique_after_json_round_trip():
    valid = ArtifactCandidateDetailsMissingSummary(
        gap_type="candidate_details_missing", gap_id="gap-1", evidence_indexes=(0,), candidate_uids=("u1", "u2"),
    )
    payload = json.loads(valid.model_dump_json())
    payload["candidate_uids"] = ["u1", "u1"]
    with pytest.raises(ValidationError):
        ArtifactCandidateDetailsMissingSummary.model_validate_json(json.dumps(payload))


def test_artifact_avoid_gap_supports_empty_terminal_evidence_and_strict_target_round_trip():
    valid = ArtifactAvoidResolutionMissingSummary(
        gap_type="avoid_resolution_missing", gap_id="gap:avoid:g1", evidence_indexes=(),
        requirement_group_id="reqgroup:avoid:0:abc",
        requirement_item_ids=("reqitem:avoid:0:def",), candidate_uids=("u1", "u2"),
    )
    payload = json.loads(valid.model_dump_json())
    assert ArtifactAvoidResolutionMissingSummary.model_validate_json(json.dumps(payload)) == valid
    payload["evidence_indexes"] = list(range(8))
    assert ArtifactAvoidResolutionMissingSummary.model_validate_json(json.dumps(payload)).evidence_indexes == tuple(range(8))
    for field, value in (("candidate_uids", ["u1", "u1"]), ("requirement_item_ids", []), ("evidence_indexes", [48]), ("evidence_indexes", list(range(9)))):
        mutated = json.loads(valid.model_dump_json())
        mutated[field] = value
        with pytest.raises(ValidationError):
            ArtifactAvoidResolutionMissingSummary.model_validate_json(json.dumps(mutated))


def test_terminal_summary_empty_evidence_requires_no_related_observation_context():
    base = _minimal_state(uuid4())
    gap = {
        "gap_type": "candidate_count_shortfall", "gap_id": "gap:attraction",
        "evidence_indexes": [], "role": "attraction", "current_count": 0, "target_count": 1,
    }
    payload = json.loads(base.model_dump_json())
    payload["pending_gaps"] = [gap]
    assert CandidateSetStateSnapshot.model_validate_json(json.dumps(payload)).pending_gaps[0].evidence_indexes == ()

    run_id = base.hotel_selection_ref.operation_id.run_id
    observation = _ref(run_id, "candidate_research.place_observation", logical_step="observation")
    coverage_record = SearchCoverageRecord(
        objective_id="objective:attraction", objective_kind=ResearchObjectiveKind.ATTRACTION_COUNT,
        action_fingerprint="sha256:" + "a" * 64, observation_index=0,
        role=ResearchRole.ATTRACTION, interest=None, meal_intent=None,
        eligible_candidate_ordinals=(),
    )
    related_payload = json.loads(base.model_dump_json())
    related_payload["observation_refs"] = [json.loads(observation.model_dump_json())]
    related_payload["search_coverage"]["records"] = [json.loads(coverage_record.model_dump_json())]
    related_payload["pending_gaps"] = [gap]
    with pytest.raises(ValidationError):
        CandidateSetStateSnapshot.model_validate_json(json.dumps(related_payload))


def test_candidate_set_ref_directory_and_derived_summaries_are_checked():
    from app.requirements import InterestTag
    from app.workflow_contracts import ArtifactRef, WorkflowPhase
    run_id = uuid4()
    hotel = ArtifactRef(
        artifact_id=uuid4(), artifact_type="hotel.selection", schema_version="1.0", content_hash="sha256:" + "a" * 64,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="hotel"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )
    kwargs = dict(
        confirmed_requirement_digest="sha256:" + "b" * 64, hotel_selection_ref=hotel,
        effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), model_action_refs=(), model_judgment_refs=(),
        observation_refs=(), candidates=(), requirement_group_states=(),
        search_coverage=SearchCoverageState(records=(), initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False),
        counts=ArtifactResearchCountSummarySnapshot(attraction_count=0, attraction_target=6, restaurant_count=0, restaurant_target=4, unique_eligible_count=0, admitted_slots=0, candidate_capacity=128),
        coverage=ArtifactResearchCoverageSummarySnapshot(effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), covered_interests=(), covered_meals=(), must_item_count=0, resolved_must_item_count=0, details_success_count=0, details_target=0, initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False),
        budgets=ArtifactResearchBudgetSummarySnapshot(iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0, semantic_invocation_limit=12, semantic_invocations_consumed=0, known_provider_attempts=0, provider_attempts_complete=True, known_usage=ArtifactKnownLLMUsageSnapshot(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0), usage_complete=True),
        pending_gaps=(), iteration_no_progress_streak=0,
    )
    state = CandidateSetStateSnapshot(**kwargs)
    assert state.candidates == ()
    with pytest.raises(ValidationError):
        CandidateSetStateSnapshot(**{**kwargs, "counts": kwargs["counts"].model_copy(update={"admitted_slots": 1})})
    with pytest.raises(ValidationError):
        CandidateSetStateSnapshot(**{
            **kwargs,
            "search_coverage": SearchCoverageState(records=(), initial_city_attraction_search_done=True, initial_hotel_restaurant_nearby_done=False),
            "coverage": kwargs["coverage"].model_copy(update={"initial_city_attraction_search_done": True}),
        })
    with pytest.raises(ValidationError):
        CandidateSetStateSnapshot(**{
            **kwargs,
            "coverage": kwargs["coverage"].model_copy(update={"must_item_count": 1}),
        })


def test_artifact_local_text_and_handles_are_normalized_and_bounded():
    coordinate = ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=30.0, longitude=120.0)
    with pytest.raises(ValidationError):
        ArtifactPlaceSummarySnapshot(uid=" u1", name="Museum", address="Road", coordinate=coordinate, provider_category=None)
    with pytest.raises(ValidationError):
        ProposedDetailsActionSnapshot(action_type="get_place_details", ordinal=0, objective_id="o", uid="u1 ")
    with pytest.raises(ValidationError):
        ProposedDetailsActionSnapshot(action_type="get_place_details", ordinal=0, objective_id=" o", uid="u1")
    assert ArtifactEvidenceHandleSnapshot(handle_id="e:47").handle_id == "e:47"
    for value in ("e:48", "e:49"):
        with pytest.raises(ValidationError):
            ArtifactEvidenceHandleSnapshot(handle_id=value)
    with pytest.raises(ValidationError):
        ArtifactEvidenceHandleSnapshot(handle_id=" e:0")


def test_judgment_artifact_rejects_duplicate_semantic_updates():
    evidence = (ArtifactEvidenceHandleSnapshot(handle_id="e:0"),)
    update = ArtifactRoleJudgmentUpdateSnapshot(
        candidate_handle="c:0", role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED,
        reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_handles=evidence,
    )
    with pytest.raises(ValidationError):
        JudgmentPatchModelSnapshot(outcome_type="judgment_patch", role_updates=(update, update), interest_updates=(), meal_updates=(), requirement_group_updates=())


def test_completed_record_cannot_bypass_state_completion_gates():
    run_id = uuid4()
    hotel = ArtifactRef(
        artifact_id=uuid4(), artifact_type="hotel.selection", schema_version="1.0", content_hash="sha256:" + "a" * 64,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="hotel"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )
    from app.requirements import InterestTag
    state = CandidateSetStateSnapshot(
        confirmed_requirement_digest="sha256:" + "b" * 64, hotel_selection_ref=hotel,
        effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), model_action_refs=(), model_judgment_refs=(), observation_refs=(), candidates=(), requirement_group_states=(),
        search_coverage=SearchCoverageState(records=(), initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False),
        counts=ArtifactResearchCountSummarySnapshot(attraction_count=0, attraction_target=6, restaurant_count=0, restaurant_target=4, unique_eligible_count=0, admitted_slots=0, candidate_capacity=128),
        coverage=ArtifactResearchCoverageSummarySnapshot(effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), covered_interests=(), covered_meals=(), must_item_count=0, resolved_must_item_count=0, details_success_count=0, details_target=0, initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False),
        budgets=ArtifactResearchBudgetSummarySnapshot(iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0, semantic_invocation_limit=12, semantic_invocations_consumed=0, known_provider_attempts=0, provider_attempts_complete=True, known_usage=ArtifactKnownLLMUsageSnapshot(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0), usage_complete=True),
        pending_gaps=(), iteration_no_progress_streak=0,
    )
    with pytest.raises(ValidationError):
        CompletedCandidateSetRecord(record_type="completed", transition_kind="normal", state=state, counts=state.counts, coverage=state.coverage, budgets=state.budgets)


def test_completed_avoid_group_must_evaluate_all_admitted_candidates():
    run_id = uuid4()
    observations = tuple(_ref(run_id, "candidate_research.place_observation", logical_step=f"observation_{index}") for index in range(6))
    candidates = tuple(
        ArtifactCandidateRecordSnapshot(
            schema_version="1.0", admission_ordinal=index,
            core=ArtifactCandidateCoreSnapshot(
                uid=f"u{index}", name=f"Place {index}", address="Road",
                coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=30.0, longitude=120.0), provider_category=None,
            ), state=CandidateState.ELIGIBLE,
            roles=(
                ArtifactRoleAssessmentSnapshot(role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(0,)),
                ArtifactRoleAssessmentSnapshot(role=ResearchRole.RESTAURANT, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(0,)),
            ), interests=(InterestTag.CULTURE, InterestTag.NATURE), meal_intents=(MealIntent.LUNCH, MealIntent.DINNER),
            must_matches=(), avoid_matches=(),
            details=ArtifactCandidateDetailsStateSnapshot(status="success", observation_index=0), observation_indexes=(0,),
            dietary_restrictions="not_evaluated", mobility_constraints="not_evaluated", budget_fit="not_evaluated",
        )
        for index in range(2)
    )
    coverage_records = (
        SearchCoverageRecord(objective_id="initial-city", objective_kind=ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, action_fingerprint="sha256:" + "0" * 64, observation_index=0, role=ResearchRole.ATTRACTION, interest=None, meal_intent=None, eligible_candidate_ordinals=(0, 1)),
        SearchCoverageRecord(objective_id="initial-hotel", objective_kind=ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY, action_fingerprint="sha256:" + "1" * 64, observation_index=1, role=ResearchRole.RESTAURANT, interest=None, meal_intent=None, eligible_candidate_ordinals=(0, 1)),
        SearchCoverageRecord(objective_id="interest-culture", objective_kind=ResearchObjectiveKind.INTEREST_COVERAGE, action_fingerprint="sha256:" + "2" * 64, observation_index=2, role=None, interest=InterestTag.CULTURE, meal_intent=None, eligible_candidate_ordinals=(0, 1)),
        SearchCoverageRecord(objective_id="interest-nature", objective_kind=ResearchObjectiveKind.INTEREST_COVERAGE, action_fingerprint="sha256:" + "3" * 64, observation_index=3, role=None, interest=InterestTag.NATURE, meal_intent=None, eligible_candidate_ordinals=(0, 1)),
        SearchCoverageRecord(objective_id="meal-lunch", objective_kind=ResearchObjectiveKind.MEAL_LUNCH, action_fingerprint="sha256:" + "4" * 64, observation_index=4, role=None, interest=None, meal_intent=MealIntent.LUNCH, eligible_candidate_ordinals=(0, 1)),
        SearchCoverageRecord(objective_id="meal-dinner", objective_kind=ResearchObjectiveKind.MEAL_DINNER, action_fingerprint="sha256:" + "5" * 64, observation_index=5, role=None, interest=None, meal_intent=MealIntent.DINNER, eligible_candidate_ordinals=(0, 1)),
    )
    group = ArtifactRequirementGroupState(
        group_id="avoid-group", requirement_kind="avoid", requirement_item_ids=("avoid-item",),
        evaluated_candidate_ordinals=(), default_verdict=MatchVerdict.UNRESOLVED,
        default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT, default_evidence_indexes=(0,), exceptions=(),
    )
    state = CandidateSetStateSnapshot(
        confirmed_requirement_digest="sha256:" + "a" * 64, hotel_selection_ref=_ref(run_id, "hotel.selection", WorkflowPhase.HOTEL_SELECTION, "hotel"),
        effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), model_action_refs=(), model_judgment_refs=(), observation_refs=observations,
        candidates=candidates, requirement_group_states=(group,),
        search_coverage=SearchCoverageState(records=coverage_records, initial_city_attraction_search_done=True, initial_hotel_restaurant_nearby_done=True),
        counts=ArtifactResearchCountSummarySnapshot(attraction_count=2, attraction_target=2, restaurant_count=2, restaurant_target=2, unique_eligible_count=2, admitted_slots=2, candidate_capacity=128),
        coverage=ArtifactResearchCoverageSummarySnapshot(effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), covered_interests=(InterestTag.CULTURE, InterestTag.NATURE), covered_meals=(MealIntent.LUNCH, MealIntent.DINNER), must_item_count=0, resolved_must_item_count=0, details_success_count=2, details_target=1, initial_city_attraction_search_done=True, initial_hotel_restaurant_nearby_done=True),
        budgets=ArtifactResearchBudgetSummarySnapshot(iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0, semantic_invocation_limit=12, semantic_invocations_consumed=0, known_provider_attempts=0, provider_attempts_complete=True, known_usage=ArtifactKnownLLMUsageSnapshot(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0), usage_complete=True),
        pending_gaps=(), iteration_no_progress_streak=0,
    )
    payload = json.loads(state.model_dump_json())
    with pytest.raises(ValidationError):
        CompletedCandidateSetRecord.model_validate_json(json.dumps({"record_type": "completed", "transition_kind": "normal", "state": payload, "counts": payload["counts"], "coverage": payload["coverage"], "budgets": payload["budgets"]}))


def test_artifact_budget_zero_calls_cannot_mark_usage_incomplete():
    with pytest.raises(ValidationError):
        ArtifactResearchBudgetSummarySnapshot(
            iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0,
            semantic_invocation_limit=12, semantic_invocations_consumed=0, known_provider_attempts=0,
            provider_attempts_complete=False,
            known_usage=ArtifactKnownLLMUsageSnapshot(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0),
            usage_complete=True,
        )


def test_artifact_budget_incomplete_metadata_may_be_lower_bounded_until_complete():
    usage = ArtifactKnownLLMUsageSnapshot(
        prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0,
        prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0,
    )
    summary = ArtifactResearchBudgetSummarySnapshot(
        iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0,
        semantic_invocation_limit=12, semantic_invocations_consumed=1, known_provider_attempts=0,
        provider_attempts_complete=False, known_usage=usage, usage_complete=False,
    )
    assert summary.semantic_invocations_consumed == 1
    with pytest.raises(ValidationError):
        ArtifactResearchBudgetSummarySnapshot(**{**summary.model_dump(mode="python"), "provider_attempts_complete": True})


def test_judgment_evidence_indexes_must_reference_current_directory():
    run_id = uuid4()
    observation = ArtifactRef(
        artifact_id=uuid4(), artifact_type="candidate_research.place_observation", schema_version="1.0",
        content_hash="sha256:" + "a" * 64,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="observation"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )

    def artifact(evidence_index):
        patch = JudgmentPatchSnapshot(
            outcome_type="judgment_patch",
            role_updates=(ArtifactRoleJudgmentUpdateResolved(
                candidate_ordinal=0, role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED,
                reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(evidence_index,),
            ),), interest_updates=(), meal_updates=(), requirement_group_updates=(),
        )
        return CandidateResearchModelJudgmentArtifact(
            schema_version="1.0", run_id=run_id, mode=ResearchMode.INITIAL,
            invocation_index=0, invocation_slot_index=0, iteration_index=0,
            prompt_version="observation-v1", prompt_sha256="sha256:" + "b" * 64,
            evidence_refs=(observation,), result=ModelJudgmentAcceptedRecord(
                record_type="accepted", patch=patch, metadata=_metadata(),
            ),
        )

    assert artifact(0).evidence_refs == (observation,)
    with pytest.raises(ValidationError):
        artifact(47)


def test_controller_rejected_judgment_handles_must_reference_current_directory():
    run_id = uuid4()
    observation = ArtifactRef(
        artifact_id=uuid4(), artifact_type="candidate_research.place_observation", schema_version="1.0",
        content_hash="sha256:" + "a" * 64,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="observation"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )
    proposed = JudgmentPatchModelSnapshot(
        outcome_type="judgment_patch",
        role_updates=(ArtifactRoleJudgmentUpdateSnapshot(
            candidate_handle="c:0", role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED,
            reason_code=RoleReasonCode.NAME_SEMANTICS,
            evidence_handles=(ArtifactEvidenceHandleSnapshot(handle_id="e:47"),),
        ),), interest_updates=(), meal_updates=(), requirement_group_updates=(),
    )
    with pytest.raises(ValidationError):
        CandidateResearchModelJudgmentArtifact(
            schema_version="1.0", run_id=run_id, mode=ResearchMode.INITIAL,
            invocation_index=0, invocation_slot_index=0, iteration_index=0,
            prompt_version="observation-v1", prompt_sha256="sha256:" + "b" * 64,
            evidence_refs=(observation,), result=ModelJudgmentControllerRejectedRecord(
                record_type="controller_rejected", proposed_patch=proposed, metadata=_metadata(), code="judgment_patch_invalid",
            ),
        )


def test_failed_map_record_requires_matching_failure_artifact_ref():
    run_id = uuid4()
    observation = ArtifactRef(
        artifact_id=uuid4(), artifact_type="candidate_research.place_observation", schema_version="1.0",
        content_hash="sha256:" + "a" * 64,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="observation"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )
    other = observation.model_copy(update={"artifact_id": uuid4(), "operation_id": OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="other")})
    failure = ArtifactMapResearchFailureSnapshot(
        failure_type="map_failure", tool=ResearchTool.SEARCH_PLACES, action_ordinal=0,
        code=BaiduMapErrorCode.TIMEOUT_ERROR, capability=MapCapability.PLACE_SEARCH,
        observation_ref=observation,
    )
    budget = ArtifactResearchBudgetSummarySnapshot(
        iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0,
        semantic_invocation_limit=12, semantic_invocations_consumed=0, known_provider_attempts=0,
        provider_attempts_complete=True, known_usage=ArtifactKnownLLMUsageSnapshot(
            prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0,
            prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0,
        ), usage_complete=True,
    )
    with pytest.raises(ValidationError):
        FailedCandidateSetRecord(
            record_type="failed", state=None, failure=failure, last_consistent_candidate_set_ref=None,
            failure_artifact_ref=other, budgets=budget, retryable=True, possible_duplicate=True,
        )


@pytest.mark.parametrize("code,expected_type,wrong_types", _CONTROL_FAILURE_REF_CASES)
def test_artifact_local_control_failure_requires_exact_persisted_evidence_ref_after_fresh_json_validation(
    code, expected_type, wrong_types,
):
    run_id = uuid4()
    with pytest.raises(ValidationError):
        FailedCandidateSetRecord.model_validate_json(
            json.dumps(_failed_record_payload(run_id, code, None))
        )
    for wrong_type in wrong_types:
        with pytest.raises(ValidationError):
            FailedCandidateSetRecord.model_validate_json(
                json.dumps(_failed_record_payload(run_id, code, wrong_type))
            )

    accepted = FailedCandidateSetRecord.model_validate_json(
        json.dumps(_failed_record_payload(run_id, code, expected_type))
    )
    assert accepted.failure_artifact_ref is not None
    assert accepted.failure_artifact_ref.artifact_type == expected_type
    assert FailedCandidateSetRecord.model_validate_json(accepted.model_dump_json()) == accepted


@pytest.mark.parametrize("code,expected_type,_wrong_types", _CONTROL_FAILURE_REF_CASES)
@pytest.mark.parametrize("invalid_ref_field", ("schema_version", "phase", "run_id"))
def test_artifact_local_control_failure_evidence_ref_rejects_wrong_version_phase_or_run_from_fresh_json(
    code, expected_type, _wrong_types, invalid_ref_field,
):
    run_id = uuid4()
    payload = _failed_record_payload(
        run_id,
        code,
        expected_type,
        include_last_consistent_ref=True,
    )
    if invalid_ref_field == "schema_version":
        payload["failure_artifact_ref"]["schema_version"] = "99.0"
    elif invalid_ref_field == "phase":
        payload["failure_artifact_ref"]["operation_id"]["phase"] = "hotel_selection"
    else:
        payload["failure_artifact_ref"]["operation_id"]["run_id"] = str(uuid4())
    with pytest.raises(ValidationError):
        FailedCandidateSetRecord.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("code,expected_type,_wrong_types", _CONTROL_FAILURE_REF_CASES)
def test_control_failure_evidence_ref_must_share_outer_candidate_artifact_run(
    code, expected_type, _wrong_types,
):
    run_id = uuid4()
    record = _failed_record_payload(run_id, code, expected_type)
    artifact_payload = {
        "schema_version": "1.0",
        "run_id": str(run_id),
        "mode": "initial",
        "predecessor_ref": None,
        "invocation_request_digest": "sha256:" + "f" * 64,
        "record": record,
    }
    accepted = CandidateResearchArtifact.model_validate_json(json.dumps(artifact_payload))
    assert accepted.record.failure_artifact_ref is not None
    assert accepted.record.failure_artifact_ref.operation_id.run_id == run_id

    artifact_payload["record"]["failure_artifact_ref"]["operation_id"]["run_id"] = str(uuid4())
    with pytest.raises(ValidationError):
        CandidateResearchArtifact.model_validate_json(json.dumps(artifact_payload))


def test_artifact_local_failed_llm_map_and_other_nullable_control_mappings_do_not_regress():
    run_id = uuid4()
    nullable_control = _failed_record_payload(run_id, "replay_mismatch", None)
    assert FailedCandidateSetRecord.model_validate_json(
        json.dumps(nullable_control)
    ).failure_artifact_ref is None

    llm = _failed_record_payload(
        run_id,
        "action_batch_invalid",
        "candidate_research.model_action",
    )
    llm["failure"] = {
        "failure_type": "llm_failure",
        "invocation_kind": "action",
        "error": {
            "code": "timeout",
            "role": "candidate_research",
            "retryable": True,
            "possible_duplicate": True,
            "provider_status_code": None,
            "retry_after_seconds": None,
            "actual_bytes": None,
            "max_bytes": None,
        },
    }
    llm["retryable"] = True
    llm["possible_duplicate"] = True
    assert FailedCandidateSetRecord.model_validate_json(json.dumps(llm))
    llm["failure_artifact_ref"] = json.loads(
        _ref(run_id, "candidate_research.model_judgment", logical_step="wrong").model_dump_json()
    )
    with pytest.raises(ValidationError):
        FailedCandidateSetRecord.model_validate_json(json.dumps(llm))

    observation_ref = json.loads(
        _ref(run_id, "candidate_research.place_observation", logical_step="observation").model_dump_json()
    )
    map_failure = _failed_record_payload(run_id, "unverified_result", None)
    map_failure["failure"] = {
        "failure_type": "map_failure",
        "tool": "search_places",
        "action_ordinal": 0,
        "code": "timeout_error",
        "capability": "place_search",
        "observation_ref": observation_ref,
    }
    map_failure["failure_artifact_ref"] = observation_ref
    map_failure["retryable"] = True
    map_failure["possible_duplicate"] = True
    assert FailedCandidateSetRecord.model_validate_json(json.dumps(map_failure))
    map_failure["failure_artifact_ref"] = None
    with pytest.raises(ValidationError):
        FailedCandidateSetRecord.model_validate_json(json.dumps(map_failure))


def test_candidate_details_index_must_belong_to_candidate_observation_directory():
    with pytest.raises(ValidationError):
        ArtifactCandidateRecordSnapshot(
            schema_version="1.0", admission_ordinal=0,
            core=ArtifactCandidateCoreSnapshot(
                uid="u1", name="Museum", address="Road",
                coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=30.0, longitude=120.0),
                provider_category=None,
            ), state=CandidateState.ELIGIBLE,
            roles=(
                ArtifactRoleAssessmentSnapshot(role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(0,)),
                ArtifactRoleAssessmentSnapshot(role=ResearchRole.RESTAURANT, verdict=RoleVerdict.CONTRADICTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_CONTRADICTS, evidence_indexes=(0,)),
            ), interests=(), meal_intents=(), must_matches=(), avoid_matches=(),
            details=ArtifactCandidateDetailsStateSnapshot(status="success", observation_index=47),
            observation_indexes=(0,), dietary_restrictions="not_evaluated", mobility_constraints="not_evaluated", budget_fit="not_evaluated",
        )


def test_excluded_candidate_details_do_not_count_toward_maturity():
    from app.requirements import InterestTag
    run_id = uuid4()
    hotel = ArtifactRef(
        artifact_id=uuid4(), artifact_type="hotel.selection", schema_version="1.0", content_hash="sha256:" + "a" * 64,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="hotel"),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )
    observation = ArtifactRef(
        artifact_id=uuid4(), artifact_type="candidate_research.place_observation", schema_version="1.0", content_hash="sha256:" + "b" * 64,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="observation"),
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc), size_bytes=1,
    )
    candidate = ArtifactCandidateRecordSnapshot(
        schema_version="1.0", admission_ordinal=0,
        core=ArtifactCandidateCoreSnapshot(
            uid="u1", name="Excluded", address="Road",
            coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=30.0, longitude=120.0), provider_category=None,
        ), state=CandidateState.EXCLUDED,
        roles=(
            ArtifactRoleAssessmentSnapshot(role=ResearchRole.ATTRACTION, verdict=RoleVerdict.CONTRADICTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_CONTRADICTS, evidence_indexes=(0,)),
            ArtifactRoleAssessmentSnapshot(role=ResearchRole.RESTAURANT, verdict=RoleVerdict.CONTRADICTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_CONTRADICTS, evidence_indexes=(0,)),
        ), interests=(), meal_intents=(), must_matches=(), avoid_matches=(),
        details=ArtifactCandidateDetailsStateSnapshot(status="success", observation_index=0), observation_indexes=(0,),
        dietary_restrictions="not_evaluated", mobility_constraints="not_evaluated", budget_fit="not_evaluated",
    )
    zero_usage = ArtifactKnownLLMUsageSnapshot(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0)
    state = CandidateSetStateSnapshot(
        confirmed_requirement_digest="sha256:" + "c" * 64, hotel_selection_ref=hotel,
        effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), model_action_refs=(), model_judgment_refs=(), observation_refs=(observation,),
        candidates=(candidate,), requirement_group_states=(),
        search_coverage=SearchCoverageState(records=(), initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False),
        counts=ArtifactResearchCountSummarySnapshot(attraction_count=0, attraction_target=6, restaurant_count=0, restaurant_target=4, unique_eligible_count=0, admitted_slots=1, candidate_capacity=128),
        coverage=ArtifactResearchCoverageSummarySnapshot(effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), covered_interests=(), covered_meals=(), must_item_count=0, resolved_must_item_count=0, details_success_count=0, details_target=0, initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False),
        budgets=ArtifactResearchBudgetSummarySnapshot(iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0, semantic_invocation_limit=12, semantic_invocations_consumed=0, known_provider_attempts=0, provider_attempts_complete=True, known_usage=zero_usage, usage_complete=True),
        pending_gaps=(), iteration_no_progress_streak=0,
    )
    assert state.coverage.details_success_count == 0


@pytest.mark.parametrize("retrieved_at", (datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=timezone.utc, microsecond=1)))
def test_artifact_provenance_requires_utc_timestamp(retrieved_at):
    from app.candidate_research.artifacts import ArtifactProvenanceSnapshot
    if retrieved_at.tzinfo is None:
        with pytest.raises(ValidationError):
            ArtifactProvenanceSnapshot(source="baidu_maps_mcp", capability=MapCapability.PLACE_SEARCH, retrieved_at=retrieved_at, verified=True)
    else:
        assert ArtifactProvenanceSnapshot(source="baidu_maps_mcp", capability=MapCapability.PLACE_SEARCH, retrieved_at=retrieved_at, verified=True).verified is True


def test_action_disposition_duplicate_and_reuse_ref_rules_are_bidirectional():
    run_id = uuid4()
    ref = _ref(run_id, "candidate_research.place_observation", logical_step="observation")
    base = dict(action_ordinal=0, action_fingerprint="sha256:" + "a" * 64, disposition="accepted", reused_observation_ref=None)
    assert ActionDispositionSnapshot(**base).disposition == "accepted"
    for mutation in ({"disposition": "duplicate_action"}, {"disposition": "accepted", "reused_observation_ref": ref}, {"disposition": "duplicate_action", "reused_observation_ref": _ref(run_id, "candidate_research.model_action", logical_step="bad")}):
        changed = dict(base); changed.update(mutation)
        with pytest.raises(ValidationError):
            ActionDispositionSnapshot(**changed)


@pytest.mark.parametrize("anchor_type,uid,valid", [("hotel", None, True), ("candidate", "u1", True), ("hotel", "u1", False), ("candidate", None, False), ("candidate", " u1", False)])
def test_artifact_anchor_snapshot_pair_and_normalization(anchor_type, uid, valid):
    payload = {"anchor_type": anchor_type, "uid": uid}
    if valid:
        assert ArtifactResearchAnchorSnapshot(**payload).anchor_type == anchor_type
    else:
        with pytest.raises(ValidationError):
            ArtifactResearchAnchorSnapshot(**payload)


def test_artifact_search_action_and_interest_order_guards_cover_both_area_modes():
    city = ProposedSearchActionSnapshot(action_type="search_places", ordinal=0, objective_id="o", query="museum", tag=None, area_mode=SearchAreaMode.CITY, anchor=None, radius_km=None, interest=None, meal_intent=None)
    assert city.area_mode is SearchAreaMode.CITY
    for mutation in ({"query": " museum"}, {"area_mode": "city", "anchor": {"anchor_type": "hotel", "uid": None}}, {"area_mode": "nearby", "anchor": None, "radius_km": None}):
        changed = json.loads(city.model_dump_json()); changed.update(mutation)
        with pytest.raises(ValidationError):
            ProposedSearchActionSnapshot.model_validate_json(json.dumps(changed))
    nearby = ProposedSearchActionSnapshot(action_type="search_places", ordinal=0, objective_id="o", query="museum", tag="tag", area_mode=SearchAreaMode.NEARBY, anchor=ArtifactResearchAnchorSnapshot(anchor_type="hotel", uid=None), radius_km=3, interest=None, meal_intent=None)
    assert nearby.radius_km == 3
    batch = ProposedActionBatchSnapshot(outcome_type="proposed_actions", effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), actions=(city,))
    assert batch.actions[0].ordinal == 0
    bad = json.loads(batch.model_dump_json()); bad["effective_interests"] = ["nature", "culture"]
    with pytest.raises(ValidationError):
        ProposedActionBatchSnapshot.model_validate_json(json.dumps(bad))


def test_model_action_accepted_record_covers_no_useful_and_disposition_cardinality():
    output = NoUsefulActionProposedSnapshot(outcome_type="no_useful_action", reason_code="no_legal_action")
    valid = ModelActionAcceptedRecord(record_type="accepted", output=output, metadata=_metadata(), action_dispositions=())
    assert valid.output.outcome_type == "no_useful_action"
    changed = json.loads(valid.model_dump_json()); changed["action_dispositions"] = [{"action_ordinal": 0, "action_fingerprint": "sha256:" + "a" * 64, "disposition": "accepted", "reused_observation_ref": None}]
    with pytest.raises(ValidationError):
        ModelActionAcceptedRecord.model_validate_json(json.dumps(changed))


@pytest.mark.parametrize("handle", ("c:0", "c:127", "c:128", "x:0"))
def test_artifact_candidate_handle_regex_boundaries(handle):
    payload = dict(candidate_handle=handle, role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_handles=(ArtifactEvidenceHandleSnapshot(handle_id="e:0"),))
    if handle in {"c:0", "c:127"}:
        assert ArtifactRoleJudgmentUpdateSnapshot(**payload).candidate_handle == handle
    else:
        with pytest.raises(ValidationError):
            ArtifactRoleJudgmentUpdateSnapshot(**payload)


def test_artifact_group_judgment_semantics_cover_empty_evaluation_and_exception_scope():
    base = dict(requirement_group_id="group", requirement_kind="avoid", evaluated_candidate_handles=(), default_verdict=MatchVerdict.UNRESOLVED, default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT, default_evidence_handles=(ArtifactEvidenceHandleSnapshot(handle_id="e:0"),), exceptions=())
    assert ArtifactRequirementGroupJudgmentUpdateSnapshot(**base).evaluated_candidate_handles == ()
    for mutation in ({"default_verdict": MatchVerdict.CONFIRMED}, {"evaluated_candidate_handles": ("c:0", "c:0")}, {"evaluated_candidate_handles": ("c:0",), "exceptions": (ArtifactRequirementVerdictExceptionSnapshot(candidate_handle="c:1", verdict=MatchVerdict.NO_MATCH, reason_code=RequirementReasonCode.EXACT_NAME_MATCH, evidence_handles=(ArtifactEvidenceHandleSnapshot(handle_id="e:0"),)),)}):
        changed = dict(base); changed.update(mutation)
        with pytest.raises(ValidationError):
            ArtifactRequirementGroupJudgmentUpdateSnapshot(**changed)


def test_resolved_judgment_snapshot_rejects_duplicate_semantic_or_evidence_indexes():
    update = ArtifactRoleJudgmentUpdateResolved(candidate_ordinal=0, role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(0,))
    patch = JudgmentPatchSnapshot(outcome_type="judgment_patch", role_updates=(update,), interest_updates=(), meal_updates=(), requirement_group_updates=())
    assert patch.role_updates == (update,)
    duplicate = json.loads(patch.model_dump_json()); duplicate["role_updates"][0]["evidence_indexes"] = [0, 0]
    with pytest.raises(ValidationError):
        JudgmentPatchSnapshot.model_validate_json(json.dumps(duplicate))
    duplicate = json.loads(patch.model_dump_json()); duplicate["role_updates"].append(duplicate["role_updates"][0])
    with pytest.raises(ValidationError):
        JudgmentPatchSnapshot.model_validate_json(json.dumps(duplicate))
