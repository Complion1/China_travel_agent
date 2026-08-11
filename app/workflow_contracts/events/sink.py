"""Safe workflow event sink protocol and the stateless demo sink."""

from typing import Protocol

from app.workflow_contracts.events.models import (
    EventEmitted,
    EventEmissionOutcome,
    SafeWorkflowEvent,
)


class SafeWorkflowEventSink(Protocol):
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome: ...


class NoOpSafeWorkflowEventSink:
    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome:
        return EventEmitted(disposition="applied", event_id=event.event_id)
