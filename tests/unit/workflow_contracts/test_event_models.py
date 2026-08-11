"""Safe workflow event DTO tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, get_args, get_origin
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from app.workflow_contracts.events.models import (
    EventDeliveryFailed,
    EventEmitted,
    EventEmissionOutcome,
    SafeCounterDelta,
    SafeWorkflowEvent,
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
TRACE_ID = UUID("22345678-1234-5678-1234-567812345678")
EVENT_ID = UUID("32345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _operation(*, run_id: UUID = RUN_ID, phase: WorkflowPhase = WorkflowPhase.REQUIREMENTS) -> OperationId:
    return OperationId(run_id=run_id, phase=phase, logical_step="test_step")


def _event(
    *,
    event_type: str = "workflow.started",
    counter_name: str = "workflow.started",
    delta: int = 1,
    operation_id: OperationId | None = None,
    phase: WorkflowPhase = WorkflowPhase.REQUIREMENTS,
    run_id: UUID = RUN_ID,
    occurred_at: datetime = UTC,
    duration_ms: int | None = None,
    terminal_code: str | None = None,
    sequence_number: int = 1,
    event_id: UUID = EVENT_ID,
) -> SafeWorkflowEvent:
    return SafeWorkflowEvent(
        schema_version="1.0",
        event_id=event_id,
        sequence_number=sequence_number,
        occurred_at=occurred_at,
        run_id=run_id,
        trace_id=TRACE_ID,
        operation_id=operation_id,
        phase=phase,
        event_type=event_type,
        counter_deltas=(SafeCounterDelta(name=counter_name, delta=delta),),
        duration_ms=duration_ms,
        terminal_code=terminal_code,
    )


def test_event_dtos_are_strict_frozen_forbid_and_required() -> None:
    for model in (SafeCounterDelta, SafeWorkflowEvent, EventEmitted, EventDeliveryFailed):
        assert model.model_config["strict"] is True
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"
        for field in model.model_fields.values():
            if field.annotation is not str:
                assert field.is_required() or field.default is not None



def test_event_emission_outcome_is_exact_annotated_discriminated_union() -> None:
    assert get_origin(EventEmissionOutcome) is Annotated
    annotated_args = get_args(EventEmissionOutcome)
    assert len(annotated_args) == 2
    union_type, field_info = annotated_args
    assert get_args(union_type) == (EventEmitted, EventDeliveryFailed)
    assert field_info.discriminator == "outcome_type"


def test_event_ranges_utc_counter_order_and_operation_scope() -> None:
    _event()
    with pytest.raises(ValidationError):
        _event(sequence_number=0)
    with pytest.raises(ValidationError):
        _event(delta=0)
    with pytest.raises(ValidationError):
        _event(duration_ms=-1)
    with pytest.raises(ValidationError):
        _event(occurred_at=datetime(2026, 1, 2, 3, 4, 5))
    with pytest.raises(ValidationError):
        _event(occurred_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8))))

    with pytest.raises(ValidationError):
        SafeWorkflowEvent(
            schema_version="1.0",
            event_id=EVENT_ID,
            sequence_number=1,
            occurred_at=UTC,
            run_id=RUN_ID,
            trace_id=TRACE_ID,
            operation_id=None,
            phase=WorkflowPhase.REQUIREMENTS,
            event_type="workflow.started",
            counter_deltas=(
                SafeCounterDelta(name="z.counter", delta=1),
                SafeCounterDelta(name="a.counter", delta=1),
            ),
            duration_ms=None,
            terminal_code=None,
        )

    with pytest.raises(ValidationError):
        _event(
            operation_id=_operation(run_id=UUID("42345678-1234-5678-1234-567812345678"))
        )
    with pytest.raises(ValidationError):
        _event(
            operation_id=_operation(phase=WorkflowPhase.HOTEL_SELECTION),
        )


def test_event_outcome_discriminated_union_and_no_extra_payload() -> None:
    adapter = TypeAdapter(EventEmissionOutcome)
    emitted = EventEmitted(disposition="applied", event_id=EVENT_ID)
    failed = EventDeliveryFailed(event_id=EVENT_ID, failure_code="sink_timeout")
    emitted_copy = adapter.validate_json(
        emitted.model_dump_json(),
        strict=True,
    )
    failed_copy = adapter.validate_json(
        failed.model_dump_json(),
        strict=True,
    )
    assert type(emitted_copy) is EventEmitted
    assert type(failed_copy) is EventDeliveryFailed
    with pytest.raises(ValidationError):
        EventEmitted(disposition="applied", event_id=EVENT_ID, details="secret")  # type: ignore[call-arg]


def test_event_disposition_and_failure_code_constraints() -> None:
    with pytest.raises(ValidationError):
        EventEmitted(disposition="unknown", event_id=EVENT_ID)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        EventDeliveryFailed(event_id=EVENT_ID, failure_code="not lower snake")
