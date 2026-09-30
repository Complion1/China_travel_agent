import json
import logging

from app.llm_runtime.diagnostics import LOGGER, error_context, record_error
from app.llm_runtime.models import ToolContract, LLMErrorCode
from app.itinerary_planning.models import GetCandidateEvidenceArgs
from tests.unit.llm_runtime.test_runtime_contract_edges import operation, response, PROMPT, assert_failure
from tests.unit.llm_runtime.test_provider_adapter import make_adapter
from langchain_openai.chat_models.base import _convert_dict_to_message


def test_real_runtime_records_count_and_redacts_unknown_keys(caplog):
    args = {'candidate_handles': [f'SECRET-{i}' for i in range(20)], 'SECRET-KEY': 'SECRET-VALUE'}
    op, _, _ = operation([response(content=None, calls=(('c', 'get_candidate_evidence', args),), finish='tool_calls')])
    with error_context('run-example'):
        assert_failure(op.invoke_tools(PROMPT, (ToolContract('get_candidate_evidence', 'Evidence', GetCandidateEvidenceArgs),)), LLMErrorCode.TOOL_ARGUMENTS_INVALID)
    event = json.loads(next(r.message for r in caplog.records if r.name == LOGGER.name))
    assert event['run_id'] == 'run-example'
    limit = next(e for e in event['errors'] if e['type'] == 'too_long')
    assert limit['path'] == ['candidate_handles']
    assert (limit['actual_length'], limit['max_length']) == (20, 16)
    assert event['location']
    assert 'SECRET' not in caplog.text


def test_sdk_invalid_json_records_position_without_content(caplog):
    message = _convert_dict_to_message({'role':'assistant', 'content':'', 'tool_calls':[{'id':'c','type':'function','function':{'name':'SECRET-NAME','arguments':'{"secret":"SECRET-VALUE"} {}'}}]})
    message.response_metadata['finish_reason'] = 'tool_calls'
    make_adapter()._unwrap_ai_message(message)
    event = json.loads(next(r.message for r in caplog.records if r.name == LOGGER.name))
    assert event['event'] == 'tool_json_invalid' and event['position'] > 0
    assert event['finish_reason'] == 'tool_calls'
    assert 'SECRET' not in caplog.text


def test_live_sink_survives_prior_global_suppression_and_appends(tmp_path):
    from tests.live.travel_pipeline.test_live_requirements_to_result import enable_error_locations
    disabled = logging.root.manager.disable
    root_level = logging.getLogger().level
    levels = [(h, h.level) for h in logging.getLogger().handlers]
    old = (LOGGER.level, LOGGER.propagate, LOGGER.disabled, list(LOGGER.handlers))
    try:
        logging.disable(logging.CRITICAL)
        enable_error_locations(tmp_path)
        with error_context('run-test'):
            record_error('tool_arguments_invalid', errors=[{'path':['candidate_handles'], 'actual_length':20, 'max_length':16}])
        enable_error_locations(tmp_path)
        record_error('planning_submit_rejected', reason='route_end_time_mismatch', terminal_only=True)
        events = [json.loads(line) for line in (tmp_path/'error-locations.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len(events) == 2 and events[0]['run_id'] == 'run-test'
        assert events[1]['terminal_only'] is True
    finally:
        for h in list(LOGGER.handlers):
            if h not in old[3]: LOGGER.removeHandler(h); h.close()
        LOGGER.setLevel(old[0]); LOGGER.propagate=old[1]; LOGGER.disabled=old[2]
        logging.getLogger().setLevel(root_level)
        for h, level in levels: h.setLevel(level)
        logging.disable(disabled)


def test_broken_diagnostic_sink_cannot_replace_original_error():
    class Broken(logging.Handler):
        def emit(self, record): raise OSError('SECRET')
    handler=Broken()
    LOGGER.addHandler(handler)
    try:
        op, _, _ = operation([response(content=None, calls=(('c','get_candidate_evidence',{'candidate_handles':[]}),), finish='tool_calls')])
        assert_failure(op.invoke_tools(PROMPT, (ToolContract('get_candidate_evidence','Evidence',GetCandidateEvidenceArgs),)), LLMErrorCode.TOOL_ARGUMENTS_INVALID)
    finally: LOGGER.removeHandler(handler)
