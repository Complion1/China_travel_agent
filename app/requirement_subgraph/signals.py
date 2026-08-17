"""Safe synchronous signals emitted by the requirement subgraph."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal, Protocol, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.requirement_subgraph.errors import RequirementSubgraphStateError
from app.workflow_contracts import OperationId, WorkflowPhase


_PAUSE_STEP = re.compile(r"^requirement_pause_[0-9a-f]{32}$")
_BUDGET_STEP = re.compile(r"^clarification_round_[0-9a-f]{32}$")


class StartAccepted(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    signal_type: Literal["start_accepted"] = "start_accepted"
    run_id: UUID
    trace_id: UUID


class ResumeAccepted(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    signal_type: Literal["resume_accepted"] = "resume_accepted"
    run_id: UUID
    trace_id: UUID


class NodeReplayed(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    signal_type: Literal["node_replayed"] = "node_replayed"
    run_id: UUID
    trace_id: UUID
    operation_id: OperationId

    @model_validator(mode="after")
    def validate_operation(self) -> "NodeReplayed":
        if (
            self.operation_id.run_id != self.run_id
            or self.operation_id.phase is not WorkflowPhase.REQUIREMENTS
            or _PAUSE_STEP.fullmatch(self.operation_id.logical_step) is None
        ):
            raise ValueError("node replay operation mismatch")
        return self


class BudgetConsumed(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    signal_type: Literal["budget_consumed"] = "budget_consumed"
    run_id: UUID
    trace_id: UUID
    operation_id: OperationId
    amount: Literal[1] = 1

    @model_validator(mode="after")
    def validate_operation(self) -> "BudgetConsumed":
        if (
            self.operation_id.run_id != self.run_id
            or self.operation_id.phase is not WorkflowPhase.REQUIREMENTS
            or _BUDGET_STEP.fullmatch(self.operation_id.logical_step) is None
        ):
            raise ValueError("budget consumed operation mismatch")
        return self


class BudgetExceeded(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    signal_type: Literal["budget_exceeded"] = "budget_exceeded"
    run_id: UUID
    trace_id: UUID
    operation_id: OperationId

    @model_validator(mode="after")
    def validate_operation(self) -> "BudgetExceeded":
        if (
            self.operation_id.run_id != self.run_id
            or self.operation_id.phase is not WorkflowPhase.REQUIREMENTS
            or _BUDGET_STEP.fullmatch(self.operation_id.logical_step) is None
        ):
            raise ValueError("budget exceeded operation mismatch")
        return self


RequirementSubgraphSignal: TypeAlias = Annotated[
    StartAccepted | ResumeAccepted | NodeReplayed | BudgetConsumed | BudgetExceeded,
    Field(discriminator="signal_type"),
]


class RequirementSubgraphSignalSink(Protocol):
    def accept(self, signal: RequirementSubgraphSignal) -> None: ...


_SIGNAL_TYPES = (StartAccepted, ResumeAccepted, NodeReplayed, BudgetConsumed, BudgetExceeded)
_SIGNAL_ADAPTER = TypeAdapter(RequirementSubgraphSignal)


class NoOpRequirementSubgraphSignalSink:
    def accept(self, signal: RequirementSubgraphSignal) -> None:
        if type(signal) not in _SIGNAL_TYPES:
            raise RequirementSubgraphStateError() from None
        try:
            tree = signal.model_dump(
                mode="python",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            first = _SIGNAL_ADAPTER.validate_python(tree, strict=True)
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
            )
            _SIGNAL_ADAPTER.validate_json(payload, strict=True)
        except Exception:
            raise RequirementSubgraphStateError() from None
        return None


__all__ = [
    "StartAccepted",
    "ResumeAccepted",
    "NodeReplayed",
    "BudgetConsumed",
    "BudgetExceeded",
    "RequirementSubgraphSignal",
    "RequirementSubgraphSignalSink",
    "NoOpRequirementSubgraphSignalSink",
]
