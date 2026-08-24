"""Optional Baidu Maps launch capability DTOs."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias
from urllib.parse import unquote, urlsplit

from pydantic import Field, field_validator, model_validator

from app.baidu_map.models.common import (
    BaiduCoordinate,
    MapCapability,
    MapCapabilityUnavailableReason,
    MapLaunchService,
    MapModel,
    MapOutcomeStatus,
    MapProvenance,
    TravelMode,
)
from app.baidu_map.models.routes import RouteEndpoint


class MapLaunchRouteRequest(MapModel):
    service: Literal[MapLaunchService.ROUTE] = MapLaunchService.ROUTE
    origin: RouteEndpoint
    destination: RouteEndpoint
    mode: TravelMode
    region: str | None = Field(default=None, max_length=512)


class MapLaunchSearchRequest(MapModel):
    service: Literal[MapLaunchService.SEARCH] = MapLaunchService.SEARCH
    query: str = Field(max_length=512)
    region: str | None = Field(default=None, max_length=512)
    location: BaiduCoordinate | None = None
    radius_m: int | None = Field(default=None, ge=1, le=50000)

    @model_validator(mode="after")
    def _require_location_radius_pair(self) -> MapLaunchSearchRequest:
        if (self.location is None) != (self.radius_m is None):
            raise ValueError("location and radius_m must be supplied together")
        return self


MapLaunchRequest: TypeAlias = Annotated[
    MapLaunchRouteRequest | MapLaunchSearchRequest,
    Field(discriminator="service"),
]


def _validate_safe_map_url(value: str) -> str:
    if len(value) > 2048:
        raise ValueError("map launch URL is too long")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid map launch URL") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError("map launch URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("map launch URL must not contain userinfo")
    if port not in (None, 443):
        raise ValueError("map launch URL port is not allowed")
    if hostname is None:
        raise ValueError("map launch URL hostname is required")
    normalized_host = hostname.casefold()
    if normalized_host != "map.baidu.com" and not normalized_host.endswith(".map.baidu.com"):
        raise ValueError("map launch URL host is not allowed")
    forbidden_keys = {"ak", "key", "api_key", "token", "access_token"}
    for query_part in parsed.query.split("&"):
        if not query_part:
            continue
        key = unquote(query_part.split("=", 1)[0]).casefold()
        if key in forbidden_keys:
            raise ValueError("map launch URL contains a forbidden query key")
    return value


class MapLaunchSuccess(MapModel):
    status: Literal[MapOutcomeStatus.SUCCESS] = MapOutcomeStatus.SUCCESS
    url: str = Field(max_length=2048)
    provenance: MapProvenance

    _validate_url = field_validator("url")(_validate_safe_map_url)

    @model_validator(mode="after")
    def _require_capability(self) -> MapLaunchSuccess:
        if self.provenance.capability is not MapCapability.MAP_LAUNCH:
            raise ValueError("map launch provenance capability mismatch")
        return self


class MapLaunchNoResult(MapModel):
    status: Literal[MapOutcomeStatus.NO_RESULT] = MapOutcomeStatus.NO_RESULT
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> MapLaunchNoResult:
        if self.provenance.capability is not MapCapability.MAP_LAUNCH:
            raise ValueError("map launch provenance capability mismatch")
        return self


MapLaunchOutcome: TypeAlias = Annotated[
    MapLaunchSuccess | MapLaunchNoResult,
    Field(discriminator="status"),
]


class MapCapabilityAvailability(MapModel):
    capability: MapCapability
    available: bool
    reason_code: MapCapabilityUnavailableReason | None = None

    @model_validator(mode="after")
    def _validate_availability(self) -> MapCapabilityAvailability:
        if self.available != (self.reason_code is None):
            raise ValueError("availability and reason_code are inconsistent")
        if self.capability is not MapCapability.MAP_LAUNCH and not self.available:
            raise ValueError("core map capabilities cannot be unavailable")
        return self


__all__ = (
    "MapCapabilityAvailability",
    "MapLaunchNoResult",
    "MapLaunchOutcome",
    "MapLaunchRequest",
    "MapLaunchRouteRequest",
    "MapLaunchSearchRequest",
    "MapLaunchSuccess",
)
