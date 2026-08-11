"""Reusable execution budget manager contract and public surface test."""

from __future__ import annotations

from datetime import datetime
from inspect import iscoroutinefunction, signature
from typing import Callable, get_type_hints
from uuid import UUID

from app.workflow_contracts.budgets.in_memory import InMemoryExecutionBudgetManager
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
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase


ExecutionBudgetManagerFactory = Callable[
    [ExecutionBudgetPlan, ExecutionBudgetManagerLimits],
    ExecutionBudgetManager,
]

RUN_ID = UUID("12345678-1234-5678-1234-567812345678")


def _operation(
    logical_step: str,
    *,
    run_id: UUID = RUN_ID,
    phase: WorkflowPhase = WorkflowPhase.REQUIREMENTS,
) -> OperationId:
    return OperationId(run_id=run_id, phase=phase, logical_step=logical_step)


def _key(
    *,
    scope: ExecutionBudgetScope,
    name: str,
    phase: WorkflowPhase | None,
) -> ExecutionBudgetKey:
    return ExecutionBudgetKey(scope=scope, name=name, phase=phase)


def _plan(*, run_id: UUID = RUN_ID) -> ExecutionBudgetPlan:
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
                limit=1,
            ),
            ExecutionBudgetLimit(
                key=_key(
                    scope=ExecutionBudgetScope.RUN,
                    name="test.run_budget",
                    phase=None,
                ),
                limit=3,
            ),
        ),
    )


def _request(
    logical_step: str,
    *,
    key: ExecutionBudgetKey,
    amount: int,
    phase: WorkflowPhase = WorkflowPhase.REQUIREMENTS,
) -> BudgetConsumeRequest:
    return BudgetConsumeRequest(
        operation_id=_operation(logical_step, phase=phase),
        key=key,
        amount=amount,
    )


def _manager(
    factory: ExecutionBudgetManagerFactory,
    *,
    plan: ExecutionBudgetPlan | None = None,
    max_operation_records: int = 16,
) -> ExecutionBudgetManager:
    return factory(
        _plan() if plan is None else plan,
        ExecutionBudgetManagerLimits(max_operation_records=max_operation_records),
    )


async def assert_execution_budget_manager_contract(
    factory: ExecutionBudgetManagerFactory,
) -> None:
    plan = _plan()
    phase_key = plan.limits[0].key
    run_key = plan.limits[1].key
    manager = _manager(factory, plan=plan)

    initial = await manager.get_all_snapshots()
    assert [snapshot.key for snapshot in initial] == [run_key, phase_key]
    assert all(snapshot.consumed == 0 for snapshot in initial)

    first_request = _request("first", key=run_key, amount=1)
    applied = await manager.consume(first_request)
    assert isinstance(applied, BudgetConsumed)
    assert applied.disposition == "applied"
    assert applied.snapshot.consumed == 1

    second = await manager.consume(_request("second", key=run_key, amount=1))
    assert isinstance(second, BudgetConsumed)
    replay = await manager.consume(first_request)
    assert isinstance(replay, BudgetConsumed)
    assert replay.disposition == "replayed"
    assert replay.snapshot == applied.snapshot
    assert replay.snapshot is not applied.snapshot

    rejected_request = _request("rejected", key=phase_key, amount=2)
    rejected = await manager.consume(rejected_request)
    assert isinstance(rejected, BudgetExceeded)
    assert rejected.disposition == "rejected"
    rejected_snapshot = rejected.snapshot
    phase_applied = await manager.consume(
        _request("phase_after_rejected", key=phase_key, amount=1)
    )
    assert isinstance(phase_applied, BudgetConsumed)
    assert phase_applied.snapshot.consumed == 1
    rejected_replay = await manager.consume(rejected_request)
    assert isinstance(rejected_replay, BudgetExceeded)
    assert rejected_replay.disposition == "replayed"
    assert rejected_replay.snapshot == rejected_snapshot
    assert rejected_replay.snapshot is not rejected_snapshot
    current_phase_snapshot = await manager.get_snapshot(key=phase_key)
    assert rejected_replay.snapshot != current_phase_snapshot
    assert current_phase_snapshot == phase_applied.snapshot
    assert current_phase_snapshot.consumed == 1
    assert current_phase_snapshot.remaining == 0

    try:
        await manager.consume(
            _request("first", key=phase_key, amount=1)
        )
    except BudgetConsumeConflictError:
        pass
    else:
        raise AssertionError("configured-key conflict was not raised")

    with __import__("contextlib").suppress(BudgetConsumeConflictError):
        await manager.consume(_request("first", key=run_key, amount=2))
        raise AssertionError("amount conflict was not raised")

    unknown_key = _key(
        scope=ExecutionBudgetScope.RUN,
        name="test.unknown_budget",
        phase=None,
    )
    try:
        await manager.consume(_request("unknown", key=unknown_key, amount=1))
    except BudgetKeyNotConfiguredError:
        pass
    else:
        raise AssertionError("unknown key was not rejected")
    try:
        await manager.get_snapshot(key=unknown_key)
    except BudgetKeyNotConfiguredError:
        pass
    else:
        raise AssertionError("unconfigured snapshot key was not rejected")

    try:
        await manager.consume(
            _request(
                "wrong_run",
                key=run_key,
                amount=1,
            ).model_copy(
                update={
                    "operation_id": _operation(
                        "wrong_run",
                        run_id=UUID("12345678-1234-5678-1234-567812345679"),
                    )
                }
            )
        )
    except BudgetOperationScopeMismatchError:
        pass
    else:
        raise AssertionError("run mismatch was not rejected")

    try:
        await manager.consume(
            _request(
                "wrong_phase",
                key=phase_key,
                amount=1,
                phase=WorkflowPhase.HOTEL_SELECTION,
            )
        )
    except BudgetOperationScopeMismatchError:
        pass
    else:
        raise AssertionError("phase mismatch was not rejected")

    capacity_plan = ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=RUN_ID,
        limits=(
            ExecutionBudgetLimit(key=run_key, limit=0),
        ),
    )
    capacity_manager = _manager(
        factory,
        plan=capacity_plan,
        max_operation_records=1,
    )
    capacity_request = _request("capacity_first", key=run_key, amount=1)
    first_rejected = await capacity_manager.consume(capacity_request)
    assert isinstance(first_rejected, BudgetExceeded)
    assert isinstance(await capacity_manager.consume(capacity_request), BudgetExceeded)
    try:
        await capacity_manager.consume(
            _request("capacity_second", key=run_key, amount=1)
        )
    except ExecutionBudgetCapacityExceededError:
        pass
    else:
        raise AssertionError("operation record capacity was not enforced")

    snapshot = await manager.get_snapshot(key=run_key)
    assert snapshot.consumed == 2
    assert snapshot.remaining == 1
    second_snapshot = await manager.get_snapshot(key=run_key)
    assert snapshot is not second_snapshot


async def test_in_memory_execution_budget_manager_contract() -> None:
    await assert_execution_budget_manager_contract(
        lambda plan, limits: InMemoryExecutionBudgetManager(
            plan=plan,
            limits=limits,
        )
    )


def test_execution_budget_manager_protocol_surface() -> None:
    public_methods = {
        name for name in ExecutionBudgetManager.__dict__ if not name.startswith("_")
    }
    assert public_methods == {"consume", "get_snapshot", "get_all_snapshots"}
    for name in public_methods:
        assert iscoroutinefunction(getattr(ExecutionBudgetManager, name))

    consume_signature = signature(ExecutionBudgetManager.consume)
    snapshot_signature = signature(ExecutionBudgetManager.get_snapshot)
    all_signature = signature(ExecutionBudgetManager.get_all_snapshots)
    assert list(consume_signature.parameters) == ["self", "request"]
    assert list(snapshot_signature.parameters) == ["self", "key"]
    assert snapshot_signature.parameters["key"].kind.name == "KEYWORD_ONLY"
    assert list(all_signature.parameters) == ["self"]

    consume_hints = get_type_hints(ExecutionBudgetManager.consume)
    snapshot_hints = get_type_hints(ExecutionBudgetManager.get_snapshot)
    all_hints = get_type_hints(ExecutionBudgetManager.get_all_snapshots)
    assert consume_hints["request"] is BudgetConsumeRequest
    assert snapshot_hints["key"] is ExecutionBudgetKey
    assert snapshot_hints["return"] is ExecutionBudgetSnapshot
    assert all_hints["return"] == tuple[ExecutionBudgetSnapshot, ...]
