"""Fixed execution budget DTO and protocol vectors."""

from __future__ import annotations

from inspect import iscoroutinefunction, signature
from typing import get_type_hints
from uuid import UUID

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.workflow_contracts.budgets.manager import ExecutionBudgetManager
from app.workflow_contracts.budgets.models import (
    BudgetConsumeOutcome,
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
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
MAX_SAFE_INTEGER = 2**53 - 1


def _operation(phase: WorkflowPhase = WorkflowPhase.REQUIREMENTS) -> OperationId:
    return OperationId(
        run_id=RUN_ID,
        phase=phase,
        logical_step="budget_step",
    )


def _key(
    *,
    scope: ExecutionBudgetScope = ExecutionBudgetScope.RUN,
    name: str = "test.run_budget",
    phase: WorkflowPhase | None = None,
) -> ExecutionBudgetKey:
    return ExecutionBudgetKey(scope=scope, name=name, phase=phase)


def _snapshot(
    *,
    key: ExecutionBudgetKey | None = None,
    limit: int = 3,
    consumed: int = 1,
) -> ExecutionBudgetSnapshot:
    return ExecutionBudgetSnapshot(
        schema_version="1.0",
        run_id=RUN_ID,
        key=_key() if key is None else key,
        limit=limit,
        consumed=consumed,
        remaining=limit - consumed,
    )


def test_public_dtos_are_strict_frozen_forbid_and_required() -> None:
    dto_types = (
        ExecutionBudgetKey,
        ExecutionBudgetLimit,
        ExecutionBudgetPlan,
        ExecutionBudgetSnapshot,
        BudgetConsumeRequest,
        BudgetConsumed,
        BudgetExceeded,
        ExecutionBudgetManagerLimits,
    )
    for dto_type in dto_types:
        assert dict(dto_type.model_config) == {
            "strict": True,
            "frozen": True,
            "extra": "forbid",
        }
        assert all(field.is_required() or field_name == "outcome_type" for field_name, field in dto_type.model_fields.items())

    with pytest.raises(ValidationError):
        ExecutionBudgetKey(
            scope=ExecutionBudgetScope.RUN,
            name="test.run_budget",
        )
    with pytest.raises(ValidationError):
        ExecutionBudgetLimit(key=_key(), limit=0, extra="forbidden")
    with pytest.raises(ValidationError):
        key = _key()
        key.name = "changed"  # type: ignore[misc]


def test_scope_duplicate_and_integer_boundaries() -> None:
    with pytest.raises(ValidationError):
        _key(scope=ExecutionBudgetScope.RUN, phase=WorkflowPhase.REQUIREMENTS)
    with pytest.raises(ValidationError):
        _key(
            scope=ExecutionBudgetScope.PHASE,
            phase=None,
        )

    limit = ExecutionBudgetLimit(key=_key(), limit=MAX_SAFE_INTEGER)
    assert limit.limit == MAX_SAFE_INTEGER
    with pytest.raises(ValidationError):
        ExecutionBudgetLimit(key=_key(), limit=MAX_SAFE_INTEGER + 1)
    with pytest.raises(ValidationError):
        BudgetConsumeRequest(
            operation_id=_operation(),
            key=_key(),
            amount=0,
        )
    assert BudgetConsumeRequest(
        operation_id=_operation(),
        key=_key(),
        amount=MAX_SAFE_INTEGER,
    ).amount == MAX_SAFE_INTEGER

    duplicate = ExecutionBudgetLimit(key=_key(), limit=1)
    with pytest.raises(ValidationError):
        ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=RUN_ID,
            limits=(duplicate, duplicate),
        )


def test_snapshot_arithmetic_and_outcome_discriminator() -> None:
    assert _snapshot(limit=0, consumed=0).remaining == 0
    with pytest.raises(ValidationError):
        ExecutionBudgetSnapshot(
            schema_version="1.0",
            run_id=RUN_ID,
            key=_key(),
            limit=1,
            consumed=2,
            remaining=0,
        )
    with pytest.raises(ValidationError):
        ExecutionBudgetSnapshot(
            schema_version="1.0",
            run_id=RUN_ID,
            key=_key(),
            limit=1,
            consumed=0,
            remaining=0,
        )

    snapshot = _snapshot()
    consumed = BudgetConsumed(
        disposition="applied",
        operation_id=_operation(),
        key=snapshot.key,
        amount=1,
        snapshot=snapshot,
    )
    exceeded = BudgetExceeded(
        disposition="rejected",
        operation_id=_operation(),
        key=snapshot.key,
        amount=3,
        snapshot=snapshot,
    )
    adapter = TypeAdapter(BudgetConsumeOutcome)
    assert type(adapter.validate_python(consumed, strict=True)) is BudgetConsumed
    assert type(adapter.validate_python(exceeded, strict=True)) is BudgetExceeded
    assert type(
        adapter.validate_json(
            consumed.model_dump_json(),
            strict=True,
        )
    ) is BudgetConsumed


def test_execution_budget_manager_protocol_surface() -> None:
    public_methods = {
        name for name in ExecutionBudgetManager.__dict__ if not name.startswith("_")
    }
    assert public_methods == {"consume", "get_snapshot", "get_all_snapshots"}
    for method_name in public_methods:
        method = getattr(ExecutionBudgetManager, method_name)
        assert iscoroutinefunction(method)

    consume_signature = signature(ExecutionBudgetManager.consume)
    snapshot_signature = signature(ExecutionBudgetManager.get_snapshot)
    all_signature = signature(ExecutionBudgetManager.get_all_snapshots)
    assert list(consume_signature.parameters) == ["self", "request"]
    assert list(snapshot_signature.parameters) == ["self", "key"]
    assert snapshot_signature.parameters["key"].kind.name == "KEYWORD_ONLY"
    assert list(all_signature.parameters) == ["self"]

    consume_hints = get_type_hints(
        ExecutionBudgetManager.consume,
        include_extras=True,
    )
    snapshot_hints = get_type_hints(
        ExecutionBudgetManager.get_snapshot,
        include_extras=True,
    )
    all_hints = get_type_hints(
        ExecutionBudgetManager.get_all_snapshots,
        include_extras=True,
    )
    assert consume_hints["request"] is BudgetConsumeRequest
    assert consume_hints["return"] == BudgetConsumeOutcome
    assert snapshot_hints["key"] is ExecutionBudgetKey
    assert snapshot_hints["return"] is ExecutionBudgetSnapshot
    assert all_hints["return"] == tuple[ExecutionBudgetSnapshot, ...]


def test_plan_and_snapshot_are_fresh_models() -> None:
    plan = ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=RUN_ID,
        limits=(ExecutionBudgetLimit(key=_key(), limit=2),),
    )
    copied = ExecutionBudgetPlan.model_validate(plan.model_dump(mode="python"), strict=True)
    assert copied == plan
    assert copied is not plan
    snapshot = _snapshot()
    copied_snapshot = ExecutionBudgetSnapshot.model_validate(
        snapshot.model_dump(mode="python"),
        strict=True,
    )
    assert copied_snapshot == snapshot
    assert copied_snapshot is not snapshot
