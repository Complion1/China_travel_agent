"""Test-only deterministic providers for workflow contract tests."""

from datetime import datetime, timedelta
from uuid import UUID


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
