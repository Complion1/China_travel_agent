"""Process-local execution budget manager reference implementation."""

from __future__ import annotations

import asyncio
import json
import warnings
from dataclasses import dataclass
from typing import TypeVar, cast

from pydantic import BaseModel, TypeAdapter

from app.workflow_contracts.budgets.models import (
    BudgetConsumed,
    BudgetConsumeOutcome,
    BudgetConsumeRequest,
    BudgetExceeded,
    ExecutionBudgetKey,
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


DEMO_EXECUTION_BUDGET_MANAGER_LIMITS = ExecutionBudgetManagerLimits(
    max_operation_records=4_096,
)


TModel = TypeVar("TModel", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class _BudgetOperationRecord:
    key: ExecutionBudgetKey
    amount: int
    outcome: BudgetConsumeOutcome


def _strict_copy(
    model: BaseModel,
    model_type: type[TModel],
    error_type: type[Exception],
) -> TModel:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            python_tree = model.model_dump(
                mode="python",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            first = TypeAdapter(model_type).validate_python(
                python_tree,
                strict=True,
            )
            json_tree = first.model_dump(
                mode="json",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            encoded = json.dumps(
                json_tree,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            second = TypeAdapter(model_type).validate_json(encoded, strict=True)
    except Exception:
        raise error_type() from None
    return cast(TModel, second)


def _key_token(key: ExecutionBudgetKey) -> tuple[ExecutionBudgetScope, str, WorkflowPhase | None]:
    return key.scope, key.name, key.phase


def _copy_snapshot(snapshot: ExecutionBudgetSnapshot) -> ExecutionBudgetSnapshot:
    return _strict_copy(
        snapshot,
        ExecutionBudgetSnapshot,
        ExecutionBudgetManagerUsageError,
    )


def _replayed_outcome(
    record: _BudgetOperationRecord,
    request: BudgetConsumeRequest,
) -> BudgetConsumeOutcome:
    snapshot = _copy_snapshot(record.outcome.snapshot)
    if isinstance(record.outcome, BudgetConsumed):
        return BudgetConsumed(
            disposition="replayed",
            operation_id=request.operation_id,
            key=request.key,
            amount=request.amount,
            snapshot=snapshot,
        )
    return BudgetExceeded(
        disposition="replayed",
        operation_id=request.operation_id,
        key=request.key,
        amount=request.amount,
        snapshot=snapshot,
    )


class InMemoryExecutionBudgetManager:
    """A bounded, process-local execution budget ledger."""

    __slots__ = (
        "_plan",
        "_limits",
        "_snapshots",
        "_records",
        "_lock",
        "_loop",
    )

    def __init__(
        self,
        *,
        plan: ExecutionBudgetPlan,
        limits: ExecutionBudgetManagerLimits,
    ) -> None:
        self._plan = _strict_copy(
            plan,
            ExecutionBudgetPlan,
            ExecutionBudgetPlanValidationError,
        )
        self._limits = _strict_copy(
            limits,
            ExecutionBudgetManagerLimits,
            ExecutionBudgetPlanValidationError,
        )
        self._snapshots: dict[
            tuple[ExecutionBudgetScope, str, WorkflowPhase | None],
            ExecutionBudgetSnapshot,
        ] = {}
        for entry in self._plan.limits:
            snapshot = ExecutionBudgetSnapshot(
                schema_version=self._plan.schema_version,
                run_id=self._plan.run_id,
                key=entry.key,
                limit=entry.limit,
                consumed=0,
                remaining=entry.limit,
            )
            self._snapshots[_key_token(entry.key)] = snapshot
        self._records: dict[OperationId, _BudgetOperationRecord] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _ensure_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise ExecutionBudgetManagerUsageError() from None
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise ExecutionBudgetManagerUsageError() from None

    async def consume(self, request: BudgetConsumeRequest) -> BudgetConsumeOutcome:
        self._ensure_loop()
        fresh_request = _strict_copy(
            request,
            BudgetConsumeRequest,
            ExecutionBudgetRequestValidationError,
        )
        token = _key_token(fresh_request.key)
        if token not in self._snapshots:
            raise BudgetKeyNotConfiguredError() from None
        if fresh_request.operation_id.run_id != self._plan.run_id:
            raise BudgetOperationScopeMismatchError() from None
        if (
            fresh_request.key.scope is ExecutionBudgetScope.PHASE
            and fresh_request.key.phase is not fresh_request.operation_id.phase
        ):
            raise BudgetOperationScopeMismatchError() from None

        async with self._lock:
            record = self._records.get(fresh_request.operation_id)
            if record is not None:
                if (
                    record.key == fresh_request.key
                    and record.amount == fresh_request.amount
                ):
                    return _replayed_outcome(record, fresh_request)
                raise BudgetConsumeConflictError() from None

            if len(self._records) >= self._limits.max_operation_records:
                raise ExecutionBudgetCapacityExceededError() from None

            current = self._snapshots[token]
            if current.consumed + fresh_request.amount <= current.limit:
                next_snapshot = ExecutionBudgetSnapshot(
                    schema_version=current.schema_version,
                    run_id=current.run_id,
                    key=current.key,
                    limit=current.limit,
                    consumed=current.consumed + fresh_request.amount,
                    remaining=current.remaining - fresh_request.amount,
                )
                outcome: BudgetConsumeOutcome = BudgetConsumed(
                    disposition="applied",
                    operation_id=fresh_request.operation_id,
                    key=fresh_request.key,
                    amount=fresh_request.amount,
                    snapshot=next_snapshot,
                )
            else:
                next_snapshot = _copy_snapshot(current)
                outcome = BudgetExceeded(
                    disposition="rejected",
                    operation_id=fresh_request.operation_id,
                    key=fresh_request.key,
                    amount=fresh_request.amount,
                    snapshot=next_snapshot,
                )
            self._snapshots[token] = next_snapshot
            self._records[fresh_request.operation_id] = _BudgetOperationRecord(
                key=fresh_request.key,
                amount=fresh_request.amount,
                outcome=outcome,
            )
            return _strict_copy(
                outcome,
                BudgetConsumed if isinstance(outcome, BudgetConsumed) else BudgetExceeded,
                ExecutionBudgetManagerUsageError,
            )

    async def get_snapshot(
        self,
        *,
        key: ExecutionBudgetKey,
    ) -> ExecutionBudgetSnapshot:
        self._ensure_loop()
        fresh_key = _strict_copy(
            key,
            ExecutionBudgetKey,
            ExecutionBudgetRequestValidationError,
        )
        token = _key_token(fresh_key)
        if token not in self._snapshots:
            raise BudgetKeyNotConfiguredError() from None
        async with self._lock:
            return _copy_snapshot(self._snapshots[token])

    async def get_all_snapshots(self) -> tuple[ExecutionBudgetSnapshot, ...]:
        self._ensure_loop()
        scope_order = {
            ExecutionBudgetScope.RUN: 0,
            ExecutionBudgetScope.PHASE: 1,
        }
        phase_order: dict[WorkflowPhase | None, int] = {None: -1}
        phase_order.update({phase: index for index, phase in enumerate(WorkflowPhase)})
        async with self._lock:
            snapshots = sorted(
                self._snapshots.values(),
                key=lambda snapshot: (
                    scope_order[snapshot.key.scope],
                    phase_order[snapshot.key.phase],
                    snapshot.key.name,
                ),
            )
            return tuple(_copy_snapshot(snapshot) for snapshot in snapshots)
