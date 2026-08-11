"""InMemoryExecutionBudgetManager behavior and concurrency tests."""

from __future__ import annotations

import asyncio
from inspect import iscoroutinefunction
from uuid import UUID

import pytest

from app.workflow_contracts.budgets.in_memory import (
    InMemoryExecutionBudgetManager,
)
from app.workflow_contracts.budgets.manager import ExecutionBudgetManager
from app.workflow_contracts.budgets.models import (
    BudgetConsumeRequest,
    BudgetConsumed,
    BudgetExceeded,
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetManagerLimits,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    ExecutionBudgetSnapshot,
)
from app.workflow_contracts.errors import (
    BudgetConsumeConflictError,
    BudgetKeyNotConfiguredError,
    BudgetOperationScopeMismatchError,
    ExecutionBudgetCapacityExceededError,
    ExecutionBudgetManagerUsageError,
    ExecutionBudgetPlanValidationError,
    ExecutionBudgetRequestValidationError,
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
OTHER_RUN_ID = UUID("12345678-1234-5678-1234-567812345679")


def _operation(
    logical_step: str,
    *,
    run_id: UUID = RUN_ID,
    phase: WorkflowPhase = WorkflowPhase.REQUIREMENTS,
) -> OperationId:
    return OperationId(run_id=run_id, phase=phase, logical_step=logical_step)


def _key(
    *,
    scope: ExecutionBudgetScope = ExecutionBudgetScope.RUN,
    name: str = "test.run_budget",
    phase: WorkflowPhase | None = None,
) -> ExecutionBudgetKey:
    return ExecutionBudgetKey(scope=scope, name=name, phase=phase)


def _plan(
    *,
    run_limit: int = 3,
    phase_limit: int = 2,
    run_id: UUID = RUN_ID,
) -> ExecutionBudgetPlan:
    return ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=run_id,
        limits=(
            ExecutionBudgetLimit(
                key=_key(
                    scope=ExecutionBudgetScope.PHASE,
                    name="test.phase_budget",
                    phase=WorkflowPhase.REQUIREMENTS,
                ),
                limit=phase_limit,
            ),
            ExecutionBudgetLimit(key=_key(), limit=run_limit),
        ),
    )


def _request(
    logical_step: str,
    *,
    key: ExecutionBudgetKey | None = None,
    amount: int = 1,
    run_id: UUID = RUN_ID,
    phase: WorkflowPhase = WorkflowPhase.REQUIREMENTS,
) -> BudgetConsumeRequest:
    return BudgetConsumeRequest(
        operation_id=_operation(logical_step, run_id=run_id, phase=phase),
        key=_key() if key is None else key,
        amount=amount,
    )


def _manager(
    *,
    plan: ExecutionBudgetPlan | None = None,
    max_operation_records: int = 16,
) -> InMemoryExecutionBudgetManager:
    return InMemoryExecutionBudgetManager(
        plan=_plan() if plan is None else plan,
        limits=ExecutionBudgetManagerLimits(
            max_operation_records=max_operation_records
        ),
    )


@pytest.mark.asyncio
async def test_applied_and_rejected_replay_use_original_snapshots() -> None:
    manager = _manager(plan=_plan(run_limit=2, phase_limit=1))
    run_key = _key()
    phase_key = _key(
        scope=ExecutionBudgetScope.PHASE,
        name="test.phase_budget",
        phase=WorkflowPhase.REQUIREMENTS,
    )

    first_request = _request("first", key=run_key)
    first = await manager.consume(first_request)
    second = await manager.consume(_request("second", key=run_key))
    assert isinstance(first, BudgetConsumed)
    assert isinstance(second, BudgetConsumed)
    assert first.snapshot.consumed == 1
    assert second.snapshot.consumed == 2

    first_replay = await manager.consume(first_request)
    assert isinstance(first_replay, BudgetConsumed)
    assert first_replay.disposition == "replayed"
    assert first_replay.snapshot == first.snapshot
    assert first_replay.snapshot is not first.snapshot

    rejected_request = _request("rejected", key=phase_key, amount=2)
    rejected = await manager.consume(rejected_request)
    assert isinstance(rejected, BudgetExceeded)
    assert rejected.disposition == "rejected"
    rejected_replay = await manager.consume(rejected_request)
    assert isinstance(rejected_replay, BudgetExceeded)
    assert rejected_replay.disposition == "replayed"
    assert rejected_replay.snapshot == rejected.snapshot

    phase_snapshot = await manager.get_snapshot(key=phase_key)
    assert phase_snapshot.consumed == 0


@pytest.mark.asyncio
async def test_key_amount_conflict_and_validation_order() -> None:
    manager = _manager()
    run_key = _key()
    await manager.consume(_request("existing", key=run_key))

    with pytest.raises(BudgetConsumeConflictError):
        await manager.consume(_request("existing", key=run_key, amount=2))

    unknown = _key(
        scope=ExecutionBudgetScope.RUN,
        name="test.unknown_budget",
        phase=None,
    )
    with pytest.raises(BudgetKeyNotConfiguredError):
        await manager.consume(
            _request("unknown", key=unknown, run_id=OTHER_RUN_ID)
        )

    with pytest.raises(BudgetOperationScopeMismatchError):
        await manager.consume(
                _request("wrong_run", key=run_key, run_id=OTHER_RUN_ID)
        )

    phase_key = _key(
        scope=ExecutionBudgetScope.PHASE,
        name="test.phase_budget",
        phase=WorkflowPhase.REQUIREMENTS,
    )
    with pytest.raises(BudgetOperationScopeMismatchError):
        await manager.consume(
            _request(
                "wrong_phase",
                key=phase_key,
                phase=WorkflowPhase.HOTEL_SELECTION,
            )
        )

    forged = BudgetConsumeRequest.model_construct(
        operation_id=_operation("forged"),
        key={"scope": "run", "name": "test.run_budget", "phase": None},
        amount=1,
    )
    with pytest.raises(ExecutionBudgetRequestValidationError):
        await manager.consume(forged)


@pytest.mark.asyncio
async def test_capacity_allows_existing_replay_but_rejects_new_operation() -> None:
    manager = _manager(
        plan=_plan(run_limit=0, phase_limit=0),
        max_operation_records=2,
    )
    key = _key()
    first_request = _request("first_rejected", key=key)
    second_request = _request("second_rejected", key=key)
    first = await manager.consume(first_request)
    second = await manager.consume(second_request)
    assert isinstance(first, BudgetExceeded)
    assert isinstance(second, BudgetExceeded)
    assert isinstance(await manager.consume(first_request), BudgetExceeded)
    assert isinstance(await manager.consume(second_request), BudgetExceeded)
    with pytest.raises(ExecutionBudgetCapacityExceededError):
        await manager.consume(_request("third_rejected", key=key))


def test_plan_and_limits_are_strictly_copied() -> None:
    plan = _plan()
    limits = ExecutionBudgetManagerLimits(max_operation_records=2)
    manager = InMemoryExecutionBudgetManager(plan=plan, limits=limits)
    object.__setattr__(plan, "run_id", OTHER_RUN_ID)
    object.__setattr__(limits, "max_operation_records", 1)

    async def exercise() -> None:
        result = await manager.consume(_request("copied"))
        assert isinstance(result, BudgetConsumed)
        assert result.snapshot.run_id == RUN_ID

    asyncio.run(exercise())

    forged_plan = ExecutionBudgetPlan.model_construct(
        schema_version="1.0",
        run_id=RUN_ID,
        limits=(object(),),
    )
    with pytest.raises(ExecutionBudgetPlanValidationError):
        InMemoryExecutionBudgetManager(
            plan=forged_plan,
            limits=ExecutionBudgetManagerLimits(max_operation_records=1),
        )


def test_loop_owner_rejects_cross_event_loop_use() -> None:
    manager = _manager()
    key = _key()

    async def first_call() -> None:
        snapshot = await manager.get_snapshot(key=key)
        assert snapshot.consumed == 0

    asyncio.run(first_call())
    with pytest.raises(ExecutionBudgetManagerUsageError):
        asyncio.run(first_call())


@pytest.mark.asyncio
async def test_concurrent_consumption_is_linearized_without_exceeding_limit() -> None:
    manager = _manager(plan=_plan(run_limit=1, phase_limit=1))
    key = _key()
    barrier = asyncio.Barrier(2)

    async def call(logical_step: str) -> object:
        await barrier.wait()
        return await manager.consume(_request(logical_step, key=key))

    first, second = await asyncio.gather(
        call("concurrent_one"),
        call("concurrent_two"),
    )
    assert sum(isinstance(result, BudgetConsumed) for result in (first, second)) == 1
    assert sum(isinstance(result, BudgetExceeded) for result in (first, second)) == 1
    snapshot = await manager.get_snapshot(key=key)
    assert snapshot.consumed == 1
    assert snapshot.remaining == 0


@pytest.mark.asyncio
async def test_snapshot_freshness_sorting_and_public_api() -> None:
    plan = ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=RUN_ID,
        limits=(
            ExecutionBudgetLimit(
                key=_key(
                    scope=ExecutionBudgetScope.PHASE,
                    name="z.phase",
                    phase=WorkflowPhase.HOTEL_SELECTION,
                ),
                limit=1,
            ),
            ExecutionBudgetLimit(
                key=_key(
                    scope=ExecutionBudgetScope.RUN,
                    name="z.run",
                    phase=None,
                ),
                limit=1,
            ),
            ExecutionBudgetLimit(
                key=_key(
                    scope=ExecutionBudgetScope.PHASE,
                    name="a.phase",
                    phase=WorkflowPhase.REQUIREMENTS,
                ),
                limit=1,
            ),
        ),
    )
    manager = _manager(plan=plan)
    snapshots = await manager.get_all_snapshots()
    assert [snapshot.key.name for snapshot in snapshots] == [
        "z.run",
        "a.phase",
        "z.phase",
    ]
    again = await manager.get_all_snapshots()
    assert snapshots is not again
    assert all(left is not right for left, right in zip(snapshots, again, strict=True))

    public_methods = {
        name for name in ExecutionBudgetManager.__dict__ if not name.startswith("_")
    }
    assert public_methods == {"consume", "get_snapshot", "get_all_snapshots"}
    assert all(iscoroutinefunction(getattr(ExecutionBudgetManager, name)) for name in public_methods)

    concrete_public_methods = {
        name
        for name in InMemoryExecutionBudgetManager.__dict__
        if not name.startswith("_")
    }
    assert concrete_public_methods == {"consume", "get_snapshot", "get_all_snapshots"}
    assert all(
        iscoroutinefunction(getattr(InMemoryExecutionBudgetManager, name))
        for name in concrete_public_methods
    )
