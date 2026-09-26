from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph

from app.durable_execution import (
    DEMO_DURABLE_EXECUTION_LIMITS,
    DurableExecutionPaused,
    DurableExecutionTerminal,
    create_durable_execution_runtime,
)
from app.durable_execution.errors import (
    DurableExecutionRunAwaitingInputError,
    DurableExecutionRunBlockedError,
    DurableExecutionStateError,
)
from app.durable_execution.models import (
    DurableExecutionContract,
    DurableExecutionNodeRecoveryRule,
    NodeRecoveryClass,
)
from app.requirement_subgraph import (
    RequirementConfirmationResume,
    create_requirement_subgraph_embedded_driver,
)
from app.requirements import RequirementConfirmation
from app.workflow_contracts import DeliveryStatus, TravelSessionState, WorkflowPhase
from app.durable_execution.repository import DurableExecutionRepository
from tests.requirement_subgraph_fakes import (
    ScriptedRequirementDomainService,
    make_confirmed_transition,
    make_initial_state,
    make_need_confirmation_transition,
)
from tests.support.durable_execution_fakes import (
    FakeArtifactRepository,
    FakeBudgetPlanProvider,
    FakeCommandCodec,
    TRACE_ID,
    RUN_ID,
    make_contract,
)


class _ConfirmationCommandCodec(FakeCommandCodec):
    def requirement_resume(self, *, run_id, pause_generation, node_name, decoded_command):
        super().requirement_resume(
            run_id=run_id,
            pause_generation=pause_generation,
            node_name=node_name,
            decoded_command=decoded_command,
        )
        return RequirementConfirmationResume(
            action=RequirementConfirmation(decision="accept", revision_text=None)
        )


def _runtime_contract() -> DurableExecutionContract:
    base = make_contract()
    rules = tuple(sorted(base.node_rules + tuple(
        DurableExecutionNodeRecoveryRule(
            node_name=node_name,
            phase=WorkflowPhase.REQUIREMENTS,
            recovery_class=NodeRecoveryClass.NON_RECOVERABLE,
        )
        for node_name in (
            "requirements/process_initial_turn",
            "requirements/await_clarification",
            "requirements/await_confirmation",
        )
    ), key=lambda rule: rule.node_name))
    payload = {
        "budgeted_phases": [phase.value for phase in base.budgeted_phases],
        "command_codec": base.command_codec.model_dump(mode="json"),
        "contract_id": base.contract_id,
        "node_rules": [rule.model_dump(mode="json") for rule in rules],
        "schema_version": base.schema_version,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return DurableExecutionContract(
        contract_id=base.contract_id,
        schema_version=base.schema_version,
        command_codec=base.command_codec,
        budgeted_phases=base.budgeted_phases,
        node_rules=rules,
        contract_digest="sha256:" + hashlib.sha256(canonical).hexdigest(),
    )


class _GraphInvokeSpy:
    def __init__(self, graph, calls):
        self._graph = graph
        self._calls = calls

    async def ainvoke(self, *args, **kwargs):
        self._calls["graph_invoke"] += 1
        return await self._graph.ainvoke(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._graph, name)


class _ObservedStateGraph(StateGraph):
    def __init__(self, schema, calls):
        super().__init__(schema)
        self._calls = calls

    def compile(self, *args, **kwargs):
        return _GraphInvokeSpy(super().compile(*args, **kwargs), self._calls)


def _factory(*, process: bool, run_id=RUN_ID, trace_id=TRACE_ID, service=None, calls=None):
    initial = make_initial_state(run_id=run_id, trace_id=trace_id)
    if service is None:
        service = ScriptedRequirementDomainService(
            process_results=(make_need_confirmation_transition(initial),) if process else (),
            confirm_results=(make_confirmed_transition(initial),) if not process else (),
        )

    def build(capabilities):
        if calls is not None:
            calls["graph_factory"] += 1
        driver = create_requirement_subgraph_embedded_driver(
            domain_service=service,
            execution_budget_manager_factory=capabilities.execution_budget_manager_factory(
                node_name="requirements"
            ),
            limits=capabilities.requirement_subgraph_limits,
        )
        if calls is not None:
            original_start = driver.start_operation
            original_resume = driver.resume_accepted_operation
            original_resolve = driver.resolve_result

            def start_operation(**kwargs):
                calls["start_scope"] += 1
                return original_start(**kwargs)

            def resume_accepted_operation(**kwargs):
                calls["resume_scope"] += 1
                return original_resume(**kwargs)

            def resolve_result(**kwargs):
                calls["resolve_result"] += 1
                return original_resolve(**kwargs)

            driver.start_operation = start_operation
            driver.resume_accepted_operation = resume_accepted_operation
            driver.resolve_result = resolve_result
        capabilities.register_requirement_subgraph(node_name="requirements", driver=driver)
        builder = StateGraph(TravelSessionState) if calls is None else _ObservedStateGraph(
            TravelSessionState, calls
        )
        builder.add_node("requirements", driver.graph)

        def finish(state: TravelSessionState) -> TravelSessionState:
            if calls is not None:
                calls["finish_node"] += 1
            return state.model_copy(
                update={
                    "delivery_status": DeliveryStatus.DEMO_READY,
                    "terminal_code": "workflow_completed",
                    "terminal_outcome": None,
                }
            )

        builder.add_node("finish", finish)
        builder.add_edge(START, "requirements")
        builder.add_edge("requirements", "finish")
        builder.add_edge("finish", END)
        return builder

    return build


class _CountingCodec(_ConfirmationCommandCodec):
    def __init__(self) -> None:
        self.calls = {name: 0 for name in (
            "decode_initial", "requirement_turn", "decode_resume", "requirement_resume"
        )}

    def decode_initial(self, **kwargs):
        self.calls["decode_initial"] += 1
        return super().decode_initial(**kwargs)

    def requirement_turn(self, **kwargs):
        self.calls["requirement_turn"] += 1
        return super().requirement_turn(**kwargs)

    def decode_resume(self, **kwargs):
        self.calls["decode_resume"] += 1
        return super().decode_resume(**kwargs)

    def requirement_resume(self, **kwargs):
        self.calls["requirement_resume"] += 1
        return super().requirement_resume(**kwargs)


def _new_calls() -> dict[str, int]:
    return {name: 0 for name in (
        "graph_factory", "graph_invoke", "start_scope", "resume_scope",
        "resolve_result", "finish_node",
    )}


async def _new_observed_runtime(settings, *, run_id, trace_id, process):
    initial = make_initial_state(run_id=run_id, trace_id=trace_id)
    service = ScriptedRequirementDomainService(
        process_results=(make_need_confirmation_transition(initial),) if process else (),
        confirm_results=(make_confirmed_transition(initial),) if not process else (),
    )
    codec = _CountingCodec()
    calls = _new_calls()
    provider = FakeBudgetPlanProvider()
    runtime = await create_durable_execution_runtime(
        settings=settings,
        artifact_repository=FakeArtifactRepository(),
        contract=_runtime_contract(),
        command_codec=codec,
        budget_plan_provider=provider,
        graph_factory=_factory(
            process=process, run_id=run_id, trace_id=trace_id, service=service, calls=calls
        ),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    return runtime, codec, calls, service, provider


async def _run_evidence(connection, run_id):
    columns = (
        "lifecycle", "blocked_reason", "bootstrap", "generation", "checkpoint_id",
        "checkpoint_digest", "pending_status", "command_kind", "command_present",
        "dispatch_marker_absent", "context_present", "context_digest", "pre_checkpoint_id",
        "lease_held", "fence", "lease_active", "checkpoints", "operations", "journal",
        "active_run_count", "stored_run_count",
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT r.lifecycle, r.blocked_reason_code, r.bootstrap_state,
                       r.pause_generation, r.latest_root_checkpoint_id,
                       r.latest_root_checkpoint_digest, p.status, p.command_kind,
                       p.command_bytes IS NOT NULL, p.dispatch_started_at IS NULL,
                       p.pause_context_bytes IS NOT NULL, p.pause_context_digest,
                       p.pre_checkpoint_id, l.lease_owner IS NOT NULL,
                       l.fencing_token,
                       l.lease_owner IS NOT NULL AND l.expires_at > clock_timestamp(),
                       (SELECT count(*) FROM durable_execution.checkpoints WHERE thread_id = %s),
                       (SELECT count(*) FROM durable_execution.budget_operations WHERE run_id = %s),
                        (SELECT count(*) FROM durable_execution.operation_journal WHERE run_id = %s),
                        (SELECT active_run_count FROM durable_execution.capacity_state WHERE singleton_id = 1),
                        (SELECT stored_run_count FROM durable_execution.capacity_state WHERE singleton_id = 1)
                FROM durable_execution.runs r
                JOIN durable_execution.pending_commands p
                  ON p.run_id = r.run_id AND p.pause_generation = r.pause_generation
                JOIN durable_execution.run_leases l ON l.run_id = r.run_id
                WHERE r.run_id = %s
                """,
                (str(run_id), run_id, run_id, run_id),
            )
            row = await cursor.fetchone()
        assert row is not None, f"missing run evidence for {run_id}"
        return dict(zip(columns, row, strict=True))
    finally:
        await connection.rollback()


async def _expire_interrupted_lease(connection, run_id) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute(
            """
            UPDATE durable_execution.run_leases
            SET expires_at = clock_timestamp() - interval '1 second'
            WHERE run_id = %s AND lease_owner IS NOT NULL
            """,
            (run_id,),
        )
        assert cursor.rowcount == 1
    await connection.commit()


async def _interrupt_before_dispatch(monkeypatch, *, run_id, generation, operation) -> None:
    original = DurableExecutionRepository.transition_pending_command
    attempts = []

    async def stop_at_marker(repository, **values):
        if (
            values["run_id"] == run_id
            and values["pause_generation"] == generation
            and values["expected_status"] == "accepted"
            and values["new_status"] == "dispatch_started"
        ):
            attempts.append(values["fencing_token"])
            raise asyncio.CancelledError()
        return await original(repository, **values)

    with monkeypatch.context() as patch:
        patch.setattr(DurableExecutionRepository, "transition_pending_command", stop_at_marker)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation(), timeout=30)
    assert len(attempts) == 1


def _emit_evidence(case, run_id, **values) -> None:
    print("ACCEPTED_RECOVERY_EVIDENCE " + json.dumps(
        {"case": case, "run_id": str(run_id), **values},
        ensure_ascii=False, sort_keys=True, default=str,
    ))


@pytest.mark.asyncio
async def test_runtime_bootstrap_pause_and_cross_runtime_resume(durable_execution_settings) -> None:
    first = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=_runtime_contract(),
        command_codec=_ConfirmationCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=True),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    paused = await first.start(run_id=RUN_ID, trace_id=TRACE_ID, command="开始")
    assert type(paused) is DurableExecutionPaused
    state = await first.get_state(run_id=RUN_ID)
    assert state == paused.state
    await first.close()

    second = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=_runtime_contract(),
        command_codec=_ConfirmationCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_factory(process=False),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    terminal = await second.resume(run_id=RUN_ID, pause_token=paused.pause.pause_token, command="确认")
    assert type(terminal) is DurableExecutionTerminal
    assert terminal.state.delivery_status is DeliveryStatus.DEMO_READY
    await second.close()


async def _accepted_initial(settings, connection, monkeypatch):
    run_id, trace_id = uuid4(), uuid4()
    first, codec, calls, service, provider = await _new_observed_runtime(
        settings, run_id=run_id, trace_id=trace_id, process=True
    )
    try:
        await _interrupt_before_dispatch(
            monkeypatch, run_id=run_id, generation=0,
            operation=lambda: first.start(run_id=run_id, trace_id=trace_id, command="开始"),
        )
        with pytest.raises(DurableExecutionStateError):
            await asyncio.wait_for(first.get_state(run_id=run_id), timeout=30)
    finally:
        await first.close()
    before = await _run_evidence(connection, run_id)
    assert before["lifecycle"] == "open"
    assert before["bootstrap"] == "bootstrap_pending"
    assert before["generation"] == 0
    assert before["pending_status"] == "accepted"
    assert before["command_kind"] == "initial"
    assert before["command_present"] and before["dispatch_marker_absent"]
    assert before["checkpoint_id"] is None and before["checkpoints"] == 0
    assert before["operations"] == before["journal"] == 0
    assert not before["context_present"] and before["pre_checkpoint_id"] is None
    assert before["lease_held"] and before["fence"] == 1
    assert service.process_calls == [] and service.confirm_calls == []
    assert calls["start_scope"] == 1
    assert calls["graph_invoke"] == calls["resume_scope"] == calls["finish_node"] == 0
    assert codec.calls["requirement_turn"] == 1 and codec.calls["requirement_resume"] == 0
    assert provider.start_calls == [run_id, run_id]
    await _expire_interrupted_lease(connection, run_id)
    expired = await _run_evidence(connection, run_id)
    assert expired["pending_status"] == "accepted" and not expired["lease_active"]
    assert expired["fence"] == before["fence"]
    return run_id, trace_id, before, expired


async def _accepted_resume(settings, connection, monkeypatch):
    run_id, trace_id = uuid4(), uuid4()
    first, codec, calls, service, provider = await _new_observed_runtime(
        settings, run_id=run_id, trace_id=trace_id, process=True
    )
    try:
        paused = await asyncio.wait_for(
            first.start(run_id=run_id, trace_id=trace_id, command="开始"), timeout=30
        )
        assert type(paused) is DurableExecutionPaused
        paused_evidence = await _run_evidence(connection, run_id)
        assert paused_evidence["generation"] == 1
        assert paused_evidence["pending_status"] == "awaiting_input"
        assert paused_evidence["checkpoint_id"] is not None
        assert paused_evidence["checkpoints"] > 0
        assert not paused_evidence["lease_held"]
        await _interrupt_before_dispatch(
            monkeypatch, run_id=run_id, generation=1,
            operation=lambda: first.resume(
                run_id=run_id, pause_token=paused.pause.pause_token, command="确认"
            ),
        )
        with pytest.raises(DurableExecutionStateError):
            await asyncio.wait_for(first.get_state(run_id=run_id), timeout=30)
    finally:
        await first.close()
    before = await _run_evidence(connection, run_id)
    assert before["lifecycle"] == "open"
    assert before["generation"] == 1
    assert before["pending_status"] == "accepted"
    assert before["command_kind"] == "resume"
    assert before["command_present"] and before["dispatch_marker_absent"]
    assert before["context_present"] and before["context_digest"] is not None
    assert before["pre_checkpoint_id"] == paused_evidence["checkpoint_id"]
    assert before["checkpoint_id"] == paused_evidence["checkpoint_id"]
    assert before["checkpoint_digest"] == paused_evidence["checkpoint_digest"]
    assert before["checkpoints"] == paused_evidence["checkpoints"]
    assert not before["lease_held"] and before["fence"] == 2
    assert len(service.process_calls) == 1 and service.confirm_calls == []
    assert calls["start_scope"] == calls["resume_scope"] == 1
    assert calls["graph_invoke"] == 1 and calls["finish_node"] == 0
    assert codec.calls["requirement_turn"] == codec.calls["requirement_resume"] == 1
    assert provider.start_calls == [run_id] * 4
    return run_id, trace_id, paused, paused_evidence, before, calls, service


@pytest.mark.asyncio
async def test_recover_generation_zero_accepted_uses_initial_route(
    durable_execution_settings, db_connection, monkeypatch,
) -> None:
    run_id, trace_id, before, expired = await _accepted_initial(
        durable_execution_settings, db_connection, monkeypatch
    )
    second, codec, calls, service, provider = await _new_observed_runtime(
        durable_execution_settings, run_id=run_id, trace_id=trace_id, process=True
    )
    try:
        with pytest.raises(DurableExecutionStateError):
            await asyncio.wait_for(second.get_state(run_id=run_id), timeout=30)
        assert await _run_evidence(db_connection, run_id) == expired
        recovered = await asyncio.wait_for(second.recover(run_id=run_id), timeout=30)
        assert type(recovered) is DurableExecutionPaused
        assert len(service.process_calls) == 1 and service.confirm_calls == []
        assert codec.calls["decode_initial"] == codec.calls["requirement_turn"] == 1
        assert codec.calls["decode_resume"] == codec.calls["requirement_resume"] == 0
        assert calls["graph_factory"] == calls["graph_invoke"] == calls["start_scope"] == 1
        assert calls["resume_scope"] == calls["finish_node"] == 0
        assert calls["resolve_result"] >= 1
        assert provider.start_calls == [run_id, run_id]
        after = await _run_evidence(db_connection, run_id)
        assert after["lifecycle"] == "open"
        assert after["bootstrap"] == "bootstrap_checkpointed"
        assert after["generation"] == 1 and after["pending_status"] == "awaiting_input"
        assert after["checkpoint_id"] is not None and after["checkpoints"] > 0
        assert not after["command_present"] and not after["lease_held"]
        assert after["fence"] == before["fence"] + 1
        assert await asyncio.wait_for(second.get_state(run_id=run_id), timeout=30) == recovered.state
        with pytest.raises(DurableExecutionRunAwaitingInputError):
            await asyncio.wait_for(second.recover(run_id=run_id), timeout=30)
        _emit_evidence("generation_zero_initial", run_id, before=before, after=after,
                       codec=codec.calls, driver_graph=calls,
                       domain={"process": len(service.process_calls), "confirm": len(service.confirm_calls)})
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_recover_generation_one_accepted_uses_resume_route(
    durable_execution_settings, db_connection, monkeypatch,
) -> None:
    run_id, trace_id, paused, paused_evidence, before, first_calls, first_service = (
        await _accepted_resume(durable_execution_settings, db_connection, monkeypatch)
    )
    second, codec, calls, service, provider = await _new_observed_runtime(
        durable_execution_settings, run_id=run_id, trace_id=trace_id, process=False
    )
    try:
        recovered = await asyncio.wait_for(second.recover(run_id=run_id), timeout=30)
        assert type(recovered) is DurableExecutionTerminal
        assert recovered.state.delivery_status is DeliveryStatus.DEMO_READY
        assert len(first_service.process_calls) == 1 and first_service.confirm_calls == []
        assert service.process_calls == [] and len(service.confirm_calls) == 1
        assert codec.calls["decode_initial"] == codec.calls["requirement_turn"] == 0
        assert codec.calls["decode_resume"] == codec.calls["requirement_resume"] == 1
        assert calls["graph_factory"] == calls["graph_invoke"] == 1
        assert calls["resume_scope"] == calls["finish_node"] == 1
        assert calls["start_scope"] == 0 and calls["resolve_result"] >= 1
        assert provider.start_calls == [run_id, run_id]
        after = await _run_evidence(db_connection, run_id)
        assert after["lifecycle"] == "closed" and after["pending_status"] == "cleared"
        assert after["checkpoint_id"] != paused_evidence["checkpoint_id"]
        assert after["checkpoints"] > before["checkpoints"]
        assert not after["command_present"] and not after["lease_held"]
        assert after["fence"] == before["fence"] + 1
        assert await asyncio.wait_for(second.recover(run_id=run_id), timeout=30) == recovered
        assert await asyncio.wait_for(second.get_state(run_id=run_id), timeout=30) == recovered.state
        assert service.process_calls == [] and len(service.confirm_calls) == 1
        assert calls["resume_scope"] == calls["finish_node"] == 1
        _emit_evidence("generation_one_resume", run_id, before=before, after=after,
                       first_driver_graph=first_calls, recovered_driver_graph=calls,
                       codec=codec.calls,
                       first_domain={"process": len(first_service.process_calls), "confirm": len(first_service.confirm_calls)},
                       recovered_domain={"process": len(service.process_calls), "confirm": len(service.confirm_calls)})
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_recover_generation_zero_accepted_rejects_command_digest_mismatch(
    durable_execution_settings, db_connection, monkeypatch,
) -> None:
    run_id, trace_id, _, _ = await _accepted_initial(
        durable_execution_settings, db_connection, monkeypatch
    )
    async with db_connection.cursor() as cursor:
        await cursor.execute(
            """UPDATE durable_execution.pending_commands SET command_digest = %s
               WHERE run_id = %s AND pause_generation = 0 AND status = 'accepted'""",
            ("sha256:" + "0" * 64, run_id),
        )
        assert cursor.rowcount == 1
    await db_connection.commit()
    before = await _run_evidence(db_connection, run_id)
    second, codec, calls, service, _ = await _new_observed_runtime(
        durable_execution_settings, run_id=run_id, trace_id=trace_id, process=True
    )
    try:
        with pytest.raises(DurableExecutionStateError) as caught:
            await asyncio.wait_for(second.recover(run_id=run_id), timeout=30)
        assert type(caught.value) is DurableExecutionStateError
        after = await _run_evidence(db_connection, run_id)
        assert after["lifecycle"] == "blocked"
        assert after["blocked_reason"] == "pending_command_invalid"
        assert after["pending_status"] == "cleared" and not after["command_present"]
        assert not after["lease_active"]
        assert after["checkpoints"] == before["checkpoints"] == 0
        assert after["fence"] == before["fence"]
        assert after["active_run_count"] == before["active_run_count"] - 1
        assert after["stored_run_count"] == before["stored_run_count"]
        assert after["operations"] == before["operations"] == 0
        assert after["journal"] == before["journal"] == 0
        assert all(value == 0 for value in codec.calls.values())
        assert calls["graph_invoke"] == calls["start_scope"] == calls["resume_scope"] == calls["finish_node"] == 0
        assert service.process_calls == service.confirm_calls == []
        blocked_calls = calls.copy()
        for blocked_call in (
            lambda: second.recover(run_id=run_id),
            lambda: second.get_state(run_id=run_id),
            lambda: second.resume(
                run_id=run_id, pause_token="blocked-run", command="blocked-run",
            ),
        ):
            with pytest.raises(DurableExecutionRunBlockedError) as blocked:
                await asyncio.wait_for(blocked_call(), timeout=30)
            assert type(blocked.value) is DurableExecutionRunBlockedError
        assert await _run_evidence(db_connection, run_id) == after
        assert all(value == 0 for value in codec.calls.values())
        assert calls == blocked_calls
        assert service.process_calls == service.confirm_calls == []
        _emit_evidence("generation_zero_invalid_digest", run_id, before=before, after=after,
                       codec=codec.calls, driver_graph=calls,
                       domain={"process": 0, "confirm": 0})
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_recover_generation_one_accepted_rejects_context_digest_mismatch(
    durable_execution_settings, db_connection, monkeypatch,
) -> None:
    run_id, trace_id, paused, _, _, _, first_service = await _accepted_resume(
        durable_execution_settings, db_connection, monkeypatch
    )
    async with db_connection.cursor() as cursor:
        await cursor.execute(
            """UPDATE durable_execution.pending_commands SET pause_context_digest = %s
               WHERE run_id = %s AND pause_generation = 1 AND status = 'accepted'""",
            ("sha256:" + "0" * 64, run_id),
        )
        assert cursor.rowcount == 1
    await db_connection.commit()
    before = await _run_evidence(db_connection, run_id)
    second, codec, calls, service, _ = await _new_observed_runtime(
        durable_execution_settings, run_id=run_id, trace_id=trace_id, process=False
    )
    try:
        with pytest.raises(DurableExecutionStateError) as caught:
            await asyncio.wait_for(second.recover(run_id=run_id), timeout=30)
        assert type(caught.value) is DurableExecutionStateError
        after = await _run_evidence(db_connection, run_id)
        assert after["lifecycle"] == "blocked"
        assert after["blocked_reason"] == "pending_command_invalid"
        assert after["pending_status"] == "cleared" and not after["command_present"]
        assert not after["context_present"] and not after["lease_active"]
        assert after["checkpoints"] == before["checkpoints"]
        assert after["fence"] == before["fence"]
        assert after["active_run_count"] == before["active_run_count"] - 1
        assert after["stored_run_count"] == before["stored_run_count"]
        assert after["operations"] == before["operations"]
        assert after["journal"] == before["journal"]
        assert all(value == 0 for value in codec.calls.values())
        assert calls["graph_invoke"] == calls["start_scope"] == calls["resume_scope"] == calls["finish_node"] == 0
        assert service.process_calls == service.confirm_calls == []
        assert len(first_service.process_calls) == 1 and first_service.confirm_calls == []
        blocked_calls = calls.copy()
        for blocked_call in (
            lambda: second.recover(run_id=run_id),
            lambda: second.get_state(run_id=run_id),
            lambda: second.resume(
                run_id=run_id, pause_token=paused.pause.pause_token, command="确认",
            ),
        ):
            with pytest.raises(DurableExecutionRunBlockedError) as blocked:
                await asyncio.wait_for(blocked_call(), timeout=30)
            assert type(blocked.value) is DurableExecutionRunBlockedError
        assert await _run_evidence(db_connection, run_id) == after
        assert all(value == 0 for value in codec.calls.values())
        assert calls == blocked_calls
        assert service.process_calls == service.confirm_calls == []
        assert len(first_service.process_calls) == 1 and first_service.confirm_calls == []
        _emit_evidence("generation_one_invalid_context", run_id, before=before, after=after,
                       codec=codec.calls, driver_graph=calls,
                       first_domain={"process": 1, "confirm": 0},
                       recovered_domain={"process": 0, "confirm": 0})
    finally:
        await second.close()
