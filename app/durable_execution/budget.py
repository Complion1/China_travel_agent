"""Module 04 ``ExecutionBudgetManager`` 的 PostgreSQL 持久实现。

Manager 只保留 run/lease/fencing 的私有绑定；真正的 operation 与 journal 原子性
由 :mod:`app.durable_execution.repository` 在一个短事务中完成。
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import TypeAdapter

from app.durable_execution.errors import DurableExecutionUsageError
from app.durable_execution.repository import DurableExecutionRepository
from app.workflow_contracts import (
    BudgetConsumeOutcome,
    BudgetConsumeRequest,
    ExecutionBudgetKey,
    ExecutionBudgetManager,
    ExecutionBudgetSnapshot,
)
from app.workflow_contracts.errors import (
    ExecutionBudgetManagerUsageError,
    ExecutionBudgetRequestValidationError,
)


def _strict_copy(value, model_type, error_type):
    try:
        python_tree = value.model_dump(
            mode="python",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        return TypeAdapter(model_type).validate_python(python_tree, strict=True)
    except Exception:
        raise error_type() from None


class PersistentExecutionBudgetManager:
    """实现 Module 04 三方法合同的 run-bound manager。"""

    __slots__ = ("_repository", "_run_id", "_owner", "_fencing_token", "_node_name", "_loop")

    def __init__(
        self,
        *,
        repository: DurableExecutionRepository,
        run_id: UUID,
        owner: UUID,
        fencing_token: int,
        node_name: str | None = None,
    ) -> None:
        if (
            not all(
                callable(getattr(repository, name, None))
                for name in (
                    "_consume_budget",
                    "get_budget_snapshot",
                    "get_all_budget_snapshots",
                )
            )
            or type(run_id) is not UUID
            or type(owner) is not UUID
            or type(fencing_token) is not int
            or isinstance(fencing_token, bool)
            or fencing_token < 1
        ):
            raise DurableExecutionUsageError() from None
        self._repository = repository
        self._run_id = run_id
        self._owner = owner
        self._fencing_token = fencing_token
        self._node_name = node_name
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
        fresh = _strict_copy(
            request,
            BudgetConsumeRequest,
            ExecutionBudgetRequestValidationError,
        )
        if fresh.operation_id.run_id != self._run_id:
            raise ExecutionBudgetManagerUsageError() from None
        if fresh.key.scope.value == "phase" and fresh.key.phase is not fresh.operation_id.phase:
            raise ExecutionBudgetManagerUsageError() from None
        kwargs = {
            "request": fresh,
            "owner": self._owner,
            "fencing_token": self._fencing_token,
        }
        if self._node_name is not None:
            kwargs["node_name"] = self._node_name
        return await self._repository._consume_budget(**kwargs)

    async def get_snapshot(
        self,
        *,
        key: ExecutionBudgetKey,
    ) -> ExecutionBudgetSnapshot:
        self._ensure_loop()
        fresh = _strict_copy(key, ExecutionBudgetKey, ExecutionBudgetRequestValidationError)
        return await self._repository.get_budget_snapshot(
            run_id=self._run_id,
            key=fresh,
            owner=self._owner,
            fencing_token=self._fencing_token,
        )

    async def get_all_snapshots(self) -> tuple[ExecutionBudgetSnapshot, ...]:
        self._ensure_loop()
        return await self._repository.get_all_budget_snapshots(
            run_id=self._run_id,
            owner=self._owner,
            fencing_token=self._fencing_token,
        )


DurableExecutionBudgetManager = PersistentExecutionBudgetManager
