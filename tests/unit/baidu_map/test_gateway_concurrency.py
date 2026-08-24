from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import gc

import pytest
from pydantic import SecretStr

from app.baidu_map import (
    BaiduMapLimits,
    BaiduMapResponseError,
    BaiduMapSessionError,
    BaiduMapSettings,
    BaiduMapTimeoutError,
    BaiduCoordinate,
    GeocodeRequest,
    WeatherRequest,
)
from app.baidu_map._mcp_gateway import BaiduMapMcpGateway
from tests.support.baidu_map_fake_mcp import FakeMcpEnvironment, ScriptedMcpSession, structured_result


class Clock:
    def now(self):
        return datetime(2026, 8, 20, tzinfo=timezone.utc)


async def _gateway(
    environment: FakeMcpEnvironment,
    *,
    max_in_flight: int = 1,
    timeout: float = 1.0,
    close_timeout: float = 5.0,
):
    limits = BaiduMapLimits(
        geocode_timeout_s=timeout,
        close_timeout_s=close_timeout,
        max_in_flight=max_in_flight,
    )
    return await BaiduMapMcpGateway.create(
        settings=BaiduMapSettings(api_key=SecretStr("offline")),
        clock=Clock(), limits=limits, _client_factory=environment.factory,
    )


def _success():
    return structured_result({"status": 0, "result": {"location": {"lat": 30.2, "lng": 120.1}}})


def _observe_second_admission(gateway: BaiduMapMcpGateway) -> asyncio.Event:
    admitted = asyncio.Event()
    original_admit = gateway._admit

    async def observed_admit():
        task = await original_admit()
        if task is not None and len(gateway._in_flight) == 2:
            admitted.set()
        return task

    gateway._admit = observed_admit  # type: ignore[method-assign]
    return admitted


@pytest.mark.asyncio
async def test_shared_semaphore_caps_cross_capability_calls_at_three_with_events() -> None:
    environment = FakeMcpEnvironment()
    entered = asyncio.Event()
    release = asyncio.Event()
    entered_count = 0

    async def geocode_handler(_):
        nonlocal entered_count
        entered_count += 1
        if entered_count == 3:
            entered.set()
        await release.wait()
        return _success()

    async def weather_handler(_):
        nonlocal entered_count
        entered_count += 1
        if entered_count == 3:
            entered.set()
        await release.wait()
        return structured_result(
            {"status": 0, "result": {"now": {"text": "晴", "temp": 28}, "forecasts": []}}
        )

    environment.set_handler("map_geocode", geocode_handler)
    environment.set_handler("map_weather", weather_handler)
    gateway = await _gateway(environment, max_in_flight=3)
    point = BaiduCoordinate(latitude=30.2, longitude=120.1)
    tasks = []
    for index in range(3):
        tasks.append(asyncio.create_task(gateway.geocode(GeocodeRequest(address=str(index)))))
        tasks.append(asyncio.create_task(gateway.get_weather(WeatherRequest(location=point))))
    await entered.wait()
    assert environment.max_active_calls == 3
    assert environment.call_count == 3
    release.set()
    await asyncio.gather(*tasks)
    assert environment.max_active_calls == 3
    await gateway.aclose()


@pytest.mark.asyncio
async def test_semaphore_wait_timeout_does_not_break_session() -> None:
    environment = FakeMcpEnvironment()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_):
        entered.set()
        await release.wait()
        return structured_result(
            {"status": 0, "result": {"now": {"text": "晴", "temp": 28}, "forecasts": []}}
        )

    environment.set_handler("map_weather", handler)
    environment.set_result("map_geocode", _success())
    gateway = await _gateway(environment, timeout=0.05)
    first = asyncio.create_task(
        gateway.get_weather(
            WeatherRequest(location=BaiduCoordinate(latitude=30.2, longitude=120.1))
        )
    )
    await entered.wait()
    with pytest.raises(BaiduMapTimeoutError):
        await gateway.geocode(GeocodeRequest(address="waiter"))
    assert environment.call_count == 1
    release.set()
    await first
    await gateway.geocode(GeocodeRequest(address="still-ready"))
    assert environment.call_count == 2
    await gateway.aclose()


@pytest.mark.asyncio
async def test_broken_call_wakes_unsent_waiter_to_zero_network_session_error() -> None:
    environment = FakeMcpEnvironment()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def bad_handler(_):
        entered.set()
        await release.wait()
        return structured_result({"status": 1})

    environment.set_handler("map_geocode", bad_handler)
    gateway = await _gateway(environment)
    first = asyncio.create_task(gateway.geocode(GeocodeRequest(address="bad")))
    await entered.wait()
    waiter_admitted = asyncio.Event()
    original_admit = gateway._admit

    async def observed_admit():
        task = await original_admit()
        if task is not None and len(gateway._in_flight) == 2:
            waiter_admitted.set()
        return task

    gateway._admit = observed_admit  # type: ignore[method-assign]
    waiter = asyncio.create_task(gateway.geocode(GeocodeRequest(address="waiting")))
    await waiter_admitted.wait()
    release.set()
    with pytest.raises(BaiduMapResponseError):
        await first
    with pytest.raises(BaiduMapSessionError):
        await waiter
    assert environment.call_count == 1
    await gateway.aclose()


@pytest.mark.asyncio
async def test_terminal_failure_during_closing_blocks_admitted_unsent_waiter() -> None:
    environment = FakeMcpEnvironment()
    first_entered = asyncio.Event()
    release = asyncio.Event()

    async def bad_handler(_):
        first_entered.set()
        await release.wait()
        return structured_result({"status": 1})

    environment.set_handler("map_geocode", bad_handler)
    gateway = await _gateway(environment)
    first = asyncio.create_task(gateway.geocode(GeocodeRequest(address="bad")))
    await first_entered.wait()

    waiter_admitted = asyncio.Event()
    original_admit = gateway._admit

    async def observed_admit():
        task = await original_admit()
        if task is not None and len(gateway._in_flight) == 2:
            waiter_admitted.set()
        return task

    gateway._admit = observed_admit  # type: ignore[method-assign]
    waiter = asyncio.create_task(gateway.geocode(GeocodeRequest(address="waiting")))
    await waiter_admitted.wait()
    closing = asyncio.create_task(gateway.aclose())
    await gateway._closing_started.wait()

    release.set()
    with pytest.raises(BaiduMapResponseError):
        await first
    with pytest.raises(BaiduMapSessionError):
        await waiter
    await closing

    assert environment.call_count == 1
    assert gateway._broken is True
    assert gateway._state == "closed"
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 1


@pytest.mark.asyncio
async def test_close_allows_already_admitted_waiter_but_rejects_new_call() -> None:
    environment = FakeMcpEnvironment()
    first_entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def handler(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            await release.wait()
        return _success()

    environment.set_handler("map_geocode", handler)
    gateway = await _gateway(environment)
    first = asyncio.create_task(gateway.geocode(GeocodeRequest(address="first")))
    await first_entered.wait()
    waiter_admitted = asyncio.Event()
    original_admit = gateway._admit

    async def observed_admit():
        task = await original_admit()
        if task is not None and len(gateway._in_flight) == 2:
            waiter_admitted.set()
        return task

    gateway._admit = observed_admit  # type: ignore[method-assign]
    admitted_waiter = asyncio.create_task(gateway.geocode(GeocodeRequest(address="admitted")))
    await waiter_admitted.wait()
    closing = asyncio.create_task(gateway.aclose())
    await gateway._closing_started.wait()
    with pytest.raises(BaiduMapSessionError):
        await gateway.geocode(GeocodeRequest(address="new"))
    release.set()
    await asyncio.gather(first, admitted_waiter, closing)
    assert environment.call_count == 2


@pytest.mark.asyncio
async def test_cancelling_one_close_waiter_does_not_cancel_unique_close_task() -> None:
    environment = FakeMcpEnvironment()
    environment.close_release = asyncio.Event()
    gateway = await _gateway(environment)
    one = asyncio.create_task(gateway.aclose())
    two = asyncio.create_task(gateway.aclose())
    await environment.close_entered.wait()
    one.cancel()
    with pytest.raises(asyncio.CancelledError):
        await one
    assert not two.done()
    environment.close_release.set()
    await two
    assert environment.exit_calls == 1


@pytest.mark.asyncio
async def test_cancelled_only_close_waiter_leaves_no_unretrieved_close_failure() -> None:
    environment = FakeMcpEnvironment()
    gateway = await _gateway(environment)
    close_entered = asyncio.Event()
    close_release = asyncio.Event()

    async def failing_close_resources() -> None:
        close_entered.set()
        await close_release.wait()
        raise RuntimeError("offline-close-failure")

    gateway._close_resources = failing_close_resources  # type: ignore[method-assign]
    loop = asyncio.get_running_loop()
    prior_handler = loop.get_exception_handler()
    unhandled: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        waiter = asyncio.create_task(gateway.aclose())
        await close_entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        close_release.set()
        assert gateway._close_task is not None
        done, pending = await asyncio.wait({gateway._close_task}, timeout=1.0)
        assert done == {gateway._close_task}
        assert pending == set()
        assert gateway._state == "closed"
        del waiter
        gc.collect()
        await asyncio.sleep(0)
        assert unhandled == []
    finally:
        loop.set_exception_handler(prior_handler)


@pytest.mark.asyncio
async def test_sent_cancellation_atomically_breaks_before_ready_waiter_can_send() -> None:
    environment = FakeMcpEnvironment()
    first_entered = asyncio.Event()
    unexpected_second_send = asyncio.Event()
    provider_cancellations: list[asyncio.CancelledError] = []

    async def handler(arguments):
        if arguments["address"] == "first":
            first_entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                provider_cancellations.append(error)
                raise
        unexpected_second_send.set()
        return _success()

    environment.set_handler("map_geocode", handler)
    gateway = await _gateway(environment)
    first = asyncio.create_task(gateway.geocode(GeocodeRequest(address="first")))
    await first_entered.wait()
    waiter_admitted = _observe_second_admission(gateway)
    waiter = asyncio.create_task(gateway.geocode(GeocodeRequest(address="waiting")))
    await waiter_admitted.wait()

    finish_waiting_for_lock = asyncio.Event()
    original_finish = gateway._finish

    async def observed_finish(task, **kwargs):
        if task is first:
            finish_waiting_for_lock.set()
        return await original_finish(task, **kwargs)

    gateway._finish = observed_finish  # type: ignore[method-assign]
    await gateway._lock.acquire()
    try:
        first.cancel("primary-cancellation")
        await finish_waiting_for_lock.wait()
        assert environment.call_count == 1
        assert not unexpected_second_send.is_set()
        assert gateway._semaphore._value == 0
    finally:
        gateway._lock.release()

    with pytest.raises(asyncio.CancelledError) as cancelled:
        await first
    assert provider_cancellations and cancelled.value is provider_cancellations[0]
    with pytest.raises(BaiduMapSessionError):
        await waiter
    assert environment.call_count == 1
    assert not unexpected_second_send.is_set()
    assert gateway._state == "broken"
    assert gateway._broken is True
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 1
    await gateway.aclose()


@pytest.mark.asyncio
async def test_sent_cancellation_during_closing_blocks_admitted_waiter() -> None:
    environment = FakeMcpEnvironment()
    first_entered = asyncio.Event()
    unexpected_second_send = asyncio.Event()

    async def handler(arguments):
        if arguments["address"] == "first":
            first_entered.set()
            await asyncio.Event().wait()
        unexpected_second_send.set()
        return _success()

    environment.set_handler("map_geocode", handler)
    gateway = await _gateway(environment)
    first = asyncio.create_task(gateway.geocode(GeocodeRequest(address="first")))
    await first_entered.wait()
    waiter_admitted = _observe_second_admission(gateway)
    waiter = asyncio.create_task(gateway.geocode(GeocodeRequest(address="waiting")))
    await waiter_admitted.wait()
    closing = asyncio.create_task(gateway.aclose())
    await gateway._closing_started.wait()
    assert gateway._state == "closing"

    finish_waiting_for_lock = asyncio.Event()
    original_finish = gateway._finish

    async def observed_finish(task, **kwargs):
        if task is first:
            finish_waiting_for_lock.set()
        return await original_finish(task, **kwargs)

    gateway._finish = observed_finish  # type: ignore[method-assign]
    await gateway._lock.acquire()
    try:
        first.cancel()
        await finish_waiting_for_lock.wait()
        assert environment.call_count == 1
        assert not unexpected_second_send.is_set()
        assert gateway._semaphore._value == 0
    finally:
        gateway._lock.release()

    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(BaiduMapSessionError):
        await waiter
    await closing
    assert environment.call_count == 1
    assert not unexpected_second_send.is_set()
    assert gateway._state == "closed"
    assert gateway._broken is True
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 1


@pytest.mark.asyncio
async def test_repeated_sent_cancellation_cannot_interrupt_atomic_cleanup() -> None:
    environment = FakeMcpEnvironment()
    first_entered = asyncio.Event()
    unexpected_second_send = asyncio.Event()
    provider_cancellations: list[asyncio.CancelledError] = []

    async def handler(arguments):
        if arguments["address"] == "first":
            first_entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                provider_cancellations.append(error)
                raise
        unexpected_second_send.set()
        return _success()

    environment.set_handler("map_geocode", handler)
    gateway = await _gateway(environment)
    first = asyncio.create_task(gateway.geocode(GeocodeRequest(address="first")))
    await first_entered.wait()
    waiter_admitted = _observe_second_admission(gateway)
    waiter = asyncio.create_task(gateway.geocode(GeocodeRequest(address="waiting")))
    await waiter_admitted.wait()

    first_finish_attempt = asyncio.Event()
    retry_finish_attempt = asyncio.Event()
    finish_attempts = 0
    original_finish = gateway._finish

    async def observed_finish(task, **kwargs):
        nonlocal finish_attempts
        if task is first:
            finish_attempts += 1
            if finish_attempts == 1:
                first_finish_attempt.set()
            elif finish_attempts == 2:
                retry_finish_attempt.set()
        return await original_finish(task, **kwargs)

    gateway._finish = observed_finish  # type: ignore[method-assign]
    loop = asyncio.get_running_loop()
    prior_handler = loop.get_exception_handler()
    unhandled: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    await gateway._lock.acquire()
    try:
        first.cancel("primary-cancellation")
        await first_finish_attempt.wait()
        first.cancel("secondary-cancellation")
        await retry_finish_attempt.wait()
        assert environment.call_count == 1
        assert not unexpected_second_send.is_set()
        assert gateway._semaphore._value == 0
    finally:
        gateway._lock.release()

    try:
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await first
        assert provider_cancellations and cancelled.value is provider_cancellations[0]
        with pytest.raises(BaiduMapSessionError):
            await waiter
        assert environment.call_count == 1
        assert not unexpected_second_send.is_set()
        assert gateway._state == "broken"
        assert gateway._broken is True
        assert gateway._in_flight == set()
        assert gateway._semaphore._value == 1
        await gateway.aclose()
        assert unhandled == []
    finally:
        loop.set_exception_handler(prior_handler)


@pytest.mark.asyncio
async def test_unsent_semaphore_waiter_cancellation_does_not_break_session() -> None:
    environment = FakeMcpEnvironment()
    first_entered = asyncio.Event()
    first_release = asyncio.Event()

    async def handler(arguments):
        if arguments["address"] == "first":
            first_entered.set()
            await first_release.wait()
        return _success()

    environment.set_handler("map_geocode", handler)
    gateway = await _gateway(environment)
    first = asyncio.create_task(gateway.geocode(GeocodeRequest(address="first")))
    await first_entered.wait()
    waiter_admitted = _observe_second_admission(gateway)
    waiter = asyncio.create_task(gateway.geocode(GeocodeRequest(address="waiting")))
    await waiter_admitted.wait()

    waiter.cancel("unsent-cancellation")
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await waiter
    assert cancelled.value.args == ("unsent-cancellation",)
    assert environment.call_count == 1
    assert gateway._state == "ready"
    assert gateway._broken is False
    assert gateway._in_flight == {first}
    assert gateway._semaphore._value == 0

    first_release.set()
    await first
    await gateway.geocode(GeocodeRequest(address="still-ready"))
    assert environment.call_count == 2
    assert gateway._state == "ready"
    assert gateway._broken is False
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 1
    await gateway.aclose()


@pytest.mark.asyncio
async def test_close_timeout_tracks_delayed_call_cleanup_until_real_closed() -> None:
    environment = FakeMcpEnvironment()
    entered = asyncio.Event()
    first_cancelled = asyncio.Event()
    second_cancelled = asyncio.Event()
    cleanup_release = asyncio.Event()

    async def handler(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            first_cancelled.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                second_cancelled.set()
                await cleanup_release.wait()
                raise

    environment.set_handler("map_geocode", handler)
    gateway = await _gateway(environment, close_timeout=0.1)
    call = asyncio.create_task(gateway.geocode(GeocodeRequest(address="delayed-cleanup")))
    await entered.wait()
    closing = asyncio.create_task(gateway.aclose())
    await gateway._closing_started.wait()
    await asyncio.wait_for(second_cancelled.wait(), timeout=1.0)
    with pytest.raises(BaiduMapTimeoutError):
        await asyncio.wait_for(closing, timeout=1.0)

    close_task = gateway._close_task
    assert close_task is not None and not close_task.done()
    assert first_cancelled.is_set() and second_cancelled.is_set()
    assert gateway._state == "closing"
    assert gateway._in_flight
    assert gateway._semaphore._value == 0
    assert gateway._log_guard_owned is True
    with pytest.raises(BaiduMapSessionError):
        await gateway.geocode(GeocodeRequest(address="zero-network-during-cleanup"))
    assert environment.call_count == 1
    with pytest.raises(BaiduMapTimeoutError):
        await gateway.aclose()
    assert gateway._close_task is close_task

    cleanup_release.set()
    with pytest.raises(BaiduMapTimeoutError):
        await asyncio.wait_for(asyncio.shield(close_task), timeout=1.0)
    with pytest.raises(asyncio.CancelledError):
        await call
    assert gateway._state == "closed"
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 1
    assert gateway._owner_task.done()
    assert gateway._close_task is close_task and close_task.done()
    assert gateway._log_guard_owned is False
    await gateway.aclose()


@pytest.mark.asyncio
async def test_close_waiters_share_first_absolute_public_deadline() -> None:
    environment = FakeMcpEnvironment()
    entered = asyncio.Event()
    second_cancelled = asyncio.Event()
    cleanup_release = asyncio.Event()

    async def handler(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                second_cancelled.set()
                await cleanup_release.wait()
                raise

    environment.set_handler("map_geocode", handler)
    gateway = await _gateway(environment, close_timeout=0.15)
    call = asyncio.create_task(gateway.geocode(GeocodeRequest(address="shared-deadline")))
    await entered.wait()
    first = asyncio.create_task(gateway.aclose())
    await gateway._closing_started.wait()
    deadline = gateway._close_deadline
    assert deadline is not None
    late_gate = asyncio.Event()
    asyncio.get_running_loop().call_later(0.05, late_gate.set)
    await late_gate.wait()
    second = asyncio.create_task(gateway.aclose())
    assert gateway._close_deadline == deadline
    await asyncio.wait_for(second_cancelled.wait(), timeout=1.0)
    results = await asyncio.wait_for(
        asyncio.gather(first, second, return_exceptions=True), timeout=1.0
    )
    assert all(type(result) is BaiduMapTimeoutError for result in results)
    close_task = gateway._close_task
    assert close_task is not None and not close_task.done()
    assert gateway._state == "closing"
    assert gateway._log_guard_owned is True

    cleanup_release.set()
    with pytest.raises(BaiduMapTimeoutError):
        await asyncio.wait_for(asyncio.shield(close_task), timeout=1.0)
    with pytest.raises(asyncio.CancelledError):
        await call
    assert gateway._state == "closed"
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 1
    assert gateway._owner_task.done()
    assert gateway._close_task is close_task and close_task.done()
    assert gateway._log_guard_owned is False


@pytest.mark.asyncio
async def test_close_deadline_preserves_started_owner_context_exit_and_guard() -> None:
    environment = FakeMcpEnvironment()
    exit_started = asyncio.Event()
    exit_cancelled = asyncio.Event()
    exit_release = asyncio.Event()
    exit_completed = asyncio.Event()

    @asynccontextmanager
    async def delayed_exit_factory():
        environment.enter_calls += 1
        session = ScriptedMcpSession(environment)
        environment.sessions.append(session)
        try:
            yield session
        finally:
            environment.exit_calls += 1
            exit_started.set()
            try:
                await exit_release.wait()
            except asyncio.CancelledError:
                exit_cancelled.set()
                raise
            finally:
                exit_completed.set()

    gateway = await BaiduMapMcpGateway.create(
        settings=BaiduMapSettings(api_key=SecretStr("offline")),
        clock=Clock(),
        limits=BaiduMapLimits(close_timeout_s=0.2),
        _client_factory=delayed_exit_factory,
    )
    closing = asyncio.create_task(gateway.aclose())
    await exit_started.wait()
    with pytest.raises(BaiduMapTimeoutError):
        await asyncio.wait_for(closing, timeout=1.0)
    close_task = gateway._close_task
    assert close_task is not None and not close_task.done()
    assert not exit_cancelled.is_set()
    assert not exit_completed.is_set()
    assert gateway._owner_exit_phase_started.is_set()
    assert not gateway._owner_exit_completed.is_set()
    assert gateway._state == "closing"
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 3
    assert not gateway._owner_task.done()
    assert gateway._log_guard_owned is True
    with pytest.raises(BaiduMapSessionError):
        await gateway.geocode(GeocodeRequest(address="zero-network-during-exit"))
    with pytest.raises(BaiduMapTimeoutError):
        await gateway.aclose()

    exit_release.set()
    with pytest.raises(BaiduMapTimeoutError):
        await asyncio.wait_for(asyncio.shield(close_task), timeout=1.0)
    assert exit_completed.is_set()
    assert gateway._owner_exit_completed.is_set()
    assert gateway._state == "closed"
    assert gateway._owner_task.done()
    assert gateway._close_task is close_task and close_task.done()
    assert gateway._log_guard_owned is False
    assert environment.exit_calls == 1
    await gateway.aclose()


@pytest.mark.asyncio
async def test_close_timeout_tracks_owner_affine_exit_until_real_closed() -> None:
    environment = FakeMcpEnvironment()
    exit_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    exit_completed = asyncio.Event()
    enter_task: asyncio.Task[object] | None = None
    initialize_task: asyncio.Task[object] | None = None
    list_task: asyncio.Task[object] | None = None
    exit_task: asyncio.Task[object] | None = None

    class AffinitySession(ScriptedMcpSession):
        async def initialize(self):
            nonlocal initialize_task
            initialize_task = asyncio.current_task()
            return await super().initialize()

        async def list_tools(self):
            nonlocal list_task
            list_task = asyncio.current_task()
            return await super().list_tools()

    @asynccontextmanager
    async def delayed_exit_factory():
        nonlocal enter_task, exit_task
        environment.enter_calls += 1
        enter_task = asyncio.current_task()
        session = AffinitySession(environment)
        environment.sessions.append(session)
        try:
            yield session
        finally:
            environment.exit_calls += 1
            exit_task = asyncio.current_task()
            exit_started.set()
            try:
                await cleanup_release.wait()
            except asyncio.CancelledError:
                raise
            finally:
                exit_completed.set()

    gateway = await BaiduMapMcpGateway.create(
        settings=BaiduMapSettings(api_key=SecretStr("offline")),
        clock=Clock(),
        limits=BaiduMapLimits(close_timeout_s=0.1),
        _client_factory=delayed_exit_factory,
    )
    closing = asyncio.create_task(gateway.aclose())
    await exit_started.wait()
    with pytest.raises(BaiduMapTimeoutError):
        await asyncio.wait_for(closing, timeout=1.0)

    close_task = gateway._close_task
    assert close_task is not None and not close_task.done()
    assert not exit_completed.is_set()
    assert exit_task is not None and not exit_task.done()
    assert enter_task is initialize_task is list_task is exit_task is gateway._owner_task
    assert environment.exit_calls == 1
    assert gateway._state == "closing"
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 3
    assert not gateway._owner_task.done()
    assert gateway._log_guard_owned is True

    cleanup_release.set()
    with pytest.raises(BaiduMapTimeoutError):
        await asyncio.wait_for(asyncio.shield(close_task), timeout=1.0)
    assert gateway._state == "closed"
    assert exit_completed.is_set()
    assert gateway._owner_task.done()
    assert gateway._close_task is close_task and close_task.done()
    assert gateway._log_guard_owned is False
    await gateway.aclose()


@pytest.mark.asyncio
async def test_close_deadline_cancels_registered_call_and_still_reaches_closed() -> None:
    environment = FakeMcpEnvironment()
    entered = asyncio.Event()
    never_release = asyncio.Event()

    async def handler(_):
        entered.set()
        await never_release.wait()
        return _success()

    environment.set_handler("map_geocode", handler)
    gateway = await _gateway(environment, close_timeout=0.02)
    call = asyncio.create_task(gateway.geocode(GeocodeRequest(address="in-flight")))
    await entered.wait()
    with pytest.raises(BaiduMapTimeoutError):
        await gateway.aclose()
    close_task = gateway._close_task
    assert close_task is not None
    if not close_task.done():
        with pytest.raises(BaiduMapTimeoutError):
            await asyncio.wait_for(asyncio.shield(close_task), timeout=1.0)
    assert call.cancelled()
    assert gateway._state == "closed"
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 1
    await gateway.aclose()
