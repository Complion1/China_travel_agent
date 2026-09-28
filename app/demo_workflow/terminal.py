"""Finite business terminal states; durability failures are never swallowed."""
from app.workflow_contracts import (
    DeliveryStatus, TerminalCodeRegistry, TerminalOutcome, TravelSessionState,
    parse_travel_session_state_json, serialize_travel_session_state,
)
from .artifacts import DEMO_TERMINAL_REGISTRATIONS
from .errors import DemoWorkflowUsageError


REGISTRY = TerminalCodeRegistry(DEMO_TERMINAL_REGISTRATIONS)


def changed(state, **updates):
    original = parse_travel_session_state_json(serialize_travel_session_state(state))
    values = {name: getattr(original, name) for name in TravelSessionState.model_fields}
    return TravelSessionState.model_validate(values | updates, strict=True)


def terminal_state(state, code):
    registration = REGISTRY.resolve(code)
    outcome = None if code == "workflow_completed" else TerminalOutcome(
        schema_version="1.0", terminal_code=code, category=registration.category,
        recoverable=False, user_action_code=None, trace_id=state.trace_id,
    )
    result = changed(state, delivery_status=registration.delivery_status, terminal_code=code, terminal_outcome=outcome)
    REGISTRY.validate_state(result)
    return result


def make_cancelled_state(state: TravelSessionState) -> TravelSessionState:
    if type(state) is not TravelSessionState or state.requirement_outcome is None or state.delivery_status not in {DeliveryStatus.IN_PROGRESS, DeliveryStatus.AWAITING_USER}:
        raise DemoWorkflowUsageError()
    return terminal_state(state, "cancelled_by_user")
