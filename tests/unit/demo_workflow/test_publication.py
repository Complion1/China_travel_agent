from types import SimpleNamespace

import pytest

from app.demo_workflow.publication import day_node, index_node, formatting_failure, persist
from app.demo_workflow.plans import DemoBudgetPlanProvider, RESULT_WRITE_KEY
from app.demo_workflow.nodes import changed
from app.demo_workflow import DemoRequirementCommand
from app.requirement_subgraph import RequirementConfirmationResume
from app.requirements import RequirementConfirmation
from app.durable_execution import DurableExecutionStateError, DurableExecutionDependencyError
from app.result_formatting import ResultFormattingFailed, ResultFormattingFailureCode, ResultFormattingService, ResultReadRequest
from app.workflow_contracts import (
    WorkflowPhase, TravelSessionState, InMemoryExecutionBudgetManager, DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    ArtifactTooLargeError, DeliveryStatus,
)
from tests.support.demo_workflow_fakes import hotel_nodes
from tests.unit.result_formatting.fakes import make_scenario


@pytest.mark.parametrize("code", tuple(ResultFormattingFailureCode))
@pytest.mark.asyncio
async def test_every_formatting_failure_has_exact_safe_disposition(code):
    nodes, state, _, _ = await hotel_nodes()
    failure = ResultFormattingFailed(schema_version="1.0", outcome_type="failed", code=code)
    if code is ResultFormattingFailureCode.UNSAFE_TEXT:
        result = formatting_failure(state, failure)
        assert result.goto == "__end__" and result.update["terminal_code"] == "workflow_output_failed"
    else:
        expected = (DurableExecutionDependencyError if code is ResultFormattingFailureCode.ARTIFACT_DEPENDENCY_ERROR else
            ArtifactTooLargeError if code is ResultFormattingFailureCode.RESULT_TOO_LARGE else DurableExecutionStateError)
        with pytest.raises(expected):
            formatting_failure(state, failure)


@pytest.mark.asyncio
@pytest.mark.parametrize("days,transit,map_requested", [(1, False, False), (3, False, False), (1, True, False), (1, True, True)])
async def test_real_formatter_days_then_index_and_delivery_rules(days, transit, map_requested):
    nodes, state, command, _ = await hotel_nodes()
    cap = nodes.cap("publish_index")
    scenario = await make_scenario(cap.artifact_repository, run_id=state.run_id, days=days, transit_only=transit)
    state = changed(scenario.request.state, phase=WorkflowPhase.OUTPUT_FORMATTING)
    command[0] = DemoRequirementCommand(schema_version="1.0", kind="requirement", map_requested=map_requested,
        resume=RequirementConfirmationResume(action=RequirementConfirmation(decision="accept", revision_text=None)))
    state = await nodes.receipt(state, cap, last_outcome="planning_ready", planning_ref=scenario.request.itinerary_ref,
        candidate_set_ref=scenario.refs["candidate_set"], hotel_selection_ref=scenario.refs["hotel_selection"])
    cap.execution_budget_manager = InMemoryExecutionBudgetManager(
        plan=DemoBudgetPlanProvider().plan_for_phase(run_id=state.run_id, phase=state.phase, state=state), limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS)
    with pytest.raises(DurableExecutionStateError):
        await index_node(nodes)(state)
    for number in range(1, days + 1):
        value = await day_node(nodes, number)(state)
        state = TravelSessionState.model_validate(value.update, strict=True)
        before = await cap.execution_budget_manager.get_snapshot(key=RESULT_WRITE_KEY)
        await day_node(nodes, number)(state)
        assert before == await cap.execution_budget_manager.get_snapshot(key=RESULT_WRITE_KEY)
        assert state.delivery_status is DeliveryStatus.IN_PROGRESS
    state = TravelSessionState.model_validate((await index_node(nodes)(state)).update, strict=True)
    state = TravelSessionState.model_validate((await nodes.terminal(state)).update, strict=True)
    expected = DeliveryStatus.DEMO_READY if transit and not map_requested else DeliveryStatus.DEGRADED
    assert state.delivery_status is expected
    index_ref = next(ref for ref in state.artifact_refs if ref.artifact_type == "result_formatting.index")
    loaded = await ResultFormattingService(artifact_repository=cap.artifact_repository).load(
        ResultReadRequest(schema_version="1.0", state=state, index_ref=index_ref))
    assert loaded.outcome_type == "loaded" and len(loaded.days) == days
    assert loaded.index.core.weather_status == "not_collected"
    assert (await cap.execution_budget_manager.get_snapshot(key=RESULT_WRITE_KEY)).consumed == days + 1


@pytest.mark.asyncio
async def test_real_formatter_wrapping_lost_lease_cannot_close_business_terminal():
    nodes, state, _, _ = await hotel_nodes()
    cap = nodes.cap("publish_day_1")
    scenario = await make_scenario(cap.artifact_repository, run_id=state.run_id)
    state = changed(scenario.request.state, phase=WorkflowPhase.OUTPUT_FORMATTING)
    state = await nodes.receipt(state, cap, last_outcome="planning_ready", planning_ref=scenario.request.itinerary_ref)
    actual = cap.read_only_artifact_repository
    async def get(ref, *, expected_model):
        if ref.artifact_type == "demo.control":
            return await actual.get(ref, expected_model=expected_model)
        raise DurableExecutionStateError()
    cap.read_only_artifact_repository = SimpleNamespace(get=get)
    with pytest.raises(DurableExecutionStateError):
        await day_node(nodes, 1)(state)
    assert not any(r.artifact_type.startswith("result_formatting.") for r in state.artifact_refs)
