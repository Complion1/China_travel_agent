from __future__ import annotations

import asyncio
from enum import Enum

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.llm_runtime.models import (
    LLMErrorCode,
    LLMInvocationFailure,
    LLMUsage,
    ModelRole,
    PromptEnvelope,
    StructuredInvocationSuccess,
    ToolCallsReady,
    ToolContract,
    ToolResult,
    ToolTurnCompleted,
)
from app.llm_runtime.profiles import REQUIREMENT_EXTRACTION_PROFILE
from app.llm_runtime.protocols import ProviderFailure, ProviderResponse
from app.llm_runtime.runtime import DeepSeekRuntime, ModelOperation, _provider_failure_error
from tests.unit.llm_runtime.fakes import RecordingSleeper, ScriptedProviderAdapter


class StrictValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str


class LaxValue(BaseModel):
    value: str


class ToolMode(str, Enum):
    RAILWAY = "railway"


class StrictEnumToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: ToolMode


USAGE = {
    "prompt_tokens": 10,
    "completion_tokens": 4,
    "total_tokens": 14,
    "reasoning_tokens": 2,
    "prompt_cache_hit_tokens": 3,
    "prompt_cache_miss_tokens": 7,
}
PROMPT = PromptEnvelope(system_text="system", user_text="user")
TOOL = ToolContract(name="echo", description="Echo a value", args_schema=StrictValue)
ENUM_TOOL = ToolContract(
    name="choose_mode",
    description="Choose a travel mode",
    args_schema=StrictEnumToolArgs,
)


def response(
    *,
    content='{"value":"ok"}',
    calls=(),
    finish="stop",
    model="deepseek-v4-flash",
    usage=None,
    reasoning=None,
):
    return ProviderResponse(content, calls, finish, model, USAGE if usage is None else usage, reasoning)


def operation(script, *, byte_limit=None, semantic_limit=None):
    profile = REQUIREMENT_EXTRACTION_PROFILE.model_copy(update={
        **({"max_request_utf8_bytes": byte_limit} if byte_limit is not None else {}),
        **({"semantic_invocation_limit": semantic_limit} if semantic_limit is not None else {}),
    })
    provider = ScriptedProviderAdapter(list(script))
    sleeper = RecordingSleeper()
    return ModelOperation(profile, provider, sleeper), provider, sleeper


def assert_failure(outcome, code):
    assert isinstance(outcome, LLMInvocationFailure)
    assert outcome.error.code == code


@pytest.mark.parametrize("kwargs", [
    dict(prompt_tokens=1, completion_tokens=1, total_tokens=3, reasoning_tokens=0,
         prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1),
    dict(prompt_tokens=1, completion_tokens=1, total_tokens=2, reasoning_tokens=2,
         prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1),
    dict(prompt_tokens=2, completion_tokens=1, total_tokens=3, reasoning_tokens=1,
         prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1),
])
def test_usage_consistency_is_enforced(kwargs):
    with pytest.raises(ValidationError):
        LLMUsage(**kwargs)


def test_operation_closed_and_mode_switch_are_programming_errors():
    op, _, _ = operation([response()])
    op.invoke_structured(PROMPT, StrictValue)
    with pytest.raises(RuntimeError, match="switch"):
        asyncio.run(op.ainvoke_structured(PROMPT, StrictValue))
    op.close()
    with pytest.raises(RuntimeError, match="closed"):
        op.invoke_structured(PROMPT, StrictValue)


def test_unknown_provider_failure_is_malformed_and_retry_after_is_clamped():
    error = _provider_failure_error(
        ProviderFailure("not-a-code", False, 599, 50),
        ModelRole.REQUIREMENT_EXTRACTION,
        True,
    )
    assert error.code == LLMErrorCode.MALFORMED_PROVIDER_RESPONSE
    assert error.retry_after_seconds == 30
    assert error.possible_duplicate is True


def test_structured_preflight_does_not_claim_quota():
    op, provider, _ = operation([], byte_limit=100)
    outcome = op.invoke_structured(
        PromptEnvelope(system_text="系统", user_text="😀" * 100), StrictValue
    )
    assert_failure(outcome, LLMErrorCode.INPUT_TOO_LARGE)
    assert op._semantic_invocations_used == 0
    assert provider.call_count == 0



@pytest.mark.parametrize(("item", "code"), [
    (response(model=""), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    (response(model="wrong"), LLMErrorCode.UNEXPECTED_MODEL),
    (response(finish="mystery"), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    (response(usage={}), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    (response(usage={**USAGE, "total_tokens": 99}), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    (response(finish="content_filter"), LLMErrorCode.CONTENT_FILTERED),
    (response(content=None), LLMErrorCode.STRUCTURED_OUTPUT_INVALID),
    (response(calls=(("c", "echo", {"value": "x"}),)), LLMErrorCode.STRUCTURED_OUTPUT_INVALID),
    (response(content='{"value":1}'), LLMErrorCode.STRUCTURED_OUTPUT_INVALID),
])
def test_structured_response_rejections(item, code):
    op, _, _ = operation([item])
    assert_failure(op.invoke_structured(PROMPT, StrictValue), code)


def test_resource_finish_and_retryable_failure_retry_once():
    op, _, sleeper = operation([
        response(finish="insufficient_system_resource"),
        response(),
    ])
    outcome = op.invoke_structured(PROMPT, StrictValue)
    assert isinstance(outcome, StructuredInvocationSuccess)
    assert outcome.metadata.provider_attempts == 2
    assert outcome.metadata.possible_duplicate is True
    assert sleeper.sleeps == [1.0]

    op2, _, sleeper2 = operation([
        ProviderFailure("rate_limited", True, 429, 0),
        ProviderFailure("rate_limited", True, 429, 0),
    ])
    failed = op2.invoke_structured(PROMPT, StrictValue)
    assert_failure(failed, LLMErrorCode.RATE_LIMITED)
    assert sleeper2.sleeps == [0.0]


class BadResultProvider(ScriptedProviderAdapter):
    def invoke_json(self, request):
        return object()

    async def ainvoke_json(self, request):
        return object()


@pytest.mark.asyncio
async def test_async_structured_preflight_quota_failure_and_bad_provider_result():
    op, _, _ = operation([])
    assert_failure(await op.ainvoke_structured(PROMPT, LaxValue), LLMErrorCode.SCHEMA_UNSUPPORTED)

    op2, provider2, _ = operation([], byte_limit=100)
    assert_failure(
        await op2.ainvoke_structured(PromptEnvelope(system_text="系统", user_text="😀" * 100), StrictValue),
        LLMErrorCode.INPUT_TOO_LARGE,
    )
    assert provider2.call_count == 0

    op3, _, _ = operation([response()], semantic_limit=1)
    assert isinstance(await op3.ainvoke_structured(PROMPT, StrictValue), StructuredInvocationSuccess)
    assert_failure(await op3.ainvoke_structured(PROMPT, StrictValue), LLMErrorCode.QUOTA_EXHAUSTED)

    bad = BadResultProvider([])
    op4 = ModelOperation(REQUIREMENT_EXTRACTION_PROFILE, bad, RecordingSleeper())
    assert_failure(await op4.ainvoke_structured(PROMPT, StrictValue), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)



@pytest.mark.asyncio
async def test_async_resource_retry_sets_duplicate_metadata():
    op, _, sleeper = operation([
        response(finish="insufficient_system_resource"),
        response(),
    ])
    outcome = await op.ainvoke_structured(PROMPT, StrictValue)
    assert isinstance(outcome, StructuredInvocationSuccess)
    assert outcome.metadata.provider_attempts == 2
    assert outcome.metadata.possible_duplicate is True
    assert sleeper.async_sleeps == [1.0]


def test_sync_bad_provider_result_and_non_retryable_failure():
    bad = BadResultProvider([])
    op = ModelOperation(REQUIREMENT_EXTRACTION_PROFILE, bad, RecordingSleeper())
    assert_failure(op.invoke_structured(PROMPT, StrictValue), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)
    op2, _, _ = operation([ProviderFailure("authentication_failed", False, 401, None)])
    failed = op2.invoke_structured(PROMPT, StrictValue)
    assert_failure(failed, LLMErrorCode.AUTHENTICATION_FAILED)
    assert failed.error.provider_status_code == 401


def test_tool_preflight_duplicate_and_byte_gate_do_not_call_provider():
    op, provider, _ = operation([])
    assert_failure(op.invoke_tools(PROMPT, (TOOL, TOOL)), LLMErrorCode.INVALID_REQUEST)
    assert provider.call_count == 0

    op2, provider2, _ = operation([], byte_limit=120)
    assert_failure(
        op2.invoke_tools(PromptEnvelope(system_text="系统", user_text="😀" * 100), (TOOL,)),
        LLMErrorCode.INPUT_TOO_LARGE,
    )
    assert provider2.call_count == 0

    op3, _, _ = operation([], semantic_limit=0)
    assert_failure(op3.invoke_tools(PROMPT, (TOOL,)), LLMErrorCode.QUOTA_EXHAUSTED)

    op4, _, _ = operation([ProviderFailure("timeout", False, None, None)])
    assert_failure(op4.invoke_tools(PROMPT, (TOOL,)), LLMErrorCode.TIMEOUT)


@pytest.mark.parametrize(("item", "code"), [
    (response(content=None, calls=(("c", "echo", {"value": "x"}),), finish="tool_calls", model="wrong"), LLMErrorCode.UNEXPECTED_MODEL),
    (response(content=None, calls=(("c", "echo", {"value": "x"}),), finish="tool_calls", usage={}), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    (response(finish="length"), LLMErrorCode.OUTPUT_TRUNCATED),
    (response(finish="content_filter"), LLMErrorCode.CONTENT_FILTERED),
    (response(calls=(("c", "echo", {"value": "x"}),), finish="stop"), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    (response(content=None, calls=(), finish="tool_calls"), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    (response(content=None, calls=(("", "echo", {"value": "x"}),), finish="tool_calls"), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    (response(content=None, calls=(("c", "missing", {"value": "x"}),), finish="tool_calls"), LLMErrorCode.UNKNOWN_TOOL),
    (response(content=None, calls=(("c", "echo", {"value": 1}),), finish="tool_calls"), LLMErrorCode.TOOL_ARGUMENTS_INVALID),
])
def test_tool_response_rejections(item, code):
    op, _, _ = operation([item])
    assert_failure(op.invoke_tools(PROMPT, (TOOL,)), code)
    assert op._closed is True


def test_duplicate_tool_call_ids_are_rejected():
    item = response(
        content=None,
        calls=(("c", "echo", {"value": "x"}), ("c", "echo", {"value": "y"})),
        finish="tool_calls",
    )
    op, _, _ = operation([item])
    assert_failure(op.invoke_tools(PROMPT, (TOOL,)), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)


def test_tool_json_enum_argument_is_accepted_in_strict_mode():
    item = response(
        content=None,
        calls=(("c", "choose_mode", {"mode": "railway"}),),
        finish="tool_calls",
    )
    op, _, _ = operation([item])

    outcome = op.invoke_tools(PROMPT, (ENUM_TOOL,))

    assert isinstance(outcome, ToolCallsReady)
    assert outcome.calls[0].arguments.mode is ToolMode.RAILWAY


def test_tool_continuation_validates_results_and_replays_private_reasoning():
    first = response(
        content=None,
        calls=(("a", "echo", {"value": "x"}), ("b", "echo", {"value": "y"})),
        finish="tool_calls",
        reasoning="REASONING_SENTINEL_DO_NOT_LEAK",
    )
    op, provider, _ = operation([first, response(content="done")], semantic_limit=3)
    ready = op.invoke_tools(PROMPT, (TOOL,))
    assert isinstance(ready, ToolCallsReady)
    completed = op.continue_tools((
        ToolResult(call_id="b", content="second"),
        ToolResult(call_id="a", content="first"),
    ))
    assert isinstance(completed, ToolTurnCompleted)
    messages = provider.tool_continuations[0].messages
    assert messages[-2]["tool_call_id"] == "a"
    assert messages[-1]["tool_call_id"] == "b"
    assert "REASONING_SENTINEL" in str(messages)
    assert "REASONING_SENTINEL" not in completed.model_dump_json()


@pytest.mark.parametrize("results", [
    (),
    (ToolResult(call_id="unknown", content="x"),),
])
def test_tool_continuation_rejects_missing_or_unknown_results(results):
    first = response(content=None, calls=(("c", "echo", {"value": "x"}),), finish="tool_calls")
    op, _, _ = operation([first])
    assert isinstance(op.invoke_tools(PROMPT, (TOOL,)), ToolCallsReady)
    assert_failure(op.continue_tools(results), LLMErrorCode.INVALID_REQUEST)


def test_tool_continuation_byte_gate_quota_and_provider_failure():
    first = response(content=None, calls=(("c", "echo", {"value": "x"}),), finish="tool_calls")
    op, provider, _ = operation([first], byte_limit=800)
    assert isinstance(op.invoke_tools(PROMPT, (TOOL,)), ToolCallsReady)
    assert_failure(
        op.continue_tools((ToolResult(call_id="c", content="😀" * 1000),)),
        LLMErrorCode.INPUT_TOO_LARGE,
    )
    assert len(provider.tool_continuations) == 0

    op2, _, _ = operation([first], semantic_limit=1)
    assert isinstance(op2.invoke_tools(PROMPT, (TOOL,)), ToolCallsReady)
    assert_failure(op2.continue_tools((ToolResult(call_id="c", content="x"),)), LLMErrorCode.QUOTA_EXHAUSTED)

    op3, _, _ = operation([first, ProviderFailure("timeout", False, None, None)])
    assert isinstance(op3.invoke_tools(PROMPT, (TOOL,)), ToolCallsReady)
    assert_failure(op3.continue_tools((ToolResult(call_id="c", content="x"),)), LLMErrorCode.TIMEOUT)


@pytest.mark.asyncio
async def test_async_tool_turn_and_continuation_and_context_manager():
    first = response(content=None, calls=(("c", "echo", {"value": "x"}),), finish="tool_calls", reasoning="private")
    op, provider, _ = operation([first, response(content="done")])
    async with op:
        ready = await op.ainvoke_tools(PROMPT, (TOOL,))
        assert isinstance(ready, ToolCallsReady)
        completed = await op.acontinue_tools((ToolResult(call_id="c", content="x"),))
        assert isinstance(completed, ToolTurnCompleted)
    assert len(provider.tool_continuations) == 1
    assert op._closed is True


@pytest.mark.asyncio
async def test_async_tool_json_enum_argument_is_accepted_in_strict_mode():
    item = response(
        content=None,
        calls=(("c", "choose_mode", {"mode": "railway"}),),
        finish="tool_calls",
    )
    op, _, _ = operation([item])

    outcome = await op.ainvoke_tools(PROMPT, (ENUM_TOOL,))

    assert isinstance(outcome, ToolCallsReady)
    assert outcome.calls[0].arguments.mode is ToolMode.RAILWAY


@pytest.mark.asyncio
async def test_async_tool_preflight_failure_provider_failure_and_invalid_continuation():
    op, _, _ = operation([])
    assert_failure(await op.ainvoke_tools(PROMPT, ()), LLMErrorCode.INVALID_REQUEST)

    op2, _, _ = operation([ProviderFailure("timeout", False, None, None)])
    assert_failure(await op2.ainvoke_tools(PROMPT, (TOOL,)), LLMErrorCode.TIMEOUT)

    first = response(content=None, calls=(("c", "echo", {"value": "x"}),), finish="tool_calls")
    op3, _, _ = operation([first])
    assert isinstance(await op3.ainvoke_tools(PROMPT, (TOOL,)), ToolCallsReady)
    assert_failure(await op3.acontinue_tools(()), LLMErrorCode.INVALID_REQUEST)


@pytest.mark.asyncio
async def test_async_tool_and_continuation_preflight_quota_and_provider_failures():
    op, provider, _ = operation([], byte_limit=120)
    assert_failure(
        await op.ainvoke_tools(
            PromptEnvelope(system_text="系统", user_text="😀" * 100), (TOOL,)
        ),
        LLMErrorCode.INPUT_TOO_LARGE,
    )
    assert provider.call_count == 0

    op2, _, _ = operation([], semantic_limit=0)
    assert_failure(await op2.ainvoke_tools(PROMPT, (TOOL,)), LLMErrorCode.QUOTA_EXHAUSTED)

    first = response(content=None, calls=(("c", "echo", {"value": "x"}),), finish="tool_calls")
    op3, provider3, _ = operation([first], byte_limit=800)
    assert isinstance(await op3.ainvoke_tools(PROMPT, (TOOL,)), ToolCallsReady)
    assert_failure(
        await op3.acontinue_tools((ToolResult(call_id="c", content="😀" * 1000),)),
        LLMErrorCode.INPUT_TOO_LARGE,
    )
    assert len(provider3.tool_continuations) == 0

    op4, _, _ = operation([first], semantic_limit=1)
    assert isinstance(await op4.ainvoke_tools(PROMPT, (TOOL,)), ToolCallsReady)
    assert_failure(
        await op4.acontinue_tools((ToolResult(call_id="c", content="x"),)),
        LLMErrorCode.QUOTA_EXHAUSTED,
    )

    op5, _, _ = operation([first, ProviderFailure("timeout", False, None, None)])
    assert isinstance(await op5.ainvoke_tools(PROMPT, (TOOL,)), ToolCallsReady)
    assert_failure(
        await op5.acontinue_tools((ToolResult(call_id="c", content="x"),)),
        LLMErrorCode.TIMEOUT,
    )
