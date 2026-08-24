"""Place search and place detail DTOs."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from app.baidu_map.models.common import (
    BaiduCoordinate,
    MapCapability,
    MapModel,
    MapOutcomeStatus,
    MapProvenance,
    PlaceSearchAreaType,
)


class PlaceSummary(MapModel):
    uid: str = Field(max_length=512)
    name: str = Field(max_length=512)
    address: str = Field(max_length=512)
    coordinate: BaiduCoordinate
    category: str | None = Field(default=None, max_length=512)


class PlaceDetails(MapModel):
    uid: str = Field(max_length=512)
    name: str = Field(max_length=512)
    address: str = Field(max_length=512)
    coordinate: BaiduCoordinate
    category: str | None = Field(default=None, max_length=512)
    brand: str | None = Field(default=None, max_length=512)
    telephone: str | None = Field(default=None, max_length=512)
    rating: float | None = Field(default=None, ge=0, le=5, allow_inf_nan=False)
    business_hours: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("business_hours")
    @classmethod
    def _clean_business_hours(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item or len(item) > 512 for item in cleaned):
            raise ValueError("business hours entries must be non-blank and bounded")
        return cleaned


class CitySearchArea(MapModel):
    area_type: Literal[PlaceSearchAreaType.CITY] = PlaceSearchAreaType.CITY
    region: str = Field(max_length=512)


class NearbySearchArea(MapModel):
    area_type: Literal[PlaceSearchAreaType.NEARBY] = PlaceSearchAreaType.NEARBY
    center: BaiduCoordinate
    radius_m: int = Field(ge=1, le=50000)


PlaceSearchArea: TypeAlias = Annotated[
    CitySearchArea | NearbySearchArea,
    Field(discriminator="area_type"),
]


class PlaceSearchRequest(MapModel):
    query: str = Field(max_length=512)
    tag: str | None = Field(default=None, max_length=512)
    area: PlaceSearchArea
    result_limit: int | None = Field(default=None, ge=1, le=50)


class PlaceSearchSuccess(MapModel):
    status: Literal[MapOutcomeStatus.SUCCESS] = MapOutcomeStatus.SUCCESS
    places: tuple[PlaceSummary, ...] = Field(min_length=1, max_length=50)
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> PlaceSearchSuccess:
        if self.provenance.capability is not MapCapability.PLACE_SEARCH:
            raise ValueError("place search provenance capability mismatch")
        return self


class PlaceSearchNoResult(MapModel):
    status: Literal[MapOutcomeStatus.NO_RESULT] = MapOutcomeStatus.NO_RESULT
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> PlaceSearchNoResult:
        if self.provenance.capability is not MapCapability.PLACE_SEARCH:
            raise ValueError("place search provenance capability mismatch")
        return self


PlaceSearchOutcome: TypeAlias = Annotated[
    PlaceSearchSuccess | PlaceSearchNoResult,
    Field(discriminator="status"),
]


class PlaceDetailsRequest(MapModel):
    uid: str = Field(max_length=512)


class PlaceDetailsSuccess(MapModel):
    status: Literal[MapOutcomeStatus.SUCCESS] = MapOutcomeStatus.SUCCESS
    details: PlaceDetails
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> PlaceDetailsSuccess:
        if self.provenance.capability is not MapCapability.PLACE_DETAILS:
            raise ValueError("place details provenance capability mismatch")
        return self


class PlaceDetailsNoResult(MapModel):
    status: Literal[MapOutcomeStatus.NO_RESULT] = MapOutcomeStatus.NO_RESULT
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> PlaceDetailsNoResult:
        if self.provenance.capability is not MapCapability.PLACE_DETAILS:
            raise ValueError("place details provenance capability mismatch")
        return self


PlaceDetailsOutcome: TypeAlias = Annotated[
    PlaceDetailsSuccess | PlaceDetailsNoResult,
    Field(discriminator="status"),
]


__all__ = (
    "CitySearchArea",
    "NearbySearchArea",
    "PlaceDetails",
    "PlaceDetailsNoResult",
    "PlaceDetailsOutcome",
    "PlaceDetailsRequest",
    "PlaceDetailsSuccess",
    "PlaceSearchArea",
    "PlaceSearchNoResult",
    "PlaceSearchOutcome",
    "PlaceSearchRequest",
    "PlaceSearchSuccess",
    "PlaceSummary",
)
