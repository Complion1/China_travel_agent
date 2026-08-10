from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.llm_runtime.models import (
    CapabilityProbeReport,
    LLMErrorCode,
    ModelRole,
    PromptEnvelope,
    StructuredInvocationSuccess,
    ToolCallsReady,
    ToolContract,
    ToolResult,
    ToolTurnCompleted,
)
from app.llm_runtime.protocols import ProviderAdapter
from app.llm_runtime.runtime import DeepSeekRuntime


class _StatusOk(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ok"]


class _EchoProbeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: Literal["probe"]


_STRUCTURED_PROMPT = PromptEnvelope(
    system_text="Return the requested probe status.",
    user_text='Return {"status":"ok"}.',
)
_TOOL_PROMPT = PromptEnvelope(
    system_text="Call the supplied echo tool exactly once, then finish after its result.",
    user_text='Call echo_probe with value "probe".',
)
_ECHO_TOOL = ToolContract(
    name="echo_probe",
    description="Return the supplied probe value without side effects.",
    args_schema=_EchoProbeArgs,
)


def _append_failure(failures: list[LLMErrorCode], code: LLMErrorCode) -> None:
    if code not in failures:
        failures.append(code)


async def _run_async_structured_probe(runtime: DeepSeekRuntime) -> StructuredInvocationSuccess | None:
    operation = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
    try:
        outcome = await operation.ainvoke_structured(_STRUCTURED_PROMPT, _StatusOk)
        return outcome if isinstance(outcome, StructuredInvocationSuccess) and outcome.value.status == "ok" else None
    finally:
        operation.close()
        await runtime.aclose()


def run_capability_probe(
    provider: ProviderAdapter,
    runtime: DeepSeekRuntime,
) -> CapabilityProbeReport:
    failures: list[LLMErrorCode] = []
    try:
        try:
            models_ok = "deepseek-v4-flash" in provider.list_models()
        except Exception:
            models_ok = False
        if not models_ok:
            _append_failure(failures, LLMErrorCode.MODEL_NOT_FOUND)

        structured_operation = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
        try:
            structured_outcome = structured_operation.invoke_structured(_STRUCTURED_PROMPT, _StatusOk)
        finally:
            structured_operation.close()
        structured_ok = (
            isinstance(structured_outcome, StructuredInvocationSuccess)
            and structured_outcome.value.status == "ok"
        )
        if not structured_ok:
            code = getattr(getattr(structured_outcome, "error", None), "code", LLMErrorCode.STRUCTURED_OUTPUT_INVALID)
            _append_failure(failures, code)

        tool_operation = runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
        try:
            tool_outcome = tool_operation.invoke_tools(_TOOL_PROMPT, (_ECHO_TOOL,))
            tool_call_valid = (
                isinstance(tool_outcome, ToolCallsReady)
                and len(tool_outcome.calls) == 1
                and tool_outcome.calls[0].tool_name == "echo_probe"
                and tool_outcome.calls[0].arguments.value == "probe"
            )
            if tool_call_valid:
                continued = tool_operation.continue_tools(
                    (ToolResult(call_id=tool_outcome.calls[0].call_id, content='{"value":"probe"}'),)
                )
                tool_ok = isinstance(continued, ToolTurnCompleted)
            else:
                tool_ok = False
        finally:
            tool_operation.close()
        if not tool_ok:
            code = getattr(getattr(tool_outcome, "error", None), "code", LLMErrorCode.CAPABILITY_UNAVAILABLE)
            _append_failure(failures, code)

        try:
            async_outcome = asyncio.run(_run_async_structured_probe(runtime))
        except Exception:
            async_outcome = None
        async_ok = async_outcome is not None
        if not async_ok:
            _append_failure(failures, LLMErrorCode.CAPABILITY_UNAVAILABLE)

        successful_metadata = [
            outcome.metadata
            for outcome in (structured_outcome, tool_outcome, async_outcome)
            if isinstance(outcome, (StructuredInvocationSuccess, ToolCallsReady))
        ]
        usage_ok = len(successful_metadata) == 3 and all(
            metadata.usage.total_tokens
            == metadata.usage.prompt_tokens + metadata.usage.completion_tokens
            for metadata in successful_metadata
        )
        if not usage_ok:
            _append_failure(failures, LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)

        thinking_ok = bool(successful_metadata) and all(
            metadata.usage.reasoning_tokens > 0 for metadata in successful_metadata
        )
        if not thinking_ok:
            _append_failure(failures, LLMErrorCode.CAPABILITY_UNAVAILABLE)

        return CapabilityProbeReport(
            models_endpoint_ok=models_ok,
            thinking_ok=thinking_ok,
            structured_output_ok=structured_ok,
            tool_call_ok=tool_ok,
            async_ok=async_ok,
            usage_ok=usage_ok,
            passed=False,
            failures=tuple(failures),
        )
    finally:
        runtime.close()
