"""Typed core adapter boundary for Baidu Maps capabilities."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.baidu_map.models.geocoding import (
    GeocodeOutcome,
    GeocodeRequest,
    ReverseGeocodeOutcome,
    ReverseGeocodeRequest,
)
from app.baidu_map.models.places import (
    PlaceDetailsOutcome,
    PlaceDetailsRequest,
    PlaceSearchOutcome,
    PlaceSearchRequest,
)
from app.baidu_map.models.routes import (
    DirectionsOutcome,
    DirectionsRequest,
    RouteMatrixOutcome,
    RouteMatrixRequest,
)
from app.baidu_map.models.weather import WeatherOutcome, WeatherRequest


@runtime_checkable
class BaiduMapAdapter(Protocol):
    async def geocode(self, request: GeocodeRequest) -> GeocodeOutcome: ...

    async def reverse_geocode(
        self, request: ReverseGeocodeRequest
    ) -> ReverseGeocodeOutcome: ...

    async def search_places(self, request: PlaceSearchRequest) -> PlaceSearchOutcome: ...

    async def get_place_details(
        self, request: PlaceDetailsRequest
    ) -> PlaceDetailsOutcome: ...

    async def calculate_route_matrix(
        self, request: RouteMatrixRequest
    ) -> RouteMatrixOutcome: ...

    async def plan_directions(self, request: DirectionsRequest) -> DirectionsOutcome: ...

    async def get_weather(self, request: WeatherRequest) -> WeatherOutcome: ...


__all__ = ("BaiduMapAdapter",)
