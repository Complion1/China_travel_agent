"""Geocoding and reverse-geocoding DTOs."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from app.baidu_map.models.common import (
    BaiduCoordinate,
    MapCapability,
    MapModel,
    MapOutcomeStatus,
    MapProvenance,
)
from app.baidu_map.models.places import PlaceSummary


class GeocodeRequest(MapModel):
    address: str = Field(max_length=512)
    city: str | None = Field(default=None, max_length=512)


class GeocodeCandidate(MapModel):
    formatted_address: str = Field(max_length=512)
    coordinate: BaiduCoordinate
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class GeocodeSuccess(MapModel):
    status: Literal[MapOutcomeStatus.SUCCESS] = MapOutcomeStatus.SUCCESS
    result: GeocodeCandidate
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> GeocodeSuccess:
        if self.provenance.capability is not MapCapability.GEOCODING:
            raise ValueError("geocode provenance capability mismatch")
        return self


class GeocodeNoResult(MapModel):
    status: Literal[MapOutcomeStatus.NO_RESULT] = MapOutcomeStatus.NO_RESULT
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> GeocodeNoResult:
        if self.provenance.capability is not MapCapability.GEOCODING:
            raise ValueError("geocode provenance capability mismatch")
        return self


class GeocodeAmbiguous(MapModel):
    status: Literal[MapOutcomeStatus.AMBIGUOUS] = MapOutcomeStatus.AMBIGUOUS
    candidates: tuple[GeocodeCandidate, ...] = Field(min_length=2, max_length=10)
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> GeocodeAmbiguous:
        if self.provenance.capability is not MapCapability.GEOCODING:
            raise ValueError("geocode provenance capability mismatch")
        return self


GeocodeOutcome: TypeAlias = Annotated[
    GeocodeSuccess | GeocodeNoResult | GeocodeAmbiguous,
    Field(discriminator="status"),
]


class ReverseGeocodeRequest(MapModel):
    location: BaiduCoordinate


class AddressComponent(MapModel):
    country: str | None = Field(default=None, max_length=512)
    province: str | None = Field(default=None, max_length=512)
    city: str | None = Field(default=None, max_length=512)
    district: str | None = Field(default=None, max_length=512)
    town: str | None = Field(default=None, max_length=512)
    adcode: int | None = Field(default=None, ge=0, le=999999)


class ReverseGeocodeSuccess(MapModel):
    status: Literal[MapOutcomeStatus.SUCCESS] = MapOutcomeStatus.SUCCESS
    formatted_address: str = Field(max_length=512)
    address_component: AddressComponent
    nearby_places: tuple[PlaceSummary, ...] = Field(default=(), max_length=20)
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> ReverseGeocodeSuccess:
        if self.provenance.capability is not MapCapability.REVERSE_GEOCODING:
            raise ValueError("reverse geocode provenance capability mismatch")
        return self


class ReverseGeocodeNoResult(MapModel):
    status: Literal[MapOutcomeStatus.NO_RESULT] = MapOutcomeStatus.NO_RESULT
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> ReverseGeocodeNoResult:
        if self.provenance.capability is not MapCapability.REVERSE_GEOCODING:
            raise ValueError("reverse geocode provenance capability mismatch")
        return self


ReverseGeocodeOutcome: TypeAlias = Annotated[
    ReverseGeocodeSuccess | ReverseGeocodeNoResult,
    Field(discriminator="status"),
]


__all__ = (
    "AddressComponent",
    "GeocodeAmbiguous",
    "GeocodeCandidate",
    "GeocodeNoResult",
    "GeocodeOutcome",
    "GeocodeRequest",
    "GeocodeSuccess",
    "ReverseGeocodeNoResult",
    "ReverseGeocodeOutcome",
    "ReverseGeocodeRequest",
    "ReverseGeocodeSuccess",
)
