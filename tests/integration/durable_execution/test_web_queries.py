from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.durable_execution import DurableRunListRequest, create_durable_execution_runtime
from tests.integration.durable_execution.test_bootstrap_and_restart import (
    _ConfirmationCommandCodec, _factory, _runtime_contract,
)
from tests.support.durable_execution_fakes import (
    FakeArtifactRepository, FakeBudgetPlanProvider, FakeCommandCodec, make_recovery_contract,
)


@pytest.mark.asyncio
async def test_run_views_and_descending_keyset_pages(durable_execution_settings):
    ids = []
    for _ in range(3):
        run_id, trace_id = uuid4(), uuid4()
        runtime = await create_durable_execution_runtime(
            settings=durable_execution_settings,
            artifact_repository=FakeArtifactRepository(), contract=_runtime_contract(),
            command_codec=_ConfirmationCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
            graph_factory=_factory(process=True, run_id=run_id, trace_id=trace_id),
        )
        try:
            await runtime.start(run_id=run_id, trace_id=trace_id, command="开始")
            ids.append(run_id)
            view = await runtime.get_run(run_id=run_id)
            assert view.activity == "awaiting_user"
            assert view.phase is not None and view.delivery_status is not None
            assert view.run_id == run_id and view.trace_id == trace_id
        finally:
            await runtime.close()
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(), contract=_runtime_contract(),
        command_codec=_ConfirmationCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True),
    )
    try:
        first = await runtime.list_runs(request=DurableRunListRequest(limit=1))
        assert len(first.items) == 1 and first.next_cursor is not None
        old_id, old_trace = uuid4(), uuid4()
        old_codec = FakeCommandCodec()
        await runtime._repository.create_run(
            run_id=old_id, trace_id=old_trace, contract=make_recovery_contract(),
            start_plan=FakeBudgetPlanProvider().plan_for_start(run_id=old_id),
            initial_command=old_codec.encode_initial(run_id=old_id, trace_id=old_trace, command="old"),
            initial_owner=uuid4(),
        )
        assert (await runtime.get_run(run_id=old_id)).activity == "incompatible"
        second = await runtime.list_runs(request=DurableRunListRequest(limit=1, before=first.next_cursor))
        assert len(second.items) == 1 and second.items[0].run_id != first.items[0].run_id
        assert second.items[0].run_id != old_id
        page20 = await runtime.list_runs(request=DurableRunListRequest())
        page100 = await runtime.list_runs(request=DurableRunListRequest(limit=100))
        assert {run_id for run_id in ids} <= {item.run_id for item in page100.items}
        assert next(item for item in page100.items if item.run_id == old_id).activity == "incompatible"
        assert tuple(item.run_id for item in page20.items) == tuple(item.run_id for item in page100.items[:20])
        assert tuple((item.created_at, item.run_id) for item in page100.items) == tuple(sorted(
            ((item.created_at, item.run_id) for item in page100.items), reverse=True,
        ))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_equal_timestamp_keyset_and_newer_insert_do_not_duplicate_old_pages(
    durable_execution_settings, db_connection,
):
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(), contract=_runtime_contract(),
        command_codec=_ConfirmationCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True),
    )
    ids = [uuid4() for _ in range(3)]
    codec = _ConfirmationCommandCodec()
    provider = FakeBudgetPlanProvider()
    contract = _runtime_contract()
    try:
        for run_id in ids:
            trace_id = uuid4()
            await runtime._repository.create_run(
                run_id=run_id, trace_id=trace_id, contract=contract,
                start_plan=provider.plan_for_start(run_id=run_id),
                initial_command=codec.encode_initial(run_id=run_id, trace_id=trace_id, command="start"),
                initial_owner=uuid4(),
            )
        tied_at = datetime.now(UTC) + timedelta(days=36500)
        async with db_connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE durable_execution.runs SET created_at = %s WHERE run_id = ANY(%s)",
                (tied_at, ids),
            )
            assert cursor.rowcount == 3
        await db_connection.commit()
        first = await runtime.list_runs(request=DurableRunListRequest(limit=1))
        assert first.items[0].run_id == max(ids) and first.next_cursor is not None
        newcomer, trace_id = uuid4(), uuid4()
        await runtime._repository.create_run(
            run_id=newcomer, trace_id=trace_id, contract=contract,
            start_plan=provider.plan_for_start(run_id=newcomer),
            initial_command=codec.encode_initial(run_id=newcomer, trace_id=trace_id, command="start"),
            initial_owner=uuid4(),
        )
        async with db_connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE durable_execution.runs SET created_at = %s WHERE run_id = %s",
                (tied_at + timedelta(days=1), newcomer),
            )
        await db_connection.commit()
        found = [first.items[0].run_id]
        cursor = first.next_cursor
        while cursor is not None:
            page = await runtime.list_runs(request=DurableRunListRequest(limit=1, before=cursor))
            found.extend(item.run_id for item in page.items)
            cursor = page.next_cursor
        assert found[:3] == sorted(ids, reverse=True)
        assert len(found) == len(set(found))
        assert newcomer not in found
    finally:
        await runtime.close()
