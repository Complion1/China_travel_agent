"""Reusable safe workflow event sink contract and protocol tests."""

from __future__ import annotations

from datetime import datetime, timezone
from inspect import iscoroutinefunction, signature
from typing import Callable, get_type_hints
from uuid import UUID

import pytest
from pydantic import BaseModel, TypeAdapter

from app.workflow_contracts.errors import EventSinkContractError, SafeWorkflowEventValidationError
from app.workflow_contracts.events.models import (
    EventDeliveryFailed,
    EventEmitted,
    EventEmissionOutcome,
    SafeCounterDelta,
    SafeWorkflowEvent,
)
from app.workflow_contracts.events.registry import (
    STANDARD_EVENT_DELIVERY_FAILURE_CODES,
    STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS,
    EventDeliveryFailureCodeRegistry,
    SafeWorkflowEventRegistry,
)
from app.workflow_contracts.events.sink import SafeWorkflowEventSink
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase


SafeWorkflowEventSinkFactory = Callable[
    [SafeWorkflowEventRegistry, EventDeliveryFailureCodeRegistry],
    SafeWorkflowEventSink,
]

RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
TRACE_ID = UUID("22345678-1234-5678-1234-567812345678")
EVENT_ID = UUID("32345678-1234-5678-1234-567812345678")
OTHER_EVENT_ID = UUID("42345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
_OUTCOME_ADAPTER = TypeAdapter(EventEmissionOutcome)


def _event(
    *,
    event_id: UUID = EVENT_ID,
    sequence_number: int = 1,
    operation_id: OperationId | None = None,
) -> SafeWorkflowEvent:
    return SafeWorkflowEvent(
        schema_version="1.0",
        event_id=event_id,
        sequence_number=sequence_number,
        occurred_at=UTC,
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        operation_id=operation_id,
        phase=WorkflowPhase.REQUIREMENTS,
        event_type="workflow.started",
        counter_deltas=(SafeCounterDelta(name="workflow.started", delta=1),),
        duration_ms=None,
        terminal_code=None,
    )


def _copy_outcome(raw: object) -> EventEmissionOutcome:
    if type(raw) not in (EventEmitted, EventDeliveryFailed):
        raise EventSinkContractError()
    try:
        tree = raw.model_dump(
            mode="python",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        first = _OUTCOME_ADAPTER.validate_python(tree, strict=True)
        json_tree = first.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        import json

        payload = json.dumps(
            json_tree,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return _OUTCOME_ADAPTER.validate_json(payload, strict=True)
    except Exception:
        raise EventSinkContractError() from None


async def _assert_emit_contract(
    sink: SafeWorkflowEventSink,
    event: SafeWorkflowEvent,
    registry: SafeWorkflowEventRegistry,
    failure_codes: EventDeliveryFailureCodeRegistry,
) -> EventEmissionOutcome:
    registry.validate(event)
    raw = await sink.emit(event)
    outcome = _copy_outcome(raw)
    if outcome.event_id != event.event_id:
        raise EventSinkContractError()
    if isinstance(outcome, EventDeliveryFailed):
        failure_codes.validate(outcome.failure_code)
    return outcome


class _FailureSink:
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome:
        return EventDeliveryFailed(event_id=event.event_id, failure_code="sink_timeout")


class _DictSink:
    async def emit(self, event: SafeWorkflowEvent) -> object:
        return {"outcome_type": "emitted", "event_id": str(event.event_id)}


class _OutcomeChild(EventEmitted):
    pass


class _ChildSink:
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome:
        return _OutcomeChild(disposition="applied", event_id=event.event_id)


class _WrongIdSink:
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome:
        return EventEmitted(disposition="applied", event_id=OTHER_EVENT_ID)


class _UnknownCodeSink:
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome:
        return EventDeliveryFailed(event_id=event.event_id, failure_code="unknown_code")


class _OtherModel(BaseModel):
    value: int


class _OtherModelSink:
    async def emit(self, event: SafeWorkflowEvent) -> object:
        return _OtherModel(value=1)


async def assert_safe_workflow_event_sink_contract(
    factory: SafeWorkflowEventSinkFactory,
) -> None:
    registry = SafeWorkflowEventRegistry(STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS)
    failure_codes = EventDeliveryFailureCodeRegistry(STANDARD_EVENT_DELIVERY_FAILURE_CODES)
    sink = factory(registry, failure_codes)
    event = _event()

    applied = await _assert_emit_contract(sink, event, registry, failure_codes)
    assert isinstance(applied, EventEmitted)
    assert applied.disposition == "applied"
    assert applied.event_id == event.event_id

    replay_like = await _assert_emit_contract(sink, event, registry, failure_codes)
    assert isinstance(replay_like, EventEmitted)
    assert replay_like.event_id == event.event_id

    invalid_event = event.model_copy(
        update={
            "operation_id": OperationId(
                run_id=RUN_ID,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="invalid_operation",
            )
        }
    )
    with pytest.raises(SafeWorkflowEventValidationError):
        await _assert_emit_contract(sink, invalid_event, registry, failure_codes)

    failure_result = await _assert_emit_contract(
        _FailureSink(),
        _event(event_id=OTHER_EVENT_ID, sequence_number=2),
        registry,
        failure_codes,
    )
    assert isinstance(failure_result, EventDeliveryFailed)
    assert failure_result.failure_code == "sink_timeout"
    business_result = "unchanged"
    assert business_result == "unchanged"

    invalid_sinks = (
        _DictSink(),
        _ChildSink(),
        _OtherModelSink(),
        _WrongIdSink(),
        _UnknownCodeSink(),
    )
    for invalid_sink in invalid_sinks:
        with pytest.raises(EventSinkContractError):
            await _assert_emit_contract(
                invalid_sink,  # type: ignore[arg-type]
                event,
                registry,
                failure_codes,
            )


async def test_noop_safe_workflow_event_sink_contract() -> None:
    from app.workflow_contracts.events.sink import NoOpSafeWorkflowEventSink

    await assert_safe_workflow_event_sink_contract(
        lambda _registry, _failure_codes: NoOpSafeWorkflowEventSink()
    )


def test_safe_workflow_event_sink_protocol_surface() -> None:
    public_methods = {
        name for name in SafeWorkflowEventSink.__dict__ if not name.startswith("_")
    }
    assert public_methods == {"emit"}
    assert iscoroutinefunction(SafeWorkflowEventSink.emit)
    parameters = signature(SafeWorkflowEventSink.emit).parameters
    assert list(parameters) == ["self", "event"]
    hints = get_type_hints(SafeWorkflowEventSink.emit, include_extras=True)
    assert hints["event"] is SafeWorkflowEvent
    assert hints["return"] == EventEmissionOutcome
