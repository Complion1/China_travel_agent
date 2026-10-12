"""U08-01～05：用途完整、来源信号、unknown与反证；旧内容不补写。"""
import json
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.candidate_research import CandidateRecordV2, MealAssessment, MealIntent, MatchVerdict, MealReasonCode, MealJudgmentUpdate, EvidenceHandle
from app.candidate_research.artifacts import CandidateResearchArtifact, CandidateResearchLegacyArtifact
from app.candidate_research.purpose import apply_meal_assessment, normalise_meal_update
from tests.unit.itinerary_planning.test_service_initial import _upstream


def candidate():
    artifact = _upstream(uuid4())[3]
    return CandidateRecordV2.model_validate_json(artifact.record.state.candidates[0].model_dump_json(), strict=True)


@pytest.mark.parametrize("reason", (MealReasonCode.BUSINESS_HOURS_UNKNOWN, MealReasonCode.EVIDENCE_INSUFFICIENT))
@pytest.mark.parametrize("verdict", (MatchVerdict.UNRESOLVED, MatchVerdict.AMBIGUOUS))
def test_unknown_does_not_revoke_confirmed_association(reason, verdict):
    current = candidate().meal_assessments[0]
    incoming = MealAssessment(meal_intent=MealIntent.LUNCH, verdict=verdict, reason_code=reason, evidence_indexes=(0,))
    assert apply_meal_assessment(current, incoming) == current


def test_explicit_conflict_revokes_only_corresponding_purpose():
    original = candidate()
    incoming = MealAssessment(meal_intent=MealIntent.LUNCH, verdict=MatchVerdict.NO_MATCH,
        reason_code=MealReasonCode.BUSINESS_HOURS_CONFLICTS, evidence_indexes=(0,))
    assessments = (apply_meal_assessment(original.meal_assessments[0], incoming), original.meal_assessments[1])
    updated = CandidateRecordV2(**{**original.model_dump(mode="python"), "meal_assessments": assessments,
        "meal_intents": (MealIntent.DINNER,)})
    assert updated.meal_intents == (MealIntent.DINNER,)


@pytest.mark.parametrize("mutation", ("omit", "foreign", "positive_drift"))
def test_incomplete_or_unowned_purpose_state_is_rejected(mutation):
    data = candidate().model_dump(mode="json")
    if mutation == "omit":
        data["meal_assessments"].pop()
    elif mutation == "foreign":
        data["meal_assessments"][0]["evidence_indexes"] = [1]
    else:
        data["meal_intents"] = []
    with pytest.raises(ValidationError):
        CandidateRecordV2.model_validate_json(json.dumps(data), strict=True)


@pytest.mark.parametrize("reason,source,confirmed", (
    (MealReasonCode.TARGETED_MEAL_SEARCH, {"accepted_search": {"meal_intent": "lunch"}}, True),
    (MealReasonCode.TARGETED_MEAL_SEARCH, {"accepted_search": {"meal_intent": None}}, False),
    (MealReasonCode.BUSINESS_HOURS_SUPPORTS, {"result": {"record_type": "details_success", "details": {"business_hours": []}}}, False),
    (MealReasonCode.BUSINESS_HOURS_SUPPORTS, {"result": {"record_type": "details_success", "details": {"business_hours": ["synthetic hours"]}}}, True),
))
def test_confirmation_requires_real_target_or_nonempty_hours(reason, source, confirmed):
    update = MealJudgmentUpdate(candidate_handle="c:0", meal_intent=MealIntent.LUNCH,
        verdict=MatchVerdict.CONFIRMED, reason_code=reason, evidence_handles=(EvidenceHandle(handle_id="e:0"),))
    result = normalise_meal_update(update, {"e:0": source})
    assert (result.verdict is MatchVerdict.CONFIRMED) is confirmed


def test_legacy_artifact_roundtrip_has_no_new_fields():
    artifact = _upstream(uuid4())[3]
    data = artifact.model_dump(mode="json")
    data["schema_version"] = "1.0"
    for item in data["record"]["state"]["candidates"]:
        item["schema_version"] = "1.0"
        item.pop("meal_assessments")
    data.pop("correction_state")
    data.pop("qualification_state")
    legacy = CandidateResearchLegacyArtifact.model_validate_json(json.dumps(data), strict=True)
    encoded = legacy.model_dump_json(exclude_none=False)
    assert "meal_assessments" not in encoded
    assert CandidateResearchLegacyArtifact.model_validate_json(encoded, strict=True).model_dump_json(exclude_none=False) == encoded


def test_owner_admission_rejects_forged_positive_state():
    from app.candidate_research import candidate_admitted_purposes
    original = candidate()
    forged = original.model_copy(update={"meal_intents":()})
    assert not candidate_admitted_purposes(forged)
    assert candidate_admitted_purposes(original)


def test_restaurant_supply_requires_supported_restaurant_role():
    from app.candidate_research import candidate_admitted_purposes, ResearchRole, RoleVerdict, RoleReasonCode
    original = candidate()
    attraction = original.model_copy(update={"roles":tuple(role.model_copy(update={"verdict":RoleVerdict.UNRESOLVED,
        "reason_code":RoleReasonCode.EVIDENCE_INSUFFICIENT}) if role.role is ResearchRole.RESTAURANT else role for role in original.roles)})
    assert candidate_admitted_purposes(attraction) == ("attraction",)
