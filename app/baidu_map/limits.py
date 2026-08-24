"""Frozen construction limits for the future Baidu Maps runtime."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BaiduMapLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    connect_timeout_s: float = Field(default=10.0, gt=0, le=10.0, allow_inf_nan=False)
    write_timeout_s: float = Field(default=10.0, gt=0, le=10.0, allow_inf_nan=False)
    pool_timeout_s: float = Field(default=10.0, gt=0, le=10.0, allow_inf_nan=False)
    initialize_timeout_s: float = Field(default=15.0, gt=0, le=15.0, allow_inf_nan=False)
    list_tools_timeout_s: float = Field(default=15.0, gt=0, le=15.0, allow_inf_nan=False)
    geocode_timeout_s: float = Field(default=15.0, gt=0, le=15.0, allow_inf_nan=False)
    reverse_geocode_timeout_s: float = Field(default=15.0, gt=0, le=15.0, allow_inf_nan=False)
    place_search_timeout_s: float = Field(default=20.0, gt=0, le=20.0, allow_inf_nan=False)
    place_details_timeout_s: float = Field(default=15.0, gt=0, le=15.0, allow_inf_nan=False)
    route_matrix_timeout_s: float = Field(default=30.0, gt=0, le=30.0, allow_inf_nan=False)
    directions_timeout_s: float = Field(default=30.0, gt=0, le=30.0, allow_inf_nan=False)
    weather_timeout_s: float = Field(default=15.0, gt=0, le=15.0, allow_inf_nan=False)
    map_launch_timeout_s: float = Field(default=20.0, gt=0, le=20.0, allow_inf_nan=False)
    close_timeout_s: float = Field(default=5.0, gt=0, le=5.0, allow_inf_nan=False)
    max_in_flight: int = Field(default=3, ge=1, le=3)


DEFAULT_BAIDU_MAP_LIMITS = BaiduMapLimits()


__all__ = ("BaiduMapLimits", "DEFAULT_BAIDU_MAP_LIMITS")
