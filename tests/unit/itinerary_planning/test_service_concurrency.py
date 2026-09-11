"""09-5 同一终止 operation 的 single-flight 与取消传播回归。"""

from __future__ import annotations

import asyncio

import pytest

from app.llm_runtime import ToolTurnCompleted

from tests.unit.itinerary_planning.test_service_initial import _fixture, _metadata


@pytest.mark.asyncio
async def test_same_terminal_operation_is_singleflight_and_replays_same_result() -> None:
    service, request, runtime, operation, repository = _fixture(
        ToolTurnCompleted(final_text="需要澄清", metadata=_metadata()),
    )
    operation.block = True

    first_task = asyncio.create_task(service.plan_initial(request))
    await operation.entered.wait()
    second_task = asyncio.create_task(service.plan_initial(request))
    await asyncio.sleep(0)
    operation.release.set()

    first, second = await asyncio.gather(first_task, second_task)

    assert first == second
    assert runtime.start_count == 1
    assert operation.call_count == 1
    assert len(repository.put_requests) == 2


@pytest.mark.asyncio
async def test_cancelled_model_call_propagates_and_does_not_write_artifacts() -> None:
    service, request, _, operation, repository = _fixture(
        ToolTurnCompleted(final_text="不会返回", metadata=_metadata()),
    )
    operation.block = True

    task = asyncio.create_task(service.plan_initial(request))
    await operation.entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert repository.put_requests == []
    await service.aclose()
    assert operation.close_count == 1
