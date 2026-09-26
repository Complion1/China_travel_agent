from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app.durable_execution.errors import (
    DurableExecutionRunBusyError,
    DurableExecutionStateError,
)
from app.durable_execution.models import DEMO_DURABLE_EXECUTION_LIMITS
from app.durable_execution.repository import DurableExecutionRepository
from app.durable_execution.settings import DurableExecutionSettings
from app.requirement_subgraph import RequirementSubgraphCoordinatorLimits
from app.workflow_contracts import (
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    WorkflowPhase,
)
from tests.support.durable_execution_fakes import make_contract


async def _open_pool(settings: DurableExecutionSettings) -> AsyncConnectionPool:
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


def _plan(run_id: UUID) -> ExecutionBudgetPlan:
    return ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=run_id,
        limits=(
            ExecutionBudgetLimit(
                key=ExecutionBudgetKey(
                    scope=ExecutionBudgetScope.PHASE,
                    name="requirements.lease",
                    phase=WorkflowPhase.REQUIREMENTS,
                ),
                limit=2,
            ),
        ),
    )


@pytest_asyncio.fixture
async def repo(durable_execution_settings: DurableExecutionSettings):
    pool = await _open_pool(durable_execution_settings)
    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    await repository.initialize_capacity()
    try:
        yield repository
    finally:
        await pool.close()


async def _create(repo: DurableExecutionRepository) -> tuple[UUID, UUID]:
    run_id = uuid4()
    trace_id = uuid4()
    await repo.create_run(
        run_id=run_id,
        trace_id=trace_id,
        contract=make_contract(),
        start_plan=_plan(run_id),
    )
    return run_id, trace_id


@pytest.mark.asyncio
async def test_lease_is_db_time_owner_and_fencing_cas(repo: DurableExecutionRepository) -> None:
    run_id, _ = await _create(repo)
    owner_a = uuid4()
    owner_b = uuid4()
    token_a = await repo.acquire_lease(run_id=run_id, owner=owner_a)
    assert token_a == 1

    with pytest.raises(DurableExecutionRunBusyError):
        await repo.acquire_lease(run_id=run_id, owner=owner_b)
    with pytest.raises(DurableExecutionStateError):
        await repo.renew_lease(run_id=run_id, owner=owner_b, fencing_token=token_a)

    async with repo._pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET expires_at = clock_timestamp() - interval '1 second'
                WHERE run_id = %s
                """,
                (run_id,),
            )
    token_b = await repo.acquire_lease(run_id=run_id, owner=owner_b)
    assert token_b == 2
    assert not await repo.release_lease(run_id=run_id, owner=owner_a, fencing_token=token_a)
    await repo.renew_lease(run_id=run_id, owner=owner_b, fencing_token=token_b)


@pytest.mark.asyncio
async def test_two_independent_tasks_cannot_take_one_run(repo: DurableExecutionRepository) -> None:
    run_id, _ = await _create(repo)
    owners = (uuid4(), uuid4())
    results = await __import__("asyncio").gather(
        repo.acquire_lease(run_id=run_id, owner=owners[0]),
        repo.acquire_lease(run_id=run_id, owner=owners[1]),
        return_exceptions=True,
    )
    assert sum(isinstance(result, int) for result in results) == 1
    assert sum(isinstance(result, DurableExecutionRunBusyError) for result in results) == 1
