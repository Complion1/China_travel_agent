from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr
from pydantic import BaseModel, ConfigDict

from app.llm_runtime.profiles import REQUIREMENT_EXTRACTION_PROFILE
from app.llm_runtime.protocols import (
    ProviderFailure,
    ProviderJSONRequest,
    ProviderResponse,
    ProviderToolContinuation,
    ProviderToolRequest,
)
from app.llm_runtime.provider import (
    LangChainDeepSeekAdapter,
    _canonical_model_name,
    _classify_exception,
    _failure_from_exception,
    _normalize_response,
    _normalize_tool_calls,
    _normalized_usage,
    _retry_after,
)
from app.llm_runtime.settings import DeepSeekSettings
from app.llm_runtime.models import (
    LLMErrorCode,
    LLMInvocationFailure,
    ModelRole,
    PromptEnvelope,
    StructuredInvocationSuccess,
)
from app.llm_runtime.runtime import DeepSeekRuntime
from tests.unit.llm_runtime.fakes import RecordingSleeper, ScriptedProviderAdapter


def make_adapter() -> LangChainDeepSeekAdapter:
    return LangChainDeepSeekAdapter(DeepSeekSettings(api_key=SecretStr("test-key")))


def make_message(*, tool_calls=(), finish="stop", content='{"status":"ok"}') -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=list(tool_calls),
        response_metadata={"finish_reason": finish, "model_name": "deepseek-v4-flash"},
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "input_token_details": {"cache_read": 3},
            "output_token_details": {"reasoning": 2},
        },
        additional_kwargs={"reasoning_content": "private"},
    )


def request_json() -> ProviderJSONRequest:
    profile = REQUIREMENT_EXTRACTION_PROFILE
    return ProviderJSONRequest(
        "system", "user", {"type": "object"}, profile.max_output_tokens,
        profile.thinking_type, profile.reasoning_effort, profile.model_name, profile,
    )


def request_tools() -> ProviderToolRequest:
    profile = REQUIREMENT_EXTRACTION_PROFILE
    tools = ({
        "type": "function",
        "function": {
            "name": "echo",
            "description": "echo",
            "parameters": {"type": "object"},
        },
    },)
    return ProviderToolRequest(
        "system", "user", tools, profile.max_output_tokens,
        profile.thinking_type, profile.reasoning_effort, profile.model_name, profile,
    )


class FakeRunnable:
    def __init__(self, value):
        self.value = value
        self.sync_messages = None
        self.async_messages = None

    def invoke(self, messages):
        self.sync_messages = messages
        if isinstance(self.value, Exception):
            raise self.value
        return self.value

    async def ainvoke(self, messages):
        self.async_messages = messages
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeModel:
    def __init__(self, value, models=("deepseek-v4-flash",)):
        self.runnable = FakeRunnable(value)
        page = SimpleNamespace(data=[SimpleNamespace(id=model) for model in models])
        self.root_client = SimpleNamespace(models=SimpleNamespace(list=lambda: page))

        async def alist():
            return page

        self.root_async_client = SimpleNamespace(models=SimpleNamespace(list=alist))

    def with_structured_output(self, *args, **kwargs):
        self.structured_args = (args, kwargs)
        return self.runnable

    def bind_tools(self, *args, **kwargs):
        self.tool_args = (args, kwargs)
        return self.runnable


def test_normalizes_actual_langchain_message_and_include_raw():
    message = make_message(
        tool_calls=({"id": "c1", "name": "echo", "args": {"value": "hi"}},),
        finish="tool_calls",
        content="",
    )
    adapter = make_adapter()
    response = adapter._unwrap_raw(message)
    assert response.tool_calls == (("c1", "echo", {"value": "hi"}),)
    assert response.usage["prompt_cache_miss_tokens"] == 7
    wrapped = adapter._unwrap_raw({
        "raw": make_message(), "parsed": {"status": "ok"}, "parsing_error": None,
    })
    assert wrapped.content == '{"status":"ok"}'


def test_normalizes_current_provider_flash_alias_at_response_boundary():
    message = make_message()
    message.response_metadata["model_name"] = "deepseek-flash"
    response = make_adapter()._unwrap_raw(message)
    assert response.model_name == "deepseek-v4-flash"
    assert _canonical_model_name("unknown-model") == "unknown-model"


@pytest.mark.parametrize("bad", [
    object(),
])
def test_unwrap_rejects_malformed_values(bad):
    assert isinstance(make_adapter()._unwrap_raw(bad), ProviderFailure)


def test_include_raw_parser_failure_preserves_complete_raw_response():
    response = make_adapter()._unwrap_raw({
        "raw": make_message(content="not json"),
        "parsed": None,
        "parsing_error": ValueError("parser details stay private"),
    })
    assert response.content == "not json"
    assert response.model_name == "deepseek-v4-flash"
    assert response.finish_reason == "stop"
    assert response.usage["total_tokens"] == 14


def test_include_raw_uses_parsed_only_when_parser_succeeded():
    response = make_adapter()._unwrap_raw({
        "raw": make_message(content="```json\n{\"status\":\"ok\"}\n```") ,
        "parsed": {"status": "ok"},
        "parsing_error": None,
    })
    assert response.content == '{"status":"ok"}'
    outcome = _decode_provider_response(response)
    assert isinstance(outcome, StructuredInvocationSuccess)
    assert outcome.value.status == "ok"


def test_include_raw_parser_failure_takes_raw_even_if_parsed_is_present():
    response = make_adapter()._unwrap_raw({
        "raw": make_message(content="not json"),
        "parsed": {"status": "ok"},
        "parsing_error": ValueError("parser details stay private"),
    })
    assert response.content == "not json"


class StatusSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str


def _decode_provider_response(response: ProviderResponse):
    runtime = DeepSeekRuntime(
        provider=ScriptedProviderAdapter([response]),
        sleeper=RecordingSleeper(),
    )
    operation = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    try:
        return operation.invoke_structured(
            PromptEnvelope(system_text="Return status", user_text="status"),
            StatusSchema,
        )
    finally:
        operation.close()


def test_include_raw_parser_failure_reaches_runtime_as_structured_invalid():
    response = make_adapter()._unwrap_raw({
        "raw": make_message(content="not json"),
        "parsed": None,
        "parsing_error": ValueError("parser details stay private"),
    })
    outcome = _decode_provider_response(response)
    assert isinstance(outcome, LLMInvocationFailure)
    assert outcome.error.code is LLMErrorCode.STRUCTURED_OUTPUT_INVALID


def test_include_raw_parsed_extra_field_is_rejected_by_runtime_strict_validation():
    response = make_adapter()._unwrap_raw({
        "raw": make_message(content="```json\n{\"status\":\"ok\"}\n```") ,
        "parsed": {"status": "ok", "extra": True},
        "parsing_error": None,
    })
    outcome = _decode_provider_response(response)
    assert isinstance(outcome, LLMInvocationFailure)
    assert outcome.error.code is LLMErrorCode.STRUCTURED_OUTPUT_INVALID


def test_include_raw_parsed_non_serializable_is_malformed():
    response = make_adapter()._unwrap_raw({
        "raw": make_message(),
        "parsed": {"status": object()},
        "parsing_error": None,
    })
    assert isinstance(response, ProviderFailure)


def test_include_raw_parser_failure_with_missing_metadata_is_malformed():
    response = make_adapter()._unwrap_raw({
        "raw": SimpleNamespace(content="not json", response_metadata={}),
        "parsed": None,
        "parsing_error": ValueError("parser details stay private"),
    })
    assert isinstance(response, ProviderFailure)
    assert response.code == "malformed_provider_response"


def test_normalize_raw_openai_response_and_rejects_bad_shapes():
    raw = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "c",
                    "function": {"name": "echo", "arguments": '{"value":"x"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "model": "deepseek-v4-flash",
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "total_tokens": 3,
            "completion_tokens_details": {"reasoning_tokens": 1},
            "prompt_cache_hit_tokens": 1,
            "prompt_cache_miss_tokens": 1,
        },
    }
    response = _normalize_response(raw)
    assert response.tool_calls[0][1] == "echo"
    malformed_values = (
        {},
        {"choices": []},
        {"choices": [{"message": {}, "finish_reason": "mystery"}], "model": "x", "usage": {}},
    )
    for malformed in malformed_values:
        assert isinstance(_normalize_response(malformed), ProviderFailure)


@pytest.mark.parametrize("value", [
    None,
    1,
    [{"id": "c"}],
    [{"id": "c", "function": {"name": "x", "arguments": "{"}}],
])
def test_tool_call_normalization_rejects_invalid(value):
    assert _normalize_tool_calls(value) is None


def test_tool_call_normalization_covers_all_supported_and_invalid_shapes():
    assert _normalize_tool_calls([{"id": "c", "function": "bad"}]) is None
    assert _normalize_tool_calls([object()]) is None
    assert _normalize_tool_calls([{"id": "c", "name": "echo", "args": {"x": 1}}]) == (("c", "echo", {"x": 1}),)
    assert _normalize_tool_calls([{"id": 1, "name": "echo", "args": {}}]) is None


def test_usage_requires_complete_integer_totals():
    assert _normalized_usage({"prompt_tokens": 1}) is None
    assert _normalized_usage({"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2}) is None
    assert _normalized_usage({
        "prompt_tokens": 2,
        "completion_tokens": 1,
        "total_tokens": 3,
        "reasoning_tokens": 0,
        "prompt_cache_hit_tokens": 4,
    }) is None


def test_exception_classification_and_retry_after():
    request = httpx.Request("POST", "https://api.deepseek.com")
    timeout = openai.APITimeoutError(request=request)
    connection = openai.APIConnectionError(request=request)
    assert _classify_exception(timeout) == ("timeout", True)
    assert _classify_exception(connection) == ("transport_error", True)
    response = httpx.Response(429, request=request, headers={"retry-after": "45"})
    status = openai.APIStatusError("limited", response=response, body={})
    failure = _failure_from_exception(status)
    assert failure.code == "rate_limited"
    assert failure.retry_after_seconds == 30.0
    assert _retry_after(Exception()) is None
    bad_header = httpx.Response(429, request=request, headers={"retry-after": "later"})
    assert _retry_after(openai.APIStatusError("limited", response=bad_header, body={})) is None
    context = openai.APIStatusError(
        "too long",
        response=httpx.Response(400, request=request),
        body={"error": {"code": "context_length_exceeded"}},
    )
    assert _classify_exception(context) == ("context_length_exceeded", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("finish, expected_code", [
    ("length", LLMErrorCode.OUTPUT_TRUNCATED),
    ("content_filter", LLMErrorCode.CONTENT_FILTERED),
])
async def test_sdk_finish_exception_reaches_runtime_without_retry_or_content_leak(
    monkeypatch, capsys, async_mode, finish, expected_code,
):
    private_text = "private completion must never escape"
    if finish == "length":
        completion = openai.types.chat.ChatCompletion(
            id="offline-completion", created=0, model="deepseek-v4-flash",
            object="chat.completion",
            choices=[{
                "index": 0, "finish_reason": finish,
                "message": {"role": "assistant", "content": private_text},
            }],
        )
        error = openai.LengthFinishReasonError(completion=completion)
    else:
        error = openai.ContentFilterFinishReasonError()
    model = FakeModel(error)
    adapter = make_adapter()
    attempts = []

    def build_model(profile):
        attempts.append(profile)
        return model

    monkeypatch.setattr(adapter, "_build_model", build_model)
    sleeper = RecordingSleeper()
    runtime = DeepSeekRuntime(provider=adapter, sleeper=sleeper)
    operation = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    prompt = PromptEnvelope(system_text="Return status", user_text="status")
    try:
        outcome = (
            await operation.ainvoke_structured(prompt, StatusSchema)
            if async_mode else operation.invoke_structured(prompt, StatusSchema)
        )
    finally:
        operation.close()
        await adapter.aclose()

    assert isinstance(outcome, LLMInvocationFailure)
    assert outcome.error.code == expected_code
    assert outcome.error.retryable is False
    assert len(attempts) == 1
    assert sleeper.sleeps == sleeper.async_sleeps == []
    captured = capsys.readouterr()
    assert private_text not in outcome.model_dump_json() + captured.out + captured.err


def test_normalize_response_rejects_each_incomplete_field():
    base = {
        "choices": [{"message": {"content": "ok", "tool_calls": []}, "finish_reason": "stop"}],
        "model": "deepseek-v4-flash",
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3,
                  "reasoning_tokens": 1, "prompt_cache_hit_tokens": 1,
                  "prompt_cache_miss_tokens": 1},
    }
    mutations = [
        {**base, "choices": [{"message": 1, "finish_reason": "stop"}]},
        {**base, "usage": {}},
        {**base, "choices": [{"message": {"content": 1}, "finish_reason": "stop"}]},
        {**base, "choices": [{"message": {"content": "ok", "tool_calls": 1}, "finish_reason": "stop"}]},
        {**base, "choices": [{"message": {"content": "ok", "reasoning_content": 1}, "finish_reason": "stop"}]},
    ]
    for item in mutations:
        assert isinstance(_normalize_response(item), ProviderFailure)


class ParsedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str


def test_unwrap_ai_message_rejects_metadata_content_reasoning_and_bad_parsed():
    adapter = make_adapter()
    assert isinstance(adapter._unwrap_raw({
        "choices": [{"message": {}, "finish_reason": "stop"}],
        "model": "deepseek-v4-flash",
        "usage": {},
    }), ProviderFailure)
    assert isinstance(adapter._unwrap_ai_message(SimpleNamespace(content="x", response_metadata=1)), ProviderFailure)
    assert isinstance(adapter._unwrap_ai_message(SimpleNamespace(content="x", response_metadata={})), ProviderFailure)
    bad_calls = SimpleNamespace(
        content="x", response_metadata={"finish_reason": "stop", "model_name": "deepseek-v4-flash"},
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        tool_calls=1, additional_kwargs={},
    )
    assert isinstance(adapter._unwrap_ai_message(bad_calls), ProviderFailure)
    bad_content = SimpleNamespace(**{**bad_calls.__dict__, "tool_calls": [], "content": 1})
    assert isinstance(adapter._unwrap_ai_message(bad_content), ProviderFailure)
    bad_reasoning = SimpleNamespace(**{**bad_calls.__dict__, "tool_calls": [], "additional_kwargs": {"reasoning_content": 1}})
    assert isinstance(adapter._unwrap_ai_message(bad_reasoning), ProviderFailure)
    assert adapter._unwrap_ai_message(make_message(), ParsedModel(status="ok")).content == '{"status":"ok"}'
    assert isinstance(adapter._unwrap_ai_message(make_message(), {"bad": object()}), ProviderFailure)


def test_build_model_uses_only_locked_profile_values(monkeypatch):
    captured = {}

    def fake_chat_deepseek(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.llm_runtime.provider.ChatDeepSeek", fake_chat_deepseek)
    make_adapter()._build_model(REQUIREMENT_EXTRACTION_PROFILE)
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["max_tokens"] == 32_768
    assert captured["max_retries"] == 0
    assert captured["streaming"] is False
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in captured


def test_build_model_caches_one_model_per_profile_and_close_is_idempotent(monkeypatch):
    created = []

    class SyncClient:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    class AsyncClient:
        def __init__(self):
            self.closed = 0

        async def close(self):
            self.closed += 1

    def factory(**kwargs):
        model = FakeModel(make_message())
        model.root_client = SyncClient()
        model.root_async_client = AsyncClient()
        created.append(model)
        return model

    monkeypatch.setattr("app.llm_runtime.provider.ChatDeepSeek", factory)
    adapter = make_adapter()
    first = adapter._build_model(REQUIREMENT_EXTRACTION_PROFILE)
    same_profile = adapter._build_model(REQUIREMENT_EXTRACTION_PROFILE.model_copy())
    different_profile = adapter._build_model(
        REQUIREMENT_EXTRACTION_PROFILE.model_copy(
            update={"read_timeout_seconds": 121, "max_output_tokens": 4096}
        )
    )

    assert first is same_profile
    assert different_profile is not first
    assert len(created) == 2
    adapter.close()
    adapter.close()
    for model in created:
        assert model.root_client.closed == 1
        assert model.root_async_client.closed == 1


@pytest.mark.asyncio
async def test_adapter_aclose_is_idempotent(monkeypatch):
    model = FakeModel(make_message())

    class AsyncClient:
        def __init__(self):
            self.closed = 0

        async def close(self):
            self.closed += 1

    model.root_client = SimpleNamespace(closed=0, close=lambda: setattr(model.root_client, "closed", model.root_client.closed + 1))
    model.root_async_client = AsyncClient()
    monkeypatch.setattr("app.llm_runtime.provider.ChatDeepSeek", lambda **kwargs: model)
    adapter = make_adapter()
    adapter._build_model(REQUIREMENT_EXTRACTION_PROFILE)

    await adapter.aclose()
    await adapter.aclose()
    assert model.root_client.closed == 1
    assert model.root_async_client.closed == 1


@pytest.mark.asyncio
async def test_adapter_json_tool_continuation_and_model_listing(monkeypatch):
    adapter = make_adapter()
    structured_model = FakeModel({
        "raw": make_message(), "parsed": {"status": "ok"}, "parsing_error": None,
    })
    monkeypatch.setattr(adapter, "_build_model", lambda profile: structured_model)
    assert (await adapter.ainvoke_json(request_json())).content == '{"status":"ok"}'
    assert adapter.invoke_json(request_json()).content == '{"status":"ok"}'
    assert adapter.list_models() == ("deepseek-v4-flash",)
    assert await adapter.alist_models() == ("deepseek-v4-flash",)

    tool_model = FakeModel(make_message(
        tool_calls=({"id": "c", "name": "echo", "args": {"value": "x"}},),
        finish="tool_calls",
        content="",
    ))
    monkeypatch.setattr(adapter, "_build_model", lambda profile: tool_model)
    tool_request = request_tools()
    assert adapter.invoke_tools(tool_request).tool_calls
    assert (await adapter.ainvoke_tools(tool_request)).tool_calls
    continuation = ProviderToolContinuation(
        messages=({"role": "user", "content": "x"},),
        tools=tool_request.tools,
        max_output_tokens=tool_request.max_output_tokens,
        thinking_type=tool_request.thinking_type,
        reasoning_effort=tool_request.reasoning_effort,
        model=tool_request.model,
        profile=tool_request.profile,
    )
    assert adapter.invoke_tool_continuation(continuation).tool_calls
    assert (await adapter.ainvoke_tool_continuation(continuation)).tool_calls


@pytest.mark.asyncio
async def test_model_listing_normalizes_and_deduplicates_current_flash_alias(monkeypatch):
    adapter = make_adapter()
    model = FakeModel(make_message(), models=("deepseek-flash", "deepseek-v4-flash", "unknown-provider-model"))
    monkeypatch.setattr(adapter, "_build_model", lambda profile: model)
    assert adapter.list_models() == ("deepseek-v4-flash", "unknown-provider-model")
    assert await adapter.alist_models() == ("deepseek-v4-flash", "unknown-provider-model")


@pytest.mark.asyncio
async def test_adapter_converts_known_and_unknown_exceptions(monkeypatch):
    adapter = make_adapter()
    request = httpx.Request("POST", "https://api.deepseek.com")
    model = FakeModel(openai.APITimeoutError(request=request))
    monkeypatch.setattr(adapter, "_build_model", lambda profile: model)
    assert (await adapter.ainvoke_json(request_json())).code == "timeout"
    assert adapter.invoke_tools(request_tools()).code == "timeout"
    assert (await adapter.ainvoke_tools(request_tools())).code == "timeout"
    model.runnable.value = RuntimeError("programming")
    assert adapter.invoke_json(request_json()).code == "malformed_provider_response"
