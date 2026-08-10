"""LLM-T06: Tool operation tests"""
import pytest
from pydantic import BaseModel, ConfigDict
from app.llm_runtime.models import (
    ModelRole, PromptEnvelope, ToolContract, ToolCallsReady, ToolTurnCompleted, LLMInvocationFailure, LLMErrorCode,
)
from app.llm_runtime.protocols import ProviderResponse
from app.llm_runtime.runtime import DeepSeekRuntime
from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str


def test_invoke_tools_returns_tool_calls():
    script = [ProviderResponse(content=None, tool_calls=(("call1", "echo", {"value": "hello"}),), finish_reason="tool_calls", model_name="deepseek-v4-flash", usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "reasoning_tokens": 5, "prompt_cache_hit_tokens": 3, "prompt_cache_miss_tokens": 7}, reasoning_content=None)]
    runtime = DeepSeekRuntime(provider=ScriptedProviderAdapter(script), sleeper=RecordingSleeper())
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    prompt = PromptEnvelope(system_text="Echo", user_text="echo hello")
    tools = (ToolContract(name="echo", description="Echo back", args_schema=EchoArgs),)
    outcome = op.invoke_tools(prompt, tools)
    assert isinstance(outcome, ToolCallsReady)


def test_invoke_tools_returns_stop():
    script = [ProviderResponse(content="done", tool_calls=(), finish_reason="stop", model_name="deepseek-v4-flash", usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "reasoning_tokens": 5, "prompt_cache_hit_tokens": 3, "prompt_cache_miss_tokens": 7}, reasoning_content=None)]
    runtime = DeepSeekRuntime(provider=ScriptedProviderAdapter(script), sleeper=RecordingSleeper())
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    prompt = PromptEnvelope(system_text="Echo", user_text="hello")
    tools = (ToolContract(name="echo", description="Echo", args_schema=EchoArgs),)
    outcome = op.invoke_tools(prompt, tools)
    assert isinstance(outcome, ToolTurnCompleted)


def test_invoke_tools_requires_at_least_one_tool():
    runtime = DeepSeekRuntime(provider=ScriptedProviderAdapter([]), sleeper=RecordingSleeper())
    op = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    prompt = PromptEnvelope(system_text="Echo", user_text="hello")
    outcome = op.invoke_tools(prompt, ())
    assert isinstance(outcome, LLMInvocationFailure)
    assert outcome.error.code == LLMErrorCode.INVALID_REQUEST
