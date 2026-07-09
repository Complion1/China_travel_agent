from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class TraceEvent:
    phase: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ts": self.ts,
            "phase": self.phase,
            "message": self.message,
            "metadata": self.metadata,
        }


class TraceRecorder:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.events: list[TraceEvent] = []

    def add(self, phase: str, message: str, **metadata: Any) -> TraceEvent:
        event = TraceEvent(phase=phase, message=message, metadata=metadata)
        self.events.append(event)
        return event

    def thought(self, message: str, **metadata: Any) -> TraceEvent:
        return self.add("Thought", message, **metadata)

    def action(self, message: str, **metadata: Any) -> TraceEvent:
        return self.add("Action", message, **metadata)

    def observation(self, message: str, **metadata: Any) -> TraceEvent:
        return self.add("Observation", message, **metadata)

    def final(self, message: str, **metadata: Any) -> TraceEvent:
        return self.add("Final", message, **metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "events": [event.to_dict() for event in self.events],
        }
