from __future__ import annotations

import hashlib
import json
import asyncio
import multiprocessing
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.durable_execution import (
    DurableExecutionContract, DurableExecutionNodeRecoveryRule,
    DurableExecutionPaused, DurableExecutionTerminal, NodeRecoveryClass,
    DurableExecutionDependencyError,
    DurableExecutionRunBlockedError,
    DurableExecutionStateError,
    create_durable_execution_runtime,
    load_durable_execution_settings,
)
from app.durable_execution.repository import DurableExecutionRepository
from app.workflow_contracts import DeliveryStatus, TravelSessionState, WorkflowPhase
from tests.requirement_subgraph_fakes import make_confirmed_transition
from tests.support.durable_execution_fakes import (
    FakeArtifactRepository, FakeBudgetPlanProvider, FakeCommandCodec,
)


class _NativeCodec(FakeCommandCodec):
    _KNOWN_NODES = frozenset({"requirements", "__root__"})


def _contract():
    rules = tuple(sorted((
        DurableExecutionNodeRecoveryRule(node_name="requirements", phase=WorkflowPhase.REQUIREMENTS, recovery_class=NodeRecoveryClass.REPLAY_SAFE),
        DurableExecutionNodeRecoveryRule(node_name="hotel_discover", phase=WorkflowPhase.HOTEL_SELECTION, recovery_class=NodeRecoveryClass.REPLAY_SAFE),
        DurableExecutionNodeRecoveryRule(node_name="hotel_wait", phase=WorkflowPhase.HOTEL_SELECTION, recovery_class=NodeRecoveryClass.NON_RECOVERABLE),
        DurableExecutionNodeRecoveryRule(node_name="finish", phase=WorkflowPhase.HOTEL_SELECTION, recovery_class=NodeRecoveryClass.REPLAY_SAFE),
    ), key=lambda rule: rule.node_name))
    payload = {
        "schema_version": "1.0", "contract_id": "test.native.hotel",
        "command_codec": _NativeCodec.descriptor.model_dump(mode="json"),
        "budgeted_phases": ["requirements", "hotel_selection"],
        "node_rules": [rule.model_dump(mode="json") for rule in rules],
    }
    digest = "sha256:" + hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return DurableExecutionContract(
        contract_id="test.native.hotel", command_codec=_NativeCodec.descriptor,
        budgeted_phases=(WorkflowPhase.REQUIREMENTS, WorkflowPhase.HOTEL_SELECTION),
        node_rules=rules, contract_digest=digest,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ("direct", "accepted_crash", "checkpoint_crash", "sink_failure", "pending_failure", "reselect", "new_process"))
async def test_native_hotel_interrupt_resumes_next_segment_without_replaying_upstream(
    durable_execution_settings, db_connection, monkeypatch, case,
):
    run_id, trace_id = uuid4(), uuid4()
    calls = {"requirements": 0, "hotel_discover": 0, "after_wait": 0}
    stored = {}
    fail_finish = [case == "checkpoint_crash"]

    class _Sink:
        async def store(self, *, run_id, pause_generation, contract_digest, pause_token):
            if case == "sink_failure":
                raise RuntimeError("credential unavailable")
            stored[(run_id, pause_generation, contract_digest)] = pause_token

    def build(caps):
        graph = StateGraph(TravelSessionState)

        async def requirements(state):
            calls["requirements"] += 1
            transition = make_confirmed_transition(state)
            return TravelSessionState.model_validate({
                **state.model_dump(mode="python"),
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
                "phase": WorkflowPhase.HOTEL_SELECTION,
            }, strict=True)

        async def hotel_discover(state):
            calls["hotel_discover"] += 1
            await caps.for_node(node_name="hotel_discover").ensure_phase_budget(state=state)
            return state.model_copy(update={"delivery_status": DeliveryStatus.AWAITING_USER})

        async def hotel_wait(state):
            assert interrupt({"kind": "hotel_wait"}) is True
            assert caps.for_node(node_name="hotel_wait").current_command() == "accept"
            calls["after_wait"] += 1
            if case == "reselect" and calls["after_wait"] == 1:
                return Command(update={name: getattr(state, name) for name in type(state).model_fields}, goto="hotel_wait")
            return Command(update={"delivery_status": DeliveryStatus.IN_PROGRESS}, goto="finish")

        def finish(state):
            if fail_finish[0]:
                fail_finish[0] = False
                raise DurableExecutionStateError()
            return state.model_copy(update={
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
            })

        graph.add_node("requirements", requirements)
        graph.add_node("hotel_discover", hotel_discover)
        graph.add_node("hotel_wait", hotel_wait)
        graph.add_node("finish", finish)
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "hotel_discover")
        graph.add_edge("hotel_discover", "hotel_wait")
        graph.add_edge("finish", END)
        return graph

    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings, artifact_repository=FakeArtifactRepository(),
        contract=_contract(), command_codec=_NativeCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(), graph_factory=build,
        pause_credential_sink=_Sink(),
    )
    try:
        if case == "sink_failure":
            with pytest.raises(DurableExecutionDependencyError):
                await runtime.start(run_id=run_id, trace_id=trace_id, command="start")
            assert (await runtime.get_run(run_id=run_id)).activity == "blocked"
            return
        if case == "pending_failure":
            original = DurableExecutionRepository.create_pending_command

            async def fail_waiting(self, **values):
                if values["run_id"] == run_id and values["pause_generation"] == 1:
                    raise DurableExecutionDependencyError() from None
                return await original(self, **values)

            with monkeypatch.context() as patch:
                patch.setattr(DurableExecutionRepository, "create_pending_command", fail_waiting)
                with pytest.raises(DurableExecutionDependencyError):
                    await runtime.start(run_id=run_id, trace_id=trace_id, command="start")
            assert stored[(run_id, 1, runtime._contract.contract_digest)]
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
            with pytest.raises(DurableExecutionRunBlockedError):
                await runtime.recover(run_id=run_id)
            return
        paused = await runtime.start(run_id=run_id, trace_id=trace_id, command="start")
        assert type(paused) is DurableExecutionPaused
        assert stored[(run_id, 1, runtime._contract.contract_digest)] == paused.pause.pause_token
        assert paused.state.phase is WorkflowPhase.HOTEL_SELECTION
        if case == "accepted_crash":
            original = DurableExecutionRepository.transition_pending_command
            async def stop_before_dispatch(self, **kwargs):
                if (kwargs["run_id"] == run_id and kwargs["pause_generation"] == 1
                    and kwargs["expected_status"] == "accepted"
                    and kwargs["new_status"] == "dispatch_started"):
                    raise asyncio.CancelledError()
                return await original(self, **kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(DurableExecutionRepository, "transition_pending_command", stop_before_dispatch)
                with pytest.raises(asyncio.CancelledError):
                    await runtime.resume(run_id=run_id, pause_token=paused.pause.pause_token, command="accept")
            terminal = await runtime.recover(run_id=run_id)
        elif case == "checkpoint_crash":
            with pytest.raises(DurableExecutionStateError):
                await runtime.resume(run_id=run_id,pause_token=paused.pause.pause_token,command="accept")
            terminal = await runtime.recover(run_id=run_id)
            async with db_connection.cursor() as cursor:
                await cursor.execute("SELECT count(*) FROM durable_execution.pending_commands WHERE run_id=%s AND status='cleared' AND command_bytes IS NULL AND pause_context_bytes IS NULL",(run_id,))
                row = await cursor.fetchone()
                assert (next(iter(row.values())) if isinstance(row,dict) else row[0]) == 2
        else:
            await runtime.close()
            if case == "new_process":
                context = multiprocessing.get_context("spawn")
                parent, child = context.Pipe()
                process = context.Process(target=_resume_native_worker, args=(child,))
                process.start()
                child.close()
                try:
                    parent.send((run_id, paused.pause.pause_token))
                    assert await asyncio.to_thread(parent.poll, 30)
                    result = parent.recv()
                    await asyncio.to_thread(process.join, 30)
                    assert process.exitcode == 0
                    assert result == ("demo_ready", 1, ("requirements", "hotel_selection"))
                    assert calls == {"requirements": 1, "hotel_discover": 1, "after_wait": 0}
                finally:
                    if process.is_alive():
                        process.terminate()
                        await asyncio.to_thread(process.join, 10)
                    parent.close()
                    process.close()
                return
            runtime = await create_durable_execution_runtime(
                settings=durable_execution_settings, artifact_repository=FakeArtifactRepository(),
                contract=_contract(), command_codec=_NativeCodec(),
                budget_plan_provider=FakeBudgetPlanProvider(), graph_factory=build,
                pause_credential_sink=_Sink(),
            )
            recovered_token = stored[(run_id, 1, runtime._contract.contract_digest)]
            terminal = await runtime.resume(run_id=run_id, pause_token=recovered_token, command="accept")
        if case == "reselect":
            assert type(terminal) is DurableExecutionPaused
            assert terminal.pause.pause_token != paused.pause.pause_token
            assert stored[(run_id, 2, runtime._contract.contract_digest)] == terminal.pause.pause_token
            terminal = await runtime.resume(run_id=run_id, pause_token=terminal.pause.pause_token, command="accept")
        assert type(terminal) is DurableExecutionTerminal
        assert calls == {"requirements": 1, "hotel_discover": 1, "after_wait": 2 if case == "reselect" else 1}
        snapshots = await runtime.get_terminal_budget_snapshots(run_id=run_id)
        assert {item.key.phase for item in snapshots} == {WorkflowPhase.REQUIREMENTS, WorkflowPhase.HOTEL_SELECTION}
    finally:
        await runtime.close()


def _resume_native_worker(pipe):
    """A fresh interpreter receives credentials only through an in-memory pipe."""
    async def run():
        run_id, token = pipe.recv()
        selected = 0

        def build(caps):
            graph = StateGraph(TravelSessionState)

            def upstream(state):
                raise AssertionError("upstream must not execute after hotel pause")

            def hotel_wait(state):
                nonlocal selected
                assert interrupt({"kind": "hotel_wait"}) is True
                assert caps.for_node(node_name="hotel_wait").current_command() == "accept"
                selected += 1
                return Command(update={"delivery_status": DeliveryStatus.IN_PROGRESS}, goto="finish")

            def finish(state):
                return state.model_copy(update={
                    "delivery_status": DeliveryStatus.DEMO_READY,
                    "terminal_code": "workflow_completed",
                })

            graph.add_node("requirements", upstream)
            graph.add_node("hotel_discover", upstream)
            graph.add_node("hotel_wait", hotel_wait)
            graph.add_node("finish", finish)
            graph.add_edge(START, "requirements")
            graph.add_edge("requirements", "hotel_discover")
            graph.add_edge("hotel_discover", "hotel_wait")
            graph.add_edge("finish", END)
            return graph

        runtime = await create_durable_execution_runtime(
            settings=load_durable_execution_settings(), artifact_repository=FakeArtifactRepository(),
            contract=_contract(), command_codec=_NativeCodec(),
            budget_plan_provider=FakeBudgetPlanProvider(), graph_factory=build,
        )
        try:
            result = await runtime.resume(run_id=run_id, pause_token=token, command="accept")
            assert type(result) is DurableExecutionTerminal
            budgets = await runtime.get_terminal_budget_snapshots(run_id=run_id)
            pipe.send((result.state.delivery_status.value, selected, tuple(b.key.phase.value for b in budgets)))
        finally:
            await runtime.close()
            pipe.close()

    asyncio.run(run())
