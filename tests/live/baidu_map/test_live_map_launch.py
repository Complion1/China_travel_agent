"""LIVE-QUALIFICATION optional map-launch probe; T05 only collects without settings, Secret, or network access."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_map_launch_availability_live() -> None:
    from app.baidu_map import (
        MapCapability,
        MapCapabilityUnavailableReason,
        create_baidu_map_runtime,
        load_baidu_map_settings,
    )
    from app.workflow_contracts import SystemUtcClock

    async with create_baidu_map_runtime(
        settings=load_baidu_map_settings(), clock=SystemUtcClock()
    ) as runtime:
        availability = runtime.get_capability_availability(capability=MapCapability.MAP_LAUNCH)
        assert not availability.available
        assert availability.reason_code is MapCapabilityUnavailableReason.NOT_IN_MANIFEST
