"""Synchronous, side-effect-free system providers."""

import time
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4


class UtcClock(Protocol):
    def now(self) -> datetime: ...


class MonotonicClock(Protocol):
    def now_ns(self) -> int: ...


class UUIDFactory(Protocol):
    def new(self) -> UUID: ...


class SystemUtcClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SystemMonotonicClock:
    def now_ns(self) -> int:
        return time.monotonic_ns()


class RandomUUIDFactory:
    def new(self) -> UUID:
        return uuid4()
