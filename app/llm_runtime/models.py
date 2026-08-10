from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, StringConstraints, model_validator


# -- §6.1: ModelRole --

class ModelRole(str, Enum):
    REQUIREMENT_EXTRACTION = "requirement_extraction"
    CANDIDATE_RESEARCH = "candidate_research"
    ITINERARY_PLANNING = "itinerary_planning"
    REPLANNING = "replanning"


# -- §6.2: PromptEnvelope --

class PromptEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    system_text: Annotated[str, StringConstraints(min_length=1)]
    user_text: Annotated[str, StringConstraints(min_length=1)]

    @model_validator(mode="after")
    def _reject_whitespace_only(self):
        if self.system_text.strip() == "":
            raise ValueError("system_text must not be whitespace-only")
        if self.user_text.strip() == "":
            raise ValueError("user_text must not be whitespace-only")
        return self


# -- §6.3: ToolContract --

TArgs = TypeVar("TArgs", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ToolContract(Generic[TArgs]):
    name: str
    description: str
    args_schema: type[TArgs]


# -- §6.4: ToolResult & ValidatedToolCall --

class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    call_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    content: Annotated[str, StringConstraints(min_length=1)]


class ValidatedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    call_id: str
    tool_name: str
    arguments: BaseModel


# -- §6.5: LLMUsage & InvocationMetadata --

class LLMUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    prompt_tokens: NonNegativeInt
    completion_tokens: NonNegativeInt
    total_tokens: NonNegativeInt
    reasoning_tokens: NonNegativeInt
    prompt_cache_hit_tokens: NonNegativeInt
    prompt_cache_miss_tokens: NonNegativeInt

    @model_validator(mode="after")
    def _validate_consistency(self) -> "LLMUsage":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must equal prompt_tokens + completion_tokens")
        if self.prompt_cache_hit_tokens + self.prompt_cache_miss_tokens != self.prompt_tokens:
            raise ValueError("prompt cache token counts must equal prompt_tokens")
        if self.reasoning_tokens > self.completion_tokens:
            raise ValueError("reasoning_tokens must not exceed completion_tokens")
        return self


class InvocationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    role: ModelRole
    model_name: Literal["deepseek-v4-flash"]
    finish_reason: Literal[
        "stop", "tool_calls", "length", "content_filter",
        "insufficient_system_resource",
    ]
    provider_attempts: Annotated[int, Field(ge=1, le=2)]
    possible_duplicate: bool
    usage: LLMUsage


# -- §6.6: Error --

class LLMErrorCode(str, Enum):
    # 22 codes from spec §11.1 — exact list
    CONFIGURATION_ERROR = "configuration_error"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    SCHEMA_UNSUPPORTED = "schema_unsupported"
    INPUT_TOO_LARGE = "input_too_large"
    QUOTA_EXHAUSTED = "quota_exhausted"
    AUTHENTICATION_FAILED = "authentication_failed"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    PERMISSION_DENIED = "permission_denied"
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    STRUCTURED_OUTPUT_INVALID = "structured_output_invalid"
    TOOL_ARGUMENTS_INVALID = "tool_arguments_invalid"
    UNKNOWN_TOOL = "unknown_tool"
    OUTPUT_TRUNCATED = "output_truncated"
    CONTENT_FILTERED = "content_filtered"
    MALFORMED_PROVIDER_RESPONSE = "malformed_provider_response"
    UNEXPECTED_MODEL = "unexpected_model"


class LLMRuntimeError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    code: LLMErrorCode
    role: ModelRole
    retryable: bool
    possible_duplicate: bool
    provider_status_code: int | None = None
    retry_after_seconds: float | None = None
    actual_bytes: int | None = None
    max_bytes: int | None = None

class DeepSeekConfigurationError(Exception):
    def __init__(self, code: LLMErrorCode = LLMErrorCode.CONFIGURATION_ERROR):
        self.code = code
        super().__init__(code.value)


# -- §6.7: Outcome discriminated union --

class StructuredInvocationSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    outcome_type: Literal["structured_success"] = "structured_success"
    value: BaseModel
    metadata: InvocationMetadata


class ToolCallsReady(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    outcome_type: Literal["tool_calls"] = "tool_calls"
    calls: tuple[ValidatedToolCall, ...]
    metadata: InvocationMetadata


class ToolTurnCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    outcome_type: Literal["completed"] = "completed"
    final_text: str
    metadata: InvocationMetadata


class LLMInvocationFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    outcome_type: Literal["failure"] = "failure"
    error: LLMRuntimeError


StructuredInvocationOutcome = Annotated[
    StructuredInvocationSuccess | LLMInvocationFailure,
    Field(discriminator="outcome_type"),
]

ToolTurnOutcome = Annotated[
    ToolCallsReady | ToolTurnCompleted | LLMInvocationFailure,
    Field(discriminator="outcome_type"),
]


# -- §16: CapabilityProbeReport --

class CapabilityProbeReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["1.0"] = "1.0"
    provider: Literal["deepseek"] = "deepseek"
    model_name: Literal["deepseek-v4-flash"] = "deepseek-v4-flash"
    models_endpoint_ok: bool = False
    thinking_ok: bool = False
    structured_output_ok: bool = False
    tool_call_ok: bool = False
    async_ok: bool = False
    usage_ok: bool = False
    passed: bool = False
    failures: tuple[LLMErrorCode, ...] = ()

    @model_validator(mode="after")
    def _validate_passed_consistency(self):
        all_ok = all([
            self.models_endpoint_ok, self.thinking_ok, self.structured_output_ok,
            self.tool_call_ok, self.async_ok, self.usage_ok,
        ])
        should_pass = all_ok and len(self.failures) == 0
        if self.passed != should_pass:
            object.__setattr__(self, "passed", should_pass)
        return self
