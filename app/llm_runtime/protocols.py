from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.llm_runtime.profiles import ModelProfile


@dataclass(frozen=True, slots=True)
class ProviderJSONRequest:
    system: str
    user: str
    json_schema: dict[str, object] | None
    max_output_tokens: int
    thinking_type: str
    reasoning_effort: str
    model: str
    profile: ModelProfile


@dataclass(frozen=True, slots=True)
class ProviderToolRequest:
    system: str
    user: str
    tools: tuple[dict[str, object], ...]
    max_output_tokens: int
    thinking_type: str
    reasoning_effort: str
    model: str
    profile: ModelProfile


@dataclass(frozen=True, slots=True)
class ProviderToolContinuation:
    messages: tuple[dict[str, object], ...]
    tools: tuple[dict[str, object], ...]
    max_output_tokens: int
    thinking_type: str
    reasoning_effort: str
    model: str
    profile: ModelProfile


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: str | None = field(repr=False)
    tool_calls: tuple[tuple[str, str, dict[str, object]], ...] = field(repr=False)
    finish_reason: str
    model_name: str
    usage: dict[str, int]
    reasoning_content: str | None = field(repr=False)
    # Complete wire order, retained before SDK JSON parsing; private to M02.
    raw_tool_calls: tuple[tuple[str, str, str], ...] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    code: str
    retryable: bool
    provider_status_code: int | None
    retry_after_seconds: float | None


class ProviderAdapter(Protocol):
    def invoke_json(self, request: ProviderJSONRequest) -> ProviderResponse | ProviderFailure: ...
    async def ainvoke_json(self, request: ProviderJSONRequest) -> ProviderResponse | ProviderFailure: ...
    def invoke_tools(self, request: ProviderToolRequest) -> ProviderResponse | ProviderFailure: ...
    async def ainvoke_tools(self, request: ProviderToolRequest) -> ProviderResponse | ProviderFailure: ...
    def invoke_tool_continuation(self, request: ProviderToolContinuation) -> ProviderResponse | ProviderFailure: ...
    async def ainvoke_tool_continuation(self, request: ProviderToolContinuation) -> ProviderResponse | ProviderFailure: ...
    def list_models(self) -> tuple[str, ...]: ...
    async def alist_models(self) -> tuple[str, ...]: ...
    def close(self) -> None: ...
    async def aclose(self) -> None: ...


class Sleeper(Protocol):
    def sleep(self, seconds: float) -> None: ...
    async def async_sleep(self, seconds: float) -> None: ...
