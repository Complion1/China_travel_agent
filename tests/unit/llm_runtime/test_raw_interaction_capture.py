"""GCAPTURE: actual locked ChatDeepSeek/OpenAI serialization, offline HTTP."""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

import app.llm_runtime.provider as provider
from app.llm_runtime import (
    DeepSeekRuntime, ModelRole, PromptEnvelope, ToolContract, ToolResult,
    StructuredInvocationSuccess, ToolCallsReady, LLMInvocationFailure,
    interaction_call, interaction_run_context,
)
from app.llm_runtime.settings import DeepSeekSettings
from app.llm_runtime.interaction_recording import _MemoryRecorder
from tests.unit.llm_runtime.fakes import RecordingSleeper


class Value(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    value: int


def response(*, content='{"value":1}', finish='stop', tools=None, missing=()):
    usage = dict(prompt_tokens=10, prompt_cache_hit_tokens=8, prompt_cache_miss_tokens=2,
        completion_tokens=5, reasoning_tokens=3, total_tokens=15)
    for key in missing:
        usage.pop(key)
    message = dict(role='assistant', content=content, reasoning_content='实际返回的思考')
    if tools is not None:
        message['tool_calls'] = tools
    return json.dumps(dict(id='offline', object='chat.completion', created=1, model='deepseek-v4-flash',
        choices=[dict(index=0, message=message, finish_reason=finish)], usage=usage), ensure_ascii=False).encode()


def setup(monkeypatch, handler, recorder=None):
    original = provider.ChatDeepSeek
    def factory(**kwargs):
        model = original(**kwargs)
        client = model.root_async_client._client
        client._transport = httpx.MockTransport(handler)
        client._mounts = {}
        return model
    monkeypatch.setattr(provider, 'ChatDeepSeek', factory)
    adapter = provider.LangChainDeepSeekAdapter(DeepSeekSettings(api_key=SecretStr('SECRET_SENTINEL')))
    runtime = DeepSeekRuntime(adapter, RecordingSleeper())
    recorder = recorder or _MemoryRecorder()
    scope = interaction_run_context(run_id=uuid4(), trace_id=uuid4(), recorder=recorder)
    return runtime, recorder, scope


@pytest.mark.asyncio
@pytest.mark.parametrize('variant', ['normal', 'outer_json', 'content_json', 'schema', 'length', 'missing_usage'])
async def test_raw_before_sdk_failure_and_missing_usage(monkeypatch, variant):
    raw = {'normal': response(), 'outer_json': b'not-http-json', 'content_json': response(content='{'),
        'schema': response(content='{"value":"wrong"}'), 'length': response(finish='length'),
        'missing_usage': response(missing=('reasoning_tokens', 'prompt_cache_miss_tokens'))}[variant]
    requests = []
    def handler(request):
        requests.append(request.content)
        return httpx.Response(200, content=raw, headers={'content-type':'application/json'})
    runtime, recorder, scope = setup(monkeypatch, handler)
    try:
        with scope, interaction_call('extract'):
            outcome = await runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION).ainvoke_structured(PromptEnvelope(system_text='规则', user_text='文本'), Value)
        await scope.finish(stop_code='offline_finished')
        responses = [r for r in recorder.records if r.record_kind == 'response']
        assert len(requests) == len(responses) == 1
        assert responses[0].payload.body.content.encode() == raw
        assert isinstance(outcome, StructuredInvocationSuccess) if variant in {'normal','missing_usage'} else isinstance(outcome, LLMInvocationFailure)
        if variant == 'missing_usage':
            assert responses[0].payload.usage.reasoning_tokens is None
            assert not responses[0].payload.usage.complete
        assert recorder.records[-1].payload.records_complete
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_byte_quota_and_callback_failure_issue_no_request(monkeypatch):
    wire = []
    def handler(request):
        wire.append(request.content)
        return httpx.Response(200, content=response(content=None, finish='tool_calls', tools=[dict(id='q', type='function', function=dict(name='query', arguments='{"value":1}'))]))
    runtime, recorder, scope = setup(monkeypatch, handler)
    tool = ToolContract(name='query', description='fixture', args_schema=Value)
    claims = []
    async def callback(identity):
        claims.append(identity)
    async def failed_callback(identity):
        raise RuntimeError('FIXTURE_ADMISSION_FAILED')
    try:
        operation = runtime.start_operation(ModelRole.ITINERARY_PLANNING)
        with scope:
            with interaction_call('planning'):
                too_big = await operation.ainvoke_tools(PromptEnvelope(system_text='fixture', user_text='x' * 2097153),
                    (tool,), admission_callback=callback, request_stage='preparation')
            assert isinstance(too_big, LLMInvocationFailure) and not claims and not wire
            operation = runtime.start_operation(ModelRole.ITINERARY_PLANNING)
            with interaction_call('planning'), pytest.raises(RuntimeError, match='FIXTURE_ADMISSION_FAILED'):
                await operation.ainvoke_tools(PromptEnvelope(system_text='fixture', user_text='fixture'), (tool,),
                    admission_callback=failed_callback, request_stage='preparation')
            assert not wire and operation._semantic_invocations_used == 0
            operation = runtime.start_operation(ModelRole.ITINERARY_PLANNING)
            for index in range(17):
                with interaction_call('planning'):
                    if index == 0:
                        result = await operation.ainvoke_tools(PromptEnvelope(system_text='fixture', user_text='fixture'), (tool,), admission_callback=callback, request_stage='transport')
                    else:
                        result = await operation.acontinue_tools((ToolResult(call_id=result.calls[0].call_id, content='{}'),),
                            tools=(tool,), admission_callback=callback, request_stage='transport')
            with interaction_call('planning'):
                quota = await operation.acontinue_tools((ToolResult(call_id=result.calls[0].call_id, content='{}'),),
                    tools=(tool,), admission_callback=callback, request_stage='transport')
            assert isinstance(quota, LLMInvocationFailure) and len(wire) == len(claims) == 17
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_configured_api_secret_is_removed_from_controller_feedback(monkeypatch):
    runtime, recorder, scope = setup(monkeypatch, lambda request: httpx.Response(200, content=response()))
    try:
        with scope:
            with interaction_call('extract') as identity:
                await runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION).ainvoke_structured(
                    PromptEnvelope(system_text='fixture', user_text='fixture'), Value)
            await identity.record_tool_result(tool_name='fixture', ordinal=0, outcome='success', feedback='SECRET_SENTINEL')
        assert 'SECRET_SENTINEL' not in '\n'.join(record.model_dump_json() for record in recorder.records)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('timeouts', [1, 2])
async def test_retry_preserves_unknown_attempt_usage(monkeypatch, timeouts):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        if count <= timeouts:
            raise httpx.ReadTimeout('OFFLINE_PRIVATE_EXCEPTION', request=request)
        return httpx.Response(200, content=response(), headers={'content-type':'application/json'})
    runtime, recorder, scope = setup(monkeypatch, handler)
    try:
        with scope, interaction_call('extract'):
            await runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION).ainvoke_structured(PromptEnvelope(system_text='规则', user_text='文本'), Value)
        await scope.finish(stop_code='offline_finished')
        assert count == 2
        calls = [r for r in recorder.records if r.record_kind == 'call_summary']
        assert len(calls) == 1 and calls[0].payload.observed_provider_attempts == 2
        assert not calls[0].payload.records_complete and not calls[0].payload.usage.complete
        assert not recorder.records[-1].payload.records_complete and not recorder.records[-1].payload.known_usage.complete
        assert calls[0].payload.usage.completion_tokens == (5 if timeouts == 1 else None)
        assert 'OFFLINE_PRIVATE_EXCEPTION' not in '\n'.join(r.model_dump_json() for r in recorder.records)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_actual_continuation_schema_reasoning_history_and_once_admission(monkeypatch):
    wire = []
    def handler(request):
        wire.append(json.loads(request.content))
        tool = dict(id=f'tool-{len(wire)}', type='function', function=dict(name='query' if len(wire)==1 else 'submit', arguments='{"value":1}'))
        return httpx.Response(200, content=response(content=None, finish='tool_calls', tools=[tool]), headers={'content-type':'application/json'})
    runtime, recorder, scope = setup(monkeypatch, handler)
    query, submit = (ToolContract(name=name, description=name, args_schema=Value) for name in ('query','submit'))
    claims = []
    async def admitted(identity):
        claims.append(identity)
    try:
        operation = runtime.start_operation(ModelRole.ITINERARY_PLANNING)
        with scope:
            with interaction_call('planning'):
                first = await operation.ainvoke_tools(PromptEnvelope(system_text='规则', user_text='SECRET_SENTINEL'), (query,), admission_callback=admitted, request_stage='transport')
            with interaction_call('planning'):
                second = await operation.acontinue_tools((ToolResult(call_id=first.calls[0].call_id, content='{"status":"success"}'),),
                    tools=(submit,), admission_callback=admitted, request_stage='submit_repair')
        await scope.finish(stop_code='offline_finished')
        assert isinstance(first, ToolCallsReady) and isinstance(second, ToolCallsReady)
        assert [claim.logical_call_index for claim in claims] == [0,1]
        assert [tool['function']['name'] for tool in wire[1]['tools']] == ['submit']
        prior = next(message for message in wire[1]['messages'] if message['role']=='assistant')
        assert prior['reasoning_content'] == '实际返回的思考'
        assert prior['tool_calls'][0]['id'] == 'tool-1'
        assert next(message for message in wire[1]['messages'] if message['role']=='tool')['tool_call_id'] == 'tool-1'
        assert 'SECRET_SENTINEL' not in '\n'.join(record.model_dump_json() for record in recorder.records)
        assert 'authorization' not in '\n'.join(record.model_dump_json().lower() for record in recorder.records)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['identity', 'exception'])
async def test_recorder_fault_preserves_sdk_success_and_reports_missing(monkeypatch, fault):
    from app.llm_runtime import InteractionWriteResult
    from app.llm_runtime.interaction_recording import _MemoryRecorder
    class Recorder(_MemoryRecorder):
        async def append(self, record):
            if record.record_kind == 'request':
                if fault == 'exception':
                    raise RuntimeError('PRIVATE_STORAGE_EXCEPTION')
                return InteractionWriteResult(record_id=uuid4(), status='saved', code=None)
            return await super().append(record)
    recorder = Recorder()
    runtime, _, scope = setup(monkeypatch, lambda request: httpx.Response(200, content=response()), recorder)
    try:
        with scope, interaction_call('extract'):
            result = await runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION).ainvoke_structured(
                PromptEnvelope(system_text='fixture', user_text='fixture'), Value)
        await scope.finish(stop_code='offline_finished')
        await scope.finish(stop_code='ignored_second_finish')
        assert isinstance(result, StructuredInvocationSuccess)
        ends = [record for record in recorder.records if record.record_kind == 'run_summary']
        assert len(ends) == 1 and ends[0].payload.stop_code == 'offline_finished'
        assert not ends[0].payload.records_complete
        assert ends[0].payload.record_failure_count == len(ends[0].payload.missing_record_ids) == 1
        assert 'PRIVATE_STORAGE_EXCEPTION' not in '\n'.join(record.model_dump_json() for record in recorder.records)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_recorder_cancellation_propagates_and_restores_context(monkeypatch):
    from app.llm_runtime.interaction_recording import _resolve_call, _run_context
    class Recorder:
        async def append(self, record):
            raise asyncio.CancelledError()
    runtime, _, scope = setup(monkeypatch, lambda request: httpx.Response(200, content=response()), Recorder())
    try:
        with pytest.raises(asyncio.CancelledError):
            with scope, interaction_call('extract'):
                await runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION).ainvoke_structured(
                    PromptEnvelope(system_text='fixture', user_text='fixture'), Value)
        assert _resolve_call() is None and _run_context.get() is None
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_public_extraction_session_correlates_actual_extract_and_repair(monkeypatch):
    from tests.unit.requirement_extraction.test_session import session, strict_dto
    from app.requirements import ExtractionRepairRequired, ExtractionAttemptSucceeded
    requests = []
    valid_response = response(content=strict_dto().model_dump_json())
    def handler(request):
        requests.append(request.content)
        return httpx.Response(200, content=response(content='{}') if len(requests) == 1 else valid_response)
    runtime, recorder, scope = setup(monkeypatch, handler)
    try:
        extraction = session(runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION))
        with scope:
            initial = await extraction.extract()
            assert isinstance(initial, ExtractionRepairRequired)
            repaired = await extraction.repair(initial.feedback)
            assert isinstance(repaired, ExtractionAttemptSucceeded)
        await scope.finish(stop_code='offline_finished')
        calls = [record for record in recorder.records if record.record_kind == 'call_summary']
        assert [record.purpose for record in calls] == ['extract', 'repair']
        assert len({record.call_id for record in calls}) == len(requests) == 2
        assert recorder.records[-1].payload.known_usage.completion_tokens == 10
        assert recorder.records[-1].payload.records_complete
    finally:
        await runtime.aclose()
