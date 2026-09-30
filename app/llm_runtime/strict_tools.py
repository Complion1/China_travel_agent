"""DeepSeek wire schemas; full business validation remains local."""

from copy import deepcopy
from typing import Any


def strict_tool_definitions(tools: tuple[dict[str, object], ...]) -> list[dict[str, Any]]:
    def schema(node: dict[str, Any], definitions: dict[str, Any], refs: tuple[str, ...] = ()) -> dict[str, Any]:
        result = deepcopy(node)
        # These planning DTOs are acyclic. Inline references so the vendor sees
        # every constraint directly, without depending on its $defs dialect.
        if "$ref" in result:
            ref = result.pop("$ref")
            if not ref.startswith("#/$defs/") or ref in refs:
                raise ValueError("strict tools require acyclic local definitions")
            return schema({**definitions[ref.removeprefix("#/$defs/")], **result},
                          definitions, (*refs, ref))
        result.pop("$defs", None)
        # Version constants are supplied by local DTO defaults, not the model.
        if result.get("type") == "object":
            properties = result.get("properties", {})
            version = properties.get("schema_version", {})
            if "const" in version and version.get("default") == version["const"]:
                properties.pop("schema_version")
        # Unsupported keywords must not silently erase the model's knowledge
        # of limits still enforced by the local DTO. They remain instructions,
        # not vendor-enforced constraints.
        hints = []
        for lower, upper, unit in (("minItems", "maxItems", "items"),
                                   ("minLength", "maxLength", "characters")):
            if lower in result:
                hints.append(f"Minimum {result[lower]} {unit}.")
            if upper in result:
                hints.append(f"Maximum {result[upper]} {unit}.")
        if result.get("format") in {"date", "date-time"}:
            hints.append("Use ISO 8601 " + result["format"] + ".")
        if hints:
            result["description"] = " ".join(
                part for part in (result.get("description", ""),
                                 "Local validation: " + " ".join(hints)) if part
            )
        for key in ("title", "default", "minLength", "maxLength", "minItems", "maxItems", "discriminator"):
            result.pop(key, None)
        if result.get("format") not in {"email", "hostname", "ipv4", "ipv6", "uuid"}:
            result.pop("format", None)
        if "const" in result:
            result["enum"] = [result.pop("const")]
        if "oneOf" in result:
            result["anyOf"] = result.pop("oneOf")
        for key in ("properties",):
            if key in result:
                result[key] = {name: schema(value, definitions, refs) for name, value in result[key].items()}
        for key in ("anyOf", "allOf"):
            if key in result:
                result[key] = [schema(value, definitions, refs) for value in result[key]]
        if isinstance(result.get("items"), dict):
            result["items"] = schema(result["items"], definitions, refs)
        if result.get("type") == "object":
            if isinstance(result.get("additionalProperties"), dict):
                raise ValueError("strict tools do not support dynamic object keys")
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    result = deepcopy(list(tools))
    for tool in result:
        function = tool["function"]
        parameters = function["parameters"]
        function["parameters"] = schema(parameters, parameters.get("$defs", {}))
        # Already-wrapped function dictionaries can bypass SDK strict injection.
        function["strict"] = True
    return result
