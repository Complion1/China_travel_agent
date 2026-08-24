from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import gc
import logging
import weakref

import anyio
import pytest
from pydantic import SecretStr

from app.baidu_map import (
    BaiduCoordinate,
    BaiduMapAuthenticationError,
    BaiduMapConfigurationError,
    BaiduMapLimits,
    BaiduMapProtocolError,
    BaiduMapRateLimitError,
    BaiduMapRequestError,
    BaiduMapResponseError,
    BaiduMapSessionError,
    BaiduMapTimeoutError,
    BaiduMapSettings,
    BaiduMapToolSchemaError,
    BaiduMapToolUnavailableError,
    BaiduMapTransportError,
    CitySearchArea,
    DirectionsRequest,
    GeocodeRequest,
    MapCapability,
    MapCapabilityUnavailableReason,
    MapLaunchSearchRequest,
    MatrixTravelMode,
    NearbySearchArea,
    PlaceDetailsRequest,
    PlaceSearchRequest,
    ReverseGeocodeRequest,
    RouteEndpoint,
    RouteMatrixRequest,
    TravelMode,
    WeatherRequest,
    create_baidu_map_runtime,
)
from app.baidu_map._mcp_gateway import BaiduMapMcpGateway
from app.baidu_map import _mcp_gateway as gateway_module
from tests.support.baidu_map_fake_mcp import (
    FakeMcpEnvironment,
    ScriptedMcpSession,
    qualified_tools,
    structured_result,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 20, tzinfo=timezone.utc)


SETTINGS = BaiduMapSettings(api_key=SecretStr("offline-only-key"))
POINT = BaiduCoordinate(latitude=30.25, longitude=120.16)


async def create_gateway(environment: FakeMcpEnvironment) -> BaiduMapMcpGateway:
    return await BaiduMapMcpGateway.create(
        settings=SETTINGS,
        clock=FixedClock(),
        limits=__import__("app.baidu_map", fromlist=["DEFAULT_BAIDU_MAP_LIMITS"]).DEFAULT_BAIDU_MAP_LIMITS,
        _client_factory=environment.factory,
    )


def install_success_handlers(environment: FakeMcpEnvironment) -> None:
    environment.set_result(
        "map_geocode",
        structured_result({"status": 0, "result": {"location": {"lat": 30.25, "lng": 120.16}}}),
    )
    environment.set_result(
        "map_reverse_geocode",
        structured_result(
            {
                "status": 0,
                "result": {
                    "formatted_address": "地址",
                    "addressComponent": {},
                    "pois": [
                        {
                            "uid": "reverse-u",
                            "name": "附近地点",
                            "addr": "附近地址",
                            "point": {"x": 120.16, "y": 30.25},
                            "tag": "景点",
                        }
                    ],
                },
            }
        ),
    )
    environment.set_result(
        "map_search_places",
        structured_result({"status": 0, "results": [{"uid": "u", "name": "地点", "address": "地址", "location": {"lat": 30.25, "lng": 120.16}}]}),
    )
    environment.set_result(
        "map_place_details",
        structured_result({"status": 0, "result": {"uid": "u", "name": "地点", "address": "地址", "location": {"lat": 30.25, "lng": 120.16}, "detail_info": {}}}),
    )
    environment.set_result(
        "map_directions_matrix",
        structured_result({"status": 0, "result": [{"distance": {"value": 10}, "duration": {"value": 5}}]}),
    )

    def directions(arguments):
        step = {"instruction": "前行", "distance": 10, "duration": 5, "start_location": {"lat": 30.25, "lng": 120.16}, "end_location": {"lat": 30.25, "lng": 120.16}}
        steps = [[step]] if arguments["model"] == "transit" else [step]
        return structured_result({"status": 0, "result": {"routes": [{"distance": 10, "duration": 5, "steps": steps}]}})

    environment.set_handler("map_directions", directions)
    environment.set_result(
        "map_weather",
        structured_result({"status": 0, "result": {"now": {"text": "晴", "temp": 28}, "forecasts": []}}),
    )


@pytest.mark.asyncio
async def test_startup_initializes_and_lists_once_without_tool_call_then_closes() -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)
    assert environment.enter_calls == 1
    assert environment.initialize_calls == 1
    assert environment.list_tools_calls == 1
    assert environment.call_count == 0
    assert gateway.get_capability_availability(capability=MapCapability.GEOCODING).available
    launch = gateway.get_capability_availability(capability=MapCapability.MAP_LAUNCH)
    assert not launch.available
    assert launch.reason_code is MapCapabilityUnavailableReason.NOT_IN_MANIFEST
    assert gateway.get_availability() == launch
    await gateway.aclose()
    await gateway.aclose()
    assert environment.exit_calls == 1


@pytest.mark.asyncio
async def test_startup_releases_raw_list_tools_before_owner_waits(monkeypatch) -> None:
    environment = FakeMcpEnvironment()
    raw_refs: list[weakref.ReferenceType[object]] = []
    original_list_tools = ScriptedMcpSession.list_tools

    async def capture_list_tools(session):
        listed = await original_list_tools(session)
        raw_refs.append(weakref.ref(listed))
        return listed

    monkeypatch.setattr(ScriptedMcpSession, "list_tools", capture_list_tools)
    gateway = await create_gateway(environment)
    gc.collect()

    assert environment.initialize_calls == 1
    assert environment.list_tools_calls == 1
    assert environment.call_count == 0
    assert raw_refs and raw_refs[0]() is None
    frame = gateway._owner_task.get_coro().cr_frame
    assert frame is not None
    assert frame.f_locals.get("listed") is None
    assert all(type(value).__name__ != "ListToolsResult" for value in frame.f_locals.values())
    await gateway.aclose()


@pytest.mark.asyncio
async def test_startup_transient_timeout_retries_with_fresh_session() -> None:
    environment = FakeMcpEnvironment()
    environment.initialize_failures.append(TimeoutError())
    gateway = await create_gateway(environment)
    assert environment.enter_calls == 2
    assert environment.exit_calls == 1
    assert len(environment.sessions) == 2
    assert environment.initialize_calls == 2
    assert environment.list_tools_calls == 1
    await gateway.aclose()


@pytest.mark.asyncio
async def test_transient_retry_waits_for_first_owner_exit_before_second_session() -> None:
    environment = FakeMcpEnvironment()
    environment.initialize_failures.append(TimeoutError())
    exit_started = asyncio.Event()
    exit_release = asyncio.Event()
    exit_finished = asyncio.Event()
    exit_cancelled = asyncio.Event()
    entered_tasks: list[asyncio.Task[object] | None] = []
    exited_tasks: list[asyncio.Task[object] | None] = []

    @asynccontextmanager
    async def delayed_retry_factory():
        environment.enter_calls += 1
        session = ScriptedMcpSession(environment)
        environment.sessions.append(session)
        entered_tasks.append(asyncio.current_task())
        try:
            yield session
        finally:
            environment.exit_calls += 1
            exited_tasks.append(asyncio.current_task())
            if environment.exit_calls == 1:
                exit_started.set()
                try:
                    await exit_release.wait()
                except asyncio.CancelledError:
                    exit_cancelled.set()
                    await exit_release.wait()
                exit_finished.set()

    create_task = asyncio.create_task(
        BaiduMapMcpGateway.create(
            settings=SETTINGS,
            clock=FixedClock(),
            limits=__import__("app.baidu_map", fromlist=["DEFAULT_BAIDU_MAP_LIMITS"]).DEFAULT_BAIDU_MAP_LIMITS,
            _client_factory=delayed_retry_factory,
        )
    )
    await exit_started.wait()
    assert environment.enter_calls == 1
    assert not create_task.done()
    assert not exit_cancelled.is_set()

    exit_release.set()
    gateway = await create_task
    assert exit_finished.is_set()
    assert environment.enter_calls == 2
    assert environment.exit_calls == 1
    assert len(environment.sessions) == 2
    assert entered_tasks[0] is exited_tasks[0]
    assert entered_tasks[0] is not asyncio.current_task()
    await gateway.aclose()


@pytest.mark.asyncio
async def test_list_timeout_retries_whole_startup_with_one_list_per_session() -> None:
    environment = FakeMcpEnvironment()
    environment.list_failures.append(TimeoutError())
    gateway = await create_gateway(environment)
    assert len(environment.sessions) == 2
    assert [session.initialize_calls for session in environment.sessions] == [1, 1]
    assert [session.list_tools_calls for session in environment.sessions] == [1, 1]
    assert environment.call_count == 0
    await gateway.aclose()


@pytest.mark.asyncio
async def test_schema_failure_does_not_retry_and_restores_logging_factory() -> None:
    tools = qualified_tools()
    tools.pop()
    environment = FakeMcpEnvironment(tools=tools)
    before = logging.getLogRecordFactory()
    with pytest.raises(BaiduMapToolSchemaError):
        await create_gateway(environment)
    assert environment.enter_calls == 1
    assert environment.exit_calls == 1
    assert logging.getLogRecordFactory() is before


@pytest.mark.asyncio
async def test_all_core_methods_use_exact_outbound_arguments() -> None:
    environment = FakeMcpEnvironment(tools=qualified_tools(search_limit=True))
    install_success_handlers(environment)
    gateway = await create_gateway(environment)
    endpoint = RouteEndpoint(coordinate=POINT, label="local-only")
    await gateway.geocode(GeocodeRequest(address="西湖", city="杭州"))
    reverse = await gateway.reverse_geocode(ReverseGeocodeRequest(location=POINT))
    assert tuple(place.uid for place in reverse.nearby_places) == ("reverse-u",)
    assert reverse.nearby_places[0].coordinate == POINT
    await gateway.search_places(PlaceSearchRequest(query="博物馆", tag="景点", area=CitySearchArea(region="杭州"), result_limit=1))
    await gateway.get_place_details(PlaceDetailsRequest(uid="u"))
    await gateway.calculate_route_matrix(RouteMatrixRequest(mode=MatrixTravelMode.WALKING, origins=(POINT,), destinations=(POINT,)))
    for mode in TravelMode:
        await gateway.plan_directions(DirectionsRequest(mode=mode, origin=endpoint, destination=endpoint))
    await gateway.get_weather(WeatherRequest(location=POINT))
    calls = environment.calls
    assert calls[0] == ("map_geocode", {"address": "西湖", "city": "杭州", "is_chinese_mainland": "true"})
    assert calls[1] == ("map_reverse_geocode", {"latitude": 30.25, "longitude": 120.16})
    assert calls[2] == ("map_search_places", {"query": "博物馆", "tag": "景点", "is_chinese_mainland": "true", "region": "杭州", "limit": 1})
    assert calls[3] == ("map_place_details", {"uid": "u", "is_chinese_mainland": "true"})
    assert calls[4] == ("map_directions_matrix", {"model": "walking", "origins": "30.25,120.16", "destinations": "30.25,120.16"})
    for _, arguments in calls[5:9]:
        assert "local-only" not in repr(arguments)
        assert arguments["origin"] == "30.25,120.16"
    assert calls[9] == ("map_weather", {"location": "120.16,30.25", "is_china": "true"})
    await gateway.aclose()


@pytest.mark.asyncio
async def test_request_boundary_rejects_dict_without_network() -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)
    with pytest.raises(BaiduMapRequestError):
        await gateway.geocode({"address": "x"})  # type: ignore[arg-type]
    assert environment.call_count == 0
    await gateway.aclose()


@pytest.mark.asyncio
async def test_request_boundary_rejects_every_public_method_without_network() -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)
    calls = (
        gateway.reverse_geocode({}),
        gateway.search_places({}),
        gateway.get_place_details({}),
        gateway.calculate_route_matrix({}),
        gateway.plan_directions({}),
        gateway.get_weather({}),
    )
    for call in calls:
        with pytest.raises(BaiduMapRequestError):
            await call  # type: ignore[misc]
    assert environment.call_count == 0
    await gateway.aclose()


@pytest.mark.asyncio
async def test_sent_response_failure_cleans_all_public_method_boundaries() -> None:
    point = POINT
    endpoint = RouteEndpoint(coordinate=point)
    requests = (
        ("map_reverse_geocode", lambda gateway: gateway.reverse_geocode(ReverseGeocodeRequest(location=point))),
        (
            "map_search_places",
            lambda gateway: gateway.search_places(
                PlaceSearchRequest(query="公园", area=CitySearchArea(region="杭州"))
            ),
        ),
        ("map_place_details", lambda gateway: gateway.get_place_details(PlaceDetailsRequest(uid="u"))),
        (
            "map_directions_matrix",
            lambda gateway: gateway.calculate_route_matrix(
                RouteMatrixRequest(mode=MatrixTravelMode.WALKING, origins=(point,), destinations=(point,))
            ),
        ),
        (
            "map_directions",
            lambda gateway: gateway.plan_directions(
                DirectionsRequest(mode=TravelMode.WALKING, origin=endpoint, destination=endpoint)
            ),
        ),
        ("map_weather", lambda gateway: gateway.get_weather(WeatherRequest(location=point))),
    )
    for tool_name, invoke in requests:
        environment = FakeMcpEnvironment()
        environment.set_result(tool_name, structured_result({"status": 1}))
        gateway = await create_gateway(environment)
        with pytest.raises(BaiduMapResponseError):
            await invoke(gateway)
        assert environment.call_count == 1
        await gateway.aclose()


@pytest.mark.asyncio
async def test_startup_configuration_transport_and_production_factory_paths(monkeypatch) -> None:
    with pytest.raises(BaiduMapConfigurationError):
        await BaiduMapMcpGateway.create(settings=object(), clock=FixedClock(), limits=object())  # type: ignore[arg-type]

    failing = FakeMcpEnvironment()
    failing.initialize_failures.append(RuntimeError("offline-init"))
    with pytest.raises(BaiduMapTransportError):
        await create_gateway(failing)
    assert failing.enter_calls == 1
    assert failing.exit_calls == 1

    environment = FakeMcpEnvironment()
    monkeypatch.setattr(gateway_module, "_production_client_context", lambda **_: environment.factory())
    gateway = await BaiduMapMcpGateway.create(settings=SETTINGS, clock=FixedClock(), limits=__import__("app.baidu_map", fromlist=["DEFAULT_BAIDU_MAP_LIMITS"]).DEFAULT_BAIDU_MAP_LIMITS)
    assert environment.initialize_calls == 1
    await gateway.aclose()


@pytest.mark.asyncio
async def test_production_client_context_uses_frozen_http_session_shape(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_stream(endpoint, *, http_client):
        assert endpoint.startswith("https://mcp.map.baidu.com/mcp?")
        assert http_client.follow_redirects is False
        yield object(), object(), object()

    class FakeClientSession:
        def __init__(self, read_stream, write_stream):
            self.read_stream = read_stream
            self.write_stream = write_stream

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(gateway_module, "streamable_http_client", fake_stream)
    monkeypatch.setattr(gateway_module, "ClientSession", FakeClientSession)
    async with gateway_module._production_client_context(
        settings=SETTINGS,
        limits=__import__("app.baidu_map", fromlist=["DEFAULT_BAIDU_MAP_LIMITS"]).DEFAULT_BAIDU_MAP_LIMITS,
    ) as session:
        assert isinstance(session, FakeClientSession)


@pytest.mark.asyncio
async def test_sdk_context_enter_and_exit_run_in_same_dedicated_owner_task() -> None:
    environment = FakeMcpEnvironment()
    entered_by: asyncio.Task[object] | None = None
    exited_by: asyncio.Task[object] | None = None

    @asynccontextmanager
    async def task_affine_factory():
        nonlocal entered_by, exited_by
        entered_by = asyncio.current_task()  # type: ignore[assignment]
        async with anyio.create_task_group():
            async with environment.factory() as session:
                try:
                    yield session
                finally:
                    exited_by = asyncio.current_task()  # type: ignore[assignment]

    caller = asyncio.current_task()
    gateway = await BaiduMapMcpGateway.create(
        settings=SETTINGS,
        clock=FixedClock(),
        limits=__import__("app.baidu_map", fromlist=["DEFAULT_BAIDU_MAP_LIMITS"]).DEFAULT_BAIDU_MAP_LIMITS,
        _client_factory=task_affine_factory,
    )
    assert entered_by is gateway._owner_task
    assert entered_by is not caller
    await gateway.aclose()
    assert exited_by is entered_by
    assert environment.exit_calls == 1


@pytest.mark.asyncio
async def test_startup_cancellation_and_close_failure_are_cleaned_without_retry() -> None:
    @asynccontextmanager
    async def cancelled_factory():
        raise asyncio.CancelledError
        yield

    with pytest.raises(asyncio.CancelledError):
        await BaiduMapMcpGateway.create(
            settings=SETTINGS,
            clock=FixedClock(),
            limits=__import__("app.baidu_map", fromlist=["DEFAULT_BAIDU_MAP_LIMITS"]).DEFAULT_BAIDU_MAP_LIMITS,
            _client_factory=cancelled_factory,
        )

    environment = FakeMcpEnvironment(tools=qualified_tools()[:-1])

    @asynccontextmanager
    async def failing_exit_factory():
        session = ScriptedMcpSession(environment)
        yield session
        raise RuntimeError("exit-failure")

    with pytest.raises(BaiduMapToolSchemaError):
        await BaiduMapMcpGateway.create(
            settings=SETTINGS,
            clock=FixedClock(),
            limits=__import__("app.baidu_map", fromlist=["DEFAULT_BAIDU_MAP_LIMITS"]).DEFAULT_BAIDU_MAP_LIMITS,
            _client_factory=failing_exit_factory,
        )


@pytest.mark.asyncio
async def test_startup_cancellation_waits_for_started_owner_exit_and_preserves_cancelled_error() -> None:
    environment = FakeMcpEnvironment()
    environment.initialize_failures.append(TimeoutError())
    exit_started = asyncio.Event()
    exit_release = asyncio.Event()
    exit_finished = asyncio.Event()
    exit_cancelled = asyncio.Event()
    owner_task: asyncio.Task[object] | None = None

    @asynccontextmanager
    async def delayed_cancel_factory():
        nonlocal owner_task
        environment.enter_calls += 1
        session = ScriptedMcpSession(environment)
        environment.sessions.append(session)
        owner_task = asyncio.current_task()
        try:
            yield session
        finally:
            environment.exit_calls += 1
            exit_started.set()
            try:
                await exit_release.wait()
            except asyncio.CancelledError:
                exit_cancelled.set()
                await exit_release.wait()
            exit_finished.set()

    create_task = asyncio.create_task(
        BaiduMapMcpGateway.create(
            settings=SETTINGS,
            clock=FixedClock(),
            limits=__import__("app.baidu_map", fromlist=["DEFAULT_BAIDU_MAP_LIMITS"]).DEFAULT_BAIDU_MAP_LIMITS,
            _client_factory=delayed_cancel_factory,
        )
    )
    await exit_started.wait()
    assert owner_task is not None and not owner_task.done()
    assert environment.enter_calls == 1
    assert gateway_module._FACTORY_REFS == 1

    create_task.cancel("startup-cancel-message")
    assert not create_task.done()
    assert not exit_cancelled.is_set()

    exit_release.set()
    with pytest.raises(asyncio.CancelledError) as captured:
        await create_task
    assert captured.value.args == ("startup-cancel-message",)
    assert exit_finished.is_set()
    assert not exit_cancelled.is_set()
    assert environment.enter_calls == 1
    assert gateway_module._FACTORY_REFS == 0


@pytest.mark.asyncio
async def test_repeated_startup_cancellation_does_not_cancel_owner_exit_or_retry_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = FakeMcpEnvironment()
    environment.initialize_failures.append(TimeoutError())
    exit_started = asyncio.Event()
    exit_release = asyncio.Event()
    exit_finished = asyncio.Event()
    exit_cancelled = asyncio.Event()
    owner_task: asyncio.Task[object] | None = None
    owner_wait_started = asyncio.Event()
    real_gather = gateway_module.asyncio.gather
    real_shield = gateway_module.asyncio.shield

    def observe_gather(*awaitables: object, **kwargs: object):
        if owner_task is not None and owner_task in awaitables:
            owner_wait_started.set()
        return real_gather(*awaitables, **kwargs)

    def observe_shield(awaitable: object):
        if awaitable is owner_task:
            owner_wait_started.set()
        return real_shield(awaitable)

    monkeypatch.setattr(gateway_module.asyncio, "gather", observe_gather)
    monkeypatch.setattr(gateway_module.asyncio, "shield", observe_shield)

    @asynccontextmanager
    async def delayed_cancel_factory():
        nonlocal owner_task
        environment.enter_calls += 1
        session = ScriptedMcpSession(environment)
        environment.sessions.append(session)
        owner_task = asyncio.current_task()
        try:
            yield session
        finally:
            environment.exit_calls += 1
            exit_started.set()
            try:
                await exit_release.wait()
            except asyncio.CancelledError:
                exit_cancelled.set()
                await exit_release.wait()
            exit_finished.set()

    create_task = asyncio.create_task(
        BaiduMapMcpGateway.create(
            settings=SETTINGS,
            clock=FixedClock(),
            limits=__import__("app.baidu_map", fromlist=["DEFAULT_BAIDU_MAP_LIMITS"]).DEFAULT_BAIDU_MAP_LIMITS,
            _client_factory=delayed_cancel_factory,
        )
    )
    await exit_started.wait()
    assert owner_task is not None and not owner_task.done()
    assert environment.enter_calls == 1
    assert gateway_module._FACTORY_REFS == 1

    create_task.cancel("first-startup-cancel")
    await owner_wait_started.wait()
    assert not create_task.done()
    assert not exit_cancelled.is_set()

    create_task.cancel("second-startup-cancel")
    assert not create_task.done()
    exit_release.set()
    with pytest.raises(asyncio.CancelledError) as captured:
        await create_task
    assert captured.value.args == ("first-startup-cancel",)
    assert exit_finished.is_set()
    assert not exit_cancelled.is_set()
    assert owner_task.done()
    assert environment.enter_calls == 1
    assert gateway_module._FACTORY_REFS == 0

    gateway = await create_gateway(environment)
    assert environment.enter_calls == 2
    await gateway.aclose()
    assert environment.exit_calls == 2
    assert gateway_module._FACTORY_REFS == 0


@pytest.mark.asyncio
async def test_map_launch_validation_and_lifecycle_rejection_paths() -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)
    invalid = MapLaunchSearchRequest.model_construct(query="bad", location=None, radius_m=1)
    with pytest.raises(BaiduMapRequestError):
        await gateway.create_map_launch(invalid)
    with pytest.raises(BaiduMapRequestError):
        await gateway.create_map_launch(object())  # type: ignore[arg-type]
    with pytest.raises(BaiduMapRequestError):
        gateway.get_capability_availability(capability=object())  # type: ignore[arg-type]
    await gateway.aclose()
    with pytest.raises(BaiduMapSessionError):
        await gateway.create_map_launch(MapLaunchSearchRequest(query="公园"))
    assert environment.call_count == 0


@pytest.mark.asyncio
async def test_rate_limit_and_transport_classification_do_not_break_session() -> None:
    for status, error_type in ((401, BaiduMapAuthenticationError), (403, BaiduMapAuthenticationError), (429, BaiduMapRateLimitError)):
        environment = FakeMcpEnvironment()

        async def handler(_, status=status):
            request = __import__("httpx").Request("POST", "https://example.invalid")
            response = __import__("httpx").Response(status, request=request)
            raise __import__("httpx").HTTPStatusError("offline", request=request, response=response)

        environment.set_handler("map_geocode", handler)
        gateway = await create_gateway(environment)
        with pytest.raises(error_type):
            await gateway.geocode(GeocodeRequest(address="x"))
        assert gateway._state == ("ready" if status == 429 else "broken")
        await gateway.aclose()

    environment = FakeMcpEnvironment()
    environment.set_handler("map_geocode", lambda _: (_ for _ in ()).throw(RuntimeError("transport")))
    gateway = await create_gateway(environment)
    with pytest.raises(BaiduMapTransportError):
        await gateway.geocode(GeocodeRequest(address="x"))
    assert gateway._state == "broken"
    await gateway.aclose()


@pytest.mark.asyncio
async def test_gateway_internal_admission_timeout_and_defensive_none_paths() -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)
    assert await gateway._admit() is not None
    assert await gateway._admit() is None
    task = asyncio.current_task()
    assert task is not None
    await gateway._finish(task)
    original_current_task = gateway_module.asyncio.current_task
    gateway_module.asyncio.current_task = lambda: None  # type: ignore[method-assign]
    try:
        assert await gateway._admit() is None
    finally:
        gateway_module.asyncio.current_task = original_current_task  # type: ignore[method-assign]
    malformed = GeocodeRequest.model_construct(address=None)
    with pytest.raises(BaiduMapRequestError):
        await gateway.geocode(malformed)
    with pytest.raises(BaiduMapTimeoutError):
        await gateway._invoke(
            capability=MapCapability.GEOCODING,
            request=GeocodeRequest(address="x"),
            arguments={"address": "x"},
            timeout_s=0.01,
            parser=lambda *_: object(),
            started=__import__("time").monotonic() - 1,
        )
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 3
    assert gateway_module._format_number(-0.0) == "0"
    await gateway.aclose()


@pytest.mark.asyncio
async def test_gateway_rate_limit_parser_and_none_result_do_not_mark_broken() -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)
    with pytest.raises(BaiduMapRateLimitError):
        await gateway._invoke(
            capability=MapCapability.GEOCODING,
            request=GeocodeRequest(address="x"),
            arguments={"address": "x"},
            timeout_s=1.0,
            parser=lambda *_: (_ for _ in ()).throw(__import__("app.baidu_map", fromlist=["BaiduMapRateLimitError"]).BaiduMapRateLimitError()),
            started=__import__("time").monotonic(),
        )
    assert gateway._state == "ready"
    with pytest.raises(BaiduMapResponseError):
        await gateway._invoke(
            capability=MapCapability.GEOCODING,
            request=GeocodeRequest(address="x"),
            arguments={"address": "x"},
            timeout_s=1.0,
            parser=lambda *_: None,
            started=__import__("time").monotonic(),
        )
    assert gateway._state == "ready"
    await gateway.aclose()


@pytest.mark.asyncio
async def test_gateway_public_close_is_idempotent_and_runtime_facade_closes(monkeypatch) -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)
    await gateway.aclose()
    assert gateway._state == "closed"
    await gateway.aclose()
    assert gateway._state == "closed"

    second_environment = FakeMcpEnvironment()
    second = await create_gateway(second_environment)
    injected = False

    async def injected_create(cls, **kwargs):
        nonlocal injected
        del cls, kwargs
        injected = True
        return second

    monkeypatch.setattr(BaiduMapMcpGateway, "create", classmethod(injected_create))
    with pytest.raises(TypeError):
        __import__("app.baidu_map.runtime", fromlist=["BaiduMapRuntime"]).BaiduMapRuntime(_gateway=object())
    async with __import__("app.baidu_map.runtime", fromlist=["create_baidu_map_runtime"]).create_baidu_map_runtime(
        settings=SETTINGS, clock=FixedClock()
    ) as runtime:
        assert injected
        assert runtime.adapter is second
        assert runtime.map_launch is second
        assert runtime.get_capability_availability(capability=MapCapability.MAP_LAUNCH).available is False
async def test_sent_response_failure_breaks_runtime_and_next_call_is_zero_network() -> None:
    environment = FakeMcpEnvironment()
    environment.set_result("map_geocode", structured_result({"status": 1, "message": "ignored"}))
    gateway = await create_gateway(environment)
    with pytest.raises(BaiduMapResponseError):
        await gateway.geocode(GeocodeRequest(address="x"))
    with pytest.raises(BaiduMapSessionError):
        await gateway.geocode(GeocodeRequest(address="y"))
    assert environment.call_count == 1
    await gateway.aclose()


@pytest.mark.asyncio
async def test_unavailable_map_launch_fresh_validates_and_never_calls_provider() -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)
    request = MapLaunchSearchRequest(query="西湖")
    with pytest.raises(BaiduMapToolUnavailableError):
        await gateway.create_map_launch(request)
    assert environment.call_count == 0
    await gateway.aclose()


@pytest.mark.asyncio
async def test_map_launch_lock_wait_cancellation_drops_request_before_propagation() -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)
    original_lock = gateway._lock
    lock_entered = asyncio.Event()
    await original_lock.acquire()

    class ObservedLock:
        async def __aenter__(self):
            lock_entered.set()
            await original_lock.acquire()
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            original_lock.release()
            return False

    gateway._lock = ObservedLock()  # type: ignore[assignment]
    request = MapLaunchSearchRequest(query="MAP-LAUNCH-QUERY-CANARY", region="REGION-CANARY")
    call = asyncio.create_task(gateway.create_map_launch(request))
    await lock_entered.wait()
    call.cancel("map-launch-cancel")
    try:
        with pytest.raises(asyncio.CancelledError) as captured:
            await call
    finally:
        original_lock.release()
        gateway._lock = original_lock  # type: ignore[assignment]
    assert captured.value.args == ("map-launch-cancel",)
    assert environment.call_count == 0
    await gateway.aclose()


@pytest.mark.asyncio
async def test_matrix_outbound_preserves_one_by_n_n_by_one_and_n_by_m_order() -> None:
    environment = FakeMcpEnvironment()

    def matrix(arguments):
        origin_count = str(arguments["origins"]).count("|") + 1
        destination_count = str(arguments["destinations"]).count("|") + 1
        return structured_result(
            {
                "status": 0,
                "result": [
                    {"distance": {"value": index}, "duration": {"value": index}}
                    for index in range(origin_count * destination_count)
                ],
            }
        )

    environment.set_handler("map_directions_matrix", matrix)
    gateway = await create_gateway(environment)
    points = (
        BaiduCoordinate(latitude=1.0, longitude=101.0),
        BaiduCoordinate(latitude=0.0000001, longitude=102.0),
    )
    await gateway.calculate_route_matrix(
        RouteMatrixRequest(mode=MatrixTravelMode.DRIVING, origins=(points[0],), destinations=points)
    )
    await gateway.calculate_route_matrix(
        RouteMatrixRequest(mode=MatrixTravelMode.RIDING, origins=points, destinations=(points[1],))
    )
    await gateway.calculate_route_matrix(
        RouteMatrixRequest(mode=MatrixTravelMode.WALKING, origins=points, destinations=points)
    )
    matrix_calls = [arguments for name, arguments in environment.calls if name == "map_directions_matrix"]
    assert matrix_calls[0]["origins"] == "1,101"
    assert matrix_calls[0]["destinations"] == "1,101|0.0000001,102"
    assert matrix_calls[1]["origins"] == "1,101|0.0000001,102"
    assert matrix_calls[1]["destinations"] == "0.0000001,102"
    assert matrix_calls[2]["origins"].count("|") == 1
    assert matrix_calls[2]["destinations"].count("|") == 1
    assert all("is_chinese_mainland" not in arguments for arguments in matrix_calls)
    await gateway.aclose()


@pytest.mark.asyncio
async def test_nearby_search_branch_uses_coordinate_and_radius_arguments() -> None:
    environment = FakeMcpEnvironment()
    environment.set_result(
        "map_search_places",
        structured_result(
            {"status": 0, "results": [{"uid": "u", "name": "地点", "address": "地址", "location": {"lat": 30.25, "lng": 120.16}}]}
        ),
    )
    gateway = await create_gateway(environment)
    outcome = await gateway.search_places(
        PlaceSearchRequest(query="公园", area=NearbySearchArea(center=POINT, radius_m=100))
    )
    assert outcome.status.value == "success"
    assert environment.calls[0][1] == {
        "query": "公园",
        "is_chinese_mainland": "true",
        "location": "30.25,120.16",
        "radius": 100,
    }
    await gateway.aclose()


@pytest.mark.asyncio
async def test_context_body_exception_wins_over_close_failure(monkeypatch) -> None:
    environment = FakeMcpEnvironment()
    gateway = await create_gateway(environment)

    async def fail_close_resources():
        raise RuntimeError("close-canary")

    gateway._close_resources = fail_close_resources  # type: ignore[method-assign]

    async def injected_create(cls, **kwargs):
        del cls, kwargs
        return gateway

    monkeypatch.setattr(BaiduMapMcpGateway, "create", classmethod(injected_create))
    with pytest.raises(ValueError, match="body-wins"):
        async with create_baidu_map_runtime(settings=SETTINGS, clock=FixedClock()):
            raise ValueError("body-wins")


@pytest.mark.parametrize("body_kind", ["error", "cancel"])
@pytest.mark.asyncio
async def test_context_body_outcome_wins_while_tracked_close_finishes_later(
    monkeypatch, body_kind: str
) -> None:
    environment = FakeMcpEnvironment()
    cleanup_release = asyncio.Event()
    exit_started = asyncio.Event()

    @asynccontextmanager
    async def delayed_exit_factory():
        session = ScriptedMcpSession(environment)
        try:
            yield session
        finally:
            exit_started.set()
            await cleanup_release.wait()

    gateway = await BaiduMapMcpGateway.create(
        settings=SETTINGS,
        clock=FixedClock(),
        limits=BaiduMapLimits(close_timeout_s=0.05),
        _client_factory=delayed_exit_factory,
    )

    async def injected_create(cls, **kwargs):
        del cls, kwargs
        return gateway

    monkeypatch.setattr(BaiduMapMcpGateway, "create", classmethod(injected_create))
    expected = asyncio.CancelledError if body_kind == "cancel" else ValueError
    with pytest.raises(expected):
        async with create_baidu_map_runtime(settings=SETTINGS, clock=FixedClock()):
            if body_kind == "cancel":
                raise asyncio.CancelledError
            raise ValueError("body-wins-delayed-close")

    await asyncio.wait_for(exit_started.wait(), timeout=1.0)
    close_task = gateway._close_task
    assert close_task is not None and not close_task.done()
    assert gateway._state == "closing"
    assert gateway._log_guard_owned is True
    assert gateway._owner_exit_phase_started.is_set()
    assert not gateway._owner_exit_completed.is_set()

    cleanup_release.set()
    with pytest.raises(BaiduMapTimeoutError):
        await asyncio.wait_for(asyncio.shield(close_task), timeout=1.0)
    assert gateway._state == "closed"
    assert gateway._owner_task.done()
    assert gateway._log_guard_owned is False
