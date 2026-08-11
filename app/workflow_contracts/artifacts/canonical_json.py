"""Private canonical JSON helpers for artifact content."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from enum import Enum
from uuid import UUID

from app.workflow_contracts.artifacts.models import SAFE_INTEGER_MAX, JsonValue
from app.workflow_contracts.errors import ArtifactRequestValidationError


def _fail() -> None:
    raise ArtifactRequestValidationError() from None


def _check_string(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _fail()
    return value


def _normalize_value(value: object) -> object:
    if isinstance(value, Enum):
        return _normalize_value(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            _fail()
        try:
            normalized = value.astimezone(timezone.utc).isoformat()
        except (OverflowError, ValueError):
            _fail()
        return normalized.replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return None
    if type(value) is bool:
        return value
    if type(value) is int:
        if not -SAFE_INTEGER_MAX <= value <= SAFE_INTEGER_MAX:
            _fail()
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail()
        return 0.0 if value == 0.0 else value
    if type(value) is str:
        return _check_string(value)
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                _fail()
            normalized[_check_string(key)] = _normalize_value(item)
        return normalized
    _fail()
    return None


def _canonical_json_bytes(value: dict[str, JsonValue]) -> bytes:
    if type(value) is not dict:
        _fail()
    try:
        normalized = _normalize_value(value)
    except ArtifactRequestValidationError:
        raise
    except (RecursionError, TypeError, ValueError, OverflowError):
        _fail()
    try:
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError, OverflowError):
        _fail()
    raise AssertionError("unreachable")


def _content_hash(canonical_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()
