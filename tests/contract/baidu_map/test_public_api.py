from __future__ import annotations

from contextlib import AbstractAsyncContextManager
import inspect
from typing import get_type_hints

from app.baidu_map import (
    DEFAULT_BAIDU_MAP_LIMITS,
    BaiduMapAdapter,
    BaiduMapLimits,
    BaiduMapRuntime,
    BaiduMapSettings,
    MapCapability,
    MapCapabilityAvailability,
    MapLaunchCapability,
    create_baidu_map_runtime,
)
from app.workflow_contracts import UtcClock


def test_runtime_public_surface_and_keyword_only_availability() -> None:
    assert set(name for name in BaiduMapRuntime.__dict__ if not name.startswith("_")) == {
        "adapter", "map_launch", "get_capability_availability", "aclose"
    }
    signature = inspect.signature(BaiduMapRuntime.get_capability_availability)
    assert tuple(signature.parameters) == ("self", "capability")
    assert signature.parameters["capability"].kind is inspect.Parameter.KEYWORD_ONLY
    hints = get_type_hints(BaiduMapRuntime.get_capability_availability)
    assert hints == {"capability": MapCapability, "return": MapCapabilityAvailability}
    assert get_type_hints(BaiduMapRuntime.adapter.fget)["return"] is BaiduMapAdapter
    assert get_type_hints(BaiduMapRuntime.map_launch.fget)["return"] is MapLaunchCapability


def test_public_factory_signature_is_exact_and_has_no_transport_injection() -> None:
    signature = inspect.signature(create_baidu_map_runtime)
    assert tuple(signature.parameters) == ("settings", "clock", "limits")
    assert all(item.kind is inspect.Parameter.KEYWORD_ONLY for item in signature.parameters.values())
    assert signature.parameters["limits"].default is DEFAULT_BAIDU_MAP_LIMITS
    hints = get_type_hints(create_baidu_map_runtime)
    assert hints["settings"] is BaiduMapSettings
    assert hints["clock"] is UtcClock
    assert hints["limits"] is BaiduMapLimits
    assert "endpoint" not in str(signature)
    assert "transport" not in str(signature)
    assert isinstance(create_baidu_map_runtime, type(lambda: None))


def test_runtime_is_not_a_session_or_mcp_object_projection() -> None:
    annotations = str(get_type_hints(BaiduMapRuntime, include_extras=True))
    assert "ClientSession" not in annotations
    assert "CallToolResult" not in annotations
    assert not issubclass(BaiduMapRuntime, AbstractAsyncContextManager)
