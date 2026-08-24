"""Private, fail-closed binding manifest for the qualified MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from mcp.types import Tool

from app.baidu_map.errors import BaiduMapToolSchemaError
from app.baidu_map.models.common import MapCapability


MAP_BINDING_MANIFEST_VERSION = "1.7"

_REVERSE_COORDINATE_PROPERTIES = frozenset({"lat", "lng", "latitude", "longitude"})
_REVERSE_OUTBOUND_REQUIRED = frozenset({"latitude", "longitude"})


@dataclass(frozen=True, slots=True)
class _ManifestEntry:
    capability: MapCapability
    tool_name: str
    required: frozenset[str]
    allowed_required: frozenset[str]
    properties: Mapping[str, str]
    enum_values: Mapping[str, frozenset[str]]
    optional_properties: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ToolBinding:
    capability: MapCapability
    tool_name: str
    supports_result_limit: bool = False


@dataclass(frozen=True, slots=True)
class ToolRegistry:
    bindings: Mapping[MapCapability, ToolBinding]

    def for_capability(self, capability: MapCapability) -> ToolBinding:
        return self.bindings[capability]


_ENTRIES = (
    _ManifestEntry(
        MapCapability.GEOCODING,
        "map_geocode",
        frozenset({"address"}),
        frozenset({"address", "is_chinese_mainland"}),
        MappingProxyType(
            {"address": "string", "city": "string", "is_chinese_mainland": "string"}
        ),
        MappingProxyType({"is_chinese_mainland": frozenset({"true"})}),
    ),
    _ManifestEntry(
        MapCapability.REVERSE_GEOCODING,
        "map_reverse_geocode",
        frozenset(),
        frozenset({"latitude", "longitude"}),
        MappingProxyType({"latitude": "number", "longitude": "number"}),
        MappingProxyType({}),
    ),
    _ManifestEntry(
        MapCapability.PLACE_SEARCH,
        "map_search_places",
        frozenset({"query"}),
        frozenset({"query", "is_chinese_mainland"}),
        MappingProxyType(
            {
                "query": "string",
                "tag": "string",
                "region": "string",
                "location": "string",
                "radius": "integer",
                "is_chinese_mainland": "string",
            }
        ),
        MappingProxyType({"is_chinese_mainland": frozenset({"true"})}),
        frozenset({"limit"}),
    ),
    _ManifestEntry(
        MapCapability.PLACE_DETAILS,
        "map_place_details",
        frozenset({"uid"}),
        frozenset({"uid", "is_chinese_mainland"}),
        MappingProxyType({"uid": "string", "is_chinese_mainland": "string"}),
        MappingProxyType({"is_chinese_mainland": frozenset({"true"})}),
    ),
    _ManifestEntry(
        MapCapability.ROUTE_MATRIX,
        "map_directions_matrix",
        frozenset({"origins", "destinations"}),
        frozenset({"origins", "destinations"}),
        MappingProxyType(
            {"model": "string", "origins": "string", "destinations": "string"}
        ),
        MappingProxyType(
            {"model": frozenset({"driving", "riding", "walking"})}
        ),
    ),
    _ManifestEntry(
        MapCapability.DIRECTIONS,
        "map_directions",
        frozenset({"origin", "destination"}),
        frozenset({"origin", "destination"}),
        MappingProxyType(
            {
                "model": "string",
                "origin": "string",
                "destination": "string",
                "is_chinese_mainland": "string",
            }
        ),
        MappingProxyType(
            {
                "model": frozenset({"walking", "driving", "riding", "transit"}),
                "is_chinese_mainland": frozenset({"true"}),
            }
        ),
    ),
    _ManifestEntry(
        MapCapability.WEATHER,
        "map_weather",
        frozenset(),
        frozenset({"location", "is_china"}),
        MappingProxyType({"location": "string", "is_china": "string"}),
        MappingProxyType({"is_china": frozenset({"true"})}),
    ),
)


def _schema_types(schema: object) -> frozenset[str] | None:
    if type(schema) is not dict:
        return None
    # Combination keywords are not safe to flatten: their branch-level
    # required/enum/const constraints can narrow or invalidate the outbound
    # shape even when a direct ``type`` is also present.  The sole bounded
    # root-level exception is validated separately for reverse geocoding.
    if any(keyword in schema for keyword in ("oneOf", "anyOf", "allOf", "not", "const")):
        return None
    raw_type = schema.get("type")
    if type(raw_type) is str:
        return frozenset({raw_type})
    return None


def _is_compatible(schema: object, expected: str) -> bool:
    types = _schema_types(schema)
    if types is None:
        return False
    if expected == "number":
        # An integer-only property is not proof that arbitrary legal DTO
        # coordinates (which are real numbers) can be sent.
        return types == {"number"}
    return types == {expected}


def _reverse_root_schema_is_compatible(
    schema: dict[str, object], properties: dict[str, object]
) -> bool:
    if "anyOf" not in schema:
        return _schema_types(schema) == frozenset({"object"})
    if not set(schema) <= {"type", "properties", "required", "anyOf"}:
        return False
    if type(schema.get("type")) is not str or schema["type"] != "object":
        return False
    if any(keyword in schema for keyword in ("oneOf", "allOf", "not", "const")):
        return False
    branches = schema["anyOf"]
    if type(branches) is not list or len(branches) != 2:
        return False
    required_sets: list[frozenset[str]] = []
    for branch in branches:
        if type(branch) is not dict or set(branch) != {"required"}:
            return False
        required = branch["required"]
        if (
            type(required) is not list
            or not required
            or not all(type(name) is str for name in required)
            or len(set(required)) != len(required)
        ):
            return False
        names = frozenset(required)
        if not names <= _REVERSE_COORDINATE_PROPERTIES:
            return False
        if any(
            name not in properties or not _is_compatible(properties[name], "number")
            for name in names
        ):
            return False
        required_sets.append(names)
    return (
        len(set(required_sets)) == 2
        and _REVERSE_OUTBOUND_REQUIRED in required_sets
    )


def _enum_values(schema: object) -> frozenset[object] | None:
    if type(schema) is not dict or "enum" not in schema:
        return None
    raw = schema["enum"]
    if type(raw) is not list:
        return frozenset()
    try:
        return frozenset(raw)
    except TypeError:
        return frozenset()


def _strict_integer_enum(schema: object) -> frozenset[int] | None:
    if type(schema) is not dict or "enum" not in schema:
        return None
    raw = schema["enum"]
    if type(raw) is not list or not all(type(item) is int for item in raw):
        return frozenset()
    return frozenset(raw)


def _validate_entry(tool: Tool, entry: _ManifestEntry) -> ToolBinding:
    schema: Any = tool.inputSchema
    if type(schema) is not dict:
        raise BaiduMapToolSchemaError(capability=entry.capability)
    properties = schema.get("properties")
    required = schema.get("required", [])
    if type(properties) is not dict or type(required) is not list:
        raise BaiduMapToolSchemaError(capability=entry.capability)
    root_is_compatible = (
        _reverse_root_schema_is_compatible(schema, properties)
        if entry.capability is MapCapability.REVERSE_GEOCODING
        else _schema_types(schema) == frozenset({"object"})
    )
    if not root_is_compatible:
        raise BaiduMapToolSchemaError(capability=entry.capability)
    if not all(type(name) is str for name in required) or len(set(required)) != len(required):
        raise BaiduMapToolSchemaError(capability=entry.capability)
    actual_required = frozenset(required)
    if not entry.required <= actual_required or not actual_required <= entry.allowed_required:
        raise BaiduMapToolSchemaError(capability=entry.capability)
    for name, expected_type in entry.properties.items():
        if name not in properties or not _is_compatible(properties[name], expected_type):
            raise BaiduMapToolSchemaError(capability=entry.capability)
        allowed = entry.enum_values.get(name)
        actual = _enum_values(properties[name])
        if actual is not None and (allowed is None or not allowed <= actual):
            raise BaiduMapToolSchemaError(capability=entry.capability)
    supports_limit = False
    if entry.capability is MapCapability.PLACE_SEARCH and "limit" in properties:
        if not _is_compatible(properties["limit"], "integer"):
            raise BaiduMapToolSchemaError(capability=entry.capability)
        limit_enum = _strict_integer_enum(properties["limit"])
        if limit_enum is not None and not frozenset(range(1, 51)) <= limit_enum:
            raise BaiduMapToolSchemaError(capability=entry.capability)
        supports_limit = True
    return ToolBinding(entry.capability, entry.tool_name, supports_limit)


def validate_tool_registry(tools: object, *, next_cursor: object = None) -> ToolRegistry:
    """Validate the one-page tools/list result and discard its raw schemas."""
    if next_cursor is not None or type(tools) is not list or not all(type(t) is Tool for t in tools):
        raise BaiduMapToolSchemaError()
    by_name: dict[str, list[Tool]] = {}
    for tool in tools:
        by_name.setdefault(tool.name, []).append(tool)
    bindings: dict[MapCapability, ToolBinding] = {}
    for entry in _ENTRIES:
        matches = by_name.get(entry.tool_name, [])
        if len(matches) != 1:
            raise BaiduMapToolSchemaError(capability=entry.capability)
        bindings[entry.capability] = _validate_entry(matches[0], entry)
    return ToolRegistry(MappingProxyType(bindings))


__all__ = ("MAP_BINDING_MANIFEST_VERSION", "ToolBinding", "ToolRegistry", "validate_tool_registry")
