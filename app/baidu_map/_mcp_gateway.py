"""Private MCP transport, lifecycle and typed Baidu Maps adapter implementation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta
from decimal import Decimal
import importlib.util
import logging
from pathlib import Path
import threading
import time
from typing import Any, Protocol, cast
from urllib.parse import urlencode

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    ClientRequest,
    ListToolsResult,
)
from pydantic import BaseModel, ValidationError

from app.baidu_map._response_parsing import (
    parse_directions,
    parse_geocode,
    parse_place_details,
    parse_place_search,
    parse_reverse_geocode,
    parse_route_matrix,
    parse_weather,
    select_response_object,
)
from app.baidu_map._tool_registry import ToolRegistry, validate_tool_registry
from app.baidu_map.errors import (
    BaiduMapAuthenticationError,
    BaiduMapConfigurationError,
    BaiduMapError,
    BaiduMapProtocolError,
    BaiduMapRateLimitError,
    BaiduMapRequestError,
    BaiduMapResponseError,
    BaiduMapSessionError,
    BaiduMapTimeoutError,
    BaiduMapToolUnavailableError,
    BaiduMapTransportError,
)
from app.baidu_map.limits import BaiduMapLimits
from app.baidu_map.models import (
    BaiduCoordinate,
    CitySearchArea,
    DirectionsRequest,
    DirectionsOutcome,
    GeocodeOutcome,
    GeocodeRequest,
    MapCapability,
    MapCapabilityAvailability,
    MapCapabilityUnavailableReason,
    MapLaunchRequest,
    MapLaunchOutcome,
    MapLaunchRouteRequest,
    MapLaunchSearchRequest,
    NearbySearchArea,
    PlaceDetailsRequest,
    PlaceDetailsOutcome,
    PlaceSearchRequest,
    PlaceSearchOutcome,
    ReverseGeocodeRequest,
    ReverseGeocodeOutcome,
    RouteMatrixRequest,
    RouteMatrixOutcome,
    WeatherRequest,
    WeatherOutcome,
)
from app.baidu_map.settings import BaiduMapSettings
from app.workflow_contracts import UtcClock


_OFFICIAL_ENDPOINT = "https://mcp.map.baidu.com/mcp"
_CORE_CAPABILITIES = frozenset(cap for cap in MapCapability if cap is not MapCapability.MAP_LAUNCH)
_SENSITIVE_NAMESPACES = ("mcp", "httpx", "httpcore")
_FACTORY_LOCK = threading.RLock()
_FACTORY_REFS = 0
_PREVIOUS_FACTORY: Callable[..., logging.LogRecord] | None = None
_INSTALLED_FACTORY: Callable[..., logging.LogRecord] | None = None
_PREVIOUS_CALL_HANDLERS: Callable[[logging.Logger, logging.LogRecord], None] | None = None
_INSTALLED_CALL_HANDLERS: Callable[[logging.Logger, logging.LogRecord], None] | None = None
_PACKAGE_ROOTS: tuple[Path, ...] = ()
_GUARD_FILTER: logging.Filter | None = None
_GUARDED_HANDLERS: tuple[logging.Handler, ...] = ()
_STANDARD_RECORD_KEYS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


def _package_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for name in _SENSITIVE_NAMESPACES:
        spec = importlib.util.find_spec(name)
        locations = None if spec is None else spec.submodule_search_locations
        if locations:
            for location in locations:
                roots.append(Path(location).resolve())
    return tuple(roots)


def _is_under(pathname: str, roots: tuple[Path, ...]) -> bool:
    try:
        path = Path(pathname).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _sensitive_record(record: logging.LogRecord, roots: tuple[Path, ...]) -> bool:
    name = record.name
    return any(name == root or name.startswith(f"{root}.") for root in _SENSITIVE_NAMESPACES) or _is_under(
        record.pathname, roots
    )


def _sanitize_record(record: logging.LogRecord, roots: tuple[Path, ...]) -> None:
    if not _sensitive_record(record, roots):
        return
    record.msg = "[baidu-map-provider-log-redacted]"
    record.args = ()
    record.exc_info = None
    record.stack_info = None
    for key in tuple(record.__dict__):
        if key not in _STANDARD_RECORD_KEYS:
            del record.__dict__[key]


class _ProviderLogFilter(logging.Filter):
    def __init__(self, roots: tuple[Path, ...]) -> None:
        super().__init__()
        self._roots = roots

    def filter(self, record: logging.LogRecord) -> bool:
        _sanitize_record(record, self._roots)
        return True


def _acquire_log_guard() -> None:
    global _FACTORY_REFS, _PREVIOUS_FACTORY, _INSTALLED_FACTORY, _PACKAGE_ROOTS
    global _PREVIOUS_CALL_HANDLERS, _INSTALLED_CALL_HANDLERS
    global _GUARD_FILTER, _GUARDED_HANDLERS
    with _FACTORY_LOCK:
        if _FACTORY_REFS:
            _FACTORY_REFS += 1
            return
        previous = logging.getLogRecordFactory()
        roots = _package_roots()

        def guarded_factory(*args: object, **kwargs: object) -> logging.LogRecord:
            record = previous(*args, **kwargs)
            _sanitize_record(record, roots)
            return record

        previous_call_handlers = logging.Logger.callHandlers

        def guarded_call_handlers(
            logger: logging.Logger, record: logging.LogRecord
        ) -> None:
            # Logger.makeRecord merges caller-supplied ``extra`` after the
            # record factory runs.  Sanitize again at the last common point
            # before any handler filter or formatter can observe the record.
            _sanitize_record(record, roots)
            previous_call_handlers(logger, record)

        _PREVIOUS_FACTORY = previous
        _INSTALLED_FACTORY = guarded_factory
        _PREVIOUS_CALL_HANDLERS = previous_call_handlers
        _INSTALLED_CALL_HANDLERS = guarded_call_handlers
        _PACKAGE_ROOTS = roots
        guard_filter = _ProviderLogFilter(roots)
        handlers: list[logging.Handler] = list(logging.getLogger().handlers)
        for value in logging.Logger.manager.loggerDict.values():
            if isinstance(value, logging.Logger):
                handlers.extend(value.handlers)
        unique_handlers = tuple(dict.fromkeys(handlers))
        for handler in unique_handlers:
            handler.addFilter(guard_filter)
        _GUARD_FILTER = guard_filter
        _GUARDED_HANDLERS = unique_handlers
        logging.setLogRecordFactory(guarded_factory)
        logging.Logger.callHandlers = guarded_call_handlers  # type: ignore[assignment]
        _FACTORY_REFS = 1


def _release_log_guard() -> None:
    global _FACTORY_REFS, _PREVIOUS_FACTORY, _INSTALLED_FACTORY, _PACKAGE_ROOTS
    global _PREVIOUS_CALL_HANDLERS, _INSTALLED_CALL_HANDLERS
    global _GUARD_FILTER, _GUARDED_HANDLERS
    with _FACTORY_LOCK:
        if _FACTORY_REFS <= 0:
            return
        _FACTORY_REFS -= 1
        if _FACTORY_REFS:
            return
        if logging.getLogRecordFactory() is _INSTALLED_FACTORY and _PREVIOUS_FACTORY is not None:
            logging.setLogRecordFactory(_PREVIOUS_FACTORY)
        if (
            logging.Logger.callHandlers is _INSTALLED_CALL_HANDLERS
            and _PREVIOUS_CALL_HANDLERS is not None
        ):
            logging.Logger.callHandlers = _PREVIOUS_CALL_HANDLERS  # type: ignore[assignment]
        if _GUARD_FILTER is not None:
            for handler in _GUARDED_HANDLERS:
                handler.removeFilter(_GUARD_FILTER)
        _PREVIOUS_FACTORY = None
        _INSTALLED_FACTORY = None
        _PREVIOUS_CALL_HANDLERS = None
        _INSTALLED_CALL_HANDLERS = None
        _PACKAGE_ROOTS = ()
        _GUARD_FILTER = None
        _GUARDED_HANDLERS = ()


class _McpSession(Protocol):
    async def initialize(self) -> object: ...

    async def list_tools(self) -> ListToolsResult: ...

    async def send_request(
        self,
        request: ClientRequest,
        result_type: type[CallToolResult],
        request_read_timeout_seconds: timedelta | None = None,
    ) -> CallToolResult: ...


_ClientFactory = Callable[[], AbstractAsyncContextManager[_McpSession]]


def _build_endpoint(settings: BaiduMapSettings) -> str:
    return f"{_OFFICIAL_ENDPOINT}?{urlencode({'ak': settings.api_key.get_secret_value()})}"


@asynccontextmanager
async def _production_client_context(
    *, settings: BaiduMapSettings, limits: BaiduMapLimits
) -> AsyncIterator[_McpSession]:
    endpoint = _build_endpoint(settings)
    timeout = httpx.Timeout(
        connect=limits.connect_timeout_s,
        read=None,
        write=limits.write_timeout_s,
        pool=limits.pool_timeout_s,
    )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as http_client:
        async with streamable_http_client(endpoint, http_client=http_client) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                yield session
    endpoint = ""


def _fresh_request(model: type[BaseModel], value: object) -> BaseModel | None:
    if type(value) is not model:
        return None
    failed = False
    fresh: BaseModel | None = None
    try:
        fresh = model.model_validate(value.model_dump(mode="python"), strict=True)
    except (ValidationError, ValueError, TypeError, AttributeError):
        failed = True
    if failed or type(fresh) is not model:
        return None
    return fresh


def _format_number(value: float) -> str:
    rendered = format(Decimal(str(value)), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in ("", "-0"):
        return "0"
    return rendered


def _coordinate_text(coordinate: BaiduCoordinate, *, weather: bool = False) -> str:
    first = coordinate.longitude if weather else coordinate.latitude
    second = coordinate.latitude if weather else coordinate.longitude
    return f"{_format_number(first)},{_format_number(second)}"


def _classify_transport(exception: BaseException) -> type[BaiduMapError]:
    if isinstance(exception, McpError):
        return BaiduMapProtocolError
    if isinstance(exception, httpx.HTTPStatusError):
        status = exception.response.status_code
        if status in (401, 403):
            return BaiduMapAuthenticationError
        if status == 429:
            return BaiduMapRateLimitError
    if isinstance(exception, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return BaiduMapTimeoutError
    return BaiduMapTransportError


class _OwnerReady:
    __slots__ = ("session", "registry", "error_type", "error_capability", "transient")

    def __init__(
        self,
        *,
        session: _McpSession | None = None,
        registry: ToolRegistry | None = None,
        error_type: type[BaiduMapError] | None = None,
        error_capability: MapCapability | None = None,
        transient: bool = False,
    ) -> None:
        self.session = session
        self.registry = registry
        self.error_type = error_type
        self.error_capability = error_capability
        self.transient = transient


async def _run_client_owner(
    *,
    factory: _ClientFactory,
    limits: BaiduMapLimits,
    ready: asyncio.Future[_OwnerReady],
    close_requested: asyncio.Event,
    exit_phase_started: asyncio.Event,
    exit_completed: asyncio.Event,
    exit_required: asyncio.Event,
) -> type[BaiduMapError] | None:
    """Enter and exit every task-affine SDK context in one owner task."""
    context: AbstractAsyncContextManager[_McpSession] | None = None
    session: _McpSession | None = None
    registry: ToolRegistry | None = None
    entered = False
    published = False
    cancelled = False
    close_error: type[BaiduMapError] | None = None
    startup_error: type[BaiduMapError] | None = None
    startup_capability: MapCapability | None = None
    transient = False
    try:
        context = factory()
        session = await context.__aenter__()
        entered = True
        async with asyncio.timeout(limits.initialize_timeout_s):
            await session.initialize()
        async with asyncio.timeout(limits.list_tools_timeout_s):
            listed = await session.list_tools()
        registry = validate_tool_registry(
            listed.tools, next_cursor=getattr(listed, "nextCursor", None)
        )
        # The provider response and schemas are discovery-only material.  Do
        # not let the long-lived owner task frame retain them while waiting for
        # the private close signal.
        listed = None
        if not ready.done():
            ready.set_result(_OwnerReady(session=session, registry=registry))
        published = True
        await close_requested.wait()
    except asyncio.CancelledError:
        cancelled = True
    except BaiduMapError as error:
        if published:
            close_error = BaiduMapSessionError
        else:
            startup_error = type(error)
            startup_capability = error.capability
    except BaseException as error:
        if published:
            close_error = BaiduMapSessionError
        else:
            transient = isinstance(
                error,
                (
                    TimeoutError,
                    asyncio.TimeoutError,
                    httpx.TimeoutException,
                    httpx.ConnectError,
                    OSError,
                ),
            )
            startup_error = _classify_transport(error)
    finally:
        if entered and context is not None:
            exit_required.set()
            exit_phase_started.set()
            try:
                await context.__aexit__(None, None, None)
            except asyncio.CancelledError:
                cancelled = True
            except BaseException:
                if published:
                    close_error = BaiduMapSessionError
                exit_completed.set()
            else:
                exit_completed.set()
        if not published and not ready.done():
            if cancelled:
                ready.cancel()
            else:
                ready.set_result(
                    _OwnerReady(
                        error_type=startup_error or BaiduMapSessionError,
                        error_capability=startup_capability,
                        transient=transient,
                    )
                )
        context = None
        session = None
        registry = None
    if cancelled:
        raise asyncio.CancelledError
    return close_error


class BaiduMapMcpGateway:
    """Private concrete adapter. The package root exposes only its protocols."""

    def __init__(
        self,
        *,
        session: _McpSession,
        owner_task: asyncio.Task[type[BaiduMapError] | None],
        owner_close_requested: asyncio.Event,
        owner_exit_phase_started: asyncio.Event,
        owner_exit_completed: asyncio.Event,
        registry: ToolRegistry,
        clock: UtcClock,
        limits: BaiduMapLimits,
    ) -> None:
        self._session = session
        self._owner_task = owner_task
        self._owner_close_requested = owner_close_requested
        self._owner_exit_phase_started = owner_exit_phase_started
        self._owner_exit_completed = owner_exit_completed
        self._registry = registry
        self._clock = clock
        self._limits = limits
        self._state = "ready"
        # ``closing`` is an ownership/lifecycle fact; a terminal failure is
        # independent and must remain observable to already-admitted waiters.
        self._broken = False
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(limits.max_in_flight)
        self._in_flight: set[asyncio.Task[object]] = set()
        self._in_flight_empty = asyncio.Event()
        self._in_flight_empty.set()
        self._close_task: asyncio.Task[None] | None = None
        self._close_deadline: float | None = None
        self._closing_started = asyncio.Event()
        self._log_guard_owned = True

    @classmethod
    async def create(
        cls,
        *,
        settings: BaiduMapSettings,
        clock: UtcClock,
        limits: BaiduMapLimits,
        _client_factory: _ClientFactory | None = None,
    ) -> "BaiduMapMcpGateway":
        if (
            type(settings) is not BaiduMapSettings
            or type(limits) is not BaiduMapLimits
            or not callable(getattr(clock, "now", None))
        ):
            raise BaiduMapConfigurationError()
        _acquire_log_guard()
        factory: _ClientFactory
        if _client_factory is None:
            factory = lambda: _production_client_context(settings=settings, limits=limits)
        else:
            factory = _client_factory
        last_error: type[BaiduMapError] | None = None
        last_capability: MapCapability | None = None
        for attempt in range(2):
            ready: asyncio.Future[_OwnerReady] = asyncio.get_running_loop().create_future()
            close_requested = asyncio.Event()
            exit_phase_started = asyncio.Event()
            exit_completed = asyncio.Event()
            exit_required = asyncio.Event()
            owner_task = asyncio.create_task(
                _run_client_owner(
                    factory=factory,
                    limits=limits,
                    ready=ready,
                    close_requested=close_requested,
                    exit_phase_started=exit_phase_started,
                    exit_completed=exit_completed,
                    exit_required=exit_required,
                )
            )
            try:
                outcome = await ready
            except asyncio.CancelledError as cancellation:
                ready.cancel()
                close_requested.set()
                # No await occurs between this phase check and cancel(). The
                # owner therefore cannot start task-affine __aexit__ between
                # the decision and the cancellation injection.
                if not owner_task.done() and not exit_phase_started.is_set():
                    owner_task.cancel()
                # The owner task is the only task allowed to run the
                # task-affine context exit.  Shield each wait so later
                # cancellation requests received by this startup caller do
                # not cancel that owner or replace the original error.
                while True:
                    try:
                        await asyncio.shield(owner_task)
                        break
                    except asyncio.CancelledError:
                        if owner_task.done():
                            break
                        continue
                if exit_required.is_set():
                    while not exit_completed.is_set():
                        try:
                            await exit_completed.wait()
                        except asyncio.CancelledError:
                            continue
                _release_log_guard()
                raise cancellation
            if outcome.session is not None and outcome.registry is not None:
                return cls(
                    session=outcome.session,
                    owner_task=owner_task,
                    owner_close_requested=close_requested,
                    owner_exit_phase_started=exit_phase_started,
                    owner_exit_completed=exit_completed,
                    registry=outcome.registry,
                    clock=clock,
                    limits=limits,
                )
            if exit_required.is_set():
                await exit_completed.wait()
            await asyncio.gather(owner_task, return_exceptions=True)
            last_error = outcome.error_type or BaiduMapSessionError
            last_capability = outcome.error_capability
            if not outcome.transient or attempt == 1:
                break
        _release_log_guard()
        error_type = last_error or BaiduMapSessionError
        error_capability = last_capability
        settings = None  # type: ignore[assignment]
        clock = None  # type: ignore[assignment]
        limits = None  # type: ignore[assignment]
        factory = None  # type: ignore[assignment]
        last_error = None
        last_capability = None
        raise error_type(capability=error_capability)

    async def _admit(self) -> asyncio.Task[object] | None:
        task = asyncio.current_task()
        if task is None:
            return None
        typed_task: asyncio.Task[object] = task  # type: ignore[assignment]
        async with self._lock:
            if self._state != "ready":
                return None
            if typed_task in self._in_flight:
                return None
            self._in_flight.add(typed_task)
            self._in_flight_empty.clear()
        return typed_task

    async def _finish(
        self,
        task: asyncio.Task[object],
        *,
        release_permit: bool = False,
        mark_broken: bool = False,
    ) -> None:
        async with self._lock:
            if mark_broken:
                self._broken = True
                if self._state == "ready":
                    self._state = "broken"
            if release_permit:
                self._semaphore.release()
            self._in_flight.discard(task)
            if not self._in_flight:
                self._in_flight_empty.set()

    async def _invoke(
        self,
        *,
        capability: MapCapability,
        request: BaseModel,
        arguments: dict[str, object],
        timeout_s: float,
        parser: Callable[[object, Any, Any], object],
        started: float,
    ) -> object:
        admitted = await self._admit()
        if admitted is None:
            arguments.clear()
            arguments = None  # type: ignore[assignment]
            request = None  # type: ignore[assignment]
            raise BaiduMapSessionError(capability=capability)
        acquired = False
        sent = False
        public_error: type[BaiduMapError] | None = None
        result_value: object | None = None
        params: CallToolRequestParams | None = None
        outbound: ClientRequest | None = None
        result: CallToolResult | None = None
        selected: dict[str, Any] | None = None
        cancellation: asyncio.CancelledError | None = None
        break_session = False
        try:
            remaining = timeout_s - (time.monotonic() - started)
            if remaining <= 0:
                raise asyncio.TimeoutError
            async with asyncio.timeout(remaining):
                await self._semaphore.acquire()
            acquired = True
            async with self._lock:
                if self._broken or self._state in {"broken", "closed"}:
                    raise BaiduMapSessionError()
            remaining = timeout_s - (time.monotonic() - started)
            if remaining <= 0:
                raise asyncio.TimeoutError
            params = CallToolRequestParams(
                name=self._registry.for_capability(capability).tool_name,
                arguments=arguments,
            )
            outbound = ClientRequest(root=CallToolRequest(params=params))
            sent = True
            async with asyncio.timeout(remaining):
                result = await self._session.send_request(
                    outbound,
                    CallToolResult,
                    request_read_timeout_seconds=timedelta(seconds=remaining),
                )
            selected = select_response_object(result, capability)
            result_value = parser(selected, request, self._clock.now())
            if time.monotonic() - started > timeout_s:
                raise asyncio.TimeoutError
        except asyncio.CancelledError as error:
            cancellation = error
            break_session = sent
            cancellation.__traceback__ = None
        except BaiduMapRateLimitError:
            public_error = BaiduMapRateLimitError
        except BaiduMapError as error:
            public_error = type(error)
            break_session = sent
        except BaseException as error:
            public_error = _classify_transport(error)
            break_session = sent and public_error is not BaiduMapRateLimitError
        finally:
            arguments.clear()
            arguments = None  # type: ignore[assignment]
            request = None  # type: ignore[assignment]
            params = None
            outbound = None
            result = None
            selected = None
            if cancellation is not None or public_error is not None:
                result_value = None
            while True:
                try:
                    await self._finish(
                        admitted,
                        release_permit=acquired,
                        mark_broken=break_session,
                    )
                    break
                except asyncio.CancelledError as cleanup_cancellation:
                    if cancellation is None:
                        cancellation = cleanup_cancellation
                    break_session = break_session or sent
        if cancellation is not None:
            raise cancellation
        if public_error is not None:
            arguments = None  # type: ignore[assignment]
            request = None  # type: ignore[assignment]
            params = None
            outbound = None
            result = None
            selected = None
            result_value = None
            raise public_error(capability=capability)
        if result_value is None:
            raise BaiduMapResponseError(capability=capability)
        return result_value

    async def geocode(self, request: GeocodeRequest) -> GeocodeOutcome:
        started = time.monotonic()
        capability = MapCapability.GEOCODING
        fresh = _fresh_request(GeocodeRequest, request)
        if fresh is None:
            request = None  # type: ignore[assignment]
            raise BaiduMapRequestError(capability=capability)
        assert type(fresh) is GeocodeRequest
        arguments: dict[str, object] = {
            "address": fresh.address,
            "is_chinese_mainland": "true",
        }
        if fresh.city is not None:
            arguments["city"] = fresh.city
        error_type: type[BaiduMapError] | None = None
        outcome: object | None = None
        invoke = self._invoke(
            capability=capability, request=fresh, arguments=arguments,
            timeout_s=self._limits.geocode_timeout_s, parser=parse_geocode, started=started,
        )
        request = None  # type: ignore[assignment]
        fresh = None
        arguments = None  # type: ignore[assignment]
        try:
            outcome = await invoke
        except asyncio.CancelledError:
            invoke = None
            raise
        except BaiduMapError as error:
            error_type = type(error)
        invoke = None
        if error_type is not None:
            raise error_type(capability=capability)
        return cast(GeocodeOutcome, outcome)

    async def reverse_geocode(self, request: ReverseGeocodeRequest) -> ReverseGeocodeOutcome:
        started = time.monotonic()
        capability = MapCapability.REVERSE_GEOCODING
        fresh = _fresh_request(ReverseGeocodeRequest, request)
        if fresh is None:
            request = None  # type: ignore[assignment]
            raise BaiduMapRequestError(capability=capability)
        assert type(fresh) is ReverseGeocodeRequest
        arguments: dict[str, object] = {
            "latitude": fresh.location.latitude, "longitude": fresh.location.longitude
        }
        error_type: type[BaiduMapError] | None = None
        outcome: object | None = None
        invoke = self._invoke(
            capability=capability, request=fresh, arguments=arguments,
            timeout_s=self._limits.reverse_geocode_timeout_s,
            parser=parse_reverse_geocode, started=started,
        )
        request = None  # type: ignore[assignment]
        fresh = None
        arguments = None  # type: ignore[assignment]
        try:
            outcome = await invoke
        except asyncio.CancelledError:
            invoke = None
            raise
        except BaiduMapError as error:
            error_type = type(error)
        invoke = None
        if error_type is not None:
            raise error_type(capability=capability)
        return cast(ReverseGeocodeOutcome, outcome)

    async def search_places(self, request: PlaceSearchRequest) -> PlaceSearchOutcome:
        started = time.monotonic()
        capability = MapCapability.PLACE_SEARCH
        fresh = _fresh_request(PlaceSearchRequest, request)
        if fresh is None:
            request = None  # type: ignore[assignment]
            raise BaiduMapRequestError(capability=capability)
        assert type(fresh) is PlaceSearchRequest
        arguments: dict[str, object] = {
            "query": fresh.query,
            "is_chinese_mainland": "true",
        }
        if fresh.tag is not None:
            arguments["tag"] = fresh.tag
        if type(fresh.area) is CitySearchArea:
            arguments["region"] = fresh.area.region
        elif type(fresh.area) is NearbySearchArea:
            arguments["location"] = _coordinate_text(fresh.area.center)
            arguments["radius"] = fresh.area.radius_m
        else:
            raise BaiduMapRequestError(capability=capability)
        binding = self._registry.for_capability(capability)
        if fresh.result_limit is not None and binding.supports_result_limit:
            arguments["limit"] = fresh.result_limit
        error_type: type[BaiduMapError] | None = None
        outcome: object | None = None
        invoke = self._invoke(
            capability=capability, request=fresh, arguments=arguments,
            timeout_s=self._limits.place_search_timeout_s, parser=parse_place_search,
            started=started,
        )
        request = None  # type: ignore[assignment]
        fresh = None
        arguments = None  # type: ignore[assignment]
        try:
            outcome = await invoke
        except asyncio.CancelledError:
            invoke = None
            raise
        except BaiduMapError as error:
            error_type = type(error)
        invoke = None
        if error_type is not None:
            raise error_type(capability=capability)
        return cast(PlaceSearchOutcome, outcome)

    async def get_place_details(self, request: PlaceDetailsRequest) -> PlaceDetailsOutcome:
        started = time.monotonic()
        capability = MapCapability.PLACE_DETAILS
        fresh = _fresh_request(PlaceDetailsRequest, request)
        if fresh is None:
            request = None  # type: ignore[assignment]
            raise BaiduMapRequestError(capability=capability)
        assert type(fresh) is PlaceDetailsRequest
        arguments: dict[str, object] = {"uid": fresh.uid, "is_chinese_mainland": "true"}
        error_type: type[BaiduMapError] | None = None
        outcome: object | None = None
        invoke = self._invoke(
            capability=capability, request=fresh, arguments=arguments,
            timeout_s=self._limits.place_details_timeout_s, parser=parse_place_details,
            started=started,
        )
        request = None  # type: ignore[assignment]
        fresh = None
        arguments = None  # type: ignore[assignment]
        try:
            outcome = await invoke
        except asyncio.CancelledError:
            invoke = None
            raise
        except BaiduMapError as error:
            error_type = type(error)
        invoke = None
        if error_type is not None:
            raise error_type(capability=capability)
        return cast(PlaceDetailsOutcome, outcome)

    async def calculate_route_matrix(self, request: RouteMatrixRequest) -> RouteMatrixOutcome:
        started = time.monotonic()
        capability = MapCapability.ROUTE_MATRIX
        fresh = _fresh_request(RouteMatrixRequest, request)
        if fresh is None:
            request = None  # type: ignore[assignment]
            raise BaiduMapRequestError(capability=capability)
        assert type(fresh) is RouteMatrixRequest
        arguments: dict[str, object] = {
            "model": fresh.mode.value,
            "origins": "|".join(_coordinate_text(item) for item in fresh.origins),
            "destinations": "|".join(_coordinate_text(item) for item in fresh.destinations),
        }
        error_type: type[BaiduMapError] | None = None
        outcome: object | None = None
        invoke = self._invoke(
            capability=capability, request=fresh, arguments=arguments,
            timeout_s=self._limits.route_matrix_timeout_s, parser=parse_route_matrix,
            started=started,
        )
        request = None  # type: ignore[assignment]
        fresh = None
        arguments = None  # type: ignore[assignment]
        try:
            outcome = await invoke
        except asyncio.CancelledError:
            invoke = None
            raise
        except BaiduMapError as error:
            error_type = type(error)
        invoke = None
        if error_type is not None:
            raise error_type(capability=capability)
        return cast(RouteMatrixOutcome, outcome)

    async def plan_directions(self, request: DirectionsRequest) -> DirectionsOutcome:
        started = time.monotonic()
        capability = MapCapability.DIRECTIONS
        fresh = _fresh_request(DirectionsRequest, request)
        if fresh is None:
            request = None  # type: ignore[assignment]
            raise BaiduMapRequestError(capability=capability)
        assert type(fresh) is DirectionsRequest
        arguments: dict[str, object] = {
            "model": fresh.mode.value,
            "origin": _coordinate_text(fresh.origin.coordinate),
            "destination": _coordinate_text(fresh.destination.coordinate),
            "is_chinese_mainland": "true",
        }
        error_type: type[BaiduMapError] | None = None
        outcome: object | None = None
        invoke = self._invoke(
            capability=capability, request=fresh, arguments=arguments,
            timeout_s=self._limits.directions_timeout_s, parser=parse_directions,
            started=started,
        )
        request = None  # type: ignore[assignment]
        fresh = None
        arguments = None  # type: ignore[assignment]
        try:
            outcome = await invoke
        except asyncio.CancelledError:
            invoke = None
            raise
        except BaiduMapError as error:
            error_type = type(error)
        invoke = None
        if error_type is not None:
            raise error_type(capability=capability)
        return cast(DirectionsOutcome, outcome)

    async def get_weather(self, request: WeatherRequest) -> WeatherOutcome:
        started = time.monotonic()
        capability = MapCapability.WEATHER
        fresh = _fresh_request(WeatherRequest, request)
        if fresh is None:
            request = None  # type: ignore[assignment]
            raise BaiduMapRequestError(capability=capability)
        assert type(fresh) is WeatherRequest
        arguments: dict[str, object] = {
            "location": _coordinate_text(fresh.location, weather=True), "is_china": "true"
        }
        error_type: type[BaiduMapError] | None = None
        outcome: object | None = None
        invoke = self._invoke(
            capability=capability, request=fresh, arguments=arguments,
            timeout_s=self._limits.weather_timeout_s, parser=parse_weather, started=started,
        )
        request = None  # type: ignore[assignment]
        fresh = None
        arguments = None  # type: ignore[assignment]
        try:
            outcome = await invoke
        except asyncio.CancelledError:
            invoke = None
            raise
        except BaiduMapError as error:
            error_type = type(error)
        invoke = None
        if error_type is not None:
            raise error_type(capability=capability)
        return cast(WeatherOutcome, outcome)

    def get_availability(self) -> MapCapabilityAvailability:
        return self.get_capability_availability(capability=MapCapability.MAP_LAUNCH)

    def get_capability_availability(
        self, *, capability: MapCapability
    ) -> MapCapabilityAvailability:
        if type(capability) is not MapCapability:
            raise BaiduMapRequestError()
        if capability in _CORE_CAPABILITIES:
            return MapCapabilityAvailability(capability=capability, available=True)
        return MapCapabilityAvailability(
            capability=MapCapability.MAP_LAUNCH,
            available=False,
            reason_code=MapCapabilityUnavailableReason.NOT_IN_MANIFEST,
        )

    async def create_map_launch(self, request: MapLaunchRequest) -> MapLaunchOutcome:
        # The public request is a discriminated union, so validate its exact concrete member first.
        if type(request) not in (MapLaunchRouteRequest, MapLaunchSearchRequest):
            request = None  # type: ignore[assignment]
            raise BaiduMapRequestError(capability=MapCapability.MAP_LAUNCH)
        fresh: MapLaunchRouteRequest | MapLaunchSearchRequest | None = None
        failed = False
        try:
            fresh = type(request).model_validate(request.model_dump(mode="python"), strict=True)
        except asyncio.CancelledError:
            request = None  # type: ignore[assignment]
            fresh = None
            raise
        except (ValidationError, ValueError, TypeError, AttributeError):
            failed = True
        if failed:
            request = None  # type: ignore[assignment]
            fresh = None
            raise BaiduMapRequestError(capability=MapCapability.MAP_LAUNCH)
        try:
            async with self._lock:
                ready = self._state == "ready"
        except asyncio.CancelledError:
            request = None  # type: ignore[assignment]
            fresh = None
            raise
        if not ready:
            request = None  # type: ignore[assignment]
            fresh = None
            raise BaiduMapSessionError(capability=MapCapability.MAP_LAUNCH)
        request = None  # type: ignore[assignment]
        fresh = None
        raise BaiduMapToolUnavailableError(capability=MapCapability.MAP_LAUNCH)

    @staticmethod
    def _remaining_until(deadline: float) -> float:
        return max(0.0, deadline - time.monotonic())

    async def _wait_in_flight_until(self, deadline: float) -> bool:
        remaining = self._remaining_until(deadline)
        if remaining <= 0:
            return self._in_flight_empty.is_set()
        try:
            async with asyncio.timeout(remaining):
                await self._in_flight_empty.wait()
        except asyncio.TimeoutError:
            return False
        return True

    async def _in_flight_tasks(self) -> tuple[asyncio.Task[object], ...]:
        async with self._lock:
            return tuple(self._in_flight)

    @staticmethod
    def _consume_task_result(task: asyncio.Task[object]) -> None:
        try:
            task.result()
        except BaseException:
            pass

    async def _cancel_and_join_resources(self) -> None:
        tasks = await self._in_flight_tasks()
        self._owner_close_requested.set()
        for task in tasks:
            task.cancel()
        # The phase flag is set synchronously in the owner immediately before
        # its first await of __aexit__.  There is no await between this check
        # and cancel(), so the deadline cannot race into an already-started
        # task-affine exit.  A single cancellation before that phase lets the
        # owner enter its own finally block and finish the exit naturally.
        if (
            not self._owner_exit_phase_started.is_set()
            and not self._owner_task.done()
        ):
            self._owner_task.cancel()

        # A fixed scheduling checkpoint lets cooperative business calls enter
        # their cancellation cleanup before the bounded second request.  The
        # owner is deliberately not cancelled again: it may now be in
        # __aexit__, which must remain task-affine and cancellation-free.
        await asyncio.sleep(0)
        for task in tasks:
            if not task.done():
                task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(self._owner_task, return_exceptions=True)
        await self._in_flight_empty.wait()
        await self._owner_exit_completed.wait()

    async def _close_resources(self) -> None:
        self._owner_close_requested.set()
        error_type: type[BaiduMapError] | None = None
        try:
            owner_error = await asyncio.shield(self._owner_task)
            await self._owner_exit_completed.wait()
            if owner_error is not None:
                error_type = owner_error
        except asyncio.CancelledError:
            if self._owner_task.cancelled():
                error_type = BaiduMapSessionError
            else:
                raise
        except BaseException:
            error_type = BaiduMapSessionError
        if error_type is not None:
            raise error_type()

    async def _close_impl(self) -> None:
        close_error: type[BaiduMapError] | None = None
        timed_out = False
        deadline = self._close_deadline
        if deadline is None:
            close_error = BaiduMapSessionError
            deadline = time.monotonic()
        try:
            if close_error is None and not await self._wait_in_flight_until(deadline):
                timed_out = True
            elif close_error is None:
                remaining = self._remaining_until(deadline)
                if remaining <= 0:
                    timed_out = True
                else:
                    try:
                        async with asyncio.timeout(remaining):
                            await self._close_resources()
                    except asyncio.TimeoutError:
                        timed_out = True
                    except BaseException:
                        close_error = BaiduMapSessionError
        except asyncio.CancelledError:
            raise
        except BaseException:
            close_error = BaiduMapSessionError

        if timed_out or (
            close_error is not None and not self._owner_exit_completed.is_set()
        ):
            await self._cancel_and_join_resources()
            if time.monotonic() >= deadline:
                timed_out = True

        # _in_flight_empty is set under the same lock only after every call
        # has released its semaphore permit and removed itself.  The explicit
        # completion fact below is separate from owner task completion and
        # covers the task-affine context exit itself.
        await self._in_flight_empty.wait()
        await self._owner_exit_completed.wait()
        await asyncio.gather(self._owner_task, return_exceptions=True)
        async with self._lock:
            self._state = "closed"
        if self._log_guard_owned:
            self._log_guard_owned = False
            _release_log_guard()

        if timed_out:
            raise BaiduMapTimeoutError()
        if close_error is not None:
            raise close_error()

    async def aclose(self) -> None:
        async with self._lock:
            if self._state == "closed":
                return
            if self._close_task is None:
                self._state = "closing"
                self._closing_started.set()
                self._close_deadline = time.monotonic() + self._limits.close_timeout_s
                self._close_task = asyncio.create_task(self._close_impl())
                self._close_task.add_done_callback(self._consume_close_task_exception)
            task = self._close_task
            deadline = self._close_deadline
        assert deadline is not None
        if task.done():
            await asyncio.shield(task)
            return
        remaining = self._remaining_until(deadline)
        timed_out = remaining <= 0
        if not timed_out:
            try:
                async with asyncio.timeout(remaining):
                    await asyncio.shield(task)
            except asyncio.TimeoutError:
                timed_out = True
        if timed_out:
            raise BaiduMapTimeoutError()

    @staticmethod
    def _consume_close_task_exception(task: asyncio.Task[None]) -> None:
        """Retrieve an orphaned close failure without changing await semantics."""
        try:
            task.exception()
        except asyncio.CancelledError:
            pass


__all__ = ("BaiduMapMcpGateway",)
