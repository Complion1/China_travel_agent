"""RequirementDomainService orchestration tests against the frozen v1 contract."""

from datetime import date
from uuid import uuid4

import pytest

from app.requirements.extraction_contract import ATTR_TO_PATH, StrictFieldEvidenceDTO
from app.requirements import ExtractionRepairFeedback, ExtractionRepairRequired
from app.requirements.manual_template import serialize_manual_template
from app.requirements.models import (
    ConfirmedTravelRequirement,
    EvidenceReceipt,
    FieldMetadataRegistry,
    InterestTag,
    ManualTemplateSubmission,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainState,
    RequirementErrorCode,
    RequirementExtractionFailed,
    RequirementFieldPath,
    RequirementIntent,
    RequirementInvalidated,
    RequirementTurnInput,
    TravelBudget,
    TravelConstraints,
    TravelParty,
    TravelPreferences,
    TravelRequirement,
    TravelRooms,
    TravelTimeWindow,
    TravelTimeWindowArrival,
    TravelTimeWindowDeparture,
    VerifiedFieldEvidence,
)
from app.requirements.service import RequirementDomainService
from tests.unit.requirements.fakes import (
    FixedClock,
    InMemoryEvidenceSink,
    RecordingRequirementExtractor,
    strict_dto,
)


TODAY = date(2025, 6, 1)


def dto_with_evidence(**values):
    """Attach exactly one message-backed evidence item to every non-null DTO leaf."""
    evidence = [
        StrictFieldEvidenceDTO(
            field_path=RequirementFieldPath(ATTR_TO_PATH[name]),
            text="5000" if name == "budget_total_cny" else "行程",
        )
        for name, value in values.items()
        if name in ATTR_TO_PATH and value is not None
    ]
    return strict_dto(**values, evidence=evidence)


def complete_requirement(destination: str = "杭州") -> TravelRequirement:
    return TravelRequirement(
        destination=destination,
        time_window=TravelTimeWindow(
            arrival=TravelTimeWindowArrival(
                hub_name="杭州东站",
                local_datetime="2025-06-01 09:00",
            ),
            departure=TravelTimeWindowDeparture(
                hub_name="杭州东站",
                local_datetime="2025-06-03 18:00",
            ),
            sightseeing_start_date="2025-06-01",
            sightseeing_end_date="2025-06-03",
            sightseeing_days=3,
            hotel_check_in_date="2025-06-01",
            hotel_check_out_date="2025-06-03",
            hotel_nights=2,
        ),
        budget=TravelBudget(total_cny=5000.0),
        party=TravelParty(
            adults_count=2,
            children_count=0,
            children_ages=[],
            seniors_count=0,
            total_people=2,
        ),
        rooms=TravelRooms(room_count=1, room_type_requirements="标准间"),
        constraints=TravelConstraints(
            mobility_constraints="无",
            dietary_restrictions="无",
        ),
        preferences=TravelPreferences(
            agent_decides_interests=True,
            agent_decides_pace=True,
        ),
        inferred_scenarios=["情侣/朋友结伴"],
    )


def complete_dto(destination: str = "杭州", *, correction: bool = False):
    return dto_with_evidence(
        destination=destination,
        arrival_hub_name="杭州东站",
        arrival_datetime="2025-06-01 09:00",
        departure_hub_name="杭州东站",
        departure_datetime="2025-06-03 18:00",
        sightseeing_start_date="2025-06-01",
        sightseeing_end_date="2025-06-03",
        sightseeing_days=3,
        budget_total_cny=5000.0,
        adults_count=2,
        children_count=0,
        children_ages=[],
        seniors_count=0,
        room_count=1,
        room_type_requirements="标准间",
        mobility_constraints="无",
        dietary_restrictions="无",
        agent_decides_interests=True,
        agent_decides_pace=True,
        is_correction=correction,
    )


def make_service(*responses, sink=None):
    extractor = RecordingRequirementExtractor(responses or [strict_dto()])
    evidence_sink = sink or InMemoryEvidenceSink()
    service = RequirementDomainService(
        extractor=extractor,
        evidence_sink=evidence_sink,
        clock=FixedClock(TODAY),
    )
    return service, extractor, evidence_sink


def turn(dto_message="行程 5000", state=None, intent=RequirementIntent.INITIAL):
    return RequirementTurnInput(
        schema_version="1.0",
        turn_id=uuid4(),
        user_message=dto_message,
        session_date=TODAY,
        timezone="Asia/Shanghai",
        intent=intent,
        current=state or RequirementDomainState(),
    )


async def test_process_turn_returns_clarification_with_partial_requirement():
    dto = dto_with_evidence(destination="杭州")
    service, _, _ = make_service(dto)
    original = RequirementDomainState()

    outcome = await service.process_turn(turn(state=original))

    assert isinstance(outcome, NeedClarification)
    assert outcome.requirement.destination == "杭州"
    assert "missing" not in {issue.code for issue in outcome.issues}
    assert original.requirement.destination is None


async def test_empty_extraction_returns_clarification_without_evidence_writes():
    service, _, sink = make_service(strict_dto())
    outcome = await service.process_turn(turn())
    assert isinstance(outcome, NeedClarification)
    assert len(sink.received) == 1
    assert sink.received[0][1] == ()


async def test_complete_extraction_returns_confirmation_and_records_metadata():
    service, extractor, sink = make_service(complete_dto())
    request = turn()

    outcome = await service.process_turn(request)

    assert isinstance(outcome, NeedConfirmation)
    assert outcome.requirement.party.total_people == 2
    assert outcome.requirement.time_window.hotel_nights == 2
    assert outcome.metadata.metadata[RequirementFieldPath.DESTINATION].evidence_ref
    assert sink.received[0][0] == request.turn_id
    assert extractor.calls[0].current_requirement == request.current.requirement
    assert extractor.calls[0].turn_id == request.turn_id
    assert extractor.calls[0].intent is RequirementIntent.INITIAL


async def test_confirm_accept_requires_awaiting_complete_state_and_returns_narrow_snapshot():
    service, _, _ = make_service()
    state = RequirementDomainState(
        requirement=complete_requirement(),
        confirmation_status="awaiting_confirmation",
    )

    outcome = await service.confirm(
        state,
        RequirementConfirmation(decision="accept", revision_text=None),
    )

    assert isinstance(outcome, ConfirmedTravelRequirement)
    assert len(outcome.confirmation_summary_digest) == 64
    assert outcome.requirement == state.requirement
    assert not hasattr(outcome, "state")
    assert state.confirmation_status == "awaiting_confirmation"


async def test_confirm_accept_rejects_incomplete_or_non_awaiting_state():
    service, _, _ = make_service()
    outcome = await service.confirm(
        RequirementDomainState(),
        RequirementConfirmation(decision="accept", revision_text=None),
    )
    assert isinstance(outcome, NeedClarification)
    assert any(issue.code == RequirementErrorCode.CONFIRMATION_NOT_READY.value for issue in outcome.issues)


async def test_confirm_revise_routes_through_extractor_with_revision_intent():
    dto = dto_with_evidence(destination="上海", is_correction=True)
    service, extractor, _ = make_service(dto)
    state = RequirementDomainState(
        requirement=complete_requirement(),
        confirmation_status="confirmed",
    )

    outcome = await service.confirm(
        state,
        RequirementConfirmation(decision="revise", revision_text="行程"),
    )

    assert isinstance(outcome, RequirementInvalidated)
    assert outcome.changed_fields == (RequirementFieldPath.DESTINATION,)
    assert extractor.calls[0].intent is RequirementIntent.CONFIRMATION_REVISION
    assert extractor.calls[0].user_message == "行程"


async def test_invalidated_when_confirmed_requirement_changes():
    dto = dto_with_evidence(destination="上海", is_correction=True)
    service, _, _ = make_service(dto)
    state = RequirementDomainState(
        requirement=complete_requirement(),
        confirmation_status="confirmed",
    )
    outcome = await service.process_turn(
        turn(state=state, intent=RequirementIntent.CORRECTION)
    )
    assert isinstance(outcome, RequirementInvalidated)
    assert outcome.before_digest != outcome.after_digest
    assert outcome.reason == "confirmed_requirement_changed"


async def test_extractor_gets_exactly_one_repair_attempt_for_schema_or_evidence_error():
    repaired = dto_with_evidence(destination="杭州")
    structured_repair = ExtractionRepairRequired(
        feedback=ExtractionRepairFeedback(
            code="structured_output_invalid",
            field_paths=(),
        )
    )
    service, extractor, _ = make_service(structured_repair, repaired)
    assert isinstance(await service.process_turn(turn()), NeedClarification)
    assert len(extractor.calls) == 1
    assert extractor.sessions[0].extract_calls == 1
    assert extractor.sessions[0].repair_calls == 1

    bad_evidence = strict_dto(destination="杭州", evidence=[])
    service, extractor, _ = make_service(bad_evidence, repaired)
    assert isinstance(await service.process_turn(turn()), NeedClarification)
    assert len(extractor.calls) == 1
    assert extractor.sessions[0].repair_calls == 1


async def test_two_failed_attempts_return_fixed_error_code_only():
    service, _, _ = make_service(
        ExtractionRepairRequired(
            feedback=ExtractionRepairFeedback(
                code="structured_output_invalid", field_paths=()
            )
        ),
        ExtractionRepairRequired(
            feedback=ExtractionRepairFeedback(
                code="structured_output_invalid", field_paths=()
            )
        ),
    )
    outcome = await service.process_turn(turn())
    assert outcome == RequirementExtractionFailed(code=RequirementErrorCode.STRICT_SCHEMA_ERROR)
    assert not hasattr(outcome, "message")

    bad = strict_dto(destination="杭州", evidence=[])
    service, _, _ = make_service(bad, bad)
    outcome = await service.process_turn(turn())
    assert outcome.code is RequirementErrorCode.EVIDENCE_MISSING


async def test_evidence_not_substring_has_distinct_failure_code():
    bad = strict_dto(
        destination="杭州",
        evidence=[
            StrictFieldEvidenceDTO(
                field_path=RequirementFieldPath.DESTINATION,
                text="杭州",
            )
        ],
    )
    service, _, _ = make_service(bad, bad)
    outcome = await service.process_turn(turn(dto_message="行程"))
    assert isinstance(outcome, RequirementExtractionFailed)
    assert outcome.code is RequirementErrorCode.EVIDENCE_NOT_SUBSTRING


async def test_receipt_path_mismatch_fails_without_merging():
    class WrongSink:
        def accept(self, *, turn_id, evidence: tuple[VerifiedFieldEvidence, ...]):
            return (
                EvidenceReceipt(
                    field_path=RequirementFieldPath.PACE,
                    evidence_ref="ref",
                    evidence_digest="digest",
                ),
            )

    service, _, _ = make_service(dto_with_evidence(destination="杭州"), sink=WrongSink())
    outcome = await service.process_turn(turn())
    assert outcome == RequirementExtractionFailed(code=RequirementErrorCode.EVIDENCE_MISSING)


async def test_bad_relative_date_returns_clarification_without_partial_mutation():
    dto = dto_with_evidence(destination="杭州", sightseeing_start_date="不确定日期")
    service, _, _ = make_service(dto)
    state = RequirementDomainState()

    outcome = await service.process_turn(turn(state=state))

    assert isinstance(outcome, NeedClarification)
    assert outcome.requirement.destination is None
    assert outcome.issues[0].code == RequirementErrorCode.NORMALIZATION_ERROR.value
    assert outcome.issues[0].field_path is RequirementFieldPath.SIGHTSEEING_START_DATE
    assert state == RequirementDomainState()


async def test_budget_evidence_must_parse_to_the_extracted_amount():
    dto = dto_with_evidence(destination="杭州", budget_total_cny=6000.0)
    service, _, _ = make_service(dto)
    outcome = await service.process_turn(turn())
    assert isinstance(outcome, NeedClarification)
    assert outcome.requirement.destination is None
    assert outcome.issues[0].field_path is RequirementFieldPath.BUDGET_TOTAL_CNY


def test_process_manual_template_parse_error_is_non_mutating_clarification():
    service, _, _ = make_service()
    state = RequirementDomainState()
    outcome = service.process_manual_template(
        state,
        ManualTemplateSubmission(template_version="1", content="bad content"),
    )
    assert isinstance(outcome, NeedClarification)
    assert outcome.requirement == state.requirement
    assert outcome.issues


def test_process_manual_template_uses_same_merge_validation_and_confirmation_path():
    service, _, _ = make_service()
    state = RequirementDomainState()
    submission = serialize_manual_template(complete_requirement())

    outcome = service.process_manual_template(state, submission)

    assert isinstance(outcome, NeedConfirmation)
    assert outcome.requirement.time_window.hotel_nights == 2
    assert outcome.requirement.party.total_people == 2
    assert outcome.metadata.metadata[RequirementFieldPath.DESTINATION].source_turn_id.int == 0
    assert state == RequirementDomainState()


async def test_preference_values_and_relative_dates_are_normalized_into_patch():
    dto = dto_with_evidence(
        destination="杭州",
        sightseeing_start_date="明天",
        arrival_hub_name="萧山机场",
        interests=[InterestTag.NATURE],
    )
    service, _, _ = make_service(dto)
    outcome = await service.process_turn(turn())
    assert isinstance(outcome, NeedClarification)
    assert outcome.requirement.time_window.sightseeing_start_date == "2025-06-02"
    assert outcome.requirement.time_window.arrival.hub_type.value == "airport"
    assert outcome.requirement.preferences.interests == [InterestTag.NATURE]
    metadata = outcome.metadata.metadata[RequirementFieldPath.SIGHTSEEING_START_DATE]
    assert metadata.status.value == "pending_confirmation"
    assert metadata.normalization.rule_name == "relative_date"


async def test_intent_mismatch_repairs_once_before_evidence_validation():
    wrong = strict_dto(is_correction=True)
    repaired = strict_dto(is_correction=False)
    service, extractor, _ = make_service(wrong, repaired)

    outcome = await service.process_turn(turn(intent=RequirementIntent.INITIAL))

    assert isinstance(outcome, NeedClarification)
    session = extractor.sessions[0]
    assert session.extract_calls == 1
    assert session.repair_calls == 1
    assert session.repair_feedback[0].code == "intent_mismatch"
    assert session.repair_feedback[0].field_paths == ()


async def test_runtime_exception_propagates_without_repair():
    runtime_error = RuntimeError("safe_runtime_failure")
    service, extractor, _ = make_service(runtime_error)

    with pytest.raises(RuntimeError, match="safe_runtime_failure"):
        await service.process_turn(turn())

    session = extractor.sessions[0]
    assert session.extract_calls == 1
    assert session.repair_calls == 0


async def test_zero_children_does_not_require_ages_and_room_no_preference_is_normalized():
    dto = dto_with_evidence(
        adults_count=2,
        children_count=0,
        room_type_requirements="无特殊要求",
    )
    service, _, _ = make_service(dto)

    outcome = await service.process_turn(
        turn(dto_message="行程：2位成人，没有儿童，房型无特殊要求")
    )

    assert isinstance(outcome, NeedClarification)
    assert outcome.requirement.party.children_ages == []
    assert outcome.requirement.rooms.room_type_requirements == "NO_PREFERENCE"
    assert "儿童年龄" not in outcome.message
