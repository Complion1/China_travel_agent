from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.durable_execution.budget import PersistentExecutionBudgetManager
from app.durable_execution.errors import DurableExecutionUsageError
from app.durable_execution.repository import DurableExecutionRepository
from app.workflow_contracts import (
    BudgetConsumed,
    BudgetConsumeRequest,
    BudgetExceeded,
    ExecutionBudgetKey,
    ExecutionBudgetSnapshot,
    ExecutionBudgetScope,
    OperationId,
    WorkflowPhase,
)
from app.workflow_contracts.errors import (
    ExecutionBudgetManagerUsageError,
    ExecutionBudgetRequestValidationError,
)


RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
OWNER = UUID("22222222-2222-4222-8222-222222222222")
KEY = ExecutionBudgetKey(
    scope=ExecutionBudgetScope.PHASE,
    name="requirements.step",
    phase=WorkflowPhase.REQUIREMENTS,
)


class _BudgetRepo:
    def __init__(self) -> None:
        self.calls: list[BudgetConsumeRequest] = []
        self.snapshot = ExecutionBudgetSnapshot(
            schema_version="1.0",
            run_id=RUN_ID,
            key=KEY,
            limit=2,
            consumed=0,
            remaining=2,
        )

    async def _consume_budget(self, *, request, owner, fencing_token):
        assert owner == OWNER
        assert fencing_token == 1
        self.calls.append(request)
        if request.operation_id.logical_step == "rejected":
            return BudgetExceeded(
                disposition="rejected",
                operation_id=request.operation_id,
                key=request.key,
                amount=request.amount,
                snapshot=self.snapshot,
            )
        return BudgetConsumed(
            disposition="applied" if len(self.calls) == 1 else "replayed",
            operation_id=request.operation_id,
            key=request.key,
            amount=request.amount,
            snapshot=self.snapshot,
        )

    async def get_budget_snapshot(self, *, run_id, key, owner, fencing_token):
        assert run_id == RUN_ID and key == KEY and owner == OWNER and fencing_token == 1
        return self.snapshot

    async def get_all_budget_snapshots(self, *, run_id, owner, fencing_token):
        assert run_id == RUN_ID and owner == OWNER and fencing_token == 1
        return (self.snapshot,)


def _request(step: str = "first", amount: int = 1) -> BudgetConsumeRequest:
    return BudgetConsumeRequest(
        operation_id=OperationId(
            run_id=RUN_ID,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step=step,
        ),
        key=KEY,
        amount=amount,
    )


@pytest.mark.asyncio
async def test_persistent_manager_implements_all_three_module04_methods() -> None:
    repo = _BudgetRepo()
    manager = PersistentExecutionBudgetManager(
        repository=repo,
        run_id=RUN_ID,
        owner=OWNER,
        fencing_token=1,
    )

    applied = await manager.consume(_request())
    replayed = await manager.consume(_request("second"))
    rejected = await manager.consume(_request("rejected"))

    assert isinstance(applied, BudgetConsumed)
    assert applied.disposition == "applied"
    assert isinstance(replayed, BudgetConsumed)
    assert replayed.disposition == "replayed"
    assert isinstance(rejected, BudgetExceeded)
    assert rejected.disposition == "rejected"
    assert await manager.get_snapshot(key=KEY) == repo.snapshot
    assert await manager.get_all_snapshots() == (repo.snapshot,)


@pytest.mark.asyncio
async def test_persistent_manager_strictly_copies_request_and_binds_run() -> None:
    repo = _BudgetRepo()
    manager = PersistentExecutionBudgetManager(
        repository=repo,
        run_id=RUN_ID,
        owner=OWNER,
        fencing_token=1,
    )
    forged = BudgetConsumeRequest.model_construct(
        operation_id=OperationId(
            run_id=UUID("33333333-3333-4333-8333-333333333333"),
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="first",
        ),
        key=KEY,
        amount=1,
    )
    with pytest.raises(ExecutionBudgetManagerUsageError):
        await manager.consume(forged)
    assert repo.calls == []


def test_start_plan_requires_requirements_and_rejects_future_phase() -> None:
    from app.workflow_contracts import ExecutionBudgetLimit, ExecutionBudgetPlan

    no_requirements = ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=RUN_ID,
        limits=(
            ExecutionBudgetLimit(
                key=ExecutionBudgetKey(
                    scope=ExecutionBudgetScope.PHASE,
                    name="hotel.step",
                    phase=WorkflowPhase.HOTEL_SELECTION,
                ),
                limit=1,
            ),
        ),
    )
    with pytest.raises(Exception):
        DurableExecutionRepository._validate_start_plan(no_requirements)


def test_manager_binds_to_one_event_loop() -> None:
    repo = _BudgetRepo()
    manager = PersistentExecutionBudgetManager(
        repository=repo,
        run_id=RUN_ID,
        owner=OWNER,
        fencing_token=1,
    )

    async def use_manager() -> None:
        await manager.get_all_snapshots()

    asyncio.run(use_manager())
    with pytest.raises(Exception):
        asyncio.run(use_manager())
