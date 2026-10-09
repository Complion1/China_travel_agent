from uuid import uuid4

import pytest

from app.demo_workflow import DemoBudgetPlanProvider, DemoWorkflowUsageError
from app.demo_workflow.plans import hotel_plan, research_plan, planning_plan, BUDGETED_PHASES
from app.workflow_contracts import WorkflowPhase, TravelSessionState
from tests.requirement_subgraph_fakes import make_initial_state, make_confirmed_transition
from tests.unit.result_formatting.fakes import make_confirmed


@pytest.mark.parametrize("days", [1, 60])
def test_plans_use_shared_limits_and_unique_call_names(days):
    run_id = uuid4()
    assert len(hotel_plan(run_id).search_operation_ids) == 3
    for consumed in (0, 5, 17, 19):
        plan = planning_plan(run_id, days, 2, consumed, 1)
        assert tuple(s.invocation_slot_index for s in plan.invocation_slots) == tuple(range(consumed, 19))
        assert len(plan.matrix_operation_ids) == min(17, 19 - consumed) * days
        assert len(plan.direction_operation_ids) == 1
        assert plan == planning_plan(run_id, days, 2, consumed, 1)
    assert planning_plan(run_id, days, 0).terminal_successor_operation_id != planning_plan(run_id, days, 1).terminal_successor_operation_id
    for remaining in (0, 1, 6):
        plan = research_plan(run_id, 3, remaining, 9)
        assert tuple(s.invocation_slot_index for s in plan.iteration_slots) == tuple(range(remaining))
        assert sum(len(s.place_observation_operation_ids) for s in plan.iteration_slots) == min(9, 8 * remaining)
        assert plan.terminal_successor_operation_id.logical_step == "research_3_terminal"


@pytest.mark.asyncio
async def test_budget_provider_preserves_run_and_phase_amounts():
    provider = DemoBudgetPlanProvider()
    state = make_initial_state()
    assert provider.plan_for_start(run_id=state.run_id).limits[0].limit == 6
    transition = await make_confirmed()
    confirmed = TravelSessionState.model_validate({
        **state.model_dump(mode="python"), "requirement_state": transition.state,
        "requirement_outcome": transition.outcome,
    }, strict=True)
    days = confirmed.requirement_outcome.requirement.time_window.sightseeing_days
    for phase, expected in zip(BUDGETED_PHASES[1:], ((3,), (6, 48), (19, days * 8 + 2), (days + 1,))):
        plan = provider.plan_for_phase(run_id=state.run_id, phase=phase, state=confirmed.model_copy(update={"phase": phase}))
        assert tuple(item.limit for item in plan.limits) == expected
    with pytest.raises(DemoWorkflowUsageError):
        provider.plan_for_phase(run_id=uuid4(), phase=WorkflowPhase.REQUIREMENTS, state=state)


@pytest.mark.parametrize("call", [-1, 6, True])
def test_invalid_plan_indices_rejected(call):
    with pytest.raises(DemoWorkflowUsageError):
        research_plan(uuid4(), call)
