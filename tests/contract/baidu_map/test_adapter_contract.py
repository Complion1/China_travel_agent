"""Module 06 Protocol and final package-root contract tests."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import get_type_hints

from app import baidu_map
from app.baidu_map import (
    BaiduMapAdapter,
    DirectionsOutcome,
    DirectionsRequest,
    GeocodeOutcome,
    GeocodeRequest,
    MapCapabilityAvailability,
    MapLaunchCapability,
    MapLaunchOutcome,
    MapLaunchRequest,
    PlaceDetailsOutcome,
    PlaceDetailsRequest,
    PlaceSearchOutcome,
    PlaceSearchRequest,
    ReverseGeocodeOutcome,
    ReverseGeocodeRequest,
    RouteMatrixOutcome,
    RouteMatrixRequest,
    WeatherOutcome,
    WeatherRequest,
)


EXPECTED_STAGE_ALL = (
    "MAP_SCHEMA_VERSION",
    "MAP_BINDING_MANIFEST_VERSION",
    "DEFAULT_BAIDU_MAP_LIMITS",
    "BaiduMapAdapter",
    "MapLaunchCapability",
    "BaiduMapRuntime",
    "create_baidu_map_runtime",
    "BaiduMapSettings",
    "load_baidu_map_settings",
    "BaiduMapLimits",
    "MapCapability",
    "CoordinateSystem",
    "TravelMode",
    "MatrixTravelMode",
    "MapOutcomeStatus",
    "PlaceSearchAreaType",
    "MapLaunchService",
    "MapCapabilityUnavailableReason",
    "BaiduCoordinate",
    "MapProvenance",
    "GeocodeRequest",
    "GeocodeCandidate",
    "GeocodeSuccess",
    "GeocodeNoResult",
    "GeocodeAmbiguous",
    "GeocodeOutcome",
    "ReverseGeocodeRequest",
    "AddressComponent",
    "ReverseGeocodeSuccess",
    "ReverseGeocodeNoResult",
    "ReverseGeocodeOutcome",
    "PlaceSummary",
    "PlaceDetails",
    "CitySearchArea",
    "NearbySearchArea",
    "PlaceSearchArea",
    "PlaceSearchRequest",
    "PlaceSearchSuccess",
    "PlaceSearchNoResult",
    "PlaceSearchOutcome",
    "PlaceDetailsRequest",
    "PlaceDetailsSuccess",
    "PlaceDetailsNoResult",
    "PlaceDetailsOutcome",
    "RouteMatrixRequest",
    "RouteMatrixCellSuccess",
    "RouteMatrixCellNoResult",
    "RouteMatrixCell",
    "RouteMatrixSuccess",
    "RouteMatrixNoResult",
    "RouteMatrixOutcome",
    "RouteEndpoint",
    "DirectionsRequest",
    "RouteStep",
    "RouteAlternative",
    "DirectionsSuccess",
    "DirectionsNoResult",
    "DirectionsOutcome",
    "WeatherRequest",
    "CurrentWeather",
    "DailyWeatherForecast",
    "WeatherSuccess",
    "WeatherNoResult",
    "WeatherOutcome",
    "MapLaunchRouteRequest",
    "MapLaunchSearchRequest",
    "MapLaunchRequest",
    "MapLaunchSuccess",
    "MapLaunchNoResult",
    "MapLaunchOutcome",
    "MapCapabilityAvailability",
    "BaiduMapErrorCode",
    "BaiduMapError",
    "BaiduMapRequestError",
    "BaiduMapConfigurationError",
    "BaiduMapAuthenticationError",
    "BaiduMapRateLimitError",
    "BaiduMapTimeoutError",
    "BaiduMapTransportError",
    "BaiduMapSessionError",
    "BaiduMapToolUnavailableError",
    "BaiduMapToolSchemaError",
    "BaiduMapProtocolError",
    "BaiduMapResponseError",
    "BaiduMapConcurrencyError",
)


class _Adapter:
    async def geocode(self, request: GeocodeRequest) -> GeocodeOutcome:
        raise NotImplementedError

    async def reverse_geocode(self, request: ReverseGeocodeRequest) -> ReverseGeocodeOutcome:
        raise NotImplementedError

    async def search_places(self, request: PlaceSearchRequest) -> PlaceSearchOutcome:
        raise NotImplementedError

    async def get_place_details(self, request: PlaceDetailsRequest) -> PlaceDetailsOutcome:
        raise NotImplementedError

    async def calculate_route_matrix(self, request: RouteMatrixRequest) -> RouteMatrixOutcome:
        raise NotImplementedError

    async def plan_directions(self, request: DirectionsRequest) -> DirectionsOutcome:
        raise NotImplementedError

    async def get_weather(self, request: WeatherRequest) -> WeatherOutcome:
        raise NotImplementedError


class _MapLaunch:
    def get_availability(self) -> MapCapabilityAvailability:
        raise NotImplementedError

    async def create_map_launch(self, request: MapLaunchRequest) -> MapLaunchOutcome:
        raise NotImplementedError


def _public_callable_names(protocol: type[object]) -> set[str]:
    return {
        name
        for name, value in protocol.__dict__.items()
        if not name.startswith("_") and callable(value)
    }


def _assert_method_contract(
    protocol: type[object],
    expected: dict[str, tuple[type[object] | object, type[object] | object, bool]],
) -> None:
    assert _public_callable_names(protocol) == set(expected)
    for name, (request_type, return_type, is_async) in expected.items():
        method = getattr(protocol, name)
        signature = inspect.signature(method)
        expected_parameters = ("self",) if request_type is None else ("self", "request")
        assert tuple(signature.parameters) == expected_parameters
        assert all(
            parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            for parameter in signature.parameters.values()
        )
        assert all(parameter.default is inspect.Parameter.empty for parameter in signature.parameters.values())
        hints = get_type_hints(method, include_extras=True)
        if request_type is not None:
            assert hints["request"] == request_type
        assert hints["return"] == return_type
        assert inspect.iscoroutinefunction(method) is is_async


def test_protocols_are_runtime_checkable_with_exact_signatures() -> None:
    assert isinstance(_Adapter(), BaiduMapAdapter)
    assert isinstance(_MapLaunch(), MapLaunchCapability)
    _assert_method_contract(
        BaiduMapAdapter,
        {
            "geocode": (GeocodeRequest, GeocodeOutcome, True),
            "reverse_geocode": (ReverseGeocodeRequest, ReverseGeocodeOutcome, True),
            "search_places": (PlaceSearchRequest, PlaceSearchOutcome, True),
            "get_place_details": (PlaceDetailsRequest, PlaceDetailsOutcome, True),
            "calculate_route_matrix": (RouteMatrixRequest, RouteMatrixOutcome, True),
            "plan_directions": (DirectionsRequest, DirectionsOutcome, True),
            "get_weather": (WeatherRequest, WeatherOutcome, True),
        },
    )
    _assert_method_contract(
        MapLaunchCapability,
        {
            "get_availability": (None, MapCapabilityAvailability, False),
            "create_map_launch": (MapLaunchRequest, MapLaunchOutcome, True),
        },
    )


def test_package_root_projection_is_exact_and_private_names_absent() -> None:
    assert isinstance(baidu_map.__all__, tuple)
    assert baidu_map.__all__ == EXPECTED_STAGE_ALL
    deferred_names = (
        "BaiduMapMcpGateway",
        "MapModel",
        "runtime",
        "registry",
        "parser",
        "endpoint",
        "session",
    )
    for name in deferred_names:
        assert name not in baidu_map.__all__
        assert not hasattr(baidu_map, name)


def test_protocol_modules_have_no_forbidden_runtime_boundaries() -> None:
    package_dir = Path(__file__).parents[3] / "app" / "baidu_map"
    forbidden = (
        "mcp",
        "ClientSession",
        "CallToolResult",
        "OperationId",
        "ArtifactRepository",
        "ExecutionBudget",
        "call_tool",
        "Any",
        "list",
        "set",
    )
    for filename in ("adapter.py", "capabilities.py"):
        source = (package_dir / filename).read_text(encoding="utf-8")
        assert all(token not in source for token in forbidden)
    for protocol in (BaiduMapAdapter, MapLaunchCapability):
        for name in _public_callable_names(protocol):
            source = inspect.getsource(getattr(protocol, name))
            assert all(token not in source for token in forbidden)
