"""Execution budget manager protocol boundary."""

from __future__ import annotations

from typing import Protocol

from app.workflow_contracts.budgets.models import (
    BudgetConsumeOutcome,
    BudgetConsumeRequest,
    ExecutionBudgetKey,
    ExecutionBudgetSnapshot,
)


class ExecutionBudgetManager(Protocol):
    async def consume(self, request: BudgetConsumeRequest) -> BudgetConsumeOutcome: ...

    async def get_snapshot(
        self,
        *,
        key: ExecutionBudgetKey,
    ) -> ExecutionBudgetSnapshot: ...

    async def get_all_snapshots(self) -> tuple[ExecutionBudgetSnapshot, ...]: ...
