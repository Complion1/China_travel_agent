"""U09-01：必要用途供给缺口必须在任何规划外调之前处理。"""
import hashlib
from datetime import date
import pytest


@pytest.mark.asyncio
async def test_entry_state_proof_is_accepted_by_real_research_and_preserved_in_successor():
    from app.candidate_research import CandidateResearchService, CandidateResearchArtifact, CandidateResearchOperationPlan, NoUsefulActionProposed, JudgmentPatch
    from app.demo_workflow.supplement import research_gaps
    from app.itinerary_planning.service import _supplement_request
    from app.itinerary_planning import ItineraryPlanningArtifact
    from app.workflow_contracts import OperationId, WorkflowPhase
    from tests.support import candidate_research_fakes as upstream
    from tests.unit.candidate_research.test_service_initial import _request, _first_objective_action, _action_batch_from_prompt
    from tests.unit.candidate_research.test_service_supplemental import _slots
    planner, request, _, planning_model, repository = _fixture()
    initial, *_ = _request(request.operation_plan.run_id)
    initial = initial.model_copy(update={"confirmed": request.confirmed, "hotel_selection_ref": request.hotel_selection_ref})
    model = upstream.FakeModelOperation()
    from tests.unit.candidate_research.test_qualification import legal_judgment
    from app.candidate_research import ProposedActionBatch, RoleJudgmentUpdate, ResearchRole, RoleVerdict, RoleReasonCode, EvidenceHandle
    model.responses.extend((lambda _: upstream.structured(ProposedActionBatch(actions=(_first_objective_action(model),))),
        lambda _: legal_judgment(model, JudgmentPatch(role_updates=(RoleJudgmentUpdate(candidate_handle="c:0",role=ResearchRole.ATTRACTION,
            verdict=RoleVerdict.SUPPORTED,reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS,evidence_handles=(EvidenceHandle(handle_id="e:0"),)),),interest_updates=(),meal_updates=(),requirement_group_updates=())),
        lambda _: upstream.structured(__import__("app.candidate_research", fromlist=["ProposedActionBatch"]).ProposedActionBatch(actions=(_first_objective_action(model),))),
        upstream.structured(JudgmentPatch(role_updates=(),interest_updates=(),meal_updates=(),requirement_group_updates=())),
        upstream.structured(NoUsefulActionProposed(reason_code="prior_no_progress"))))
    maps = upstream.FakeMapAdapter((upstream.search_success("admission-source"),upstream.search_no_result()))
    researcher = CandidateResearchService(runtime=upstream.FakeRuntime(model), map_adapter=maps,
        artifact_repository=repository, budget_manager=upstream.FakeBudgetManager(initial.operation_plan.run_id))
    source = await researcher.research_initial(initial)
    assert source.outcome_type == "incomplete", source.model_dump(mode="json")["failure"] if source.outcome_type=="failed" else source.outcome_type
    request = request.model_copy(update={"candidate_set_ref": source.candidate_set_ref})
    awaiting = await planner.plan_initial(request)
    assert awaiting.outcome_type == "candidate_supplement_requested", awaiting
    artifact = (await repository.get(awaiting.planning_ref, expected_model=ItineraryPlanningArtifact)).content
    source_artifact = (await repository.get(source.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    remaining = 6-source_artifact.record.state.budgets.iterations_consumed
    plan = CandidateResearchOperationPlan(run_id=initial.operation_plan.run_id,
        iteration_slots=_slots(initial.operation_plan.run_id,count=remaining,prefix="entry_real",place_width=1),
        terminal_successor_operation_id=OperationId(run_id=initial.operation_plan.run_id,phase=WorkflowPhase.CANDIDATE_RESEARCH,logical_step="entry_real_terminal"))
    supplemental = _supplement_request(artifact,plan)
    assert supplemental.schema_version == "2.0"
    assert research_gaps(awaiting.gaps) == supplemental.gaps
    assert all(gap.evidence_refs == (source.candidate_set_ref,) for gap in supplemental.gaps)
    model.responses.append(upstream.structured(NoUsefulActionProposed(reason_code="prior_no_progress")))
    outcome = await researcher.research_supplemental(supplemental)
    assert outcome.outcome_type == "incomplete", outcome
    successor = (await repository.get(outcome.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    assert successor.predecessor_ref == source.candidate_set_ref
    assert all(gap.source_candidate_set_ref == source.candidate_set_ref for gap in successor.record.gaps if gap.gap_type=="meal_count_shortfall")
    assert planning_model.call_count == 0
    assert maps.call_count == 2
from app.candidate_research import MatchVerdict, MealIntent
from app.candidate_research.artifacts import CandidateResearchArtifact
from app.itinerary_planning import ItineraryPlanningCandidateSupplementRequested, ItineraryPlanningIncomplete, PlanningStopReason
from app.itinerary_planning.admission import candidate_purposes
from tests.unit.itinerary_planning.test_service_initial import _fixture


def with_unknown_meals(repository, request, *, exhausted=False):
    artifact = repository.contents[request.candidate_set_ref.artifact_id]
    import copy
    data = copy.deepcopy(artifact)
    for candidate in data["record"]["state"]["candidates"]:
        candidate["meal_intents"] = []
        for assessment in candidate["meal_assessments"]:
            assessment.update(verdict="unresolved", reason_code="evidence_insufficient")
    for counts in (data["record"]["counts"], data["record"]["state"]["counts"]):
        counts["restaurant_count"] = 0
    if exhausted:
        for budgets in (data["record"]["budgets"], data["record"]["state"]["budgets"]):
            budgets.update(iterations_consumed=6, place_calls_consumed=48)
    import json
    updated = CandidateResearchArtifact.model_validate_json(json.dumps(data), strict=True)
    _, encoded = repository._encoded(updated)
    ref = request.candidate_set_ref.model_copy(update={"content_hash": "sha256:"+hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)})
    repository.seed(ref, updated)
    return request.model_copy(update={"candidate_set_ref": ref})


@pytest.mark.asyncio
@pytest.mark.parametrize("exhausted", (False, True))
async def test_missing_meals_routes_to_research_or_specific_incomplete_without_model_or_map(exhausted):
    service, request, runtime, operation, repository = _fixture()
    request = with_unknown_meals(repository, request, exhausted=exhausted)
    outcome = await service.plan_initial(request)
    if exhausted:
        assert isinstance(outcome, ItineraryPlanningIncomplete), outcome
        assert PlanningStopReason.CANDIDATE_SUPPLY_INSUFFICIENT in outcome.stop_reasons
    else:
        assert isinstance(outcome, ItineraryPlanningCandidateSupplementRequested), outcome
        assert {gap.target.meal_intent for gap in outcome.gaps} == set(MealIntent)
    assert operation.call_count == 0
    assert not service._map_adapter.directions_calls
    assert not service._map_adapter.matrix_calls
    assert not service._map_adapter.geocode_calls
    assert outcome.budgets.model_invocations_consumed == outcome.budgets.direction_calls_consumed == 0


def test_supply_lower_bound_counts_distinct_uids_across_all_full_days():
    from app.itinerary_planning.admission import supply_shortages
    from tests.unit.itinerary_planning.test_service_initial import _upstream, _confirmed
    from app.candidate_research import CandidateRecordV2
    from uuid import uuid4
    candidates = tuple(CandidateRecordV2.model_validate_json(item.model_dump_json(),strict=True)
                       for item in _upstream(uuid4())[3].record.state.candidates)
    confirmed = _confirmed()
    window = confirmed.requirement.time_window.model_copy(update={"sightseeing_start_date":"2026-09-03",
        "sightseeing_end_date":"2026-09-05","sightseeing_days":3,
        "departure":confirmed.requirement.time_window.departure.model_copy(update={"local_datetime":"2026-09-06 18:00"})})
    confirmed = confirmed.model_copy(update={"requirement":confirmed.requirement.model_copy(update={"time_window":window})})
    gaps = supply_shortages(candidates,confirmed,())
    assert ("count","attraction",2,3,None) in gaps
    assert ("meal","lunch",2,3,None) in gaps
    assert ("meal","dinner",2,3,None) in gaps


def test_exact_state_proof_rejects_forged_current_count():
    from app.candidate_research import CandidateResearchService, CandidateResearchSupplementalRequest, MealCountShortfallGap
    from tests.unit.candidate_research.test_service_initial import _request
    service, request, _, _, repository = _fixture()
    state = CandidateResearchArtifact.model_validate_json(__import__("json").dumps(repository.contents[request.candidate_set_ref.artifact_id]),strict=True).record.state
    plan = _request(request.operation_plan.run_id)[0].operation_plan
    gap = MealCountShortfallGap(gap_id="forged-count",meal_intent=MealIntent.LUNCH,current_count=0,target_count=3,
        evidence_refs=(request.candidate_set_ref,),anchor=__import__("app.candidate_research",fromlist=["HotelAnchorRef"]).HotelAnchorRef(),radius_km=3)
    supplemental = CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=request.candidate_set_ref,
        operation_plan=plan,gaps=(gap,))
    with pytest.raises(ValueError,match="state quota"):
        CandidateResearchService._validate_supplemental_evidence(supplemental,state)
