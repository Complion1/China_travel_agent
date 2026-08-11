"""Synchronous, side-effect-free system providers."""

import time
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4


@runtime_checkable
class UtcClock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class MonotonicClock(Protocol):
    def now_ns(self) -> int: ...


@runtime_checkable
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
