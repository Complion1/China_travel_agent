from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from app.llm_runtime.models import LLMErrorCode, LLMRuntimeError, PromptEnvelope, ModelRole
from app.llm_runtime.profiles import ModelProfile

JSON_ONLY_INSTRUCTION = (
    "\n\nReturn exactly one JSON object matching the supplied JSON Schema."
    "\nDo not wrap it in Markdown. Do not add commentary before or after the JSON."
    "\nEvery required property must be present. Use null only where the schema permits null."
    "\nJSON Schema:\n"
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def structured_system_text(system_text: str, json_schema: dict[str, object]) -> str:
    return system_text + JSON_ONLY_INSTRUCTION + canonical_json(json_schema)


def validate_consumer_schema(schema: type) -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        errors.append("not a Pydantic BaseModel")
        return errors
    config = schema.model_config
    if config.get("extra") != "forbid":
        errors.append("schema must have extra='forbid'")
    if not config.get("strict", False):
        errors.append("schema must have strict=True")
    try:
        js = schema.model_json_schema()
    except Exception as e:
        errors.append(f"model_json_schema() failed: {e}")
        return errors
    if js.get("type") != "object":
        errors.append("schema root must be object type")
    try:
        canonical_json(js)
    except (TypeError, ValueError) as exc:
        errors.append(f"schema is not JSON serializable: {type(exc).__name__}")
    return errors


_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_tool_contract(name: str, description: str, args_schema: type) -> list[str]:
    errors: list[str] = []
    if not _NAME_RE.match(name):
        errors.append(f"tool name '{name}' must match ^[A-Za-z0-9_-]{{1,64}}$")
    desc = description.strip()
    if not (1 <= len(desc) <= 1024):
        errors.append(f"tool description must be 1..1024 chars, got {len(desc)}")
    if not isinstance(args_schema, type) or not issubclass(args_schema, BaseModel):
        errors.append("args_schema must be a Pydantic BaseModel")
    else:
        config = args_schema.model_config
        if config.get("extra") != "forbid":
            errors.append("args_schema must have extra='forbid'")
        if not config.get("strict", False):
            errors.append("args_schema must have strict=True")
        try:
            js = args_schema.model_json_schema()
            if js.get("type") != "object":
                errors.append("args_schema root must be object type")
            canonical_json(js)
        except Exception as exc:
            errors.append(f"args_schema is not JSON serializable: {type(exc).__name__}")
    return errors


def canonical_request_bytes(
    *,
    profile: ModelProfile,
    prompt: PromptEnvelope,
    json_schema: dict[str, object] | None,
    tools: tuple[dict[str, object], ...] = (),
    internal_tool_history: tuple[dict[str, object], ...] = (),
) -> bytes:
    messages: list[dict[str, object]] = [{"role": "system", "content": prompt.system_text}]
    if json_schema:
        messages[0]["content"] = structured_system_text(prompt.system_text, json_schema)

    messages.append({"role": "user", "content": prompt.user_text})
    messages.extend(internal_tool_history)

    body: dict[str, object] = {
        "model": profile.model_name,
        "thinking": {"type": profile.thinking_type},
        "reasoning_effort": profile.reasoning_effort,
        "max_tokens": profile.max_output_tokens,
        "messages": messages,
    }
    if tools:
        body["tools"] = list(tools)
    raw = canonical_json(body)
    return raw.encode("utf-8")


def check_byte_gate(actual_bytes: int, limit: int, role: ModelRole) -> LLMRuntimeError | None:
    if actual_bytes > limit:
        return LLMRuntimeError(
            code=LLMErrorCode.INPUT_TOO_LARGE,
            role=role,
            retryable=False,
            possible_duplicate=False,
            actual_bytes=actual_bytes,
            max_bytes=limit,
        )
    return None
