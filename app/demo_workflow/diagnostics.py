"""Locally complete counters, asynchronously delivered at the terminal boundary."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.requirement_extraction import ExtractionInvocationEvent
from app.workflow_contracts import (
    RunSummary, SafeCounterValue, SafeCounterDelta, SafeWorkflowEvent,
    SafeWorkflowEventRegistration, SafeWorkflowEventRegistry,
    STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS, FieldRequirement, WorkflowPhase,
    EventEmitted, EventDeliveryFailed, EventDeliveryFailureCodeRegistry,
    STANDARD_EVENT_DELIVERY_FAILURE_CODES, DeliveryStatus,
)
from .errors import DemoWorkflowUsageError
from .models import fresh
from .terminal import REGISTRY


_NAMES = tuple(sorted(("workflow.started", "workflow.resumed", "workflow.replayed", "workflow.terminal",
    "artifact.cache_hit", "extraction.extract", "extraction.repair", "extraction.failed")))
_EVENTS = SafeWorkflowEventRegistry(STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS + tuple(
    SafeWorkflowEventRegistration(event_type=name, counter_names=(name,), operation_id=FieldRequirement.FORBIDDEN,
        duration_ms=FieldRequirement.FORBIDDEN, terminal_code=FieldRequirement.FORBIDDEN)
    for name in ("extraction.extract", "extraction.repair", "extraction.failed")
))
_FAILURES = EventDeliveryFailureCodeRegistry(STANDARD_EVENT_DELIVERY_FAILURE_CODES)


class DemoSummaryView(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["1.0"]
    summary: RunSummary | None
    unavailable_reason: Literal["process_history_unavailable"] | None

    @model_validator(mode="after")
    def validate_view(self):
        if (self.summary is None) == (self.unavailable_reason is None):
            raise ValueError("summary or unavailability required")
        if self.summary is not None:
            object.__setattr__(self, "summary", fresh(self.summary, RunSummary))
        return self


@dataclass
class _History:
    trace_id: UUID
    start_ns: int
    events: list = field(default_factory=list)
    counters: dict = field(default_factory=lambda: dict.fromkeys(_NAMES, 0))
    delivered: int = 0
    failures: int = 0
    terminal_state: object = None
    summary: object = None


class DemoDiagnostics:
    def __init__(self, *, sink, clock, monotonic_clock, uuid_provider):
        self._sink, self._clock, self._monotonic, self._uuid = sink, clock, monotonic_clock, uuid_provider
        self._histories = {}
        self._operation = ContextVar("demo_operation", default=None)

    def start_run(self, *, run_id, trace_id):
        if type(run_id) is not UUID or type(trace_id) is not UUID or run_id in self._histories:
            raise DemoWorkflowUsageError()
        self._histories[run_id] = _History(trace_id, self._monotonic.now_ns())
        self._emit(run_id, "workflow.started", WorkflowPhase.REQUIREMENTS)

    @contextmanager
    def operation(self, *, run_id, trace_id, turn_id, kind):
        if type(run_id) is not UUID or type(trace_id) is not UUID or (turn_id is not None and type(turn_id) is not UUID) or kind not in {"initial", "resume", "recover"}:
            raise DemoWorkflowUsageError()
        history = self._histories.get(run_id)
        if history is not None and history.trace_id != trace_id:
            raise DemoWorkflowUsageError()
        token = self._operation.set((run_id, trace_id, turn_id, kind, set()))
        try:
            yield
        finally:
            self._operation.reset(token)

    def accept(self, event):
        event = fresh(event, ExtractionInvocationEvent)
        context = self._operation.get()
        if context is None or event.turn_id != context[2]:
            raise DemoWorkflowUsageError()
        self._emit(context[0], "extraction." + event.attempt_kind, WorkflowPhase.REQUIREMENTS)
        if event.outcome_kind == "runtime_failure":
            self._emit(context[0], "extraction.failed", WorkflowPhase.REQUIREMENTS)

    def _resume_accepted(self, state):
        context = self._operation.get()
        if context is not None and context[0] == state.run_id and context[3] == "resume" and "accepted" not in context[4]:
            self._emit(state.run_id, "workflow.resumed", state.phase)
            context[4].add("accepted")

    def _emit(self, run_id, event_type, phase, *, operation_id=None, duration_ms=None, terminal_code=None):
        history = self._histories.get(run_id)
        if history is None:
            return  # A restarted run will explicitly have no complete summary.
        if history.summary is not None or len(history.events) >= 16384:
            raise DemoWorkflowUsageError()
        name = "artifact.cache_hit" if event_type == "artifact.reused" else event_type
        event = SafeWorkflowEvent(schema_version="1.0", event_id=self._uuid.new(),
            sequence_number=len(history.events) + 1, occurred_at=self._clock.now(), run_id=run_id,
            trace_id=history.trace_id, operation_id=operation_id, phase=phase, event_type=event_type,
            counter_deltas=(SafeCounterDelta(name=name, delta=1),), duration_ms=duration_ms, terminal_code=terminal_code)
        _EVENTS.validate(event)
        if any(old.event_id == event.event_id for old in history.events):
            raise DemoWorkflowUsageError()
        history.events.append(event)
        history.counters[name] += 1

    async def finish(self, *, state, budget_snapshots):
        REGISTRY.validate_state(state)
        if state.delivery_status in {DeliveryStatus.IN_PROGRESS, DeliveryStatus.AWAITING_USER}:
            raise DemoWorkflowUsageError()
        history = self._histories.get(state.run_id)
        if history is None:
            return DemoSummaryView(schema_version="1.0", summary=None, unavailable_reason="process_history_unavailable")
        if history.trace_id != state.trace_id or (history.terminal_state is not None and history.terminal_state != state):
            raise DemoWorkflowUsageError()
        if history.summary is not None:
            if history.summary.summary.budget_snapshots != budget_snapshots:
                raise DemoWorkflowUsageError()
            return fresh(history.summary, DemoSummaryView)
        if history.terminal_state is None:
            duration = (self._monotonic.now_ns() - history.start_ns) // 1_000_000
            self._emit(state.run_id, "workflow.terminal", state.phase, duration_ms=duration, terminal_code=state.terminal_code)
            history.terminal_state = state.model_copy(deep=True)
        while history.delivered < len(history.events):
            event = history.events[history.delivered]
            result = await self._sink.emit(event)
            if type(result) not in {EventEmitted, EventDeliveryFailed}:
                raise DemoWorkflowUsageError()
            result = fresh(result, type(result))
            if result.event_id != event.event_id:
                raise DemoWorkflowUsageError()
            if type(result) is EventDeliveryFailed:
                _FAILURES.validate(result.failure_code)
                history.failures += 1
            history.delivered += 1
        summary = RunSummary(schema_version="1.0", run_id=state.run_id, trace_id=state.trace_id,
            terminal_code=state.terminal_code, duration_ms=history.events[-1].duration_ms,
            cache_hits=history.counters["artifact.cache_hit"], event_delivery_failures=history.failures,
            counters=tuple(SafeCounterValue(name=name, value=history.counters[name]) for name in _NAMES),
            budget_snapshots=budget_snapshots)
        history.summary = DemoSummaryView(schema_version="1.0", summary=summary, unavailable_reason=None)
        return fresh(history.summary, DemoSummaryView)
