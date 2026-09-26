from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from psycopg.rows import dict_row

from app.artifact_persistence import create_artifact_persistence_runtime
from app.artifact_persistence.settings import ArtifactPersistenceSettings
from app.durable_execution import (
    DEMO_DURABLE_EXECUTION_LIMITS,
    DurableExecutionRunBlockedError,
    DurableExecutionRunBusyError,
    DurableExecutionTerminal,
    create_durable_execution_runtime,
)
from app.durable_execution.errors import DurableExecutionDependencyError, DurableExecutionStateError
from app.durable_execution.models import NodeRecoveryClass
from app.durable_execution.repository import DurableExecutionRepository
from app.durable_execution.runtime import DurableExecutionRuntime
from app.workflow_contracts import (
    BudgetConsumeRequest,
    DeliveryStatus,
    ExecutionBudgetKey,
    ExecutionBudgetScope,
    OperationResultIndeterminateError,
    TravelSessionState,
)
from app.workflow_contracts.artifacts.models import ArtifactAbsent, ArtifactFound, ArtifactRef, ArtifactWriteRequest
from app.workflow_contracts.artifacts.registry import ArtifactSchemaRegistration, ArtifactSchemaRegistry
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase
from tests.support.durable_execution_fakes import (
    FakeArtifactRepository,
    FakeBudgetPlanProvider,
    FakeCommandCodec,
    make_recovery_contract,
)
from tests.requirement_subgraph_fakes import make_confirmed_transition
from tests.unit.workflow_contracts.fakes import StrictSampleArtifact


class _JournalRecorder:
    async def get_operation_journal_evidence(self, *, run_id, operation_id, node_name, owner, fencing_token):
        assert run_id == operation_id.run_id
        assert node_name == "requirements"
        assert type(owner) is UUID and fencing_token == 1
        return {"latest_state": "execution_permitted", "latest_node_name": node_name}

    async def append_journal_transition(self, **values):
        self.transition = values


class _CountingArtifactRepository:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.lookup_calls = 0
        self.get_calls = 0
        self.put_calls = 0

    async def lookup(self, *, operation_id):
        self.lookup_calls += 1
        return await self.delegate.lookup(operation_id=operation_id)

    async def put(self, request):
        self.put_calls += 1
        return await self.delegate.put(request)

    async def get(self, ref, *, expected_model):
        self.get_calls += 1
        return await self.delegate.get(ref, expected_model=expected_model)


async def _expire_lease(runtime, run_id: UUID) -> None:
    async with runtime._repository._pool.connection() as connection:
        async with connection.transaction():
            result = await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET expires_at = clock_timestamp() - interval '1 second'
                WHERE run_id = %s
                """,
                (run_id,),
            )
            assert result.rowcount == 1


@pytest.mark.asyncio
async def test_existing_module10_artifact_is_reused_without_a_second_put(
    durable_execution_settings,
) -> None:
    operation_id = OperationId(
        run_id=uuid4(),
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="sample_result",
    )
    request = ArtifactWriteRequest(
        operation_id=operation_id,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={
            "label": "recovery evidence",
            "count": 10,
            "ratio": 2.5,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": "deterministic recovery",
        },
    )
    settings = ArtifactPersistenceSettings(
        host=durable_execution_settings.host,
        port=durable_execution_settings.port,
        database=durable_execution_settings.database,
        user=durable_execution_settings.user,
        password=durable_execution_settings.password,
    )
    artifact_runtime = await create_artifact_persistence_runtime(
        settings=settings,
        registry=ArtifactSchemaRegistry((ArtifactSchemaRegistration(
            artifact_type="test.sample_artifact",
            schema_version="1.0",
            model=StrictSampleArtifact,
        ),)),
    )
    try:
        original_ref = await artifact_runtime.repository.put(request)
        counted = _CountingArtifactRepository(artifact_runtime.repository)
        journal = _JournalRecorder()
        from app.durable_execution.runtime import _GraphCapabilities

        capabilities = _GraphCapabilities(
            repository=journal,
            artifact_repository=counted,
            contract=make_recovery_contract(nodes=(("requirements", NodeRecoveryClass.JOURNAL_GUARDED),)),
            provider=FakeBudgetPlanProvider(),
            limits=DEMO_DURABLE_EXECUTION_LIMITS,
        )
        capabilities.for_node(node_name="requirements")
        adapter = capabilities.artifact_repository_for(node_name="requirements")
        with capabilities.bind(
            run_id=operation_id.run_id,
            owner=uuid4(),
            fencing_token=1,
            command=None,
            node_name="requirements",
            checkpoint=None,
        ):
            reused_ref = await adapter.put(request)

        outcome = await artifact_runtime.repository.lookup(operation_id=operation_id)
        assert isinstance(outcome, ArtifactFound)
        assert reused_ref == original_ref == outcome.ref
        assert counted.lookup_calls == 1
        assert counted.put_calls == 0
        assert journal.transition["journal_state"] == "artifact_confirmed"
        assert journal.transition["operation_phase"] == operation_id.phase.value
        assert journal.transition["logical_step"] == operation_id.logical_step
    finally:
        await artifact_runtime.close()


@pytest.mark.asyncio
async def test_journal_guarded_recovery_reuses_permissioned_module10_artifact(
    durable_execution_settings,
    monkeypatch,
) -> None:
    settings = ArtifactPersistenceSettings(
        host=durable_execution_settings.host,
        port=durable_execution_settings.port,
        database=durable_execution_settings.database,
        user=durable_execution_settings.user,
        password=durable_execution_settings.password.get_secret_value(),
    )
    artifact_runtime = await create_artifact_persistence_runtime(
        settings=settings,
        registry=ArtifactSchemaRegistry((ArtifactSchemaRegistration(
            artifact_type="test.sample_artifact",
            schema_version="1.0",
            model=StrictSampleArtifact,
        ),)),
    )
    artifacts = _CountingArtifactRepository(artifact_runtime.repository)
    provider = FakeBudgetPlanProvider()
    external_calls = 0

    def build(capabilities):
        node = capabilities.for_node(node_name="requirements")
        graph = StateGraph(TravelSessionState)

        async def requirements(state: TravelSessionState) -> TravelSessionState:
            nonlocal external_calls
            operation_id = OperationId(
                run_id=state.run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="sample_result",
            )
            outcome = await node.artifact_repository.lookup(operation_id=operation_id)
            if isinstance(outcome, ArtifactFound):
                read = await node.artifact_repository.get(
                    outcome.ref,
                    expected_model=StrictSampleArtifact,
                )
                assert read.content.label == "recovery evidence"
                ref = outcome.ref
            else:
                assert isinstance(outcome, ArtifactAbsent)
                await node.execution_budget_manager.consume(BudgetConsumeRequest(
                    operation_id=operation_id,
                    key=ExecutionBudgetKey(
                        scope=ExecutionBudgetScope.PHASE,
                        name="requirements.clarification_round",
                        phase=WorkflowPhase.REQUIREMENTS,
                    ),
                    amount=1,
                ))
                external_calls += 1
                ref = await node.artifact_repository.put(ArtifactWriteRequest(
                    operation_id=operation_id,
                    artifact_type="test.sample_artifact",
                    schema_version="1.0",
                    content={
                        "label": "recovery evidence",
                        "count": 10,
                        "ratio": 2.5,
                        "occurred_on": "2026-08-11",
                        "observed_at": "2026-08-11T09:30:00Z",
                        "note": "deterministic recovery",
                    },
                ))
            transition = make_confirmed_transition(state)
            return state.model_copy(update={
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
                "artifact_refs": (*state.artifact_refs, ref),
            })

        def finish(state: TravelSessionState) -> TravelSessionState:
            transition = make_confirmed_transition(state)
            return state.model_copy(update={
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph.add_node("requirements", requirements)
        graph.add_node("finish", finish)
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "finish")
        graph.add_edge("finish", END)
        return graph

    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=artifacts,
        contract=make_recovery_contract(nodes=(
            ("requirements", NodeRecoveryClass.JOURNAL_GUARDED),
            ("finish", NodeRecoveryClass.REPLAY_SAFE),
        )),
        command_codec=FakeCommandCodec(),
        budget_plan_provider=provider,
        graph_factory=build,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    original_append = DurableExecutionRepository.append_journal_transition
    interrupted = False
    closure_append_states = []
    run_id, trace_id = uuid4(), uuid4()

    async def interrupt_after_module10_put(repository, **values):
        nonlocal interrupted
        if values.get("journal_state") == "artifact_confirmed" and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        result = await original_append(repository, **values)
        if interrupted and values.get("journal_state") in {
            "artifact_confirmed", "checkpoint_confirmed", "completed",
        }:
            closure_append_states.append(values["journal_state"])
        return result

    monkeypatch.setattr(DurableExecutionRepository, "append_journal_transition", interrupt_after_module10_put)
    try:
        with pytest.raises(DurableExecutionDependencyError):
            await runtime.start(run_id=run_id, trace_id=trace_id, command="journal-guarded recovery")
        assert interrupted is True
        assert external_calls == 1
        assert artifacts.put_calls == 1
        pending = await runtime._repository.get_pending_command(run_id=run_id)
        assert pending is not None and pending["status"] == "dispatch_started"
        evidence = await runtime._repository.get_recovery_operation_evidence(run_id=run_id)
        assert len(evidence) == 1
        assert evidence[0]["journal_state"] == "execution_permitted"

        await _expire_lease(runtime, run_id)

        lookup_before_recovery = artifacts.lookup_calls
        get_before_recovery = artifacts.get_calls
        put_before_recovery = artifacts.put_calls
        external_calls_before_recovery = external_calls
        confirmation_observations = []
        original_confirm = DurableExecutionRuntime._confirm_managed_operations

        async def inspect_confirmation(runtime_instance, **values):
            rows = await runtime._repository.get_latest_operation_evidence(
                run_id=values["run_id"],
                owner=values["owner"],
                fencing_token=values["fencing_token"],
            )
            assert len(rows) == 1
            row = rows[0]
            operation_id = OperationId(
                run_id=values["run_id"],
                phase=WorkflowPhase(str(row["operation_phase"])),
                logical_step=str(row["operation_logical_step"]),
            )
            refs = tuple(
                ref for ref in values["state"].artifact_refs
                if ref.operation_id == operation_id
            )
            assert row["journal_state"] == "artifact_confirmed"
            assert row["node_name"] == "requirements"
            assert len(refs) == 1
            ref = refs[0]
            artifact_ref_matches = (
                row["artifact_id"],
                row["artifact_type"],
                row["artifact_schema_version"],
                row["artifact_content_hash"],
                row["artifact_created_at"],
                row["artifact_size_bytes"],
            ) == (
                ref.artifact_id,
                ref.artifact_type,
                ref.schema_version,
                ref.content_hash,
                ref.created_at,
                ref.size_bytes,
            )
            pointer = values["pointer"]
            checkpoint_valid = (
                type(pointer) is tuple
                and len(pointer) == 3
                and pointer[0] == ""
                and type(pointer[1]) is str
                and bool(pointer[1])
                and type(pointer[2]) is str
                and pointer[2].startswith("sha256:")
            )
            confirmation_observations.append({
                "operation_id_matches_ref": operation_id == ref.operation_id,
                "node": row["node_name"],
                "artifact_ref_matches_journal": artifact_ref_matches,
                "checkpoint_valid": checkpoint_valid,
                "fence_verified": True,
            })
            print(
                "FIX03 confirmation evidence: latest=artifact_confirmed; "
                "OperationId/ref match=True; node=requirements; "
                "ArtifactRef/journal match=True; checkpoint valid=True; fence verified=True"
            )
            assert artifact_ref_matches
            assert checkpoint_valid
            return await original_confirm(runtime_instance, **values)

        monkeypatch.setattr(DurableExecutionRuntime, "_confirm_managed_operations", inspect_confirmation)

        terminal = await runtime.recover(run_id=run_id)
        assert type(terminal) is DurableExecutionTerminal
        assert terminal.state.delivery_status is DeliveryStatus.DEMO_READY
        assert confirmation_observations == [{
            "operation_id_matches_ref": True,
            "node": "requirements",
            "artifact_ref_matches_journal": True,
            "checkpoint_valid": True,
            "fence_verified": True,
        }]
        assert external_calls == external_calls_before_recovery == 1
        assert artifacts.lookup_calls - lookup_before_recovery == 3
        assert artifacts.get_calls - get_before_recovery == 1
        assert artifacts.put_calls == put_before_recovery == 1
        assert closure_append_states == [
            "artifact_confirmed", "checkpoint_confirmed", "completed",
        ]
        assert external_calls == 1
        assert artifacts.put_calls == 1
        persisted = await artifact_runtime.repository.lookup(
            operation_id=OperationId(
                run_id=run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="sample_result",
            )
        )
        assert isinstance(persisted, ArtifactFound)
        assert terminal.state.artifact_refs == (persisted.ref,)
        completed_evidence = await runtime._repository.get_recovery_operation_evidence(run_id=run_id)
        assert completed_evidence[0]["journal_state"] == "completed"
        assert completed_evidence[0]["artifact_id"] is not None
        async with runtime._repository._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT node_name, journal_state, artifact_id, artifact_type,
                           artifact_schema_version, artifact_content_hash,
                           artifact_created_at, artifact_size_bytes,
                           checkpoint_ns, checkpoint_id, checkpoint_digest
                    FROM durable_execution.operation_journal
                    WHERE run_id = %s
                    ORDER BY transition_ordinal
                    """,
                    (run_id,),
                )
                journal_history = await cursor.fetchall()
        assert [row["journal_state"] for row in journal_history] == [
            "execution_permitted", "artifact_confirmed",
            "checkpoint_confirmed", "completed",
        ]
        assert all(row["node_name"] == "requirements" for row in journal_history)
        expected_artifact = (
            persisted.ref.artifact_id,
            persisted.ref.artifact_type,
            persisted.ref.schema_version,
            persisted.ref.content_hash,
            persisted.ref.created_at,
            persisted.ref.size_bytes,
        )
        assert all((
            row["artifact_id"], row["artifact_type"], row["artifact_schema_version"],
            row["artifact_content_hash"], row["artifact_created_at"], row["artifact_size_bytes"],
        ) == expected_artifact for row in journal_history[1:])
        assert all(
            row["checkpoint_ns"] is None and row["checkpoint_id"] is None and row["checkpoint_digest"] is None
            for row in journal_history[:2]
        )
        assert all(
            row["checkpoint_ns"] == ""
            and type(row["checkpoint_id"]) is str
            and bool(row["checkpoint_id"])
            and type(row["checkpoint_digest"]) is str
            and row["checkpoint_digest"].startswith("sha256:")
            for row in journal_history[2:]
        )
        assert (
            journal_history[2]["checkpoint_ns"],
            journal_history[2]["checkpoint_id"],
            journal_history[2]["checkpoint_digest"],
        ) == (
            journal_history[3]["checkpoint_ns"],
            journal_history[3]["checkpoint_id"],
            journal_history[3]["checkpoint_digest"],
        )
    finally:
        await runtime.close()
        await artifact_runtime.close()


class _GraphCallCounter:
    def __init__(self, graph, calls: dict[str, int]) -> None:
        self._graph = graph
        self._calls = calls

    async def ainvoke(self, *args, **kwargs):
        self._calls["graph_invoke"] += 1
        return await self._graph.ainvoke(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._graph, name)


class _ObservedArtifactGraph(StateGraph):
    def __init__(self, calls: dict[str, int]) -> None:
        super().__init__(TravelSessionState)
        self._calls = calls

    def compile(self, *args, **kwargs):
        return _GraphCallCounter(super().compile(*args, **kwargs), self._calls)


class _InjectedLookupRepository:
    """只替换目标 OperationId 的一次恢复 lookup；其他操作委托真实 M10。"""

    def __init__(self, delegate, operation_id: OperationId, outcome) -> None:
        self.delegate = delegate
        self.operation_id = operation_id
        self.outcome = outcome
        self.injected_lookups = 0

    async def lookup(self, *, operation_id):
        if operation_id == self.operation_id:
            self.injected_lookups += 1
            return self.outcome
        return await self.delegate.lookup(operation_id=operation_id)

    async def put(self, request):
        return await self.delegate.put(request)

    async def get(self, ref, *, expected_model):
        return await self.delegate.get(ref, expected_model=expected_model)


def _guarded_graph_factory(
    *, run_id: UUID, stop_after: str | None, calls: dict[str, int],
    external_calls: list[UUID],
):
    def build(capabilities):
        node = capabilities.for_node(node_name="requirements")
        graph = _ObservedArtifactGraph(calls)

        async def requirements(state: TravelSessionState) -> TravelSessionState:
            calls["requirements_node"] += 1
            operation_id = OperationId(
                run_id=state.run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="sample_result",
            )
            outcome = await node.artifact_repository.lookup(operation_id=operation_id)
            if isinstance(outcome, ArtifactFound):
                read = await node.artifact_repository.get(
                    outcome.ref, expected_model=StrictSampleArtifact,
                )
                assert read.content.label == "recovery evidence"
                ref = outcome.ref
            else:
                assert isinstance(outcome, ArtifactAbsent)
                await node.execution_budget_manager.consume(BudgetConsumeRequest(
                    operation_id=operation_id,
                    key=ExecutionBudgetKey(
                        scope=ExecutionBudgetScope.PHASE,
                        name="requirements.clarification_round",
                        phase=WorkflowPhase.REQUIREMENTS,
                    ),
                    amount=1,
                ))
                if state.run_id == run_id and stop_after == "permission":
                    raise asyncio.CancelledError()
                external_calls.append(state.run_id)
                if state.run_id == run_id and stop_after == "external":
                    raise asyncio.CancelledError()
                ref = await node.artifact_repository.put(ArtifactWriteRequest(
                    operation_id=operation_id,
                    artifact_type="test.sample_artifact",
                    schema_version="1.0",
                    content={
                        "label": "recovery evidence",
                        "count": 10,
                        "ratio": 2.5,
                        "occurred_on": "2026-08-11",
                        "observed_at": "2026-08-11T09:30:00Z",
                        "note": "deterministic recovery",
                    },
                ))
                if state.run_id == run_id and stop_after == "artifact_confirmed":
                    raise asyncio.CancelledError()
            transition = make_confirmed_transition(state)
            return state.model_copy(update={
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
                "artifact_refs": (*state.artifact_refs, ref),
            })

        def finish(state: TravelSessionState) -> TravelSessionState:
            calls["finish_node"] += 1
            if state.run_id == run_id and stop_after == "post_node":
                raise DurableExecutionDependencyError()
            transition = make_confirmed_transition(state)
            return state.model_copy(update={
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph.add_node("requirements", requirements)
        graph.add_node("finish", finish)
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "finish")
        graph.add_edge("finish", END)
        return graph

    return build


async def _artifact_recovery_evidence(connection, run_id: UUID) -> dict:
    try:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """
                SELECT r.lifecycle, r.bootstrap_state, r.blocked_reason_code,
                       r.pause_generation,
                       r.latest_root_checkpoint_id AS checkpoint_id,
                       r.latest_root_checkpoint_digest AS checkpoint_digest,
                       r.journal_row_count, p.status AS pending_status,
                       p.command_bytes IS NOT NULL AS command_present,
                       p.pause_context_bytes IS NOT NULL AS context_present,
                       p.accepted_at IS NOT NULL AS accepted_present,
                       p.dispatch_started_at IS NOT NULL AS dispatch_present,
                       p.cleared_at IS NOT NULL AS cleared_present,
                       l.lease_owner IS NOT NULL AS lease_held,
                       l.expires_at > clock_timestamp() AS lease_active,
                       l.fencing_token AS fence,
                       (SELECT count(*) FROM durable_execution.checkpoints
                        WHERE thread_id = %s) AS checkpoint_count,
                        (SELECT count(*) FROM durable_execution.budget_operations
                         WHERE run_id = %s) AS operation_count,
                        (SELECT count(*) FROM artifact_store.artifacts
                         WHERE run_id = %s) AS artifact_count,
                        (SELECT active_run_count FROM durable_execution.capacity_state
                         WHERE singleton_id = 1) AS active_run_count,
                        (SELECT stored_run_count FROM durable_execution.capacity_state
                         WHERE singleton_id = 1) AS stored_run_count
                FROM durable_execution.runs r
                JOIN durable_execution.pending_commands p
                  ON p.run_id = r.run_id AND p.pause_generation = r.pause_generation
                JOIN durable_execution.run_leases l ON l.run_id = r.run_id
                WHERE r.run_id = %s
                """,
                (str(run_id), run_id, run_id, run_id),
            )
            run = await cursor.fetchone()
            assert run is not None
            await cursor.execute(
                """
                SELECT transition_ordinal, node_name, journal_state,
                       operation_phase, operation_logical_step, artifact_id,
                       reason_code, checkpoint_id
                FROM durable_execution.operation_journal
                WHERE run_id = %s ORDER BY transition_ordinal
                """,
                (run_id,),
            )
            journal = await cursor.fetchall()
        return {"run": dict(run), "journal": tuple(dict(row) for row in journal)}
    finally:
        await connection.rollback()


async def _run_blocked_artifact_case(
    *, settings, connection, stop_after: str, injected_response: str | None,
) -> None:
    run_id, trace_id = uuid4(), uuid4()
    operation_id = OperationId(
        run_id=run_id, phase=WorkflowPhase.REQUIREMENTS, logical_step="sample_result",
    )
    artifact_runtime = await create_artifact_persistence_runtime(
        settings=ArtifactPersistenceSettings(
            host=settings.host, port=settings.port, database=settings.database,
            user=settings.user, password=settings.password.get_secret_value(),
        ),
        registry=ArtifactSchemaRegistry((ArtifactSchemaRegistration(
            artifact_type="test.sample_artifact", schema_version="1.0",
            model=StrictSampleArtifact,
        ),)),
    )
    artifacts = _CountingArtifactRepository(artifact_runtime.repository)
    contract = make_recovery_contract(nodes=(
        ("requirements", NodeRecoveryClass.JOURNAL_GUARDED),
        ("finish", NodeRecoveryClass.REPLAY_SAFE),
    ))
    external_calls: list[UUID] = []
    before_calls = {"graph_invoke": 0, "requirements_node": 0, "finish_node": 0}
    recovery_calls = {"graph_invoke": 0, "requirements_node": 0, "finish_node": 0}
    first = None
    second = None
    try:
        first = await create_durable_execution_runtime(
            settings=settings, artifact_repository=artifacts, contract=contract,
            command_codec=FakeCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
            graph_factory=_guarded_graph_factory(
                run_id=run_id, stop_after=stop_after, calls=before_calls,
                external_calls=external_calls,
            ),
            limits=DEMO_DURABLE_EXECUTION_LIMITS,
        )
        with pytest.raises(DurableExecutionDependencyError):
            await asyncio.wait_for(
                first.start(run_id=run_id, trace_id=trace_id, command="artifact reconciliation"),
                timeout=30,
            )
        assert before_calls == {
            "graph_invoke": 1, "requirements_node": 1, "finish_node": 0,
        }
        expected_external = 0 if stop_after == "permission" else 1
        expected_artifacts = 0 if stop_after in {"permission", "external"} else 1
        assert external_calls == [run_id] * expected_external
        assert artifacts.put_calls == expected_artifacts

        before = await _artifact_recovery_evidence(connection, run_id)
        assert before["run"]["lifecycle"] == "open"
        assert before["run"]["pending_status"] == "dispatch_started"
        assert before["run"]["command_present"]
        assert before["run"]["accepted_present"] and before["run"]["dispatch_present"]
        assert before["run"]["bootstrap_state"] == "bootstrap_pending"
        assert before["run"]["checkpoint_id"] is None
        assert before["run"]["checkpoint_count"] > 0
        assert before["run"]["operation_count"] == 1
        assert before["run"]["artifact_count"] == expected_artifacts
        assert before["run"]["journal_row_count"] == 1 + expected_artifacts
        assert [row["journal_state"] for row in before["journal"]] == (
            ["execution_permitted"]
            if expected_artifacts == 0
            else ["execution_permitted", "artifact_confirmed"]
        )
        assert all(row["node_name"] == "requirements" for row in before["journal"])
        assert all(row["checkpoint_id"] is None for row in before["journal"])
        assert before["run"]["lease_held"] and before["run"]["lease_active"]
        if expected_artifacts:
            assert before["journal"][-1]["artifact_id"] is not None
        else:
            assert before["journal"][-1]["artifact_id"] is None

        # 同进程受控中断仅模拟 dispatch 中断；到期后由新 Runtime 公开 recover。
        await _expire_lease(first, run_id)
        await first.close()
        expired = await _artifact_recovery_evidence(connection, run_id)
        assert expired["run"]["lease_held"] and not expired["run"]["lease_active"]
        assert expired["run"]["fence"] == before["run"]["fence"]

        actual = await artifact_runtime.repository.lookup(operation_id=operation_id)
        injected = None
        recovery_artifacts = artifacts
        if injected_response is not None:
            assert isinstance(actual, ArtifactFound)
            if injected_response == "absent":
                response = ArtifactAbsent(operation_id=operation_id)
            else:
                assert injected_response == "ref_mismatch"
                response = ArtifactFound(ref=ArtifactRef.model_validate(
                    {**actual.ref.model_dump(mode="python"), "artifact_id": uuid4()},
                    strict=True,
                ))
            injected = _InjectedLookupRepository(artifacts, operation_id, response)
            recovery_artifacts = injected
        else:
            assert isinstance(actual, ArtifactAbsent)

        second = await create_durable_execution_runtime(
            settings=settings, artifact_repository=recovery_artifacts, contract=contract,
            command_codec=FakeCommandCodec(), budget_plan_provider=FakeBudgetPlanProvider(),
            graph_factory=_guarded_graph_factory(
                run_id=run_id, stop_after=None, calls=recovery_calls,
                external_calls=external_calls,
            ),
            limits=DEMO_DURABLE_EXECUTION_LIMITS,
        )
        put_before_recover = artifacts.put_calls
        external_before_recover = len(external_calls)
        first_error = (
            OperationResultIndeterminateError
            if expected_artifacts == 0 else DurableExecutionStateError
        )
        with pytest.raises(first_error) as caught:
            await asyncio.wait_for(second.recover(run_id=run_id), timeout=30)
        assert type(caught.value) is first_error
        after = await _artifact_recovery_evidence(connection, run_id)
        assert after["run"]["lifecycle"] == "blocked"
        assert after["run"]["pending_status"] == "cleared"
        assert not after["run"]["command_present"]
        assert not after["run"]["context_present"]
        assert after["run"]["cleared_present"]
        assert after["run"]["accepted_present"] and after["run"]["dispatch_present"]
        assert not after["run"]["lease_held"]
        assert after["run"]["fence"] == before["run"]["fence"]
        assert after["run"]["bootstrap_state"] == "bootstrap_checkpointed"
        assert type(after["run"]["checkpoint_id"]) is str
        assert after["run"]["checkpoint_id"]
        assert after["run"]["checkpoint_digest"].startswith("sha256:")
        assert after["run"]["checkpoint_count"] == before["run"]["checkpoint_count"]
        assert after["run"]["operation_count"] == before["run"]["operation_count"]
        assert after["run"]["artifact_count"] == before["run"]["artifact_count"]
        assert after["run"]["active_run_count"] == before["run"]["active_run_count"] - 1
        assert after["run"]["stored_run_count"] == before["run"]["stored_run_count"]
        if expected_artifacts == 0:
            assert after["run"]["blocked_reason_code"] == "operation_indeterminate"
            assert [row["journal_state"] for row in after["journal"]] == [
                "execution_permitted", "indeterminate",
            ]
            assert after["journal"][-1]["reason_code"] == "lease_lost_after_permission"
            assert after["journal"][-1]["artifact_id"] is None
            assert after["run"]["journal_row_count"] == 2
        else:
            assert after["run"]["blocked_reason_code"] == "artifact_invalid"
            assert after["journal"] == before["journal"]
            assert after["run"]["journal_row_count"] == 2
            assert injected is not None and injected.injected_lookups == 1
        assert recovery_calls == {
            "graph_invoke": 0, "requirements_node": 0, "finish_node": 0,
        }
        assert len(external_calls) == external_before_recover
        assert artifacts.put_calls == put_before_recover
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
        assert await _artifact_recovery_evidence(connection, run_id) == after
        assert recovery_calls == {
            "graph_invoke": 0, "requirements_node": 0, "finish_node": 0,
        }
        assert len(external_calls) == external_before_recover
        assert artifacts.put_calls == put_before_recover
        if injected is not None:
            assert injected.injected_lookups == 1
        print("ARTIFACT_RECOVERY_EVIDENCE " + json.dumps({
            "case": stop_after if injected_response is None else injected_response,
            "run_id": str(run_id), "before": before, "expired": expired,
            "after": after, "graph_before": before_calls,
            "graph_recovery": recovery_calls, "domain_calls": len(external_calls),
            "artifact_put_calls": artifacts.put_calls,
            "injected_response": injected_response,
            "actual_m10_artifact_found": isinstance(actual, ArtifactFound),
        }, ensure_ascii=False, sort_keys=True, default=str))
    finally:
        if second is not None:
            await second.close()
        if first is not None:
            await first.close()
        await artifact_runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_after", ("permission", "external"))
async def test_permission_without_artifact_blocks_without_recalling_external_service(
    durable_execution_settings, db_connection, stop_after,
) -> None:
    await _run_blocked_artifact_case(
        settings=durable_execution_settings, connection=db_connection,
        stop_after=stop_after, injected_response=None,
    )


@pytest.mark.asyncio
async def test_confirmed_artifact_missing_response_blocks_without_reexecution(
    durable_execution_settings, db_connection,
) -> None:
    await _run_blocked_artifact_case(
        settings=durable_execution_settings, connection=db_connection,
        stop_after="artifact_confirmed", injected_response="absent",
    )


@pytest.mark.asyncio
async def test_confirmed_artifact_ref_mismatch_blocks_without_reexecution(
    durable_execution_settings, db_connection,
) -> None:
    await _run_blocked_artifact_case(
        settings=durable_execution_settings, connection=db_connection,
        stop_after="artifact_confirmed", injected_response="ref_mismatch",
    )


@pytest.mark.asyncio
async def test_post_node_checkpoint_repair_propagates_artifact_failure_once(
    durable_execution_settings, db_connection,
) -> None:
    run_id, trace_id = uuid4(), uuid4()
    operation_id = OperationId(
        run_id=run_id, phase=WorkflowPhase.REQUIREMENTS, logical_step="sample_result",
    )
    artifact_runtime = await create_artifact_persistence_runtime(
        settings=ArtifactPersistenceSettings(
            host=durable_execution_settings.host, port=durable_execution_settings.port,
            database=durable_execution_settings.database, user=durable_execution_settings.user,
            password=durable_execution_settings.password.get_secret_value(),
        ),
        registry=ArtifactSchemaRegistry((ArtifactSchemaRegistration(
            artifact_type="test.sample_artifact", schema_version="1.0",
            model=StrictSampleArtifact,
        ),)),
    )
    artifacts = _CountingArtifactRepository(artifact_runtime.repository)
    contract = make_recovery_contract(nodes=(
        ("requirements", NodeRecoveryClass.JOURNAL_GUARDED),
        ("finish", NodeRecoveryClass.REPLAY_SAFE),
    ))
    external_calls: list[UUID] = []
    before_calls = {"graph_invoke": 0, "requirements_node": 0, "finish_node": 0}
    recovery_calls = {"graph_invoke": 0, "requirements_node": 0, "finish_node": 0}
    first = None
    second = None
    try:
        first = await create_durable_execution_runtime(
            settings=durable_execution_settings, artifact_repository=artifacts,
            contract=contract, command_codec=FakeCommandCodec(),
            budget_plan_provider=FakeBudgetPlanProvider(),
            graph_factory=_guarded_graph_factory(
                run_id=run_id, stop_after="post_node", calls=before_calls,
                external_calls=external_calls,
            ), limits=DEMO_DURABLE_EXECUTION_LIMITS,
        )
        with pytest.raises(DurableExecutionDependencyError):
            await asyncio.wait_for(
                first.start(run_id=run_id, trace_id=trace_id, command="post-node repair"), timeout=30,
            )
        before = await _artifact_recovery_evidence(db_connection, run_id)
        assert before["run"]["lifecycle"] == "open"
        assert before["run"]["pending_status"] == "dispatch_started"
        assert before["run"]["checkpoint_id"] is None
        assert [row["journal_state"] for row in before["journal"]] == [
            "execution_permitted", "artifact_confirmed",
        ]
        assert before_calls == {"graph_invoke": 1, "requirements_node": 1, "finish_node": 1}
        assert external_calls == [run_id]
        assert artifacts.put_calls == 1
        assert isinstance(await artifact_runtime.repository.lookup(operation_id=operation_id), ArtifactFound)
        await _expire_lease(first, run_id)
        await first.close()

        injected = _InjectedLookupRepository(
            artifacts, operation_id, ArtifactAbsent(operation_id=operation_id),
        )
        second = await create_durable_execution_runtime(
            settings=durable_execution_settings, artifact_repository=injected,
            contract=contract, command_codec=FakeCommandCodec(),
            budget_plan_provider=FakeBudgetPlanProvider(),
            graph_factory=_guarded_graph_factory(
                run_id=run_id, stop_after=None, calls=recovery_calls,
                external_calls=external_calls,
            ), limits=DEMO_DURABLE_EXECUTION_LIMITS,
        )
        with pytest.raises(DurableExecutionStateError) as caught:
            await asyncio.wait_for(second.recover(run_id=run_id), timeout=30)
        assert type(caught.value) is DurableExecutionStateError
        after = await _artifact_recovery_evidence(db_connection, run_id)
        assert after["run"]["lifecycle"] == "blocked"
        assert after["run"]["blocked_reason_code"] == "artifact_invalid"
        assert after["run"]["pending_status"] == "cleared"
        assert not after["run"]["command_present"]
        assert after["run"]["fence"] == before["run"]["fence"]
        assert after["run"]["journal_row_count"] == before["run"]["journal_row_count"]
        assert after["journal"] == before["journal"]
        assert after["run"]["active_run_count"] == before["run"]["active_run_count"] - 1
        assert after["run"]["stored_run_count"] == before["run"]["stored_run_count"]
        assert recovery_calls == {"graph_invoke": 0, "requirements_node": 0, "finish_node": 0}
        assert external_calls == [run_id] and artifacts.put_calls == 1
        assert injected.injected_lookups == 1
        for call in (
            lambda: second.recover(run_id=run_id),
            lambda: second.get_state(run_id=run_id),
            lambda: second.resume(run_id=run_id, pause_token="blocked-run", command="blocked-run"),
        ):
            with pytest.raises(DurableExecutionRunBlockedError) as blocked:
                await asyncio.wait_for(call(), timeout=30)
            assert type(blocked.value) is DurableExecutionRunBlockedError
        assert await _artifact_recovery_evidence(db_connection, run_id) == after
        assert recovery_calls == {"graph_invoke": 0, "requirements_node": 0, "finish_node": 0}
        assert external_calls == [run_id] and artifacts.put_calls == 1
        assert injected.injected_lookups == 1
    finally:
        if second is not None:
            await second.close()
        if first is not None:
            await first.close()
        await artifact_runtime.close()


@pytest.mark.asyncio
async def test_dispatch_crash_repairs_post_node_checkpoint_and_runs_only_the_next_node(
    durable_execution_settings,
    monkeypatch,
) -> None:
    safe_calls = 0
    finish_attempts = 0
    finish_effects = 0
    finish_entered = asyncio.Event()
    finish_release = asyncio.Event()
    repair_committed = asyncio.Event()

    def build(_capabilities):
        graph = StateGraph(TravelSessionState)

        def safe(state: TravelSessionState) -> TravelSessionState:
            nonlocal safe_calls
            safe_calls += 1
            return state

        async def finish(state: TravelSessionState) -> TravelSessionState:
            nonlocal finish_attempts, finish_effects
            finish_attempts += 1
            if finish_attempts == 2:
                assert repair_committed.is_set()
            finish_entered.set()
            await finish_release.wait()
            finish_effects += 1
            transition = make_confirmed_transition(state)
            return state.model_copy(update={
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph.add_node("safe", safe)
        graph.add_node("finish", finish)
        graph.add_edge(START, "safe")
        graph.add_edge("safe", "finish")
        graph.add_edge("finish", END)
        return graph

    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=make_recovery_contract(),
        command_codec=FakeCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=build,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    run_id, trace_id = uuid4(), uuid4()
    original_repair = DurableExecutionRepository.repair_dispatch_checkpoint

    async def observe_repair_commit(repository, **values):
        await original_repair(repository, **values)
        assert safe_calls == 1
        assert finish_attempts == 1
        assert not bool((await runtime._repository.get_lease_state(run_id=run_id))["active"])
        repair_committed.set()

    monkeypatch.setattr(DurableExecutionRepository, "repair_dispatch_checkpoint", observe_repair_commit)
    start_task = asyncio.create_task(
        runtime.start(run_id=run_id, trace_id=trace_id, command="演示")
    )
    try:
        await finish_entered.wait()
        with pytest.raises(DurableExecutionRunBusyError):
            await runtime.recover(run_id=run_id)
        start_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start_task

        pending = await runtime._repository.get_pending_command(run_id=run_id)
        assert pending is not None
        assert pending["status"] == "dispatch_started"
        assert safe_calls == 1
        assert finish_effects == 0

        finish_release.set()
        await _expire_lease(runtime, run_id)
        terminal = await runtime.recover(run_id=run_id)

        assert type(terminal) is DurableExecutionTerminal
        assert terminal.state.delivery_status is DeliveryStatus.DEMO_READY
        assert safe_calls == 1
        assert finish_attempts == 2
        assert finish_effects == 1
        closed_pending = await runtime._repository.get_pending_command(run_id=run_id)
        assert closed_pending is not None
        assert closed_pending["status"] == "cleared"
    finally:
        finish_release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_terminal_checkpoint_crash_gap_closes_idempotently_without_execution(
    durable_execution_settings,
    monkeypatch,
) -> None:
    node_calls = 0
    provider = FakeBudgetPlanProvider()
    artifacts = FakeArtifactRepository()

    def build(_capabilities):
        graph = StateGraph(TravelSessionState)

        def safe(state: TravelSessionState) -> TravelSessionState:
            return state

        async def finish(state: TravelSessionState) -> TravelSessionState:
            nonlocal node_calls
            node_calls += 1
            transition = make_confirmed_transition(state)
            return state.model_copy(update={
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph.add_node("safe", safe)
        graph.add_node("finish", finish)
        graph.add_edge(START, "safe")
        graph.add_edge("safe", "finish")
        graph.add_edge("finish", END)
        return graph

    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=artifacts,
        contract=make_recovery_contract(nodes=(
            ("safe", NodeRecoveryClass.REPLAY_SAFE),
            ("finish", NodeRecoveryClass.REPLAY_SAFE),
        )),
        command_codec=FakeCommandCodec(),
        budget_plan_provider=provider,
        graph_factory=build,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    run_id, trace_id = uuid4(), uuid4()
    def crash_after_checkpoint(_runtime, **kwargs):
        raise asyncio.CancelledError()

    async def active_run_count() -> int:
        async with runtime._repository._pool.connection() as connection:
            async with connection.transaction():
                row = await (await connection.execute(
                    "SELECT active_run_count FROM durable_execution.capacity_state WHERE singleton_id = 1"
                )).fetchone()
                return int(row[0])

    from app.durable_execution.runtime import DurableExecutionRuntime

    monkeypatch.setattr(DurableExecutionRuntime, "_resolve_graph_result", crash_after_checkpoint)
    try:
        with pytest.raises(asyncio.CancelledError):
            await runtime.start(run_id=run_id, trace_id=trace_id, command="终态修复")
        assert node_calls == 1
        assert (await runtime._repository.get_run_row(run_id=run_id))["lifecycle"] == "open"
        active_before_repair = await active_run_count()
        provider_calls = len(provider.start_calls)

        await _expire_lease(runtime, run_id)
        terminal = await runtime.recover(run_id=run_id)
        assert type(terminal) is DurableExecutionTerminal
        assert terminal.state.delivery_status is DeliveryStatus.DEMO_READY
        assert (await runtime._repository.get_run_row(run_id=run_id))["lifecycle"] == "closed"
        assert await active_run_count() == active_before_repair - 1
        assert node_calls == 1
        assert len(provider.start_calls) == provider_calls
        assert artifacts.put_calls == []
        assert artifacts.lookup_calls == []

        repeated = await runtime.recover(run_id=run_id)
        assert type(repeated) is DurableExecutionTerminal
        assert repeated.state == terminal.state
        assert node_calls == 1
        assert await active_run_count() == active_before_repair - 1
        assert len(provider.start_calls) == provider_calls
        assert artifacts.put_calls == []
    finally:
        await runtime.close()
