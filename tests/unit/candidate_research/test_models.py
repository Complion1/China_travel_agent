from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.candidate_research import (
    AreaCoverageMissingGap,
    AvoidResolutionMissingGap,
    CandidateAnchorRef,
    CandidateCountShortfallGap,
    CandidateDetailsMissingGap,
    CandidateResearchControlFailureCode, CandidateResearchInitialRequest,
    CandidateResearchOperationPlan, CandidateResearchSupplementalRequest, CandidateResearchStopReason, CandidateState,
    CandidateResearchCompleted, CandidateResearchFailed, CandidateResearchIncomplete,
    HotelAnchorRef,
    InterestCoverageMissingGap,
    MealCoverageMissingGap,
    MealIntent,
    MustVisitResolutionMissingGap,
    ProposedActionBatch, ProposedSearchAction, ResearchBudgetSummary,
    ReplacementCandidateNeededGap,
    ResearchCoverageSummary, ResearchCountSummary, ResearchRole, SearchAreaMode,
    ResearchIterationOperationIds, ResearchInvocationKind, ResearchTool,
)
from app.candidate_research.models import (
    CandidateResearchGap,
    ArtifactCoordinateSnapshot, CandidateCore, CandidateRecord, CandidateDetailsState,
    InterestTag as CandidateInterestTag, RoleAssessment, RoleReasonCode, RoleVerdict,
    CandidateResearchError, CandidateCountShortfallSummary, CandidateDetailsMissingSummary,
    ReplacementCandidateNeededSummary, InterestCoverageMissingSummary, AreaCoverageMissingSummary,
    MealCoverageMissingSummary, MustVisitResolutionMissingSummary, AvoidResolutionMissingSummary,
    LLMResearchFailure, MapResearchFailure, ControlResearchFailure,
)
from app.requirements import ConfirmedTravelRequirement, FieldMetadataRegistry, InterestTag, TravelRequirement
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase


def _op(run_id, name, phase=WorkflowPhase.CANDIDATE_RESEARCH):
    return OperationId(run_id=run_id, phase=phase, logical_step=name)


def _ref(run_id, artifact_type="hotel.selection", phase=WorkflowPhase.HOTEL_SELECTION):
    return ArtifactRef(
        artifact_id=uuid4(), artifact_type=artifact_type, schema_version="1.0",
        content_hash="sha256:" + "a" * 64, operation_id=_op(run_id, "ref_" + artifact_type.replace(".", "_"), phase),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1,
    )


def _public_budget():
    return {
        "iteration_limit": 6, "iterations_consumed": 0,
        "place_call_limit": 48, "place_calls_consumed": 0,
        "semantic_invocation_limit": 12, "semantic_invocations_consumed": 0,
        "known_provider_attempts": 0, "provider_attempts_complete": True,
        "known_usage": {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "reasoning_tokens": 0, "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
        },
        "usage_complete": True,
    }


_CONTROL_FAILURE_REF_CASES = (
    (
        CandidateResearchControlFailureCode.ACTION_BATCH_INVALID,
        "candidate_research.model_action",
        ("candidate_research.model_judgment", "candidate_research.place_observation"),
    ),
    (
        CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID,
        "candidate_research.model_judgment",
        ("candidate_research.model_action", "candidate_research.place_observation"),
    ),
    (
        CandidateResearchControlFailureCode.UNVERIFIED_RESULT,
        "candidate_research.place_observation",
        ("candidate_research.model_action", "candidate_research.model_judgment"),
    ),
)


def _public_failed_payload(
    run_id,
    code: CandidateResearchControlFailureCode,
    failure_artifact_type: str | None,
    *,
    include_last_consistent_ref: bool = False,
):
    return {
        "schema_version": "1.0",
        "outcome_type": "failed",
        "failure": {"failure_type": "control_failure", "code": code.value},
        "last_consistent_candidate_set_ref": (
            json.loads(
                _ref(
                    run_id,
                    "candidate_research.candidate_set",
                    WorkflowPhase.CANDIDATE_RESEARCH,
                ).model_dump_json()
            )
            if include_last_consistent_ref
            else None
        ),
        "failure_artifact_ref": (
            json.loads(
                _ref(
                    run_id,
                    failure_artifact_type,
                    WorkflowPhase.CANDIDATE_RESEARCH,
                ).model_dump_json()
            )
            if failure_artifact_type is not None
            else None
        ),
        "budgets": _public_budget(),
        "retryable": False,
        "possible_duplicate": False,
    }


def _completed_payload():
    run_id = uuid4()
    return {
        "schema_version": "1.0", "outcome_type": "completed",
        "candidate_set_ref": _ref(run_id, "candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH),
        "counts": {
            "attraction_count": 6, "attraction_target": 6,
            "restaurant_count": 4, "restaurant_target": 4,
            "unique_eligible_count": 6, "admitted_slots": 6,
            "candidate_capacity": 128,
        },
        "coverage": {
            "effective_interests": (InterestTag.CULTURE, InterestTag.NATURE),
            "covered_interests": (InterestTag.CULTURE, InterestTag.NATURE),
            "covered_meals": (MealIntent.LUNCH, MealIntent.DINNER),
            "must_item_count": 2, "resolved_must_item_count": 2,
            "details_success_count": 2, "details_target": 2,
            "initial_city_attraction_search_done": True,
            "initial_hotel_restaurant_nearby_done": True,
        },
        "budgets": _public_budget(),
    }


def _plan(run_id, slots=0):
    return CandidateResearchOperationPlan(
        run_id=run_id,
        iteration_slots=tuple(
            {
                "invocation_slot_index": index,
                "action_operation_id": _op(run_id, f"a{index}"),
                "judgment_operation_id": _op(run_id, f"j{index}"),
                "candidate_set_operation_id": _op(run_id, f"c{index}"),
                "place_observation_operation_ids": (),
            }
            for index in range(slots)
        ),
        terminal_successor_operation_id=_op(run_id, "terminal"),
    )


def test_operation_plan_contiguous_unique_and_same_run():
    run_id = uuid4()
    value = _plan(run_id, 2)
    assert tuple(slot.invocation_slot_index for slot in value.iteration_slots) == (0, 1)
    with pytest.raises(ValidationError):
        CandidateResearchOperationPlan(run_id=run_id, iteration_slots=(), terminal_successor_operation_id=_op(uuid4(), "terminal"))
    with pytest.raises(ValidationError):
        CandidateResearchOperationPlan(
            run_id=run_id,
            iteration_slots=(
                {
                    "invocation_slot_index": 1,
                    "action_operation_id": _op(run_id, "a"),
                    "judgment_operation_id": _op(run_id, "j"),
                    "candidate_set_operation_id": _op(run_id, "c"),
                    "place_observation_operation_ids": (),
                },
            ),
            terminal_successor_operation_id=_op(run_id, "terminal"),
        )


def test_initial_refs_match_plan_run():
    run_id = uuid4()
    confirmed = ConfirmedTravelRequirement(
        schema_version="1.0", requirement=TravelRequirement(),
        metadata_without_raw_evidence=FieldMetadataRegistry(), confirmation_summary_digest="confirmed",
    )
    request = CandidateResearchInitialRequest(confirmed=confirmed, hotel_selection_ref=_ref(run_id), operation_plan=_plan(run_id, 6))
    assert request.schema_version == "1.0"
    with pytest.raises(ValidationError):
        CandidateResearchInitialRequest(confirmed=confirmed, hotel_selection_ref=_ref(uuid4()), operation_plan=_plan(run_id, 6))


def test_action_batch_ordinals_area_and_union_are_strict():
    action = ProposedSearchAction(ordinal=0, objective_id="objective:x", query=" museums ", area_mode=SearchAreaMode.CITY)
    assert action.query == "museums"
    assert ProposedActionBatch(actions=(action,)).actions[0].ordinal == 0
    with pytest.raises(ValidationError):
        ProposedActionBatch(actions=(action.model_copy(update={"ordinal": 1}),))
    with pytest.raises(ValidationError):
        ProposedSearchAction(ordinal=0, objective_id="x", query="museums", area_mode=SearchAreaMode.NEARBY)


@pytest.mark.parametrize(
    ("area_mode", "anchor", "radius_km"),
    [(SearchAreaMode.CITY, None, None), (SearchAreaMode.NEARBY, HotelAnchorRef(), 3)],
)
def test_action_area_contract_round_trips_and_rejects_partial_nearby_fields(area_mode, anchor, radius_km):
    action = ProposedSearchAction(
        ordinal=0, objective_id="objective:area", query="museum",
        area_mode=area_mode, anchor=anchor, radius_km=radius_km,
    )
    payload = json.loads(action.model_dump_json())
    assert ProposedSearchAction.model_validate_json(json.dumps(payload)) == action
    if area_mode is SearchAreaMode.CITY:
        payload["anchor"] = {"anchor_type": "hotel"}
    else:
        payload["radius_km"] = None
    with pytest.raises(ValidationError):
        ProposedSearchAction.model_validate_json(json.dumps(payload))


def test_public_summary_fields_are_required_and_strict():
    with pytest.raises(ValidationError):
        ResearchBudgetSummary()
    count = ResearchCountSummary(
        attraction_count=0, attraction_target=6, restaurant_count=0, restaurant_target=4,
        unique_eligible_count=0, admitted_slots=0, candidate_capacity=128,
    )
    coverage = ResearchCoverageSummary(
        effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), covered_interests=(), covered_meals=(), must_item_count=0,
        resolved_must_item_count=0, details_success_count=0, details_target=0,
        initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False,
    )
    assert count.candidate_capacity == 128 and coverage.covered_meals == ()


def test_stop_enum_order_and_candidate_state_values_are_frozen():
    assert CandidateResearchStopReason.CANDIDATE_CAPACITY_REACHED.value == "candidate_capacity_reached"
    assert CandidateResearchControlFailureCode.SERVICE_USAGE_ERROR.value == "service_usage_error"
    assert CandidateState.CONFLICTED.value == "conflicted"


def test_candidate_state_is_recomputed_from_role_and_match_content():
    candidate = CandidateRecord(
        schema_version="1.0", admission_ordinal=0,
        core=CandidateCore(uid="u1", name="Museum", address="Road", coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=30.0, longitude=120.0), provider_category=None),
        state=CandidateState.ELIGIBLE,
        roles=(
            RoleAssessment(role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(0,)),
            RoleAssessment(role=ResearchRole.RESTAURANT, verdict=RoleVerdict.CONTRADICTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_CONTRADICTS, evidence_indexes=(0,)),
        ),
        interests=(CandidateInterestTag.CULTURE,), meal_intents=(), must_matches=(), avoid_matches=(),
        details=CandidateDetailsState(status="not_requested", observation_index=None), observation_indexes=(0,),
        dietary_restrictions="not_evaluated", mobility_constraints="not_evaluated", budget_fit="not_evaluated",
    )
    assert candidate.state is CandidateState.ELIGIBLE
    with pytest.raises(ValidationError):
        CandidateRecord(**{**candidate.model_dump(mode="python"), "state": CandidateState.EXCLUDED})


def test_marker_errors_have_no_message_payload_or_exception_chain():
    error = CandidateResearchError(CandidateResearchControlFailureCode.ARTIFACT_ERROR)
    assert error.code is CandidateResearchControlFailureCode.ARTIFACT_ERROR
    assert error.args == ()
    assert str(error) == ""
    assert repr(error) == "CandidateResearchError"
    with pytest.raises(AttributeError):
        error.args = ("leak",)
    with pytest.raises(AttributeError):
        error.code = CandidateResearchControlFailureCode.SERVICE_USAGE_ERROR
    with pytest.raises(AttributeError):
        error._code = CandidateResearchControlFailureCode.SERVICE_USAGE_ERROR


def test_budget_zero_calls_require_zero_usage_and_complete_counters():
    usage = {
        "prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1,
        "reasoning_tokens": 0, "prompt_cache_hit_tokens": 1, "prompt_cache_miss_tokens": 0,
    }
    with pytest.raises(ValidationError):
        ResearchBudgetSummary(
            iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0,
            semantic_invocation_limit=12, semantic_invocations_consumed=0,
            known_provider_attempts=0, provider_attempts_complete=True, known_usage=usage, usage_complete=True,
        )


def test_budget_incomplete_metadata_may_be_temporarily_lower_bounded():
    zero_usage = {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "reasoning_tokens": 0, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0,
    }
    summary = ResearchBudgetSummary(
        iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0,
        semantic_invocation_limit=12, semantic_invocations_consumed=1,
        known_provider_attempts=0, provider_attempts_complete=False,
        known_usage=zero_usage, usage_complete=False,
    )
    assert summary.semantic_invocations_consumed == 1
    with pytest.raises(ValidationError):
        ResearchBudgetSummary(
            **{**summary.model_dump(mode="python"), "provider_attempts_complete": True}
        )


def test_completed_public_outcome_rejects_each_visible_unmet_gate_after_fresh_json_validation():
    base = json.loads(CandidateResearchCompleted(**_completed_payload()).model_dump_json())
    assert CandidateResearchCompleted.model_validate_json(json.dumps(base))
    mutations = (
        ("counts", "attraction_count", 5),
        ("counts", "restaurant_count", 3),
        ("coverage", "covered_interests", ["culture"]),
        ("coverage", "covered_meals", ["lunch"]),
        ("coverage", "resolved_must_item_count", 1),
        ("coverage", "details_target", 3),
        ("coverage", "details_success_count", 1),
        ("coverage", "initial_city_attraction_search_done", False),
        ("coverage", "initial_hotel_restaurant_nearby_done", False),
    )
    for section, field, value in mutations:
        payload = json.loads(json.dumps(base))
        payload[section][field] = value
        with pytest.raises(ValidationError):
            CandidateResearchCompleted.model_validate_json(json.dumps(payload))


def test_public_outcome_count_coverage_relationships_reject_contradictory_fresh_json():
    base = json.loads(CandidateResearchCompleted(**_completed_payload()).model_dump_json())
    incomplete = dict(base, outcome_type="incomplete", gaps=[{
        "gap_type": "candidate_count_shortfall", "gap_id": "g1",
        "evidence_indexes": [0], "role": "attraction", "current_count": 0, "target_count": 1,
    }], stop_reasons=["iteration_limit_reached"])
    for field, value in (("details_success_count", 7), ("details_target", 1)):
        payload = json.loads(json.dumps(incomplete))
        payload["coverage"][field] = value
        with pytest.raises(ValidationError):
            CandidateResearchIncomplete.model_validate_json(json.dumps(payload))


def test_public_gap_summary_branches_keep_shortfall_uid_and_anchor_semantics_after_json_round_trip():
    count = CandidateCountShortfallSummary(gap_id="g1", evidence_indexes=(0,), role=ResearchRole.ATTRACTION, current_count=0, target_count=1)
    details = CandidateDetailsMissingSummary(gap_id="g2", evidence_indexes=(0,), candidate_uids=("u1",))
    replacement = ReplacementCandidateNeededSummary(gap_id="g3", evidence_indexes=(0,), rejected_uid="u2", role=ResearchRole.ATTRACTION, anchor=None, radius_km=None)
    for model, mutations in (
        (count, {"current_count": 1}),
        (details, {"candidate_uids": ["u1", "u1"]}),
        (replacement, {"anchor": {"anchor_type": "candidate", "uid": "u1"}}),
    ):
        payload = json.loads(model.model_dump_json())
        payload.update(mutations)
        with pytest.raises(ValidationError):
            type(model).model_validate_json(json.dumps(payload))


def test_public_summary_allows_empty_evidence_only_at_dto_boundary_and_keeps_index_bounds_strict():
    summary = CandidateCountShortfallSummary(
        gap_id="gap:empty", evidence_indexes=(), role=ResearchRole.ATTRACTION,
        current_count=0, target_count=1,
    )
    assert type(summary).model_validate_json(summary.model_dump_json()) == summary
    payload = json.loads(summary.model_dump_json())
    payload["evidence_indexes"] = list(range(8))
    assert type(summary).model_validate_json(json.dumps(payload)).evidence_indexes == tuple(range(8))
    payload["evidence_indexes"] = [48]
    with pytest.raises(ValidationError):
        type(summary).model_validate_json(json.dumps(payload))
    payload["evidence_indexes"] = list(range(9))
    with pytest.raises(ValidationError):
        type(summary).model_validate_json(json.dumps(payload))


def test_avoid_resolution_gap_is_typed_and_strict_after_fresh_json_round_trip():
    run_id = uuid4()
    evidence = _ref(run_id, "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH)
    gap = AvoidResolutionMissingGap(
        gap_id="gap:avoid:g1", evidence_refs=(evidence,),
        requirement_group_id="reqgroup:avoid:0:abc",
        requirement_item_ids=("reqitem:avoid:0:def", "reqitem:avoid:1:ghi"),
        candidate_uids=("u2", "u7"),
    )
    payload = json.loads(gap.model_dump_json())
    assert AvoidResolutionMissingGap.model_validate_json(json.dumps(payload)) == gap
    for mutation in (
        {"candidate_uids": ["u2", "u2"]},
        {"requirement_item_ids": ["reqitem:avoid:0:def", "reqitem:avoid:0:def"]},
        {"candidate_uids": []},
    ):
        mutated = json.loads(gap.model_dump_json())
        mutated.update(mutation)
        with pytest.raises(ValidationError):
            AvoidResolutionMissingGap.model_validate_json(json.dumps(mutated))


@pytest.mark.parametrize(
    ("factory", "invalid_field"),
    [
        (lambda ref: CandidateCountShortfallGap(gap_id="gap:count", evidence_refs=(ref,), role=ResearchRole.ATTRACTION, current_count=0, target_count=1), "target_count"),
        (lambda ref: InterestCoverageMissingGap(gap_id="gap:interest", evidence_refs=(ref,), interest=InterestTag.CULTURE), "interest"),
        (lambda ref: AreaCoverageMissingGap(gap_id="gap:area", evidence_refs=(ref,), role=ResearchRole.ATTRACTION, anchor=HotelAnchorRef(), radius_km=3), "radius_km"),
        (lambda ref: MealCoverageMissingGap(gap_id="gap:meal", evidence_refs=(ref,), meal_intent=MealIntent.LUNCH, anchor=HotelAnchorRef(), radius_km=3), "meal_intent"),
        (lambda ref: MustVisitResolutionMissingGap(gap_id="gap:must", evidence_refs=(ref,), requirement_group_id="group:must", requirement_item_ids=("item:must",)), "requirement_item_ids"),
        (lambda ref: CandidateDetailsMissingGap(gap_id="gap:details", evidence_refs=(ref,), candidate_uids=("u1",)), "candidate_uids"),
        (lambda ref: ReplacementCandidateNeededGap(gap_id="gap:replacement", evidence_refs=(ref,), rejected_uid="u0", role=ResearchRole.RESTAURANT, anchor=CandidateAnchorRef(uid="u1"), radius_km=5), "radius_km"),
    ],
)
def test_public_gap_union_round_trips_and_rejects_fresh_json_field_mutation(factory, invalid_field):
    run_id = uuid4()
    observation = _ref(run_id, "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH)
    gap = factory(observation)
    encoded = gap.model_dump_json()
    adapter = TypeAdapter(CandidateResearchGap)
    assert adapter.validate_json(encoded, strict=True) == gap

    payload = json.loads(encoded)
    if invalid_field == "target_count":
        payload["current_count"] = payload["target_count"]
    elif invalid_field == "interest":
        payload["interest"] = "not-an-interest"
    elif invalid_field == "radius_km":
        payload["radius_km"] = None if payload.get("anchor") is not None else 7
    elif invalid_field == "meal_intent":
        payload["meal_intent"] = "snack"
    elif invalid_field == "requirement_item_ids":
        payload["requirement_item_ids"] = []
    elif invalid_field == "candidate_uids":
        payload["candidate_uids"] = ["u1", "u1"]
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(payload), strict=True)


@pytest.mark.parametrize("code,expected_type,wrong_types", _CONTROL_FAILURE_REF_CASES)
def test_public_control_failure_requires_exact_persisted_evidence_ref_after_fresh_json_validation(
    code, expected_type, wrong_types,
):
    run_id = uuid4()
    with pytest.raises(ValidationError):
        CandidateResearchFailed.model_validate_json(
            json.dumps(_public_failed_payload(run_id, code, None))
        )
    for wrong_type in wrong_types:
        with pytest.raises(ValidationError):
            CandidateResearchFailed.model_validate_json(
                json.dumps(_public_failed_payload(run_id, code, wrong_type))
            )

    accepted = CandidateResearchFailed.model_validate_json(
        json.dumps(_public_failed_payload(run_id, code, expected_type))
    )
    assert accepted.failure_artifact_ref is not None
    assert accepted.failure_artifact_ref.artifact_type == expected_type
    assert CandidateResearchFailed.model_validate_json(accepted.model_dump_json()) == accepted


@pytest.mark.parametrize("code,expected_type,_wrong_types", _CONTROL_FAILURE_REF_CASES)
@pytest.mark.parametrize("invalid_ref_field", ("schema_version", "phase", "run_id"))
def test_public_control_failure_evidence_ref_rejects_wrong_version_phase_or_run_from_fresh_json(
    code, expected_type, _wrong_types, invalid_ref_field,
):
    run_id = uuid4()
    payload = _public_failed_payload(
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
        CandidateResearchFailed.model_validate_json(json.dumps(payload))


def test_public_failed_llm_map_and_other_nullable_control_mappings_do_not_regress():
    run_id = uuid4()
    nullable_control = _public_failed_payload(
        run_id,
        CandidateResearchControlFailureCode.REPLAY_MISMATCH,
        None,
    )
    assert CandidateResearchFailed.model_validate_json(
        json.dumps(nullable_control)
    ).failure_artifact_ref is None

    llm = _public_failed_payload(
        run_id,
        CandidateResearchControlFailureCode.ACTION_BATCH_INVALID,
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
    assert CandidateResearchFailed.model_validate_json(json.dumps(llm))
    llm["failure_artifact_ref"] = json.loads(
        _ref(
            run_id,
            "candidate_research.model_judgment",
            WorkflowPhase.CANDIDATE_RESEARCH,
        ).model_dump_json()
    )
    with pytest.raises(ValidationError):
        CandidateResearchFailed.model_validate_json(json.dumps(llm))

    observation_ref = json.loads(
        _ref(
            run_id,
            "candidate_research.place_observation",
            WorkflowPhase.CANDIDATE_RESEARCH,
        ).model_dump_json()
    )
    map_failure = _public_failed_payload(
        run_id,
        CandidateResearchControlFailureCode.UNVERIFIED_RESULT,
        None,
    )
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
    assert CandidateResearchFailed.model_validate_json(json.dumps(map_failure))
    map_failure["failure_artifact_ref"] = None
    with pytest.raises(ValidationError):
        CandidateResearchFailed.model_validate_json(json.dumps(map_failure))


def test_iteration_and_plan_cross_field_guards_cover_phase_run_and_identity():
    run_id = uuid4()
    slot = _plan(run_id, 1).iteration_slots[0]
    for mutation in (
        {"action_operation_id": json.loads(_op(run_id, "bad", WorkflowPhase.HOTEL_SELECTION).model_dump_json())},
        {"judgment_operation_id": json.loads(_op(run_id, "a0").model_dump_json())},
        {"candidate_set_operation_id": json.loads(_op(uuid4(), "c0").model_dump_json())},
    ):
        payload = json.loads(slot.model_dump_json())
        payload.update(mutation)
        with pytest.raises(ValidationError):
            ResearchIterationOperationIds.model_validate_json(json.dumps(payload))
    plan = _plan(run_id, 1)
    for mutation in (
        {"run_id": str(uuid4())},
        {"terminal_successor_operation_id": json.loads(_op(run_id, "x", WorkflowPhase.HOTEL_SELECTION).model_dump_json())},
    ):
        payload = json.loads(plan.model_dump_json())
        payload.update(mutation)
        with pytest.raises(ValidationError):
            CandidateResearchOperationPlan.model_validate_json(json.dumps(payload))


def test_initial_and_supplemental_reference_guards_cover_all_ref_dimensions():
    run_id = uuid4()
    confirmed = ConfirmedTravelRequirement(
        schema_version="1.0", requirement=TravelRequirement(),
        metadata_without_raw_evidence=FieldMetadataRegistry(), confirmation_summary_digest="confirmed",
    )
    valid = CandidateResearchInitialRequest(confirmed=confirmed, hotel_selection_ref=_ref(run_id), operation_plan=_plan(run_id, 6))
    for mutation in (
        {"hotel_selection_ref": json.loads(_ref(run_id, "candidate_research.candidate_set", WorkflowPhase.HOTEL_SELECTION).model_dump_json())},
        {"hotel_selection_ref": json.loads(_ref(run_id, "hotel.selection", WorkflowPhase.CANDIDATE_RESEARCH).model_dump_json())},
        {"hotel_selection_ref": json.loads(_ref(uuid4(), "hotel.selection").model_dump_json())},
    ):
        payload = json.loads(valid.model_dump_json())
        payload.update(mutation)
        with pytest.raises(ValidationError):
            CandidateResearchInitialRequest.model_validate_json(json.dumps(payload))
    with pytest.raises(ValidationError):
        CandidateResearchInitialRequest(confirmed=confirmed, hotel_selection_ref=_ref(run_id), operation_plan=_plan(run_id, 5))

    observation = _ref(run_id, "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH)
    gap = CandidateCountShortfallGap(gap_id="gap:x", evidence_refs=(observation,), role=ResearchRole.ATTRACTION, current_count=0, target_count=1)
    supplemental = CandidateResearchSupplementalRequest(
        predecessor_candidate_set_ref=_ref(run_id, "candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH),
        gaps=(gap,), operation_plan=_plan(run_id, 0),
    )
    for mutation in (
        {"predecessor_candidate_set_ref": json.loads(_ref(run_id, "hotel.selection", WorkflowPhase.CANDIDATE_RESEARCH).model_dump_json())},
        {"predecessor_candidate_set_ref": json.loads(_ref(uuid4(), "candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH).model_dump_json())},
        {"gaps": [dict(json.loads(gap.model_dump_json()), evidence_refs=[json.loads(_ref(run_id, "candidate_research.model_action", WorkflowPhase.CANDIDATE_RESEARCH).model_dump_json())])]},
        {"gaps": [dict(json.loads(gap.model_dump_json()), evidence_refs=[json.loads(_ref(uuid4(), "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH).model_dump_json())])]},
    ):
        payload = json.loads(supplemental.model_dump_json())
        payload.update(mutation)
        with pytest.raises(ValidationError):
            CandidateResearchSupplementalRequest.model_validate_json(json.dumps(payload))
    duplicate = json.loads(supplemental.model_dump_json())
    duplicate["gaps"][0]["evidence_refs"].append(duplicate["gaps"][0]["evidence_refs"][0])
    with pytest.raises(ValidationError):
        CandidateResearchSupplementalRequest.model_validate_json(json.dumps(duplicate))


@pytest.mark.parametrize("model,mutation", [
    (CandidateCountShortfallGap, {"current_count": 1, "target_count": 1}),
    (CandidateDetailsMissingGap, {"candidate_uids": ["u1", "u1"]}),
    (AvoidResolutionMissingGap, {"requirement_item_ids": ["item", "item"]}),
    (ReplacementCandidateNeededGap, {"anchor": {"anchor_type": "hotel"}, "radius_km": None}),
])
def test_public_gap_validators_reject_semantically_invalid_fresh_json(model, mutation):
    run_id = uuid4()
    ref = _ref(run_id, "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH)
    factories = {
        CandidateCountShortfallGap: dict(gap_id="g", evidence_refs=(ref,), role=ResearchRole.ATTRACTION, current_count=0, target_count=1),
        CandidateDetailsMissingGap: dict(gap_id="g", evidence_refs=(ref,), candidate_uids=("u1",)),
        AvoidResolutionMissingGap: dict(gap_id="g", evidence_refs=(ref,), requirement_group_id="group", requirement_item_ids=("item",), candidate_uids=("u1",)),
        ReplacementCandidateNeededGap: dict(gap_id="g", evidence_refs=(ref,), rejected_uid="u0", role=ResearchRole.ATTRACTION, anchor=None, radius_km=None),
    }
    payload = json.loads(model(**factories[model]).model_dump_json())
    payload.update(mutation)
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(payload))


def test_action_and_judgment_union_validators_cover_bounds_and_semantic_keys():
    action = ProposedSearchAction(ordinal=0, objective_id="objective:x", query="museum", area_mode=SearchAreaMode.CITY)
    payload = json.loads(action.model_dump_json())
    for mutation in ({"ordinal": 8}, {"objective_id": ""}, {"tag": " "}):
        changed = json.loads(action.model_dump_json()); changed.update(mutation)
        with pytest.raises(ValidationError):
            ProposedSearchAction.model_validate_json(json.dumps(changed))
    batch = ProposedActionBatch(actions=(action,))
    changed = json.loads(batch.model_dump_json()); changed["actions"][0]["ordinal"] = 1
    with pytest.raises(ValidationError):
        ProposedActionBatch.model_validate_json(json.dumps(changed))
    role = {"candidate_handle": "c:0", "role": "attraction", "verdict": "supported", "reason_code": "name_semantics", "evidence_handles": [{"handle_id": "e:0"}]}
    patch = {"outcome_type": "judgment_patch", "role_updates": [role, role], "interest_updates": [], "meal_updates": [], "requirement_group_updates": []}
    with pytest.raises(ValidationError):
        from app.candidate_research.models import JudgmentPatch
        JudgmentPatch.model_validate_json(json.dumps(patch))


@pytest.mark.parametrize("status,index,valid", [("not_requested", None, True), ("success", 0, True), ("no_result", 47, True), ("not_requested", 0, False), ("success", None, False)])
def test_candidate_details_state_json_contract(status, index, valid):
    payload = {"status": status, "observation_index": index}
    if valid:
        assert CandidateDetailsState.model_validate_json(json.dumps(payload)).status == status
    else:
        with pytest.raises(ValidationError):
            CandidateDetailsState.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("mutation", [
    {"total_tokens": 99}, {"prompt_cache_miss_tokens": 99}, {"reasoning_tokens": 2},
])
def test_known_usage_rejects_each_inconsistent_counter(mutation):
    payload = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "reasoning_tokens": 0, "prompt_cache_hit_tokens": 1, "prompt_cache_miss_tokens": 0}
    payload.update(mutation)
    with pytest.raises(ValidationError):
        from app.candidate_research.models import KnownLLMUsage
        KnownLLMUsage.model_validate_json(json.dumps(payload))


def test_public_count_and_coverage_relationships_cover_each_false_branch():
    count = {"attraction_count": 0, "attraction_target": 1, "restaurant_count": 0, "restaurant_target": 1, "unique_eligible_count": 1, "admitted_slots": 1, "candidate_capacity": 128}
    for mutation in ({"attraction_count": 2}, {"restaurant_count": 2}, {"unique_eligible_count": 2}):
        payload = dict(count); payload.update(mutation)
        with pytest.raises(ValidationError):
            ResearchCountSummary.model_validate_json(json.dumps(payload))
    coverage = {"effective_interests": ["culture", "nature"], "covered_interests": [], "covered_meals": [], "must_item_count": 1, "resolved_must_item_count": 0, "details_success_count": 0, "details_target": 0, "initial_city_attraction_search_done": False, "initial_hotel_restaurant_nearby_done": False}
    for mutation in ({"effective_interests": ["nature", "culture"]}, {"covered_interests": ["history"]}, {"covered_meals": ["dinner", "lunch"]}, {"resolved_must_item_count": 2}, {"details_success_count": 129}):
        payload = dict(coverage); payload.update(mutation)
        with pytest.raises(ValidationError):
            ResearchCoverageSummary.model_validate_json(json.dumps(payload))


def test_summary_gap_evidence_and_failure_discriminators_cover_fresh_json_edges():
    for cls, kwargs in (
        (CandidateCountShortfallSummary, dict(gap_id="g", evidence_indexes=(0,), role=ResearchRole.ATTRACTION, current_count=0, target_count=1)),
        (InterestCoverageMissingSummary, dict(gap_id="g", evidence_indexes=(0,), interest=InterestTag.CULTURE)),
        (AreaCoverageMissingSummary, dict(gap_id="g", evidence_indexes=(0,), role=ResearchRole.ATTRACTION, anchor=HotelAnchorRef(), radius_km=3)),
        (MealCoverageMissingSummary, dict(gap_id="g", evidence_indexes=(0,), meal_intent=MealIntent.LUNCH, anchor=HotelAnchorRef(), radius_km=3)),
        (MustVisitResolutionMissingSummary, dict(gap_id="g", evidence_indexes=(0,), requirement_group_id="group", requirement_item_ids=("item",))),
        (AvoidResolutionMissingSummary, dict(gap_id="g", evidence_indexes=(0,), requirement_group_id="group", requirement_item_ids=("item",), candidate_uids=("u1",))),
        (CandidateDetailsMissingSummary, dict(gap_id="g", evidence_indexes=(0,), candidate_uids=("u1",))),
        (ReplacementCandidateNeededSummary, dict(gap_id="g", evidence_indexes=(0,), rejected_uid="u0", role=ResearchRole.ATTRACTION, anchor=None, radius_km=None)),
    ):
        value = cls(**kwargs)
        payload = json.loads(value.model_dump_json()); payload["evidence_indexes"] = [0, 0]
        with pytest.raises(ValidationError):
            cls.model_validate_json(json.dumps(payload))


def test_llm_and_map_failure_ref_guards_cover_wrong_role_and_capability():
    from app.baidu_map import BaiduMapErrorCode, MapCapability
    from app.llm_runtime import LLMErrorCode, LLMRuntimeError, ModelRole
    run_id = uuid4()
    error = LLMRuntimeError(code=LLMErrorCode.TIMEOUT, role=ModelRole.CANDIDATE_RESEARCH, retryable=True, possible_duplicate=True)
    valid = LLMResearchFailure(invocation_kind=ResearchInvocationKind.ACTION, error=error)
    assert valid.error.role is ModelRole.CANDIDATE_RESEARCH
    bad = json.loads(valid.model_dump_json()); bad["error"]["role"] = "other"
    with pytest.raises(ValidationError):
        LLMResearchFailure.model_validate_json(json.dumps(bad))
    ref = _ref(run_id, "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH)
    map_failure = MapResearchFailure(tool=ResearchTool.SEARCH_PLACES, action_ordinal=0, code=BaiduMapErrorCode.TIMEOUT_ERROR, capability=MapCapability.PLACE_SEARCH, observation_ref=ref)
    for mutation in ({"capability": "place_details"}, {"tool": "get_place_details"}, {"observation_ref": json.loads(_ref(run_id, "candidate_research.model_action", WorkflowPhase.CANDIDATE_RESEARCH).model_dump_json())}):
        changed = json.loads(map_failure.model_dump_json()); changed.update(mutation)
        with pytest.raises(ValidationError):
            MapResearchFailure.model_validate_json(json.dumps(changed))
