from __future__ import annotations

from typing import Any

from app.llm_runtime.protocols import (
    ProviderAdapter,
    ProviderFailure,
    ProviderJSONRequest,
    ProviderResponse,
    ProviderToolContinuation,
    ProviderToolRequest,
)


class ScriptedProviderAdapter:
    def __init__(self, script: list[ProviderResponse | ProviderFailure]):
        self._script = script
        self._index = 0
        self.json_requests: list[ProviderJSONRequest] = []
        self.tool_requests: list[ProviderToolRequest] = []
        self.tool_continuations: list[ProviderToolContinuation] = []
        self.call_count = 0

    def _next(self) -> ProviderResponse | ProviderFailure:
        if self._index >= len(self._script):
            raise AssertionError("ScriptedProviderAdapter: sequence exhausted")
        val = self._script[self._index]
        self._index += 1
        self.call_count += 1
        return val

    def invoke_json(self, request: ProviderJSONRequest) -> ProviderResponse | ProviderFailure:
        self.json_requests.append(request)
        return self._next()

    async def ainvoke_json(self, request: ProviderJSONRequest) -> ProviderResponse | ProviderFailure:
        self.json_requests.append(request)
        return self._next()

    def invoke_tools(self, request: ProviderToolRequest) -> ProviderResponse | ProviderFailure:
        self.tool_requests.append(request)
        return self._next()

    async def ainvoke_tools(self, request: ProviderToolRequest) -> ProviderResponse | ProviderFailure:
        self.tool_requests.append(request)
        return self._next()

    def invoke_tool_continuation(self, request: ProviderToolContinuation) -> ProviderResponse | ProviderFailure:
        self.tool_continuations.append(request)
        return self._next()

    async def ainvoke_tool_continuation(self, request: ProviderToolContinuation) -> ProviderResponse | ProviderFailure:
        self.tool_continuations.append(request)
        return self._next()

    def list_models(self) -> tuple[str, ...]:
        return ("deepseek-v4-flash",)

    async def alist_models(self) -> tuple[str, ...]:
        return ("deepseek-v4-flash",)


class RecordingSleeper:
    def __init__(self):
        self.sleeps: list[float] = []
        self.async_sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise AssertionError("negative sleep")
        self.sleeps.append(seconds)

    async def async_sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise AssertionError("negative sleep")
        self.async_sleeps.append(seconds)
