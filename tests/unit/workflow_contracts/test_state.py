"""State, terminal registry, and state codec contract tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.requirements import (
    ConfirmedTravelRequirement,
    FieldMetadataRegistry,
    NeedClarification,
    NeedConfirmation,
    RequirementErrorCode,
    RequirementExtractionFailed,
    RequirementFieldPath,
    RequirementInvalidated,
    TravelRequirement,
)
from app.workflow_contracts.artifacts.models import ArtifactRef
from app.workflow_contracts.errors import (
    EventSinkContractError,
    TerminalRegistryConfigurationError,
    TerminalStateMismatchError,
    UnsupportedTerminalCodeError,
    UnsupportedWorkflowStateVersionError,
    WorkflowStateValidationError,
)
from app.workflow_contracts.identifiers import FieldRequirement, OperationId, WorkflowPhase
from app.workflow_contracts.state import (
    BASE_TERMINAL_CODE_REGISTRATIONS,
    DeliveryStatus,
    TerminalCategory,
    TerminalCodeRegistry,
    TerminalCodeRegistration,
    TerminalOutcome,
    TravelSessionState,
    parse_travel_session_state_json,
    serialize_travel_session_state,
)


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
TRACE_ID = UUID("22345678-1234-5678-1234-567812345678")
OTHER_RUN_ID = UUID("32345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _operation(
    logical_step: str,
    *,
    phase: WorkflowPhase = WorkflowPhase.REQUIREMENTS,
    run_id: UUID = RUN_ID,
) -> OperationId:
    return OperationId(run_id=run_id, phase=phase, logical_step=logical_step)


def _ref(
    artifact_id: UUID,
    logical_step: str,
    *,
    phase: WorkflowPhase = WorkflowPhase.REQUIREMENTS,
    run_id: UUID = RUN_ID,
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content_hash="sha256:" + "0" * 64,
        operation_id=_operation(logical_step, phase=phase, run_id=run_id),
        created_at=UTC,
        size_bytes=1,
    )


def _outcomes() -> tuple[object, ...]:
    requirement = TravelRequirement()
    metadata = FieldMetadataRegistry()
    return (
        NeedClarification(
            requirement=requirement,
            metadata=metadata,
            issues=(),
            message="需要确认：杭州",
        ),
        NeedConfirmation(
            requirement=requirement,
            metadata=metadata,
            summary="请确认杭州行程",
        ),
        ConfirmedTravelRequirement(
            schema_version="1.0",
            requirement=requirement,
            metadata_without_raw_evidence=metadata,
            confirmation_summary_digest="digest-1",
        ),
        RequirementExtractionFailed(code=RequirementErrorCode.INPUT_TOO_LONG),
        RequirementInvalidated(
            changed_fields=(RequirementFieldPath.DESTINATION,),
            before_digest="before",
            after_digest="after",
            reason="用户修正",
        ),
    )


def _state(
    *,
    delivery_status: DeliveryStatus = DeliveryStatus.IN_PROGRESS,
    terminal_code: str | None = None,
    terminal_outcome: TerminalOutcome | None = None,
    requirement_outcome: object | None = None,
    artifact_refs: tuple[ArtifactRef, ...] = (),
) -> TravelSessionState:
    return TravelSessionState(
        schema_version="1.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        phase=WorkflowPhase.REQUIREMENTS,
        delivery_status=delivery_status,
        terminal_code=terminal_code,
        terminal_outcome=terminal_outcome,
        requirement_outcome=requirement_outcome,
        artifact_refs=artifact_refs,
    )


def _cancelled_outcome(*, trace_id: UUID = TRACE_ID) -> TerminalOutcome:
    return TerminalOutcome(
        schema_version="1.0",
        terminal_code="cancelled_by_user",
        category=TerminalCategory.INPUT,
        recoverable=False,
        user_action_code=None,
        trace_id=trace_id,
    )


def test_state_dtos_are_strict_frozen_forbid_and_required() -> None:
    for model in (TerminalOutcome, TravelSessionState, TerminalCodeRegistration):
        assert model.model_config["strict"] is True
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"
        assert all(field.is_required() for field in model.model_fields.values())
    assert TravelSessionState.model_fields["requirement_outcome"].repr is False


def test_delivery_status_matrix_and_terminal_outcome_invariants() -> None:
    for status in (DeliveryStatus.IN_PROGRESS, DeliveryStatus.AWAITING_USER):
        _state(delivery_status=status)
        with pytest.raises(ValidationError):
            _state(delivery_status=status, terminal_code="not_allowed")

    _state(delivery_status=DeliveryStatus.DEMO_READY, terminal_code="workflow_completed")
    with pytest.raises(ValidationError):
        _state(delivery_status=DeliveryStatus.DEMO_READY)

    for status in (
        DeliveryStatus.DEGRADED,
        DeliveryStatus.FAILED,
        DeliveryStatus.CANCELLED,
    ):
        _state(
            delivery_status=status,
            terminal_code="cancelled_by_user",
            terminal_outcome=_cancelled_outcome(),
        )
        with pytest.raises(ValidationError):
            _state(delivery_status=status, terminal_code="cancelled_by_user")

    with pytest.raises(ValidationError):
        _state(
            delivery_status=DeliveryStatus.CANCELLED,
            terminal_code="cancelled_by_user",
            terminal_outcome=TerminalOutcome(
                schema_version="1.0",
                terminal_code="other_code",
                category=TerminalCategory.INPUT,
                recoverable=False,
                user_action_code=None,
                trace_id=TRACE_ID,
            ),
        )
    with pytest.raises(ValidationError):
        TerminalOutcome(
            schema_version="1.0",
            terminal_code="cancelled_by_user",
            category=TerminalCategory.INPUT,
            recoverable=False,
            user_action_code="retry",
            trace_id=TRACE_ID,
        )

    with pytest.raises(ValidationError):
        _state(
            delivery_status=DeliveryStatus.CANCELLED,
            terminal_code="cancelled_by_user",
            terminal_outcome=_cancelled_outcome(trace_id=OTHER_RUN_ID),
        )


def test_state_rejects_forged_requirement_outcome_during_fresh_copy() -> None:
    forged = NeedClarification.model_construct(
        requirement=TravelRequirement(),
        metadata=FieldMetadataRegistry(),
        issues=(),
    )
    with pytest.raises(ValidationError):
        _state(requirement_outcome=forged)


@pytest.mark.parametrize("outcome", _outcomes())
def test_requirement_domain_outcome_round_trips_every_branch_fresh(outcome: object) -> None:
    state = _state(requirement_outcome=outcome)
    assert state.requirement_outcome is not outcome
    encoded = serialize_travel_session_state(state)
    decoded = parse_travel_session_state_json(encoded)
    assert type(decoded.requirement_outcome) is type(outcome)
    assert decoded.requirement_outcome == outcome
    assert decoded.requirement_outcome is not state.requirement_outcome
    assert b"\\u" not in encoded


def test_state_codec_is_deterministic_and_accepts_only_safe_json_bytes() -> None:
    state = _state(requirement_outcome=_outcomes()[0])
    first = serialize_travel_session_state(state)
    second = serialize_travel_session_state(state)
    assert first == second
    assert first == first.rstrip()
    assert parse_travel_session_state_json(b" \n" + first + b" \n") == state

    invalid_inputs = (
        bytearray(first),
        first.decode("utf-8"),
        b"\xff",
        b"\xef\xbb\xbf" + first,
        b"[]",
        first + b" trailing",
    )
    for invalid in invalid_inputs:
        with pytest.raises(WorkflowStateValidationError):
            parse_travel_session_state_json(invalid)  # type: ignore[arg-type]


def test_state_codec_duplicate_and_version_error_priority() -> None:
    with pytest.raises(WorkflowStateValidationError):
        parse_travel_session_state_json(
            b'{"schema_version":"1.0","schema_version":"1.0"}'
        )
    with pytest.raises(UnsupportedWorkflowStateVersionError):
        parse_travel_session_state_json(
            b'{"schema_version":"9.0","nested":{"a":1,"a":2},"bad":NaN}'
        )
    with pytest.raises(UnsupportedWorkflowStateVersionError):
        parse_travel_session_state_json(
            b'{"schema_version":"9.0","run_id":1,"run_id":2,"bad":NaN}'
        )
    with pytest.raises(UnsupportedWorkflowStateVersionError):
        parse_travel_session_state_json(b'{"schema_version":null}')
    with pytest.raises(WorkflowStateValidationError):
        parse_travel_session_state_json(
            b'{"schema_version":"1.0","nested":{"a":1,"a":2}}'
        )
    with pytest.raises(WorkflowStateValidationError):
        parse_travel_session_state_json(
            b'{"schema_version":"1.0","run_id":1,"run_id":2}'
        )
    with pytest.raises(WorkflowStateValidationError):
        parse_travel_session_state_json(b'{"schema_version":"1.0","bad":NaN}')
    with pytest.raises(WorkflowStateValidationError):
        parse_travel_session_state_json(b'{"schema_version":"1.0"}')


def test_state_model_construct_and_nested_mutation_cannot_bypass_codec() -> None:
    forged = TravelSessionState.model_construct(
        schema_version="1.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        phase=WorkflowPhase.REQUIREMENTS,
        delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code="forged",
        terminal_outcome=None,
        requirement_outcome=None,
        artifact_refs=(),
    )
    with pytest.raises(WorkflowStateValidationError):
        serialize_travel_session_state(forged)

    requirement = TravelRequirement()
    outcome = NeedClarification(
        requirement=requirement,
        metadata=FieldMetadataRegistry(),
        issues=(),
        message="secret requirement text",
    )
    state = _state(requirement_outcome=outcome)
    requirement.destination = "Shanghai"
    assert state.requirement_outcome.requirement.destination is None  # type: ignore[union-attr]
    assert "secret requirement text" not in repr(state)


def test_artifact_refs_require_same_run_unique_ids_and_fixed_order() -> None:
    first = _ref(UUID("42345678-1234-5678-1234-567812345678"), "z_step")
    second = _ref(
        UUID("52345678-1234-5678-1234-567812345678"),
        "a_step",
        phase=WorkflowPhase.HOTEL_SELECTION,
    )
    _state(artifact_refs=(first, second))
    with pytest.raises(ValidationError):
        _state(artifact_refs=(second, first))
    with pytest.raises(ValidationError):
        _state(artifact_refs=(first, first))
    with pytest.raises(ValidationError):
        _state(
            artifact_refs=(
                first,
                _ref(
                    UUID("62345678-1234-5678-1234-567812345678"),
                    "other",
                    run_id=OTHER_RUN_ID,
                ),
            )
        )
    with pytest.raises(ValidationError):
        _state(
            artifact_refs=(
                first,
                _ref(
                    UUID("72345678-1234-5678-1234-567812345678"),
                    "z_step",
                ),
            )
        )


def test_terminal_registry_base_entries_nonterminal_fast_path_and_matrix() -> None:
    registry = TerminalCodeRegistry(BASE_TERMINAL_CODE_REGISTRATIONS)
    assert tuple(registration.terminal_code for registration in BASE_TERMINAL_CODE_REGISTRATIONS) == (
        "workflow_completed",
        "cancelled_by_user",
    )
    registry.validate_state(_state())
    registry.validate_state(
        _state(
            delivery_status=DeliveryStatus.DEMO_READY,
            terminal_code="workflow_completed",
        )
    )
    registry.validate_state(
        _state(
            delivery_status=DeliveryStatus.CANCELLED,
            terminal_code="cancelled_by_user",
            terminal_outcome=_cancelled_outcome(),
        )
    )

    unknown = _state(
        delivery_status=DeliveryStatus.CANCELLED,
        terminal_code="unknown_terminal",
        terminal_outcome=TerminalOutcome(
            schema_version="1.0",
            terminal_code="unknown_terminal",
            category=TerminalCategory.INTERNAL,
            recoverable=False,
            user_action_code=None,
            trace_id=TRACE_ID,
        ),
    )
    with pytest.raises(UnsupportedTerminalCodeError):
        registry.validate_state(unknown)


def test_terminal_registry_configuration_and_validation_order() -> None:
    with pytest.raises(TerminalRegistryConfigurationError):
        TerminalCodeRegistry(BASE_TERMINAL_CODE_REGISTRATIONS * 2)

    forged = TerminalCodeRegistration.model_construct(
        terminal_code="bad_terminal",
        delivery_status=DeliveryStatus.IN_PROGRESS,
        outcome_requirement=FieldRequirement.FORBIDDEN,
        category=None,
        recoverable=None,
        user_action_codes=(),
    )
    with pytest.raises(TerminalRegistryConfigurationError):
        TerminalCodeRegistry((forged,))

    action_registry = TerminalCodeRegistry(
        (
            TerminalCodeRegistration(
                terminal_code="needs_action",
                delivery_status=DeliveryStatus.DEGRADED,
                outcome_requirement=FieldRequirement.REQUIRED,
                category=TerminalCategory.PROVIDER,
                recoverable=True,
                user_action_codes=("retry",),
            ),
        )
    )
    good = _state(
        delivery_status=DeliveryStatus.DEGRADED,
        terminal_code="needs_action",
        terminal_outcome=TerminalOutcome(
            schema_version="1.0",
            terminal_code="needs_action",
            category=TerminalCategory.PROVIDER,
            recoverable=True,
            user_action_code="retry",
            trace_id=TRACE_ID,
        ),
    )
    action_registry.validate_state(good)
    bad_action = good.model_copy(
        update={
            "terminal_outcome": TerminalOutcome(
                schema_version="1.0",
                terminal_code="needs_action",
                category=TerminalCategory.PROVIDER,
                recoverable=True,
                user_action_code="other_action",
                trace_id=TRACE_ID,
            )
        }
    )
    with pytest.raises(TerminalStateMismatchError):
        action_registry.validate_state(bad_action)

    with pytest.raises(TerminalRegistryConfigurationError):
        TerminalCodeRegistry(None)  # type: ignore[arg-type]
    with pytest.raises(UnsupportedTerminalCodeError):
        action_registry.resolve([])  # type: ignore[arg-type]


def test_terminal_registration_rejects_action_order_and_semantic_combinations() -> None:
    with pytest.raises(ValidationError):
        TerminalCodeRegistration(
            terminal_code="duplicate_actions",
            delivery_status=DeliveryStatus.DEGRADED,
            outcome_requirement=FieldRequirement.REQUIRED,
            category=TerminalCategory.PROVIDER,
            recoverable=True,
            user_action_codes=("retry", "retry"),
        )
    with pytest.raises(ValidationError):
        TerminalCodeRegistration(
            terminal_code="unsorted_actions",
            delivery_status=DeliveryStatus.DEGRADED,
            outcome_requirement=FieldRequirement.REQUIRED,
            category=TerminalCategory.PROVIDER,
            recoverable=True,
            user_action_codes=("z_action", "a_action"),
        )
    with pytest.raises(ValidationError):
        TerminalCodeRegistration(
            terminal_code="forbidden_category",
            delivery_status=DeliveryStatus.DEMO_READY,
            outcome_requirement=FieldRequirement.FORBIDDEN,
            category=TerminalCategory.INPUT,
            recoverable=None,
            user_action_codes=(),
        )
    with pytest.raises(ValidationError):
        TerminalCodeRegistration(
            terminal_code="forbidden_recoverable",
            delivery_status=DeliveryStatus.DEMO_READY,
            outcome_requirement=FieldRequirement.FORBIDDEN,
            category=None,
            recoverable=False,
            user_action_codes=(),
        )
    with pytest.raises(ValidationError):
        TerminalCodeRegistration(
            terminal_code="forbidden_action",
            delivery_status=DeliveryStatus.DEMO_READY,
            outcome_requirement=FieldRequirement.FORBIDDEN,
            category=None,
            recoverable=None,
            user_action_codes=("retry",),
        )


def test_terminal_registration_rejects_missing_required_semantics_and_optional_requirement() -> None:
    with pytest.raises(ValidationError):
        TerminalCodeRegistration(
            terminal_code="missing_category",
            delivery_status=DeliveryStatus.DEGRADED,
            outcome_requirement=FieldRequirement.REQUIRED,
            category=None,
            recoverable=True,
            user_action_codes=(),
        )
    with pytest.raises(ValidationError):
        TerminalCodeRegistration(
            terminal_code="missing_recoverable",
            delivery_status=DeliveryStatus.DEGRADED,
            outcome_requirement=FieldRequirement.REQUIRED,
            category=TerminalCategory.PROVIDER,
            recoverable=None,
            user_action_codes=(),
        )
    with pytest.raises(ValidationError):
        TerminalCodeRegistration(
            terminal_code="nonrecoverable_action",
            delivery_status=DeliveryStatus.DEGRADED,
            outcome_requirement=FieldRequirement.REQUIRED,
            category=TerminalCategory.PROVIDER,
            recoverable=False,
            user_action_codes=("retry",),
        )
    with pytest.raises(ValidationError):
        TerminalCodeRegistration(
            terminal_code="optional_outcome",
            delivery_status=DeliveryStatus.DEGRADED,
            outcome_requirement=FieldRequirement.OPTIONAL,
            category=None,
            recoverable=None,
            user_action_codes=(),
        )


def test_terminal_registry_rejects_observable_registration_state_mismatches() -> None:
    demo_mismatch = TerminalCodeRegistry(
        (
            TerminalCodeRegistration(
                terminal_code="demo_mismatch",
                delivery_status=DeliveryStatus.CANCELLED,
                outcome_requirement=FieldRequirement.REQUIRED,
                category=TerminalCategory.INPUT,
                recoverable=False,
                user_action_codes=(),
            ),
        )
    )
    with pytest.raises(TerminalStateMismatchError):
        demo_mismatch.validate_state(
            _state(
                delivery_status=DeliveryStatus.DEMO_READY,
                terminal_code="demo_mismatch",
            )
        )

    status_mismatch = TerminalCodeRegistry(
        (
            TerminalCodeRegistration(
                terminal_code="status_mismatch",
                delivery_status=DeliveryStatus.DEGRADED,
                outcome_requirement=FieldRequirement.REQUIRED,
                category=TerminalCategory.INPUT,
                recoverable=False,
                user_action_codes=(),
            ),
        )
    )
    status_state = _state(
        delivery_status=DeliveryStatus.CANCELLED,
        terminal_code="status_mismatch",
        terminal_outcome=TerminalOutcome(
            schema_version="1.0",
            terminal_code="status_mismatch",
            category=TerminalCategory.INPUT,
            recoverable=False,
            user_action_code=None,
            trace_id=TRACE_ID,
        ),
    )
    with pytest.raises(TerminalStateMismatchError):
        status_mismatch.validate_state(status_state)

    forbidden_mismatch = TerminalCodeRegistry(
        (
            TerminalCodeRegistration(
                terminal_code="forbidden_terminal",
                delivery_status=DeliveryStatus.CANCELLED,
                outcome_requirement=FieldRequirement.FORBIDDEN,
                category=None,
                recoverable=None,
                user_action_codes=(),
            ),
        )
    )
    forbidden_state = _state(
        delivery_status=DeliveryStatus.CANCELLED,
        terminal_code="forbidden_terminal",
        terminal_outcome=TerminalOutcome(
            schema_version="1.0",
            terminal_code="forbidden_terminal",
            category=TerminalCategory.INPUT,
            recoverable=False,
            user_action_code=None,
            trace_id=TRACE_ID,
        ),
    )
    with pytest.raises(TerminalStateMismatchError):
        forbidden_mismatch.validate_state(forbidden_state)

    semantic_mismatch = TerminalCodeRegistry(
        (
            TerminalCodeRegistration(
                terminal_code="semantic_mismatch",
                delivery_status=DeliveryStatus.CANCELLED,
                outcome_requirement=FieldRequirement.REQUIRED,
                category=TerminalCategory.PROVIDER,
                recoverable=True,
                user_action_codes=(),
            ),
        )
    )
    semantic_state = _state(
        delivery_status=DeliveryStatus.CANCELLED,
        terminal_code="semantic_mismatch",
        terminal_outcome=TerminalOutcome(
            schema_version="1.0",
            terminal_code="semantic_mismatch",
            category=TerminalCategory.INPUT,
            recoverable=False,
            user_action_code=None,
            trace_id=TRACE_ID,
        ),
    )
    with pytest.raises(TerminalStateMismatchError):
        semantic_mismatch.validate_state(semantic_state)

    recoverable_mismatch = TerminalCodeRegistry(
        (
            TerminalCodeRegistration(
                terminal_code="recoverable_mismatch",
                delivery_status=DeliveryStatus.CANCELLED,
                outcome_requirement=FieldRequirement.REQUIRED,
                category=TerminalCategory.INPUT,
                recoverable=True,
                user_action_codes=(),
            ),
        )
    )
    recoverable_state = _state(
        delivery_status=DeliveryStatus.CANCELLED,
        terminal_code="recoverable_mismatch",
        terminal_outcome=TerminalOutcome(
            schema_version="1.0",
            terminal_code="recoverable_mismatch",
            category=TerminalCategory.INPUT,
            recoverable=False,
            user_action_code=None,
            trace_id=TRACE_ID,
        ),
    )
    with pytest.raises(TerminalStateMismatchError):
        recoverable_mismatch.validate_state(recoverable_state)


def test_state_error_markers_do_not_expose_payload() -> None:
    secret = "top-secret-requirement"
    forged = TravelSessionState.model_construct(
        schema_version="1.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        phase=WorkflowPhase.REQUIREMENTS,
        delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code="forged",
        terminal_outcome=None,
        requirement_outcome=NeedClarification(
            requirement=TravelRequirement(),
            metadata=FieldMetadataRegistry(),
            issues=(),
            message=secret,
        ),
        artifact_refs=(),
    )
    with pytest.raises(WorkflowStateValidationError) as exc_info:
        serialize_travel_session_state(forged)
    assert str(exc_info.value) == "WorkflowStateValidationError"
    assert secret not in str(exc_info.value)
