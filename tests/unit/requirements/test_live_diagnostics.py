"""The live diagnostic callback preserves evidence without exposing payloads."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from tests.live.travel_pipeline.test_live_requirements_to_result import SafeSdkDiagnostics, requirement_missing_diagnostics, research_exception_diagnostics
from app.requirements import TravelRequirement
from tests.live.travel_pipeline.test_live_requirements_to_result import HarnessFailure, SafeQualificationReport
from tests.live.travel_pipeline.test_live_requirements_to_result import ObservedExtractor, planning_exception_diagnostics, outcome_summary
from app.llm_runtime import LLMRuntimeError, LLMErrorCode, ModelRole
from app.requirements import ExtractionRepairFeedback, RequirementFieldPath


@pytest.mark.asyncio
async def test_observed_extractor_preserves_public_calls_and_logs_only_feedback(capsys):
    first, repaired = object(), object()
    inner = SimpleNamespace(extract=AsyncMock(return_value=first), repair=AsyncMock(return_value=repaired))
    source = SimpleNamespace(start_session=Mock(return_value=inner))
    request = object()
    session = ObservedExtractor(source).start_session(request)
    assert await session.extract() is first
    feedback = ExtractionRepairFeedback(code="evidence_missing", field_paths=(RequirementFieldPath.DESTINATION,))
    assert await session.repair(feedback) is repaired
    source.start_session.assert_called_once_with(request)
    inner.extract.assert_awaited_once_with()
    inner.repair.assert_awaited_once_with(feedback)
    event = json.loads(capsys.readouterr().out.removeprefix("LIVE_EVENT "))
    assert event == {"event": "requirement_repair_feedback", "code": "evidence_missing", "fields": ["destination"]}


def test_qualification_case_gate_rejects_partial_or_duplicate_suites():
    report = SafeQualificationReport()
    items = [SimpleNamespace(nodeid=f"test_fixed_live_cases[{number:03d}]") for number in range(1, 17)]
    report.pytest_collection_modifyitems(items)
    with pytest.raises(HarnessFailure):
        report.pytest_collection_modifyitems(items[:-1])
    with pytest.raises(HarnessFailure):
        report.pytest_collection_modifyitems([items[0]] * 16)


def test_qualification_report_omits_failure_payload(capsys):
    report = SafeQualificationReport()
    report.pytest_runtest_logreport(SimpleNamespace(nodeid="test_fixed_live_cases[001]",
        when="call", passed=False, failed=True, longrepr="PRIVATE_PROVIDER_TEXT field mismatch: destination"))
    output = capsys.readouterr().out
    assert "PRIVATE_PROVIDER_TEXT" not in output
    event = json.loads(output.removeprefix("LIVE_EVENT "))
    assert event["field"] == "destination"
    assert event["case"] == "001"
    assert report.failed == 1


def test_sdk_callback_keeps_counts_and_omits_model_text(capsys):
    secret = "NEVER_PRINT_RAW_PROVIDER_CONTENT"
    message = SimpleNamespace(content=secret, usage_metadata={"input_tokens": 10}, response_metadata={"secret": secret},
                              invalid_tool_calls=[])
    response = SimpleNamespace(llm_output={"model_name": "deepseek-flash", "untrusted": secret,
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                       "completion_tokens_details": {"reasoning_tokens": 15, "secret": secret}}},
        generations=[[SimpleNamespace(message=message, generation_info={"finish_reason": "stop"})]])
    SafeSdkDiagnostics().on_llm_end(response)
    output = capsys.readouterr().out
    assert secret not in output
    event = json.loads(output.removeprefix("LIVE_EVENT "))
    assert event["usage"]["completion_tokens_details.reasoning_tokens"] == 15
    assert event["generations"][0]["finish_reason"] == "stop"


def test_sdk_error_callback_never_prints_exception_message(capsys):
    SafeSdkDiagnostics().on_llm_error(ValueError("NEVER_PRINT_EXCEPTION_PAYLOAD"))
    output = capsys.readouterr().out
    assert "NEVER_PRINT_EXCEPTION_PAYLOAD" not in output
    assert json.loads(output.removeprefix("LIVE_EVENT "))["error_type"] == "ValueError"


def test_missing_diagnostic_reports_field_paths_without_requirement_values(capsys):
    requirement = TravelRequirement(destination="PRIVATE_DESTINATION")
    requirement_missing_diagnostics(requirement)
    output = capsys.readouterr().out
    assert "PRIVATE_DESTINATION" not in output
    event = json.loads(output.removeprefix("LIVE_EVENT "))
    assert "party.adults_count" in event["fields"]
    assert "budget.total_cny" not in event["fields"]
    assert event["needs_interests"] is True


def test_research_trace_reports_location_only_and_restores_previous_trace(capsys):
    previous = sys.gettrace()
    namespace = {}
    exec(compile('def fail():\n    raise ValueError("PRIVATE_DIAGNOSTIC_MESSAGE")\n',
                 '/workspace/app/candidate_research/offline_fixture.py', 'exec'), namespace)
    try:
        with research_exception_diagnostics():
            namespace['fail']()
    except ValueError:
        pass
    else:
        raise AssertionError("diagnostics must not suppress exceptions")
    assert sys.gettrace() is previous
    output = capsys.readouterr().out
    assert "PRIVATE_DIAGNOSTIC_MESSAGE" not in output
    event = json.loads(output.removeprefix("LIVE_EVENT "))
    assert event["file"] == "offline_fixture.py" and event["line"] == 2
    assert event["error_type"] == "ValueError"


def test_planning_trace_is_bounded_private_and_preserves_control_error(capsys):
    from app.itinerary_planning.service import _Indeterminate
    previous = sys.gettrace()
    namespace = {"_Indeterminate": _Indeterminate}
    exec(compile('def fail():\n    raise _Indeterminate("PRIVATE_PLANNING_PAYLOAD")\n',
                 '/workspace/app/itinerary_planning/offline_fixture.py', 'exec'), namespace)
    with planning_exception_diagnostics():
        for _ in range(40):
            with pytest.raises(_Indeterminate):
                namespace['fail']()
    assert sys.gettrace() is previous
    output = capsys.readouterr().out
    assert "PRIVATE_PLANNING_PAYLOAD" not in output
    events = [json.loads(line.removeprefix("LIVE_EVENT ")) for line in output.splitlines()]
    assert events == [{"event": "planning_exception_location", "file": "offline_fixture.py",
                       "line": 2, "error_type": "_Indeterminate"}]


def test_outcome_summary_exposes_only_typed_size_counts():
    error = LLMRuntimeError(code=LLMErrorCode.INPUT_TOO_LARGE, role=ModelRole.ITINERARY_PLANNING,
                            retryable=False, possible_duplicate=False, actual_bytes=600000, max_bytes=524288)
    outcome = SimpleNamespace(failure=SimpleNamespace(error=error, private="PRIVATE_PLANNING_PAYLOAD"))
    summary = outcome_summary(outcome)
    assert summary["actual_bytes"] == 600000 and summary["max_bytes"] == 524288
    assert "PRIVATE_PLANNING_PAYLOAD" not in json.dumps(summary)
    outcome.failure.error = SimpleNamespace(code=LLMErrorCode.INPUT_TOO_LARGE,
                                           actual_bytes="PRIVATE_PLANNING_PAYLOAD", max_bytes=524288)
    assert "actual_bytes" not in outcome_summary(outcome)


def test_outcome_summary_exposes_only_typed_map_failure_fields():
    from app.baidu_map import BaiduMapErrorCode
    from app.itinerary_planning import MapPlanningFailure
    failure = MapPlanningFailure(capability="route_matrix", error_code=BaiduMapErrorCode.TIMEOUT_ERROR,
                                 action_ordinal=3, retryable=True, possible_duplicate=False)
    summary = outcome_summary(SimpleNamespace(failure=failure))
    assert summary["capability"] == "route_matrix" and summary["action_ordinal"] == 3
    assert summary["retryable"] is True and summary["error_code"] == "timeout_error"
    untyped = SimpleNamespace(capability="PRIVATE_PROVIDER_PAYLOAD", action_ordinal=3, retryable=True)
    assert "capability" not in outcome_summary(SimpleNamespace(failure=untyped))


def test_backup_slot_diagnostic_is_closed_and_bounded(capsys):
    from tests.live.travel_pipeline.test_live_requirements_to_result import backup_slot_diagnostics
    valid = SimpleNamespace(day_no=2, slots=SimpleNamespace(
        expected=("attraction", "dinner"), actual=()), private="PRIVATE_MODEL_ARGUMENTS")
    backup_slot_diagnostics(valid)
    output = capsys.readouterr().out
    assert "PRIVATE_MODEL_ARGUMENTS" not in output
    assert json.loads(output.removeprefix("LIVE_EVENT ")) == {
        "event": "planning_backup_slots", "day_no": 2,
        "expected": ["attraction", "dinner"], "actual": [],
    }
    for actual in (("PRIVATE_MODEL_ARGUMENTS",), ("lunch",) * 4, "lunch", None, (False,)):
        backup_slot_diagnostics(SimpleNamespace(day_no=2, slots=SimpleNamespace(expected=(), actual=actual)))
    for day_no in (True, 0, 65, "PRIVATE_MODEL_ARGUMENTS"):
        backup_slot_diagnostics(SimpleNamespace(day_no=day_no, slots=valid.slots))
    assert capsys.readouterr().out == ""
