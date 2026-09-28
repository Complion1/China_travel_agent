from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.durable_execution.errors import (
    DurableExecutionConfigurationError, DurableExecutionStateError,
)
from app.durable_execution.runtime import (
    DurableExecutionRuntime, _canonical_bytes, _digest,
)
from app.workflow_contracts import DeliveryStatus, TerminalCategory, TerminalOutcome, TravelSessionState
from tests.requirement_subgraph_fakes import make_confirmed_transition, make_initial_state
from tests.support.durable_execution_fakes import RUN_ID, TRACE_ID


@pytest.mark.asyncio
async def test_missing_cancellation_factory_rejects_before_repository_access():
    runtime = object.__new__(DurableExecutionRuntime)
    runtime._closed = False
    runtime._cancellation_state_factory = None
    with pytest.raises(DurableExecutionConfigurationError):
        await runtime.request_cancel(run_id=RUN_ID)
    with pytest.raises(DurableExecutionConfigurationError):
        await runtime.settle_cancel(run_id=RUN_ID)


def test_cancel_terminal_requires_canonical_bytes_and_matching_base_pointer():
    initial = make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID)
    transition = make_confirmed_transition(initial)
    state = TravelSessionState.model_validate({
        **initial.model_dump(mode="python"),
        "requirement_state": transition.state,
        "requirement_outcome": transition.outcome,
        "delivery_status": DeliveryStatus.CANCELLED,
        "terminal_code": "cancelled_by_user",
        "terminal_outcome": TerminalOutcome(
            schema_version="1.0", terminal_code="cancelled_by_user",
            category=TerminalCategory.INPUT, recoverable=False,
            user_action_code=None, trace_id=TRACE_ID,
        ),
    }, strict=True)
    data = _canonical_bytes(state)
    pointer_digest = _digest(b"checkpoint")
    row = {
        "run_id": RUN_ID, "trace_id": TRACE_ID, "lifecycle": "closed",
        "finalized_at": datetime.now(UTC), "cancel_requested_at": datetime.now(UTC),
        "latest_root_checkpoint_ns": "", "latest_root_checkpoint_id": "checkpoint-id",
        "latest_root_checkpoint_digest": pointer_digest,
        "cancel_base_checkpoint_id": "checkpoint-id",
        "cancel_base_checkpoint_digest": pointer_digest,
        "cancel_terminal_bytes": data, "cancel_terminal_digest": _digest(data),
    }
    runtime = object.__new__(DurableExecutionRuntime)
    assert runtime._cancel_terminal(row) == state
    with pytest.raises(DurableExecutionStateError):
        runtime._cancel_terminal({**row, "cancel_terminal_digest": _digest(b"tamper")})
    with pytest.raises(DurableExecutionStateError):
        runtime._cancel_terminal({**row, "cancel_base_checkpoint_id": "different"})
    with pytest.raises(DurableExecutionStateError):
        runtime._cancel_terminal({**row, "cancel_terminal_bytes": data + b" " , "cancel_terminal_digest": _digest(data + b" ")})
