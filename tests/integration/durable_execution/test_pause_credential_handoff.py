from __future__ import annotations

from uuid import uuid4

import pytest

from app.durable_execution import (
    DurableExecutionDependencyError, DurableExecutionPaused,
    DurableExecutionResumeRejectedError, DurableExecutionRunBlockedError,
    DurableExecutionStateError, DurableExecutionTerminal, create_durable_execution_runtime,
)
from app.durable_execution.repository import DurableExecutionRepository
from tests.integration.durable_execution.test_bootstrap_and_restart import (
    _ConfirmationCommandCodec, _factory, _runtime_contract,
)
from tests.support.durable_execution_fakes import FakeArtifactRepository, FakeBudgetPlanProvider


class _MemorySink:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.values = {}

    async def store(self, *, run_id, pause_generation, contract_digest, pause_token):
        if self.fail:
            raise RuntimeError("credential store unavailable")
        key = (run_id, pause_generation, contract_digest)
        prior = self.values.setdefault(key, pause_token)
        if prior != pause_token:
            raise RuntimeError("token conflict")


@pytest.mark.asyncio
async def test_m05_pause_token_is_stored_before_waiting_is_reported(durable_execution_settings):
    run_id, trace_id = uuid4(), uuid4()
    sink = _MemorySink()
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings, artifact_repository=FakeArtifactRepository(),
        contract=_runtime_contract(), command_codec=_ConfirmationCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True, run_id=run_id, trace_id=trace_id),
        pause_credential_sink=sink,
    )
    try:
        result = await runtime.start(run_id=run_id, trace_id=trace_id, command="开始")
        assert type(result) is DurableExecutionPaused
        assert sink.values[(run_id, 1, runtime._contract.contract_digest)] == result.pause.pause_token
        pending = await runtime._repository.get_pending_command(run_id=run_id)
        assert pending["status"] == "awaiting_input"
        assert result.pause.pause_token.encode() not in pending["pause_context_bytes"]
        assert result.pause.pause_token.encode() not in repr(pending).encode()
        await runtime.close()
        runtime = await create_durable_execution_runtime(
            settings=durable_execution_settings, artifact_repository=FakeArtifactRepository(),
            contract=_runtime_contract(), command_codec=_ConfirmationCommandCodec(),
            budget_plan_provider=FakeBudgetPlanProvider(),
            graph_factory=_factory(process=False, run_id=run_id, trace_id=trace_id),
            pause_credential_sink=sink,
        )
        with pytest.raises(DurableExecutionResumeRejectedError):
            await runtime.resume(run_id=run_id, pause_token="wrong-generation-token", command="确认")
        recovered_token = sink.values[(run_id, 1, runtime._contract.contract_digest)]
        assert type(await runtime.resume(
            run_id=run_id, pause_token=recovered_token, command="确认",
        )) is DurableExecutionTerminal
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_sink_failure_never_reports_pause_and_blocks_recovery(durable_execution_settings):
    run_id, trace_id = uuid4(), uuid4()
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings, artifact_repository=FakeArtifactRepository(),
        contract=_runtime_contract(), command_codec=_ConfirmationCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True, run_id=run_id, trace_id=trace_id),
        pause_credential_sink=_MemorySink(fail=True),
    )
    try:
        with pytest.raises(DurableExecutionDependencyError):
            await runtime.start(run_id=run_id, trace_id=trace_id, command="开始")
        with pytest.raises(DurableExecutionRunBlockedError):
            await runtime.recover(run_id=run_id)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_stored_token_without_waiting_pending_cannot_authorize_recovery(
    durable_execution_settings, db_connection, monkeypatch,
):
    run_id, trace_id = uuid4(), uuid4()
    sink = _MemorySink()
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings, artifact_repository=FakeArtifactRepository(),
        contract=_runtime_contract(), command_codec=_ConfirmationCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True, run_id=run_id, trace_id=trace_id),
        pause_credential_sink=sink,
    )
    original = DurableExecutionRepository.create_pending_command
    async def fail_waiting(self, **kwargs):
        if kwargs["run_id"] == run_id and kwargs["pause_generation"] == 1:
            raise DurableExecutionDependencyError() from None
        return await original(self, **kwargs)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(DurableExecutionRepository, "create_pending_command", fail_waiting)
            with pytest.raises(DurableExecutionDependencyError):
                await runtime.start(run_id=run_id, trace_id=trace_id, command="开始")
        assert (run_id, 1, runtime._contract.contract_digest) in sink.values
        pending = await runtime._repository.get_pending_command(run_id=run_id)
        assert pending["status"] == "cleared"
        async with db_connection.cursor() as cursor:
            await cursor.execute(
                """UPDATE durable_execution.run_leases
                   SET expires_at = clock_timestamp() - interval '1 second'
                   WHERE run_id = %s AND lease_owner IS NOT NULL""", (run_id,),
            )
            assert cursor.rowcount == 1
        await db_connection.commit()
        with pytest.raises(DurableExecutionStateError):
            await runtime.recover(run_id=run_id)
        assert (await runtime._repository.get_run_row(run_id=run_id))["lifecycle"] == "blocked"
    finally:
        await runtime.close()
