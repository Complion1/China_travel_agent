"""Contract tests for workflow clock and UUID providers."""

from datetime import datetime, timezone, timedelta
from inspect import signature
from typing import get_type_hints
from uuid import UUID

import pytest

from app.workflow_contracts.providers import (
    MonotonicClock,
    RandomUUIDFactory,
    SystemMonotonicClock,
    SystemUtcClock,
    UUIDFactory,
    UtcClock,
)
from tests.unit.workflow_contracts.fakes import (
    FixedUtcClock,
    SequenceUUIDFactory,
    StepMonotonicClock,
)


def test_provider_protocol_surfaces_are_sync_and_exact() -> None:
    assert get_type_hints(UtcClock.now)["return"] is datetime
    assert get_type_hints(MonotonicClock.now_ns)["return"] is int
    assert get_type_hints(UUIDFactory.new)["return"] is UUID
    assert not hasattr(UtcClock.now, "__await__")
    assert not hasattr(MonotonicClock.now_ns, "__await__")
    assert not hasattr(UUIDFactory.new, "__await__")

    assert list(signature(UtcClock.now).parameters) == ["self"]
    assert list(signature(MonotonicClock.now_ns).parameters) == ["self"]
    assert list(signature(UUIDFactory.new).parameters) == ["self"]


def test_system_utc_clock_is_utc_and_protocol_compatible() -> None:
    provider = SystemUtcClock()
    observed = provider.now()

    assert isinstance(observed, datetime)
    assert observed.tzinfo is timezone.utc
    assert observed.utcoffset() == timedelta(0)


def test_system_monotonic_clock_is_non_decreasing_and_protocol_compatible() -> None:
    provider = SystemMonotonicClock()
    first = provider.now_ns()
    second = provider.now_ns()

    assert isinstance(first, int)
    assert isinstance(second, int)
    assert second >= first


def test_random_uuid_factory_returns_v4_uuids_and_protocol_compatible() -> None:
    provider = RandomUUIDFactory()
    first = provider.new()
    second = provider.new()

    assert isinstance(first, UUID)
    assert isinstance(second, UUID)
    assert first.version == 4
    assert second.version == 4


def test_fixed_utc_clock_rejects_naive_and_non_utc_values() -> None:
    fixed = datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)
    provider = FixedUtcClock(fixed)

    assert provider.now() == fixed
    assert provider.now() is fixed

    with pytest.raises(ValueError):
        FixedUtcClock(datetime(2026, 8, 11, 9, 30))
    with pytest.raises(ValueError):
        FixedUtcClock(
            datetime(2026, 8, 11, 17, 30, tzinfo=timezone(timedelta(hours=8)))
        )


def test_step_monotonic_clock_returns_tuple_order_and_fails_closed() -> None:
    provider = StepMonotonicClock((10, 20))

    assert provider.now_ns() == 10
    assert provider.now_ns() == 20
    with pytest.raises(AssertionError):
        provider.now_ns()
    with pytest.raises(AssertionError):
        provider.now_ns()


def test_sequence_uuid_factory_returns_tuple_order_and_fails_closed() -> None:
    first = UUID("00000000-0000-0000-0000-000000000001")
    second = UUID("00000000-0000-0000-0000-000000000002")
    provider = SequenceUUIDFactory((first, second))

    assert provider.new() == first
    assert provider.new() == second
    with pytest.raises(AssertionError):
        provider.new()
    with pytest.raises(AssertionError):
        provider.new()
