from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.durable_execution import (
    DurableExecutionCancellationPendingError,
    DurableExecutionRunBlockedError,
    DurableExecutionStateError,
    DurableExecutionUsageError,
    DurableExecutionPaused,
    DurableExecutionTerminal,
    DurableRunListRequest,
    create_durable_execution_runtime,
)
from app.workflow_contracts import DeliveryStatus, TerminalCategory, TerminalOutcome, TravelSessionState
from tests.integration.durable_execution.test_bootstrap_and_restart import (
    _ConfirmationCommandCodec, _factory, _runtime_contract,
)
from tests.support.durable_execution_fakes import FakeArtifactRepository, FakeBudgetPlanProvider
from tests.support.durable_execution_fakes import make_requirement_recovery_contract
from tests.integration.durable_execution.test_failures_and_cancellation import (
    _BlockingConfirmationService, _requirement_graph_factory,
)
from app.durable_execution.repository import _CommitUnknown
from app.durable_execution.repository import DurableExecutionRepository


def _cancel_state(state: TravelSessionState) -> TravelSessionState:
    return TravelSessionState.model_validate(
        {
            **state.model_dump(mode="python"),
            "delivery_status": DeliveryStatus.CANCELLED,
            "terminal_code": "cancelled_by_user",
            "terminal_outcome": TerminalOutcome(
                schema_version="1.0", terminal_code="cancelled_by_user",
                category=TerminalCategory.INPUT, recoverable=False,
                user_action_code=None, trace_id=state.trace_id,
            ),
        }, strict=True,
    )


@pytest.mark.asyncio
async def test_paused_cancel_is_persistent_terminal_and_keeps_budget(durable_execution_settings, db_connection):
    run_id, trace_id = uuid4(), uuid4()
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(), contract=_runtime_contract(),
        command_codec=_ConfirmationCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True, run_id=run_id, trace_id=trace_id),
        cancellation_state_factory=_cancel_state,
    )
    try:
        paused = await runtime.start(run_id=run_id, trace_id=trace_id, command="开始")
        assert type(paused) is DurableExecutionPaused
        with pytest.raises(DurableExecutionUsageError):
            await runtime.get_terminal_budget_snapshots(run_id=run_id)
        receipt = await runtime.request_cancel(run_id=run_id)
        assert receipt.status == "requested" and receipt.state is None
        assert (await runtime.request_cancel(run_id=run_id)).status == "requested"
        with pytest.raises(DurableExecutionCancellationPendingError):
            await runtime.get_state(run_id=run_id)
        with pytest.raises(DurableExecutionCancellationPendingError):
            await runtime.get_terminal_budget_snapshots(run_id=run_id)
        with pytest.raises(DurableExecutionCancellationPendingError):
            await runtime.resume(run_id=run_id, pause_token=paused.pause.pause_token, command="确认")
        assert (await runtime.get_run(run_id=run_id)).activity == "cancelling"
        settled = await runtime.settle_cancel(run_id=run_id)
        assert settled.status == "cancelled"
        assert settled.state.delivery_status is DeliveryStatus.CANCELLED
        assert (await runtime.get_state(run_id=run_id)) == settled.state
        assert type(await runtime.recover(run_id=run_id)) is DurableExecutionTerminal
        assert (await runtime.get_run(run_id=run_id)).activity == "terminal"
        snapshots = await runtime.get_terminal_budget_snapshots(run_id=run_id)
        assert snapshots and all(item.run_id == run_id for item in snapshots)
        assert (await runtime.request_cancel(run_id=run_id)).status == "already_terminal"
        async with db_connection.cursor() as cursor:
            await cursor.execute(
                """UPDATE durable_execution.budget_limits
                   SET consumed_value = consumed_value + 1
                   WHERE run_id = %s AND consumed_value < limit_value""", (run_id,),
            )
            assert cursor.rowcount == 1
        await db_connection.commit()
        with pytest.raises(DurableExecutionStateError):
            await runtime.get_terminal_budget_snapshots(run_id=run_id)
        async with db_connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE durable_execution.budget_limits SET consumed_value = %s WHERE run_id = %s",
                (snapshots[0].consumed, run_id),
            )
        await db_connection.commit()
        async with db_connection.cursor() as cursor:
            await cursor.execute(
                """UPDATE durable_execution.runs
                   SET latest_root_checkpoint_id = 'forged-checkpoint',
                       cancel_base_checkpoint_id = 'forged-checkpoint'
                   WHERE run_id = %s""", (run_id,),
            )
        await db_connection.commit()
        assert (await runtime.get_run(run_id=run_id)).activity == "blocked"
        page = await runtime.list_runs(request=DurableRunListRequest(limit=100))
        assert next(item for item in page.items if item.run_id == run_id).activity == "blocked"
        with pytest.raises(DurableExecutionStateError):
            await runtime.get_state(run_id=run_id)
        with pytest.raises(DurableExecutionStateError):
            await runtime.get_terminal_budget_snapshots(run_id=run_id)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cancel_after_dispatch_with_unknown_result_blocks_instead_of_claiming_cancelled(durable_execution_settings):
    run_id, trace_id = uuid4(), uuid4()
    service = _BlockingConfirmationService()
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=make_requirement_recovery_contract(),
        command_codec=_ConfirmationCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_requirement_graph_factory(service),
        cancellation_state_factory=_cancel_state,
    )
    try:
        paused = await runtime.start(run_id=run_id, trace_id=trace_id, command="开始")
        task = asyncio.create_task(runtime.resume(
            run_id=run_id, pause_token=paused.pause.pause_token, command="确认",
        ))
        await asyncio.wait_for(service.confirm_started.wait(), timeout=10)
        assert (await runtime.request_cancel(run_id=run_id)).status == "requested"
        with pytest.raises(asyncio.CancelledError):
            await task
        service.confirm_release.set()
        result = await runtime.settle_cancel(run_id=run_id)
        assert result.status == "blocked" and result.state is None
        assert (await runtime.get_run(run_id=run_id)).activity == "blocked"
        with pytest.raises(DurableExecutionRunBlockedError):
            await runtime.get_terminal_budget_snapshots(run_id=run_id)
        assert len(service.confirm_calls) == 1
    finally:
        service.confirm_release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_cancel_commit_unknown_reconciles_from_persisted_evidence(durable_execution_settings):
    run_id, trace_id = uuid4(), uuid4()
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(), contract=_runtime_contract(),
        command_codec=_ConfirmationCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True, run_id=run_id, trace_id=trace_id),
        cancellation_state_factory=_cancel_state,
    )
    try:
        await runtime.start(run_id=run_id, trace_id=trace_id, command="开始")
        points = {"cancel_request_commit_unknown", "cancel_terminal_commit_unknown"}
        def fault(point):
            if point in points:
                points.remove(point)
                raise _CommitUnknown("committed")
        runtime._repository._fault_hook = fault
        assert (await runtime.request_cancel(run_id=run_id)).status == "requested"
        assert (await runtime.settle_cancel(run_id=run_id)).status == "cancelled"
        assert points == set()
        assert (await runtime.get_state(run_id=run_id)).delivery_status is DeliveryStatus.CANCELLED
    finally:
        runtime._repository._fault_hook = None
        await runtime.close()


@pytest.mark.asyncio
async def test_cancel_maintenance_waits_for_active_lease_then_settles_without_graph_replay(
    durable_execution_settings, db_connection, monkeypatch,
):
    run_id, trace_id = uuid4(), uuid4()
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(), contract=_runtime_contract(),
        command_codec=_ConfirmationCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True, run_id=run_id, trace_id=trace_id),
        cancellation_state_factory=_cancel_state,
    )
    try:
        assert type(await runtime.start(run_id=run_id, trace_id=trace_id, command="开始")) is DurableExecutionPaused
        lease_owner = uuid4()
        token = await runtime._repository.acquire_lease(run_id=run_id, owner=lease_owner)
        assert token > 1
        entered = asyncio.Event()
        original = DurableExecutionRepository.request_cancellation

        async def signal_request(self, **values):
            if values["run_id"] == run_id:
                entered.set()
            return await original(self, **values)

        with monkeypatch.context() as patch:
            patch.setattr(DurableExecutionRepository, "request_cancellation", signal_request)
            async with runtime._repository.fencing_transaction(
                run_id=run_id, owner=lease_owner, fencing_token=token,
            ):
                cancel = asyncio.create_task(runtime.request_cancel(run_id=run_id))
                await asyncio.wait_for(entered.wait(), timeout=10)
                assert not cancel.done()
            assert (await cancel).status == "requested"
        assert (await runtime.settle_cancel(run_id=run_id)).status == "requested"
        assert (await runtime.settle_cancel(run_id=run_id)).status == "requested"
        with pytest.raises(DurableExecutionCancellationPendingError):
            await runtime._repository.acquire_lease(run_id=run_id, owner=uuid4())
        with pytest.raises(DurableExecutionStateError):
            await runtime._repository.verify_fence(
                run_id=run_id, owner=lease_owner, fencing_token=token,
            )
        async with db_connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE durable_execution.run_leases SET expires_at = clock_timestamp() - interval '1 second' WHERE run_id = %s",
                (run_id,),
            )
            assert cursor.rowcount == 1
        await db_connection.commit()
        assert (await runtime.settle_cancel(run_id=run_id)).status == "cancelled"
        assert (await runtime.settle_cancel(run_id=run_id)).status == "already_terminal"
        assert (await runtime.get_state(run_id=run_id)).delivery_status is DeliveryStatus.CANCELLED
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ("cancel", "completion"))
async def test_cancel_and_normal_resume_obey_first_committed_decision(
    durable_execution_settings, monkeypatch, winner,
):
    run_id, trace_id = uuid4(), uuid4()
    starter = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(), contract=_runtime_contract(),
        command_codec=_ConfirmationCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True, run_id=run_id, trace_id=trace_id),
        cancellation_state_factory=_cancel_state,
    )
    runtime = None
    release = asyncio.Event()
    entered = asyncio.Event()
    try:
        paused = await starter.start(run_id=run_id, trace_id=trace_id, command="开始")
        await starter.close()
        runtime = await create_durable_execution_runtime(
            settings=durable_execution_settings,
            artifact_repository=FakeArtifactRepository(), contract=_runtime_contract(),
            command_codec=_ConfirmationCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
            graph_factory=_factory(process=False, run_id=run_id, trace_id=trace_id),
            cancellation_state_factory=_cancel_state,
        )
        if winner == "cancel":
            original = DurableExecutionRepository.transition_pending_command

            async def held_accept(self, **values):
                if (values["run_id"] == run_id and values["expected_status"] == "awaiting_input"
                    and values["new_status"] == "accepted"):
                    entered.set()
                    await release.wait()
                return await original(self, **values)

            with monkeypatch.context() as patch:
                patch.setattr(DurableExecutionRepository, "transition_pending_command", held_accept)
                resume = asyncio.create_task(runtime.resume(
                    run_id=run_id, pause_token=paused.pause.pause_token, command="确认",
                ))
                await asyncio.wait_for(entered.wait(), timeout=10)
                assert (await runtime.request_cancel(run_id=run_id)).status == "requested"
                release.set()
                with pytest.raises(DurableExecutionStateError):
                    await resume
            assert (await runtime.settle_cancel(run_id=run_id)).status == "cancelled"
        else:
            original = DurableExecutionRepository.request_cancellation

            async def held_cancel(self, **values):
                if values["run_id"] == run_id:
                    entered.set()
                    await release.wait()
                return await original(self, **values)

            with monkeypatch.context() as patch:
                patch.setattr(DurableExecutionRepository, "request_cancellation", held_cancel)
                cancel = asyncio.create_task(runtime.request_cancel(run_id=run_id))
                await asyncio.wait_for(entered.wait(), timeout=10)
                assert type(await runtime.resume(
                    run_id=run_id, pause_token=paused.pause.pause_token, command="确认",
                )) is DurableExecutionTerminal
                release.set()
                assert (await cancel).status == "already_terminal"
        assert (await runtime.get_run(run_id=run_id)).activity == "terminal"
    finally:
        release.set()
        if runtime is not None:
            await runtime.close()
        await starter.close()
