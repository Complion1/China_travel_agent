"""LLM-T07: Privacy and serialization tests"""
import pickle
import pytest
from pydantic import BaseModel, ConfigDict
from app.llm_runtime.models import ModelRole, PromptEnvelope
from app.llm_runtime.protocols import ProviderResponse
from app.llm_runtime.runtime import DeepSeekRuntime
from app.llm_runtime.settings import DeepSeekSettings
from pydantic import SecretStr
from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper


class SimpleSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str


def make_runtime(script):
    return DeepSeekRuntime(
        provider=ScriptedProviderAdapter(script),
        sleeper=RecordingSleeper(),
    )


def test_operation_repr_no_secret():
    script = [ProviderResponse(content='{"value":"ok"}', tool_calls=(), finish_reason="stop", model_name="deepseek-v4-flash", usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "reasoning_tokens": 5, "prompt_cache_hit_tokens": 3, "prompt_cache_miss_tokens": 7}, reasoning_content=None)]
    runtime = make_runtime(script)
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    r = repr(op)
    assert "deepseek" not in r.lower() or "requirement" in r.lower()


def test_runtime_repr_no_secret():
    runtime = make_runtime([])
    r = repr(runtime)
    assert "api_key" not in r.lower()


def test_operation_cannot_pickle():
    script = [ProviderResponse(content='{"value":"ok"}', tool_calls=(), finish_reason="stop", model_name="deepseek-v4-flash", usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "reasoning_tokens": 5, "prompt_cache_hit_tokens": 3, "prompt_cache_miss_tokens": 7}, reasoning_content=None)]
    runtime = make_runtime(script)
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    with pytest.raises(TypeError):
        pickle.dumps(op)


def test_settings_repr_no_key():
    s = DeepSeekSettings(api_key=SecretStr("sk-secret"))
    assert "sk-secret" not in repr(s)
    assert "sk-secret" not in str(s)
