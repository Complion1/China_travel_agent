"""DLG-T01: frozen public model and boundary contracts."""

import json
from typing import get_type_hints
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.requirements import (
    ConfirmedTravelRequirement,
    FieldMetadata,
    FieldMetadataRegistry,
    FieldStatus,
    HubType,
    InterestTag,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainOutcome,
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
    TravelParty,
    TravelPace,
    TravelRequirement,
    TravelRooms,
    TravelTimeWindowArrival,
    TravelTimeWindow,
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


def test_public_input_boundaries_allow_service_length_preflight():
    base = {
        "schema_version": "1.0",
        "turn_id": uuid4(),
        "session_date": __import__("datetime").date(2025, 6, 1),
        "timezone": "Asia/Shanghai",
        "intent": RequirementIntent.INITIAL,
        "current": RequirementDomainState(),
    }
    assert RequirementTurnInput(user_message="x" * 8000, **base).user_message == "x" * 8000
    assert RequirementTurnInput(user_message="x" * 8001, **base).user_message == "x" * 8001
    with pytest.raises(ValidationError):
        RequirementTurnInput(user_message="", **base)
    assert RequirementConfirmation(decision="revise", revision_text="x" * 8001)


def test_transition_has_exact_public_shape_and_strict_json_round_trip():
    transition = RequirementDomainTransition(
        state=RequirementDomainState(
            requirement=TravelRequirement(destination="杭州"),
            confirmation_status="awaiting_confirmation",
        ),
        outcome=NeedConfirmation(
            requirement=TravelRequirement(destination="杭州"),
            metadata=FieldMetadataRegistry(),
            summary="summary",
        ),
    )
    assert tuple(RequirementDomainTransition.model_fields) == ("state", "outcome")
    assert all(field.is_required() for field in RequirementDomainTransition.model_fields.values())
    assert "schema_version" not in RequirementDomainTransition.model_fields
    assert RequirementDomainTransition.model_config["extra"] == "forbid"
    assert RequirementDomainTransition.model_config["frozen"] is True
    assert RequirementDomainTransition.model_config["strict"] is True

    payload = json.dumps(
        transition.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    restored = TypeAdapter(RequirementDomainTransition).validate_json(payload, strict=True)
    assert restored.state.requirement.destination == "杭州"
    assert isinstance(restored.outcome, NeedConfirmation)


def _transition_requirement() -> TravelRequirement:
    return TravelRequirement(
        destination="杭州",
        time_window=TravelTimeWindow(
            arrival=TravelTimeWindowArrival(
                hub_name="杭州东站",
                hub_type=HubType.RAILWAY_STATION,
            )
        ),
        party=TravelParty(adults_count=2),
    )


def _transition_metadata() -> tuple[FieldMetadataRegistry, RequirementIssue]:
    issue = RequirementIssue(
        code="normalization_error",
        field_path=RequirementFieldPath.DESTINATION,
        message="invalid",
    )
    return (
        FieldMetadataRegistry(
            metadata={
                RequirementFieldPath.DESTINATION: FieldMetadata(
                    status=FieldStatus.EXTRACTED,
                    source_turn_id=uuid4(),
                    issues=[issue],
                )
            }
        ),
        issue,
    )


def _valid_transition_cases():
    requirement = _transition_requirement()
    metadata, issue = _transition_metadata()
    invalidated_requirement = requirement.model_copy(deep=True)
    invalidated_digest = __import__("hashlib").sha256(
        invalidated_requirement.model_dump_json(exclude_none=False).encode("utf-8")
    ).hexdigest()
    return [
        (
            RequirementDomainState(
                requirement=requirement,
                metadata=metadata,
                issues=(issue,),
                confirmation_status="collecting",
            ),
            NeedClarification(
                requirement=requirement,
                metadata=metadata,
                issues=(issue,),
                message="补充信息",
            ),
            NeedClarification,
        ),
        (
            RequirementDomainState(
                requirement=requirement,
                metadata=metadata,
                confirmation_status="awaiting_confirmation",
            ),
            NeedConfirmation(
                requirement=requirement,
                metadata=metadata,
                summary="summary",
            ),
            NeedConfirmation,
        ),
        (
            RequirementDomainState(
                requirement=requirement,
                metadata=metadata,
                confirmation_status="confirmed",
            ),
            ConfirmedTravelRequirement(
                schema_version="1.0",
                requirement=requirement,
                metadata_without_raw_evidence=metadata,
                confirmation_summary_digest="digest",
            ),
            ConfirmedTravelRequirement,
        ),
        (
            RequirementDomainState(
                requirement=requirement,
                metadata=metadata,
                issues=(issue,),
                confirmation_status="confirmed",
            ),
            RequirementExtractionFailed(code=RequirementErrorCode.STRICT_SCHEMA_ERROR),
            RequirementExtractionFailed,
        ),
        (
            RequirementDomainState(
                requirement=invalidated_requirement,
                metadata=metadata,
                confirmation_status="awaiting_confirmation",
            ),
            RequirementInvalidated(
                changed_fields=(RequirementFieldPath.DESTINATION,),
                before_digest="before",
                after_digest=invalidated_digest,
                reason="confirmed_requirement_changed",
                summary="summary",
            ),
            RequirementInvalidated,
        ),
    ]


@pytest.mark.parametrize("state,outcome,expected_type", _valid_transition_cases())
def test_transition_accepts_all_five_state_outcome_branches_and_round_trips(
    state, outcome, expected_type
):
    transition = RequirementDomainTransition(state=state, outcome=outcome)
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
    assert isinstance(restored.outcome, expected_type)
    assert restored.state.requirement.time_window.arrival.hub_type is HubType.RAILWAY_STATION
    assert isinstance(restored.state.issues, tuple)
    assert isinstance(restored.state.metadata.metadata[RequirementFieldPath.DESTINATION].source_turn_id, type(uuid4()))
    if isinstance(restored.outcome, NeedClarification):
        assert isinstance(restored.outcome.issues, tuple)
    if isinstance(restored.outcome, RequirementInvalidated):
        assert restored.outcome.changed_fields == (RequirementFieldPath.DESTINATION,)


def test_transition_and_invalidated_have_exact_annotations_and_field_order():
    hints = get_type_hints(RequirementDomainTransition, include_extras=True)
    assert hints["state"] is RequirementDomainState
    assert hints["outcome"] == RequirementDomainOutcome
    assert tuple(RequirementInvalidated.model_fields) == (
        "outcome_type",
        "changed_fields",
        "before_digest",
        "after_digest",
        "reason",
        "summary",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda state, _outcome: setattr(
                state.metadata.metadata[RequirementFieldPath.DESTINATION],
                "source_turn_id",
                str(uuid4()),
            ),
            id="uuid-to-string",
        ),
        pytest.param(
            lambda state, _outcome: setattr(
                state.requirement.time_window.arrival, "hub_type", "railway_station"
            ),
            id="enum-to-string",
        ),
        pytest.param(
            lambda state, _outcome: setattr(
                state.requirement.party, "adults_count", True
            ),
            id="int-to-bool",
        ),
        pytest.param(
            lambda state, _outcome: setattr(state, "issues", [RequirementIssue(
                code="x", field_path=RequirementFieldPath.DESTINATION, message="x"
            )]),
            id="tuple-to-list",
        ),
        pytest.param(
            lambda state, _outcome: setattr(state.requirement, "destination", 123),
            id="constrained-string-to-int",
        ),
        pytest.param(
            lambda state, _outcome: state.metadata.metadata.__setitem__("bad", FieldMetadata()),
            id="metadata-key-to-string",
        ),
        pytest.param(
            lambda state, _outcome: setattr(
                state.metadata.metadata[RequirementFieldPath.DESTINATION].issues[0],
                "field_path",
                "destination",
            ),
            id="issue-field-path-to-string",
        ),
    ],
)
def test_transition_rejects_mutation_corrupted_pydantic_instances(mutate):
    state, outcome, _ = _valid_transition_cases()[0]
    mutate(state, outcome)
    with pytest.raises(ValidationError):
        RequirementDomainTransition(state=state, outcome=outcome)


@pytest.mark.parametrize(
    "state_mutation,outcome_factory",
    [
        (lambda state: setattr(state, "confirmation_status", "awaiting_confirmation"), lambda r, m, i: NeedClarification(requirement=r, metadata=m, issues=(i,), message="x")),
        (lambda state: setattr(state, "requirement", TravelRequirement(destination="上海")), lambda r, m, i: NeedClarification(requirement=r, metadata=m, issues=(i,), message="x")),
        (lambda state: setattr(state, "metadata", FieldMetadataRegistry()), lambda r, m, i: NeedClarification(requirement=r, metadata=m, issues=(i,), message="x")),
        (lambda state: setattr(state, "issues", ()), lambda r, m, i: NeedClarification(requirement=r, metadata=m, issues=(i,), message="x")),
    ],
)
def test_transition_rejects_need_clarification_pair_mismatches(state_mutation, outcome_factory):
    state, _outcome, _ = _valid_transition_cases()[0]
    requirement = state.requirement
    metadata = state.metadata
    issue = state.issues[0]
    state_mutation(state)
    with pytest.raises(ValidationError):
        RequirementDomainTransition(
            state=state,
            outcome=outcome_factory(requirement, metadata, issue),
        )


@pytest.mark.parametrize(
    "state_mutation,outcome_factory",
    [
        (lambda state: setattr(state, "confirmation_status", "collecting"), lambda r, m: NeedConfirmation(requirement=r, metadata=m, summary="x")),
        (lambda state: setattr(state, "issues", (RequirementIssue(code="x", field_path=RequirementFieldPath.DESTINATION, message="x"),)), lambda r, m: NeedConfirmation(requirement=r, metadata=m, summary="x")),
        (lambda state: setattr(state, "requirement", TravelRequirement(destination="上海")), lambda r, m: NeedConfirmation(requirement=r, metadata=m, summary="x")),
        (lambda state: setattr(state, "metadata", FieldMetadataRegistry()), lambda r, m: NeedConfirmation(requirement=r, metadata=m, summary="x")),
    ],
)
def test_transition_rejects_need_confirmation_pair_mismatches(state_mutation, outcome_factory):
    state, outcome, _ = _valid_transition_cases()[1]
    state_mutation(state)
    with pytest.raises(ValidationError):
        RequirementDomainTransition(
            state=state,
            outcome=outcome_factory(outcome.requirement, outcome.metadata),
        )


def test_transition_rejects_confirmed_invalidated_and_boundary_mismatches():
    for mutation in (
        lambda state: setattr(state, "confirmation_status", "awaiting_confirmation"),
        lambda state: setattr(state, "issues", (RequirementIssue(
            code="x", field_path=RequirementFieldPath.DESTINATION, message="x"
        ),)),
        lambda state: setattr(state, "requirement", TravelRequirement(destination="上海")),
        lambda state: setattr(state, "metadata", FieldMetadataRegistry()),
    ):
        confirmed_state, confirmed_outcome, _ = _valid_transition_cases()[2]
        mutation(confirmed_state)
        with pytest.raises(ValidationError):
            RequirementDomainTransition(state=confirmed_state, outcome=confirmed_outcome)

    invalidated_state, invalidated_outcome, _ = _valid_transition_cases()[4]
    invalidated_state.issues = (RequirementIssue(
        code="x", field_path=RequirementFieldPath.DESTINATION, message="x"
    ),)
    with pytest.raises(ValidationError):
        RequirementDomainTransition(state=invalidated_state, outcome=invalidated_outcome)

    invalidated_state, invalidated_outcome, _ = _valid_transition_cases()[4]
    invalidated_state.confirmation_status = "collecting"
    with pytest.raises(ValidationError):
        RequirementDomainTransition(state=invalidated_state, outcome=invalidated_outcome)

    invalidated_state, invalidated_outcome, _ = _valid_transition_cases()[4]
    invalidated_outcome.after_digest = "wrong"
    with pytest.raises(ValidationError):
        RequirementDomainTransition(state=invalidated_state, outcome=invalidated_outcome)

    valid_state, valid_outcome, _ = _valid_transition_cases()[1]
    with pytest.raises(ValidationError):
        RequirementDomainTransition(state=None, outcome=valid_outcome)
    with pytest.raises(ValidationError):
        RequirementDomainTransition(state=valid_state, outcome=None)
    with pytest.raises(ValidationError):
        RequirementDomainTransition(state=valid_state)
    with pytest.raises(ValidationError):
        RequirementDomainTransition(state=valid_state, outcome=valid_outcome, extra="x")


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
        summary="summary",
    )
    for outcome in (clarification, confirmation, confirmed, failed, invalidated):
        assert not hasattr(outcome, "state")


def test_transition_rebuilds_nested_state_and_outcome_without_shared_references():
    requirement = _transition_requirement()
    metadata, issue = _transition_metadata()
    state = RequirementDomainState(
        requirement=requirement,
        metadata=metadata,
        issues=(issue,),
        confirmation_status="collecting",
    )
    outcome = NeedClarification(
        requirement=requirement,
        metadata=metadata,
        issues=(issue,),
        message="补充信息",
    )

    transition = RequirementDomainTransition(state=state, outcome=outcome)

    assert transition.state is not state
    assert transition.outcome is not outcome
    assert transition.state.requirement is not transition.outcome.requirement
    assert transition.state.metadata is not transition.outcome.metadata
    transition.outcome.requirement.destination = "上海"
    assert transition.state.requirement.destination == "杭州"
    transition.outcome.metadata.metadata[RequirementFieldPath.DESTINATION].issues[0].message = "outcome"
    assert transition.state.metadata.metadata[RequirementFieldPath.DESTINATION].issues[0].message == "invalid"
    state.requirement.destination = "南京"
    state.metadata.metadata[RequirementFieldPath.DESTINATION].issues[0].message = "input"
    state.issues[0].message = "input issue"
    assert transition.state.requirement.destination == "杭州"
    assert transition.state.metadata.metadata[RequirementFieldPath.DESTINATION].issues[0].message == "invalid"
    assert transition.state.issues[0].message == "invalid"
    transition.state.issues[0].message = "state issue"
    assert transition.outcome.issues[0].message == "invalid"


def test_transition_rejects_mismatched_outcome_state_pair():
    with pytest.raises(ValidationError):
        RequirementDomainTransition(
            state=RequirementDomainState(),
            outcome=NeedConfirmation(
                requirement=TravelRequirement(),
                metadata=FieldMetadataRegistry(),
                summary="summary",
            ),
        )
