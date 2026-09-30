"""G-EXPORT: reconcile persisted records without inventing token usage."""
import json
from uuid import uuid4

import httpx
import pytest

from app.llm_runtime import ModelRole, PromptEnvelope, ToolContract, ToolResult, interaction_call
from scripts.export_interactions import (
    ExportError, fallback_records, merge_records, render_summary, summarize, write_export,
)
from tests.unit.llm_runtime.test_raw_interaction_capture import setup, response, Value


@pytest.mark.asyncio
@pytest.mark.parametrize('diagnostic_cancel', [False, True])
async def test_live_archive_failure_preserves_primary_and_closes_runtime(monkeypatch, tmp_path, diagnostic_cancel):
    import asyncio
    from types import SimpleNamespace
    from tests.live.travel_pipeline import test_live_requirements_to_result as worker
    primary = worker.HarnessFailure('OFFLINE_PRIMARY_FAILURE')
    secondary = asyncio.CancelledError() if diagnostic_cancel else RuntimeError('PRIVATE_DIAGNOSTIC_FAILURE')
    closed = []
    class Runtime:
        async def aclose(self):
            closed.append('llm')
    class Maps:
        async def __aenter__(self):
            raise primary
        async def __aexit__(self, *args):
            pass
    class Recorder:
        async def read_run(self, run_id):
            raise secondary
    emitted = []
    monkeypatch.setenv('LIVE_DIAGNOSTIC_DIR', str(tmp_path))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'OFFLINE_SENTINEL')
    monkeypatch.setenv('BAIDU_MAPS_API_KEY', 'OFFLINE_SENTINEL')
    monkeypatch.setattr(worker, 'DeepSeekRuntime', lambda **kwargs: Runtime())
    monkeypatch.setattr(worker, 'LangChainDeepSeekAdapter', lambda settings: object())
    monkeypatch.setattr(worker, 'ObservedProvider', lambda adapter: adapter)
    monkeypatch.setattr(worker, 'load_settings', lambda **kwargs: object())
    monkeypatch.setattr(worker, 'load_baidu_map_settings', lambda **kwargs: object())
    monkeypatch.setattr(worker, 'create_baidu_map_runtime', lambda **kwargs: Maps())
    monkeypatch.setattr(worker, 'emit', lambda event, **kwargs: emitted.append((event, kwargs)))
    with pytest.raises(asyncio.CancelledError if diagnostic_cancel else worker.HarnessFailure) as caught:
        await worker.production_root(SimpleNamespace(interaction_recorder=Recorder()))
    assert caught.value is (secondary if diagnostic_cancel else primary) and closed == ['llm']
    assert not emitted if diagnostic_cancel else emitted[-1][1]['complete'] is False
    assert 'PRIVATE_DIAGNOSTIC_FAILURE' not in str(emitted)


@pytest.mark.asyncio
async def test_live_root_prepares_fallback_before_runtime_or_external_calls(monkeypatch, tmp_path):
    from tests.live.travel_pipeline import test_live_requirements_to_result as worker
    prepared = []
    class Persistence:
        async def close(self):
            pass
    async def create(**kwargs):
        path = kwargs['interaction_fallback_dir']
        assert path == tmp_path / 'fallback' and path.is_dir()
        prepared.append(path)
        return Persistence()
    async def root(persistence, **kwargs):
        return {'offline': True}
    monkeypatch.setenv('LIVE_DIAGNOSTIC_DIR', str(tmp_path))
    monkeypatch.setattr(worker, 'settings', lambda: object())
    monkeypatch.setattr(worker, 'create_artifact_persistence_runtime', create)
    monkeypatch.setattr(worker, 'production_root', root)
    assert await worker.run('root') == {'offline': True}
    assert prepared == [tmp_path / 'fallback']


@pytest.mark.asyncio
async def test_actual_usage_duplicates_fallback_dedup_and_no_overwrite(monkeypatch, tmp_path):
    def handler(request):
        tools = [dict(id='submit', type='function', function=dict(name='submit_itinerary', arguments='{"value":1}'))]
        return httpx.Response(200, content=response(content=None, finish='tool_calls', tools=tools))
    runtime, recorder, scope = setup(monkeypatch, handler)
    try:
        operation = runtime.start_operation(ModelRole.ITINERARY_PLANNING)
        tool = ToolContract(name='submit_itinerary', description='fixture', args_schema=Value)
        with scope:
            with interaction_call('planning') as identity:
                first = await operation.ainvoke_tools(PromptEnvelope(system_text='fixture', user_text='fixture'), (tool,), request_stage='transport')
            await identity.record_validation(stage='submit', passed=False, code='fixture_rejected', budget_category='submit_repair')
            with interaction_call('planning'):
                await operation.acontinue_tools((ToolResult(call_id=first.calls[0].call_id, content='{"status":"rejected"}'),),
                    tools=(tool,), request_stage='submit_repair')
        await scope.finish(stop_code='fixture_finished')
        run_id = recorder.records[0].run_id
        records = merge_records(run_id, recorder.records, recorder.records)
        data = summarize(records)
        assert len(records) == len(recorder.records)
        assert data['known_usage']['completion_tokens'] == 10
        assert data['known_usage']['reasoning_tokens'] == 6
        assert all(row[5] == 2 for row in data['rows'])
        assert data['records_complete'] and len(data['repeated_submissions']) == 1
        assert '不估算片段 token' in render_summary(run_id, records)
        targets = write_export(run_id, tmp_path / str(run_id), records)
        assert fallback_records(tmp_path, run_id) == records
        original = [target.read_bytes() for target in targets]
        with pytest.raises(ExportError, match='export_output_exists'):
            write_export(run_id, tmp_path / str(run_id), records)
        assert [target.read_bytes() for target in targets] == original
        with pytest.raises(ExportError, match='export_record_conflict'):
            merge_records(run_id, records, (records[0].model_copy(update={'captured_at': records[-1].captured_at}),))
        with pytest.raises(ExportError, match='export_run_mismatch'):
            merge_records(uuid4(), records)
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_missing_reasoning_and_end_summary_remain_unknown(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=response(missing=('reasoning_tokens',)))
    runtime, recorder, scope = setup(monkeypatch, handler)
    try:
        with scope, interaction_call('extract'):
            await runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION).ainvoke_structured(
                PromptEnvelope(system_text='fixture', user_text='fixture'), Value)
        data = summarize(recorder.records)
        assert data['known_usage']['completion_tokens'] == 5
        assert data['known_usage']['reasoning_tokens'] is None
        assert data['rows'][0][5] is None
        assert not data['records_complete'] and data['run_summary_count'] == 0
        await scope.finish(stop_code='fixture_finished')
        assert summarize(recorder.records)['records_complete']
    finally:
        await runtime.aclose()


def test_invalid_fallback_is_visible(tmp_path):
    run_id = uuid4()
    folder = tmp_path / str(run_id)
    folder.mkdir()
    (folder / 'interactions.jsonl').write_text('{broken', encoding='utf8')
    with pytest.raises(ExportError, match='export_fallback_invalid'):
        fallback_records(tmp_path, run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [b'{"choices":null}', b'{"choices":1}', b'{"choices":[{"message":{"tool_calls":1}}]}',
    b'{"choices":[{"message":{"tool_calls":[{"function":null}]}}]}'])
async def test_malformed_provider_body_is_still_exportable(monkeypatch, tmp_path, raw):
    runtime, recorder, scope = setup(monkeypatch, lambda request: httpx.Response(200, content=raw))
    try:
        with scope, interaction_call('extract'):
            await runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION).ainvoke_structured(
                PromptEnvelope(system_text='fixture', user_text='fixture'), Value)
        await scope.finish(stop_code='fixture_failed')
        paths = write_export(recorder.records[0].run_id, tmp_path, recorder.records)
        assert all(path.exists() for path in paths)
        assert any(record.payload.body.content.encode() == raw for record in recorder.records if record.record_kind == 'response')
        assert summarize(recorder.records)['known_usage']['completion_tokens'] is None
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_run_summary_counts_must_reconcile_and_negative_nonreasoning_is_unknown(monkeypatch):
    raw = json.loads(response())
    raw['usage']['reasoning_tokens'] = 9
    runtime, recorder, scope = setup(monkeypatch, lambda request: httpx.Response(200, content=json.dumps(raw).encode()))
    try:
        with scope, interaction_call('extract'):
            await runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION).ainvoke_structured(
                PromptEnvelope(system_text='fixture', user_text='fixture'), Value)
        await scope.finish(stop_code='fixture_failed')
        assert summarize(recorder.records)['rows'][0][5] is None
        end = recorder.records[-1]
        wrong = end.model_copy(update={'payload': end.payload.model_copy(update={'observed_admitted_calls': 2, 'observed_provider_attempts': 2})})
        assert not summarize([*recorder.records[:-1], wrong])['records_complete']
    finally:
        await runtime.aclose()
