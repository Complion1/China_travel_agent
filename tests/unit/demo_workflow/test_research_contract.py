from types import SimpleNamespace
import pytest
from app.candidate_research import ControlResearchFailure, CandidateResearchControlFailureCode
from app.demo_workflow.nodes import failure_code
from app.demo_workflow.artifacts import DEMO_TERMINAL_REGISTRATIONS
from app.llm_runtime import LLMErrorCode


@pytest.mark.parametrize('code,terminal', [
    ('invalid_input','requirement_failed'), ('context_contract_error','research_context_invalid'),
    ('action_correction_exhausted','research_correction_exhausted'), ('judgment_patch_invalid','research_judgment_invalid'),
    ('judgment_required_missing', 'research_recovery_exhausted'),
    ('state_invariant_failed','research_state_invalid'), ('replay_mismatch','workflow_replay_mismatch'),
])
def test_research_safe_failure_categories_are_distinct_and_registered(code, terminal):
    failure = ControlResearchFailure(code=CandidateResearchControlFailureCode(code))
    assert failure_code(failure) == terminal
    assert terminal in {item.terminal_code for item in DEMO_TERMINAL_REGISTRATIONS}


@pytest.mark.parametrize('code', list(LLMErrorCode))
def test_model_output_failures_are_not_reported_as_provider_unavailability(code):
    local = {'configuration_error', 'capability_unavailable', 'schema_unsupported', 'input_too_large'}
    output = {'structured_output_invalid', 'tool_arguments_invalid', 'unknown_tool', 'output_truncated', 'malformed_provider_response'}
    terminal = ('workflow_contract_failed' if code.value in local else
        'workflow_model_output_invalid' if code.value in output else
        'workflow_budget_exhausted' if code.value == 'quota_exhausted' else 'workflow_provider_failed')
    assert failure_code(SimpleNamespace(failure_type='llm_failure',error=SimpleNamespace(code=code))) == terminal
