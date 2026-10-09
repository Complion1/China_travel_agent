"""RequirementDomainService orchestration tests against the frozen v1 contract."""

import hashlib
import json
from datetime import date
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from app.requirements.extraction_contract import ATTR_TO_PATH, StrictFieldEvidenceDTO
from app.requirements import ExtractionRepairFeedback, ExtractionRepairRequired
from app.requirements.manual_template import serialize_manual_template
from app.requirements.models import (
    ConfirmedTravelRequirement,
    EvidenceReceipt,
    FieldMetadataRegistry,
    InterestTag,
    HubType,
    ManualTemplateSubmission,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainState,
    RequirementDomainTransition,
    RequirementErrorCode,
    RequirementExtractionFailed,
    RequirementFieldPath,
    RequirementIntent,
    RequirementInvalidated,
    RequirementIssue,
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
from app.requirements.dialogue import render_confirmation_summary
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
                hub_type=HubType.RAILWAY_STATION,
                local_datetime="2025-06-01 09:00",
            ),
            departure=TravelTimeWindowDeparture(
                hub_name="杭州东站",
                hub_type=HubType.RAILWAY_STATION,
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

    assert isinstance(outcome.outcome, NeedClarification)
    assert outcome.state.requirement.destination == "杭州"
    assert "missing" not in {issue.code for issue in outcome.outcome.issues}
    assert original.requirement.destination is None


async def test_process_turn_8000_code_points_enters_extractor_pipeline():
    service, extractor, sink = make_service(strict_dto())

    transition = await service.process_turn(turn(dto_message="x" * 8000))

    assert isinstance(transition, RequirementDomainTransition)
    assert isinstance(transition.outcome, NeedClarification)
    assert len(extractor.calls) == 1
    assert extractor.calls[0].user_message == "x" * 8000
    assert len(sink.received) == 1


async def test_empty_extraction_returns_clarification_without_evidence_writes():
    service, _, sink = make_service(strict_dto())
    outcome = await service.process_turn(turn())
    assert isinstance(outcome.outcome, NeedClarification)
    assert len(sink.received) == 1
    assert sink.received[0][1] == ()


async def test_complete_extraction_returns_confirmation_and_records_metadata():
    service, extractor, sink = make_service(complete_dto())
    request = turn()

    outcome = await service.process_turn(request)

    assert isinstance(outcome.outcome, NeedConfirmation)
    assert outcome.state.requirement.party.total_people == 2
    assert outcome.state.requirement.time_window.hotel_nights == 2
    assert outcome.state.metadata.metadata[RequirementFieldPath.DESTINATION].evidence_ref
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

    assert isinstance(outcome.outcome, ConfirmedTravelRequirement)
    assert len(outcome.outcome.confirmation_summary_digest) == 64
    assert outcome.outcome.requirement == state.requirement
    assert outcome.state.confirmation_status == "confirmed"
    assert state.confirmation_status == "awaiting_confirmation"


async def test_confirm_accept_rejects_incomplete_or_non_awaiting_state():
    service, _, _ = make_service()
    outcome = await service.confirm(
        RequirementDomainState(),
        RequirementConfirmation(decision="accept", revision_text=None),
    )
    assert isinstance(outcome.outcome, NeedClarification)
    assert any(issue.code == RequirementErrorCode.CONFIRMATION_NOT_READY.value for issue in outcome.outcome.issues)


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

    assert isinstance(outcome.outcome, RequirementInvalidated)
    assert outcome.outcome.changed_fields == (RequirementFieldPath.DESTINATION,)
    assert extractor.calls[0].intent is RequirementIntent.CONFIRMATION_REVISION
    assert extractor.calls[0].user_message == "行程"


async def test_confirm_revise_8000_code_points_enters_extractor_pipeline():
    service, extractor, sink = make_service(strict_dto(is_correction=True))
    state = RequirementDomainState(
        requirement=complete_requirement(),
        confirmation_status="confirmed",
    )

    transition = await service.confirm(
        state,
        RequirementConfirmation(decision="revise", revision_text="x" * 8000),
    )

    assert isinstance(transition, RequirementDomainTransition)
    assert isinstance(transition.outcome, NeedConfirmation)
    assert len(extractor.calls) == 1
    assert extractor.calls[0].user_message == "x" * 8000
    assert extractor.calls[0].intent is RequirementIntent.CONFIRMATION_REVISION
    assert len(sink.received) == 1


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
    assert isinstance(outcome.outcome, RequirementInvalidated)
    assert outcome.outcome.before_digest != outcome.outcome.after_digest
    assert outcome.outcome.reason == "confirmed_requirement_changed"
    assert outcome.state.confirmation_status == "awaiting_confirmation"
    assert outcome.state.issues == ()
    assert outcome.outcome.summary == render_confirmation_summary(outcome.state.requirement)
    assert outcome.outcome.after_digest == hashlib.sha256(
        outcome.state.requirement.model_dump_json(exclude_none=False).encode("utf-8")
    ).hexdigest()
    confirmed = await service.confirm(
        outcome.state,
        RequirementConfirmation(decision="accept", revision_text=None),
    )
    assert confirmed.state.confirmation_status == "confirmed"
    assert isinstance(confirmed.outcome, ConfirmedTravelRequirement)


async def test_process_turn_8001_returns_fresh_failure_before_side_effects():
    service, extractor, sink = make_service(strict_dto())
    original = RequirementDomainState(
        requirement=complete_requirement(),
        confirmation_status="confirmed",
    )

    transition = await service.process_turn(turn(dto_message="x" * 8001, state=original))

    assert transition.outcome == RequirementExtractionFailed(
        code=RequirementErrorCode.INPUT_TOO_LONG
    )
    assert transition.state == original
    assert transition.state is not original
    assert transition.state.requirement is not original.requirement
    assert extractor.calls == []
    assert sink.received == []


async def test_extraction_failure_preserves_complete_fresh_prior_state():
    failure = ExtractionRepairRequired(
        feedback=ExtractionRepairFeedback(
            code="structured_output_invalid",
            field_paths=(),
        )
    )
    service, extractor, sink = make_service(failure, failure)
    issue = RequirementIssue(
        code="existing_issue",
        field_path=RequirementFieldPath.DESTINATION,
        message="keep me",
    )
    original = RequirementDomainState(
        requirement=complete_requirement(),
        metadata=FieldMetadataRegistry(),
        issues=(issue,),
        confirmation_status="confirmed",
    )

    transition = await service.process_turn(turn(state=original))

    assert isinstance(transition, RequirementDomainTransition)
    assert isinstance(transition.outcome, RequirementExtractionFailed)
    assert transition.state == original
    assert transition.state is not original
    assert transition.state.requirement is not original.requirement
    assert transition.state.metadata is not original.metadata
    assert transition.state.issues is not original.issues
    assert transition.state.issues[0] is not original.issues[0]
    assert extractor.calls and extractor.sessions[0].repair_calls == 1
    assert sink.received == []


async def test_service_transition_json_round_trip_preserves_nested_types():
    service, _, _ = make_service(complete_dto())

    transition = await service.process_turn(turn())
    restored = TypeAdapter(RequirementDomainTransition).validate_json(
        json.dumps(
            transition.model_dump(
                mode="json",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            ),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        strict=True,
    )

    assert isinstance(transition, RequirementDomainTransition)
    assert isinstance(restored.outcome, NeedConfirmation)
    assert isinstance(restored.state.metadata.metadata[RequirementFieldPath.DESTINATION].source_turn_id, type(uuid4()))
    assert restored.state.requirement.time_window.arrival.hub_type is HubType.RAILWAY_STATION
    assert isinstance(restored.state.issues, tuple)
    assert restored.state.requirement.party.total_people == 2


async def test_service_transition_state_and_outcome_are_deeply_independent():
    dto = dto_with_evidence(destination="杭州")
    service, _, _ = make_service(dto)
    input_state = RequirementDomainState()

    transition = await service.process_turn(
        turn(state=input_state, dto_message="行程")
    )

    assert isinstance(transition.outcome, NeedClarification)
    assert transition.state.requirement is not input_state.requirement
    assert transition.state.metadata is not transition.outcome.metadata
    outcome_metadata_issues = list(
        transition.outcome.metadata.metadata[RequirementFieldPath.DESTINATION].issues
    )
    transition.state.requirement.destination = "上海"
    transition.state.metadata.metadata[RequirementFieldPath.DESTINATION].issues.append(
        RequirementIssue(
            code="state_only",
            field_path=RequirementFieldPath.DESTINATION,
            message="state only",
        )
    )
    assert transition.outcome.requirement.destination == "杭州"
    assert transition.outcome.metadata.metadata[RequirementFieldPath.DESTINATION].issues == outcome_metadata_issues

    transition.outcome.requirement.destination = "南京"
    transition.outcome.metadata.metadata[RequirementFieldPath.DESTINATION].issues.append(
        RequirementIssue(
            code="outcome_only",
            field_path=RequirementFieldPath.DESTINATION,
            message="outcome only",
        )
    )
    assert transition.state.requirement.destination == "上海"
    assert [issue.code for issue in transition.state.metadata.metadata[RequirementFieldPath.DESTINATION].issues] == [
        "state_only"
    ]

    issue_dto = dto_with_evidence(destination="杭州", sightseeing_start_date="不确定日期")
    issue_service, _, _ = make_service(issue_dto)
    issue_transition = await issue_service.process_turn(turn(dto_message="行程"))
    assert isinstance(issue_transition.outcome, NeedClarification)
    assert issue_transition.state.issues is not issue_transition.outcome.issues
    issue_transition.state.issues[0].message = "state issue only"
    assert issue_transition.outcome.issues[0].message != "state issue only"
    issue_transition.outcome.issues[0].message = "outcome issue only"
    assert issue_transition.state.issues[0].message == "state issue only"


async def test_revise_8001_returns_fresh_failure_before_side_effects():
    service, extractor, sink = make_service(strict_dto())
    original = RequirementDomainState(
        requirement=complete_requirement(),
        confirmation_status="awaiting_confirmation",
    )

    transition = await service.confirm(
        original,
        RequirementConfirmation(decision="revise", revision_text="x" * 8001),
    )

    assert transition.outcome == RequirementExtractionFailed(
        code=RequirementErrorCode.INPUT_TOO_LONG
    )
    assert transition.state == original
    assert transition.state is not original
    assert transition.state.requirement is not original.requirement
    assert extractor.calls == []
    assert sink.received == []


async def test_extractor_gets_exactly_one_repair_attempt_for_schema_or_evidence_error():
    repaired = dto_with_evidence(destination="杭州")
    structured_repair = ExtractionRepairRequired(
        feedback=ExtractionRepairFeedback(
            code="structured_output_invalid",
            field_paths=(),
        )
    )
    service, extractor, _ = make_service(structured_repair, repaired)
    assert isinstance((await service.process_turn(turn())).outcome, NeedClarification)
    assert len(extractor.calls) == 1
    assert extractor.sessions[0].extract_calls == 1
    assert extractor.sessions[0].repair_calls == 1

    bad_evidence = strict_dto(destination="杭州", evidence=[])
    service, extractor, _ = make_service(bad_evidence, repaired)
    assert isinstance((await service.process_turn(turn())).outcome, NeedClarification)
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
    assert outcome.outcome == RequirementExtractionFailed(code=RequirementErrorCode.STRICT_SCHEMA_ERROR)

    bad = strict_dto(destination="杭州", evidence=[])
    service, _, _ = make_service(bad, bad)
    outcome = await service.process_turn(turn())
    assert outcome.outcome.code is RequirementErrorCode.EVIDENCE_MISSING


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
    assert isinstance(outcome.outcome, RequirementExtractionFailed)
    assert outcome.outcome.code is RequirementErrorCode.EVIDENCE_NOT_SUBSTRING


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
    assert outcome.outcome == RequirementExtractionFailed(code=RequirementErrorCode.EVIDENCE_MISSING)


async def test_bad_relative_date_returns_clarification_without_partial_mutation():
    dto = dto_with_evidence(destination="杭州", sightseeing_start_date="不确定日期")
    service, _, _ = make_service(dto)
    state = RequirementDomainState()

    outcome = await service.process_turn(turn(state=state))

    assert isinstance(outcome.outcome, NeedClarification)
    assert outcome.state.requirement.destination is None
    assert outcome.outcome.issues[0].code == RequirementErrorCode.NORMALIZATION_ERROR.value
    assert outcome.outcome.issues[0].field_path is RequirementFieldPath.SIGHTSEEING_START_DATE
    assert state == RequirementDomainState()


async def test_deferred_budget_does_not_normalize_or_block_active_fields():
    dto = dto_with_evidence(destination="杭州", budget_total_cny=6000.0)
    service, _, _ = make_service(dto)
    outcome = await service.process_turn(turn())
    assert isinstance(outcome.outcome, NeedClarification)
    assert outcome.state.requirement.destination == "杭州"
    assert outcome.state.requirement.budget.total_cny is None
    assert all(issue.field_path is not RequirementFieldPath.BUDGET_TOTAL_CNY for issue in outcome.outcome.issues)


async def test_complete_requirement_can_confirm_without_amount_or_budget_evidence():
    dto = complete_dto().model_copy(update={"budget_total_cny": None,
        "evidence": [item for item in complete_dto().evidence
                     if item.field_path is not RequirementFieldPath.BUDGET_TOTAL_CNY]})
    service, extractor, sink = make_service(dto)
    result = await service.process_turn(turn())
    assert isinstance(result.outcome, NeedConfirmation)
    assert result.state.requirement.budget.total_cny is None
    confirmed = await service.confirm(result.state, RequirementConfirmation(decision="accept", revision_text=None))
    assert isinstance(confirmed.outcome, ConfirmedTravelRequirement)
    assert confirmed.outcome.requirement.budget.total_cny is None
    assert all(item.field_path not in {RequirementFieldPath.BUDGET_TOTAL_CNY, RequirementFieldPath.BUDGET_IS_APPROXIMATE}
               for item in sink.received[0][1])
    assert extractor.sessions[0].repair_calls == 0


def test_process_manual_template_parse_error_is_non_mutating_clarification():
    service, _, _ = make_service()
    state = RequirementDomainState()
    outcome = service.process_manual_template(
        state,
        ManualTemplateSubmission(template_version="1", content="bad content"),
    )
    assert isinstance(outcome.outcome, NeedClarification)
    assert outcome.state.requirement == state.requirement
    assert outcome.outcome.issues


def test_process_manual_template_uses_same_merge_validation_and_confirmation_path():
    service, _, _ = make_service()
    state = RequirementDomainState()
    submission = serialize_manual_template(complete_requirement())

    outcome = service.process_manual_template(state, submission)

    assert isinstance(outcome.outcome, NeedConfirmation)
    assert outcome.state.requirement.time_window.hotel_nights == 2
    assert outcome.state.requirement.party.total_people == 2
    assert outcome.state.metadata.metadata[RequirementFieldPath.DESTINATION].source_turn_id.int == 0
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
    assert isinstance(outcome.outcome, NeedClarification)
    assert outcome.state.requirement.time_window.sightseeing_start_date == "2025-06-02"
    assert outcome.state.requirement.time_window.arrival.hub_type.value == "airport"
    assert outcome.state.requirement.preferences.interests == [InterestTag.NATURE]
    metadata = outcome.state.metadata.metadata[RequirementFieldPath.SIGHTSEEING_START_DATE]
    assert metadata.status.value == "pending_confirmation"
    assert metadata.normalization.rule_name == "relative_date"


async def test_intent_mismatch_repairs_once_before_evidence_validation():
    wrong = strict_dto(is_correction=True)
    repaired = strict_dto(is_correction=False)
    service, extractor, _ = make_service(wrong, repaired)

    outcome = await service.process_turn(turn(intent=RequirementIntent.INITIAL))

    assert isinstance(outcome.outcome, NeedClarification)
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

    assert isinstance(outcome.outcome, NeedClarification)
    assert outcome.state.requirement.party.children_ages == []
    assert outcome.state.requirement.rooms.room_type_requirements == "NO_PREFERENCE"
    assert "儿童年龄" not in outcome.outcome.message
