import pytest

from app.demo_workflow.artifacts import DEMO_TERMINAL_REGISTRATIONS
from app.demo_workflow.terminal import terminal_state, make_cancelled_state
from app.demo_workflow.errors import DemoWorkflowUsageError
from app.workflow_contracts import TerminalCodeRegistry, DeliveryStatus
from tests.requirement_subgraph_fakes import make_initial_state, make_confirmed_transition


def test_cancellation_changes_only_terminal_dimensions():
    state = make_initial_state()
    with pytest.raises(DemoWorkflowUsageError):
        make_cancelled_state(state)
    transition = make_confirmed_transition(state)
    state = type(state).model_validate({**state.model_dump(mode="python"),
        "requirement_state": transition.state, "requirement_outcome": transition.outcome}, strict=True)
    result = make_cancelled_state(state)
    assert result.delivery_status is DeliveryStatus.CANCELLED
    for name in type(state).model_fields:
        if name not in {"delivery_status", "terminal_code", "terminal_outcome"}:
            assert getattr(state, name) == getattr(result, name)
    with pytest.raises(DemoWorkflowUsageError):
        make_cancelled_state(result)


@pytest.mark.parametrize("registration", DEMO_TERMINAL_REGISTRATIONS)
def test_all_terminal_codes_match_real_registry(registration):
    state = make_initial_state()
    transition = make_confirmed_transition(state)
    state = type(state).model_validate({**state.model_dump(mode="python"),
        "requirement_state": transition.state, "requirement_outcome": transition.outcome}, strict=True)
    result = terminal_state(state, registration.terminal_code)
    TerminalCodeRegistry(DEMO_TERMINAL_REGISTRATIONS).validate_state(result)
