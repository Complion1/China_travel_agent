from __future__ import annotations

import inspect
from uuid import UUID

import pytest

from app.durable_execution.budget import PersistentExecutionBudgetManager
from app.workflow_contracts import (
    BudgetConsumed,
    BudgetConsumeRequest,
    ExecutionBudgetKey,
    ExecutionBudgetSnapshot,
    ExecutionBudgetScope,
    ExecutionBudgetManager,
    OperationId,
    WorkflowPhase,
)


RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
OWNER = UUID("55555555-5555-4555-8555-555555555555")
KEY = ExecutionBudgetKey(
    scope=ExecutionBudgetScope.PHASE,
    name="requirements.contract",
    phase=WorkflowPhase.REQUIREMENTS,
)


class _ContractRepo:
    async def _consume_budget(self, *, request, owner, fencing_token):
        assert request.operation_id.run_id == RUN_ID
        assert owner == OWNER and fencing_token == 7
        snapshot = ExecutionBudgetSnapshot(
            schema_version="1.0",
            run_id=RUN_ID,
            key=KEY,
            limit=3,
            consumed=request.amount,
            remaining=3 - request.amount,
        )
        return BudgetConsumed(
            disposition="applied",
            operation_id=request.operation_id,
            key=KEY,
            amount=request.amount,
            snapshot=snapshot,
        )

    async def get_budget_snapshot(self, *, run_id, key, owner, fencing_token):
        del owner, fencing_token
        return ExecutionBudgetSnapshot(
            schema_version="1.0",
            run_id=run_id,
            key=key,
            limit=3,
            consumed=0,
            remaining=3,
        )

    async def get_all_budget_snapshots(self, *, run_id, owner, fencing_token):
        return (await self.get_budget_snapshot(run_id=run_id, key=KEY, owner=owner, fencing_token=fencing_token),)


def test_module04_protocol_surface_remains_exact() -> None:
    assert {
        name for name in ExecutionBudgetManager.__dict__ if not name.startswith("_")
    } == {"consume", "get_snapshot", "get_all_snapshots"}
    assert list(inspect.signature(PersistentExecutionBudgetManager.consume).parameters) == [
        "self", "request"
    ]
    assert list(inspect.signature(PersistentExecutionBudgetManager.get_snapshot).parameters) == [
        "self", "key"
    ]
    assert list(inspect.signature(PersistentExecutionBudgetManager.get_all_snapshots).parameters) == [
        "self"
    ]


@pytest.mark.asyncio
async def test_persistent_manager_returns_typed_outcome_and_snapshot() -> None:
    manager = PersistentExecutionBudgetManager(
        repository=_ContractRepo(),
        run_id=RUN_ID,
        owner=OWNER,
        fencing_token=7,
    )
    request = BudgetConsumeRequest(
        operation_id=OperationId(
            run_id=RUN_ID,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="contract_step",
        ),
        key=KEY,
        amount=1,
    )
    outcome = await manager.consume(request)
    assert isinstance(outcome, BudgetConsumed)
    assert outcome.snapshot.run_id == RUN_ID
    assert await manager.get_snapshot(key=KEY) == outcome.snapshot.model_copy(update={"consumed": 0, "remaining": 3})
