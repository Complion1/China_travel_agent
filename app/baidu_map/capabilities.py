"""Optional capability boundary kept separate from the seven core methods."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.baidu_map.models.map_launch import (
    MapCapabilityAvailability,
    MapLaunchOutcome,
    MapLaunchRequest,
)


@runtime_checkable
class MapLaunchCapability(Protocol):
    def get_availability(self) -> MapCapabilityAvailability: ...

    async def create_map_launch(self, request: MapLaunchRequest) -> MapLaunchOutcome: ...


__all__ = ("MapLaunchCapability",)
