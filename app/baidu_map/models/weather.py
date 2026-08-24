"""Weather request, current conditions and forecast DTOs."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, ValidationInfo, field_validator, model_validator

from app.baidu_map.models.common import (
    BaiduCoordinate,
    MapCapability,
    MapModel,
    MapOutcomeStatus,
    MapProvenance,
)


class WeatherRequest(MapModel):
    location: BaiduCoordinate


class CurrentWeather(MapModel):
    condition: str = Field(max_length=512)
    temperature_c: float = Field(allow_inf_nan=False)
    feels_like_c: float | None = Field(default=None, allow_inf_nan=False)
    humidity_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    wind_description: str | None = Field(default=None, max_length=512)


class DailyWeatherForecast(MapModel):
    date: date
    day_condition: str = Field(max_length=512)
    night_condition: str = Field(max_length=512)
    high_c: float = Field(allow_inf_nan=False)
    low_c: float = Field(allow_inf_nan=False)

    @field_validator("date", mode="before")
    @classmethod
    def _parse_json_date(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json" and type(value) is str:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return value
        return value

    @model_validator(mode="after")
    def _enforce_temperature_order(self) -> DailyWeatherForecast:
        if self.high_c < self.low_c:
            raise ValueError("daily high temperature must not be below low temperature")
        return self


class WeatherSuccess(MapModel):
    status: Literal[MapOutcomeStatus.SUCCESS] = MapOutcomeStatus.SUCCESS
    current: CurrentWeather
    forecast: tuple[DailyWeatherForecast, ...] = Field(default=(), max_length=5)
    provenance: MapProvenance

    @model_validator(mode="after")
    def _validate_forecast(self) -> WeatherSuccess:
        dates = tuple(item.date for item in self.forecast)
        if any(left >= right for left, right in zip(dates, dates[1:])):
            raise ValueError("weather forecast dates must be strictly increasing")
        if self.provenance.capability is not MapCapability.WEATHER:
            raise ValueError("weather provenance capability mismatch")
        return self


class WeatherNoResult(MapModel):
    status: Literal[MapOutcomeStatus.NO_RESULT] = MapOutcomeStatus.NO_RESULT
    provenance: MapProvenance

    @model_validator(mode="after")
    def _require_capability(self) -> WeatherNoResult:
        if self.provenance.capability is not MapCapability.WEATHER:
            raise ValueError("weather provenance capability mismatch")
        return self


WeatherOutcome: TypeAlias = Annotated[
    WeatherSuccess | WeatherNoResult,
    Field(discriminator="status"),
]


__all__ = (
    "CurrentWeather",
    "DailyWeatherForecast",
    "WeatherNoResult",
    "WeatherOutcome",
    "WeatherRequest",
    "WeatherSuccess",
)
