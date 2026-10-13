"""新任务只收集有效字段，旧领域规则及四项文本要求保持。"""

from datetime import date
import hashlib
from uuid import uuid4

import pytest

from app.requirements import (
    ConfirmedTravelRequirement, NeedClarification, NeedConfirmation,
    RequirementConfirmation, RequirementDomainService, RequirementDomainState,
    RequirementIntent, RequirementTurnInput, TravelBudget, TravelConstraints,
    TravelParty, TravelRooms, deferred_field_paths,
)
from app.requirements.dialogue import render_confirmation_summary
from app.requirements.manual_template import parse_manual_template, serialize_manual_template
from .fakes import FixedClock, InMemoryEvidenceSink, RecordingRequirementExtractor, strict_dto
from .test_requirement_service import complete_requirement


def active_requirement():
    requirement = complete_requirement()
    requirement.budget = TravelBudget()
    requirement.party = TravelParty()
    requirement.rooms = TravelRooms()
    requirement.constraints = TravelConstraints()
    requirement.inferred_scenarios = []
    return requirement


def service(*responses):
    extractor = RecordingRequirementExtractor(list(responses))
    sink = InMemoryEvidenceSink()
    return RequirementDomainService(extractor=extractor, evidence_sink=sink, clock=FixedClock(date(2026, 10, 13))), extractor


@pytest.mark.asyncio
async def test_active_requirement_confirms_without_invented_party_rooms_or_constraints():
    requirement = active_requirement()
    domain, _ = service()
    state = RequirementDomainState(schema_version="1.1", requirement=requirement, confirmation_status="awaiting_confirmation")
    transition = await domain.confirm(state, RequirementConfirmation(decision="accept", revision_text=None))
    assert type(transition.outcome) is ConfirmedTravelRequirement
    assert transition.outcome.schema_version == transition.state.schema_version == "1.1"
    assert transition.outcome.requirement == requirement
    summary = render_confirmation_summary(requirement, collection_version="1.1")
    assert all(label not in summary for label in ("成人数量", "儿童年龄", "房间数量", "行动限制", "饮食禁忌", "总预算"))
    assert transition.outcome.confirmation_summary_digest == hashlib.sha256(summary.encode()).hexdigest()
    assert transition.state.requirement.party.total_people is None
    assert transition.state.requirement.rooms.room_count is None


@pytest.mark.asyncio
async def test_deferred_dto_fields_cannot_reintroduce_questions_or_evidence():
    # 缺少证据的停用字段仍被忽略，不触发修复或生成伪造证据。
    dto = strict_dto(adults_count=0, children_count=2, children_ages=[6], seniors_count=1,
                     room_count=1, room_type_requirements="家庭房", mobility_constraints="轮椅", dietary_restrictions="过敏")
    domain, extractor = service(dto)
    state = RequirementDomainState(schema_version="1.1", requirement=active_requirement())
    transition = await domain.process_turn(RequirementTurnInput(turn_id=uuid4(), user_message="补充旅行需求",
        session_date=date(2026, 10, 13), intent=RequirementIntent.SUPPLEMENT, current=state))
    assert type(transition.outcome) is NeedConfirmation
    assert transition.state.requirement == state.requirement
    assert extractor.calls[0].collection_version == "1.1"
    assert not extractor.sessions[0].repair_feedback
    assert not set(path.value for path in transition.state.metadata.metadata) & deferred_field_paths("1.1")


@pytest.mark.asyncio
async def test_active_missing_endpoint_still_requires_clarification():
    requirement = active_requirement()
    requirement.time_window.departure.hub_name = None
    domain, _ = service(strict_dto())
    transition = await domain.process_turn(RequirementTurnInput(turn_id=uuid4(), user_message="继续确认",
        session_date=date(2026, 10, 13), intent=RequirementIntent.SUPPLEMENT,
        current=RequirementDomainState(schema_version="1.1", requirement=requirement)))
    assert type(transition.outcome) is NeedClarification
    assert "离开机场/车站" in transition.outcome.message
    assert "成人数量" not in transition.outcome.message


@pytest.mark.asyncio
async def test_legacy_task_keeps_required_party_and_exact_summary():
    domain, _ = service()
    blank_party = RequirementDomainState(requirement=active_requirement(), confirmation_status="awaiting_confirmation")
    assert type((await domain.confirm(blank_party, RequirementConfirmation(decision="accept", revision_text=None))).outcome) is NeedClarification
    complete = complete_requirement()
    transition = await domain.confirm(RequirementDomainState(requirement=complete, confirmation_status="awaiting_confirmation"),
        RequirementConfirmation(decision="accept", revision_text=None))
    assert transition.outcome.schema_version == "1.0"
    summary = render_confirmation_summary(complete)
    assert "成人数量：2" in summary and "房间数量：1" in summary
    assert transition.outcome.confirmation_summary_digest == hashlib.sha256(summary.encode()).hexdigest()


@pytest.mark.asyncio
async def test_four_text_preferences_survive_new_confirmation_unchanged():
    from app.requirements import ATTR_TO_PATH, RequirementFieldPath, StrictFieldEvidenceDTO
    values = dict(transport_preferences="少换乘", daily_time_preferences="十点后出门",
                  free_text_notes="第二天十九点看演出，参观博物馆两小时")
    message = "；".join(values.values())
    dto = strict_dto(**values, evidence=[StrictFieldEvidenceDTO(field_path=RequirementFieldPath(ATTR_TO_PATH[key]), text=value)
                                       for key, value in values.items()])
    domain, _ = service(dto)
    transition = await domain.process_turn(RequirementTurnInput(turn_id=uuid4(), user_message=message,
        session_date=date(2026, 10, 13), intent=RequirementIntent.SUPPLEMENT,
        current=RequirementDomainState(schema_version="1.1", requirement=active_requirement())))
    assert type(transition.outcome) is NeedConfirmation
    for key, value in values.items():
        assert getattr(transition.state.requirement.preferences, key) == value
        assert value in transition.outcome.summary


@pytest.mark.asyncio
async def test_new_manual_template_has_only_active_fields_and_legacy_parser_remains():
    domain, _ = service()
    submission = serialize_manual_template(active_requirement(), template_version="2")
    patch = parse_manual_template(submission)
    assert patch.party is None and patch.rooms is None and patch.constraints is None
    transition = domain.process_manual_template(RequirementDomainState(schema_version="1.1"), submission)
    assert type(transition.outcome) is NeedConfirmation
    old = serialize_manual_template(complete_requirement())
    assert parse_manual_template(old).party.adults_count == 2
    assert type(domain.process_manual_template(RequirementDomainState(schema_version="1.1"), old).outcome) is NeedClarification
