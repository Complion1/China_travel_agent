"""Destination endpoint regressions, using real evidence verification and M01."""
import pytest
from app.requirements import (ConfirmedTravelRequirement, FieldStatus, HubType,
    NeedClarification, NeedConfirmation, RequirementConfirmation, RequirementDomainState,
    RequirementFieldPath, RequirementIntent, RequirementIssue, StrictFieldEvidenceDTO,
    validate_requirement_hubs)
from app.requirements.merge import RequirementMerger
from tests.unit.requirements.fakes import strict_dto
from tests.unit.requirements.test_requirement_service import complete_requirement, make_service, turn


@pytest.mark.parametrize("name,hub_type,code", [
    (None, None, "hub_name_missing"),
    ("上海", None, "hub_name_not_specific"),
    ("合肥南站", None, "hub_type_missing"),
    ("合肥南站", HubType.AIRPORT, "hub_type_mismatch"),
    ("合肥新桥机场", HubType.RAILWAY_STATION, "hub_type_mismatch"),
])
async def test_legacy_awaiting_confirmation_is_revalidated_without_extraction(name, hub_type, code):
    req = complete_requirement()
    req.time_window.departure.hub_name = name
    req.time_window.departure.hub_type = hub_type
    state = RequirementDomainState(requirement=req, confirmation_status="awaiting_confirmation")
    service, extractor, sink = make_service()
    result = await service.confirm(state, RequirementConfirmation(decision="accept", revision_text=None))
    assert type(result.outcome) is NeedClarification
    assert code in {issue.code for issue in result.outcome.issues}
    assert result.state.confirmation_status == "collecting"
    assert not extractor.calls and not sink.received
    assert state.confirmation_status == "awaiting_confirmation"


async def test_both_missing_types_and_times_are_required_without_breaking_legacy_dto():
    req = complete_requirement()
    for endpoint in (req.time_window.arrival, req.time_window.departure):
        endpoint.hub_type = None
        endpoint.local_datetime = None
    historical = ConfirmedTravelRequirement(schema_version="1.0", requirement=req, metadata_without_raw_evidence={},
        confirmation_summary_digest="a" * 64)
    assert ConfirmedTravelRequirement.model_validate_json(historical.model_dump_json(), strict=True) == historical
    missing = RequirementMerger().detect_missing_fields(req)
    assert all(f"time_window.{role}.{field}" in missing
        for role in ("arrival", "departure") for field in ("hub_type", "local_datetime"))
    issues = validate_requirement_hubs(req)
    assert len(issues) == 4
    assert {issue.code for issue in issues} == {"hub_type_missing", "hub_time_missing"}


@pytest.mark.parametrize("bad_name,bad_type,answer_name,answer_type", [
    ("上海", None, "合肥南站", None),
    ("合肥南站", HubType.AIRPORT, None, HubType.RAILWAY_STATION),
    ("合肥新桥机场", HubType.RAILWAY_STATION, "合肥南站", None),
])
async def test_evidenced_supplement_repairs_invalid_pair_and_clears_counterpart_metadata(
        bad_name, bad_type, answer_name, answer_type):
    req = complete_requirement("合肥")
    req.time_window.departure.hub_name, req.time_window.departure.hub_type = bad_name, bad_type
    first_service, _, _ = make_service()
    invalid = await first_service.process_turn(turn(state=RequirementDomainState(requirement=req)))
    assert type(invalid.outcome) is NeedClarification
    message = "补充旅行需求：\n离开合肥的机场/车站：合肥南站\n离开枢纽类型：火车站或高铁站"
    evidence, values = [], {}
    if answer_name:
        values["departure_hub_name"] = answer_name
        evidence.append(StrictFieldEvidenceDTO(field_path=RequirementFieldPath.DEPARTURE_HUB_NAME, text="合肥南站"))
    if answer_type:
        values["departure_hub_type"] = answer_type
        evidence.append(StrictFieldEvidenceDTO(field_path=RequirementFieldPath.DEPARTURE_HUB_TYPE, text="火车站或高铁站"))
    service, extractor, _ = make_service(strict_dto(**values, evidence=evidence))
    fixed = await service.process_turn(turn(dto_message=message, state=invalid.state, intent=RequirementIntent.SUPPLEMENT))
    assert type(fixed.outcome) is NeedConfirmation
    assert fixed.state.requirement.time_window.departure.hub_name == "合肥南站"
    assert fixed.state.requirement.time_window.departure.hub_type is HubType.RAILWAY_STATION
    assert fixed.state.issues == ()
    assert all(not meta.issues and meta.status not in {FieldStatus.INVALID, FieldStatus.CONFLICT}
        for path, meta in fixed.state.metadata.metadata.items() if path.value.startswith("time_window.departure."))
    confirmed = await service.confirm(fixed.state, RequirementConfirmation(decision="accept", revision_text=None))
    assert type(confirmed.outcome) is ConfirmedTravelRequirement
    assert len(extractor.calls) == 1


async def test_valid_hub_change_requires_correction_and_unrelated_conflict_survives():
    req = complete_requirement()
    issue = RequirementIssue(code="requirement_conflict", field_path=RequirementFieldPath.ROOM_COUNT, message="请核对房间数量")
    state = RequirementDomainState(requirement=req, issues=(issue,))
    dto = strict_dto(departure_hub_name="杭州萧山机场", evidence=[
        StrictFieldEvidenceDTO(field_path=RequirementFieldPath.DEPARTURE_HUB_NAME, text="杭州萧山机场")])
    service, _, _ = make_service(dto)
    result = await service.process_turn(turn(dto_message="离开杭州的机场/车站：杭州萧山机场", state=state,
        intent=RequirementIntent.SUPPLEMENT))
    assert type(result.outcome) is NeedClarification
    assert result.state.requirement.time_window.departure == req.time_window.departure
    assert issue in result.state.issues
    assert any(item.code == "requirement_conflict" and item.field_path is RequirementFieldPath.DEPARTURE_HUB_NAME
        for item in result.state.issues)


async def test_unverified_supplement_cannot_replace_invalid_existing_name():
    from app.requirements import RequirementExtractionFailed
    req = complete_requirement()
    req.time_window.departure.hub_name = "上海"
    state = RequirementDomainState(requirement=req)
    invalid_dto = strict_dto(departure_hub_name="合肥南站")
    service, extractor, sink = make_service(invalid_dto, invalid_dto)
    result = await service.process_turn(turn(dto_message="合肥南站", state=state, intent=RequirementIntent.SUPPLEMENT))
    assert type(result.outcome) is RequirementExtractionFailed
    assert result.state.requirement == state.requirement
    assert len(extractor.calls) == 1 and not sink.received
    assert extractor.sessions[0].extract_calls == extractor.sessions[0].repair_calls == 1


@pytest.mark.parametrize("name", ["车站", "合肥公交车站", "合肥汽车客运中心站", "合肥地铁车站"])
async def test_unspecific_or_nonrail_station_cannot_pass_public_check_or_confirmation(name):
    req = complete_requirement("合肥")
    req.time_window.departure.hub_name = name
    assert any(issue.code == "hub_name_not_specific" and issue.field_path is RequirementFieldPath.DEPARTURE_HUB_NAME
        for issue in validate_requirement_hubs(req))
    service, extractor, _ = make_service()
    outcome = await service.confirm(RequirementDomainState(requirement=req, confirmation_status="awaiting_confirmation"),
        RequirementConfirmation(decision="accept", revision_text=None))
    assert type(outcome.outcome) is NeedClarification and not extractor.calls


async def test_unspecific_name_does_not_authorize_overwriting_valid_type_then_current_mismatch_can_be_repaired(monkeypatch):
    req = complete_requirement("合肥")
    req.time_window.departure.hub_name = "合肥"
    req.time_window.departure.hub_type = HubType.AIRPORT
    evidence = [StrictFieldEvidenceDTO(field_path=RequirementFieldPath.DEPARTURE_HUB_NAME, text="合肥南站"),
        StrictFieldEvidenceDTO(field_path=RequirementFieldPath.DEPARTURE_HUB_TYPE, text="火车站或高铁站")]
    dto = strict_dto(departure_hub_name="合肥南站", departure_hub_type=HubType.RAILWAY_STATION, evidence=evidence)
    service, _, _ = make_service(dto)
    original_merge = service._merger.merge
    observed = []
    def inspect_patch(current, patch, *, verified_paths=()):
        proof = tuple(verified_paths)
        assert patch.evidence == []
        observed.append(proof)
        return original_merge(current, patch, verified_paths=proof)
    monkeypatch.setattr(service._merger, "merge", inspect_patch)
    answer = "补充旅行需求：\n离开合肥的机场/车站：合肥南站\n离开枢纽类型：火车站或高铁站"
    result = await service.process_turn(turn(dto_message=answer, state=RequirementDomainState(requirement=req), intent=RequirementIntent.SUPPLEMENT))
    assert type(result.outcome) is NeedClarification
    assert result.state.requirement.time_window.departure.hub_name == "合肥南站"
    assert result.state.requirement.time_window.departure.hub_type is HubType.AIRPORT
    assert any(issue.code == "requirement_conflict" and issue.field_path is RequirementFieldPath.DEPARTURE_HUB_TYPE for issue in result.state.issues)
    assert set(observed[0]) == {item.field_path.value for item in evidence}
    repair, _, _ = make_service(strict_dto(departure_hub_type=HubType.RAILWAY_STATION, evidence=[evidence[1]]))
    fixed = await repair.process_turn(turn(dto_message="离开枢纽类型：火车站或高铁站", state=result.state, intent=RequirementIntent.SUPPLEMENT))
    assert type(fixed.outcome) is NeedConfirmation and fixed.state.issues == ()
