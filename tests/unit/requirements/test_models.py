"""DLG-T01: frozen public model and boundary contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.requirements import (
    ConfirmedTravelRequirement,
    FieldMetadataRegistry,
    InterestTag,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainState,
    RequirementErrorCode,
    RequirementExtractionFailed,
    RequirementFieldPath,
    RequirementIntent,
    RequirementInvalidated,
    RequirementIssue,
    RequirementTurnInput,
    TravelBudget,
    TravelParty,
    TravelPace,
    TravelRequirement,
    TravelRooms,
    TravelTimeWindowArrival,
)


def test_public_models_are_strict_and_forbid_extra_fields():
    with pytest.raises(ValidationError):
        TravelRequirement(destination=123)
    with pytest.raises(ValidationError):
        TravelRequirement(destination="杭州", extra_field="x")


def test_requirement_field_path_registry_covers_every_domain_leaf():
    paths = {item.value for item in RequirementFieldPath}
    assert {
        "time_window.hotel_check_in_date",
        "time_window.hotel_check_out_date",
        "time_window.hotel_nights",
        "budget.scope",
        "party.total_people",
        "preferences.daily_time_preferences",
        "preferences.free_text_notes",
        "inferred_scenarios",
    } <= paths


def test_fixed_chinese_preference_enums():
    assert {item.value for item in InterestTag} == {"人文", "自然", "美食", "打卡"}
    assert {item.value for item in TravelPace} == {"轻松", "标准", "紧凑"}


def test_unicode_text_boundaries_are_code_points():
    assert TravelRequirement(destination="杭" * 64).destination == "杭" * 64
    with pytest.raises(ValidationError):
        TravelRequirement(destination="杭" * 65)
    assert TravelTimeWindowArrival(hub_name="站" * 128).hub_name == "站" * 128
    with pytest.raises(ValidationError):
        TravelTimeWindowArrival(hub_name="站" * 129)
    assert TravelRooms(room_count=1, room_type_requirements="房" * 500)
    with pytest.raises(ValidationError):
        TravelRooms(room_count=1, room_type_requirements="房" * 501)


def test_people_and_child_age_boundaries():
    assert TravelParty(adults_count=100, children_count=1, children_ages=[17])
    with pytest.raises(ValidationError):
        TravelParty(adults_count=101)
    with pytest.raises(ValidationError):
        TravelParty(children_count=1, children_ages=[18])
    with pytest.raises(ValidationError):
        TravelParty(children_count=100, children_ages=[1] * 101)


def test_budget_scope_is_fixed_to_destination_only():
    budget = TravelBudget(total_cny=5000.0)
    assert budget.scope == "destination_only"
    with pytest.raises(ValidationError):
        TravelBudget(total_cny=5000.0, scope="all_transport")


@pytest.mark.parametrize(
    ("decision", "revision_text"),
    [("accept", None), ("revise", "改成上海")],
)
def test_confirmation_valid_combinations(decision, revision_text):
    action = RequirementConfirmation(decision=decision, revision_text=revision_text)
    assert action.decision == decision


@pytest.mark.parametrize(
    ("decision", "revision_text"),
    [("accept", "顺便修改"), ("revise", None), ("revise", "")],
)
def test_confirmation_rejects_invalid_combinations(decision, revision_text):
    with pytest.raises(ValidationError):
        RequirementConfirmation(decision=decision, revision_text=revision_text)


def test_turn_input_has_uuid_and_fixed_timezone():
    turn = RequirementTurnInput(
        schema_version="1.0",
        turn_id=uuid4(),
        user_message="杭州",
        session_date=__import__("datetime").date(2025, 6, 1),
        timezone="Asia/Shanghai",
        intent=RequirementIntent.INITIAL,
        current=RequirementDomainState(),
    )
    assert turn.timezone == "Asia/Shanghai"


def test_outcomes_are_narrow_and_do_not_expose_domain_state():
    requirement = TravelRequirement(destination="杭州")
    metadata = FieldMetadataRegistry()
    issue = RequirementIssue(
        code="normalization_error",
        field_path=RequirementFieldPath.DESTINATION,
        message="invalid",
    )
    clarification = NeedClarification(
        requirement=requirement, metadata=metadata, issues=(issue,), message="补充信息"
    )
    confirmation = NeedConfirmation(
        requirement=requirement, metadata=metadata, summary="summary"
    )
    confirmed = ConfirmedTravelRequirement(
        schema_version="1.0",
        requirement=requirement,
        metadata_without_raw_evidence=metadata,
        confirmation_summary_digest="digest",
    )
    failed = RequirementExtractionFailed(code=RequirementErrorCode.STRICT_SCHEMA_ERROR)
    invalidated = RequirementInvalidated(
        changed_fields=(RequirementFieldPath.DESTINATION,),
        before_digest="before",
        after_digest="after",
        reason="destination changed",
    )
    for outcome in (clarification, confirmation, confirmed, failed, invalidated):
        assert not hasattr(outcome, "state")
