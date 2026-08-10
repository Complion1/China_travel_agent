from __future__ import annotations

import json
import asyncio
import inspect
import threading
from collections.abc import Mapping
from typing import Any

import httpx
import openai
from langchain_deepseek import ChatDeepSeek

from app.llm_runtime.profiles import ModelProfile, REQUIREMENT_EXTRACTION_PROFILE
from app.llm_runtime.protocols import (
    ProviderFailure,
    ProviderJSONRequest,
    ProviderResponse,
    ProviderToolContinuation,
    ProviderToolRequest,
)
from app.llm_runtime.settings import DeepSeekSettings

_KNOWN_FINISH_REASONS = frozenset(
    {"stop", "tool_calls", "length", "content_filter", "insufficient_system_resource"}
)


def _classify_status(status_code: int) -> tuple[str, bool]:
    mapping: dict[int, tuple[str, bool]] = {
        400: ("invalid_request", False),
        401: ("authentication_failed", False),
        402: ("insufficient_balance", False),
        403: ("permission_denied", False),
        404: ("model_not_found", False),
        422: ("invalid_request", False),
        429: ("rate_limited", True),
        500: ("provider_unavailable", True),
        502: ("provider_unavailable", True),
        503: ("provider_unavailable", True),
        504: ("provider_unavailable", True),
    }
    return mapping.get(status_code, ("malformed_provider_response", False))


def _classify_exception(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, (openai.APITimeoutError, httpx.ReadTimeout)):
        return ("timeout", True)
    if isinstance(
        exc,
        (
            openai.APIConnectionError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.WriteError,
            httpx.RemoteProtocolError,
        ),
    ):
        return ("transport_error", True)
    if isinstance(exc, openai.APIStatusError):
        body = getattr(exc, "body", None)
        if isinstance(body, Mapping):
            error = body.get("error", body)
            if isinstance(error, Mapping) and error.get("code") == "context_length_exceeded":
                return ("context_length_exceeded", False)
        return _classify_status(exc.status_code)
    return ("malformed_provider_response", False)


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    raw = headers.get("retry-after") if headers is not None else None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(value, 30.0))


def _failure_from_exception(exc: Exception) -> ProviderFailure:
    code, retryable = _classify_exception(exc)
    status = exc.status_code if isinstance(exc, openai.APIStatusError) else None
    return ProviderFailure(
        code=code,
        retryable=retryable,
        provider_status_code=status,
        retry_after_seconds=_retry_after(exc),
    )


def _normalized_usage(
    usage_raw: Mapping[str, Any] | None,
    usage_metadata: Mapping[str, Any] | None = None,
) -> dict[str, int] | None:
    raw = usage_raw or {}
    meta = usage_metadata or {}

    prompt = raw.get("prompt_tokens", meta.get("input_tokens"))
    completion = raw.get("completion_tokens", meta.get("output_tokens"))
    total = raw.get("total_tokens", meta.get("total_tokens"))
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (prompt, completion, total)):
        return None

    completion_details = raw.get("completion_tokens_details", {}) or {}
    output_details = meta.get("output_token_details", {}) or {}
    prompt_details = raw.get("prompt_tokens_details", raw.get("prompt_cache_details", {})) or {}
    input_details = meta.get("input_token_details", {}) or {}
    reasoning = raw.get(
        "reasoning_tokens",
        completion_details.get("reasoning_tokens", output_details.get("reasoning", 0)),
    )
    cache_hit = raw.get(
        "prompt_cache_hit_tokens",
        prompt_details.get("cached_tokens", input_details.get("cache_read", 0)),
    )
    cache_miss = raw.get("prompt_cache_miss_tokens")
    if cache_miss is None and isinstance(cache_hit, int):
        cache_miss = prompt - cache_hit

    values = (reasoning, cache_hit, cache_miss)
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values):
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "reasoning_tokens": reasoning,
        "prompt_cache_hit_tokens": cache_hit,
        "prompt_cache_miss_tokens": cache_miss,
    }


def _normalize_tool_calls(tool_calls: object) -> tuple[tuple[str, str, dict[str, object]], ...] | None:
    if not isinstance(tool_calls, (list, tuple)):
        return None
    normalized: list[tuple[str, str, dict[str, object]]] = []
    for item in tool_calls:
        if not isinstance(item, Mapping):
            return None
        if "function" in item:
            function = item.get("function")
            if not isinstance(function, Mapping):
                return None
            name = function.get("name")
            arguments = function.get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    return None
        else:
            name = item.get("name")
            arguments = item.get("args", {})
        call_id = item.get("id")
        if not isinstance(call_id, str) or not isinstance(name, str) or not isinstance(arguments, dict):
            return None
        normalized.append((call_id, name, arguments))
    return tuple(normalized)


def _normalize_response(raw: Mapping[str, Any]) -> ProviderResponse | ProviderFailure:
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        return _malformed()
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, Mapping):
        return _malformed()
    finish_reason = choice.get("finish_reason")
    model_name = raw.get("model")
    if finish_reason not in _KNOWN_FINISH_REASONS or not isinstance(model_name, str) or not model_name:
        return _malformed()
    usage = _normalized_usage(raw.get("usage") if isinstance(raw.get("usage"), Mapping) else None)
    if usage is None:
        return _malformed()
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        return _malformed()
    tool_calls = _normalize_tool_calls(message.get("tool_calls", []))
    if tool_calls is None:
        return _malformed()
    reasoning = message.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        return _malformed()
    return ProviderResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        model_name=model_name,
        usage=usage,
        reasoning_content=reasoning,
    )


def _malformed() -> ProviderFailure:
    return ProviderFailure(
        code="malformed_provider_response",
        retryable=False,
        provider_status_code=None,
        retry_after_seconds=None,
    )


class LangChainDeepSeekAdapter:
    def __init__(self, settings: DeepSeekSettings):
        self._settings = settings
        self._models: dict[ModelProfile, ChatDeepSeek] = {}
        self._model_lock = threading.RLock()
        self._closed = False

    def _build_model(self, profile: ModelProfile) -> ChatDeepSeek:
        key = profile
        with self._model_lock:
            if self._closed:
                raise RuntimeError("Provider closed")
            model = self._models.get(key)
            if model is None:
                model = ChatDeepSeek(
                    model=profile.model_name,
                    api_key=self._settings.api_key,
                    base_url=profile.base_url,
                    timeout=httpx.Timeout(
                        connect=float(profile.connect_timeout_seconds),
                        read=float(profile.read_timeout_seconds),
                        write=float(profile.write_timeout_seconds),
                        pool=float(profile.pool_timeout_seconds),
                    ),
                    max_retries=0,
                    max_tokens=profile.max_output_tokens,
                    streaming=False,
                    reasoning_effort=profile.reasoning_effort,
                    extra_body={"thinking": {"type": profile.thinking_type}},
                )
                self._models[key] = model
            return model

    def invoke_json(self, request: ProviderJSONRequest) -> ProviderResponse | ProviderFailure:
        try:
            runnable = self._build_model(request.profile).with_structured_output(
                request.json_schema,
                method="json_mode",
                include_raw=True,
            )
            raw = runnable.invoke(self._initial_messages(request.system, request.user))
            return self._unwrap_raw(raw)
        except Exception as exc:
            return _failure_from_exception(exc)

    async def ainvoke_json(self, request: ProviderJSONRequest) -> ProviderResponse | ProviderFailure:
        try:
            runnable = self._build_model(request.profile).with_structured_output(
                request.json_schema,
                method="json_mode",
                include_raw=True,
            )
            raw = await runnable.ainvoke(self._initial_messages(request.system, request.user))
            return self._unwrap_raw(raw)
        except Exception as exc:
            return _failure_from_exception(exc)

    def invoke_tools(self, request: ProviderToolRequest) -> ProviderResponse | ProviderFailure:
        return self._invoke_bound_tools(request.profile, request.tools, self._initial_messages(request.system, request.user))

    async def ainvoke_tools(self, request: ProviderToolRequest) -> ProviderResponse | ProviderFailure:
        return await self._ainvoke_bound_tools(request.profile, request.tools, self._initial_messages(request.system, request.user))

    def invoke_tool_continuation(self, request: ProviderToolContinuation) -> ProviderResponse | ProviderFailure:
        return self._invoke_bound_tools(request.profile, request.tools, list(request.messages))

    async def ainvoke_tool_continuation(self, request: ProviderToolContinuation) -> ProviderResponse | ProviderFailure:
        return await self._ainvoke_bound_tools(request.profile, request.tools, list(request.messages))

    def _invoke_bound_tools(
        self,
        profile: ModelProfile,
        tools: tuple[dict[str, object], ...],
        messages: list[dict[str, object]],
    ) -> ProviderResponse | ProviderFailure:
        try:
            raw = self._build_model(profile).bind_tools(list(tools), strict=False).invoke(messages)
            return self._unwrap_raw(raw)
        except Exception as exc:
            return _failure_from_exception(exc)

    async def _ainvoke_bound_tools(
        self,
        profile: ModelProfile,
        tools: tuple[dict[str, object], ...],
        messages: list[dict[str, object]],
    ) -> ProviderResponse | ProviderFailure:
        try:
            raw = await self._build_model(profile).bind_tools(list(tools), strict=False).ainvoke(messages)
            return self._unwrap_raw(raw)
        except Exception as exc:
            return _failure_from_exception(exc)

    @staticmethod
    def _initial_messages(system: str, user: str) -> list[dict[str, object]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _unwrap_raw(self, raw: Any) -> ProviderResponse | ProviderFailure:
        if isinstance(raw, Mapping) and {"raw", "parsed", "parsing_error"}.issubset(raw):
            raw_message = raw.get("raw")
            parsed = raw.get("parsed")
            parsing_error = raw.get("parsing_error")
            if parsing_error is None and parsed is not None:
                return self._unwrap_ai_message(raw_message, parsed=parsed)
            # A parser failure is content invalidity; preserve the raw message so
            # Runtime can perform the one strict, schema-aware local validation.
            return self._unwrap_ai_message(raw_message)
        if isinstance(raw, Mapping):
            return _normalize_response(raw)
        return self._unwrap_ai_message(raw)

    def _unwrap_ai_message(self, message: Any, parsed: Any = None) -> ProviderResponse | ProviderFailure:
        if message is None or not hasattr(message, "content"):
            return _malformed()
        metadata = getattr(message, "response_metadata", None) or {}
        if not isinstance(metadata, Mapping):
            return _malformed()
        usage_raw = metadata.get("token_usage") if isinstance(metadata.get("token_usage"), Mapping) else None
        usage_metadata = getattr(message, "usage_metadata", None)
        usage = _normalized_usage(usage_raw, usage_metadata if isinstance(usage_metadata, Mapping) else None)
        finish_reason = metadata.get("finish_reason")
        model_name = metadata.get("model_name", metadata.get("model"))
        if usage is None or finish_reason not in _KNOWN_FINISH_REASONS or not isinstance(model_name, str) or not model_name:
            return _malformed()
        tool_calls = _normalize_tool_calls(getattr(message, "tool_calls", []))
        if tool_calls is None:
            return _malformed()
        content = getattr(message, "content", None)
        if parsed is not None:
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(mode="json")
            try:
                content = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError):
                return _malformed()
        if content is not None and not isinstance(content, str):
            return _malformed()
        additional = getattr(message, "additional_kwargs", None) or {}
        reasoning = additional.get("reasoning_content") if isinstance(additional, Mapping) else None
        if reasoning is not None and not isinstance(reasoning, str):
            return _malformed()
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            model_name=model_name,
            usage=usage,
            reasoning_content=reasoning,
        )

    def list_models(self) -> tuple[str, ...]:
        page = self._build_model(REQUIREMENT_EXTRACTION_PROFILE).root_client.models.list()
        return tuple(model.id for model in page.data)

    async def alist_models(self) -> tuple[str, ...]:
        page = await self._build_model(REQUIREMENT_EXTRACTION_PROFILE).root_async_client.models.list()
        return tuple(model.id for model in page.data)

    @staticmethod
    def _close_sync_client(client: Any) -> None:
        close = getattr(client, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            result.close()  # type: ignore[attr-defined]
            raise RuntimeError("async provider client requires aclose")

    @staticmethod
    async def _close_async_client(client: Any) -> None:
        close = getattr(client, "close", None)
        if not callable(close):
            close = getattr(client, "aclose", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    def _close_sync_models(self, models: tuple[ChatDeepSeek, ...]) -> None:
        closed: set[int] = set()
        for model in models:
            client = getattr(model, "root_client", None)
            if client is None:
                client = getattr(model, "http_client", None)
            if client is not None and id(client) not in closed:
                self._close_sync_client(client)
                closed.add(id(client))

    async def _close_async_models(self, models: tuple[ChatDeepSeek, ...]) -> None:
        closed: set[int] = set()
        for model in models:
            client = getattr(model, "root_async_client", None)
            if client is None:
                client = getattr(model, "http_async_client", None)
            if client is not None and id(client) not in closed:
                await self._close_async_client(client)
                closed.add(id(client))

    async def aclose(self) -> None:
        with self._model_lock:
            if self._closed:
                return
            self._closed = True
            models = tuple(self._models.values())
            self._models.clear()
        self._close_sync_models(models)
        await self._close_async_models(models)

    def close(self) -> None:
        with self._model_lock:
            if self._closed:
                return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        raise RuntimeError("use aclose while an event loop is running")
