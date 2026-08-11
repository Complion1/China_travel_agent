"""Safe workflow event registry tests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.workflow_contracts.errors import (
    EventSinkContractError,
    SafeWorkflowEventRegistryConfigurationError,
    SafeWorkflowEventValidationError,
    UnsupportedWorkflowEventTypeError,
)
from app.workflow_contracts.events.models import SafeCounterDelta, SafeWorkflowEvent
from app.workflow_contracts.events.registry import (
    STANDARD_EVENT_DELIVERY_FAILURE_CODES,
    STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS,
    EventDeliveryFailureCodeRegistry,
    SafeWorkflowEventRegistration,
    SafeWorkflowEventRegistry,
)
from app.workflow_contracts.identifiers import FieldRequirement, OperationId, WorkflowPhase


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
TRACE_ID = UUID("22345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _event(
    event_type: str,
    counter_name: str,
    *,
    operation: bool = False,
    duration_ms: int | None = None,
    terminal_code: str | None = None,
    delta: int = 1,
    event_id: UUID = UUID("32345678-1234-5678-1234-567812345678"),
) -> SafeWorkflowEvent:
    return SafeWorkflowEvent(
        schema_version="1.0",
        event_id=event_id,
        sequence_number=1,
        occurred_at=UTC,
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        operation_id=(
            OperationId(
                run_id=RUN_ID,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="test_step",
            )
            if operation
            else None
        ),
        phase=WorkflowPhase.REQUIREMENTS,
        event_type=event_type,
        counter_deltas=(SafeCounterDelta(name=counter_name, delta=delta),),
        duration_ms=duration_ms,
        terminal_code=terminal_code,
    )


def test_standard_event_and_failure_code_catalogues_are_exact() -> None:
    assert tuple(registration.event_type for registration in STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS) == (
        "workflow.started",
        "workflow.resumed",
        "workflow.replayed",
        "workflow.terminal",
        "artifact.created",
        "artifact.reused",
        "budget.consumed",
        "budget.exceeded",
    )
    assert STANDARD_EVENT_DELIVERY_FAILURE_CODES == (
        "sink_unavailable",
        "sink_timeout",
        "sink_rejected",
        "sink_capacity_exceeded",
    )

    registry = SafeWorkflowEventRegistry(STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS)
    cases = (
        _event("workflow.started", "workflow.started"),
        _event("workflow.resumed", "workflow.resumed"),
        _event("workflow.replayed", "workflow.replayed", operation=True),
        _event("workflow.terminal", "workflow.terminal", duration_ms=4, terminal_code="workflow_completed"),
        _event("artifact.created", "artifact.created", operation=True),
        _event("artifact.reused", "artifact.cache_hit", operation=True),
        _event("budget.consumed", "budget.units_consumed", operation=True, delta=3),
        _event("budget.exceeded", "budget.exceeded", operation=True),
    )
    for event in cases:
        registry.validate(event)


def test_event_registry_field_requirements_and_delta_validation() -> None:
    registry = SafeWorkflowEventRegistry(STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS)
    with pytest.raises(SafeWorkflowEventValidationError):
        registry.validate(_event("workflow.replayed", "workflow.replayed"))
    with pytest.raises(SafeWorkflowEventValidationError):
        registry.validate(_event("workflow.started", "workflow.started", delta=2))
    invalid_budget_event = SafeWorkflowEvent.model_construct(
        schema_version="1.0",
        event_id=UUID("42345678-1234-5678-1234-567812345678"),
        sequence_number=1,
        occurred_at=UTC,
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        operation_id=OperationId(
            run_id=RUN_ID,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="test_step",
        ),
        phase=WorkflowPhase.REQUIREMENTS,
        event_type="budget.consumed",
        counter_deltas=(
            SafeCounterDelta.model_construct(name="budget.units_consumed", delta=0),
        ),
        duration_ms=None,
        terminal_code=None,
    )
    with pytest.raises(SafeWorkflowEventValidationError):
        registry.validate(invalid_budget_event)

    optional = SafeWorkflowEventRegistry(
        (
            SafeWorkflowEventRegistration(
                event_type="custom.event",
                counter_names=("custom.counter",),
                operation_id=FieldRequirement.OPTIONAL,
                duration_ms=FieldRequirement.OPTIONAL,
                terminal_code=FieldRequirement.OPTIONAL,
            ),
        )
    )
    optional.validate(_event("custom.event", "custom.counter"))
    optional.validate(
        _event(
            "custom.event",
            "custom.counter",
            operation=True,
            duration_ms=2,
            terminal_code="custom_terminal",
        )
    )


def test_event_registry_configuration_unknown_and_strict_copy() -> None:
    with pytest.raises(SafeWorkflowEventRegistryConfigurationError):
        SafeWorkflowEventRegistry(STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS[:1] * 2)
    forged = SafeWorkflowEventRegistration.model_construct(
        event_type="custom.event",
        counter_names=("z.counter", "a.counter"),
        operation_id=FieldRequirement.OPTIONAL,
        duration_ms=FieldRequirement.OPTIONAL,
        terminal_code=FieldRequirement.OPTIONAL,
    )
    with pytest.raises(SafeWorkflowEventRegistryConfigurationError):
        SafeWorkflowEventRegistry((forged,))

    registry = SafeWorkflowEventRegistry(STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS)
    with pytest.raises(UnsupportedWorkflowEventTypeError):
        registry.resolve("custom.unknown")
    forged_event = SafeWorkflowEvent.model_construct(
        schema_version="1.0",
        event_id=UUID("42345678-1234-5678-1234-567812345678"),
        sequence_number=1,
        occurred_at=UTC,
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        operation_id=None,
        phase=WorkflowPhase.REQUIREMENTS,
        event_type="workflow.started",
        counter_deltas=(),
        duration_ms=None,
        terminal_code=None,
    )
    with pytest.raises(SafeWorkflowEventValidationError):
        registry.validate(forged_event)


def test_event_registration_rejects_duplicate_and_unsorted_counter_names() -> None:
    with pytest.raises(ValidationError):
        SafeWorkflowEventRegistration(
            event_type="custom.duplicate",
            counter_names=("custom.counter", "custom.counter"),
            operation_id=FieldRequirement.OPTIONAL,
            duration_ms=FieldRequirement.OPTIONAL,
            terminal_code=FieldRequirement.OPTIONAL,
        )
    with pytest.raises(ValidationError):
        SafeWorkflowEventRegistration(
            event_type="custom.unsorted",
            counter_names=("z.counter", "a.counter"),
            operation_id=FieldRequirement.OPTIONAL,
            duration_ms=FieldRequirement.OPTIONAL,
            terminal_code=FieldRequirement.OPTIONAL,
        )


def test_event_registry_rejects_unreadable_and_non_iterable_registrations() -> None:
    with pytest.raises(SafeWorkflowEventRegistryConfigurationError):
        SafeWorkflowEventRegistry((object(),))  # type: ignore[arg-type]
    with pytest.raises(SafeWorkflowEventRegistryConfigurationError):
        SafeWorkflowEventRegistry(None)  # type: ignore[arg-type]


def test_event_registry_configuration_failure_precedes_runtime_event_validation() -> None:
    forged = SafeWorkflowEventRegistration.model_construct(
        event_type="custom.invalid",
        counter_names=("z.counter", "a.counter"),
        operation_id=FieldRequirement.OPTIONAL,
        duration_ms=FieldRequirement.OPTIONAL,
        terminal_code=FieldRequirement.OPTIONAL,
    )
    with pytest.raises(SafeWorkflowEventRegistryConfigurationError):
        SafeWorkflowEventRegistry((forged,))


def test_failure_code_registry_validates_format_before_membership() -> None:
    registry = EventDeliveryFailureCodeRegistry(STANDARD_EVENT_DELIVERY_FAILURE_CODES)
    registry.validate("sink_timeout")
    with pytest.raises(EventSinkContractError):
        registry.validate("not lower snake")
    with pytest.raises(EventSinkContractError):
        registry.validate("unknown_code")
    with pytest.raises(SafeWorkflowEventRegistryConfigurationError):
        EventDeliveryFailureCodeRegistry(("sink_timeout", "sink_timeout"))
    with pytest.raises(SafeWorkflowEventRegistryConfigurationError):
        EventDeliveryFailureCodeRegistry(("not lower snake",))
