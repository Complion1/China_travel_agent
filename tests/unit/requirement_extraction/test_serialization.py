import json
from datetime import date
from uuid import UUID

from app.requirement_extraction.serialization import (
    serialize_extract_input,
    serialize_repair_input,
)
from app.requirements import (
    ExtractionInput,
    ExtractionRepairFeedback,
    RequirementFieldPath,
    RequirementIntent,
    TravelRequirement,
)


def request(message: str = "我想去杭州") -> ExtractionInput:
    return ExtractionInput(
        turn_id=UUID("00000000-0000-0000-0000-000000000001"),
        user_message=message,
        session_date=date(2026, 7, 14),
        timezone="Asia/Shanghai",
        current_requirement=TravelRequirement(),
        intent=RequirementIntent.INITIAL,
    )


def test_extract_serialization_is_pretty_sorted_unicode_json():
    text = serialize_extract_input(request())
    assert "我想去杭州" in text
    assert "\\u6211" not in text
    assert text.startswith('{\n  "current_requirement"')
    assert json.loads(text)["session_date"] == "2026-07-14"


def test_extract_serialization_keeps_all_null_fields():
    payload = json.loads(serialize_extract_input(request()))
    assert payload["current_requirement"]["destination"] is None
    assert payload["current_requirement"]["time_window"]["arrival"]["hub_name"] is None


def test_extract_serialization_excludes_turn_id():
    text = serialize_extract_input(request())
    assert "turn_id" not in text
    assert "00000000-0000" not in text


def test_extract_serialization_has_exact_top_level_keys():
    payload = json.loads(serialize_extract_input(request()))
    assert set(payload) == {
        "current_requirement",
        "intent",
        "session_date",
        "timezone",
        "user_message",
    }


def test_serialization_does_not_append_terminal_newline():
    assert not serialize_extract_input(request()).endswith("\n")


def test_repair_serialization_adds_only_typed_feedback():
    original = json.loads(serialize_extract_input(request()))
    feedback = ExtractionRepairFeedback(
        code="evidence_missing",
        field_paths=(RequirementFieldPath.DESTINATION,),
    )
    repaired = json.loads(serialize_repair_input(request(), feedback))
    assert {key: value for key, value in repaired.items() if key != "repair_feedback"} == original
    assert repaired["repair_feedback"] == {
        "code": "evidence_missing",
        "field_paths": ["destination"],
    }


def test_repair_serialization_uses_sorted_feedback_paths():
    feedback = ExtractionRepairFeedback(
        code="evidence_duplicate",
        field_paths=(RequirementFieldPath.PACE, RequirementFieldPath.DESTINATION),
    )
    payload = json.loads(serialize_repair_input(request(), feedback))
    assert payload["repair_feedback"]["field_paths"] == ["destination", "preferences.pace"]


def test_long_input_is_not_truncated_by_serializer():
    message = "杭" * 8000
    payload = json.loads(serialize_extract_input(request(message)))
    assert payload["user_message"] == message
