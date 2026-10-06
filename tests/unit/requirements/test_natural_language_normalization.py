"""Regressions for explicit Chinese dates/times in the approved live request."""
from datetime import date
from uuid import uuid4

import pytest

from app.requirements import (
    InterestTag, NeedConfirmation, RequirementDomainService,
    RequirementDomainState, RequirementFieldPath, RequirementIntent, RequirementTurnInput,
    StrictFieldEvidenceDTO, TravelPace,
)
from app.requirements.extraction_contract import ATTR_TO_PATH
from app.requirements.normalization import NormalizationError, normalize_local_datetime, normalize_relative_date
from tests.unit.requirements.fakes import FixedClock, InMemoryEvidenceSink, RecordingRequirementExtractor, strict_dto

TODAY = date(2026, 9, 26)


@pytest.mark.parametrize(("raw", "expected"), [
    ("10 月 20 日", "2026-10-20"), ("2027 年 10 月 20 日", "2027-10-20"),
    ("2028年2月29日", "2028-02-29"), ("10月20日", "2026-10-20"),
])
def test_chinese_calendar_date_preserves_explicit_year(raw, expected):
    assert normalize_relative_date(raw, TODAY) == (expected, False)


@pytest.mark.parametrize("raw", ["2026 年 2 月 29 日", "10 月 32 日", "22日", "10 月中旬", "2026 年 10 月 20 日或21日"])
def test_invalid_or_ambiguous_calendar_date_is_not_guessed(raw):
    with pytest.raises(NormalizationError):
        normalize_relative_date(raw, TODAY)


@pytest.mark.parametrize(("raw", "expected", "relative", "approximate"), [
    ("2026 年 10 月 20 日上午 09:00", "2026-10-20 09:00", False, False),
    ("10 月 22 日晚上 19:00", "2026-10-22 19:00", False, False),
    ("10月20日下午三点左右", "2026-10-20 15:00", False, True),
    ("明天 09:00", "2026-09-27 09:00", True, False),
    ("2026-10-20 00:00", "2026-10-20 00:00", False, False),
    ("10月20日下午三点半", "2026-10-20 15:30", False, False),
    ("10月20日中午12:00", "2026-10-20 12:00", False, False),
])
def test_datetime_keeps_day_period_and_approximation(raw, expected, relative, approximate):
    assert normalize_local_datetime(raw, TODAY) == (expected, relative, approximate)


@pytest.mark.parametrize("raw", [
    "10月20日上午19:00", "10月20日晚上12点", "10月20日三点", "10月20日24:00",
    "10月20日09:60", "10月20日09:00或10:00", "2026年2月30日上午09:00", "三点左右",
])
def test_datetime_conflicts_and_missing_date_require_clarification(raw):
    with pytest.raises(NormalizationError):
        normalize_local_datetime(raw, TODAY)


@pytest.mark.parametrize(("mobility", "diet"), (
    ("没有行动限制", "没有饮食禁忌"),
    ("没有行动限制和饮食禁忌", "没有行动限制和饮食禁忌"),
    ("行动限制：无", "饮食禁忌：无"),
    ("行动限制: 无", "饮食禁忌: 无"),
))
async def test_explicit_three_day_request_can_reach_confirmation_without_model_normalizing(mobility, diet):
    # Only extraction is scripted. Real evidence verification, normalization,
    # merge, derived nights/people, validation and confirmation run end to end.
    values = dict(destination="杭州", arrival_hub_name="杭州东站", departure_hub_name="杭州东站",
        arrival_datetime="2026 年 10 月 20 日上午 09:00", departure_datetime="10 月 22 日晚上 19:00",
        sightseeing_start_date="10 月 20 日", sightseeing_end_date="22 日", sightseeing_days=3,
        adults_count=2, children_count=0, seniors_count=0,
        room_count=1, room_type_requirements="双床房", mobility_constraints=mobility,
        dietary_restrictions=diet, interests=[InterestTag.NATURE, InterestTag.CULTURE],
        pace=TravelPace.NORMAL, hotel_preferences="西湖附近")
    evidence = {name: str(value) for name, value in values.items()}
    evidence.update(interests="自然和人文", pace="标准")
    dto = strict_dto(**values, evidence=[StrictFieldEvidenceDTO(field_path=RequirementFieldPath(ATTR_TO_PATH[k]), text=v)
                                       for k, v in evidence.items()])
    extractor = RecordingRequirementExtractor([dto])
    sink = InMemoryEvidenceSink()
    service = RequirementDomainService(extractor=extractor, evidence_sink=sink, clock=FixedClock(TODAY))
    transition = await service.process_turn(RequirementTurnInput(turn_id=uuid4(), user_message="；".join(evidence.values()),
        session_date=TODAY, intent=RequirementIntent.INITIAL, current=RequirementDomainState()))
    assert isinstance(transition.outcome, NeedConfirmation)
    requirement = transition.state.requirement
    assert requirement.budget.total_cny is None
    assert requirement.time_window.arrival.local_datetime == "2026-10-20 09:00"
    assert requirement.time_window.departure.local_datetime == "2026-10-22 19:00"
    assert requirement.time_window.sightseeing_end_date == "2026-10-22"
    assert requirement.time_window.hotel_nights == 2
    assert requirement.party.total_people == 2
    assert requirement.inferred_scenarios == []
    assert requirement.constraints.mobility_constraints == mobility
    assert requirement.constraints.dietary_restrictions == diet
    accepted_evidence = {item.field_path: item.text for item in sink.received[0][1]}
    for field_path, raw_value in (
        (RequirementFieldPath.MOBILITY_CONSTRAINTS, mobility),
        (RequirementFieldPath.DIETARY_RESTRICTIONS, diet),
    ):
        assert accepted_evidence[field_path] == raw_value
        field_metadata = transition.state.metadata.metadata[field_path]
        assert field_metadata.evidence_ref is not None
        assert field_metadata.evidence_digest == raw_value[:16]
    assert sink.received[0][1][0].text in "；".join(evidence.values())
    meta = transition.state.metadata.metadata[RequirementFieldPath.ARRIVAL_LOCAL_DATETIME]
    assert meta.normalization.raw_value == values["arrival_datetime"]
    assert extractor.sessions[0].repair_calls == 0


async def test_short_range_end_does_not_guess_month_rollover():
    dto = strict_dto(sightseeing_start_date="10月30日", sightseeing_end_date="2日", evidence=[
        StrictFieldEvidenceDTO(field_path=RequirementFieldPath.SIGHTSEEING_START_DATE, text="10月30日"),
        StrictFieldEvidenceDTO(field_path=RequirementFieldPath.SIGHTSEEING_END_DATE, text="2日")])
    service = RequirementDomainService(extractor=RecordingRequirementExtractor([dto]),
        evidence_sink=InMemoryEvidenceSink(), clock=FixedClock(TODAY))
    transition = await service.process_turn(RequirementTurnInput(turn_id=uuid4(), user_message="10月30日至2日",
        session_date=TODAY, intent=RequirementIntent.INITIAL, current=RequirementDomainState()))
    assert any(issue.code == "normalization_error" and issue.field_path == RequirementFieldPath.SIGHTSEEING_END_DATE
               for issue in transition.state.issues)
