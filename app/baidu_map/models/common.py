"""Shared public values and validation for the Baidu Maps contract."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import math
from typing import Literal, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


MAP_SCHEMA_VERSION = "1.0"

_FIXED_SEMANTIC_FIELDS = frozenset(
    {
        "schema_version",
        "coordinate_system",
        "source",
        "capability",
        "status",
        "area_type",
        "service",
        "mode",
        "reason_code",
    }
)


class CoordinateSystem(str, Enum):
    BD09LL = "bd09ll"


class MapCapability(str, Enum):
    GEOCODING = "geocoding"
    REVERSE_GEOCODING = "reverse_geocoding"
    PLACE_SEARCH = "place_search"
    PLACE_DETAILS = "place_details"
    ROUTE_MATRIX = "route_matrix"
    DIRECTIONS = "directions"
    WEATHER = "weather"
    MAP_LAUNCH = "map_launch"


class TravelMode(str, Enum):
    DRIVING = "driving"
    RIDING = "riding"
    WALKING = "walking"
    TRANSIT = "transit"


class MatrixTravelMode(str, Enum):
    DRIVING = "driving"
    RIDING = "riding"
    WALKING = "walking"


class MapOutcomeStatus(str, Enum):
    SUCCESS = "success"
    NO_RESULT = "no_result"
    AMBIGUOUS = "ambiguous"


class PlaceSearchAreaType(str, Enum):
    CITY = "city"
    NEARBY = "nearby"


class MapLaunchService(str, Enum):
    ROUTE = "route"
    SEARCH = "search"


class MapCapabilityUnavailableReason(str, Enum):
    NOT_IN_MANIFEST = "not_in_manifest"
    TOOL_NOT_LISTED = "tool_not_listed"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"


def _clean_text(value: object) -> object:
    if type(value) is str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("string must not be blank")
        return cleaned
    return value


def _clean_optional_text(value: object) -> object:
    if value is None:
        return None
    return _clean_text(value)


def _clean_text_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_clean_text(value) for value in values)  # type: ignore[return-value]


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be UTC aware")
    return value


class MapModel(BaseModel):
    """Strict, immutable DTO base with the mandatory schema version field."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    schema_version: Literal["1.0"] = MAP_SCHEMA_VERSION

    @model_validator(mode="before")
    @classmethod
    def _trim_scalar_strings(cls, value: object) -> object:
        if isinstance(value, dict):
            return {
                key: item if key in _FIXED_SEMANTIC_FIELDS else _clean_text(item)
                for key, item in value.items()
            }
        return value

    @model_validator(mode="before")
    @classmethod
    def _restore_json_tuples(cls, value: object, info: ValidationInfo) -> object:
        if info.mode != "json" or not isinstance(value, dict):
            return value
        restored = dict(value)
        for name, field in cls.model_fields.items():
            if get_origin(field.annotation) is tuple and type(restored.get(name)) is type([]):
                restored[name] = tuple(restored[name])
        return restored


class BaiduCoordinate(MapModel):
    coordinate_system: Literal[CoordinateSystem.BD09LL] = CoordinateSystem.BD09LL
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class MapProvenance(MapModel):
    source: Literal["baidu_maps_mcp"] = "baidu_maps_mcp"
    capability: MapCapability
    retrieved_at: datetime
    verified: bool

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def _parse_json_datetime(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json" and type(value) is str:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        return value

    _validate_retrieved_at = field_validator("retrieved_at")(_require_utc)


__all__ = (
    "MAP_SCHEMA_VERSION",
    "BaiduCoordinate",
    "CoordinateSystem",
    "MapCapability",
    "MapCapabilityUnavailableReason",
    "MapLaunchService",
    "MapModel",
    "MapOutcomeStatus",
    "MapProvenance",
    "MatrixTravelMode",
    "PlaceSearchAreaType",
    "TravelMode",
)
