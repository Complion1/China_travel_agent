"""Public composition validates actual upstream values, not parallel DTOs."""
import pytest

from app.demo_workflow import create_demo_workflow_definition
from app.requirements import NeedConfirmation
from tests.support.demo_workflow_fakes import full_dependencies


@pytest.mark.asyncio
async def test_real_initial_requirement_and_production_definition():
    dependencies, turn, providers = await full_dependencies()
    definition = create_demo_workflow_definition(dependencies=dependencies)
    transition = await dependencies.domain_service.process_turn(turn)
    assert type(transition.outcome) is NeedConfirmation, transition
    assert len(providers.extractor.calls) == 1
    assert definition.contract.contract_id == "demo_workflow.web.v1"
    assert len(definition.contract.node_rules) == 76
    await definition.aclose()


@pytest.mark.asyncio
async def test_research_boundary_script_uses_real_service_and_budget():
    from app.hotel_selection import HotelSelectionRequest, HotelSelectCommand
    from app.candidate_research import CandidateResearchService, CandidateResearchInitialRequest, CandidateResearchCompleted
    from app.workflow_contracts import InMemoryExecutionBudgetManager, DEMO_EXECUTION_BUDGET_MANAGER_LIMITS, WorkflowPhase
    from app.demo_workflow.plans import hotel_plan, research_plan, operation, DemoBudgetPlanProvider
    from app.demo_workflow.terminal import changed
    from tests.support.demo_workflow_fakes import hotel_nodes, research_boundaries
    from tests.support.candidate_research_fakes import FakeRuntime
    nodes, state, commands, adapter = await hotel_nodes()
    from app.requirements import RequirementConfirmation
    dependencies, turn, providers = await full_dependencies()
    waiting = await dependencies.domain_service.process_turn(turn)
    confirmed = await dependencies.domain_service.confirm(waiting.state, RequirementConfirmation(decision="accept",revision_text=None))
    state = changed(state,requirement_state=confirmed.state,requirement_outcome=confirmed.outcome)
    service = nodes.service("hotel_discover", state.run_id)
    discovery = await service.discover_candidates(state.requirement_outcome, hotel_plan(state.run_id))
    selected = await service.record_selection(HotelSelectionRequest(selection_operation_id=operation(state.run_id,state.phase,"hotel_selection_0"),
        candidate_set_ref=discovery.candidate_set_ref,command=HotelSelectCommand(selected_uid="hotel1")), active_candidate_set_ref=discovery.candidate_set_ref)
    state = changed(state,phase=WorkflowPhase.CANDIDATE_RESEARCH)
    budget = InMemoryExecutionBudgetManager(plan=DemoBudgetPlanProvider().plan_for_phase(run_id=state.run_id,phase=state.phase,state=state),limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS)
    model, maps = research_boundaries()
    research = CandidateResearchService(runtime=FakeRuntime(model),map_adapter=maps,
        artifact_repository=nodes.cap("research").artifact_repository,budget_manager=budget)
    result = await research.research_initial(CandidateResearchInitialRequest(confirmed=state.requirement_outcome,
        hotel_selection_ref=selected.selection_ref,operation_plan=research_plan(state.run_id)))
    assert type(result) is CandidateResearchCompleted, result.model_dump(mode="json")
    await research.close_run(state.run_id)
    from app.itinerary_planning import ItineraryPlanningInitialRequest, ItineraryPlanningCompleted
    from app.demo_workflow.plans import planning_plan
    from types import SimpleNamespace
    state = changed(state,phase=WorkflowPhase.ITINERARY_PLANNING)
    budget = InMemoryExecutionBudgetManager(plan=DemoBudgetPlanProvider().plan_for_phase(run_id=state.run_id,phase=state.phase,state=state),limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS)
    service = dependencies.planning_factory(SimpleNamespace(artifact_repository=nodes.cap("planning").artifact_repository,execution_budget_manager=budget))
    request = ItineraryPlanningInitialRequest(confirmed=state.requirement_outcome,
        hotel_selection_ref=selected.selection_ref,candidate_set_ref=result.candidate_set_ref,operation_plan=planning_plan(state.run_id,1))
    planned = await service.plan_initial(request)
    assert type(planned) is ItineraryPlanningCompleted, [v.get("tool_result") for group in providers.planning_model.feedback for v in group]
    from app.demo_workflow.plans import planning_request_digest
    from app.itinerary_planning import ItineraryPlanningArtifact, ItineraryPlanningResumeRequest
    from app.itinerary_planning.fingerprints import fingerprint_request_resume
    saved = await nodes.cap("planning").artifact_repository.get(planned.itinerary_ref,expected_model=ItineraryPlanningArtifact)
    assert saved.content.request_digest == planning_request_digest(request)
    resumed = ItineraryPlanningResumeRequest(predecessor_planning_ref=planned.itinerary_ref,
        supplemented_candidate_set_ref=result.candidate_set_ref,operation_plan=planning_plan(state.run_id,1,1,3,4),
        supplemental_operation_plan=research_plan(state.run_id,1,4,32))
    assert planning_request_digest(resumed) == fingerprint_request_resume(resumed)
    await service.close_run(state.run_id)
