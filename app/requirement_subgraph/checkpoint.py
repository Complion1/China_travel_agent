"""Closed-set checkpoint serializer for module 05."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command, Interrupt, Overwrite, Send
from pydantic import BaseModel, TypeAdapter

from app.requirements import (
    ConfirmedTravelRequirement,
    NeedClarification,
    NeedConfirmation,
    RequirementDomainOutcome,
    RequirementDomainState,
    RequirementExtractionFailed,
    RequirementInvalidated,
)
from app.workflow_contracts import (
    ArtifactRef,
    DeliveryStatus,
    TerminalOutcome,
    TravelSessionState,
    WorkflowPhase,
    parse_travel_session_state_json,
    serialize_travel_session_state,
)

from app.requirement_subgraph.errors import RequirementSubgraphStateError
from app.requirement_subgraph.models import _fresh_model


_STATE_TAG = "requirement_subgraph.travel_session_state.2.0"
_REQUIREMENT_STATE_TAG = "requirement_subgraph.requirement_domain_state.1.0"
_REQUIREMENT_OUTCOME_TAG = "requirement_subgraph.requirement_domain_outcome.1.0"
_TERMINAL_OUTCOME_TAG = "requirement_subgraph.terminal_outcome.1.0"
_ARTIFACT_TUPLE_TAG = "requirement_subgraph.artifact_ref_tuple.1.0"
_INTERRUPT_TUPLE_TAG = "requirement_subgraph.interrupt_tuple.1.0"
_OWN_TAGS = frozenset(
    {
        _STATE_TAG,
        _REQUIREMENT_STATE_TAG,
        _REQUIREMENT_OUTCOME_TAG,
        _TERMINAL_OUTCOME_TAG,
        _ARTIFACT_TUPLE_TAG,
        _INTERRUPT_TUPLE_TAG,
    }
)
_DELEGATE_TAGS = frozenset({"null", "bytes", "bytearray", "json", "msgpack"})
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_ALLOWED_JSON_MODULES = (
    ("app", "workflow_contracts", "identifiers", "WorkflowPhase"),
    ("app", "workflow_contracts", "state", "DeliveryStatus"),
)
_ALLOWED_MSGPACK_MODULES = (
    ("app.workflow_contracts.identifiers", "WorkflowPhase"),
    ("app.workflow_contracts.state", "DeliveryStatus"),
)


class _ParsedObject(dict[str, object]):
    __slots__ = ("duplicates",)

    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        super().__init__()
        seen: set[str] = set()
        duplicates: list[str] = []
        for key, value in pairs:
            if key in seen:
                duplicates.append(key)
            seen.add(key)
            self[key] = value
        self.duplicates = tuple(duplicates)


_INVALID_NUMBER = object()


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        raise RequirementSubgraphStateError() from None


def _json_tree_from_bytes(data: bytes) -> object:
    if type(data) is not bytes or data.startswith(b"\xef\xbb\xbf"):
        raise RequirementSubgraphStateError() from None
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_ParsedObject,
            parse_constant=lambda _value: _INVALID_NUMBER,
        )
    except Exception:
        raise RequirementSubgraphStateError() from None


def _invalid_json_tree(value: object) -> bool:
    if value is _INVALID_NUMBER:
        return True
    if isinstance(value, _ParsedObject):
        return bool(value.duplicates) or any(
            _invalid_json_tree(item) for item in value.values()
        )
    if type(value) is list:
        return any(_invalid_json_tree(item) for item in value)
    return False


def _fresh_tagged(model_type: object, value: object) -> BaseModel:
    if not isinstance(value, BaseModel):
        raise RequirementSubgraphStateError() from None
    try:
        return cast(BaseModel, _fresh_model(model_type, value))
    except Exception:
        raise RequirementSubgraphStateError() from None


def _model_json(value: BaseModel) -> bytes:
    return _canonical_json(
        value.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
    )


def _artifact_tuple_json(value: tuple[ArtifactRef, ...]) -> bytes:
    try:
        adapter = TypeAdapter(tuple[ArtifactRef, ...])
        first = adapter.validate_python(value, strict=True)
        return _canonical_json(adapter.dump_python(first, mode="json"))
    except Exception:
        raise RequirementSubgraphStateError() from None


def _validate_tree(value: object) -> None:
    value_type = type(value)
    if value is None or value_type in (bool, int, str, bytes, bytearray):
        return
    if value_type is float:
        if not math.isfinite(value):
            raise RequirementSubgraphStateError() from None
        return
    if value_type in (UUID, datetime, date, time, timedelta, timezone, ZoneInfo):
        return
    if value_type in (WorkflowPhase, DeliveryStatus):
        return
    if value_type is Interrupt:
        if type(value.id) is not str:
            raise RequirementSubgraphStateError() from None
        _validate_tree(value.value)
        return
    if value_type is Overwrite:
        if value.type != "__overwrite__":
            raise RequirementSubgraphStateError() from None
        _validate_tree(value.value)
        return
    if value_type is Send:
        if type(value.node) is not str or value.timeout is not None:
            raise RequirementSubgraphStateError() from None
        _validate_tree(value.arg)
        return
    if value_type is Command:
        if value.graph is not None and type(value.graph) is not str:
            raise RequirementSubgraphStateError() from None
        if value.update is not None:
            _validate_tree(value.update)
        if value.resume is not None:
            _validate_tree(value.resume)
        goto = value.goto
        if type(goto) in (tuple, list):
            for item in goto:
                if type(item) not in (str, Send):
                    raise RequirementSubgraphStateError() from None
                _validate_tree(item)
        elif type(goto) not in (str, Send):
            raise RequirementSubgraphStateError() from None
        return
    if value_type is list:
        for item in value:
            _validate_tree(item)
        return
    if value_type is dict:
        if value.get("lc") == 2 and value.get("type") == "constructor":
            raise RequirementSubgraphStateError() from None
        for key, item in value.items():
            _validate_tree(key)
            _validate_tree(item)
        return
    raise RequirementSubgraphStateError() from None


def _require_exact_interrupt_tuple(value: object) -> tuple[Interrupt, ...] | None:
    if type(value) is not tuple:
        return None
    if len(value) == 0 or not all(type(item) is Interrupt for item in value):
        return None
    result = cast(tuple[Interrupt, ...], value)
    for item in result:
        if _HEX32.fullmatch(item.id) is None:
            raise RequirementSubgraphStateError() from None
        _validate_json_value(item.value)
    return result


def _require_exact_artifact_tuple(value: object) -> tuple[ArtifactRef, ...] | None:
    if type(value) is not tuple:
        return None
    if not all(type(item) is ArtifactRef for item in value):
        return None
    return cast(tuple[ArtifactRef, ...], value)


def _validate_json_value(value: object) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise RequirementSubgraphStateError() from None
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item)
        return
    if type(value) is dict or type(value) is _ParsedObject:
        for key, item in value.items():
            if type(key) is not str:
                raise RequirementSubgraphStateError() from None
            _validate_json_value(item)
        return
    raise RequirementSubgraphStateError() from None


def _plain_json_tree(value: object) -> object:
    if type(value) is _ParsedObject:
        return {key: _plain_json_tree(item) for key, item in value.items()}
    if type(value) is list:
        return [_plain_json_tree(item) for item in value]
    return value


def _validate_json_constructor(value: _ParsedObject) -> None:
    if set(value) != {"lc", "type", "id", "args"}:
        raise RequirementSubgraphStateError() from None
    if value["lc"] != 2 or value["type"] != "constructor":
        raise RequirementSubgraphStateError() from None
    identifier = value["id"]
    args = value["args"]
    if type(identifier) is not list or len(identifier) != 4:
        raise RequirementSubgraphStateError() from None
    if not all(type(item) is str for item in identifier):
        raise RequirementSubgraphStateError() from None
    if type(args) is not list or len(args) != 1 or type(args[0]) is not str:
        raise RequirementSubgraphStateError() from None
    if tuple(identifier) not in _ALLOWED_JSON_MODULES:
        raise RequirementSubgraphStateError() from None


def _validate_json_tree(value: object) -> None:
    if type(value) is _ParsedObject:
        if value.get("lc") == 2 or value.get("type") == "constructor":
            _validate_json_constructor(value)
            return
        for item in value.values():
            _validate_json_tree(item)
        return
    if type(value) is list:
        for item in value:
            _validate_json_tree(item)


def _json_tree_matches(tree: object, restored: object) -> bool:
    if type(tree) is _ParsedObject:
        if tree.get("lc") == 2 and tree.get("type") == "constructor":
            identifier = tuple(cast(list[str], tree["id"]))
            value = cast(list[str], tree["args"])[0]
            if identifier == _ALLOWED_JSON_MODULES[0]:
                return type(restored) is WorkflowPhase and restored.value == value
            if identifier == _ALLOWED_JSON_MODULES[1]:
                return type(restored) is DeliveryStatus and restored.value == value
            return False
        if type(restored) is not dict or set(tree) != set(restored):
            return False
        return all(_json_tree_matches(item, restored[key]) for key, item in tree.items())
    if type(tree) is list:
        return type(restored) is list and len(tree) == len(restored) and all(
            _json_tree_matches(item, other) for item, other in zip(tree, restored)
        )
    return type(tree) is type(restored) and tree == restored


def _delegate_round_trip(
    delegate: JsonPlusSerializer,
    restored: object,
) -> None:
    try:
        _validate_tree(restored)
        tag, payload = delegate.dumps_typed(restored)
        if tag not in _DELEGATE_TAGS:
            raise RequirementSubgraphStateError() from None
        second = delegate.loads_typed((tag, payload))
        _validate_tree(second)
        if type(second) is not type(restored) or second != restored:
            raise RequirementSubgraphStateError() from None
    except RequirementSubgraphStateError:
        raise RequirementSubgraphStateError() from None
    except Exception:
        raise RequirementSubgraphStateError() from None


def _interrupt_payload(value: tuple[Interrupt, ...]) -> bytes:
    payload: list[dict[str, object]] = []
    for item in value:
        _validate_json_value(item.value)
        if _HEX32.fullmatch(item.id) is None:
            raise RequirementSubgraphStateError() from None
        payload.append({"id": item.id, "value": item.value})
    return _canonical_json(payload)


def _load_interrupt_tuple(data: bytes) -> tuple[Interrupt, ...]:
    tree = _json_tree_from_bytes(data)
    if _invalid_json_tree(tree) or type(tree) is not list or not tree:
        raise RequirementSubgraphStateError() from None
    result: list[Interrupt] = []
    for item in tree:
        if type(item) is not _ParsedObject:
            raise RequirementSubgraphStateError() from None
        if set(item) != {"id", "value"}:
            raise RequirementSubgraphStateError() from None
        identifier = item["id"]
        if type(identifier) is not str or _HEX32.fullmatch(identifier) is None:
            raise RequirementSubgraphStateError() from None
        value = item["value"]
        _validate_json_value(value)
        result.append(Interrupt(value=_plain_json_tree(value), id=identifier))
    restored = tuple(result)
    if _interrupt_payload(restored) != data:
        raise RequirementSubgraphStateError() from None
    return restored


class RequirementCheckpointSerializer:
    """A strict wrapper around LangGraph's safe JSON/msgpack serializer."""

    __slots__ = ("_delegate",)

    def __init__(self) -> None:
        self._delegate = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_json_modules=_ALLOWED_JSON_MODULES,
            allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES,
        )

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        try:
            if type(obj) is TravelSessionState:
                payload = serialize_travel_session_state(obj)
                tag = _STATE_TAG
            elif type(obj) is RequirementDomainState:
                value = _fresh_tagged(RequirementDomainState, obj)
                payload = _model_json(value)
                tag = _REQUIREMENT_STATE_TAG
            elif type(obj) in (
                NeedClarification,
                NeedConfirmation,
                ConfirmedTravelRequirement,
                RequirementExtractionFailed,
                RequirementInvalidated,
            ):
                value = _fresh_tagged(RequirementDomainOutcome, obj)
                payload = _model_json(value)
                tag = _REQUIREMENT_OUTCOME_TAG
            elif type(obj) is TerminalOutcome:
                value = _fresh_tagged(TerminalOutcome, obj)
                payload = _model_json(value)
                tag = _TERMINAL_OUTCOME_TAG
            elif (artifact_tuple := _require_exact_artifact_tuple(obj)) is not None:
                payload = _artifact_tuple_json(artifact_tuple)
                tag = _ARTIFACT_TUPLE_TAG
            elif (interrupt_tuple := _require_exact_interrupt_tuple(obj)) is not None:
                payload = _interrupt_payload(interrupt_tuple)
                tag = _INTERRUPT_TUPLE_TAG
            else:
                if type(obj) is list and obj and all(type(item) is Interrupt for item in obj):
                    raise RequirementSubgraphStateError() from None
                if type(obj) is tuple:
                    raise RequirementSubgraphStateError() from None
                _validate_tree(obj)
                tag, payload = self._delegate.dumps_typed(obj)
                if tag not in _DELEGATE_TAGS:
                    raise RequirementSubgraphStateError() from None
                restored = self._delegate.loads_typed((tag, payload))
                _validate_tree(restored)
                if type(restored) is not type(obj) or restored != obj:
                    raise RequirementSubgraphStateError() from None
                _delegate_round_trip(self._delegate, restored)
                return tag, payload
            restored = self.loads_typed((tag, payload))
            if type(restored) is not type(obj) or restored != obj:
                raise RequirementSubgraphStateError() from None
            return tag, payload
        except RequirementSubgraphStateError:
            raise
        except Exception:
            raise RequirementSubgraphStateError() from None

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        try:
            if type(data) is not tuple or len(data) != 2:
                raise RequirementSubgraphStateError() from None
            tag, payload = data
            if type(tag) is not str:
                raise RequirementSubgraphStateError() from None
            if type(payload) is not bytes and not (
                tag in {"bytes", "bytearray"} and type(payload) is bytearray
            ):
                raise RequirementSubgraphStateError() from None
            if tag.startswith("requirement_subgraph."):
                if type(payload) is not bytes:
                    raise RequirementSubgraphStateError() from None
                if tag not in _OWN_TAGS:
                    raise RequirementSubgraphStateError() from None
                if tag == _STATE_TAG:
                    restored = parse_travel_session_state_json(payload)
                    if serialize_travel_session_state(restored) != payload:
                        raise RequirementSubgraphStateError() from None
                    return restored
                if tag == _REQUIREMENT_STATE_TAG:
                    tree = _json_tree_from_bytes(payload)
                    if _invalid_json_tree(tree):
                        raise RequirementSubgraphStateError() from None
                    restored = TypeAdapter(RequirementDomainState).validate_json(
                        payload,
                        strict=True,
                    )
                    if _model_json(restored) != payload:
                        raise RequirementSubgraphStateError() from None
                    return restored
                if tag == _REQUIREMENT_OUTCOME_TAG:
                    tree = _json_tree_from_bytes(payload)
                    if _invalid_json_tree(tree):
                        raise RequirementSubgraphStateError() from None
                    restored = TypeAdapter(RequirementDomainOutcome).validate_json(
                        payload,
                        strict=True,
                    )
                    if _model_json(restored) != payload:
                        raise RequirementSubgraphStateError() from None
                    return restored
                if tag == _TERMINAL_OUTCOME_TAG:
                    tree = _json_tree_from_bytes(payload)
                    if _invalid_json_tree(tree):
                        raise RequirementSubgraphStateError() from None
                    restored = TypeAdapter(TerminalOutcome).validate_json(
                        payload,
                        strict=True,
                    )
                    if _model_json(restored) != payload:
                        raise RequirementSubgraphStateError() from None
                    return restored
                if tag == _ARTIFACT_TUPLE_TAG:
                    tree = _json_tree_from_bytes(payload)
                    if _invalid_json_tree(tree):
                        raise RequirementSubgraphStateError() from None
                    restored = TypeAdapter(tuple[ArtifactRef, ...]).validate_json(
                        payload,
                        strict=True,
                    )
                    if _artifact_tuple_json(restored) != payload:
                        raise RequirementSubgraphStateError() from None
                    return restored
                return _load_interrupt_tuple(payload)
            if tag not in _DELEGATE_TAGS:
                raise RequirementSubgraphStateError() from None
            if tag == "json":
                tree = _json_tree_from_bytes(payload)
                if _invalid_json_tree(tree):
                    raise RequirementSubgraphStateError() from None
                if _canonical_json(_plain_json_tree(tree)) != payload:
                    raise RequirementSubgraphStateError() from None
                _validate_json_tree(tree)
            restored = self._delegate.loads_typed((tag, payload))
            if tag == "json" and not _json_tree_matches(tree, restored):
                raise RequirementSubgraphStateError() from None
            _delegate_round_trip(self._delegate, restored)
            return restored
        except RequirementSubgraphStateError:
            raise
        except Exception:
            raise RequirementSubgraphStateError() from None


__all__ = ["RequirementCheckpointSerializer"]
