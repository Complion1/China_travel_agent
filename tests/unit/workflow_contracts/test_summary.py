"""RunSummary and safe counter contract tests."""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from app.workflow_contracts.budgets.models import (
    ExecutionBudgetKey,
    ExecutionBudgetSnapshot,
    ExecutionBudgetScope,
)
from app.workflow_contracts.identifiers import WorkflowPhase
from app.workflow_contracts.summary import RunSummary, SafeCounterValue


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
OTHER_RUN_ID = UUID("22345678-1234-5678-1234-567812345678")
TRACE_ID = UUID("32345678-1234-5678-1234-567812345678")


def _key(
    scope: ExecutionBudgetScope,
    name: str,
    phase: WorkflowPhase | None,
) -> ExecutionBudgetKey:
    return ExecutionBudgetKey(scope=scope, name=name, phase=phase)


def _snapshot(
    key: ExecutionBudgetKey,
    *,
    limit: int = 3,
    consumed: int = 0,
    run_id: UUID = RUN_ID,
) -> ExecutionBudgetSnapshot:
    return ExecutionBudgetSnapshot(
        schema_version="1.0",
        run_id=run_id,
        key=key,
        limit=limit,
        consumed=consumed,
        remaining=limit - consumed,
    )


def _summary(
    *,
    counters: tuple[SafeCounterValue, ...] = (
        SafeCounterValue(name="artifact.cache_hit", value=2),
        SafeCounterValue(name="workflow.started", value=1),
    ),
    budget_snapshots: tuple[ExecutionBudgetSnapshot, ...] = (),
    cache_hits: int = 2,
) -> RunSummary:
    return RunSummary(
        schema_version="1.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        terminal_code="workflow_completed",
        duration_ms=10,
        cache_hits=cache_hits,
        event_delivery_failures=0,
        counters=counters,
        budget_snapshots=budget_snapshots,
    )


def test_summary_dtos_are_strict_frozen_forbid_and_required() -> None:
    for model in (SafeCounterValue, RunSummary):
        assert model.model_config["strict"] is True
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"
        assert all(field.is_required() for field in model.model_fields.values())


def test_summary_validates_cache_hits_and_counter_sorting() -> None:
    summary = _summary()
    assert summary.cache_hits == 2
    assert _summary(
        counters=(SafeCounterValue(name="workflow.started", value=1),),
        cache_hits=0,
    ).cache_hits == 0
    with pytest.raises(ValidationError):
        _summary(cache_hits=1)
    with pytest.raises(ValidationError):
        _summary(
            counters=(
                SafeCounterValue(name="workflow.started", value=1),
                SafeCounterValue(name="artifact.cache_hit", value=2),
            )
        )
    with pytest.raises(ValidationError):
        _summary(
            counters=(
                SafeCounterValue(name="artifact.cache_hit", value=1),
                SafeCounterValue(name="artifact.cache_hit", value=2),
            )
        )


def test_summary_budget_snapshots_match_run_are_unique_and_sorted() -> None:
    run_key = _key(ExecutionBudgetScope.RUN, "z.run", None)
    requirements_key = _key(
        ExecutionBudgetScope.PHASE,
        "a.phase",
        WorkflowPhase.REQUIREMENTS,
    )
    hotel_key = _key(
        ExecutionBudgetScope.PHASE,
        "z.phase",
        WorkflowPhase.HOTEL_SELECTION,
    )
    snapshots = (
        _snapshot(run_key),
        _snapshot(requirements_key),
        _snapshot(hotel_key),
    )
    assert _summary(budget_snapshots=snapshots).budget_snapshots == snapshots
    with pytest.raises(ValidationError):
        _summary(budget_snapshots=tuple(reversed(snapshots)))
    with pytest.raises(ValidationError):
        _summary(budget_snapshots=(snapshots[0], snapshots[0]))
    with pytest.raises(ValidationError):
        _summary(budget_snapshots=(_snapshot(run_key, run_id=OTHER_RUN_ID),))


def test_summary_integer_boundaries_and_frozen_fields() -> None:
    with pytest.raises(ValidationError):
        SafeCounterValue(name="workflow.started", value=-1)
    with pytest.raises(ValidationError):
        SafeCounterValue(name="workflow.started", value=2**53)
    with pytest.raises(ValidationError):
        _summary(cache_hits=3)


def test_summary_strict_json_round_trip_is_fresh_and_deterministic() -> None:
    run_key = _key(ExecutionBudgetScope.RUN, "z.run", None)
    phase_key = _key(
        ExecutionBudgetScope.PHASE,
        "a.phase",
        WorkflowPhase.REQUIREMENTS,
    )
    summary = _summary(
        budget_snapshots=(_snapshot(run_key), _snapshot(phase_key)),
    )
    adapter = TypeAdapter(RunSummary)
    tree = summary.model_dump(
        mode="python",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    first = adapter.validate_python(tree, strict=True)
    json_tree = first.model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    payload = json.dumps(
        json_tree,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    restored = adapter.validate_json(payload, strict=True)
    restored_again = adapter.validate_json(payload, strict=True)

    assert restored == summary
    assert restored is not summary
    assert restored.counters is not summary.counters
    assert restored.budget_snapshots is not summary.budget_snapshots
    assert all(
        left is not right
        for left, right in zip(restored.counters, summary.counters, strict=True)
    )
    assert all(
        left is not right
        for left, right in zip(
            restored.budget_snapshots,
            summary.budget_snapshots,
            strict=True,
        )
    )
    assert restored == restored_again
