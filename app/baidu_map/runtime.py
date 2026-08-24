"""Public ownership boundary for one qualified Baidu Maps MCP session."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.baidu_map._mcp_gateway import BaiduMapMcpGateway
from app.baidu_map.adapter import BaiduMapAdapter
from app.baidu_map.capabilities import MapLaunchCapability
from app.baidu_map.limits import BaiduMapLimits, DEFAULT_BAIDU_MAP_LIMITS
from app.baidu_map.models import MapCapability, MapCapabilityAvailability
from app.baidu_map.settings import BaiduMapSettings
from app.workflow_contracts import UtcClock


class BaiduMapRuntime:
    def __init__(self, *, _gateway: BaiduMapMcpGateway) -> None:
        if type(_gateway) is not BaiduMapMcpGateway:
            raise TypeError("invalid Baidu Maps gateway")
        self._gateway = _gateway

    @property
    def adapter(self) -> BaiduMapAdapter:
        return self._gateway

    @property
    def map_launch(self) -> MapLaunchCapability:
        return self._gateway

    def get_capability_availability(
        self, *, capability: MapCapability
    ) -> MapCapabilityAvailability:
        return self._gateway.get_capability_availability(capability=capability)

    async def aclose(self) -> None:
        await self._gateway.aclose()


@asynccontextmanager
async def create_baidu_map_runtime(
    *,
    settings: BaiduMapSettings,
    clock: UtcClock,
    limits: BaiduMapLimits = DEFAULT_BAIDU_MAP_LIMITS,
) -> AsyncIterator[BaiduMapRuntime]:
    gateway = await BaiduMapMcpGateway.create(settings=settings, clock=clock, limits=limits)
    runtime = BaiduMapRuntime(_gateway=gateway)
    body_failed = False
    try:
        yield runtime
    except BaseException:
        body_failed = True
        try:
            await runtime.aclose()
        except BaseException:
            pass
        raise
    finally:
        if not body_failed:
            await runtime.aclose()


__all__ = ("BaiduMapRuntime", "create_baidu_map_runtime")
