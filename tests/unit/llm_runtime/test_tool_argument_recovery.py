"""Approved M02 §20: generic, atomic, explicit schema recovery."""
import asyncio
import json
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.llm_runtime import (
    DeepSeekRuntime, ModelRole, PromptEnvelope, ToolContract, ToolResult,
    ToolCallsReady, ToolTurnRejected, LLMInvocationFailure, LLMErrorCode,
)
from app.llm_runtime.protocols import ProviderResponse, ProviderFailure
from app.llm_runtime.runtime import ModelOperation
from app.llm_runtime.profiles import get_profile
from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper


class CounterArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    quantity: int = Field(ge=1, le=4)
    mode: Literal["fast", "slow"]
    labels: tuple[str, ...] = Field(min_length=1, max_length=2)


class LabelArgs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    label: str = Field(min_length=1, max_length=5, pattern=r"^[A-Z]+$")


TOOLS = (ToolContract("counter", "fixture", CounterArgs), ToolContract("label", "fixture", LabelArgs))
PROMPT = PromptEnvelope(system_text="fixture", user_text="fixture")
GOOD = '{ "labels": ["A"], "mode":"fast", "quantity": 1 }'
USAGE = dict(prompt_tokens=10, completion_tokens=5, total_tokens=15, reasoning_tokens=3,
    prompt_cache_hit_tokens=8, prompt_cache_miss_tokens=2)


def reply(*calls, finish="tool_calls", model="deepseek-v4-flash", usage=None):
    return ProviderResponse(content=None, tool_calls=(), finish_reason=finish, model_name=model,
        usage=USAGE if usage is None else usage, reasoning_content="PRIVATE_REASONING",
        raw_tool_calls=tuple(calls))


def operation(*responses, **profile_changes):
    adapter = ScriptedProviderAdapter(list(responses))
    profile = get_profile(ModelRole.ITINERARY_PLANNING).model_copy(update=profile_changes)
    return ModelOperation(profile, adapter, RecordingSleeper()), adapter


@pytest.mark.parametrize("name,raw,rule", [
    ("counter", '{"mode":"fast","labels":["A"]}', "missing"),
    ("counter", '{"quantity":"1","mode":"fast","labels":["A"]}', "type"),
    ("counter", '{"quantity":1,"mode":"bad","labels":["A"]}', "enum"),
    ("counter", '{"quantity":1,"mode":"fast","labels":["A"],"SECRET_INPUT":1}', "extra"),
    ("counter", '{"quantity":0,"mode":"fast","labels":["A"]}', "constraint"),
    ("counter", '{"quantity":1,"mode":"fast","labels":[]}', "constraint"),
    ("label", '{"label":"TOO_LONG"}', "constraint"),
    ("label", '{"label":"lower"}', "constraint"),
    ("label", '{', "invalid_json"),
    ("label", '[]', "type"),
])
def test_generic_errors_reject_whole_batch_then_revalidate(name, raw, rule, caplog):
    bad = reply(("bad", name, raw), ("sibling", "label", '{"label":"OK"}'))
    good = reply(("fixed", "counter", GOOD), ("sibling2", "label", '{"label":"OK"}'))
    op, provider = operation(bad, good)
    rejected = op.invoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
    assert isinstance(rejected, ToolTurnRejected)
    assert rejected.calls[0].issues[0].rule == rule
    assert not rejected.calls[1].issues
    assert all(json.loads(result.content)["status"] == "not_executed" for result in rejected.tool_results())
    assert "SECRET_INPUT" not in rejected.model_dump_json() + repr(rejected) + caplog.text
    assert "PRIVATE_REASONING" not in repr(op) + repr(bad) + rejected.model_dump_json() + caplog.text
    result = op.repair_tools()
    assert isinstance(result, ToolCallsReady) and len(result.calls) == 2
    wire = provider.tool_continuations[0].messages
    assert wire[2]["tool_calls"][0]["function"]["arguments"] == raw
    assert wire[2]["reasoning_content"] == "PRIVATE_REASONING"
    assert [message["tool_call_id"] for message in wire if message["role"] == "tool"] == ["bad", "sibling"]
    assert op._semantic_invocations_used == provider.call_count == 2


@pytest.mark.parametrize("tail,code", [
    (("later", "unknown", '{}'), LLMErrorCode.UNKNOWN_TOOL),
    (("first", "label", '{"label":"OK"}'), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    ((" ", "label", '{"label":"OK"}'), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    (("x"*257, "label", '{"label":"OK"}'), LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
])
def test_later_protocol_error_precedes_first_parameter_error(tail, code):
    op, provider = operation(reply(("first", "counter", '{}'), tail))
    result = op.invoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
    assert isinstance(result, LLMInvocationFailure) and result.error.code is code
    assert provider.call_count == 1 and op._closed


@pytest.mark.parametrize("changes,code", [
    ({"finish":"length"}, LLMErrorCode.OUTPUT_TRUNCATED),
    ({"finish":"content_filter"}, LLMErrorCode.CONTENT_FILTERED),
    ({"finish":"stop"}, LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
    ({"model":"other"}, LLMErrorCode.UNEXPECTED_MODEL),
    ({"usage":{}}, LLMErrorCode.MALFORMED_PROVIDER_RESPONSE),
])
def test_envelope_failures_are_never_format_rejections(changes, code):
    op, _ = operation(reply(("bad", "counter", '{}'), **changes))
    result = op.invoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
    assert isinstance(result, LLMInvocationFailure) and result.error.code is code


def test_default_consumer_still_closes_and_pending_misuse_does_not_destroy_owner():
    parsed_bad = ProviderResponse(None, (("bad", "counter", {}),), "tool_calls", "deepseek-v4-flash", USAGE, None)
    op, _ = operation(parsed_bad)
    result = op.invoke_tools(PROMPT, TOOLS)
    assert isinstance(result, LLMInvocationFailure) and result.error.code is LLMErrorCode.TOOL_ARGUMENTS_INVALID and op._closed
    op, provider = operation(reply(("bad", "counter", '{}')), reply(("good", "counter", GOOD)))
    assert isinstance(op.invoke_tools(PROMPT, TOOLS, allow_argument_repair=True), ToolTurnRejected)
    with pytest.raises(RuntimeError, match="repair_pending"):
        op.continue_tools((ToolResult(call_id="bad", content="{}"),))
    with pytest.raises(RuntimeError, match="repair_pending"):
        op.invoke_tools(PROMPT, TOOLS)
    assert not op._closed and provider.call_count == 1
    assert isinstance(op.repair_tools(), ToolCallsReady)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.asyncio
async def test_repair_limits_and_contract_preflight_issue_no_request(async_mode):
    for case in ("quota", "byte", "tools", "absent"):
        op, provider = operation(reply(("bad", "counter", '{}')))
        invoke = op.ainvoke_tools if async_mode else op.invoke_tools
        if case != "absent":
            rejected = invoke(PROMPT, TOOLS, allow_argument_repair=True)
            if async_mode:
                await rejected
        if case == "quota":
            op._profile = op._profile.model_copy(update={"semantic_invocation_limit":1})
        if case == "byte":
            op._profile = op._profile.model_copy(update={"max_request_utf8_bytes":1})
        repair = op.arepair_tools if async_mode else op.repair_tools
        result = repair(**({"tools":TOOLS[:1]} if case == "tools" else {}))
        if async_mode:
            result = await result
        assert isinstance(result, LLMInvocationFailure) and op._closed
        assert provider.call_count == (0 if case == "absent" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["close", "cancel"])
async def test_late_repair_response_cannot_resurrect_and_parallel_calls_are_refused(termination):
    started, release = asyncio.Event(), asyncio.Event()
    op, provider = operation(reply(("bad", "counter", '{}')), reply(("good", "counter", GOOD)))
    await op.ainvoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
    original = provider.ainvoke_tool_continuation
    async def blocked(request):
        started.set()
        await release.wait()
        return await original(request)
    provider.ainvoke_tool_continuation = blocked
    task = asyncio.create_task(op.arepair_tools())
    await started.wait()
    with pytest.raises(RuntimeError, match="in_progress"):
        await op.arepair_tools()
    with pytest.raises(RuntimeError, match="in_progress"):
        await op.ainvoke_structured(PROMPT, CounterArgs)
    assert not op._closed
    if termination == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        op.close()
        release.set()
        with pytest.raises(RuntimeError, match="closed"):
            await task
    assert op._closed and op._private_tool_history == [] and op._rejected_turn is None


@pytest.mark.asyncio
async def test_closed_during_admission_never_sends():
    op, provider = operation(reply(("bad", "counter", '{}')))
    async def callback(_):
        op.close()
    with pytest.raises(RuntimeError, match="closed"):
        await op.ainvoke_tools(PROMPT, TOOLS, allow_argument_repair=True, admission_callback=callback)
    assert provider.call_count == op._semantic_invocations_used == 0 and op._tool_prompt is None


@pytest.mark.asyncio
async def test_actual_sdk_wire_preserves_complete_invalid_and_valid_batch(monkeypatch):
    from tests.unit.llm_runtime.test_raw_interaction_capture import setup, response
    bad_calls = [dict(id="bad", type="function", function=dict(name="counter", arguments='{')),
        dict(id="valid", type="function", function=dict(name="counter", arguments=GOOD)),
        dict(id="bad2", type="function", function=dict(name="label", arguments='{"label":'))]
    good_calls = [dict(id="fixed", type="function", function=dict(name="counter", arguments=GOOD))]
    wire = []
    def handler(request):
        wire.append(json.loads(request.content))
        return httpx.Response(200, content=response(content=None, finish="tool_calls", tools=bad_calls if len(wire)==1 else good_calls))
    runtime, recorder, scope = setup(monkeypatch, handler)
    try:
        with scope:
            op = runtime.start_operation(ModelRole.ITINERARY_PLANNING)
            rejected = await op.ainvoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
            assert isinstance(rejected, ToolTurnRejected) and len(rejected.calls) == 3
            assert isinstance(await op.arepair_tools(), ToolCallsReady)
        original = wire[1]["messages"][2]
        assert original["tool_calls"] == bad_calls
        assert original["reasoning_content"] == "实际返回的思考"
        assert [m["tool_call_id"] for m in wire[1]["messages"] if m["role"]=="tool"] == ["bad", "valid", "bad2"]
        assert "_interaction_tool_calls" not in wire[1]
        validations = [r.payload for r in recorder.records if r.record_kind=="validation"]
        assert any(not p.passed and p.code=="tool_arguments_invalid" for p in validations)
        assert all(t["function"]["strict"] for t in wire[1]["tools"])
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.asyncio
async def test_repair_provider_failure_is_terminal_and_not_hidden_retry(async_mode):
    op, provider = operation(reply(("bad", "counter", '{}')), ProviderFailure("authentication_error", False, 401, None))
    if async_mode:
        await op.ainvoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
        result = await op.arepair_tools()
    else:
        op.invoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
        result = op.repair_tools()
    assert isinstance(result, LLMInvocationFailure) and provider.call_count == 2
    assert op._closed and op._private_tool_history == []


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.asyncio
async def test_initial_repair_policy_is_boolean_and_cannot_be_reselected(async_mode):
    op, provider = operation(reply(("good", "counter", GOOD)))
    invoke = op.ainvoke_tools if async_mode else op.invoke_tools
    result = invoke(PROMPT, TOOLS, allow_argument_repair="true")
    if async_mode:
        result = await result
    assert isinstance(result, LLMInvocationFailure) and provider.call_count==0
    op, provider = operation(reply(("good", "counter", GOOD)))
    invoke = op.ainvoke_tools if async_mode else op.invoke_tools
    result = invoke(PROMPT, TOOLS, allow_argument_repair=True)
    if async_mode:
        await result
    with pytest.raises(RuntimeError, match="already_started"):
        if async_mode:
            await invoke(PROMPT, TOOLS, allow_argument_repair=False)
        else:
            invoke(PROMPT, TOOLS, allow_argument_repair=False)
    assert provider.call_count==1 and not op._closed
    op.close()


@pytest.mark.parametrize("calls", [(('id', 'counter', 3),), (('id', [], '{}'),), (('id',),)])
def test_forged_provider_shapes_do_not_become_repairable(calls):
    op, provider = operation(reply(*calls))
    result = op.invoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
    assert isinstance(result, LLMInvocationFailure) and result.error.code is LLMErrorCode.MALFORMED_PROVIDER_RESPONSE


def test_rejected_batch_capacity_and_default_raw_json_failure():
    op, _ = operation(reply(("bad", "counter", '{}'), *((f"ok-{i}", "counter", GOOD) for i in range(128))))
    result = op.invoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
    assert isinstance(result, LLMInvocationFailure) and result.error.code is LLMErrorCode.MALFORMED_PROVIDER_RESPONSE
    op, _ = operation(reply(("bad", "counter", '{')))
    assert isinstance(op.invoke_tools(PROMPT, TOOLS), LLMInvocationFailure)


def test_private_schema_exception_is_fatal_and_deep_paths_are_bounded(caplog):
    from pydantic import field_validator, create_model
    class InternalCheck(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
        value: str
        @field_validator("value")
        @classmethod
        def validate_value(cls, value):
            raise TypeError("PRIVATE_VALIDATOR_INPUT")
    op, provider = operation(reply(("id", "check", '{"value":"PRIVATE_VALIDATOR_INPUT"}')))
    result = op.invoke_tools(PROMPT, (ToolContract("check", "fixture", InternalCheck),), allow_argument_repair=True)
    assert isinstance(result, LLMInvocationFailure) and op._closed and op._rejected_turn is None
    assert "PRIVATE_VALIDATOR_INPUT" not in result.model_dump_json()+caplog.text
    with pytest.raises(RuntimeError, match="closed"):
        op.repair_tools()
    assert provider.call_count == 1
    schema = create_model("LongField", __config__=ConfigDict(strict=True, extra="forbid"), **{"a"*300:(int, ...)})
    op, _ = operation(reply(("id", "check", '{}')))
    result = op.invoke_tools(PROMPT, (ToolContract("check", "fixture", schema),), allow_argument_repair=True)
    assert isinstance(result, ToolTurnRejected) and result.calls[0].issues[0].field == "field_path_too_long"


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_opt_in_without_complete_raw_batch_fails_closed(async_mode):
    parsed = ProviderResponse(None, (("id", "counter", {"quantity": 1}),), "tool_calls", "deepseek-v4-flash", USAGE, None)
    op, provider = operation(parsed)
    invoke = op.ainvoke_tools if async_mode else op.invoke_tools
    result = invoke(PROMPT, TOOLS, allow_argument_repair=True)
    if async_mode:
        result = await result
    assert isinstance(result, LLMInvocationFailure) and result.error.code is LLMErrorCode.MALFORMED_PROVIDER_RESPONSE
    assert op._closed and op._rejected_turn is None and provider.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
async def test_pending_rejection_refuses_structured_without_closing_owner(async_mode):
    op, provider = operation(reply(("bad", "counter", '{}')), reply(("good", "counter", GOOD)))
    if async_mode:
        await op.ainvoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
        with pytest.raises(RuntimeError, match="repair_pending"):
            await op.ainvoke_structured(PROMPT, CounterArgs)
        assert isinstance(await op.arepair_tools(), ToolCallsReady)
    else:
        op.invoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
        with pytest.raises(RuntimeError, match="repair_pending"):
            op.invoke_structured(PROMPT, CounterArgs)
        assert isinstance(op.repair_tools(), ToolCallsReady)
    assert not op._closed and provider.call_count == 2 and not provider.json_requests


def test_rejection_dto_cannot_forge_success_or_retryability():
    from app.llm_runtime import LLMRuntimeError
    from tests.unit.itinerary_planning.test_service_initial import _metadata
    for error, calls in ((LLMRuntimeError(code=LLMErrorCode.UNKNOWN_TOOL, role=ModelRole.ITINERARY_PLANNING, retryable=False, possible_duplicate=False), ()),
        (LLMRuntimeError(code=LLMErrorCode.TOOL_ARGUMENTS_INVALID, role=ModelRole.ITINERARY_PLANNING, retryable=False, possible_duplicate=False), ())):
        with pytest.raises(ValueError):
            ToolTurnRejected(error=error, metadata=_metadata(), calls=calls)
    op, _ = operation(reply(("id", "counter", '{}')))
    rejected = op.invoke_tools(PROMPT, TOOLS, allow_argument_repair=True)
    for changes in ({"error":LLMRuntimeError(code=LLMErrorCode.UNKNOWN_TOOL, role=ModelRole.ITINERARY_PLANNING, retryable=False, possible_duplicate=False)},
        {"error":LLMRuntimeError(code=LLMErrorCode.TOOL_ARGUMENTS_INVALID, role=ModelRole.ITINERARY_PLANNING, retryable=True, possible_duplicate=False)},
        {"calls":(rejected.calls[0].model_copy(update={"issues":()}),)}, {"calls":rejected.calls*2}):
        with pytest.raises(ValueError):
            ToolTurnRejected.model_validate({**rejected.model_dump(mode="python"), **changes}, strict=True)


@pytest.mark.parametrize("wire", [None, 3, [3], [{"type":"other"}], [{"type":"function", "function":3}],
    [{"id":"x", "type":"function", "function":{"name":"counter", "arguments":3}}]])
def test_raw_protocol_envelopes_are_closed(wire):
    from app.llm_runtime.provider import _raw_tool_calls
    assert _raw_tool_calls(wire) is None
