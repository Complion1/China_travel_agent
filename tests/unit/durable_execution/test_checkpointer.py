from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.durable_execution.checkpointer import (
    FencedCheckpointSaver,
    _fresh_metadata,
    _namespace_to_qualified,
    _qualified_name,
)
from app.durable_execution.errors import (
    DurableExecutionConfigurationError,
    DurableExecutionStateError,
    DurableExecutionUsageError,
)
from app.durable_execution.models import DEMO_DURABLE_EXECUTION_LIMITS
from app.durable_execution.repository import DurableExecutionRepository


def test_namespace_mapping_is_strict_and_deterministic() -> None:
    task_id = "12345678-1234-4234-8234-1234567890ab"
    assert _namespace_to_qualified("requirements:" + task_id) == "requirements"
    assert _namespace_to_qualified("requirements:" + task_id + "|child:" + task_id) == "requirements/child"
    assert _qualified_name("requirements/child") == "requirements/child"
    for value in (
        "child",
        "child:not-a-uuid",
        "child:" + task_id + ":extra",
        "child:" + task_id.upper(),
        "__start__:" + task_id,
    ):
        with pytest.raises((DurableExecutionStateError, DurableExecutionConfigurationError, ValueError)):
            _namespace_to_qualified(value)


def test_metadata_is_closed_and_fresh() -> None:
    parents = {"": "checkpoint-1"}
    result = _fresh_metadata(
        {"source": "loop", "step": 0, "parents": parents},
        {"child"},
    )
    parents[""] = "changed"
    assert result == {"source": "loop", "step": 0, "parents": {"": "checkpoint-1"}}
    with pytest.raises(DurableExecutionStateError):
        _fresh_metadata({"source": "loop", "step": 0, "canary": "secret"}, set())
    for step in (-1, 0, 2**53 - 1):
        assert _fresh_metadata({"source": "loop", "step": step, "parents": {}}, set())["step"] == step
    for step in (-2, 2**53, True, 1.5, "1"):
        with pytest.raises(DurableExecutionStateError):
            _fresh_metadata({"source": "loop", "step": step, "parents": {}}, set())


@pytest.mark.asyncio
async def test_decorator_does_not_expose_history_or_delete_surface() -> None:
    repository = DurableExecutionRepository(
        coordination_pool=object(),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    saver = FencedCheckpointSaver(
        saver=AsyncPostgresSaver(None),
        repository=repository,
        run_id=uuid4(),
        owner=uuid4(),
        fencing_token=1,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        registered_node_names=("child",),
    )
    with pytest.raises(DurableExecutionUsageError):
        await saver.adelete_thread(str(uuid4()))
    with pytest.raises(DurableExecutionUsageError):
        # 调用私有 decorator 的读取枚举接口也不能成为公开 history 通道。
        async for _item in saver.alist(None):
            pass
