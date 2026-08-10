"""LLM-T05: Structured Runtime tests"""
from enum import Enum

import pytest
from pydantic import BaseModel, ConfigDict
from app.llm_runtime.models import (
    ModelRole, PromptEnvelope, StructuredInvocationSuccess,
    LLMInvocationFailure, LLMErrorCode, LLMUsage,
)
from app.llm_runtime.protocols import ProviderResponse, ProviderFailure
from app.llm_runtime.runtime import DeepSeekRuntime, _clamp_retry_after, _is_possible_duplicate
from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper


class CitySchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    city: str


class LaxSchema(BaseModel):
    city: str


class TravelMode(str, Enum):
    RAILWAY = "railway"


class EnumSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: TravelMode


def make_runtime(script):
    return DeepSeekRuntime(provider=ScriptedProviderAdapter(script), sleeper=RecordingSleeper())


def make_response(content='{"city":"杭州"}', finish="stop"):
    return ProviderResponse(content=content, tool_calls=(), finish_reason=finish,
        model_name="deepseek-v4-flash",
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
               "reasoning_tokens": 5, "prompt_cache_hit_tokens": 3,
               "prompt_cache_miss_tokens": 7},
        reasoning_content=None)


def test_structured_success():
    runtime = make_runtime([make_response()])
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    prompt = PromptEnvelope(system_text="Helpful", user_text="杭州")
    outcome = op.invoke_structured(prompt, CitySchema)
    assert isinstance(outcome, StructuredInvocationSuccess)
    assert outcome.value.city == "杭州"


def test_structured_json_enum_is_accepted_in_strict_mode():
    runtime = make_runtime([make_response(content='{"mode":"railway"}')])
    operation = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)

    outcome = operation.invoke_structured(
        PromptEnvelope(system_text="Return mode", user_text="railway"),
        EnumSchema,
    )

    assert isinstance(outcome, StructuredInvocationSuccess)
    assert outcome.value.mode is TravelMode.RAILWAY


@pytest.mark.asyncio
async def test_async_structured_json_enum_is_accepted_in_strict_mode():
    runtime = make_runtime([make_response(content='{"mode":"railway"}')])
    operation = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)

    outcome = await operation.ainvoke_structured(
        PromptEnvelope(system_text="Return mode", user_text="railway"),
        EnumSchema,
    )

    assert isinstance(outcome, StructuredInvocationSuccess)
    assert outcome.value.mode is TravelMode.RAILWAY


def test_structured_schema_rejected():
    runtime = make_runtime([])
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    outcome = op.invoke_structured(PromptEnvelope(system_text="Helpful", user_text="杭州"), LaxSchema)
    assert isinstance(outcome, LLMInvocationFailure)
    assert outcome.error.code == LLMErrorCode.SCHEMA_UNSUPPORTED


def test_structured_quota_exhausted():
    runtime = make_runtime([make_response(), make_response()])
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    prompt = PromptEnvelope(system_text="Helpful", user_text="杭州")
    op.invoke_structured(prompt, CitySchema)
    op.invoke_structured(prompt, CitySchema)
    outcome = op.invoke_structured(prompt, CitySchema)
    assert isinstance(outcome, LLMInvocationFailure)
    assert outcome.error.code == LLMErrorCode.QUOTA_EXHAUSTED


def test_structured_output_truncated():
    runtime = make_runtime([make_response(finish="length")])
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    outcome = op.invoke_structured(PromptEnvelope(system_text="Helpful", user_text="杭州"), CitySchema)
    assert isinstance(outcome, LLMInvocationFailure)
    assert outcome.error.code == LLMErrorCode.OUTPUT_TRUNCATED


def test_structured_retry_then_success():
    runtime = make_runtime([ProviderFailure(code="rate_limited", retryable=True, provider_status_code=429, retry_after_seconds=0.5), make_response()])
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    outcome = op.invoke_structured(PromptEnvelope(system_text="Helpful", user_text="杭州"), CitySchema)
    assert isinstance(outcome, StructuredInvocationSuccess)


def test_structured_non_retryable_failure():
    runtime = make_runtime([ProviderFailure(code="authentication_failed", retryable=False, provider_status_code=401, retry_after_seconds=None)])
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    outcome = op.invoke_structured(PromptEnvelope(system_text="Helpful", user_text="杭州"), CitySchema)
    assert isinstance(outcome, LLMInvocationFailure)


def test_clamp_retry_after():
    assert _clamp_retry_after(None) == 1.0
    assert _clamp_retry_after(0) == 0.0
    assert _clamp_retry_after(5.0) == 5.0
    assert _clamp_retry_after(60.0) == 30.0


def test_is_possible_duplicate():
    assert _is_possible_duplicate("timeout")
    assert _is_possible_duplicate("transport_error")
    assert not _is_possible_duplicate("rate_limited")


def test_context_manager():
    runtime = make_runtime([make_response()])
    with runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION) as op:
        assert not op._closed
    assert op._closed


@pytest.mark.parametrize("content", [
    "not json",
    '{}',
    '{"city":"杭州","extra":true}',
    '{"city":123}',
])
def test_structured_invalid_json_and_strict_validation(content):
    runtime = make_runtime([make_response(content=content, finish="stop")])
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    outcome = op.invoke_structured(PromptEnvelope(system_text="Helpful", user_text="杭州"), CitySchema)
    assert isinstance(outcome, LLMInvocationFailure)
    assert outcome.error.code == LLMErrorCode.STRUCTURED_OUTPUT_INVALID


class LifecycleProvider(ScriptedProviderAdapter):
    def __init__(self):
        super().__init__([])
        self.sync_closes = 0
        self.async_closes = 0

    def close(self):
        self.sync_closes += 1

    async def aclose(self):
        self.async_closes += 1


def test_runtime_close_is_idempotent_and_rejects_new_operations():
    provider = LifecycleProvider()
    runtime = DeepSeekRuntime(provider=provider, sleeper=RecordingSleeper())
    runtime.close()
    runtime.close()
    assert provider.sync_closes == 1
    with pytest.raises(RuntimeError, match="Runtime closed"):
        runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)


@pytest.mark.asyncio
async def test_runtime_aclose_is_idempotent():
    provider = LifecycleProvider()
    runtime = DeepSeekRuntime(provider=provider, sleeper=RecordingSleeper())
    await runtime.aclose()
    await runtime.aclose()
    assert provider.async_closes == 1
    with pytest.raises(RuntimeError, match="Runtime closed"):
        runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
