"""官方 LangGraph Saver 的私有 fencing 装饰器。

本模块不拥有 checkpoint 表，也不复制官方 SQL。官方 Saver 负责所有
checkpoint 持久化；本模块只负责配置、namespace、metadata 和 lease fencing。
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from pydantic import BaseModel
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.runtime import ExecutionInfo, Runtime
from langgraph.types import Interrupt, PregelTask, StateSnapshot
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.durable_execution.errors import (
    DurableExecutionConfigurationError,
    DurableExecutionStateError,
    DurableExecutionUsageError,
)
from app.durable_execution.models import DurableExecutionLimits
from app.durable_execution.repository import DurableExecutionRepository
from app.durable_execution.serializer import BoundedCheckpointSerializer
from app.durable_execution.settings import DurableExecutionSettings
from app.workflow_contracts import ArtifactRef, DeliveryStatus, TravelSessionState, WorkflowPhase


_MAX_CHECKPOINT_ID_LENGTH = 200
_ALLOWED_CONFIGURABLE_KEYS = frozenset(
    {
        "thread_id",
        "checkpoint_ns",
        "checkpoint_id",
        "checkpoint_map",
        "__pregel_runtime",
        "__pregel_replay_state",
        "__pregel_task_id",
        "__pregel_send",
        "__pregel_read",
        "__pregel_checkpointer",
        "__pregel_stream",
        "__pregel_cache",
        "__pregel_scratchpad",
        "__pregel_call",
        "__pregel_resuming",
        "__pregel_node_finished",
        "__pregel_timed_attempt_observer",
        "__pregel_runner_submit",
        "__pregel_durability",
        "__pregel_resume_map",
        "__pregel_stream_messages_v2",
        "__pregel_node_error",
    }
)
_ALLOWED_RUNTIME_CONFIG_KEYS = frozenset(
    {
        "configurable",
        "tags",
        "metadata",
        "callbacks",
        "recursion_limit",
        "max_concurrency",
        "run_name",
    }
)
_ALLOWED_METADATA_KEYS = frozenset({"source", "step", "parents"})
_ALLOWED_SOURCES = frozenset({"input", "loop"})
_FRAMEWORK_NODES = frozenset({"__start__", "__end__"})


@dataclass(frozen=True, slots=True)
class _NamespacePart:
    node: str
    task_id: UUID


def _safe_checkpoint_id(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= _MAX_CHECKPOINT_ID_LENGTH:
        raise DurableExecutionStateError() from None
    if value.strip() != value or any(unicodedata.category(char).startswith("C") for char in value):
        raise DurableExecutionStateError() from None
    return value


def _safe_node_segment(value: object) -> str:
    if type(value) is not str or value == "" or value.strip() != value:
        raise DurableExecutionConfigurationError() from None
    if any(char in "/|:" for char in value):
        raise DurableExecutionConfigurationError() from None
    if value in _FRAMEWORK_NODES:
        raise DurableExecutionConfigurationError() from None
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise DurableExecutionConfigurationError() from None
    return value


def _qualified_name(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise DurableExecutionConfigurationError() from None
    parts = value.split("/")
    if any(part == "" for part in parts):
        raise DurableExecutionConfigurationError() from None
    return "/".join(_safe_node_segment(part) for part in parts)


def _parse_namespace(value: object) -> tuple[_NamespacePart, ...]:
    if type(value) is not str or value == "":
        raise DurableExecutionStateError() from None
    parts = value.split("|")
    if any(part == "" for part in parts):
        raise DurableExecutionStateError() from None
    parsed: list[_NamespacePart] = []
    for part in parts:
        if part.count(":") != 1:
            raise DurableExecutionStateError() from None
        node, task_text = part.split(":", 1)
        _safe_node_segment(node)
        try:
            task_id = UUID(task_text)
        except (TypeError, ValueError, AttributeError):
            raise DurableExecutionStateError() from None
        if str(task_id) != task_text:
            raise DurableExecutionStateError() from None
        parsed.append(_NamespacePart(node=node, task_id=task_id))
    return tuple(parsed)


def _namespace_to_qualified(value: object) -> str:
    return _qualified_name("/".join(item.node for item in _parse_namespace(value)))


def _canonical_task_id(value: object) -> UUID:
    if type(value) is not str:
        raise DurableExecutionStateError() from None
    try:
        task_id = UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise DurableExecutionStateError() from None
    if str(task_id) != value:
        raise DurableExecutionStateError() from None
    return task_id


def _child_namespace(parent_namespace: str, node: str, task_id: UUID) -> str:
    suffix = f"{node}:{task_id}"
    return suffix if parent_namespace == "" else f"{parent_namespace}|{suffix}"


def _fresh_checkpoint_map(value: object, registered_names: set[str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise DurableExecutionStateError() from None
    result: dict[str, str] = {}
    for namespace, checkpoint_id in value.items():
        if type(namespace) is not str:
            raise DurableExecutionStateError() from None
        if namespace != "" and _namespace_to_qualified(namespace) not in registered_names:
            raise DurableExecutionStateError() from None
        if namespace in result:
            raise DurableExecutionStateError() from None
        result[namespace] = _safe_checkpoint_id(checkpoint_id)
    return result


def _fresh_metadata(value: CheckpointMetadata, registered_names: set[str]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise DurableExecutionStateError() from None
    keys = set(value)
    if keys != _ALLOWED_METADATA_KEYS:
        raise DurableExecutionStateError() from None
    source = value.get("source")
    step = value.get("step")
    if type(source) is not str or source not in _ALLOWED_SOURCES:
        raise DurableExecutionStateError() from None
    if type(step) is not int or isinstance(step, bool) or not -1 <= step <= (2**53 - 1):
        raise DurableExecutionStateError() from None
    parents = value.get("parents", {})
    if not isinstance(parents, Mapping):
        raise DurableExecutionStateError() from None
    fresh_parents: dict[str, str] = {}
    for namespace, checkpoint_id in parents.items():
        if type(namespace) is not str:
            raise DurableExecutionStateError() from None
        if namespace != "" and _namespace_to_qualified(namespace) not in registered_names:
            raise DurableExecutionStateError() from None
        fresh_parents[namespace] = _safe_checkpoint_id(checkpoint_id)
    return {"source": source, "step": step, "parents": fresh_parents}


def _canonical_safe_value(value: object) -> object:
    """递归复制 checkpoint 内允许进入 digest 的 JSON 安全结构。"""

    if value is None or type(value) is bool or type(value) is int or type(value) is str:
        return value
    if type(value) is UUID:
        return str(value)
    if isinstance(value, Enum):
        return _canonical_safe_value(value.value)
    if isinstance(value, BaseModel):
        return _canonical_safe_value(
            value.model_dump(
                mode="json",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
        )
    if type(value) is float:
        if not math.isfinite(value):
            raise DurableExecutionStateError() from None
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or key in result:
                raise DurableExecutionStateError() from None
            result[key] = _canonical_safe_value(item)
        return result
    raise DurableExecutionStateError() from None


def _strict_artifact_refs(value: object) -> tuple[ArtifactRef, ...]:
    """按 ArtifactRef 的 JSON 合同严格复制 checkpoint 中的引用。"""

    if not isinstance(value, (list, tuple)):
        raise DurableExecutionStateError() from None
    refs = []
    try:
        for item in value:
            if type(item) is ArtifactRef:
                encoded = json.dumps(
                    item.model_dump(
                        mode="json",
                        exclude_none=False,
                        by_alias=False,
                        exclude_computed_fields=True,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            elif isinstance(item, Mapping):
                encoded = json.dumps(
                    _canonical_safe_value(item),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            else:
                raise ValueError
            refs.append(ArtifactRef.model_validate_json(encoded, strict=True))
        return tuple(refs)
    except Exception:
        raise DurableExecutionStateError() from None


def _strict_state_object(value: object) -> TravelSessionState:
    """从 LangGraph channel values 提取唯一的 strict TravelSessionState。"""

    try:
        if type(value) is TravelSessionState:
            state = value
        elif type(value) is dict:
            if "__start__" in value:
                start_value = value["__start__"]
                if type(start_value) is not TravelSessionState:
                    raise ValueError
                state = start_value
                remainder = set(value) - {"__start__"}
                state_fields = set(TravelSessionState.model_fields)
                if any(
                    key != "__start__"
                    and (
                        not isinstance(key, str)
                        or (
                            not key.startswith("branch:to:")
                            and key not in state_fields
                        )
                    )
                    for key in remainder
                ):
                    raise ValueError
            else:
                channel_state = {
                    key: item
                    for key, item in value.items()
                    if not (isinstance(key, str) and key.startswith("branch:to:"))
                }
                try:
                    state = TravelSessionState.model_validate(channel_state, strict=True)
                except Exception:
                    state_json = json.dumps(
                        _canonical_safe_value(channel_state),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    try:
                        state = TravelSessionState.model_validate_json(
                            state_json,
                            strict=True,
                        )
                    except Exception:
                        raw_refs = channel_state.get("artifact_refs", ())
                        if not isinstance(raw_refs, (list, tuple)):
                            raise
                        artifact_refs = _strict_artifact_refs(raw_refs)
                        payload_without_refs = dict(channel_state)
                        payload_without_refs["artifact_refs"] = []
                        base_json = json.dumps(
                            _canonical_safe_value(payload_without_refs),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                        base_state = TravelSessionState.model_validate_json(
                            base_json,
                            strict=True,
                        )
                        payload = base_state.model_dump(
                            mode="python",
                            exclude_none=False,
                            by_alias=False,
                            exclude_computed_fields=True,
                        )
                        payload["artifact_refs"] = artifact_refs
                        state = TravelSessionState.model_validate(payload, strict=True)
        else:
            raise ValueError
        return state
    except DurableExecutionStateError:
        raise
    except Exception:
        raise DurableExecutionStateError() from None


def _strict_state_tree(value: object) -> dict[str, object]:
    return _strict_state_object(value).model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )


def _safe_checkpoint_digest(
    checkpoint: Checkpoint,
    *,
    checkpoint_ns: str = "",
    parents: Mapping[str, str] | None = None,
    checkpoint_map: Mapping[str, str] | None = None,
) -> str:
    """只摘要设计闭集：state、pending interrupt、pointer 和 parent。"""

    try:
        if not isinstance(checkpoint, Mapping):
            raise ValueError
        required_keys = {"v", "id", "channel_values"}
        if not required_keys.issubset(checkpoint):
            raise ValueError
        if type(checkpoint["v"]) is not int:
            raise ValueError
        channel_values = checkpoint.get("channel_values")
        if not isinstance(channel_values, Mapping):
            raise ValueError
        if "state" in channel_values:
            state_value = channel_values["state"]
        else:
            state_value = channel_values
        if type(state_value) is TravelSessionState:
            state_tree = state_value.model_dump(
                mode="json",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
        elif type(state_value) is dict:
            state_tree = _strict_state_tree(state_value)
        else:
            raise ValueError
        if type(checkpoint_ns) is not str:
            raise ValueError
        root_parent = {} if parents is None else _canonical_safe_value(parents)
        root_map = {} if checkpoint_map is None else _canonical_safe_value(checkpoint_map)
        if not isinstance(root_parent, dict) or not isinstance(root_map, dict):
            raise ValueError
        projection = {
            "state": state_tree,
            "pending": _canonical_safe_value(checkpoint.get("interrupt")),
            "pointer": {
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": _safe_checkpoint_id(checkpoint.get("id")),
                "checkpoint_map": root_map,
            },
            "parents": root_parent,
        }
        encoded = json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except DurableExecutionStateError:
        raise
    except Exception:
        raise DurableExecutionStateError() from None
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _snapshot_config_projection(
    config: Mapping[str, Any],
    *,
    registered_names: set[str],
    run_id: UUID,
    expected_qualified: str | None = None,
) -> dict[str, object]:
    if not isinstance(config, Mapping):
        raise DurableExecutionStateError() from None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        raise DurableExecutionStateError() from None
    if type(configurable.get("thread_id")) is not str or configurable["thread_id"] != str(run_id):
        raise DurableExecutionStateError() from None
    namespace = configurable.get("checkpoint_ns")
    if type(namespace) is not str:
        raise DurableExecutionStateError() from None
    qualified = "" if namespace == "" else _namespace_to_qualified(namespace)
    if qualified != "" and qualified not in registered_names:
        raise DurableExecutionStateError() from None
    if expected_qualified is not None and qualified != expected_qualified:
        raise DurableExecutionStateError() from None
    checkpoint_id = _safe_checkpoint_id(configurable.get("checkpoint_id"))
    checkpoint_map = configurable.get("checkpoint_map", {})
    if checkpoint_map is None:
        checkpoint_map = {}
    return {
        "thread_id": str(run_id),
        "checkpoint_ns": namespace,
        "checkpoint_id": checkpoint_id,
        "checkpoint_map": _fresh_checkpoint_map(checkpoint_map, registered_names),
    }


def _safe_interrupt_projection(value: object) -> dict[str, object]:
    if type(value) is not Interrupt:
        raise DurableExecutionStateError() from None
    interrupt_id = _safe_checkpoint_id(getattr(value, "id", None))
    return {
        "id": interrupt_id,
        "value": _canonical_safe_value(getattr(value, "value", None)),
    }


def _safe_snapshot_projection(
    snapshot: StateSnapshot,
    *,
    registered_names: set[str],
    run_id: UUID,
    expected_qualified: str | None = None,
    expected_namespace: str | None = None,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """生成严格的 StateSnapshot projection，并返回其实际 descendant config 闭包。"""

    if type(snapshot) is not StateSnapshot:
        raise DurableExecutionStateError() from None
    config_projection = _snapshot_config_projection(
        snapshot.config,
        registered_names=registered_names,
        run_id=run_id,
        expected_qualified=expected_qualified,
    )
    if expected_qualified is None and config_projection["checkpoint_ns"] != "":
        raise DurableExecutionStateError() from None
    if expected_namespace is not None and config_projection["checkpoint_ns"] != expected_namespace:
        raise DurableExecutionStateError() from None
    if type(snapshot.next) is not tuple:
        raise DurableExecutionStateError() from None
    next_nodes: list[str] = []
    for node in snapshot.next:
        if type(node) is not str:
            raise DurableExecutionStateError() from None
        if node in _FRAMEWORK_NODES:
            next_nodes.append(node)
            continue
        qualified = _qualified_name(node)
        if expected_qualified:
            qualified = f"{expected_qualified}/{qualified}"
        if qualified not in registered_names:
            raise DurableExecutionStateError() from None
        next_nodes.append(node)
    if type(snapshot.tasks) is not tuple or type(snapshot.interrupts) is not tuple:
        raise DurableExecutionStateError() from None
    task_projection: list[dict[str, object]] = []
    descendant_configs: list[dict[str, object]] = []
    for task in snapshot.tasks:
        if type(task) is not PregelTask:
            raise DurableExecutionStateError() from None
        task_name = _safe_node_segment(getattr(task, "name", None))
        task_qualified = task_name if not expected_qualified else f"{expected_qualified}/{task_name}"
        if task_qualified not in registered_names:
            raise DurableExecutionStateError() from None
        task_uuid = _canonical_task_id(getattr(task, "id", None))
        task_id = str(task_uuid)
        raw_path = getattr(task, "path", None)
        if not isinstance(raw_path, tuple) or not raw_path or task_name not in raw_path:
            raise DurableExecutionStateError() from None
        task_path = _canonical_safe_value(raw_path)
        if getattr(task, "error", None) is not None:
            raise DurableExecutionStateError() from None
        task_interrupts = getattr(task, "interrupts", ())
        if type(task_interrupts) is not tuple:
            raise DurableExecutionStateError() from None
        child_projection: dict[str, object] | None = None
        child_configs: tuple[dict[str, object], ...] = ()
        task_state = getattr(task, "state", None)
        if task_state is not None:
            if type(task_state) is not StateSnapshot:
                raise DurableExecutionStateError() from None
            child_projection, child_configs = _safe_snapshot_projection(
                task_state,
                registered_names=registered_names,
                run_id=run_id,
                expected_qualified=task_qualified,
                expected_namespace=_child_namespace(
                    str(config_projection["checkpoint_ns"]),
                    task_name,
                    task_uuid,
                ),
            )
            descendant_configs.extend(child_configs)
            child_config = child_projection["config"]
            if not isinstance(child_config, dict):
                raise DurableExecutionStateError() from None
            descendant_configs.append(child_config)
        task_projection.append(
            {
                "id": task_id,
                "name": task_name,
                "path": task_path,
                "interrupts": [_safe_interrupt_projection(item) for item in task_interrupts],
                "state": child_projection,
            }
        )
    snapshot_parent = getattr(snapshot, "parent_config", None)
    parent_projection = (
        None
        if snapshot_parent is None
        else _snapshot_config_projection(
            snapshot_parent,
            registered_names=registered_names,
            run_id=run_id,
        )
    )
    projection = {
        "values": _strict_state_tree(snapshot.values),
        "next": next_nodes,
        "config": config_projection,
        "parent_config": parent_projection,
        "tasks": task_projection,
        "interrupts": [_safe_interrupt_projection(item) for item in snapshot.interrupts],
    }
    # Metadata is part of strict validation, never of the safe digest projection.
    _fresh_metadata(snapshot.metadata, registered_names)
    return projection, tuple(descendant_configs)


def _safe_snapshot_digest(
    snapshot: StateSnapshot,
    *,
    root_tuple: CheckpointTuple,
    descendant_tuples: Sequence[CheckpointTuple],
    registered_names: set[str],
    run_id: UUID,
) -> str:
    """摘要 StateSnapshot、实际 task closure 和每个官方 checkpoint 的 parent 证据。"""

    projection, descendant_configs = _safe_snapshot_projection(
        snapshot,
        registered_names=registered_names,
        run_id=run_id,
        expected_qualified=None,
    )
    if projection["config"] != _snapshot_config_projection(
        root_tuple.config,
        registered_names=registered_names,
        run_id=run_id,
    ):
        raise DurableExecutionStateError() from None
    expected_keys = {
        (
            item["checkpoint_ns"],
            item["checkpoint_id"],
        )
        for item in descendant_configs
        if isinstance(item, dict)
    }
    actual_keys: set[tuple[object, object]] = set()
    checkpoint_projection: list[dict[str, object]] = []
    for item in (root_tuple, *tuple(descendant_tuples)):
        config_projection = _snapshot_config_projection(
            item.config,
            registered_names=registered_names,
            run_id=run_id,
        )
        key = (config_projection["checkpoint_ns"], config_projection["checkpoint_id"])
        if key in actual_keys:
            raise DurableExecutionStateError() from None
        actual_keys.add(key)
        metadata = _fresh_metadata(item.metadata, registered_names)
        parent_config = getattr(item, "parent_config", None)
        parent_projection = (
            None
            if parent_config is None
            else _snapshot_config_projection(
                parent_config,
                registered_names=registered_names,
                run_id=run_id,
            )
        )
        checkpoint_projection.append(
            {
                "config": config_projection,
                "parent_config": parent_projection,
                "checkpoint": _safe_checkpoint_digest(
                    item.checkpoint,
                    checkpoint_ns=str(config_projection["checkpoint_ns"]),
                    parents=metadata["parents"],
                    checkpoint_map=config_projection["checkpoint_map"],
                ),
            }
        )
    if expected_keys != actual_keys - {
        ("", projection["config"]["checkpoint_id"]),
    }:
        raise DurableExecutionStateError() from None
    encoded = json.dumps(
        {"snapshot": projection, "checkpoints": checkpoint_projection},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _restore_state_channels(checkpoint: Checkpoint) -> Checkpoint:
    """恢复官方逐 channel JSON 读回的两个严格枚举字段。"""

    if not isinstance(checkpoint, Mapping):
        raise DurableExecutionStateError() from None
    channel_values = checkpoint.get("channel_values")
    if not isinstance(channel_values, Mapping):
        raise DurableExecutionStateError() from None
    restored = dict(channel_values)
    for key, enum_type in (
        ("phase", WorkflowPhase),
        ("delivery_status", DeliveryStatus),
    ):
        value = restored.get(key)
        if type(value) is str:
            try:
                restored[key] = enum_type(value)
            except ValueError:
                raise DurableExecutionStateError() from None
        elif value is not None and type(value) is not enum_type:
            raise DurableExecutionStateError() from None
    result = dict(checkpoint)
    result["channel_values"] = restored
    return result


class FencedCheckpointSaver(BaseCheckpointSaver):
    """只在当前 run 的 advisory fencing transaction 内委托官方 Saver。"""

    def __init__(
        self,
        *,
        saver: AsyncPostgresSaver,
        repository: DurableExecutionRepository,
        run_id: UUID,
        owner: UUID,
        fencing_token: int,
        limits: DurableExecutionLimits,
        registered_node_names: Sequence[str] = (),
        checkpoint_pool: AsyncConnectionPool | None = None,
    ) -> None:
        if type(saver) is not AsyncPostgresSaver:
            raise DurableExecutionConfigurationError() from None
        if type(repository) is not DurableExecutionRepository:
            raise DurableExecutionConfigurationError() from None
        if type(run_id) is not UUID or type(owner) is not UUID:
            raise DurableExecutionConfigurationError() from None
        if type(fencing_token) is not int or isinstance(fencing_token, bool) or fencing_token < 1:
            raise DurableExecutionConfigurationError() from None
        if type(limits) is not DurableExecutionLimits:
            raise DurableExecutionConfigurationError() from None
        try:
            names = {_qualified_name(name) for name in registered_node_names}
        except DurableExecutionConfigurationError:
            raise
        except Exception:
            raise DurableExecutionConfigurationError() from None
        serializer = BoundedCheckpointSerializer(limits=limits)
        super().__init__(serde=serializer)
        self._saver = saver
        self._repository = repository
        self._run_id = run_id
        self._owner = owner
        self._fencing_token = fencing_token
        self._limits = limits
        self._registered_node_names = names
        self._checkpoint_pool = checkpoint_pool

    def _checked_config(
        self,
        config: Mapping[str, Any],
        *,
        allow_actual_namespace: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(config, Mapping):
            raise DurableExecutionUsageError() from None
        if not set(config).issubset(_ALLOWED_RUNTIME_CONFIG_KEYS) or "configurable" not in config:
            raise DurableExecutionUsageError() from None
        configurable = config.get("configurable")
        if not isinstance(configurable, Mapping):
            raise DurableExecutionUsageError() from None
        if not set(configurable).issubset(_ALLOWED_CONFIGURABLE_KEYS):
            raise DurableExecutionUsageError() from None
        thread_id = configurable.get("thread_id")
        if type(thread_id) is not str or thread_id != str(self._run_id):
            raise DurableExecutionUsageError() from None
        namespace = configurable.get("checkpoint_ns", "")
        if type(namespace) is not str:
            raise DurableExecutionUsageError() from None
        if namespace != "":
            if _namespace_to_qualified(namespace) not in self._registered_node_names:
                raise DurableExecutionUsageError() from None
            if not allow_actual_namespace:
                runtime = configurable.get("__pregel_runtime")
                if type(runtime) is not Runtime or type(runtime.execution_info) is not ExecutionInfo:
                    raise DurableExecutionUsageError() from None
                try:
                    namespace_parts = _parse_namespace(namespace)
                    execution_info = runtime.execution_info
                    task_id = _canonical_task_id(execution_info.task_id)
                except (AttributeError, DurableExecutionStateError):
                    raise DurableExecutionUsageError() from None
                if (
                    execution_info.checkpoint_ns != namespace
                    or task_id != namespace_parts[-1].task_id
                    or type(execution_info.thread_id) is not str
                    or execution_info.thread_id != str(self._run_id)
                ):
                    raise DurableExecutionUsageError() from None
                if "__pregel_task_id" in configurable:
                    try:
                        configured_task_id = _canonical_task_id(configurable["__pregel_task_id"])
                    except DurableExecutionStateError:
                        raise DurableExecutionUsageError() from None
                    if configured_task_id != task_id:
                        raise DurableExecutionUsageError() from None
        fresh_configurable: dict[str, Any] = {
            "thread_id": thread_id,
            "checkpoint_ns": namespace,
        }
        if "checkpoint_id" in configurable and configurable["checkpoint_id"] is not None:
            fresh_configurable["checkpoint_id"] = _safe_checkpoint_id(configurable["checkpoint_id"])
        if "checkpoint_map" in configurable and configurable["checkpoint_map"] is not None:
            fresh_configurable["checkpoint_map"] = _fresh_checkpoint_map(
                configurable["checkpoint_map"],
                self._registered_node_names,
            )
        # 官方 Saver 会把 config.metadata/configurable 的额外值合并进 JSONB；
        # 这里只传递已闭合的 configurable，避免 caller metadata 旁路泄漏。
        return {"configurable": fresh_configurable}

    def _checked_result_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        return self._checked_config(config, allow_actual_namespace=True)

    async def aget_tuple(self, config: Mapping[str, Any]) -> CheckpointTuple | None:
        # LangGraph 的子图恢复会把官方 Saver 返回的实际 namespace 原样回传；
        # 这类任务 UUID 可能不是 RFC 版本 4/7，但仍必须满足 canonical UUID
        # 和已登记 namespace 路径约束。
        checked = self._checked_result_config(config)
        try:
            result = await self._saver.aget_tuple(checked)
            if result is None:
                return None
            return result._replace(checkpoint=_restore_state_channels(result.checkpoint))
        except DurableExecutionStateError:
            raise
        except Exception:
            raise DurableExecutionStateError() from None

    def _root_pointer_from_tuple(self, result: CheckpointTuple) -> tuple[str, str, str]:
        config = result.config
        if not isinstance(config, Mapping):
            raise DurableExecutionStateError() from None
        configurable = config.get("configurable")
        if not isinstance(configurable, Mapping):
            raise DurableExecutionStateError() from None
        if (
            configurable.get("thread_id") != str(self._run_id)
            or configurable.get("checkpoint_ns") != ""
        ):
            raise DurableExecutionStateError() from None
        checkpoint_id = _safe_checkpoint_id(configurable.get("checkpoint_id"))
        metadata = result.metadata
        parents = metadata.get("parents") if isinstance(metadata, Mapping) else None
        checkpoint_map = configurable.get("checkpoint_map")
        digest = _safe_checkpoint_digest(
            _restore_state_channels(result.checkpoint),
            checkpoint_ns="",
            parents=parents,
            checkpoint_map=checkpoint_map,
        )
        return "", checkpoint_id, digest

    async def activation_pointer(self) -> tuple[str, str, str]:
        """读取进入 budgeted phase 首 node 前的 root checkpoint 证据。"""

        config = {"configurable": {"thread_id": str(self._run_id), "checkpoint_ns": ""}}
        checked = self._checked_result_config(config)
        try:
            result = await self._saver.aget_tuple(checked)
        except DurableExecutionStateError:
            raise
        except Exception:
            raise DurableExecutionStateError() from None
        if result is None:
            raise DurableExecutionStateError() from None
        return self._root_pointer_from_tuple(result)

    async def activation_state(self, pointer: tuple[str, str, str]) -> TravelSessionState:
        """按 activation 绑定的 root pointer 读取当时的安全 state。"""

        if type(pointer) is not tuple or len(pointer) != 3 or pointer[0] != "":
            raise DurableExecutionStateError() from None
        namespace, checkpoint_id, digest = pointer
        config = {
            "configurable": {
                "thread_id": str(self._run_id),
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }
        checked = self._checked_result_config(config)
        try:
            result = await self._saver.aget_tuple(checked)
        except DurableExecutionStateError:
            raise
        except Exception:
            raise DurableExecutionStateError() from None
        if result is None:
            raise DurableExecutionStateError() from None
        if self._root_pointer_from_tuple(result) != (namespace, checkpoint_id, digest):
            raise DurableExecutionStateError() from None
        checkpoint = _restore_state_channels(result.checkpoint)
        channel_values = checkpoint.get("channel_values")
        if isinstance(channel_values, Mapping) and "state" in channel_values:
            value: object = channel_values["state"]
        else:
            value = channel_values
        return _strict_state_object(value)

    async def alist(
        self,
        config: Mapping[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        raise DurableExecutionUsageError() from None
        yield  # pragma: no cover

    async def aget(self, config: Mapping[str, Any]) -> Any:
        raise DurableExecutionUsageError() from None

    async def aget_delta_channel_history(self, config: Mapping[str, Any]) -> Any:
        raise DurableExecutionUsageError() from None

    async def adelete_for_runs(self, *args: Any, **kwargs: Any) -> None:
        raise DurableExecutionUsageError() from None

    async def adelete_thread(self, thread_id: str) -> None:
        raise DurableExecutionUsageError() from None

    async def aprune(self, *args: Any, **kwargs: Any) -> None:
        raise DurableExecutionUsageError() from None

    async def acopy_thread(self, *args: Any, **kwargs: Any) -> None:
        raise DurableExecutionUsageError() from None

    async def aput(
        self,
        config: Mapping[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> dict[str, Any]:
        checked = self._checked_config(config)
        fresh_metadata = _fresh_metadata(metadata, self._registered_node_names)
        namespace = checked["configurable"]["checkpoint_ns"]
        checkpoint_map = checked["configurable"].get("checkpoint_map")
        expected_checkpoint_id = _safe_checkpoint_id(checkpoint.get("id"))
        expected_digest = _safe_checkpoint_digest(
            checkpoint,
            checkpoint_ns=namespace,
            parents=fresh_metadata["parents"],
            checkpoint_map=checkpoint_map,
        )
        result: dict[str, Any]
        async with self._repository.fencing_transaction(
            run_id=self._run_id,
            owner=self._owner,
            fencing_token=self._fencing_token,
        ) as connection:
            try:
                if namespace != "":
                    parent_id = fresh_metadata["parents"].get("")
                    if (
                        not isinstance(checkpoint_map, Mapping)
                        or checkpoint_map.get("") != parent_id
                        or parent_id is None
                        or dict(checkpoint_map) != dict(fresh_metadata["parents"])
                    ):
                        raise DurableExecutionStateError() from None
                    parent_config = {
                        "configurable": {
                            "thread_id": str(self._run_id),
                            "checkpoint_ns": "",
                            "checkpoint_id": parent_id,
                        }
                    }
                    parent_tuple = await self._saver.aget_tuple(parent_config)
                    if parent_tuple is None:
                        raise DurableExecutionStateError() from None
                    parent_actual = self._checked_result_config(parent_tuple.config)
                    if (
                        parent_actual["configurable"].get("checkpoint_id") != parent_id
                        or parent_actual["configurable"].get("checkpoint_ns") != ""
                    ):
                        raise DurableExecutionStateError() from None
                result = await self._saver.aput(
                    checked,
                    checkpoint,
                    fresh_metadata,
                    new_versions,
                )
                checked_result = self._checked_result_config(result)
                result_configurable = checked_result["configurable"]
                if (
                    result_configurable["checkpoint_ns"] != namespace
                    or result_configurable.get("checkpoint_id") is None
                ):
                    raise DurableExecutionStateError() from None
                checkpoint_id = _safe_checkpoint_id(result_configurable["checkpoint_id"])
                if checkpoint_id != expected_checkpoint_id:
                    raise DurableExecutionStateError() from None
                result = checked_result
            except DurableExecutionStateError:
                raise
            except Exception:
                raise DurableExecutionStateError() from None

        return result

    async def _verified_snapshot_pointer(
        self,
        snapshot: StateSnapshot,
    ) -> tuple[str, str, str]:
        """只读验证 StateSnapshot 的完整 bounded lineage 并返回其真实 root pointer。"""

        if type(snapshot) is not StateSnapshot:
            raise DurableExecutionUsageError() from None
        projection, descendant_projections = _safe_snapshot_projection(
            snapshot,
            registered_names=self._registered_node_names,
            run_id=self._run_id,
        )
        root_projection = projection["config"]
        if not isinstance(root_projection, dict) or root_projection["checkpoint_ns"] != "":
            raise DurableExecutionStateError() from None
        latest_config = {
            "configurable": {
                "thread_id": str(self._run_id),
                "checkpoint_ns": "",
            }
        }
        lineage = await self._read_bounded_lineage(
            latest_config,
            (),
            snapshot=snapshot,
        )
        if not lineage:
            raise DurableExecutionStateError() from None
        latest = lineage[0]
        actual_root_projection = _snapshot_config_projection(
            latest.config,
            registered_names=self._registered_node_names,
            run_id=self._run_id,
        )
        if actual_root_projection != root_projection:
            raise DurableExecutionStateError() from None
        expected_descendant_keys = {
            (str(item["checkpoint_ns"]), str(item["checkpoint_id"]))
            for item in descendant_projections
        }
        descendant_tuples = tuple(
            item
            for item in lineage[1:]
            if (
                str(_snapshot_config_projection(
                    item.config,
                    registered_names=self._registered_node_names,
                    run_id=self._run_id,
                )["checkpoint_ns"]),
                str(_snapshot_config_projection(
                    item.config,
                    registered_names=self._registered_node_names,
                    run_id=self._run_id,
                )["checkpoint_id"]),
            ) in expected_descendant_keys
        )
        if len(descendant_tuples) != len(expected_descendant_keys):
            raise DurableExecutionStateError() from None
        digest = _safe_snapshot_digest(
            snapshot,
            root_tuple=latest,
            descendant_tuples=tuple(descendant_tuples),
            registered_names=self._registered_node_names,
            run_id=self._run_id,
        )
        return "", str(root_projection["checkpoint_id"]), digest

    async def _confirm_graph_state(self, snapshot: StateSnapshot) -> None:
        """在脱离 aput 的 crash gap 后，以完整 StateSnapshot 证据确认 root pointer。"""

        pointer = await self._verified_snapshot_pointer(snapshot)
        await self._repository.set_root_checkpoint_pointer(
            run_id=self._run_id,
            owner=self._owner,
            fencing_token=self._fencing_token,
            checkpoint_id=pointer[1],
            checkpoint_digest=pointer[2],
        )

    async def _confirm_root_pointer(self, snapshot: StateSnapshot) -> None:
        """旧私有名称只保留为 StateSnapshot 入口，禁止裸 root tuple 提升。"""

        await self._confirm_graph_state(snapshot)

    async def aput_writes(
        self,
        config: Mapping[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        checked = self._checked_config(config)
        if type(task_id) is not str or type(task_path) is not str:
            raise DurableExecutionUsageError() from None
        async with self._repository.fencing_transaction(
            run_id=self._run_id,
            owner=self._owner,
            fencing_token=self._fencing_token,
        ):
            try:
                await self._saver.aput_writes(
                    checked,
                    writes,
                    task_id,
                    task_path,
                )
            except Exception:
                raise DurableExecutionStateError() from None

    async def _read_bounded_lineage(
        self,
        root_config: Mapping[str, Any],
        descendant_configs: Sequence[Mapping[str, Any]],
        *,
        snapshot: StateSnapshot | None = None,
        _known_root: CheckpointTuple | None = None,
        _known_descendants: Sequence[CheckpointTuple] | None = None,
    ) -> tuple[CheckpointTuple, ...]:
        """以官方 ``alist`` 读取完整的 bounded tuple 集合并验证闭包。"""

        max_count = min(self._limits.max_lineage_checkpoints, 256)
        if max_count < 1 or type(descendant_configs) is not tuple:
            raise DurableExecutionUsageError() from None
        if type(snapshot) is not StateSnapshot:
            raise DurableExecutionUsageError() from None
        projection, descendant_projections = _safe_snapshot_projection(
            snapshot,
            registered_names=self._registered_node_names,
            run_id=self._run_id,
        )
        checked_root = self._checked_config(root_config)
        if checked_root["configurable"]["checkpoint_ns"] != "":
            raise DurableExecutionUsageError() from None
        root_projection = projection["config"]
        if not isinstance(root_projection, dict):
            raise DurableExecutionStateError() from None
        expected_descendant_keys = tuple(
            (str(item["checkpoint_ns"]), str(item["checkpoint_id"]))
            for item in descendant_projections
        )
        if len(set(expected_descendant_keys)) != len(expected_descendant_keys):
            raise DurableExecutionStateError() from None
        expected_keys = {("", str(root_projection["checkpoint_id"])), *expected_descendant_keys}
        expected_descendant_projections = {
            (str(item["checkpoint_ns"]), str(item["checkpoint_id"])): item
            for item in descendant_projections
        }
        if len(expected_keys) > max_count:
            raise DurableExecutionStateError() from None

        # ``_known_*`` 是旧的 aget_tuple 入口参数，不能绕过本次完整读取；
        # R04 要求每一个 tuple 都由同一 bounded read 计数并验证。
        del _known_root, _known_descendants
        try:
            # 多取一个 sentinel 仅用于区分“恰好达到上限”和“超过上限”；
            # 一旦命中 sentinel 立即 fail closed，绝不会把超预算集合继续处理。
            records: list[CheckpointTuple] = []
            async for item in self._saver.alist(
                {"configurable": {"thread_id": str(self._run_id)}},
                limit=max_count + 1,
            ):
                records.append(item)
                if len(records) > max_count:
                    raise DurableExecutionStateError() from None
            if not records:
                raise DurableExecutionStateError() from None

            by_key: dict[tuple[str, str], CheckpointTuple] = {}
            by_namespace: dict[str, list[tuple[str, str]]] = {}
            projections: dict[tuple[str, str], dict[str, object]] = {}

            for item in records:
                item_projection = _snapshot_config_projection(
                    item.config,
                    registered_names=self._registered_node_names,
                    run_id=self._run_id,
                )
                key = (
                    str(item_projection["checkpoint_ns"]),
                    str(item_projection["checkpoint_id"]),
                )
                if key in by_key:
                    raise DurableExecutionStateError() from None
                by_key[key] = item
                projections[key] = item_projection
                by_namespace.setdefault(key[0], []).append(key)

            # 官方 alist 按 checkpoint_id 降序返回；第一个 tuple 才是该
            # namespace 的 latest。其余 tuple 只有在 parent closure 中才合法。
            latest_by_namespace = {
                namespace: keys[0]
                for namespace, keys in by_namespace.items()
            }
            root_key = latest_by_namespace.get("")
            if root_key is None or root_key != ("", str(root_projection["checkpoint_id"])):
                raise DurableExecutionStateError() from None
            expected_namespaces = {namespace for namespace, _ in expected_descendant_keys}
            for namespace, checkpoint_id in expected_descendant_keys:
                if latest_by_namespace.get(namespace) != (namespace, checkpoint_id):
                    raise DurableExecutionStateError() from None
            historical_latest: list[tuple[str, str]] = []
            for namespace, key in latest_by_namespace.items():
                if namespace == "" or namespace in expected_namespaces:
                    continue
                historical_item = by_key[key]
                historical_metadata = _fresh_metadata(
                    historical_item.metadata,
                    self._registered_node_names,
                )
                root_parent_id = historical_metadata["parents"].get("")
                if (
                    root_parent_id is None
                    or ("", root_parent_id) not in by_key
                    or root_parent_id == root_key[1]
                ):
                    # 当前 snapshot 没有该 task 时，只有能锚定到旧 root
                    # 的历史 namespace 才可存在；指向当前 root 的额外
                    # namespace 是 fork，孤立 namespace 则是 orphan。
                    raise DurableExecutionStateError() from None
                historical_latest.append(key)

            def tuple_key_from_config(config: Mapping[str, Any]) -> tuple[str, str]:
                config_projection = _snapshot_config_projection(
                    config,
                    registered_names=self._registered_node_names,
                    run_id=self._run_id,
                )
                return (
                    str(config_projection["checkpoint_ns"]),
                    str(config_projection["checkpoint_id"]),
                )

            # 从 root/desc latest 沿 parent_config 建立精确闭包。所有父引用
            # 必须已经在 alist 返回集合中，避免用 aget_tuple 绕过读预算。
            closure: set[tuple[str, str]] = set()
            pending = list(expected_keys) + historical_latest
            while pending:
                key = pending.pop()
                if key in closure:
                    continue
                item = by_key.get(key)
                if item is None:
                    raise DurableExecutionStateError() from None
                closure.add(key)
                parent_config = getattr(item, "parent_config", None)
                if parent_config is not None:
                    parent_key = tuple_key_from_config(parent_config)
                    if parent_key not in by_key:
                        # 官方 Postgres Saver 可能保留一条 child 的历史
                        # tuple，而其 parent 只存在于 pending writes 中；
                        # 当前 task 的 latest 若缺 parent 必须失败，但历史
                        # 边界不应被误报成当前 graph 的 missing latest。
                        if key[0] == "" or latest_by_namespace.get(key[0]) == key:
                            raise DurableExecutionStateError() from None
                        parent_key = None
                    if parent_key is not None:
                        namespace = key[0]
                        parent_namespace = parent_key[0]
                        if namespace != "" and parent_namespace not in {"", namespace}:
                            if not _namespace_to_qualified(namespace).startswith(
                                _namespace_to_qualified(parent_namespace) + "/"
                            ):
                                raise DurableExecutionStateError() from None
                        if namespace == "" and parent_namespace != "":
                            raise DurableExecutionStateError() from None
                        pending.append(parent_key)

                item_projection = projections[key]
                metadata = _fresh_metadata(item.metadata, self._registered_node_names)
                expected_descendant = expected_descendant_projections.get(key)
                if expected_descendant is not None:
                    if any(
                        item_projection[field] != expected_descendant[field]
                        for field in ("thread_id", "checkpoint_ns", "checkpoint_id")
                    ):
                        raise DurableExecutionStateError() from None
                    expected_map = expected_descendant["checkpoint_map"]
                    if metadata["parents"].get("") != root_key[1]:
                        raise DurableExecutionStateError() from None
                    if expected_map:
                        if any(
                            expected_map.get(namespace) != checkpoint_id
                            for namespace, checkpoint_id in metadata["parents"].items()
                        ):
                            raise DurableExecutionStateError() from None
                        extra_namespaces = set(expected_map) - set(metadata["parents"])
                        if extra_namespaces - {key[0]}:
                            raise DurableExecutionStateError() from None
                        if key[0] in expected_map and expected_map[key[0]] != key[1]:
                            raise DurableExecutionStateError() from None
                for parent_namespace, parent_id in metadata["parents"].items():
                    parent_key = (parent_namespace, parent_id)
                    if parent_key not in by_key:
                        raise DurableExecutionStateError() from None
                    if parent_namespace != "":
                        namespace = key[0]
                        if not (
                            _namespace_to_qualified(namespace) == _namespace_to_qualified(parent_namespace)
                            or _namespace_to_qualified(namespace).startswith(
                                _namespace_to_qualified(parent_namespace) + "/"
                            )
                        ):
                            raise DurableExecutionStateError() from None
                    pending.append(parent_key)

                if item_projection["checkpoint_map"] not in ({}, metadata["parents"]):
                    raise DurableExecutionStateError() from None
                _safe_checkpoint_digest(
                    item.checkpoint,
                    checkpoint_ns=key[0],
                    parents=metadata["parents"],
                    checkpoint_map=item_projection["checkpoint_map"],
                )

            if closure != set(by_key):
                raise DurableExecutionStateError() from None

            # 由于 alist 是全线程读取，所有 tuple 都已计数；返回顺序固定，
            # 不依赖 set 的迭代顺序，供 digest 生成稳定输入。
            ordered_keys = [root_key]
            ordered_keys.extend(
                (namespace, checkpoint_id)
                for namespace, checkpoint_id in expected_descendant_keys
            )
            ordered_keys.extend(
                key for key in by_key if key in closure and key not in ordered_keys
            )
            return tuple(by_key[key] for key in ordered_keys)
        except DurableExecutionStateError:
            raise
        except Exception:
            raise DurableExecutionStateError() from None

    async def _repair_root_pointer(self, snapshot: StateSnapshot) -> None:
        """修复入口也必须携带完整 StateSnapshot，不允许裸 tuple 提升。"""

        await self._confirm_graph_state(snapshot)

    async def aclose(self) -> None:
        if self._checkpoint_pool is not None:
            await self._checkpoint_pool.close()


def _checkpoint_pool_kwargs(settings: DurableExecutionSettings) -> dict[str, object]:
    return {
        "host": settings.host,
        "port": settings.port,
        "dbname": settings.database,
        "user": settings.user,
        "password": settings.password.get_secret_value(),
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "connect_timeout": 10,
        "options": "-c search_path=durable_execution,pg_catalog -c timezone=UTC -c synchronous_commit=on -c statement_timeout=10000 -c lock_timeout=5000",
    }


async def create_fenced_checkpoint_saver(
    *,
    settings: DurableExecutionSettings,
    repository: DurableExecutionRepository,
    run_id: UUID,
    owner: UUID,
    fencing_token: int,
    limits: DurableExecutionLimits,
    registered_node_names: Sequence[str] = (),
    checkpoint_pool: AsyncConnectionPool | None = None,
) -> FencedCheckpointSaver:
    """创建官方 Saver 装饰器；未传共享池时保持独立 1/1 pool 行为。"""

    owns_pool = checkpoint_pool is None
    pool = checkpoint_pool or AsyncConnectionPool(
        "",
        kwargs=_checkpoint_pool_kwargs(settings),
        min_size=1,
        max_size=1,
        open=False,
        timeout=10.0,
    )
    try:
        if owns_pool:
            await pool.open(wait=True)
        saver = AsyncPostgresSaver(
            pool,
            serde=BoundedCheckpointSerializer(limits=limits),
        )
        return FencedCheckpointSaver(
            saver=saver,
            repository=repository,
            run_id=run_id,
            owner=owner,
            fencing_token=fencing_token,
            limits=limits,
            registered_node_names=registered_node_names,
            checkpoint_pool=pool if owns_pool else None,
        )
    except Exception:
        if owns_pool:
            await pool.close()
        raise DurableExecutionConfigurationError() from None


__all__ = ["create_fenced_checkpoint_saver"]
