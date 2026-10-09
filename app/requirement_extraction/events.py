from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.llm_runtime import InvocationMetadata, LLMRuntimeError


class ExtractionInvocationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"] = "1.0"
    turn_id: UUID
    prompt_version: Literal["requirement-extraction-v1", "requirement-extraction-v2-budget-deferred", "requirement-extraction-v3-explicit-negatives", "requirement-extraction-v4-hub-roles"]
    attempt_kind: Literal["extract", "repair"]
    outcome_kind: Literal["succeeded", "repair_required", "runtime_failure"]
    semantic_call_number: Literal[1, 2]
    invocation_metadata: InvocationMetadata | None
    runtime_error: LLMRuntimeError | None

    @model_validator(mode="after")
    def validate_event(self) -> "ExtractionInvocationEvent":
        expected_number = 1 if self.attempt_kind == "extract" else 2
        if self.semantic_call_number != expected_number:
            raise ValueError("semantic_call_number does not match attempt_kind")
        if self.outcome_kind == "succeeded":
            if self.invocation_metadata is None or self.runtime_error is not None:
                raise ValueError("succeeded requires metadata only")
        elif self.outcome_kind == "repair_required":
            if self.invocation_metadata is not None or self.runtime_error is not None:
                raise ValueError("repair_required carries no runtime fields")
        elif self.invocation_metadata is not None or self.runtime_error is None:
            raise ValueError("runtime_failure requires runtime_error only")
        return self


class ExtractionInvocationSink(Protocol):
    def accept(self, event: ExtractionInvocationEvent) -> None: ...


class NoOpExtractionInvocationSink:
    def accept(self, event: ExtractionInvocationEvent) -> None:
        return None
