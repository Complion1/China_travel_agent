"""Strict DTOs for safe workflow events and sink outcomes."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.workflow_contracts.artifacts.models import SAFE_INTEGER_MAX
from app.workflow_contracts.identifiers import (
    EventDeliveryFailureCode,
    OperationId,
    SafeCounterName,
    TerminalCode,
    WorkflowEventType,
    WorkflowPhase,
)


class SafeCounterDelta(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: SafeCounterName
    delta: int = Field(ge=1, le=SAFE_INTEGER_MAX)


class SafeWorkflowEvent(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"]
    event_id: UUID
    sequence_number: int = Field(ge=1, le=SAFE_INTEGER_MAX)
    occurred_at: datetime
    run_id: UUID
    trace_id: UUID
    operation_id: OperationId | None
    phase: WorkflowPhase
    event_type: WorkflowEventType
    counter_deltas: tuple[SafeCounterDelta, ...]
    duration_ms: int | None = Field(ge=0, le=SAFE_INTEGER_MAX)
    terminal_code: TerminalCode | None

    @model_validator(mode="after")
    def validate_event_invariants(self) -> "SafeWorkflowEvent":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != timedelta(0):
            raise ValueError("occurred_at must be UTC")
        names = tuple(counter.name for counter in self.counter_deltas)
        if len(names) != len(set(names)) or names != tuple(sorted(names)):
            raise ValueError("counter names must be unique and sorted")
        if self.operation_id is not None:
            if self.operation_id.run_id != self.run_id:
                raise ValueError("operation run does not match event run")
            if self.operation_id.phase is not self.phase:
                raise ValueError("operation phase does not match event phase")
        return self


class EventEmitted(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    outcome_type: Literal["emitted"] = "emitted"
    disposition: Literal["applied", "replayed"]
    event_id: UUID


class EventDeliveryFailed(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    outcome_type: Literal["delivery_failed"] = "delivery_failed"
    event_id: UUID
    failure_code: EventDeliveryFailureCode


EventEmissionOutcome = Annotated[
    EventEmitted | EventDeliveryFailed,
    Field(discriminator="outcome_type"),
]
