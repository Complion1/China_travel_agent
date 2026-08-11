"""Strict execution budget DTOs."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.workflow_contracts.identifiers import ExecutionBudgetName, OperationId, WorkflowPhase


_SAFE_INTEGER_MAX = 2**53 - 1


class ExecutionBudgetScope(str, Enum):
    RUN = "run"
    PHASE = "phase"


class ExecutionBudgetKey(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    scope: ExecutionBudgetScope
    name: ExecutionBudgetName
    phase: WorkflowPhase | None

    @model_validator(mode="after")
    def validate_scope(self) -> "ExecutionBudgetKey":
        if self.scope is ExecutionBudgetScope.RUN and self.phase is not None:
            raise ValueError("run scope requires phase=None")
        if self.scope is ExecutionBudgetScope.PHASE and self.phase is None:
            raise ValueError("phase scope requires a phase")
        return self


class ExecutionBudgetLimit(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    key: ExecutionBudgetKey
    limit: int = Field(ge=0, le=_SAFE_INTEGER_MAX)


class ExecutionBudgetPlan(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"]
    run_id: UUID
    limits: tuple[ExecutionBudgetLimit, ...]

    @model_validator(mode="after")
    def reject_duplicate_keys(self) -> "ExecutionBudgetPlan":
        keys = [
            (limit.key.scope, limit.key.name, limit.key.phase)
            for limit in self.limits
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate execution budget key")
        return self


class ExecutionBudgetSnapshot(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"]
    run_id: UUID
    key: ExecutionBudgetKey
    limit: int = Field(ge=0, le=_SAFE_INTEGER_MAX)
    consumed: int = Field(ge=0, le=_SAFE_INTEGER_MAX)
    remaining: int = Field(ge=0, le=_SAFE_INTEGER_MAX)

    @model_validator(mode="after")
    def validate_arithmetic(self) -> "ExecutionBudgetSnapshot":
        if self.consumed > self.limit:
            raise ValueError("consumed exceeds limit")
        if self.remaining != self.limit - self.consumed:
            raise ValueError("remaining does not match consumed")
        return self


class BudgetConsumeRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    operation_id: OperationId
    key: ExecutionBudgetKey
    amount: int = Field(ge=1, le=_SAFE_INTEGER_MAX)


class BudgetConsumed(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    outcome_type: Literal["consumed"] = "consumed"
    disposition: Literal["applied", "replayed"]
    operation_id: OperationId
    key: ExecutionBudgetKey
    amount: int = Field(ge=1, le=_SAFE_INTEGER_MAX)
    snapshot: ExecutionBudgetSnapshot


class BudgetExceeded(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    outcome_type: Literal["exceeded"] = "exceeded"
    disposition: Literal["rejected", "replayed"]
    operation_id: OperationId
    key: ExecutionBudgetKey
    amount: int = Field(ge=1, le=_SAFE_INTEGER_MAX)
    snapshot: ExecutionBudgetSnapshot


BudgetConsumeOutcome = Annotated[
    BudgetConsumed | BudgetExceeded,
    Field(discriminator="outcome_type"),
]


class ExecutionBudgetManagerLimits(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    max_operation_records: int = Field(ge=1, le=_SAFE_INTEGER_MAX)
