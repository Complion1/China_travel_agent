"""Public-contract tests for module 01/04 requirement-state continuity."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.requirements import (
    ConfirmedTravelRequirement,
    EvidenceReceipt,
    FieldMetadataRegistry,
    HubType,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainOutcome,
    RequirementDomainService,
    RequirementDomainState,
    RequirementDomainTransition,
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
)
from app.workflow_contracts import (
    DeliveryStatus,
    TravelSessionState,
    WorkflowPhase,
    parse_travel_session_state_json,
    serialize_travel_session_state,
)


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
TRACE_ID = UUID("22345678-1234-5678-1234-567812345678")


def _complete_requirement(destination: str = "杭州") -> TravelRequirement:
    return TravelRequirement(
        destination=destination,
        time_window=TravelTimeWindow(
            arrival=TravelTimeWindowArrival(
                hub_name="杭州东站",
                hub_type=HubType.RAILWAY_STATION,
                local_datetime="2026-08-12 09:00",
            ),
            departure=TravelTimeWindowDeparture(
                hub_name="杭州东站",
                hub_type=HubType.RAILWAY_STATION,
                local_datetime="2026-08-14 18:00",
            ),
            sightseeing_start_date="2026-08-12",
            sightseeing_end_date="2026-08-14",
            sightseeing_days=3,
            hotel_check_in_date="2026-08-12",
            hotel_check_out_date="2026-08-14",
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
    )


def _complete_state(*, status: str = "awaiting_confirmation") -> RequirementDomainState:
    return RequirementDomainState(
        requirement=_complete_requirement(),
        metadata=FieldMetadataRegistry(),
        confirmation_status=status,
    )


def _digest(requirement: TravelRequirement) -> str:
    return hashlib.sha256(
        requirement.model_dump_json(exclude_none=False).encode("utf-8")
    ).hexdigest()


def _transition_cases() -> tuple[RequirementDomainTransition, ...]:
    initial = RequirementDomainState()
    complete = _complete_state()
    changed = _complete_requirement("上海")
    invalidated_state = RequirementDomainState(
        requirement=changed,
        metadata=complete.metadata,
        confirmation_status="awaiting_confirmation",
    )
    return (
        RequirementDomainTransition(
            state=initial,
            outcome=NeedClarification(
                requirement=initial.requirement,
                metadata=initial.metadata,
                issues=initial.issues,
                message="请补充需求",
            ),
        ),
        RequirementDomainTransition(
            state=complete,
            outcome=NeedConfirmation(
                requirement=complete.requirement,
                metadata=complete.metadata,
                summary="请确认杭州行程",
            ),
        ),
        RequirementDomainTransition(
            state=complete.model_copy(update={"confirmation_status": "confirmed"}),
            outcome=ConfirmedTravelRequirement(
                schema_version="1.0",
                requirement=complete.requirement,
                metadata_without_raw_evidence=complete.metadata,
                confirmation_summary_digest="summary-digest",
            ),
        ),
        RequirementDomainTransition(
            state=complete.model_copy(update={"confirmation_status": "confirmed"}),
            outcome=RequirementExtractionFailed(
                code=RequirementErrorCode.INPUT_TOO_LONG,
            ),
        ),
        RequirementDomainTransition(
            state=invalidated_state,
            outcome=RequirementInvalidated(
                changed_fields=(RequirementFieldPath.DESTINATION,),
                before_digest=_digest(complete.requirement),
                after_digest=_digest(changed),
                reason="confirmed_requirement_changed",
                summary="请确认上海行程",
            ),
        ),
    )


class _NoOpExtractor:
    def start_session(self, _request: object) -> object:
        raise AssertionError("the accept path must not call extraction")


class _NoOpSink:
    def accept(
        self,
        *,
        turn_id: UUID,
        evidence: tuple[object, ...],
    ) -> tuple[EvidenceReceipt, ...]:
        return ()


class _FixedClock:
    def today(self) -> date:
        return date(2026, 8, 12)


def _session_state(transition: RequirementDomainTransition) -> TravelSessionState:
    return TravelSessionState(
        schema_version="2.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        phase=WorkflowPhase.REQUIREMENTS,
        delivery_status=DeliveryStatus.AWAITING_USER,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=transition.state,
        requirement_outcome=transition.outcome,
        artifact_refs=(),
    )


@pytest.mark.parametrize("transition", _transition_cases())
def test_all_module01_transition_branches_round_trip_as_fresh_state(
    transition: RequirementDomainTransition,
) -> None:
    state = _session_state(transition)
    encoded = serialize_travel_session_state(state)
    parsed = parse_travel_session_state_json(encoded)
    assert parsed.requirement_state == transition.state
    assert parsed.requirement_outcome == transition.outcome
    assert parsed.requirement_state is not transition.state
    assert parsed.requirement_outcome is not transition.outcome
    assert parsed.requirement_state.requirement is not transition.state.requirement
    assert parsed.requirement_outcome is not None

    next_turn = RequirementTurnInput(
        turn_id=uuid4(),
        user_message="继续",
        session_date=date(2026, 8, 12),
        intent=RequirementIntent.SUPPLEMENT,
        current=parsed.requirement_state,
    )
    assert next_turn.current == parsed.requirement_state
    assert next_turn.current is parsed.requirement_state


def test_extraction_failure_and_invalidation_keep_authoritative_full_state() -> None:
    extraction_failure, invalidation = _transition_cases()[3:]
    failed_state = _session_state(extraction_failure)
    invalidated_state = _session_state(invalidation)

    failed_round_trip = parse_travel_session_state_json(
        serialize_travel_session_state(failed_state)
    )
    invalidated_round_trip = parse_travel_session_state_json(
        serialize_travel_session_state(invalidated_state)
    )

    assert failed_round_trip.requirement_state == extraction_failure.state
    assert failed_round_trip.requirement_outcome == extraction_failure.outcome
    assert invalidated_round_trip.requirement_state.requirement.destination == "上海"
    assert invalidated_round_trip.requirement_outcome.summary == "请确认上海行程"  # type: ignore[union-attr]
    assert invalidated_round_trip.requirement_state != RequirementDomainState()


@pytest.mark.asyncio
@pytest.mark.parametrize("case_index", [1, 4])
async def test_need_confirmation_and_invalidated_use_public_state_based_accept(
    case_index: int,
) -> None:
    transition = _transition_cases()[case_index]
    parsed = parse_travel_session_state_json(
        serialize_travel_session_state(_session_state(transition))
    )
    service = RequirementDomainService(
        extractor=_NoOpExtractor(),
        evidence_sink=_NoOpSink(),
        clock=_FixedClock(),
    )
    accepted = await service.confirm(
        parsed.requirement_state,
        RequirementConfirmation(decision="accept", revision_text=None),
    )
    assert isinstance(accepted, RequirementDomainTransition)
    assert isinstance(accepted.outcome, ConfirmedTravelRequirement)
    assert accepted.state.confirmation_status == "confirmed"
