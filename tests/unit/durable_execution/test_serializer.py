from __future__ import annotations

import pytest

from app.durable_execution.errors import DurableExecutionStateError
from app.durable_execution.models import DEMO_DURABLE_EXECUTION_LIMITS
from app.durable_execution.serializer import BoundedCheckpointSerializer


def _limits(payload_bytes: int):
    return DEMO_DURABLE_EXECUTION_LIMITS.model_copy(update={"max_payload_bytes": payload_bytes})


def test_serializer_delegates_and_enforces_default_boundary() -> None:
    serializer = BoundedCheckpointSerializer(limits=DEMO_DURABLE_EXECUTION_LIMITS)
    assert serializer.delegate_type.__name__ == "RequirementCheckpointSerializer"
    tag, payload = serializer.dumps_typed(b"x" * 1_048_576)
    assert tag == "bytes"
    assert serializer.loads_typed((tag, payload)) == b"x" * 1_048_576


def test_serializer_rejects_write_and_read_over_instance_boundary() -> None:
    serializer = BoundedCheckpointSerializer(limits=_limits(32))
    serializer.dumps_typed(b"x" * 32)
    with pytest.raises(DurableExecutionStateError):
        serializer.dumps_typed(b"x" * 33)
    with pytest.raises(DurableExecutionStateError):
        serializer.loads_typed(("bytes", b"x" * 33))


def test_serializer_rejects_pickle_tag_before_delegate() -> None:
    serializer = BoundedCheckpointSerializer(limits=_limits(32))
    with pytest.raises(DurableExecutionStateError):
        serializer.loads_typed(("pickle", b"x"))


def test_serializer_boundary_is_shared_by_binary_payloads() -> None:
    serializer = BoundedCheckpointSerializer(limits=_limits(8))
    for payload in (b"contract", b"command", b"context"):
        assert serializer.loads_typed(("bytes", payload)) == payload
    with pytest.raises(DurableExecutionStateError):
        serializer.loads_typed(("bytes", b"checkpoint"))
