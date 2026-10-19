from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from app.demo_workflow.supplement import research_gaps
from app.demo_workflow.commands import canonical
from app.itinerary_planning import PlanningGap
from tests.contract.candidate_research.test_artifact_registration import _ref


@pytest.mark.parametrize("target,expected", [
    ({"target_type": "candidate_count", "role": "attraction", "current_count": 0, "target_count": 1}, "candidate_count_shortfall"),
    ({"target_type": "candidate_details", "candidate_uids": ["a"]}, "candidate_details_missing"),
    ({"target_type": "area_coverage", "role": "attraction", "anchor": {"ref_type": "hotel"}, "radius_km": 3}, "area_coverage_missing"),
    ({"target_type": "meal_coverage", "meal_intent": "lunch", "anchor": {"ref_type": "candidate", "admission_ordinal": 0, "uid": "a"}, "radius_km": 3}, "meal_coverage_missing"),
    ({"target_type": "must_resolution", "requirement_group_id": "g", "requirement_item_ids": ["i"]}, "must_visit_resolution_missing"),
    ({"target_type": "avoid_resolution", "requirement_group_id": "g", "requirement_item_ids": ["i"], "candidate_uids": ["a"]}, "avoid_resolution_missing"),
    ({"target_type": "replacement", "role": "attraction", "rejected_uid": "a", "anchor": None, "radius_km": None}, "replacement_candidate_needed"),
])
def test_all_approved_target_mappings_keep_evidence_and_targets(target, expected):
    evidence = _ref(uuid4(), "candidate_research.place_observation")
    gap = TypeAdapter(PlanningGap).validate_json(canonical(dict(gap_type="candidate_supply", gap_id="gap", date=None, evidence_refs=[evidence.model_dump(mode="json")], target=target)), strict=True)
    result, = research_gaps((gap,))
    assert result.gap_type == expected
    assert result.evidence_refs == (evidence,)
    assert result.gap_id == gap.gap_id


@pytest.mark.asyncio
async def test_incomplete_research_continues_but_no_draft_planning_stops_with_real_ref():
    from types import SimpleNamespace
    from app.demo_workflow.nodes import changed
    from app.demo_workflow.plans import DemoBudgetPlanProvider, planning_plan
    from app.candidate_research import CandidateResearchArtifact, CandidateResearchLegacyArtifact, CandidateResearchIncomplete
    from app.itinerary_planning import ItineraryPlanningIncomplete, ItineraryPlanningArtifact
    from app.workflow_contracts import WorkflowPhase, ArtifactWriteRequest, InMemoryExecutionBudgetManager, DEMO_EXECUTION_BUDGET_MANAGER_LIMITS, TravelSessionState
    from tests.support.demo_workflow_fakes import hotel_nodes
    from tests.unit.result_formatting.fakes import make_scenario

    nodes, state, _, _ = await hotel_nodes()
    cap = nodes.cap("research")
    scenario = await make_scenario(cap.artifact_repository, run_id=state.run_id, planning_version="8.0")
    previous = (await cap.artifact_repository.get(scenario.refs["candidate_set"], expected_model=CandidateResearchLegacyArtifact)).content
    candidate = previous.model_dump(mode="json")
    candidate["schema_version"] = "5.0"
    candidate["invocation_attempt_refs"] = []
    for budget in (candidate["record"]["budgets"], candidate["record"]["state"]["budgets"]):
        budget.update(correction_limit=2, corrections_consumed=0)
    candidate["correction_state"] = []
    candidate["qualification_state"] = {"assessments": [], "steps": []}
    for item in candidate["record"]["state"]["candidates"]:
        item.update(schema_version="2.0", meal_intents=[], meal_assessments=[dict(meal_intent=meal,
            verdict="unresolved", reason_code="evidence_insufficient", evidence_indexes=item["observation_indexes"][:1]) for meal in ("lunch", "dinner")])
    for counts in (candidate["record"]["counts"], candidate["record"]["state"]["counts"]):
        counts["restaurant_count"] = 0
    candidate = CandidateResearchArtifact.model_validate_json(canonical(candidate), strict=True)
    current_ref = await cap.artifact_repository.put(ArtifactWriteRequest(operation_id=scenario.refs["candidate_set"].operation_id.model_copy(
        update={"logical_step": "current_candidate_set"}), artifact_type="candidate_research.candidate_set", schema_version="5.0", content=candidate.model_dump(mode="json")))
    record = candidate.record
    research_result = CandidateResearchIncomplete.model_validate_json(canonical(dict(
        candidate_set_ref=current_ref.model_dump(mode="json"),
        counts=record.counts.model_dump(mode="json"), coverage=record.coverage.model_dump(mode="json"),
        budgets=record.budgets.model_dump(mode="json"), gaps=[g.model_dump(mode="json") for g in record.gaps],
        stop_reasons=[r.value for r in record.stop_reasons])), strict=True)
    calls = []
    async def research(request):
        calls.append("research_initial")
        return research_result
    nodes.dependencies.research_factory = lambda caps: SimpleNamespace(research_initial=research)
    state = await nodes.receipt(state, cap, last_outcome="hotel_selected",
        hotel_selection_ref=scenario.refs["hotel_selection"], hotel_candidate_ref=scenario.refs["hotel_discovery"])
    result = await nodes.research(changed(state, phase=WorkflowPhase.CANDIDATE_RESEARCH))
    assert result.goto == "prepare_planning"
    state = TravelSessionState.model_validate(result.update, strict=True)
    state = changed(state, phase=WorkflowPhase.ITINERARY_PLANNING)
    cap.execution_budget_manager = InMemoryExecutionBudgetManager(
        plan=DemoBudgetPlanProvider().plan_for_phase(run_id=state.run_id, phase=state.phase, state=state),
        limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS)
    artifact = scenario.itinerary.model_dump(mode="json")
    artifact["schema_version"] = "8.0"
    artifact["directories"].update(schema_version="7.0", candidate_set_refs=[current_ref.model_dump(mode="json")],
        model_action_refs=[], hub_geocode_refs=[], matrix_refs=[], directions_refs=[])
    artifact["record"]["budgets"].update(schema_version="8.0", model_invocation_limit=19, semantic_invocation_limit=19,
        normal_invocation_limit=17, format_repair_limit=2, format_repair_used=0)
    artifact["record"].update(record_type="incomplete", draft=None, stop_reasons=["no_legal_action"],
        gaps=[dict(schema_version="1.0", summary_type="planning_process", gap_id="stop", evidence_indexes=[], process_reason="no_legal_action")])
    plan = planning_plan(state.run_id, 1)
    from app.itinerary_planning import ItineraryPlanningInitialRequest
    from app.demo_workflow.plans import planning_request_digest
    artifact["request_digest"] = planning_request_digest(ItineraryPlanningInitialRequest(confirmed=state.requirement_outcome,
        hotel_selection_ref=scenario.refs["hotel_selection"],candidate_set_ref=current_ref,operation_plan=plan))
    incomplete = ItineraryPlanningArtifact.model_validate_json(canonical(artifact), strict=True)
    terminal_ref = await cap.artifact_repository.put(ArtifactWriteRequest(operation_id=plan.terminal_successor_operation_id,
        artifact_type="itinerary_planning.itinerary", schema_version="8.0", content=incomplete.model_dump(mode="json")))
    planning_result = ItineraryPlanningIncomplete.model_validate_json(canonical(dict(
        schema_version="8.0",
        itinerary_ref=None, summary=incomplete.record.summary.model_dump(mode="json"), budgets=incomplete.record.budgets.model_dump(mode="json"),
        stop_reasons=["no_legal_action"], gaps=[dict(gap_type="planning_process", gap_id="stop", evidence_refs=[], process_reason="no_legal_action")])), strict=True)
    async def planning(request):
        calls.append("planning_initial")
        assert request.candidate_set_ref == research_result.candidate_set_ref
        return planning_result
    nodes.dependencies.planning_factory = lambda caps: SimpleNamespace(plan_initial=planning)
    result = await nodes.planning(state)
    assert result.goto == "terminal"
    state = TravelSessionState.model_validate(result.update, strict=True)
    assert terminal_ref in state.artifact_refs
    assert not any(r.artifact_type.startswith("result_formatting.") for r in state.artifact_refs)
    assert (await nodes.terminal(state)).update["terminal_code"] == "planning_incomplete"
    assert calls == ["research_initial", "planning_initial"]
