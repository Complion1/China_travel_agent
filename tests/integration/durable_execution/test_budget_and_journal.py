from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from langgraph.graph import END, START, StateGraph
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app.durable_execution import (
    DurableExecutionPaused,
    DurableExecutionTerminal,
    create_durable_execution_runtime,
)
from app.durable_execution.errors import (
    DurableExecutionDependencyError,
    DurableExecutionRunBlockedError,
    DurableExecutionStateError,
    DurableExecutionUsageError,
)
from app.durable_execution.models import (
    DEMO_DURABLE_EXECUTION_LIMITS,
    DurableExecutionContract,
    DurableExecutionNodeRecoveryRule,
    NodeRecoveryClass,
)
from app.durable_execution.repository import (
    DurableExecutionRepository,
    _BlockCause,
    _BlockedReason,
    _BlockFacts,
    _CommitUnknown,
)
from app.durable_execution.settings import DurableExecutionSettings
from app.requirement_subgraph import (
    RequirementConfirmationResume,
    create_requirement_subgraph_embedded_driver,
)
from app.requirements import RequirementConfirmation
from app.workflow_contracts import (
    BudgetConsumeRequest,
    DeliveryStatus,
    ExecutionBudgetCapacityExceededError,
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    OperationId,
    TravelSessionState,
    WorkflowPhase,
)
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
    make_contract,
)


async def _pool(settings: DurableExecutionSettings) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        conninfo="",
        kwargs={
            "host": settings.host,
            "port": settings.port,
            "dbname": settings.database,
            "user": settings.user,
            "password": settings.password.get_secret_value(),
            "autocommit": False,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=durable_execution,pg_catalog -c timezone=UTC",
        },
        min_size=1,
        max_size=4,
        open=False,
    )
    await pool.open(wait=True)
    return pool


def _plan(run_id: UUID, *, limit: int = 1) -> ExecutionBudgetPlan:
    return ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=run_id,
        limits=(
            ExecutionBudgetLimit(
                key=ExecutionBudgetKey(
                    scope=ExecutionBudgetScope.PHASE,
                    name="requirements.atomic",
                    phase=WorkflowPhase.REQUIREMENTS,
                ),
                limit=limit,
            ),
        ),
    )


@pytest_asyncio.fixture
async def repo(durable_execution_settings: DurableExecutionSettings):
    pool = await _pool(durable_execution_settings)
    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    await repository.initialize_capacity()
    try:
        yield repository
    finally:
        await pool.close()


async def _run(repo: DurableExecutionRepository):
    run_id, owner = uuid4(), uuid4()
    await repo.create_run(
        run_id=run_id,
        trace_id=uuid4(),
        contract=make_contract(),
        start_plan=_plan(run_id),
    )
    token = await repo.acquire_lease(run_id=run_id, owner=owner)
    manager = repo._budget_manager(run_id=run_id, owner=owner, fencing_token=token)
    key = _plan(run_id).limits[0].key
    return run_id, owner, token, manager, key


async def _cleanup_run(pool: AsyncConnectionPool, run_id: UUID) -> None:
    async with pool.connection() as connection:
        async with connection.transaction():
            for table in (
                "operation_journal",
                "budget_operations",
                "budget_limits",
                "pending_commands",
                "run_leases",
                "runs",
            ):
                await connection.execute(
                    f"DELETE FROM durable_execution.{table} WHERE run_id = %s",
                    (run_id,),
                )
            await connection.execute(
                """
                UPDATE durable_execution.capacity_state
                SET stored_run_count = (SELECT count(*) FROM durable_execution.runs),
                    active_run_count = (SELECT count(*) FROM durable_execution.runs WHERE lifecycle = 'open')
                WHERE singleton_id = 1
                """
            )


async def _terminate_fault_connection_backend(
    pool: AsyncConnectionPool,
    repository: DurableExecutionRepository,
) -> None:
    connection = repository._fault_connection
    assert connection is not None
    assert connection.closed is False
    backend_pid = connection.info.backend_pid
    async with pool.connection() as terminator:
        async with terminator.transaction():
            cursor = await terminator.execute(
                "SELECT pg_terminate_backend(%s) AS terminated",
                (backend_pid,),
            )
            assert (await cursor.fetchone())["terminated"] is True
    # 客户端尚未执行 COMMIT，故此处仍不是 connection.closed 快捷分支；
    # 下一步 transaction.__aexit__ 的真实 COMMIT 才观察到服务端断开。
    assert connection.closed is False


@pytest.mark.asyncio
async def test_budget_operation_and_journal_are_atomic_and_idempotent(repo: DurableExecutionRepository) -> None:
    run_id, owner, token, manager, key = await _run(repo)
    request = BudgetConsumeRequest(
        operation_id=OperationId(
            run_id=run_id,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="atomic_step",
        ),
        key=key,
        amount=1,
    )
    applied = await manager.consume(request)
    replayed = await manager.consume(request)
    assert applied.outcome_type == "consumed"
    assert applied.disposition == "applied"
    assert replayed.disposition == "replayed"
    with pytest.raises(Exception):
        await repo.append_journal_transition(
            run_id=run_id,
            operation_phase="requirements",
            logical_step="atomic_step",
            node_name="requirements",
            journal_state="completed",
            owner=owner,
            fencing_token=token,
            checkpoint=("", "cp-1", "sha256:" + "1" * 64),
        )

    async with repo._pool.connection() as connection:
        async with connection.transaction():
            cursor = await connection.execute(
                """
                SELECT count(*) AS rows
                FROM durable_execution.operation_journal
                WHERE run_id = %s AND operation_logical_step = 'atomic_step'
                """,
                (run_id,),
            )
            assert (await cursor.fetchone())["rows"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ("committed", "absent", "partial", "conflict"))
async def test_commit_unknown_does_fresh_read_then_one_retry_only(
    durable_execution_settings: DurableExecutionSettings,
    mode: str,
) -> None:
    pool = await _pool(durable_execution_settings)
    mutated = False
    points: list[str] = []
    run_id: UUID | None = None

    async def fault(point: str) -> None:
        nonlocal mutated
        points.append(point)
        if point != "budget_commit_unknown" or mutated:
            return
        mutated = True
        async with pool.connection() as connection:
            async with connection.transaction():
                if mode == "absent":
                    await connection.execute(
                        "DELETE FROM durable_execution.operation_journal WHERE run_id = %s AND operation_logical_step = %s",
                        (run_id, "unknown_commit"),
                    )
                    await connection.execute(
                        "DELETE FROM durable_execution.budget_operations WHERE run_id = %s AND operation_logical_step = %s",
                        (run_id, "unknown_commit"),
                    )
                    await connection.execute(
                        "UPDATE durable_execution.runs SET managed_operation_count = 0, journal_row_count = 0 WHERE run_id = %s",
                        (run_id,),
                    )
                    await connection.execute(
                        "UPDATE durable_execution.budget_limits SET consumed_value = 0 WHERE run_id = %s",
                        (run_id,),
                    )
                elif mode == "partial":
                    await connection.execute(
                        "DELETE FROM durable_execution.operation_journal WHERE run_id = %s AND operation_logical_step = %s",
                        (run_id, "unknown_commit"),
                    )
                elif mode == "conflict":
                    await connection.execute(
                        "UPDATE durable_execution.budget_operations SET amount = amount + 1 WHERE run_id = %s AND operation_logical_step = %s",
                        (run_id, "unknown_commit"),
                    )
            raise _CommitUnknown("committed")

    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        fault_hook=fault,
    )
    await repository.initialize_capacity()
    try:
        run_id, owner = uuid4(), uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        manager = repository._budget_manager(run_id=run_id, owner=owner, fencing_token=token)
        key = _plan(run_id).limits[0].key
        request = BudgetConsumeRequest(
            operation_id=OperationId(
                run_id=run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="unknown_commit",
            ),
            key=key,
            amount=1,
        )
        if mode in {"partial", "conflict"}:
            with pytest.raises(DurableExecutionStateError):
                await manager.consume(request)
        else:
            outcome = await manager.consume(request)
            assert outcome.disposition == "applied"
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT count(*) AS operation_rows,
                           count(*) FILTER (WHERE j.journal_state = 'execution_permitted') AS journal_rows,
                           r.managed_operation_count, r.journal_row_count,
                           l.limit_value, l.consumed_value,
                           max(b.amount) AS amount
                    FROM durable_execution.budget_operations AS b
                    JOIN durable_execution.runs AS r USING (run_id)
                    JOIN durable_execution.budget_limits AS l USING (run_id)
                    LEFT JOIN durable_execution.operation_journal AS j
                      ON j.run_id = b.run_id
                     AND j.operation_phase = b.operation_phase
                     AND j.operation_logical_step = b.operation_logical_step
                    WHERE b.run_id = %s AND b.operation_logical_step = 'unknown_commit'
                    GROUP BY r.managed_operation_count, r.journal_row_count,
                             l.limit_value, l.consumed_value
                    """,
                    (run_id,),
                )
                row = await cursor.fetchone()
                assert row["operation_rows"] == 1
                assert row["journal_rows"] == (0 if mode == "partial" else 1)
                assert row["managed_operation_count"] == 1
                assert row["journal_row_count"] == 1
                assert row["limit_value"] == 1
                assert row["consumed_value"] == 1
                if mode == "conflict":
                    assert row["amount"] == 2
        write_points = [point for point in points if point in {"budget_commit_unknown", "budget_retry_commit_unknown"}]
        assert len(write_points) == (2 if mode == "absent" else 1)
    finally:
        if run_id is not None:
            await _cleanup_run(pool, run_id)
        await pool.close()


@pytest.mark.asyncio
async def test_budget_commit_unknown_refuses_retry_when_precondition_changed(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    points: list[str] = []
    run_id: UUID | None = None

    async def fault(point: str) -> None:
        points.append(point)
        if point != "budget_commit_unknown":
            return
        assert run_id is not None
        async with pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "DELETE FROM durable_execution.operation_journal WHERE run_id = %s AND operation_logical_step = %s",
                    (run_id, "precondition_changed"),
                )
                await connection.execute(
                    "DELETE FROM durable_execution.budget_operations WHERE run_id = %s AND operation_logical_step = %s",
                    (run_id, "precondition_changed"),
                )
                await connection.execute(
                    "UPDATE durable_execution.runs SET managed_operation_count = 0, journal_row_count = 0 WHERE run_id = %s",
                    (run_id,),
                )
                # 目标行已消失，但 consumed ledger 不再等于首次写入前的 intent。
                await connection.execute(
                    "UPDATE durable_execution.budget_limits SET consumed_value = 1 WHERE run_id = %s",
                    (run_id,),
                )
        raise _CommitUnknown("committed")

    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        fault_hook=fault,
    )
    await repository.initialize_capacity()
    try:
        run_id, owner = uuid4(), uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        request = BudgetConsumeRequest(
            operation_id=OperationId(
                run_id=run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="precondition_changed",
            ),
            key=_plan(run_id).limits[0].key,
            amount=1,
        )
        with pytest.raises(DurableExecutionStateError):
            await repository._budget_manager(
                run_id=run_id,
                owner=owner,
                fencing_token=token,
            ).consume(request)
        assert points.count("budget_commit_unknown") == 1
        assert points.count("budget_retry_commit_exception") == 0
        assert points.count("budget_retry_commit_unknown") == 0
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT r.lifecycle, r.managed_operation_count, r.journal_row_count,
                           l.limit_value, l.consumed_value,
                           (SELECT count(*) FROM durable_execution.budget_operations
                            WHERE run_id = r.run_id AND operation_logical_step = 'precondition_changed') AS operations,
                           (SELECT count(*) FROM durable_execution.operation_journal
                            WHERE run_id = r.run_id AND operation_logical_step = 'precondition_changed') AS journals
                    FROM durable_execution.runs AS r
                    JOIN durable_execution.budget_limits AS l USING (run_id)
                    WHERE r.run_id = %s
                    """,
                    (run_id,),
                )
                row = await cursor.fetchone()
        assert row == {
            "lifecycle": "open",
            "managed_operation_count": 0,
            "journal_row_count": 0,
            "limit_value": 1,
            "consumed_value": 1,
            "operations": 0,
            "journals": 0,
        }
    finally:
        if run_id is not None:
            await _cleanup_run(pool, run_id)
        await pool.close()


@pytest.mark.asyncio
async def test_budget_second_commit_unknown_uses_final_read_without_third_write(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    points: list[str] = []
    run_id: UUID | None = None

    async def fault(point: str) -> None:
        points.append(point)
        if point == "budget_commit_unknown":
            assert run_id is not None
            async with pool.connection() as connection:
                async with connection.transaction():
                    await connection.execute(
                        "DELETE FROM durable_execution.operation_journal WHERE run_id = %s AND operation_logical_step = %s",
                        (run_id, "second_unknown"),
                    )
                    await connection.execute(
                        "DELETE FROM durable_execution.budget_operations WHERE run_id = %s AND operation_logical_step = %s",
                        (run_id, "second_unknown"),
                    )
                    await connection.execute(
                        "UPDATE durable_execution.runs SET managed_operation_count = 0, journal_row_count = 0 WHERE run_id = %s",
                        (run_id,),
                    )
                    await connection.execute(
                        "UPDATE durable_execution.budget_limits SET consumed_value = 0 WHERE run_id = %s",
                        (run_id,),
                    )
            raise _CommitUnknown("absent")
        if point == "budget_retry_commit_unknown":
            raise _CommitUnknown("unknown")

    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        fault_hook=fault,
    )
    await repository.initialize_capacity()
    try:
        run_id, owner = uuid4(), uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        key = _plan(run_id).limits[0].key
        request = BudgetConsumeRequest(
            operation_id=OperationId(
                run_id=run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="second_unknown",
            ),
            key=key,
            amount=1,
        )
        outcome = await repository._budget_manager(
            run_id=run_id,
            owner=owner,
            fencing_token=token,
        ).consume(request)
        assert outcome.disposition == "applied"
        assert points.count("budget_commit_unknown") == 1
        assert points.count("budget_retry_commit_unknown") == 1
        assert points.count("budget_commit_exception") == 1
        assert points.count("budget_retry_commit_exception") == 1
        assert not any(point == "budget_retry_retry_commit_unknown" for point in points)
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT count(*) AS n FROM durable_execution.budget_operations WHERE run_id = %s",
                    (run_id,),
                )
                assert (await cursor.fetchone())["n"] == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_block_adds_indeterminate_and_reconciles_commit_unknown(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    points: list[str] = []
    run_id: UUID | None = None

    async def fault(point: str) -> None:
        points.append(point)
        if point == "block_commit_unknown":
            assert run_id is not None
            async with pool.connection() as connection:
                async with connection.transaction():
                    await connection.execute(
                        "DELETE FROM durable_execution.operation_journal WHERE run_id = %s AND journal_state = 'indeterminate'",
                        (run_id,),
                    )
                    await connection.execute(
                        "UPDATE durable_execution.runs SET lifecycle = 'open', blocked_reason_code = NULL, finalized_at = NULL, journal_row_count = 1 WHERE run_id = %s",
                        (run_id,),
                    )
                    await connection.execute(
                        "UPDATE durable_execution.run_leases SET lease_owner = %s, fencing_token = %s, expires_at = clock_timestamp() + interval '60 seconds', renewed_at = clock_timestamp() WHERE run_id = %s",
                        (owner, token, run_id),
                    )
                    await connection.execute(
                        """
                        UPDATE durable_execution.capacity_state
                        SET active_run_count = (
                            SELECT count(*) FROM durable_execution.runs WHERE lifecycle = 'open'
                        )
                        WHERE singleton_id = 1
                        """,
                    )
            raise _CommitUnknown("absent")
        if point == "block_retry_commit_unknown":
            raise _CommitUnknown("unknown")

    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        fault_hook=fault,
    )
    await repository.initialize_capacity()
    try:
        run_id, owner = uuid4(), uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        operation = BudgetConsumeRequest(
            operation_id=OperationId(
                run_id=run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="block_step",
            ),
            key=_plan(run_id).limits[0].key,
            amount=1,
        )
        assert (await repository._budget_manager(run_id=run_id, owner=owner, fencing_token=token).consume(operation)).disposition == "applied"
        points.clear()
        await repository.block_run(
            run_id=run_id,
            facts=_BlockFacts(
                cancelled_after_permission=True,
                operation_id=operation.operation_id,
            ),
            owner=owner,
            fencing_token=token,
        )
        row = await repository.get_run_row(run_id=run_id)
        assert row["lifecycle"] == "blocked"
        assert row["blocked_reason_code"] == "operation_indeterminate"
        assert row["journal_row_count"] == 2
        assert points.count("block_before_commit") == 1
        assert points.count("block_commit_exception") == 1
        assert points.count("block_retry_commit_exception") == 1
        assert points.count("block_commit_unknown") == 1
        assert points.count("block_retry_commit_unknown") == 1
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT journal_state, reason_code, transition_ordinal
                    FROM durable_execution.operation_journal
                    WHERE run_id = %s
                    ORDER BY transition_ordinal
                    """,
                    (run_id,),
                )
                rows = await cursor.fetchall()
                assert [(row["journal_state"], row["reason_code"], row["transition_ordinal"]) for row in rows] == [
                    ("execution_permitted", None, 1),
                    ("indeterminate", "cancelled_after_permission", 2),
                ]
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ("committed", "partial", "conflict", "precondition_changed"))
async def test_block_commit_unknown_uses_authoritative_database_state(
    durable_execution_settings: DurableExecutionSettings,
    mode: str,
) -> None:
    pool = await _pool(durable_execution_settings)
    points: list[str] = []
    run_id: UUID | None = None
    mutated = False
    repository: DurableExecutionRepository

    async def fault(point: str) -> None:
        nonlocal mutated
        points.append(point)
        if point != "block_commit_unknown" or mutated:
            return
        mutated = True
        assert run_id is not None
        if mode != "committed":
            async with pool.connection() as connection:
                async with connection.transaction():
                    if mode == "partial":
                        await connection.execute(
                            "DELETE FROM durable_execution.operation_journal WHERE run_id = %s AND journal_state = 'indeterminate'",
                            (run_id,),
                        )
                    elif mode == "conflict":
                        await connection.execute(
                            "UPDATE durable_execution.runs SET blocked_reason_code = 'state_invalid' WHERE run_id = %s",
                            (run_id,),
                        )
                    else:
                        assert repository._fault_connection is not None
                        await repository._fault_connection.execute(
                            "UPDATE durable_execution.capacity_state SET active_run_count = active_run_count + 1 WHERE singleton_id = 1",
                        )
                        await repository._fault_connection.commit()
        raise _CommitUnknown("committed")

    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        fault_hook=fault,
    )
    await repository.initialize_capacity()
    try:
        run_id, owner = uuid4(), uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        operation = BudgetConsumeRequest(
            operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.REQUIREMENTS, logical_step=f"block_{mode}"),
            key=_plan(run_id).limits[0].key,
            amount=1,
        )
        assert (await repository._budget_manager(run_id=run_id, owner=owner, fencing_token=token).consume(operation)).disposition == "applied"
        facts = _BlockFacts(cancelled_after_permission=True, operation_id=operation.operation_id)
        if mode == "committed":
            await repository.block_run(run_id=run_id, facts=facts, owner=owner, fencing_token=token)
            assert (await repository.get_run_row(run_id=run_id))["lifecycle"] == "blocked"
        else:
            with pytest.raises(DurableExecutionStateError):
                await repository.block_run(run_id=run_id, facts=facts, owner=owner, fencing_token=token)
            row = await repository.get_run_row(run_id=run_id)
            if mode == "partial":
                assert row["lifecycle"] == "blocked"
                assert row["journal_row_count"] == 2
                async with pool.connection() as connection:
                    async with connection.transaction():
                        cursor = await connection.execute(
                            "SELECT count(*) AS n FROM durable_execution.operation_journal WHERE run_id = %s AND journal_state = 'indeterminate'",
                            (run_id,),
                        )
                        assert (await cursor.fetchone())["n"] == 0
            elif mode == "conflict":
                assert row["lifecycle"] == "blocked"
                assert row["blocked_reason_code"] == "state_invalid"
            else:
                assert row["lifecycle"] == "blocked"
                async with pool.connection() as connection:
                    async with connection.transaction():
                        cursor = await connection.execute(
                            "SELECT active_run_count, (SELECT count(*) FROM durable_execution.runs WHERE lifecycle = 'open') AS open_count FROM durable_execution.capacity_state WHERE singleton_id = 1",
                        )
                        state = await cursor.fetchone()
                        assert state["active_run_count"] > state["open_count"]
        assert points.count("block_commit_unknown") == 1
        assert points.count("block_retry_commit_unknown") == 0
    finally:
        if run_id is not None:
            await _cleanup_run(pool, run_id)
        await pool.close()


@pytest.mark.asyncio
async def test_budget_real_commit_exception_is_reconciled_by_fresh_read(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    points: list[str] = []
    fired = False
    repository: DurableExecutionRepository

    async def fault(point: str) -> None:
        nonlocal fired
        points.append(point)
        if point == "budget_commit_exception" and not fired:
            fired = True
            await _terminate_fault_connection_backend(pool, repository)

    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        fault_hook=fault,
    )
    await repository.initialize_capacity()
    try:
        run_id, owner = uuid4(), uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        key = _plan(run_id).limits[0].key
        request = BudgetConsumeRequest(
            operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.REQUIREMENTS, logical_step="real_commit_exception"),
            key=key,
            amount=1,
        )
        outcome = await repository._budget_manager(run_id=run_id, owner=owner, fencing_token=token).consume(request)
        assert outcome.disposition == "applied"
        assert points.count("budget_commit_exception") == 1
        assert points.count("budget_commit_exception_observed") == 1
        assert points.count("budget_retry_commit_exception") == 1
        assert points.count("budget_retry_commit_exception_observed") == 0
        assert points.count("budget_retry_commit_unknown") == 1
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT count(*) AS operations, (SELECT count(*) FROM durable_execution.operation_journal WHERE run_id = %s) AS journals FROM durable_execution.budget_operations WHERE run_id = %s",
                    (run_id, run_id),
                )
                row = await cursor.fetchone()
                assert row["operations"] == 1
                assert row["journals"] == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_block_real_commit_exception_is_reconciled_by_fresh_read(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    points: list[str] = []
    fired = False
    repository: DurableExecutionRepository

    async def fault(point: str) -> None:
        nonlocal fired
        points.append(point)
        if point == "block_commit_exception" and not fired:
            fired = True
            await _terminate_fault_connection_backend(pool, repository)

    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        fault_hook=fault,
    )
    await repository.initialize_capacity()
    try:
        run_id, owner = uuid4(), uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        operation = BudgetConsumeRequest(
            operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.REQUIREMENTS, logical_step="block_real_commit_exception"),
            key=_plan(run_id).limits[0].key,
            amount=1,
        )
        assert (await repository._budget_manager(run_id=run_id, owner=owner, fencing_token=token).consume(operation)).disposition == "applied"
        await repository.block_run(
            run_id=run_id,
            facts=_BlockFacts(cancelled_after_permission=True, operation_id=operation.operation_id),
            owner=owner,
            fencing_token=token,
        )
        assert (await repository.get_run_row(run_id=run_id))["lifecycle"] == "blocked", points
        assert points.count("block_commit_exception") == 1
        assert points.count("block_commit_exception_observed") == 1
        assert points.count("block_retry_commit_exception") == 1
        assert points.count("block_retry_commit_exception_observed") == 0
        assert points.count("block_retry_commit_unknown") == 1
    finally:
        await pool.close()


def test_operation_indeterminate_requires_an_exact_operation_id() -> None:
    with pytest.raises(DurableExecutionUsageError):
        _BlockFacts(
            cause=_BlockCause.operation_indeterminate(),
            external_result_unknown=True,
        )
    with pytest.raises(DurableExecutionUsageError):
        _BlockFacts(artifact_missing_after_permission=True)
    with pytest.raises(DurableExecutionUsageError):
        _BlockFacts(
            cause=_BlockCause.operation_indeterminate(),
            operation_id=OperationId(
                run_id=uuid4(),
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="missing_result_evidence",
            ),
        )


@pytest.mark.asyncio
async def test_unique_unrelated_permitted_operation_does_not_get_indeterminate(
    repo: DurableExecutionRepository,
) -> None:
    run_id, owner, token, manager, key = await _run(repo)
    operation = BudgetConsumeRequest(
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.REQUIREMENTS, logical_step="unrelated"),
        key=key,
        amount=1,
    )
    assert (await manager.consume(operation)).disposition == "applied"
    await repo.block_run(
        run_id=run_id,
        facts=_BlockFacts(cancelled_after_permission=True),
        owner=owner,
        fencing_token=token,
    )
    row = await repo.get_run_row(run_id=run_id)
    assert row["blocked_reason_code"] == "journal_invalid"
    assert row["journal_row_count"] == 1


@pytest.mark.asyncio
async def test_subsequent_budgeted_phase_can_skip_unbudgeted_phase(repo: DurableExecutionRepository) -> None:
    run_id, owner = uuid4(), uuid4()
    start_plan = _plan(run_id)
    await repo.create_run(
        run_id=run_id,
        trace_id=uuid4(),
        contract=make_contract(),
        start_plan=start_plan,
    )
    token = await repo.acquire_lease(run_id=run_id, owner=owner)
    await repo.set_root_checkpoint_pointer(
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        checkpoint_id="root",
        checkpoint_digest="sha256:" + "a" * 64,
    )
    candidate_plan = ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=run_id,
        limits=(
            ExecutionBudgetLimit(
                key=ExecutionBudgetKey(
                    scope=ExecutionBudgetScope.PHASE,
                    name="candidate.search",
                    phase=WorkflowPhase.CANDIDATE_RESEARCH,
                ),
                limit=1,
            ),
        ),
    )
    await repo.activate_phase(
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        phase=WorkflowPhase.CANDIDATE_RESEARCH,
        plan=candidate_plan,
        checkpoint=("", "root", "sha256:" + "a" * 64),
    )
    snapshots = await repo.get_all_budget_snapshots(
        run_id=run_id,
        owner=owner,
        fencing_token=token,
    )
    assert {snapshot.key.phase for snapshot in snapshots} == {
        WorkflowPhase.REQUIREMENTS,
        WorkflowPhase.CANDIDATE_RESEARCH,
    }


def _cross_phase_contract(
    *,
    hotel_nodes: tuple[str, ...] = ("hotel_select",),
    contract_id: str = "test.cross_phase.contract",
) -> DurableExecutionContract:
    descriptor = FakeCommandCodec.descriptor
    rules = tuple(sorted(
        (
            DurableExecutionNodeRecoveryRule(
                node_name="requirements",
                phase=WorkflowPhase.REQUIREMENTS,
                recovery_class=NodeRecoveryClass.REPLAY_SAFE,
            ),
            *(
                DurableExecutionNodeRecoveryRule(
                    node_name=node_name,
                    phase=WorkflowPhase.HOTEL_SELECTION,
                    recovery_class=NodeRecoveryClass.REPLAY_SAFE,
                )
                for node_name in hotel_nodes
            ),
            DurableExecutionNodeRecoveryRule(
                node_name="finish",
                phase=WorkflowPhase.HOTEL_SELECTION,
                recovery_class=NodeRecoveryClass.REPLAY_SAFE,
            ),
        ),
        key=lambda rule: rule.node_name,
    ))
    payload = {
        "budgeted_phases": ["requirements", "hotel_selection"],
        "command_codec": descriptor.model_dump(mode="json"),
        "contract_id": contract_id,
        "node_rules": [rule.model_dump(mode="json") for rule in rules],
        "schema_version": "1.0",
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return DurableExecutionContract(
        contract_id=contract_id,
        command_codec=descriptor,
        budgeted_phases=(WorkflowPhase.REQUIREMENTS, WorkflowPhase.HOTEL_SELECTION),
        node_rules=rules,
        contract_digest="sha256:" + hashlib.sha256(canonical).hexdigest(),
    )


def _cross_phase_graph_factory(
    calls: dict[str, int],
    *,
    promote: bool = True,
    hotel_nodes: tuple[str, ...] = ("hotel_select",),
    probe=None,
):
    def build(capabilities):
        requirements = capabilities.for_node(node_name="requirements")
        hotel_caps = {
            node_name: capabilities.for_node(node_name=node_name)
            for node_name in hotel_nodes
        }

        async def requirements_node(state: TravelSessionState) -> TravelSessionState:
            await requirements.ensure_phase_budget(state=state)
            if not promote:
                return state
            transition = make_confirmed_transition(state)
            return state.model_copy(update={
                "phase": WorkflowPhase.HOTEL_SELECTION,
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
            })

        def make_hotel(node_name: str):
            hotel = hotel_caps[node_name]

            async def hotel_node(state: TravelSessionState) -> TravelSessionState:
                await hotel.ensure_phase_budget(state=state)
                if probe is not None:
                    await probe(state)
                calls["hotel"] += 1
                return state

            return hotel_node

        def finish(state: TravelSessionState) -> TravelSessionState:
            return state.model_copy(update={
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph = StateGraph(TravelSessionState)
        graph.add_node("requirements", requirements_node)
        for node_name in hotel_nodes:
            graph.add_node(node_name, make_hotel(node_name))
        graph.add_node("finish", finish)
        graph.add_edge(START, "requirements")
        previous = "requirements"
        for node_name in hotel_nodes:
            graph.add_edge(previous, node_name)
            previous = node_name
        graph.add_edge(previous, "finish")
        graph.add_edge("finish", END)
        return graph

    return build


async def _hotel_activation_rows(runtime, run_id):
    rows = await runtime._repository.get_budget_activation_rows(run_id=run_id)
    return tuple(
        row for row in rows
        if str(row["phase_code"]) == WorkflowPhase.HOTEL_SELECTION.value
    )


async def _hotel_consumed(runtime, run_id) -> int:
    async with runtime._repository._pool.connection() as connection:
        async with connection.transaction():
            cursor = await connection.execute(
                "SELECT consumed_value FROM durable_execution.budget_limits"
                " WHERE run_id = %s AND phase_code = %s",
                (run_id, WorkflowPhase.HOTEL_SELECTION.value),
            )
            rows = await cursor.fetchall()
    return sum(int(row[0]) for row in rows)


@pytest.mark.asyncio
async def test_runtime_cross_phase_activation_binds_persisted_checkpoint(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    calls = {"hotel": 0}
    holder: dict[str, object] = {}

    async def probe(state: TravelSessionState) -> None:
        rows = await _hotel_activation_rows(holder["runtime"], state.run_id)
        assert len(rows) == 1
        assert int(rows[0]["activation_ordinal"]) == 1
        assert calls["hotel"] == 0

    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=_cross_phase_contract(),
        command_codec=FakeCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_cross_phase_graph_factory(calls, probe=probe),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    holder["runtime"] = runtime
    run_id, trace_id = uuid4(), uuid4()
    try:
        result = await runtime.start(run_id=run_id, trace_id=trace_id, command="start")
        assert type(result) is DurableExecutionTerminal
        assert calls["hotel"] == 1
        rows = await runtime._repository.get_budget_activation_rows(run_id=run_id)
        by_phase = {str(row["phase_code"]): row for row in rows}
        hotel_rows = await _hotel_activation_rows(runtime, run_id)
        assert len(hotel_rows) == 1
        hotel_row = hotel_rows[0]
        assert int(hotel_row["activation_ordinal"]) == 1
        assert type(hotel_row["activation_checkpoint_ns"]) is str
        assert hotel_row["activation_checkpoint_ns"] == ""
        assert type(hotel_row["activation_checkpoint_id"]) is str
        assert type(hotel_row["activation_checkpoint_digest"]) is str
        assert by_phase[WorkflowPhase.REQUIREMENTS.value]["activation_checkpoint_id"] is None
    finally:
        await runtime.close()


class _PhasePlanProvider(FakeBudgetPlanProvider):
    _SUPPORTED = frozenset({WorkflowPhase.HOTEL_SELECTION, WorkflowPhase.CANDIDATE_RESEARCH})

    def __init__(self, *, limit_by_outcome: bool = False) -> None:
        super().__init__()
        self.limit_by_outcome = limit_by_outcome
        self.inputs: list[tuple[WorkflowPhase, str]] = []

    def plan_for_phase(self, *, run_id, phase, state):
        outcome_name = type(state.requirement_outcome).__name__
        self.inputs.append((phase, outcome_name))
        if phase not in self._SUPPORTED:
            raise DurableExecutionUsageError() from None
        limit = 1
        if self.limit_by_outcome:
            limit = 1 if outcome_name == "ConfirmedTravelRequirement" else 2
        return ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=run_id,
            limits=(
                ExecutionBudgetLimit(
                    key=ExecutionBudgetKey(
                        scope=ExecutionBudgetScope.PHASE,
                        name=f"{phase.value}.step",
                        phase=phase,
                    ),
                    limit=limit,
                ),
            ),
        )


async def _expire_lease(runtime, run_id) -> None:
    async with runtime._repository._pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE durable_execution.run_leases SET lease_owner = COALESCE(lease_owner, %s),"
                " expires_at = clock_timestamp() - interval '1 second', renewed_at = clock_timestamp()"
                " WHERE run_id = %s",
                (uuid4(), run_id),
            )


async def _tamper_hotel_limit(runtime, run_id, *, delta: int = 1) -> None:
    async with runtime._repository._pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE durable_execution.budget_limits SET limit_value = limit_value + %s"
                " WHERE run_id = %s AND phase_code = %s",
                (delta, run_id, WorkflowPhase.HOTEL_SELECTION.value),
            )


async def _tamper_hotel_checkpoint(runtime, run_id) -> None:
    async with runtime._repository._pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE durable_execution.budget_limits SET activation_checkpoint_id = %s,"
                " activation_checkpoint_digest = %s"
                " WHERE run_id = %s AND phase_code = %s",
                ("0" * 32, "sha256:" + "0" * 64, run_id, WorkflowPhase.HOTEL_SELECTION.value),
            )


def _promote_to_hotel(state: TravelSessionState) -> TravelSessionState:
    transition = make_confirmed_transition(state)
    return state.model_copy(update={
        "phase": WorkflowPhase.HOTEL_SELECTION,
        "requirement_state": transition.state,
        "requirement_outcome": transition.outcome,
    })


def _reentry_graph_factory(calls):
    def build(capabilities):
        req = capabilities.for_node(node_name="requirements")
        select = capabilities.for_node(node_name="hotel_select")
        confirm = capabilities.for_node(node_name="hotel_confirm")

        async def requirements_node(state):
            await req.ensure_phase_budget(state=state)
            return _promote_to_hotel(state)

        async def hotel_select(state):
            await select.ensure_phase_budget(state=state)
            calls["hotel"] += 1
            transition = make_need_confirmation_transition(state)
            return state.model_copy(update={
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
            })

        async def hotel_confirm(state):
            await confirm.ensure_phase_budget(state=state)
            calls["hotel"] += 1
            return state

        def finish(state):
            return state.model_copy(update={
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph = StateGraph(TravelSessionState)
        graph.add_node("requirements", requirements_node)
        graph.add_node("hotel_select", hotel_select)
        graph.add_node("hotel_confirm", hotel_confirm)
        graph.add_node("finish", finish)
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "hotel_select")
        graph.add_edge("hotel_select", "hotel_confirm")
        graph.add_edge("hotel_confirm", "finish")
        graph.add_edge("finish", END)
        return graph

    return build


def _crash_graph_factory(calls, *, crash):
    def build(capabilities):
        req = capabilities.for_node(node_name="requirements")
        hotel = capabilities.for_node(node_name="hotel_select")

        async def requirements_node(state):
            await req.ensure_phase_budget(state=state)
            return _promote_to_hotel(state)

        async def hotel_select(state):
            await hotel.ensure_phase_budget(state=state)
            calls["hotel"] += 1
            return state

        async def boom(state):
            if crash and calls["boom"] == 0:
                calls["boom"] += 1
                raise RuntimeError("controlled crash")
            return state

        def finish(state):
            return state.model_copy(update={
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph = StateGraph(TravelSessionState)
        graph.add_node("requirements", requirements_node)
        graph.add_node("hotel_select", hotel_select)
        graph.add_node("boom", boom)
        graph.add_node("finish", finish)
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "hotel_select")
        graph.add_edge("hotel_select", "boom")
        graph.add_edge("boom", "finish")
        graph.add_edge("finish", END)
        return graph

    return build


@pytest.mark.asyncio
async def test_runtime_cross_phase_reentry_recomputes_from_bound_checkpoint(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    calls = {"hotel": 0}
    provider = _PhasePlanProvider(limit_by_outcome=True)
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=_cross_phase_contract(hotel_nodes=("hotel_select", "hotel_confirm")),
        command_codec=FakeCommandCodec(),
        budget_plan_provider=provider,
        graph_factory=_reentry_graph_factory(calls),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    run_id, trace_id = uuid4(), uuid4()
    try:
        result = await runtime.start(run_id=run_id, trace_id=trace_id, command="start")
        assert type(result) is DurableExecutionTerminal
        assert calls["hotel"] == 2
        hotel_rows = await _hotel_activation_rows(runtime, run_id)
        assert len(hotel_rows) == 1
        assert int(hotel_rows[0]["limit_value"]) == 1
        hotel_inputs = [item for item in provider.inputs if item[0] is WorkflowPhase.HOTEL_SELECTION]
        assert len(hotel_inputs) == 2
        assert {name for _, name in hotel_inputs} == {"ConfirmedTravelRequirement"}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_first_activation_rejects_inconsistent_caller_state(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    calls = {"hotel": 0}
    provider = _PhasePlanProvider(limit_by_outcome=True)

    def build(capabilities):
        req = capabilities.for_node(node_name="requirements")
        hotel = capabilities.for_node(node_name="hotel_select")

        async def requirements_node(state):
            await req.ensure_phase_budget(state=state)
            return _promote_to_hotel(state)

        async def hotel_select(state):
            transition = make_need_confirmation_transition(state)
            inconsistent = state.model_copy(update={
                "requirement_state": transition.state,
                "requirement_outcome": transition.outcome,
            })
            await hotel.ensure_phase_budget(state=inconsistent)
            calls["hotel"] += 1
            return state

        def finish(state):
            return state.model_copy(update={
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph = StateGraph(TravelSessionState)
        graph.add_node("requirements", requirements_node)
        graph.add_node("hotel_select", hotel_select)
        graph.add_node("finish", finish)
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "hotel_select")
        graph.add_edge("hotel_select", "finish")
        graph.add_edge("finish", END)
        return graph

    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=_cross_phase_contract(),
        command_codec=FakeCommandCodec(),
        budget_plan_provider=provider,
        graph_factory=build,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    run_id, trace_id = uuid4(), uuid4()
    try:
        with pytest.raises(DurableExecutionStateError):
            await runtime.start(run_id=run_id, trace_id=trace_id, command="start")
        assert calls["hotel"] == 0
        assert len(await _hotel_activation_rows(runtime, run_id)) == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_cross_phase_state_mismatch_is_rejected(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    calls = {"hotel": 0}
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=_cross_phase_contract(),
        command_codec=FakeCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_cross_phase_graph_factory(calls, promote=False),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    run_id, trace_id = uuid4(), uuid4()
    try:
        with pytest.raises(DurableExecutionStateError):
            await runtime.start(run_id=run_id, trace_id=trace_id, command="start")
        assert calls["hotel"] == 0
        assert len(await _hotel_activation_rows(runtime, run_id)) == 0
    finally:
        await runtime.close()


class _CrossPhaseConfirmationCodec(FakeCommandCodec):
    def requirement_resume(self, *, run_id, pause_generation, node_name, decoded_command):
        return RequirementConfirmationResume(
            action=RequirementConfirmation(decision="accept", revision_text=None)
        )


def _m05_cross_phase_contract() -> DurableExecutionContract:
    base = make_contract()
    rules = tuple(sorted(
        base.node_rules + (
            DurableExecutionNodeRecoveryRule(
                node_name="requirements/process_initial_turn",
                phase=WorkflowPhase.REQUIREMENTS,
                recovery_class=NodeRecoveryClass.NON_RECOVERABLE,
            ),
            DurableExecutionNodeRecoveryRule(
                node_name="requirements/await_clarification",
                phase=WorkflowPhase.REQUIREMENTS,
                recovery_class=NodeRecoveryClass.NON_RECOVERABLE,
            ),
            DurableExecutionNodeRecoveryRule(
                node_name="requirements/await_confirmation",
                phase=WorkflowPhase.REQUIREMENTS,
                recovery_class=NodeRecoveryClass.NON_RECOVERABLE,
            ),
            DurableExecutionNodeRecoveryRule(
                node_name="promote",
                phase=WorkflowPhase.REQUIREMENTS,
                recovery_class=NodeRecoveryClass.REPLAY_SAFE,
            ),
            DurableExecutionNodeRecoveryRule(
                node_name="hotel_select",
                phase=WorkflowPhase.HOTEL_SELECTION,
                recovery_class=NodeRecoveryClass.REPLAY_SAFE,
            ),
            DurableExecutionNodeRecoveryRule(
                node_name="hotel_confirm",
                phase=WorkflowPhase.HOTEL_SELECTION,
                recovery_class=NodeRecoveryClass.REPLAY_SAFE,
            ),
            DurableExecutionNodeRecoveryRule(
                node_name="finish",
                phase=WorkflowPhase.HOTEL_SELECTION,
                recovery_class=NodeRecoveryClass.REPLAY_SAFE,
            ),
        ),
        key=lambda rule: rule.node_name,
    ))
    payload = {
        "budgeted_phases": [phase.value for phase in base.budgeted_phases],
        "command_codec": base.command_codec.model_dump(mode="json"),
        "contract_id": "test.m05_cross_phase.contract",
        "node_rules": [rule.model_dump(mode="json") for rule in rules],
        "schema_version": base.schema_version,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return DurableExecutionContract(
        contract_id="test.m05_cross_phase.contract",
        command_codec=base.command_codec,
        budgeted_phases=base.budgeted_phases,
        node_rules=rules,
        contract_digest="sha256:" + hashlib.sha256(canonical).hexdigest(),
    )


def _m05_cross_phase_factory(*, run_id, trace_id, process, calls):
    def build(capabilities):
        initial = make_initial_state(run_id=run_id, trace_id=trace_id)
        service = ScriptedRequirementDomainService(
            process_results=(make_need_confirmation_transition(initial),) if process else (),
            confirm_results=(make_confirmed_transition(initial),) if not process else (),
        )
        driver = create_requirement_subgraph_embedded_driver(
            domain_service=service,
            execution_budget_manager_factory=capabilities.execution_budget_manager_factory(
                node_name="requirements"
            ),
            limits=capabilities.requirement_subgraph_limits,
        )
        capabilities.register_requirement_subgraph(node_name="requirements", driver=driver)
        select = capabilities.for_node(node_name="hotel_select")
        confirm = capabilities.for_node(node_name="hotel_confirm")

        def promote(state: TravelSessionState) -> TravelSessionState:
            return state.model_copy(update={"phase": WorkflowPhase.HOTEL_SELECTION})

        async def hotel_select(state: TravelSessionState) -> TravelSessionState:
            await select.ensure_phase_budget(state=state)
            calls["select"] += 1
            return state

        async def hotel_confirm(state: TravelSessionState) -> TravelSessionState:
            await confirm.ensure_phase_budget(state=state)
            calls["confirm"] += 1
            return state

        def finish(state: TravelSessionState) -> TravelSessionState:
            return state.model_copy(update={
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        builder = StateGraph(TravelSessionState)
        builder.add_node("requirements", driver.graph)
        builder.add_node("promote", promote)
        builder.add_node("hotel_select", hotel_select)
        builder.add_node("hotel_confirm", hotel_confirm)
        builder.add_node("finish", finish)
        builder.add_edge(START, "requirements")
        builder.add_edge("requirements", "promote")
        builder.add_edge("promote", "hotel_select")
        builder.add_edge("hotel_select", "hotel_confirm")
        builder.add_edge("hotel_confirm", "finish")
        builder.add_edge("finish", END)
        return builder

    return build


async def _tamper_requirements_limit(runtime, run_id, *, delta: int = 1) -> None:
    async with runtime._repository._pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE durable_execution.budget_limits SET limit_value = limit_value + %s"
                " WHERE run_id = %s AND phase_code = %s",
                (delta, run_id, WorkflowPhase.REQUIREMENTS.value),
            )


def _three_phase_contract() -> DurableExecutionContract:
    descriptor = FakeCommandCodec.descriptor
    definitions = (
        ("requirements", WorkflowPhase.REQUIREMENTS),
        ("hotel_select", WorkflowPhase.HOTEL_SELECTION),
        ("promote_candidate", WorkflowPhase.HOTEL_SELECTION),
        ("candidate_select", WorkflowPhase.CANDIDATE_RESEARCH),
        ("boom", WorkflowPhase.CANDIDATE_RESEARCH),
        ("finish", WorkflowPhase.CANDIDATE_RESEARCH),
    )
    node_rules = tuple(sorted(
        (
            DurableExecutionNodeRecoveryRule(
                node_name=node_name,
                phase=phase,
                recovery_class=NodeRecoveryClass.REPLAY_SAFE,
            )
            for node_name, phase in definitions
        ),
        key=lambda rule: rule.node_name,
    ))
    payload = {
        "budgeted_phases": ["requirements", "hotel_selection", "candidate_research"],
        "command_codec": descriptor.model_dump(mode="json"),
        "contract_id": "test.three_phase.contract",
        "node_rules": [rule.model_dump(mode="json") for rule in node_rules],
        "schema_version": "1.0",
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return DurableExecutionContract(
        contract_id="test.three_phase.contract",
        command_codec=descriptor,
        budgeted_phases=(
            WorkflowPhase.REQUIREMENTS,
            WorkflowPhase.HOTEL_SELECTION,
            WorkflowPhase.CANDIDATE_RESEARCH,
        ),
        node_rules=node_rules,
        contract_digest="sha256:" + hashlib.sha256(canonical).hexdigest(),
    )


def _three_phase_crash_factory(calls, *, crash):
    def build(capabilities):
        req = capabilities.for_node(node_name="requirements")
        hotel = capabilities.for_node(node_name="hotel_select")
        candidate = capabilities.for_node(node_name="candidate_select")

        async def requirements_node(state):
            await req.ensure_phase_budget(state=state)
            return _promote_to_hotel(state)

        async def hotel_select(state):
            await hotel.ensure_phase_budget(state=state)
            calls["hotel"] += 1
            return state

        def promote_candidate(state):
            return state.model_copy(update={"phase": WorkflowPhase.CANDIDATE_RESEARCH})

        async def candidate_select(state):
            await candidate.ensure_phase_budget(state=state)
            calls["candidate"] += 1
            return state

        async def boom(state):
            if crash and calls["boom"] == 0:
                calls["boom"] += 1
                raise RuntimeError("controlled crash")
            return state

        def finish(state):
            return state.model_copy(update={
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph = StateGraph(TravelSessionState)
        graph.add_node("requirements", requirements_node)
        graph.add_node("hotel_select", hotel_select)
        graph.add_node("promote_candidate", promote_candidate)
        graph.add_node("candidate_select", candidate_select)
        graph.add_node("boom", boom)
        graph.add_node("finish", finish)
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "hotel_select")
        graph.add_edge("hotel_select", "promote_candidate")
        graph.add_edge("promote_candidate", "candidate_select")
        graph.add_edge("candidate_select", "boom")
        graph.add_edge("boom", "finish")
        graph.add_edge("finish", END)
        return graph

    return build


@pytest.mark.asyncio
async def test_runtime_cross_runtime_resume_performs_first_phase_activation(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    # 该用例证明的是重启后由新 Runtime 首次激活 hotel 阶段，不是已有 activation 的复验。
    calls = {"select": 0, "confirm": 0}
    run_id, trace_id = uuid4(), uuid4()
    contract = _m05_cross_phase_contract()
    first = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=_CrossPhaseConfirmationCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_m05_cross_phase_factory(
            run_id=run_id, trace_id=trace_id, process=True, calls=calls
        ),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        paused = await first.start(run_id=run_id, trace_id=trace_id, command="start")
        assert type(paused) is DurableExecutionPaused
        assert calls == {"select": 0, "confirm": 0}
        assert len(await _hotel_activation_rows(first, run_id)) == 0
    finally:
        await first.close()

    provider = _PhasePlanProvider()
    second = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=_CrossPhaseConfirmationCodec(),
        budget_plan_provider=provider,
        graph_factory=_m05_cross_phase_factory(
            run_id=run_id, trace_id=trace_id, process=False, calls=calls
        ),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        terminal = await second.resume(
            run_id=run_id, pause_token=paused.pause.pause_token, command="确认"
        )
        assert type(terminal) is DurableExecutionTerminal
        assert calls == {"select": 1, "confirm": 1}
        hotel_rows = await _hotel_activation_rows(second, run_id)
        assert len(hotel_rows) == 1
        hotel_inputs = [item for item in provider.inputs if item[0] is WorkflowPhase.HOTEL_SELECTION]
        assert hotel_inputs
        assert {name for _, name in hotel_inputs} == {"ConfirmedTravelRequirement"}
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_runtime_cross_runtime_recover_reverifies_existing_activation(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    calls = {"hotel": 0, "boom": 0}
    contract = _cross_phase_contract(hotel_nodes=("hotel_select", "boom"))
    run_id, trace_id = uuid4(), uuid4()
    first = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=FakeCommandCodec(),
        budget_plan_provider=_PhasePlanProvider(),
        graph_factory=_crash_graph_factory(calls, crash=True),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        with pytest.raises(DurableExecutionDependencyError):
            await first.start(run_id=run_id, trace_id=trace_id, command="start")
    finally:
        await first.close()

    provider = _PhasePlanProvider()
    second = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=FakeCommandCodec(),
        budget_plan_provider=provider,
        graph_factory=_crash_graph_factory(calls, crash=False),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        before = await _hotel_activation_rows(second, run_id)
        assert len(before) == 1
        pointer_before = (
            before[0]["activation_checkpoint_ns"],
            before[0]["activation_checkpoint_id"],
            before[0]["activation_checkpoint_digest"],
        )
        limit_before = int(before[0]["limit_value"])
        consumed_before = await _hotel_consumed(second, run_id)
        assert calls["hotel"] == 1
        await _expire_lease(second, run_id)
        result = await second.recover(run_id=run_id)
        assert type(result) is DurableExecutionTerminal
        after = await _hotel_activation_rows(second, run_id)
        assert len(after) == 1
        assert (
            after[0]["activation_checkpoint_ns"],
            after[0]["activation_checkpoint_id"],
            after[0]["activation_checkpoint_digest"],
        ) == pointer_before
        assert int(after[0]["limit_value"]) == limit_before
        assert await _hotel_consumed(second, run_id) == consumed_before
        assert calls["hotel"] == 1
        assert [item for item in provider.inputs if item[0] is WorkflowPhase.HOTEL_SELECTION]
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_runtime_recover_rejects_plan_mismatch_for_inactive_phase(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    calls = {"hotel": 0, "candidate": 0, "boom": 0}
    contract = _three_phase_contract()
    run_id, trace_id = uuid4(), uuid4()
    first = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=FakeCommandCodec(),
        budget_plan_provider=_PhasePlanProvider(),
        graph_factory=_three_phase_crash_factory(calls, crash=True),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        with pytest.raises(DurableExecutionDependencyError):
            await first.start(run_id=run_id, trace_id=trace_id, command="start")
    finally:
        await first.close()

    second = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=FakeCommandCodec(),
        budget_plan_provider=_PhasePlanProvider(),
        graph_factory=_three_phase_crash_factory(calls, crash=False),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        assert calls == {"hotel": 1, "candidate": 1, "boom": 1}
        await _tamper_hotel_limit(second, run_id, delta=1)
        await _expire_lease(second, run_id)
        with pytest.raises(DurableExecutionStateError) as caught:
            await second.recover(run_id=run_id)
        assert type(caught.value) is DurableExecutionStateError
        row = await second._repository.get_run_row(run_id=run_id)
        assert row["lifecycle"] == "blocked"
        assert row["blocked_reason_code"] == "budget_mismatch"
        assert calls == {"hotel": 1, "candidate": 1, "boom": 1}
        blocked_evidence = (
            row,
            await second._repository.get_pending_command(run_id=run_id),
            await second._repository.get_lease_state(run_id=run_id),
            await second._repository.get_recovery_operation_evidence(run_id=run_id),
        )
        assert blocked_evidence[1]["status"] == "cleared"
        assert blocked_evidence[1]["command_bytes"] is None
        for call in (
            lambda: second.recover(run_id=run_id),
            lambda: second.get_state(run_id=run_id),
            lambda: second.resume(run_id=run_id, pause_token="blocked-run", command="resume"),
        ):
            with pytest.raises(DurableExecutionRunBlockedError) as blocked:
                await call()
            assert type(blocked.value) is DurableExecutionRunBlockedError
        assert blocked_evidence == (
            await second._repository.get_run_row(run_id=run_id),
            await second._repository.get_pending_command(run_id=run_id),
            await second._repository.get_lease_state(run_id=run_id),
            await second._repository.get_recovery_operation_evidence(run_id=run_id),
        )
        assert calls == {"hotel": 1, "candidate": 1, "boom": 1}
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_runtime_recover_rejects_missing_bound_checkpoint(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    calls = {"hotel": 0, "candidate": 0, "boom": 0}
    contract = _three_phase_contract()
    run_id, trace_id = uuid4(), uuid4()
    first = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=FakeCommandCodec(),
        budget_plan_provider=_PhasePlanProvider(),
        graph_factory=_three_phase_crash_factory(calls, crash=True),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        with pytest.raises(DurableExecutionDependencyError):
            await first.start(run_id=run_id, trace_id=trace_id, command="start")
    finally:
        await first.close()

    second = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=FakeCommandCodec(),
        budget_plan_provider=_PhasePlanProvider(),
        graph_factory=_three_phase_crash_factory(calls, crash=False),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        await _tamper_hotel_checkpoint(second, run_id)
        await _expire_lease(second, run_id)
        with pytest.raises(DurableExecutionStateError) as caught:
            await second.recover(run_id=run_id)
        assert type(caught.value) is DurableExecutionStateError
        row = await second._repository.get_run_row(run_id=run_id)
        assert row["lifecycle"] == "blocked"
        assert row["blocked_reason_code"] == "budget_mismatch"
        assert calls == {"hotel": 1, "candidate": 1, "boom": 1}
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_runtime_resume_rejects_tampered_activation_before_dispatch(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    calls = {"select": 0, "confirm": 0}
    run_id, trace_id = uuid4(), uuid4()
    contract = _m05_cross_phase_contract()
    first = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=_CrossPhaseConfirmationCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_m05_cross_phase_factory(
            run_id=run_id, trace_id=trace_id, process=True, calls=calls
        ),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        paused = await first.start(run_id=run_id, trace_id=trace_id, command="start")
        assert type(paused) is DurableExecutionPaused
    finally:
        await first.close()

    second = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=FakeArtifactRepository(),
        contract=contract,
        command_codec=_CrossPhaseConfirmationCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_m05_cross_phase_factory(
            run_id=run_id, trace_id=trace_id, process=False, calls=calls
        ),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        await _tamper_requirements_limit(second, run_id, delta=1)
        with pytest.raises(DurableExecutionStateError) as caught:
            await second.resume(
                run_id=run_id, pause_token=paused.pause.pause_token, command="确认"
            )
        assert type(caught.value) is DurableExecutionStateError
        row = await second._repository.get_run_row(run_id=run_id)
        assert row["lifecycle"] == "blocked"
        assert row["blocked_reason_code"] == "budget_mismatch"
        assert calls == {"select": 0, "confirm": 0}
        for call in (
            lambda: second.recover(run_id=run_id),
            lambda: second.get_state(run_id=run_id),
            lambda: second.resume(run_id=run_id, pause_token=paused.pause.pause_token, command="确认"),
        ):
            with pytest.raises(DurableExecutionRunBlockedError) as blocked:
                await call()
            assert type(blocked.value) is DurableExecutionRunBlockedError
        assert calls == {"select": 0, "confirm": 0}
    finally:
        await second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", tuple(_BlockedReason), ids=lambda reason: reason.value)
async def test_all_blocked_reasons_are_typed_and_closed(
    repo: DurableExecutionRepository,
    reason: _BlockedReason,
) -> None:
    run_id, owner, token, manager, key = await _run(repo)
    cause_factory = getattr(_BlockCause, reason.value)
    cause = cause_factory()
    assert type(cause) is _BlockCause
    assert cause.reason is reason
    facts: _BlockFacts
    expected_indeterminate = 0
    if reason in {_BlockedReason.ARTIFACT_INVALID, _BlockedReason.OPERATION_INDETERMINATE}:
        operation = BudgetConsumeRequest(
            operation_id=OperationId(
                run_id=run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step=f"reason_{reason.value}",
            ),
            key=key,
            amount=1,
        )
        assert (await manager.consume(operation)).disposition == "applied"
        facts = _BlockFacts(
            cause=cause,
            operation_id=operation.operation_id,
            artifact_missing_after_permission=reason is _BlockedReason.ARTIFACT_INVALID,
            external_result_unknown=reason is _BlockedReason.OPERATION_INDETERMINATE,
        )
        expected_indeterminate = 1
    else:
        facts = _BlockFacts(cause=cause)
    await repo.block_run(
        run_id=run_id,
        facts=facts,
        owner=owner,
        fencing_token=token,
    )
    row = await repo.get_run_row(run_id=run_id)
    assert row["lifecycle"] == "blocked"
    assert row["blocked_reason_code"] == reason.value
    async with repo._pool.connection() as connection:
        async with connection.transaction():
            cursor = await connection.execute(
                "SELECT count(*) AS n FROM durable_execution.operation_journal WHERE run_id = %s AND journal_state = 'indeterminate'",
                (run_id,),
            )
            assert (await cursor.fetchone())["n"] == expected_indeterminate


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fact_values", "expected"),
    (
        ({"lease_lost_after_permission": True}, "lease_lost_after_permission"),
        ({"cancelled_after_permission": True}, "cancelled_after_permission"),
        ({"artifact_missing_after_permission": True}, "artifact_missing_after_permission"),
        ({"external_result_unknown": True}, "external_result_unknown"),
        (
            {
                "lease_lost_after_permission": True,
                "cancelled_after_permission": True,
                "artifact_missing_after_permission": True,
                "external_result_unknown": True,
            },
            "lease_lost_after_permission",
        ),
    ),
)
async def test_block_reason_facts_and_priority_are_persisted(
    durable_execution_settings: DurableExecutionSettings,
    fact_values: dict[str, bool],
    expected: str,
) -> None:
    pool = await _pool(durable_execution_settings)
    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    await repository.initialize_capacity()
    try:
        run_id, owner, token, manager, key = await _run(repository)
        operation = BudgetConsumeRequest(
            operation_id=OperationId(
                run_id=run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="reason_step",
            ),
            key=key,
            amount=1,
        )
        assert (await manager.consume(operation)).disposition == "applied"
        facts = _BlockFacts(**fact_values, operation_id=operation.operation_id)
        await repository.block_run(
            run_id=run_id,
            facts=facts,
            owner=owner,
            fencing_token=token,
        )
        row = await repository.get_run_row(run_id=run_id)
        assert row["lifecycle"] == "blocked"
        assert row["blocked_reason_code"] == (
            "artifact_invalid" if expected == "artifact_missing_after_permission" else "operation_indeterminate"
        )
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT reason_code, journal_row_count
                    FROM durable_execution.operation_journal AS j
                    JOIN durable_execution.runs AS r USING (run_id)
                    WHERE j.run_id = %s AND j.journal_state = 'indeterminate'
                    """,
                    (run_id,),
                )
                result = await cursor.fetchone()
                assert result["reason_code"] == expected
                assert result["journal_row_count"] == 2
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_block_multiple_candidates_and_fault_rollback_are_atomic(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    fired = False

    def fault(point: str) -> None:
        nonlocal fired
        if point == "block_before_commit" and not fired:
            fired = True
            raise RuntimeError("rollback seam")

    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        fault_hook=fault,
    )
    await repository.initialize_capacity()
    try:
        run_id, owner = uuid4(), uuid4()
        start_plan = _plan(run_id, limit=2)
        await repository.create_run(
            run_id=run_id, trace_id=uuid4(), contract=make_contract(), start_plan=start_plan
        )
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        manager = repository._budget_manager(run_id=run_id, owner=owner, fencing_token=token)
        key = start_plan.limits[0].key
        for logical_step in ("first", "second"):
            request = BudgetConsumeRequest(
                operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.REQUIREMENTS, logical_step=logical_step),
                key=key,
                amount=1,
            )
            assert (await manager.consume(request)).disposition == "applied"
        with pytest.raises(Exception):
            await repository.block_run(
                run_id=run_id,
                facts=_BlockFacts(cancelled_after_permission=True),
                owner=owner,
                fencing_token=token,
            )
        row = await repository.get_run_row(run_id=run_id)
        assert row["lifecycle"] == "open"
        assert row["journal_row_count"] == 2
        await repository.block_run(
            run_id=run_id,
            facts=_BlockFacts(cancelled_after_permission=True),
            owner=owner,
            fencing_token=token,
        )
        row = await repository.get_run_row(run_id=run_id)
        assert row["blocked_reason_code"] == "node_classification_invalid"
        assert row["journal_row_count"] == 2
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_custom_low_operation_capacity_rolls_back(
    custom_capacity_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(custom_capacity_settings)
    limits = DEMO_DURABLE_EXECUTION_LIMITS.model_copy(
        update={"max_managed_operations_per_run": 1, "max_journal_rows_per_run": 4}
    )
    repository = DurableExecutionRepository(coordination_pool=pool, limits=limits)
    await repository.initialize_capacity()
    try:
        run_id, _owner, _token, manager, key = await _run(repository)
        first = BudgetConsumeRequest(
            operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.REQUIREMENTS, logical_step="capacity_step"),
            key=key,
            amount=1,
        )
        assert (await manager.consume(first)).disposition == "applied"
        with pytest.raises(ExecutionBudgetCapacityExceededError):
            await manager.consume(
                first.model_copy(
                    update={
                        "operation_id": first.operation_id.model_copy(
                            update={"logical_step": "second_capacity_step"}
                        )
                    }
                )
            )
        row = await repository.get_run_row(run_id=run_id)
        assert row["lifecycle"] == "open"
        assert row["journal_row_count"] == 1
    finally:
        await pool.close()
