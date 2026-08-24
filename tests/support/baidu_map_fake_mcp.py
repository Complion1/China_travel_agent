"""Programmable offline MCP boundary for module 06 tests only."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

from mcp.types import CallToolRequest, CallToolResult, ListToolsResult, TextContent, Tool


def _object_schema(
    *, required: tuple[str, ...], properties: dict[str, dict[str, object]]
) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": list(required)}


def qualified_tools(*, search_limit: bool = False) -> list[Tool]:
    search_properties: dict[str, dict[str, object]] = {
        "query": {"type": "string"},
        "tag": {"type": "string"},
        "region": {"type": "string"},
        "location": {"type": "string"},
        "radius": {"type": "integer"},
        "is_chinese_mainland": {"type": "string", "enum": ["true"]},
    }
    if search_limit:
        search_properties["limit"] = {"type": "integer"}
    definitions = (
        (
            "map_geocode",
            ("address",),
            {
                "address": {"type": "string"},
                "city": {"type": "string"},
                "is_chinese_mainland": {"type": "string", "enum": ["true"]},
            },
        ),
        (
            "map_reverse_geocode",
            (),
            {
                "lat": {"type": "number"},
                "lng": {"type": "number"},
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
            },
        ),
        ("map_search_places", ("query",), search_properties),
        (
            "map_place_details",
            ("uid",),
            {
                "uid": {"type": "string"},
                "is_chinese_mainland": {"type": "string", "enum": ["true"]},
            },
        ),
        (
            "map_directions_matrix",
            ("origins", "destinations"),
            {
                "model": {"type": "string"},
                "origins": {"type": "string"},
                "destinations": {"type": "string"},
            },
        ),
        (
            "map_directions",
            ("origin", "destination"),
            {
                "model": {"type": "string"},
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "is_chinese_mainland": {"type": "string", "enum": ["true"]},
            },
        ),
        (
            "map_weather",
            (),
            {
                "location": {"type": "string"},
                "is_china": {"type": "string", "enum": ["true"]},
            },
        ),
    )
    tools = [
        Tool(name=name, description="offline-qualified", inputSchema=_object_schema(required=required, properties=properties))
        for name, required, properties in definitions
    ]
    reverse = next(tool for tool in tools if tool.name == "map_reverse_geocode")
    reverse.inputSchema["anyOf"] = [
        {"required": ["lat", "lng"]},
        {"required": ["latitude", "longitude"]},
    ]
    return tools


def structured_result(payload: dict[str, object], *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[], structuredContent=deepcopy(payload), isError=is_error)


def text_result(payload: dict[str, object], *, is_error: bool = False) -> CallToolResult:
    import json

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        isError=is_error,
    )


Handler = Callable[[dict[str, object]], CallToolResult | Awaitable[CallToolResult]]


class ScriptedMcpSession:
    def __init__(self, environment: "FakeMcpEnvironment") -> None:
        self.environment = environment
        self.initialize_calls = 0
        self.list_tools_calls = 0
        self.send_request_calls = 0

    async def initialize(self) -> object:
        self.initialize_calls += 1
        self.environment.initialize_calls += 1
        if self.environment.initialize_failures:
            raise self.environment.initialize_failures.popleft()
        return object()

    async def list_tools(self) -> ListToolsResult:
        self.list_tools_calls += 1
        self.environment.list_tools_calls += 1
        if self.environment.list_failures:
            raise self.environment.list_failures.popleft()
        return ListToolsResult(
            tools=deepcopy(self.environment.tools),
            nextCursor=self.environment.next_cursor,
        )

    async def send_request(self, request, result_type, request_read_timeout_seconds=None):
        assert result_type is CallToolResult
        assert request_read_timeout_seconds is not None
        assert type(request.root) is CallToolRequest
        params = request.root.params
        arguments = deepcopy(params.arguments or {})
        self.send_request_calls += 1
        self.environment.call_count += 1
        self.environment.calls.append((params.name, arguments))
        self.environment.active_calls += 1
        self.environment.max_active_calls = max(
            self.environment.max_active_calls, self.environment.active_calls
        )
        try:
            handler = self.environment.handlers[params.name]
            value = handler(arguments)
            if asyncio.iscoroutine(value):
                value = await value
            return deepcopy(value)
        finally:
            self.environment.active_calls -= 1


class FakeMcpEnvironment:
    def __init__(self, *, tools: list[Tool] | None = None, next_cursor: str | None = None) -> None:
        self.tools = qualified_tools() if tools is None else tools
        self.next_cursor = next_cursor
        self.handlers: dict[str, Handler] = defaultdict(
            lambda: lambda _: structured_result({"status": 1})
        )
        self.initialize_failures: deque[BaseException] = deque()
        self.list_failures: deque[BaseException] = deque()
        self.sessions: list[ScriptedMcpSession] = []
        self.enter_calls = 0
        self.exit_calls = 0
        self.initialize_calls = 0
        self.list_tools_calls = 0
        self.call_count = 0
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.active_calls = 0
        self.max_active_calls = 0
        self.close_entered = asyncio.Event()
        self.close_release: asyncio.Event | None = None

    def set_result(self, name: str, result: CallToolResult) -> None:
        self.handlers[name] = lambda _: deepcopy(result)

    def set_handler(self, name: str, handler: Handler) -> None:
        self.handlers[name] = handler

    @asynccontextmanager
    async def factory(self) -> AsyncIterator[ScriptedMcpSession]:
        self.enter_calls += 1
        session = ScriptedMcpSession(self)
        self.sessions.append(session)
        try:
            yield session
        finally:
            self.exit_calls += 1
            self.close_entered.set()
            if self.close_release is not None:
                await self.close_release.wait()


__all__ = (
    "FakeMcpEnvironment",
    "ScriptedMcpSession",
    "qualified_tools",
    "structured_result",
    "text_result",
)
