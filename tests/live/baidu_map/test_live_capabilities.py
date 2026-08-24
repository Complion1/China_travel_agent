"""LIVE-QUALIFICATION probe; T05 only performs collect-only, without settings, Secret, or network access."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from functools import wraps
import importlib
import math
import re
from typing import Any
from unittest.mock import patch

import pytest


_ASCII_DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", re.ASCII)


def _ascii_decimal_string(value: object) -> bool:
    return type(value) is str and _ASCII_DECIMAL.fullmatch(value) is not None


def _json_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


class _LiveSdkBoundaryObserver:
    """Only fixed protocol facts; no requests, arguments, responses, or Secret."""

    def __init__(self) -> None:
        self.initialize_count = 0
        self.tools_list_count = 0
        self.call_tool_request_count = 0
        self.call_tool_names: list[str] = []

    def record_initialize(self) -> None:
        self.initialize_count += 1

    def record_tools_list(self) -> None:
        self.tools_list_count += 1

    def record_call_tool(self, name: str) -> None:
        # The registry name is the only SDK fact retained by this observer.
        self.call_tool_names.append(name)


class _ObservedMcpSession:
    def __init__(self, delegate: Any, observer: _LiveSdkBoundaryObserver) -> None:
        self._delegate = delegate
        self._observer = observer

    async def initialize(self) -> object:
        self._observer.record_initialize()
        return await self._delegate.initialize()

    async def list_tools(self) -> object:
        self._observer.record_tools_list()
        return await self._delegate.list_tools()

    async def send_request(
        self,
        request: Any,
        result_type: type[Any],
        request_read_timeout_seconds: timedelta | None = None,
    ) -> object:
        # Read only ClientRequest.root.params.name; never touch arguments or content.
        from mcp.types import CallToolRequest

        if type(request.root) is not CallToolRequest:
            raise AssertionError("unexpected non-tool SDK request")
        self._observer.call_tool_request_count += 1
        self._observer.record_call_tool(request.root.params.name)
        return await self._delegate.send_request(
            request,
            result_type,
            request_read_timeout_seconds=request_read_timeout_seconds,
        )


_DIRECTION_MODES = ("walking", "driving", "riding", "transit")


class _DirectionsModeObservation:
    """Fixed, per-mode conclusions; never retains a provider object or scalar."""

    def __init__(self) -> None:
        self.parser_completed = False
        self.raw_routes_nonempty = False
        self.all_raw_routes_observed = False
        self.instruction_singular = False
        self.raw_steps_nesting = False
        self.raw_coordinate_category = False
        self.public_result_nonempty = False
        self.raw_all_validated_before_filtering = False
        self.blank_instruction_seen = False
        self.blank_filtering_applied = False


class _LiveStructureObserver:
    """Transient booleans for selected raw structure; production parsers remain authoritative."""

    def __init__(self) -> None:
        self.reverse_adcode_path_exists = False
        self.reverse_adcode_ascii_decimal_string = False
        self.reverse_parser_completed = False
        self.reverse_raw_all_validated = False
        self.reverse_provider_addr_point_shape = False
        self.reverse_point_xy_json_number = False
        self.reverse_public_at_most_twenty = False
        self.reverse_public_matches_first_twenty = False
        self.directions = {mode: _DirectionsModeObservation() for mode in _DIRECTION_MODES}
        self.weather_raw_at_most_seven = False
        self.weather_raw_all_validated_before_truncation = False
        self.weather_parser_completed = False

    @staticmethod
    def _raw_step_fields_present(step: object) -> bool:
        if type(step) is not dict:
            return False
        return all(
            field in step
            for field in ("instruction", "distance", "duration", "start_location", "end_location")
        )

    @staticmethod
    def _raw_step_coordinates(step: object, *, decimal_strings: bool) -> bool:
        if type(step) is not dict:
            return False
        for field in ("start_location", "end_location"):
            location = step.get(field)
            if type(location) is not dict:
                return False
            latitude = location.get("lat")
            longitude = location.get("lng")
            if decimal_strings:
                if not (_ascii_decimal_string(latitude) and _ascii_decimal_string(longitude)):
                    return False
            elif not (_json_number(latitude) and _json_number(longitude)):
                return False
        return True

    def observe_reverse(self, raw: object, outcome: object) -> None:
        root = raw if type(raw) is dict else None
        result = root.get("result") if type(root) is dict else None
        component = result.get("addressComponent") if type(result) is dict else None
        path_exists = type(component) is dict and "adcode" in component
        self.reverse_adcode_path_exists = path_exists
        value = component.get("adcode") if path_exists else None
        self.reverse_adcode_ascii_decimal_string = _ascii_decimal_string(value)
        self.reverse_parser_completed = True
        self.reverse_raw_all_validated = True
        pois = result.get("pois") if type(result) is dict else None
        if type(pois) is not list:
            return
        qualified: list[dict[str, object]] = []
        provider_shape = True
        point_numbers = True
        for item in pois:
            if type(item) is not dict:
                provider_shape = False
                point_numbers = False
                continue
            if any(
                field not in item or item[field] is None
                for field in ("uid", "name", "addr", "point")
            ):
                continue
            point = item["point"]
            if type(point) is not dict:
                provider_shape = False
                point_numbers = False
                continue
            if any(field not in point or point[field] is None for field in ("x", "y")):
                continue
            provider_shape = provider_shape and all(
                type(item[field]) is str and bool(item[field].strip())
                for field in ("uid", "name", "addr")
            )
            point_numbers = point_numbers and _json_number(point["x"]) and _json_number(point["y"])
            qualified.append(item)
        self.reverse_provider_addr_point_shape = provider_shape and bool(qualified)
        self.reverse_point_xy_json_number = point_numbers and bool(qualified)
        public = getattr(outcome, "nearby_places", None)
        self.reverse_public_at_most_twenty = type(public) is tuple and len(public) <= 20
        expected = qualified[:20]
        if type(public) is not tuple or len(public) != len(expected):
            return
        matches = True
        for public_item, provider_item in zip(public, expected):
            point = provider_item["point"]
            assert type(point) is dict
            provider_tag = provider_item.get("tag")
            expected_category = (
                provider_tag.strip()
                if type(provider_tag) is str and provider_tag.strip()
                else None
            )
            matches = matches and (
                public_item.uid == provider_item["uid"].strip()
                and public_item.name == provider_item["name"].strip()
                and public_item.address == provider_item["addr"].strip()
                and public_item.coordinate.latitude == float(point["y"])
                and public_item.coordinate.longitude == float(point["x"])
                and public_item.category == expected_category
            )
        self.reverse_public_matches_first_twenty = matches

    def _observe_route(self, route: object, *, transit: bool) -> tuple[bool, bool, bool, bool, bool, bool]:
        if type(route) is not dict:
            return False, False, False, False, False, False
        steps = route.get("steps")
        if type(steps) is not list or not steps:
            return False, False, False, False, False, False
        route_observed = True
        nesting_ok = True
        instruction_ok = True
        coordinates_ok = True
        fields_ok = True
        blank_seen = False
        saw_step = False

        def observe_step(step: object) -> None:
            nonlocal route_observed, instruction_ok, coordinates_ok, fields_ok
            nonlocal blank_seen, saw_step
            saw_step = True
            if type(step) is not dict:
                route_observed = False
                instruction_ok = False
                coordinates_ok = False
                fields_ok = False
                return
            fields_ok = fields_ok and self._raw_step_fields_present(step)
            instruction_ok = (
                instruction_ok
                and "instruction" in step
                and "instructions" not in step
            )
            coordinates_ok = coordinates_ok and self._raw_step_coordinates(
                step, decimal_strings=not transit
            )
            instruction = step.get("instruction")
            if type(instruction) is str and not instruction.strip():
                blank_seen = True

        if transit:
            for group in steps:
                if type(group) is not list or not group:
                    route_observed = False
                    nesting_ok = False
                    continue
                for step in group:
                    observe_step(step)
        else:
            if not all(type(step) is dict for step in steps):
                route_observed = False
                nesting_ok = False
            for step in steps:
                observe_step(step)
        if not saw_step:
            route_observed = False
            nesting_ok = False
            instruction_ok = False
            coordinates_ok = False
            fields_ok = False
        return (
            route_observed,
            nesting_ok,
            instruction_ok,
            coordinates_ok,
            fields_ok,
            blank_seen,
        )

    def observe_directions(self, raw: object, request: Any, outcome: Any) -> None:
        mode = request.mode.value
        state = self.directions.get(mode)
        if state is None:
            return
        state.parser_completed = True
        root = raw if type(raw) is dict else None
        result = root.get("result") if type(root) is dict else None
        routes = result.get("routes") if type(result) is dict else None
        state.raw_routes_nonempty = type(routes) is list and bool(routes)
        if not state.raw_routes_nonempty:
            return
        all_routes_observed = True
        all_nesting_ok = True
        all_instruction_ok = True
        all_coordinates_ok = True
        all_fields_ok = True
        blank_seen = False
        for route in routes:
            (
                route_observed,
                nesting_ok,
                instruction_ok,
                coordinates_ok,
                fields_ok,
                route_blank_seen,
            ) = self._observe_route(route, transit=mode == "transit")
            all_routes_observed = all_routes_observed and route_observed
            all_nesting_ok = all_nesting_ok and nesting_ok
            all_instruction_ok = all_instruction_ok and instruction_ok
            all_coordinates_ok = all_coordinates_ok and coordinates_ok
            all_fields_ok = all_fields_ok and fields_ok
            blank_seen = blank_seen or route_blank_seen
        state.all_raw_routes_observed = all_routes_observed
        state.raw_steps_nesting = all_routes_observed and all_nesting_ok
        state.instruction_singular = all_routes_observed and all_instruction_ok
        state.raw_coordinate_category = all_routes_observed and all_coordinates_ok
        state.blank_instruction_seen = blank_seen
        state.raw_all_validated_before_filtering = (
            mode == "transit"
            and state.parser_completed
            and all_routes_observed
            and all_fields_ok
        )
        public_alternatives = getattr(outcome, "alternatives", None)
        state.public_result_nonempty = (
            type(public_alternatives) is tuple and bool(public_alternatives)
        )
        if state.public_result_nonempty:
            for alternative in public_alternatives:
                public_steps = getattr(alternative, "steps", None)
                if type(public_steps) is not tuple or not public_steps:
                    state.public_result_nonempty = False
                    break
        state.blank_filtering_applied = (
            mode == "transit"
            and state.raw_all_validated_before_filtering
            and state.blank_instruction_seen
            and state.public_result_nonempty
        )

    def observe_weather(self, raw: object) -> None:
        self.weather_parser_completed = True
        root = raw if type(raw) is dict else None
        result = root.get("result") if type(root) is dict else None
        forecasts = result.get("forecasts") if type(result) is dict else None
        self.weather_raw_at_most_seven = type(forecasts) is list and len(forecasts) <= 7
        self.weather_raw_all_validated_before_truncation = (
            self.weather_parser_completed
            and self.weather_raw_at_most_seven
            and all(
                type(item) is dict
                and all(field in item for field in ("date", "text_day", "text_night", "high", "low"))
                for item in forecasts
            )
        )


def _wrap_observed_parser(name: str, original, observer: _LiveStructureObserver):
    @wraps(original)
    def wrapped(raw, request, retrieved_at):
        # The selected raw object goes directly to the production parser first.
        outcome = original(raw, request, retrieved_at)
        if name == "reverse":
            observer.observe_reverse(raw, outcome)
        elif name == "directions":
            observer.observe_directions(raw, request, outcome)
        else:
            observer.observe_weather(raw)
        return outcome

    return wrapped


@pytest.mark.asyncio
async def test_seven_core_capabilities_live() -> None:
    from app.baidu_map import (
        DEFAULT_BAIDU_MAP_LIMITS,
        BaiduCoordinate,
        CitySearchArea,
        CoordinateSystem,
        DirectionsSuccess,
        DirectionsRequest,
        GeocodeRequest,
        GeocodeSuccess,
        MatrixTravelMode,
        PlaceDetailsRequest,
        PlaceDetailsSuccess,
        PlaceSearchRequest,
        PlaceSearchSuccess,
        ReverseGeocodeRequest,
        ReverseGeocodeSuccess,
        RouteEndpoint,
        RouteMatrixRequest,
        RouteMatrixSuccess,
        TravelMode,
        WeatherRequest,
        WeatherSuccess,
        create_baidu_map_runtime,
        load_baidu_map_settings,
    )
    from app.baidu_map import MapOutcomeStatus
    from app.workflow_contracts import SystemUtcClock

    gateway_module = importlib.import_module("app.baidu_map._mcp_gateway")
    sdk_observer = _LiveSdkBoundaryObserver()
    structure_observer = _LiveStructureObserver()
    original_client_context = gateway_module._production_client_context

    @asynccontextmanager
    async def observed_client_context(*, settings, limits):
        async with original_client_context(settings=settings, limits=limits) as session:
            yield _ObservedMcpSession(session, sdk_observer)

    address_a = "浙江省杭州市西湖区龙井路1号"
    address_b = "浙江省杭州市西湖区北山街89号"
    city = "杭州市"
    counts = {
        "geocode": 0,
        "reverse_geocode": 0,
        "search_places": 0,
        "place_details": 0,
        "route_matrix": 0,
        "directions": 0,
        "weather": 0,
    }

    trace: list[tuple[str, str | None]] = []

    async def counted(name: str, capability: str, mode: str | None, operation):
        counts[name] += 1
        trace.append((capability, mode))
        return await operation()

    settings = load_baidu_map_settings()
    with (
        patch.object(gateway_module, "_production_client_context", observed_client_context),
        patch.object(
            gateway_module,
            "parse_reverse_geocode",
            _wrap_observed_parser("reverse", gateway_module.parse_reverse_geocode, structure_observer),
        ),
        patch.object(
            gateway_module,
            "parse_directions",
            _wrap_observed_parser("directions", gateway_module.parse_directions, structure_observer),
        ),
        patch.object(
            gateway_module,
            "parse_weather",
            _wrap_observed_parser("weather", gateway_module.parse_weather, structure_observer),
        ),
    ):
      async with create_baidu_map_runtime(
          settings=settings, clock=SystemUtcClock(), limits=DEFAULT_BAIDU_MAP_LIMITS
      ) as runtime:
        first = await counted(
            "geocode", "geocode", "A", lambda: runtime.adapter.geocode(GeocodeRequest(address=address_a, city=city))
        )
        second = await counted(
            "geocode", "geocode", "B", lambda: runtime.adapter.geocode(GeocodeRequest(address=address_b, city=city))
        )
        assert type(first) is GeocodeSuccess
        assert first.status is MapOutcomeStatus.SUCCESS
        assert type(second) is GeocodeSuccess
        assert second.status is MapOutcomeStatus.SUCCESS
        a = first.result.coordinate
        b = second.result.coordinate
        assert type(a) is BaiduCoordinate
        assert type(b) is BaiduCoordinate
        reverse = await counted(
            "reverse_geocode", "reverse", None, lambda: runtime.adapter.reverse_geocode(ReverseGeocodeRequest(location=a))
        )
        assert type(reverse) is ReverseGeocodeSuccess
        assert reverse.status is MapOutcomeStatus.SUCCESS
        assert type(reverse.address_component.adcode) is int
        assert type(reverse.address_component.adcode) is not bool
        places = await counted(
            "search_places", "search", None, lambda: runtime.adapter.search_places(
                PlaceSearchRequest(query="博物馆", tag="旅游景点", area=CitySearchArea(region=city), result_limit=5)
            )
        )
        assert type(places) is PlaceSearchSuccess
        assert places.status is MapOutcomeStatus.SUCCESS
        assert len(places.places) >= 1
        selected_uid = places.places[0].uid
        details = await counted(
            "place_details", "details", None, lambda: runtime.adapter.get_place_details(PlaceDetailsRequest(uid=selected_uid))
        )
        assert type(details) is PlaceDetailsSuccess
        assert details.status is MapOutcomeStatus.SUCCESS
        assert details.details.uid == selected_uid
        for mode in (MatrixTravelMode.WALKING, MatrixTravelMode.DRIVING, MatrixTravelMode.RIDING):
            matrix = await counted(
                "route_matrix", "matrix", mode.value,
                lambda mode=mode: runtime.adapter.calculate_route_matrix(
                    RouteMatrixRequest(mode=mode, origins=(a,), destinations=(b,))
                ),
            )
            assert type(matrix) is RouteMatrixSuccess
            assert matrix.status is MapOutcomeStatus.SUCCESS
            assert len(matrix.cells) == 1
            assert matrix.cells[0].status is MapOutcomeStatus.SUCCESS
        endpoint_a, endpoint_b = RouteEndpoint(coordinate=a), RouteEndpoint(coordinate=b)
        for mode in (TravelMode.WALKING, TravelMode.DRIVING, TravelMode.RIDING, TravelMode.TRANSIT):
            directions = await counted(
                "directions", "directions", mode.value,
                lambda mode=mode: runtime.adapter.plan_directions(
                    DirectionsRequest(
                        mode=mode,
                        origin=endpoint_a,
                        destination=endpoint_b,
                        alternatives_limit=1,
                    )
                ),
            )
            assert type(directions) is DirectionsSuccess
            assert directions.status is MapOutcomeStatus.SUCCESS
            assert len(directions.alternatives) == 1
            assert len(directions.alternatives[0].steps) >= 1
            for step in directions.alternatives[0].steps:
                assert type(step.instruction) is str
                assert type(step.start) is BaiduCoordinate
                assert type(step.end) is BaiduCoordinate
                assert step.start.coordinate_system is CoordinateSystem.BD09LL
                assert step.end.coordinate_system is CoordinateSystem.BD09LL
        weather = await counted(
            "weather", "weather", None, lambda: runtime.adapter.get_weather(WeatherRequest(location=a))
        )
        assert type(weather) is WeatherSuccess
        assert weather.status is MapOutcomeStatus.SUCCESS
        assert type(weather.current.condition) is str
        assert len(weather.forecast) <= 5
        forecast_dates = tuple(item.date for item in weather.forecast)
        assert all(
            left < right
            for left, right in zip(forecast_dates, forecast_dates[1:])
        )

    assert sdk_observer.initialize_count == 1
    assert sdk_observer.tools_list_count == 1
    assert sdk_observer.call_tool_request_count == 13
    assert sdk_observer.call_tool_names == [
        "map_geocode",
        "map_geocode",
        "map_reverse_geocode",
        "map_search_places",
        "map_place_details",
        "map_directions_matrix",
        "map_directions_matrix",
        "map_directions_matrix",
        "map_directions",
        "map_directions",
        "map_directions",
        "map_directions",
        "map_weather",
    ]
    assert structure_observer.reverse_adcode_path_exists
    assert structure_observer.reverse_adcode_ascii_decimal_string
    assert structure_observer.reverse_parser_completed
    assert structure_observer.reverse_raw_all_validated
    assert structure_observer.reverse_provider_addr_point_shape
    assert structure_observer.reverse_point_xy_json_number
    assert structure_observer.reverse_public_at_most_twenty
    assert structure_observer.reverse_public_matches_first_twenty
    assert tuple(structure_observer.directions) == _DIRECTION_MODES
    for mode in ("walking", "driving", "riding"):
        direction = structure_observer.directions[mode]
        assert direction.parser_completed
        assert direction.raw_routes_nonempty
        assert direction.all_raw_routes_observed
        assert direction.instruction_singular
        assert direction.raw_steps_nesting
        assert direction.raw_coordinate_category
        assert direction.public_result_nonempty
    transit = structure_observer.directions["transit"]
    assert transit.parser_completed
    assert transit.raw_routes_nonempty
    assert transit.all_raw_routes_observed
    assert transit.instruction_singular
    assert transit.raw_steps_nesting
    assert transit.raw_coordinate_category
    assert transit.raw_all_validated_before_filtering
    assert transit.blank_instruction_seen
    assert transit.blank_filtering_applied
    assert transit.public_result_nonempty
    assert structure_observer.weather_raw_at_most_seven
    assert structure_observer.weather_raw_all_validated_before_truncation
    assert counts == {
        "geocode": 2,
        "reverse_geocode": 1,
        "search_places": 1,
        "place_details": 1,
        "route_matrix": 3,
        "directions": 4,
        "weather": 1,
    }
    assert trace == [
        ("geocode", "A"),
        ("geocode", "B"),
        ("reverse", None),
        ("search", None),
        ("details", None),
        ("matrix", "walking"),
        ("matrix", "driving"),
        ("matrix", "riding"),
        ("directions", "walking"),
        ("directions", "driving"),
        ("directions", "riding"),
        ("directions", "transit"),
        ("weather", None),
    ]
