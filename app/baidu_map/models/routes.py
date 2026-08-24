"""Route matrix and directions DTOs."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from app.baidu_map.models.common import (
    BaiduCoordinate,
    MapCapability,
    MapModel,
    MapOutcomeStatus,
    MapProvenance,
    MatrixTravelMode,
    TravelMode,
)


class RouteMatrixRequest(MapModel):
    mode: MatrixTravelMode
    origins: tuple[BaiduCoordinate, ...] = Field(min_length=1)
    destinations: tuple[BaiduCoordinate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _enforce_capacity(self) -> RouteMatrixRequest:
        if len(self.origins) * len(self.destinations) > 100:
            raise ValueError("route matrix cell capacity exceeded")
        return self


class RouteMatrixCellSuccess(MapModel):
    status: Literal[MapOutcomeStatus.SUCCESS] = MapOutcomeStatus.SUCCESS
    origin_index: int = Field(ge=0, le=99)
    destination_index: int = Field(ge=0, le=99)
    distance_m: int = Field(ge=0)
    duration_s: int = Field(ge=0)


class RouteMatrixCellNoResult(MapModel):
    status: Literal[MapOutcomeStatus.NO_RESULT] = MapOutcomeStatus.NO_RESULT
    origin_index: int = Field(ge=0, le=99)
    destination_index: int = Field(ge=0, le=99)


RouteMatrixCell: TypeAlias = Annotated[
    RouteMatrixCellSuccess | RouteMatrixCellNoResult,
    Field(discriminator="status"),
]


class RouteMatrixSuccess(MapModel):
    status: Literal[MapOutcomeStatus.SUCCESS] = MapOutcomeStatus.SUCCESS
    origin_count: int = Field(ge=1, le=100)
    destination_count: int = Field(ge=1, le=100)
    cells: tuple[RouteMatrixCell, ...] = Field(min_length=1, max_length=100)
    provenance: MapProvenance

    @model_validator(mode="after")
    def _validate_cells(self) -> RouteMatrixSuccess:
        if self.origin_count * self.destination_count > 100:
            raise ValueError("route matrix cell capacity exceeded")
        expected = tuple(
            (origin_index, destination_index)
            for origin_index in range(self.origin_count)
            for destination_index in range(self.destination_count)
        )
        actual = tuple((cell.origin_index, cell.destination_index) for cell in self.cells)
        if actual != expected:
            raise ValueError("route matrix cells must cover the ordered cartesian product")
        if not any(cell.status is MapOutcomeStatus.SUCCESS for cell in self.cells):
            raise ValueError("route matrix success requires one successful cell")
        if self.provenance.capability is not MapCapability.ROUTE_MATRIX:
            raise ValueError("route matrix provenance capability mismatch")
        return self


class RouteMatrixNoResult(MapModel):
    status: Literal[MapOutcomeStatus.NO_RESULT] = MapOutcomeStatus.NO_RESULT
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> RouteMatrixNoResult:
        if self.provenance.capability is not MapCapability.ROUTE_MATRIX:
            raise ValueError("route matrix provenance capability mismatch")
        return self


RouteMatrixOutcome: TypeAlias = Annotated[
    RouteMatrixSuccess | RouteMatrixNoResult,
    Field(discriminator="status"),
]


class RouteEndpoint(MapModel):
    coordinate: BaiduCoordinate
    label: str | None = Field(default=None, max_length=512)


class DirectionsRequest(MapModel):
    mode: TravelMode
    origin: RouteEndpoint
    destination: RouteEndpoint
    alternatives_limit: int = Field(default=1, ge=1, le=3)


class RouteStep(MapModel):
    instruction: str = Field(max_length=1024)
    distance_m: int = Field(ge=0)
    duration_s: int = Field(ge=0)
    start: BaiduCoordinate
    end: BaiduCoordinate


class RouteAlternative(MapModel):
    distance_m: int = Field(ge=0)
    duration_s: int = Field(ge=0)
    steps: tuple[RouteStep, ...] = Field(min_length=1, max_length=128)


class DirectionsSuccess(MapModel):
    status: Literal[MapOutcomeStatus.SUCCESS] = MapOutcomeStatus.SUCCESS
    alternatives: tuple[RouteAlternative, ...] = Field(min_length=1, max_length=3)
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> DirectionsSuccess:
        if self.provenance.capability is not MapCapability.DIRECTIONS:
            raise ValueError("directions provenance capability mismatch")
        return self


class DirectionsNoResult(MapModel):
    status: Literal[MapOutcomeStatus.NO_RESULT] = MapOutcomeStatus.NO_RESULT
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> DirectionsNoResult:
        if self.provenance.capability is not MapCapability.DIRECTIONS:
            raise ValueError("directions provenance capability mismatch")
        return self


DirectionsOutcome: TypeAlias = Annotated[
    DirectionsSuccess | DirectionsNoResult,
    Field(discriminator="status"),
]


__all__ = (
    "DirectionsNoResult",
    "DirectionsOutcome",
    "DirectionsRequest",
    "DirectionsSuccess",
    "RouteAlternative",
    "RouteEndpoint",
    "RouteMatrixCell",
    "RouteMatrixCellNoResult",
    "RouteMatrixCellSuccess",
    "RouteMatrixNoResult",
    "RouteMatrixOutcome",
    "RouteMatrixRequest",
    "RouteMatrixSuccess",
    "RouteStep",
)
