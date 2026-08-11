"""Test-only deterministic providers for workflow contract tests."""

import asyncio
import json
from datetime import date, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, TypeAdapter

from app.workflow_contracts.errors import (
    EventIdentityConflictError,
    EventSequenceConflictError,
    EventSinkContractError,
)
from app.workflow_contracts.events.models import (
    EventDeliveryFailed,
    EventEmitted,
    EventEmissionOutcome,
    SafeWorkflowEvent,
)
from app.workflow_contracts.events.registry import SafeWorkflowEventRegistry


class FixedUtcClock:
    def __init__(self, fixed: datetime) -> None:
        if fixed.tzinfo is None or fixed.utcoffset() != timedelta(0):
            raise ValueError("fixed datetime must be UTC")
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed


class StepMonotonicClock:
    def __init__(self, values_ns: tuple[int, ...]) -> None:
        self._values_ns = values_ns
        self._index = 0

    def now_ns(self) -> int:
        if self._index >= len(self._values_ns):
            raise AssertionError("StepMonotonicClock values exhausted")
        value = self._values_ns[self._index]
        self._index += 1
        return value


class SequenceUUIDFactory:
    def __init__(self, values: tuple[UUID, ...]) -> None:
        self._values = values
        self._index = 0

    def new(self) -> UUID:
        if self._index >= len(self._values):
            raise AssertionError("SequenceUUIDFactory values exhausted")
        value = self._values[self._index]
        self._index += 1
        return value


class StrictSampleArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    label: str
    count: int
    ratio: float
    occurred_on: date
    observed_at: datetime
    note: str | None


_EVENT_ADAPTER = TypeAdapter(SafeWorkflowEvent)


def _copy_event(event: SafeWorkflowEvent) -> SafeWorkflowEvent:
    try:
        tree = event.model_dump(
            mode="python",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        first = _EVENT_ADAPTER.validate_python(tree, strict=True)
        json_tree = first.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        payload = json.dumps(
            json_tree,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return _EVENT_ADAPTER.validate_json(payload, strict=True)
    except Exception:
        raise EventSinkContractError() from None


class RecordingSafeWorkflowEventSink:
    __slots__ = (
        "_registry",
        "_max_events",
        "_events_by_id",
        "_events_by_sequence",
        "_lock",
    )

    def __init__(
        self,
        *,
        registry: SafeWorkflowEventRegistry,
        max_events: int,
    ) -> None:
        if type(max_events) is not int or not 0 <= max_events <= 2**53 - 1:
            raise ValueError("max_events must be a safe non-negative integer")
        self._registry = registry
        self._max_events = max_events
        self._events_by_id: dict[UUID, SafeWorkflowEvent] = {}
        self._events_by_sequence: dict[tuple[UUID, int], UUID] = {}
        self._lock = asyncio.Lock()

    async def emit(self, event: SafeWorkflowEvent) -> EventEmissionOutcome:
        self._registry.validate(event)
        fresh_event = _copy_event(event)
        async with self._lock:
            existing = self._events_by_id.get(fresh_event.event_id)
            if existing is not None:
                if existing != fresh_event:
                    raise EventIdentityConflictError()
                return EventEmitted(
                    disposition="replayed",
                    event_id=fresh_event.event_id,
                )

            sequence_key = (fresh_event.run_id, fresh_event.sequence_number)
            existing_event_id = self._events_by_sequence.get(sequence_key)
            if existing_event_id is not None:
                raise EventSequenceConflictError()

            if len(self._events_by_id) >= self._max_events:
                return EventDeliveryFailed(
                    event_id=fresh_event.event_id,
                    failure_code="sink_capacity_exceeded",
                )

            self._events_by_id[fresh_event.event_id] = fresh_event
            self._events_by_sequence[sequence_key] = fresh_event.event_id
            return EventEmitted(
                disposition="applied",
                event_id=fresh_event.event_id,
            )

    @property
    def events(self) -> tuple[SafeWorkflowEvent, ...]:
        ordered = sorted(
            self._events_by_id.values(),
            key=lambda event: (str(event.run_id), event.sequence_number),
        )
        return tuple(_copy_event(event) for event in ordered)
