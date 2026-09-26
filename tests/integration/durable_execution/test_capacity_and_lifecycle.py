from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app.durable_execution.errors import DurableExecutionConfigurationError
from app.durable_execution.models import DEMO_DURABLE_EXECUTION_LIMITS
from app.durable_execution.repository import DurableExecutionRepository
from app.durable_execution.settings import DurableExecutionSettings
from app.workflow_contracts import (
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    WorkflowPhase,
)
from tests.support.durable_execution_fakes import make_contract


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


def _plan(run_id: UUID) -> ExecutionBudgetPlan:
    return ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=run_id,
        limits=(
            ExecutionBudgetLimit(
                key=ExecutionBudgetKey(
                    scope=ExecutionBudgetScope.PHASE,
                    name="requirements.lifecycle",
                    phase=WorkflowPhase.REQUIREMENTS,
                ),
                limit=1,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_capacity_singleton_initializes_once_and_counts_match(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    try:
        first = DurableExecutionRepository(
            coordination_pool=pool,
            limits=DEMO_DURABLE_EXECUTION_LIMITS,
        )
        second = DurableExecutionRepository(
            coordination_pool=pool,
            limits=DEMO_DURABLE_EXECUTION_LIMITS,
        )
        results = await asyncio.gather(
            first.initialize_capacity(),
            second.initialize_capacity(),
            return_exceptions=True,
        )
        assert all(result == DEMO_DURABLE_EXECUTION_LIMITS for result in results)
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT count(*) AS rows FROM durable_execution.capacity_state"
                )
                assert (await cursor.fetchone())["rows"] == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_run_lifecycle_releases_active_but_retains_stored(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    await repository.initialize_capacity()
    try:
        run_id = uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        owner = uuid4()
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        digest = "sha256:" + "2" * 64
        await repository.set_root_checkpoint_pointer(
            run_id=run_id,
            owner=owner,
            fencing_token=token,
            checkpoint_id="root-1",
            checkpoint_digest=digest,
        )
        await repository.close_run(
            run_id=run_id,
            owner=owner,
            fencing_token=token,
            pointer=("", "root-1", digest),
        )
        row = await repository.get_run_row(run_id=run_id)
        assert row["lifecycle"] == "closed"
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT active_run_count, stored_run_count FROM durable_execution.capacity_state"
                )
                counts = await cursor.fetchone()
                assert counts["active_run_count"] < counts["stored_run_count"]
    finally:
        await pool.close()
