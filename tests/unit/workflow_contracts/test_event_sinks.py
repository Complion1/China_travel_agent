"""NoOp and Recording safe workflow event sink tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from inspect import iscoroutinefunction
from uuid import UUID

import pytest

from app.workflow_contracts.errors import (
    EventIdentityConflictError,
    EventSequenceConflictError,
    SafeWorkflowEventValidationError,
)
from app.workflow_contracts.events.models import (
    EventDeliveryFailed,
    EventEmitted,
    SafeCounterDelta,
    SafeWorkflowEvent,
)
from app.workflow_contracts.events.registry import (
    STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS,
    SafeWorkflowEventRegistry,
)
from app.workflow_contracts.events.sink import NoOpSafeWorkflowEventSink
from app.workflow_contracts.identifiers import WorkflowPhase
from tests.unit.workflow_contracts.fakes import RecordingSafeWorkflowEventSink


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
OTHER_RUN_ID = UUID("22345678-1234-5678-1234-567812345678")
TRACE_ID = UUID("32345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _event(
    *,
    event_id: UUID,
    sequence_number: int,
    run_id: UUID = RUN_ID,
    delta: int = 1,
) -> SafeWorkflowEvent:
    return SafeWorkflowEvent(
        schema_version="1.0",
        event_id=event_id,
        sequence_number=sequence_number,
        occurred_at=UTC,
        run_id=run_id,
        trace_id=TRACE_ID,
        operation_id=None,
        phase=WorkflowPhase.REQUIREMENTS,
        event_type="workflow.started",
        counter_deltas=(SafeCounterDelta(name="workflow.started", delta=delta),),
        duration_ms=None,
        terminal_code=None,
    )


def _registry() -> SafeWorkflowEventRegistry:
    return SafeWorkflowEventRegistry(STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS)


def test_noop_sink_is_stateless_and_always_applied() -> None:
    sink = NoOpSafeWorkflowEventSink()
    first_event = _event(
        event_id=UUID("42345678-1234-5678-1234-567812345678"),
        sequence_number=1,
    )

    async def exercise() -> None:
        first = await sink.emit(first_event)
        second = await sink.emit(first_event)
        assert isinstance(first, EventEmitted)
        assert isinstance(second, EventEmitted)
        assert first.disposition == "applied"
        assert second.disposition == "applied"
        assert first.event_id == first_event.event_id
        assert second.event_id == first_event.event_id

    asyncio.run(exercise())
    assert set(name for name in NoOpSafeWorkflowEventSink.__dict__ if not name.startswith("_")) == {"emit"}
    assert iscoroutinefunction(NoOpSafeWorkflowEventSink.emit)


@pytest.mark.asyncio
async def test_recording_sink_replay_identity_sequence_capacity_and_fresh_events() -> None:
    sink = RecordingSafeWorkflowEventSink(registry=_registry(), max_events=1)
    first_event = _event(
        event_id=UUID("52345678-1234-5678-1234-567812345678"),
        sequence_number=1,
    )
    first = await sink.emit(first_event)
    assert isinstance(first, EventEmitted)
    assert first.disposition == "applied"

    replay = await sink.emit(first_event)
    assert isinstance(replay, EventEmitted)
    assert replay.disposition == "replayed"

    identity_conflict = first_event.model_copy(
        update={"sequence_number": 2}
    )
    with pytest.raises(EventIdentityConflictError):
        await sink.emit(identity_conflict)

    sequence_conflict = _event(
        event_id=UUID("62345678-1234-5678-1234-567812345678"),
        sequence_number=1,
    )
    with pytest.raises(EventSequenceConflictError):
        await sink.emit(sequence_conflict)

    full = await sink.emit(
        _event(
            event_id=UUID("72345678-1234-5678-1234-567812345678"),
            sequence_number=2,
        )
    )
    assert isinstance(full, EventDeliveryFailed)
    assert full.failure_code == "sink_capacity_exceeded"
    assert len(sink.events) == 1

    first_view = sink.events
    second_view = sink.events
    assert first_view is not second_view
    assert first_view[0] is not second_view[0]
    assert first_view[0] == first_event


@pytest.mark.asyncio
async def test_recording_sink_replay_precedes_capacity_and_zero_capacity() -> None:
    full_sink = RecordingSafeWorkflowEventSink(registry=_registry(), max_events=0)
    event = _event(
        event_id=UUID("82345678-1234-5678-1234-567812345678"),
        sequence_number=1,
    )
    result = await full_sink.emit(event)
    assert isinstance(result, EventDeliveryFailed)
    assert result.failure_code == "sink_capacity_exceeded"
    assert full_sink.events == ()

    with pytest.raises(ValueError):
        RecordingSafeWorkflowEventSink(registry=_registry(), max_events=-1)
    with pytest.raises(ValueError):
        RecordingSafeWorkflowEventSink(registry=_registry(), max_events=2**53)


@pytest.mark.asyncio
async def test_recording_sink_validates_before_capacity_and_linearizes_concurrency() -> None:
    sink = RecordingSafeWorkflowEventSink(registry=_registry(), max_events=1)
    invalid = _event(
        event_id=UUID("92345678-1234-5678-1234-567812345678"),
        sequence_number=1,
        delta=2,
    )
    with pytest.raises(SafeWorkflowEventValidationError):
        await sink.emit(invalid)
    assert sink.events == ()

    barrier = asyncio.Barrier(2)

    async def emit(sequence_number: int, event_id: UUID) -> object:
        await barrier.wait()
        return await sink.emit(
            _event(event_id=event_id, sequence_number=sequence_number)
        )

    first, second = await asyncio.gather(
        emit(1, UUID("a2345678-1234-5678-1234-567812345678")),
        emit(2, UUID("b2345678-1234-5678-1234-567812345678")),
    )
    assert sum(isinstance(result, EventEmitted) for result in (first, second)) == 1
    assert sum(isinstance(result, EventDeliveryFailed) for result in (first, second)) == 1
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_recording_events_sort_by_run_string_then_sequence() -> None:
    sink = RecordingSafeWorkflowEventSink(registry=_registry(), max_events=4)
    events = (
        _event(
            event_id=UUID("c2345678-1234-5678-1234-567812345678"),
            sequence_number=2,
            run_id=OTHER_RUN_ID,
        ),
        _event(
            event_id=UUID("d2345678-1234-5678-1234-567812345678"),
            sequence_number=1,
            run_id=OTHER_RUN_ID,
        ),
        _event(
            event_id=UUID("e2345678-1234-5678-1234-567812345678"),
            sequence_number=1,
            run_id=RUN_ID,
        ),
    )
    for event in events:
        await sink.emit(event)
    assert [(str(event.run_id), event.sequence_number) for event in sink.events] == [
        (str(RUN_ID), 1),
        (str(OTHER_RUN_ID), 1),
        (str(OTHER_RUN_ID), 2),
    ]
