"""Run summary DTOs."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.workflow_contracts.budgets.models import ExecutionBudgetSnapshot
from app.workflow_contracts.identifiers import SafeCounterName, TerminalCode, WorkflowPhase


SAFE_INTEGER_MAX = 2**53 - 1
_PHASE_ORDER = {phase: index for index, phase in enumerate(WorkflowPhase)}


class SafeCounterValue(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: SafeCounterName
    value: int = Field(ge=0, le=SAFE_INTEGER_MAX)


class RunSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"]
    run_id: UUID
    trace_id: UUID
    terminal_code: TerminalCode
    duration_ms: int = Field(ge=0, le=SAFE_INTEGER_MAX)
    cache_hits: int = Field(ge=0, le=SAFE_INTEGER_MAX)
    event_delivery_failures: int = Field(ge=0, le=SAFE_INTEGER_MAX)
    counters: tuple[SafeCounterValue, ...]
    budget_snapshots: tuple[ExecutionBudgetSnapshot, ...]

    @model_validator(mode="after")
    def validate_summary_invariants(self) -> "RunSummary":
        counter_names = tuple(counter.name for counter in self.counters)
        if len(counter_names) != len(set(counter_names)):
            raise ValueError("duplicate counter name")
        if counter_names != tuple(sorted(counter_names)):
            raise ValueError("counters are not sorted")

        budget_keys = tuple(snapshot.key for snapshot in self.budget_snapshots)
        if len(budget_keys) != len(set(budget_keys)):
            raise ValueError("duplicate budget key")
        if any(snapshot.run_id != self.run_id for snapshot in self.budget_snapshots):
            raise ValueError("budget snapshot run mismatch")

        def budget_sort_key(snapshot: ExecutionBudgetSnapshot) -> tuple[int, int, int, str]:
            key = snapshot.key
            phase_is_none = key.phase is None
            phase_order = _PHASE_ORDER.get(key.phase, len(_PHASE_ORDER))
            return (
                0 if key.scope.value == "run" else 1,
                1 if phase_is_none else 0,
                phase_order,
                key.name,
            )

        budget_keys_in_order = tuple(
            budget_sort_key(snapshot) for snapshot in self.budget_snapshots
        )
        if budget_keys_in_order != tuple(sorted(budget_keys_in_order)):
            raise ValueError("budget snapshots are not sorted")

        cache_counter = next(
            (counter.value for counter in self.counters if counter.name == "artifact.cache_hit"),
            0,
        )
        if self.cache_hits != cache_counter:
            raise ValueError("cache_hits does not match artifact.cache_hit")
        return self
