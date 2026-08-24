from __future__ import annotations

from copy import deepcopy

import pytest

from app.baidu_map import BaiduMapToolSchemaError, MapCapability
from app.baidu_map._tool_registry import (
    MAP_BINDING_MANIFEST_VERSION,
    _enum_values,
    _is_compatible,
    _schema_types,
    validate_tool_registry,
)
from tests.support.baidu_map_fake_mcp import qualified_tools


def _tool(tools, name):
    return next(item for item in tools if item.name == name)


def test_qualified_direct_string_manifest_binds_exact_seven_core_tools() -> None:
    tools = qualified_tools()
    assert all(type(tool.inputSchema["type"]) is str for tool in tools)
    registry = validate_tool_registry(tools)
    assert MAP_BINDING_MANIFEST_VERSION == "1.7"
    assert tuple(registry.bindings) == tuple(cap for cap in MapCapability if cap is not MapCapability.MAP_LAUNCH)
    assert registry.for_capability(MapCapability.ROUTE_MATRIX).tool_name == "map_directions_matrix"
    assert not registry.for_capability(MapCapability.PLACE_SEARCH).supports_result_limit


def test_qualified_optional_integer_limit_is_enabled() -> None:
    registry = validate_tool_registry(qualified_tools(search_limit=True))
    assert registry.for_capability(MapCapability.PLACE_SEARCH).supports_result_limit


def test_complete_explicit_integer_limit_enum_is_compatible() -> None:
    tools = qualified_tools(search_limit=True)
    _tool(tools, "map_search_places").inputSchema["properties"]["limit"]["enum"] = list(
        range(1, 51)
    )
    assert validate_tool_registry(tools).for_capability(MapCapability.PLACE_SEARCH).supports_result_limit


def test_known_always_sent_required_fields_and_reverse_optional_required_are_compatible() -> None:
    tools = qualified_tools()
    _tool(tools, "map_geocode").inputSchema["required"].append("is_chinese_mainland")
    _tool(tools, "map_reverse_geocode").inputSchema["required"] = ["latitude", "longitude"]
    _tool(tools, "map_weather").inputSchema["required"] = ["location", "is_china"]
    registry = validate_tool_registry(tools)
    assert len(registry.bindings) == 7


def test_reverse_direct_object_schema_remains_compatible_without_aliases() -> None:
    tools = qualified_tools()
    schema = _tool(tools, "map_reverse_geocode").inputSchema
    del schema["anyOf"]
    del schema["properties"]["lat"]
    del schema["properties"]["lng"]
    assert len(validate_tool_registry(tools).bindings) == 7


@pytest.mark.parametrize(
    "mutation",
    [
        "not_list",
        "branch_count",
        "branch_not_object",
        "extra_branch_keyword",
        "required_not_list",
        "empty_required",
        "duplicate_required_name",
        "duplicate_branch",
        "unknown_required",
        "missing_outbound_branch",
        "referenced_property_missing",
        "referenced_property_type",
        "additional_root_combo",
        "unexpected_root_constraint",
    ],
)
def test_reverse_root_anyof_is_strictly_bounded(mutation: str) -> None:
    tools = qualified_tools()
    schema = _tool(tools, "map_reverse_geocode").inputSchema
    branches = schema["anyOf"]
    if mutation == "not_list":
        schema["anyOf"] = {"required": ["latitude", "longitude"]}
    elif mutation == "branch_count":
        branches.pop()
    elif mutation == "branch_not_object":
        branches[0] = ["lat", "lng"]
    elif mutation == "extra_branch_keyword":
        branches[0]["type"] = "object"
    elif mutation == "required_not_list":
        branches[0]["required"] = "lat"
    elif mutation == "empty_required":
        branches[0]["required"] = []
    elif mutation == "duplicate_required_name":
        branches[0]["required"] = ["lat", "lat"]
    elif mutation == "duplicate_branch":
        branches[0]["required"] = ["latitude", "longitude"]
    elif mutation == "unknown_required":
        branches[0]["required"] = ["lat", "hidden"]
    elif mutation == "missing_outbound_branch":
        branches[1]["required"] = ["latitude"]
    elif mutation == "referenced_property_missing":
        del schema["properties"]["lat"]
    elif mutation == "referenced_property_type":
        schema["properties"]["lat"] = {"type": "integer"}
    elif mutation == "additional_root_combo":
        schema["oneOf"] = [{"required": ["latitude", "longitude"]}]
    elif mutation == "unexpected_root_constraint":
        schema["if"] = {"required": ["lat"]}
    with pytest.raises(BaiduMapToolSchemaError):
        validate_tool_registry(tools)


def test_complete_explicit_mode_enums_are_compatible() -> None:
    tools = qualified_tools()
    _tool(tools, "map_directions_matrix").inputSchema["properties"]["model"]["enum"] = [
        "walking", "driving", "riding"
    ]
    _tool(tools, "map_directions").inputSchema["properties"]["model"]["enum"] = [
        "transit", "walking", "driving", "riding"
    ]
    assert len(validate_tool_registry(tools).bindings) == 7


def test_unknown_optional_tool_and_type_array_property_are_ignored_without_dynamic_binding() -> None:
    tools = qualified_tools()
    _tool(tools, "map_geocode").inputSchema["properties"]["future_optional"] = {
        "type": ["string", "null"]
    }
    tools.append(deepcopy(tools[0]))
    tools[-1].name = "map_guessable_new_tool"
    registry = validate_tool_registry(tools)
    assert len(registry.bindings) == 7
    assert all(binding.tool_name != "map_guessable_new_tool" for binding in registry.bindings.values())


def test_registry_schema_helpers_fail_closed_for_malformed_direct_shapes() -> None:
    assert _schema_types(None) is None
    assert _schema_types({}) is None
    assert _schema_types({"type": []}) is None
    assert _schema_types({"type": ["string", 1]}) is None
    assert _schema_types({"type": ["string", "string"]}) is None
    assert _schema_types({"type": ["number", "number"]}) is None
    assert _schema_types({"type": "string", "oneOf": []}) is None
    assert not _is_compatible({"type": "integer"}, "number")
    assert not _is_compatible({"type": ["number", "integer"]}, "number")
    assert _enum_values({"enum": ["walking", "driving"]}) == frozenset({"walking", "driving"})
    assert _enum_values({"enum": "walking"}) == frozenset()
    assert _enum_values({"enum": [{"unhashable": []}]}) == frozenset()


def test_registry_rejects_malformed_properties_and_incomplete_search_limit_enum() -> None:
    tools = qualified_tools(search_limit=True)
    _tool(tools, "map_geocode").inputSchema["properties"] = None
    with pytest.raises(BaiduMapToolSchemaError):
        validate_tool_registry(tools)

    tools = qualified_tools()
    _tool(tools, "map_geocode").inputSchema["required"] = ["address", "address"]
    with pytest.raises(BaiduMapToolSchemaError):
        validate_tool_registry(tools)

    tools = qualified_tools(search_limit=True)
    _tool(tools, "map_search_places").inputSchema["properties"]["limit"]["enum"] = [1]
    with pytest.raises(BaiduMapToolSchemaError):
        validate_tool_registry(tools)


def test_registry_rejects_duplicate_schema_type_members() -> None:
    tools = qualified_tools()
    _tool(tools, "map_geocode").inputSchema["properties"]["address"]["type"] = [
        "string",
        "string",
    ]
    with pytest.raises(BaiduMapToolSchemaError):
        validate_tool_registry(tools)


def test_registry_rejects_bool_as_integer_limit_enum_member() -> None:
    tools = qualified_tools(search_limit=True)
    _tool(tools, "map_search_places").inputSchema["properties"]["limit"]["enum"] = [
        True,
        *range(2, 51),
    ]
    with pytest.raises(BaiduMapToolSchemaError):
        validate_tool_registry(tools)


@pytest.mark.parametrize(
    "mutation",
    [
        "root_object",
        "address_string",
        "directions_model_string",
        "reverse_latitude_number",
        "reverse_longitude_number_integer",
        "search_limit_integer",
        "reverse_anyof_referenced_coordinate",
    ],
)
def test_registry_rejects_type_arrays_at_every_consumed_publication_gate(
    mutation: str,
) -> None:
    tools = qualified_tools(search_limit=mutation == "search_limit_integer")
    if mutation == "root_object":
        _tool(tools, "map_geocode").inputSchema["type"] = ["object"]
    elif mutation == "address_string":
        _tool(tools, "map_geocode").inputSchema["properties"]["address"]["type"] = [
            "string"
        ]
    elif mutation == "directions_model_string":
        _tool(tools, "map_directions").inputSchema["properties"]["model"]["type"] = [
            "string"
        ]
    elif mutation == "reverse_latitude_number":
        _tool(tools, "map_reverse_geocode").inputSchema["properties"]["latitude"][
            "type"
        ] = ["number"]
    elif mutation == "reverse_longitude_number_integer":
        _tool(tools, "map_reverse_geocode").inputSchema["properties"]["longitude"][
            "type"
        ] = ["number", "integer"]
    elif mutation == "search_limit_integer":
        _tool(tools, "map_search_places").inputSchema["properties"]["limit"]["type"] = [
            "integer"
        ]
    elif mutation == "reverse_anyof_referenced_coordinate":
        _tool(tools, "map_reverse_geocode").inputSchema["properties"]["lat"]["type"] = [
            "number"
        ]
    with pytest.raises(BaiduMapToolSchemaError):
        validate_tool_registry(tools)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "pagination",
        "root",
        "unknown_required",
        "model_missing",
        "model_type",
        "model_enum",
        "reverse_latitude_missing",
        "reverse_latitude_type",
        "limit_type",
        "enum_malformed",
        "dynamic_enum",
        "conditionally_required",
        "oneof_unknown_required",
        "property_enum_missing",
        "oneof_duplicate",
        "anyof_mixed",
        "anyof_conflict",
        "direct_type_with_combo",
        "reverse_integer_only",
        "combo_malformed",
    ],
)
def test_registry_fails_closed_for_contract_drift(mutation: str) -> None:
    tools = qualified_tools(search_limit=mutation == "limit_type")
    next_cursor = None
    if mutation == "missing":
        tools.pop()
    elif mutation == "duplicate":
        tools.append(deepcopy(tools[0]))
    elif mutation == "pagination":
        next_cursor = "next"
    elif mutation == "root":
        _tool(tools, "map_geocode").inputSchema["type"] = "array"
    elif mutation == "unknown_required":
        _tool(tools, "map_geocode").inputSchema["required"].append("unknown")
    elif mutation == "model_missing":
        del _tool(tools, "map_directions").inputSchema["properties"]["model"]
    elif mutation == "model_type":
        _tool(tools, "map_directions_matrix").inputSchema["properties"]["model"] = {"type": "number"}
    elif mutation == "model_enum":
        _tool(tools, "map_directions").inputSchema["properties"]["model"]["enum"] = ["walking"]
    elif mutation == "reverse_latitude_missing":
        del _tool(tools, "map_reverse_geocode").inputSchema["properties"]["latitude"]
    elif mutation == "reverse_latitude_type":
        _tool(tools, "map_reverse_geocode").inputSchema["properties"]["latitude"] = {"type": "string"}
    elif mutation == "limit_type":
        _tool(tools, "map_search_places").inputSchema["properties"]["limit"] = {"type": "string"}
    elif mutation == "enum_malformed":
        _tool(tools, "map_directions").inputSchema["properties"]["model"]["enum"] = [{"bad": True}]
    elif mutation == "dynamic_enum":
        _tool(tools, "map_geocode").inputSchema["properties"]["address"]["enum"] = ["one-address"]
    elif mutation == "conditionally_required":
        _tool(tools, "map_search_places").inputSchema["required"].append("region")
    elif mutation == "oneof_unknown_required":
        _tool(tools, "map_geocode").inputSchema["oneOf"] = [
            {"type": "object", "required": ["address", "hidden"]}
        ]
    elif mutation == "property_enum_missing":
        _tool(tools, "map_directions").inputSchema["properties"]["model"] = {
            "type": "string", "enum": ["walking"]
        }
    elif mutation == "oneof_duplicate":
        _tool(tools, "map_directions").inputSchema["properties"]["model"] = {
            "oneOf": [{"type": "string"}, {"type": "string"}]
        }
    elif mutation == "anyof_mixed":
        _tool(tools, "map_directions").inputSchema["properties"]["model"] = {
            "anyOf": [{"type": "string"}, {"type": "number"}]
        }
    elif mutation == "anyof_conflict":
        _tool(tools, "map_directions").inputSchema["properties"]["model"] = {
            "anyOf": [
                {"type": "string", "enum": ["walking"]},
                {"type": "string", "enum": ["driving"]},
            ]
        }
    elif mutation == "direct_type_with_combo":
        _tool(tools, "map_directions").inputSchema["properties"]["model"] = {
            "type": "string", "oneOf": [{"type": "string"}]
        }
    elif mutation == "reverse_integer_only":
        _tool(tools, "map_reverse_geocode").inputSchema["properties"]["latitude"] = {
            "type": "integer"
        }
    elif mutation == "combo_malformed":
        _tool(tools, "map_directions").inputSchema["properties"]["model"] = {
            "oneOf": "malformed"
        }
    with pytest.raises(BaiduMapToolSchemaError):
        validate_tool_registry(tools, next_cursor=next_cursor)
