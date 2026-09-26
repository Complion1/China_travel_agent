"""Module 11 的持久执行门面。"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid4

from langgraph.graph import StateGraph
from langgraph.types import StateSnapshot
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, TypeAdapter
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.durable_execution.budget import PersistentExecutionBudgetManager
from app.durable_execution.checkpointer import (
    FencedCheckpointSaver,
    _canonical_safe_value,
    _checkpoint_pool_kwargs,
    _strict_artifact_refs,
    create_fenced_checkpoint_saver,
)
from app.durable_execution.errors import (
    DurableExecutionConfigurationError,
    DurableExecutionDependencyError,
    DurableExecutionError,
    DurableExecutionIncompatibleRunError,
    DurableExecutionResumeRejectedError,
    DurableExecutionRunAlreadyExistsError,
    DurableExecutionRunAwaitingInputError,
    DurableExecutionRunBlockedError,
    DurableExecutionRunBusyError,
    DurableExecutionStateError,
    DurableExecutionUsageError,
)
from app.durable_execution.models import (
    DEMO_DURABLE_EXECUTION_LIMITS,
    DurableExecutionContract,
    DurableExecutionEncodedCommand,
    DurableExecutionLimits,
    DurableExecutionPaused,
    DurableExecutionPauseEnvelope,
    DurableExecutionResult,
    DurableExecutionTerminal,
    NodeRecoveryClass,
    contract_canonical_bytes,
)
from app.durable_execution.protocols import (
    DurableExecutionBudgetPlanProvider,
    DurableExecutionCommandCodec,
    DurableExecutionGraphCapabilities,
    DurableExecutionNodeCapabilities,
)
from app.durable_execution.repository import (
    DurableExecutionRepository,
    _BlockCause,
    _BlockFacts,
    _PHASE_ORDINAL,
    check_durable_execution_schema,
)
from app.durable_execution.settings import DurableExecutionSettings
from app.requirement_subgraph import (
    RequirementSubgraphDurablePause,
    RequirementSubgraphEmbeddedDriver,
    RequirementSubgraphPaused,
)
from app.workflow_contracts import (
    ArtifactAbsent,
    ArtifactRef,
    ArtifactFound,
    ArtifactRepository,
    ArtifactRepositoryDependencyError,
    ArtifactWriteRequest,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    OperationId,
    OperationResultIndeterminateError,
    TravelSessionState,
    WorkflowPhase,
)


_NATIVE_PAUSE_DOMAIN = b"durable-execution-pause-token-v1\x00"
_CURRENT_CONTEXT: contextvars.ContextVar["_TaskContext | None"] = contextvars.ContextVar(
    "durable_execution_task_context", default=None
)


def _fresh_model(model_type: object, value: object, error_type: type[Exception]) -> Any:
    try:
        adapter = TypeAdapter(model_type)
        result = adapter.validate_python(value, strict=True)
        if isinstance(result, BaseModel):
            encoded = json.dumps(
                result.model_dump(
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
            result = adapter.validate_json(encoded, strict=True)
        return result
    except Exception:
        raise error_type() from None


def _fresh_state(value: object) -> TravelSessionState:
    try:
        if type(value) is TravelSessionState:
            source = value.model_dump(
                mode="json",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
        elif isinstance(value, Mapping):
            source = dict(value)
        else:
            source = None
        if source is not None and "artifact_refs" in source:
            refs = _strict_artifact_refs(source["artifact_refs"])
            payload_without_refs = dict(source)
            payload_without_refs["artifact_refs"] = []
            encoded = json.dumps(
                _canonical_safe_value(payload_without_refs),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            base_state = TypeAdapter(TravelSessionState).validate_json(encoded, strict=True)
            payload = base_state.model_dump(
                mode="python",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            payload["artifact_refs"] = refs
            result = TypeAdapter(TravelSessionState).validate_python(payload, strict=True)
        else:
            result = _fresh_model(TravelSessionState, value, DurableExecutionStateError)
    except Exception:
        raise DurableExecutionStateError() from None
    if type(result) is not TravelSessionState:
        raise DurableExecutionStateError() from None
    return result


def _fresh_encoded(value: object) -> DurableExecutionEncodedCommand:
    result = _fresh_model(DurableExecutionEncodedCommand, value, DurableExecutionStateError)
    if type(result) is not DurableExecutionEncodedCommand:
        raise DurableExecutionStateError() from None
    return result


def _canonical_bytes(value: BaseModel) -> bytes:
    try:
        return json.dumps(
            value.model_dump(
                mode="json",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        raise DurableExecutionStateError() from None


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _plan_digest(plan: ExecutionBudgetPlan) -> str:
    return _digest(_canonical_bytes(plan))


def _pointer(row: Mapping[str, object]) -> tuple[str, str, str] | None:
    values = (
        row.get("latest_root_checkpoint_ns"),
        row.get("latest_root_checkpoint_id"),
        row.get("latest_root_checkpoint_digest"),
    )
    if all(value is None for value in values):
        return None
    if any(type(value) is not str for value in values) or values[0] != "":
        raise DurableExecutionStateError() from None
    if not 1 <= len(values[1]) <= 200 or not values[2].startswith("sha256:"):
        raise DurableExecutionStateError() from None
    return values[0], values[1], values[2]


def _pending_pointer(row: Mapping[str, object]) -> tuple[str, str, str] | None:
    values = (row.get("pre_checkpoint_ns"), row.get("pre_checkpoint_id"), row.get("pre_checkpoint_digest"))
    if all(value is None for value in values):
        return None
    if any(type(value) is not str for value in values):
        raise DurableExecutionStateError() from None
    return values[0], values[1], values[2]


def _native_token_digest(run_id: UUID, token: str) -> str:
    return _digest(
        _NATIVE_PAUSE_DOMAIN
        + str(run_id).encode("ascii")
        + b"\x00"
        + token.encode("ascii")
    )


@dataclass(frozen=True, slots=True)
class _TaskContext:
    run_id: UUID
    owner: UUID
    fencing_token: int
    command: object
    node_name: str | None
    checkpoint: tuple[str, str, str] | None
    checkpoint_accessor: FencedCheckpointSaver | None = None


@contextmanager
def _bind_root_node_context(*, node_name: str):
    if type(node_name) is not str or not node_name:
        raise DurableExecutionUsageError() from None
    current = _CURRENT_CONTEXT.get()
    if (
        type(current) is not _TaskContext
        or type(current.run_id) is not UUID
        or type(current.owner) is not UUID
        or type(current.fencing_token) is not int
        or current.fencing_token < 1
    ):
        raise DurableExecutionUsageError() from None
    token = _CURRENT_CONTEXT.set(replace(current, node_name=node_name))
    try:
        yield
    finally:
        _CURRENT_CONTEXT.reset(token)


class _ArtifactRepositoryAdapter:
    __slots__ = ("_graph", "_node_name")

    def __init__(self, graph: "_GraphCapabilities", node_name: str) -> None:
        self._graph = graph
        self._node_name = node_name

    def _context(self) -> _TaskContext:
        node = self._graph._nodes.get(self._node_name)
        if node is None:
            raise DurableExecutionUsageError() from None
        return node._context()

    @staticmethod
    def _fresh_request(value: object) -> ArtifactWriteRequest:
        return _fresh_model(ArtifactWriteRequest, value, DurableExecutionUsageError)

    @staticmethod
    def _fresh_ref(value: object) -> ArtifactRef:
        return _fresh_model(ArtifactRef, value, DurableExecutionUsageError)

    @staticmethod
    def _journal_matches_ref(evidence: Mapping[str, object], ref: ArtifactRef) -> bool:
        return (
            evidence.get("latest_artifact_id"),
            evidence.get("latest_artifact_type"),
            evidence.get("latest_artifact_schema_version"),
            evidence.get("latest_artifact_content_hash"),
            evidence.get("latest_artifact_created_at"),
            evidence.get("latest_artifact_size_bytes"),
        ) == (
            ref.artifact_id,
            ref.artifact_type,
            ref.schema_version,
            ref.content_hash,
            ref.created_at,
            ref.size_bytes,
        )

    @staticmethod
    def _operation_id(value: object) -> OperationId:
        return _fresh_model(OperationId, value, DurableExecutionUsageError)

    async def lookup(self, *, operation_id: OperationId):
        context = self._context()
        fresh_operation_id = self._operation_id(operation_id)
        if fresh_operation_id.run_id != context.run_id:
            raise DurableExecutionUsageError() from None
        return await self._graph.artifact_repository.lookup(operation_id=fresh_operation_id)

    async def get(self, ref: ArtifactRef, *, expected_model: type[BaseModel]):
        context = self._context()
        fresh_ref = self._fresh_ref(ref)
        if fresh_ref.operation_id.run_id != context.run_id:
            raise DurableExecutionUsageError() from None
        if not isinstance(expected_model, type) or not issubclass(expected_model, BaseModel):
            raise DurableExecutionUsageError() from None
        artifact = await self._graph.artifact_repository.get(
            fresh_ref,
            expected_model=expected_model,
        )
        if self._graph._rule(self._node_name).recovery_class is NodeRecoveryClass.JOURNAL_GUARDED:
            context = self._context()
            evidence = await self._graph.repository.get_operation_journal_evidence(
                run_id=context.run_id,
                operation_id=fresh_ref.operation_id,
                node_name=self._node_name,
                owner=context.owner,
                fencing_token=context.fencing_token,
            )
            if evidence["latest_state"] not in {
                "execution_permitted", "artifact_confirmed", "checkpoint_confirmed", "completed",
            }:
                raise DurableExecutionStateError() from None
            if evidence["latest_state"] == "execution_permitted":
                await self._graph.repository.append_journal_transition(
                    run_id=context.run_id,
                    operation_phase=fresh_ref.operation_id.phase.value,
                    logical_step=fresh_ref.operation_id.logical_step,
                    node_name=self._node_name,
                    journal_state="artifact_confirmed",
                    owner=context.owner,
                    fencing_token=context.fencing_token,
                    artifact={
                        "artifact_id": fresh_ref.artifact_id,
                        "artifact_type": fresh_ref.artifact_type,
                        "artifact_schema_version": fresh_ref.schema_version,
                        "artifact_content_hash": fresh_ref.content_hash,
                        "artifact_created_at": fresh_ref.created_at,
                        "artifact_size_bytes": fresh_ref.size_bytes,
                    },
                )
            elif not self._journal_matches_ref(evidence, fresh_ref):
                raise DurableExecutionStateError() from None
        return artifact

    async def put(self, request: ArtifactWriteRequest) -> ArtifactRef:
        context = self._context()
        fresh_request = self._fresh_request(request)
        operation_id = self._operation_id(fresh_request.operation_id)
        if operation_id.run_id != context.run_id:
            raise DurableExecutionUsageError() from None
        rule = self._graph._rule(self._node_name)
        if rule.recovery_class is NodeRecoveryClass.REPLAY_SAFE:
            raise DurableExecutionUsageError() from None
        existing = await self._graph.artifact_repository.lookup(operation_id=operation_id)
        if isinstance(existing, ArtifactFound):
            fresh_ref = self._fresh_ref(existing.ref)
            try:
                canonical = json.dumps(
                    fresh_request.content,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                content_hash = "sha256:" + hashlib.sha256(canonical).hexdigest()
            except Exception:
                raise DurableExecutionStateError() from None
            if (
                fresh_ref.operation_id != operation_id
                or fresh_ref.artifact_type != fresh_request.artifact_type
                or fresh_ref.schema_version != fresh_request.schema_version
                or fresh_ref.content_hash != content_hash
                or fresh_ref.size_bytes != len(canonical)
            ):
                raise DurableExecutionStateError() from None
            if rule.recovery_class is NodeRecoveryClass.JOURNAL_GUARDED:
                evidence = await self._graph.repository.get_operation_journal_evidence(
                    run_id=context.run_id,
                    operation_id=operation_id,
                    node_name=self._node_name,
                    owner=context.owner,
                    fencing_token=context.fencing_token,
                )
                if evidence["latest_state"] not in {
                    "execution_permitted", "artifact_confirmed", "checkpoint_confirmed", "completed",
                }:
                    raise DurableExecutionStateError() from None
                artifact = {
                    "artifact_id": fresh_ref.artifact_id,
                    "artifact_type": fresh_ref.artifact_type,
                    "artifact_schema_version": fresh_ref.schema_version,
                    "artifact_content_hash": fresh_ref.content_hash,
                    "artifact_created_at": fresh_ref.created_at,
                    "artifact_size_bytes": fresh_ref.size_bytes,
                }
                if evidence["latest_state"] in {"checkpoint_confirmed", "completed"}:
                    if not self._journal_matches_ref(evidence, fresh_ref):
                        raise DurableExecutionStateError() from None
                else:
                    await self._graph.repository.append_journal_transition(
                        run_id=context.run_id,
                        operation_phase=operation_id.phase.value,
                        logical_step=operation_id.logical_step,
                        node_name=self._node_name,
                        journal_state="artifact_confirmed",
                        owner=context.owner,
                        fencing_token=context.fencing_token,
                        artifact=artifact,
                    )
            return fresh_ref
        if not isinstance(existing, ArtifactAbsent) or existing.operation_id != operation_id:
            raise DurableExecutionStateError() from None
        if rule.recovery_class is NodeRecoveryClass.JOURNAL_GUARDED:
            evidence = await self._graph.repository.get_operation_journal_evidence(
                run_id=context.run_id,
                operation_id=operation_id,
                node_name=self._node_name,
                owner=context.owner,
                fencing_token=context.fencing_token,
            )
            if evidence["latest_state"] != "execution_permitted":
                raise DurableExecutionStateError() from None
        fresh_ref = self._fresh_ref(
            await self._graph.artifact_repository.put(fresh_request)
        )
        if fresh_ref.operation_id != operation_id:
            raise DurableExecutionStateError() from None
        if rule.recovery_class is NodeRecoveryClass.JOURNAL_GUARDED:
            await self._graph.repository.append_journal_transition(
                run_id=context.run_id,
                operation_phase=operation_id.phase.value,
                logical_step=operation_id.logical_step,
                node_name=self._node_name,
                journal_state="artifact_confirmed",
                owner=context.owner,
                fencing_token=context.fencing_token,
                artifact={
                    "artifact_id": fresh_ref.artifact_id,
                    "artifact_type": fresh_ref.artifact_type,
                    "artifact_schema_version": fresh_ref.schema_version,
                    "artifact_content_hash": fresh_ref.content_hash,
                    "artifact_created_at": fresh_ref.created_at,
                    "artifact_size_bytes": fresh_ref.size_bytes,
                },
            )
        return fresh_ref


class _RunBoundBudgetManager:
    __slots__ = ("_capabilities", "_node_name", "_run_id")

    def __init__(self, capabilities: "_GraphCapabilities", node_name: str, run_id: UUID) -> None:
        self._capabilities = capabilities
        self._node_name = node_name
        self._run_id = run_id

    def _manager(self) -> PersistentExecutionBudgetManager:
        context = _CURRENT_CONTEXT.get()
        if context is None or context.run_id != self._run_id or context.node_name != self._node_name:
            raise DurableExecutionUsageError() from None
        return PersistentExecutionBudgetManager(
            repository=self._capabilities.repository,
            run_id=context.run_id,
            owner=context.owner,
            fencing_token=context.fencing_token,
            node_name=self._node_name,
        )

    async def consume(self, request):
        return await self._manager().consume(request)

    async def get_snapshot(self, *, key):
        return await self._manager().get_snapshot(key=key)

    async def get_all_snapshots(self):
        return await self._manager().get_all_snapshots()


class _NodeCapabilities:
    __slots__ = ("_graph", "_node_name")

    def __init__(self, graph: "_GraphCapabilities", node_name: str) -> None:
        self._graph = graph
        self._node_name = node_name

    @property
    def node_name(self) -> str:
        return self._node_name

    def _context(self) -> _TaskContext:
        context = _CURRENT_CONTEXT.get()
        if context is None or context.node_name != self._node_name:
            raise DurableExecutionUsageError() from None
        return context

    @property
    def artifact_repository(self) -> ArtifactRepository:
        self._context()
        return self._graph.artifact_repository_for(node_name=self._node_name)

    @property
    def execution_budget_manager(self):
        return self._graph.execution_budget_manager_factory(node_name=self._node_name)(self._context().run_id)

    def current_command(self) -> object:
        return self._context().command

    async def ensure_phase_budget(self, *, state: TravelSessionState) -> None:
        context = self._context()
        fresh_state = _fresh_state(state)
        if fresh_state.run_id != context.run_id:
            raise DurableExecutionUsageError() from None
        await self._graph.ensure_phase_budget(
            run_id=context.run_id,
            owner=context.owner,
            fencing_token=context.fencing_token,
            node_name=self._node_name,
            state=fresh_state,
            checkpoint_accessor=context.checkpoint_accessor,
        )

    @contextmanager
    def task_context(self, *, run_id: UUID, command: object):
        current = _CURRENT_CONTEXT.get()
        if type(run_id) is not UUID or current is None or current.run_id != run_id:
            raise DurableExecutionUsageError() from None
        token = _CURRENT_CONTEXT.set(
            _TaskContext(
                run_id=run_id,
                owner=current.owner,
                fencing_token=current.fencing_token,
                command=command,
                node_name=self._node_name,
                checkpoint=current.checkpoint,
                checkpoint_accessor=current.checkpoint_accessor,
            )
        )
        try:
            yield self
        finally:
            _CURRENT_CONTEXT.reset(token)


class _GraphCapabilities:
    __slots__ = (
        "repository", "artifact_repository", "contract", "provider", "limits",
        "_nodes", "_managers", "_drivers", "_artifact_adapters",
    )

    def __init__(self, *, repository, artifact_repository, contract, provider, limits) -> None:
        self.repository = repository
        self.artifact_repository = artifact_repository
        self.contract = contract
        self.provider = provider
        self.limits = limits
        self._nodes: dict[str, _NodeCapabilities] = {}
        self._managers: dict[tuple[str, UUID], _RunBoundBudgetManager] = {}
        self._drivers: dict[str, RequirementSubgraphEmbeddedDriver] = {}
        self._artifact_adapters: dict[str, _ArtifactRepositoryAdapter] = {}

    @property
    def requirement_subgraph_limits(self):
        from app.requirement_subgraph import RequirementSubgraphCoordinatorLimits

        return RequirementSubgraphCoordinatorLimits(max_runs=self.limits.max_stored_runs)

    def _rule(self, node_name: str):
        if type(node_name) is not str:
            raise DurableExecutionUsageError() from None
        for rule in self.contract.node_rules:
            if rule.node_name == node_name:
                return rule
        raise DurableExecutionUsageError() from None

    def for_node(self, *, node_name: str) -> DurableExecutionNodeCapabilities:
        self._rule(node_name)
        if node_name not in self._nodes:
            self._nodes[node_name] = _NodeCapabilities(self, node_name)
        return self._nodes[node_name]

    def execution_budget_manager_factory(self, *, node_name: str) -> Callable[[UUID], object]:
        self.for_node(node_name=node_name)

        def build(run_id: UUID) -> object:
            if type(run_id) is not UUID:
                raise DurableExecutionUsageError() from None
            key = (node_name, run_id)
            if key not in self._managers:
                self._managers[key] = _RunBoundBudgetManager(self, node_name, run_id)
            return self._managers[key]

        return build

    def artifact_repository_for(self, *, node_name: str) -> ArtifactRepository:
        self._rule(node_name)
        if node_name not in self._artifact_adapters:
            self._artifact_adapters[node_name] = _ArtifactRepositoryAdapter(self, node_name)
        return self._artifact_adapters[node_name]

    def register_requirement_subgraph(self, *, node_name: str, driver: RequirementSubgraphEmbeddedDriver) -> None:
        if node_name != "requirements" or type(driver) is not RequirementSubgraphEmbeddedDriver:
            raise DurableExecutionUsageError() from None
        self.for_node(node_name=node_name)
        if node_name in self._drivers:
            raise DurableExecutionUsageError() from None
        qualified_rules = {
            rule.node_name: rule
            for rule in self.contract.node_rules
            if rule.node_name in {
                "requirements/process_initial_turn",
                "requirements/await_clarification",
                "requirements/await_confirmation",
            }
        }
        if set(qualified_rules) != {
            "requirements/process_initial_turn",
            "requirements/await_clarification",
            "requirements/await_confirmation",
        } or any(
            rule.recovery_class is not NodeRecoveryClass.NON_RECOVERABLE
            for rule in qualified_rules.values()
        ):
            raise DurableExecutionConfigurationError() from None
        if (
            driver.limits != self.requirement_subgraph_limits
            or getattr(driver.graph, "checkpointer", None) is not None
            or not all(callable(getattr(driver, name, None)) for name in (
                "start_operation", "verify_pause_token", "resume_accepted_operation", "resolve_result"
            ))
        ):
            raise DurableExecutionUsageError() from None
        self._drivers[node_name] = driver

    @property
    def drivers(self) -> Mapping[str, RequirementSubgraphEmbeddedDriver]:
        return self._drivers

    @staticmethod
    def _activation_pointer(row: Mapping[str, object]) -> tuple[str, str, str]:
        namespace = row.get("activation_checkpoint_ns")
        checkpoint_id = row.get("activation_checkpoint_id")
        digest = row.get("activation_checkpoint_digest")
        if (
            type(namespace) is not str
            or namespace != ""
            or type(checkpoint_id) is not str
            or type(digest) is not str
        ):
            raise DurableExecutionStateError() from None
        return namespace, checkpoint_id, digest

    async def ensure_phase_budget(
        self, *, run_id, owner, fencing_token, node_name, state, checkpoint_accessor
    ) -> None:
        rule = self._rule(node_name)
        phase = rule.phase
        if phase not in self.contract.budgeted_phases or state.phase is not phase:
            raise DurableExecutionStateError() from None
        if phase is WorkflowPhase.REQUIREMENTS:
            plan = _fresh_model(
                ExecutionBudgetPlan,
                self.provider.plan_for_start(run_id=run_id),
                DurableExecutionStateError,
            )
            await self.repository.activate_phase(
                run_id=run_id,
                owner=owner,
                fencing_token=fencing_token,
                phase=phase,
                plan=plan,
                checkpoint=None,
            )
            return
        if type(checkpoint_accessor) is not FencedCheckpointSaver:
            raise DurableExecutionStateError() from None
        ordinal = _PHASE_ORDINAL[phase.value]
        rows = await self.repository.get_budget_activation_rows(run_id=run_id)
        phase_rows = tuple(
            row
            for row in rows
            if row.get("phase_code") == phase.value
            and int(row["activation_ordinal"]) == ordinal
        )
        if phase_rows:
            checkpoint = self._activation_pointer(phase_rows[0])
            plan_state = await checkpoint_accessor.activation_state(checkpoint)
            if plan_state.run_id != run_id or plan_state.phase is not phase:
                raise DurableExecutionStateError() from None
        else:
            checkpoint = await checkpoint_accessor.activation_pointer()
            plan_state = await checkpoint_accessor.activation_state(checkpoint)
            if plan_state.run_id != run_id or plan_state.phase is not phase:
                raise DurableExecutionStateError() from None
            if _canonical_bytes(plan_state) != _canonical_bytes(state):
                raise DurableExecutionStateError() from None
        plan = _fresh_model(
            ExecutionBudgetPlan,
            self.provider.plan_for_phase(run_id=run_id, phase=phase, state=plan_state),
            DurableExecutionStateError,
        )
        await self.repository.activate_phase(
            run_id=run_id,
            owner=owner,
            fencing_token=fencing_token,
            phase=phase,
            plan=plan,
            checkpoint=checkpoint,
        )

    @contextmanager
    def bind(self, *, run_id, owner, fencing_token, command, node_name, checkpoint, checkpoint_accessor=None):
        token = _CURRENT_CONTEXT.set(
            _TaskContext(
                run_id,
                owner,
                fencing_token,
                command,
                node_name,
                checkpoint,
                checkpoint_accessor,
            )
        )
        try:
            yield
        finally:
            _CURRENT_CONTEXT.reset(token)

class _NativeOperation:
    def __init__(self, state: TravelSessionState) -> None:
        self._state = state

    async def __aenter__(self) -> TravelSessionState:
        return self._state

    async def __aexit__(self, exc_type, exc_value, traceback) -> bool:
        del exc_type, exc_value, traceback
        return False


def _install_requirement_context_bridge(
    graph_template: StateGraph,
    driver: RequirementSubgraphEmbeddedDriver,
    contexts: dict[UUID, contextvars.Context],
    results: dict[UUID, object],
) -> None:
    """让嵌入式 Module 05 节点在 LangGraph 子任务中看到同一操作租约。"""

    nodes = getattr(graph_template, "nodes", None)
    if not isinstance(nodes, Mapping) or "requirements" not in nodes:
        raise DurableExecutionConfigurationError() from None
    spec = nodes["requirements"]
    runnable = getattr(spec, "runnable", None)
    if not callable(getattr(runnable, "ainvoke", None)):
        raise DurableExecutionConfigurationError() from None
    async def invoke(value: object, config: Mapping[str, object] | None = None) -> object:
        raw_run_id = None
        if isinstance(config, Mapping):
            configurable = config.get("configurable")
            if isinstance(configurable, Mapping):
                raw_run_id = configurable.get("thread_id")
        if raw_run_id is None:
            if isinstance(value, TravelSessionState):
                raw_run_id = str(value.run_id)
            elif isinstance(value, Mapping):
                raw_run_id = value.get("run_id")
        try:
            run_id = raw_run_id if type(raw_run_id) is UUID else UUID(str(raw_run_id))
        except Exception:
            raise DurableExecutionStateError() from None
        driver_context = contexts.get(run_id)
        if driver_context is None:
            raise DurableExecutionStateError() from None
        result = await asyncio.create_task(
            runnable.ainvoke(value, config=config),
            context=driver_context.copy(),
        )
        results[run_id] = result
        return result

    try:
        nodes["requirements"] = replace(spec, runnable=RunnableLambda(invoke))
    except Exception:
        raise DurableExecutionConfigurationError() from None


def _install_root_node_task_contexts(
    graph_template: StateGraph,
    capabilities: _GraphCapabilities,
) -> None:
    """按实际注册的根 node 名绑定 capability task context。"""

    nodes = getattr(graph_template, "nodes", None)
    if not isinstance(nodes, Mapping) or not nodes:
        raise DurableExecutionConfigurationError() from None

    def wrap(node_name: str, runnable):
        if not callable(getattr(runnable, "invoke", None)) or not callable(
            getattr(runnable, "ainvoke", None)
        ):
            raise DurableExecutionConfigurationError() from None

        def invoke(value: object, config=None, **kwargs):
            with _bind_root_node_context(node_name=node_name):
                return runnable.invoke(value, config=config, **kwargs)

        async def ainvoke(value: object, config=None, **kwargs):
            with _bind_root_node_context(node_name=node_name):
                return await runnable.ainvoke(value, config=config, **kwargs)

        return RunnableLambda(invoke, afunc=ainvoke)

    try:
        for node_name, spec in tuple(nodes.items()):
            if type(node_name) is not str:
                raise DurableExecutionConfigurationError() from None
            runnable = getattr(spec, "runnable", None)
            nodes[node_name] = replace(
                spec,
                runnable=wrap(node_name, runnable),
            )
    except DurableExecutionError:
        raise
    except Exception:
        raise DurableExecutionConfigurationError() from None


class DurableExecutionRuntime:
    __slots__ = (
        "_settings", "_artifact_repository", "_contract", "_command_codec",
        "_budget_plan_provider", "_graph_factory", "_limits", "_closed",
        "_coordination_pool", "_checkpoint_pool", "_repository", "_capabilities",
        "_graph_template", "_owner", "_poisoned", "_operations", "_renew_tasks",
        "_requirement_contexts", "_requirement_results",
    )

    def __init__(self, *, settings, artifact_repository, contract, command_codec,
                 budget_plan_provider, graph_factory, limits, coordination_pool,
                 checkpoint_pool, repository, capabilities, graph_template,
                 requirement_contexts, requirement_results) -> None:
        self._settings = settings
        self._artifact_repository = artifact_repository
        self._contract = contract
        self._command_codec = command_codec
        self._budget_plan_provider = budget_plan_provider
        self._graph_factory = graph_factory
        self._limits = limits
        self._closed = False
        self._coordination_pool = coordination_pool
        self._checkpoint_pool = checkpoint_pool
        self._repository = repository
        self._capabilities = capabilities
        self._graph_template = graph_template
        self._owner = uuid4()
        self._poisoned: set[UUID] = set()
        self._operations: set[asyncio.Task[Any]] = set()
        self._renew_tasks: set[asyncio.Task[Any]] = set()
        self._requirement_contexts = requirement_contexts
        self._requirement_results = requirement_results

    def _ensure_open(self) -> None:
        if self._closed:
            raise DurableExecutionUsageError() from None

    def _check_poisoned(self, run_id: UUID) -> None:
        if type(run_id) is UUID and run_id in self._poisoned:
            raise DurableExecutionStateError() from None

    def _check_codec(self) -> None:
        try:
            if self._command_codec.descriptor != self._contract.command_codec:
                raise DurableExecutionConfigurationError() from None
        except DurableExecutionConfigurationError:
            raise
        except Exception:
            raise DurableExecutionConfigurationError() from None

    async def _existing_run(self, run_id: UUID):
        try:
            return await self._repository.get_run_row(run_id=run_id)
        except Exception as exc:
            from app.durable_execution.errors import DurableExecutionRunNotFoundError
            if isinstance(exc, DurableExecutionRunNotFoundError):
                return None
            raise

    async def start(self, *, run_id: UUID, trace_id: UUID, command: object) -> DurableExecutionResult:
        self._ensure_open()
        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        if await self._existing_run(run_id) is not None:
            raise DurableExecutionRunAlreadyExistsError() from None
        if type(trace_id) is not UUID:
            raise DurableExecutionUsageError() from None
        self._check_codec()
        try:
            encoded = _fresh_encoded(self._command_codec.encode_initial(run_id=run_id, trace_id=trace_id, command=command))
            decoded = self._command_codec.decode_initial(run_id=run_id, trace_id=trace_id, encoded=encoded)
            if _fresh_encoded(self._command_codec.encode_initial(run_id=run_id, trace_id=trace_id, command=decoded)) != encoded:
                raise DurableExecutionStateError() from None
            state = _fresh_state(self._command_codec.initial_state(run_id=run_id, trace_id=trace_id, decoded_command=decoded))
            turn = self._command_codec.requirement_turn(run_id=run_id, trace_id=trace_id, decoded_command=decoded)
            plan = _fresh_model(ExecutionBudgetPlan, self._budget_plan_provider.plan_for_start(run_id=run_id), DurableExecutionStateError)
            if plan.run_id != run_id:
                raise DurableExecutionStateError() from None
            owner = self._owner
            await self._repository.create_run(
                run_id=run_id, trace_id=trace_id, contract=self._contract,
                start_plan=plan, initial_command=encoded, initial_owner=owner,
            )
            return await self._execute_pending(
                run_id=run_id, pause_generation=0, encoded=encoded, decoded=decoded,
                state=state, command=turn, owner=owner, fencing_token=1,
                kind="initial", node_name="requirements",
                driver=self._capabilities.drivers.get("requirements"),
            )
        except (DurableExecutionError,):
            raise
        except Exception:
            raise DurableExecutionDependencyError() from None

    async def resume(self, *, run_id: UUID, pause_token: str, command: object) -> DurableExecutionResult:
        self._ensure_open()
        self._check_poisoned(run_id)
        if type(run_id) is not UUID or type(pause_token) is not str:
            raise DurableExecutionUsageError() from None
        row = await self._repository.get_run_row(run_id=run_id)
        self._check_resume_lifecycle(row)
        await self._check_stored_contract(run_id)
        pending = await self._repository.get_pending_command(run_id=run_id)
        self._check_waiting_pending(row, pending)
        assert pending is not None
        generation = int(pending["pause_generation"])
        context = self._decode_pause_context(run_id, row, pending)
        driver = self._capabilities.drivers.get("requirements") if pending["pause_context_type"] == "requirement_subgraph.pause" else None
        if driver is not None:
            driver.verify_pause_token(durable_pause=context, pause_token=pause_token)
        elif not hmac.compare_digest(str(pending["pause_token_digest"]), _native_token_digest(run_id, pause_token)):
            raise DurableExecutionResumeRejectedError() from None
        await self._verify_recovery_budget_activations(run_id)
        self._check_codec()
        encoded = _fresh_encoded(self._command_codec.encode_resume(
            run_id=run_id, pause_generation=generation, node_name=str(pending["node_name"]), command=command
        ))
        decoded = self._command_codec.decode_resume(
            run_id=run_id, pause_generation=generation, node_name=str(pending["node_name"]), encoded=encoded
        )
        if _fresh_encoded(self._command_codec.encode_resume(
            run_id=run_id, pause_generation=generation, node_name=str(pending["node_name"]), command=decoded
        )) != encoded:
            raise DurableExecutionStateError() from None
        owner = self._owner
        token = await self._repository.acquire_lease(run_id=run_id, owner=owner)
        try:
            state, _ = await self._read_graph_state(run_id=run_id, owner=owner, fencing_token=token)
            self._validate_pause_context(
                run_id=run_id, row=row, pending=pending, state=state,
                context=context, generation=generation,
            )
            await self._repository.transition_pending_command(
                run_id=run_id, pause_generation=generation, expected_status="awaiting_input",
                new_status="accepted", owner=owner, fencing_token=token, encoded_command=encoded,
            )
            resume = self._command_codec.requirement_resume(
                run_id=run_id, pause_generation=generation, node_name=str(pending["node_name"]), decoded_command=decoded
            ) if driver is not None else decoded
            return await self._execute_pending(
                run_id=run_id, pause_generation=generation, encoded=encoded, decoded=decoded,
                state=state, command=resume, owner=owner, fencing_token=token, kind="resume",
                node_name=str(pending["node_name"]), durable_pause=context if driver is not None else None,
                driver=driver, accepted_already=True,
            )
        except BaseException:
            await self._safe_release(run_id, owner, token)
            raise

    async def recover(self, *, run_id: UUID) -> DurableExecutionResult:
        self._ensure_open()
        self._check_poisoned(run_id)
        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        row = await self._repository.get_run_row(run_id=run_id)
        if row["lifecycle"] == "blocked":
            raise DurableExecutionRunBlockedError() from None
        if row["lifecycle"] == "closed":
            return await self._read_terminal_state(run_id)
        await self._check_stored_contract(run_id)
        if bool((await self._repository.get_lease_state(run_id=run_id))["active"]):
            raise DurableExecutionRunBusyError() from None
        pending = await self._repository.get_pending_command(run_id=run_id)
        if pending is None:
            await self._block(run_id, _BlockCause.pending_command_invalid())
            raise DurableExecutionStateError() from None
        status = str(pending["status"])
        if status == "awaiting_input":
            try:
                self._check_waiting_pending(row, pending)
            except DurableExecutionStateError:
                await self._block(run_id, _BlockCause.pending_command_invalid())
                raise DurableExecutionStateError() from None
            raise DurableExecutionRunAwaitingInputError() from None
        if status == "accepted":
            await self._verify_recovery_budget_activations(run_id)
            return await self._recover_accepted(row, pending)
        if status == "dispatch_started":
            return await self._recover_dispatch_started(row, pending)
        if status in {"checkpointed", "cleared"}:
            pointer = _pointer(row)
            if pointer is None:
                await self._block(run_id, _BlockCause.checkpoint_invalid())
                raise DurableExecutionStateError() from None
            try:
                state, _ = await self._read_graph_state(run_id=run_id, owner=uuid4(), fencing_token=1)
            except (DurableExecutionRunBusyError, DurableExecutionRunBlockedError,
                    DurableExecutionDependencyError, DurableExecutionConfigurationError,
                    DurableExecutionIncompatibleRunError):
                raise
            except Exception:
                await self._block(run_id, _BlockCause.checkpoint_invalid())
                raise DurableExecutionStateError() from None
            if state.delivery_status.value not in {"in_progress", "awaiting_user"}:
                try:
                    await self._reconcile_state_artifacts(state)
                except DurableExecutionDependencyError:
                    raise
                except Exception:
                    await self._block(run_id, _BlockCause.artifact_invalid())
                    raise DurableExecutionStateError() from None
                return await self._repair_terminal(run_id, state, pointer)
            await self._verify_recovery_budget_activations(run_id)
            await self._block(run_id, _BlockCause.checkpoint_invalid())
            raise DurableExecutionStateError() from None
        await self._block(run_id, _BlockCause.pending_command_invalid())
        raise DurableExecutionStateError() from None

    async def _verify_recovery_budget_activations(self, run_id: UUID, *, recovery_guard=None) -> None:
        try:
            await self._verify_budget_activations(run_id)
        except (DurableExecutionDependencyError, DurableExecutionConfigurationError,
                DurableExecutionRunBusyError, DurableExecutionRunBlockedError,
                DurableExecutionIncompatibleRunError):
            raise
        except Exception:
            await self._block(run_id, _BlockCause.budget_mismatch(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None

    async def get_state(self, *, run_id: UUID) -> TravelSessionState:
        self._ensure_open()
        self._check_poisoned(run_id)
        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        row = await self._repository.get_run_row(run_id=run_id)
        if row["lifecycle"] == "blocked":
            raise DurableExecutionRunBlockedError() from None
        pointer = _pointer(row)
        if pointer is None:
            raise DurableExecutionStateError() from None
        if bool((await self._repository.get_lease_state(run_id=run_id))["active"]):
            raise DurableExecutionRunBusyError() from None
        state, _ = await self._read_graph_state(run_id=run_id, owner=uuid4(), fencing_token=1)
        row2 = await self._repository.get_run_row(run_id=run_id)
        if bool((await self._repository.get_lease_state(run_id=run_id))["active"]):
            raise DurableExecutionRunBusyError() from None
        if row2["lifecycle"] != row["lifecycle"] or _pointer(row2) != pointer:
            raise DurableExecutionStateError() from None
        if state.delivery_status.value not in {"in_progress", "awaiting_user"}:
            if row2["lifecycle"] == "open":
                return await self._repair_terminal(run_id, state, pointer)
            return state
        return state

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0

        async def within_deadline(awaitable):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            return await asyncio.wait_for(awaitable, timeout=remaining)

        tasks = tuple(self._operations) + tuple(self._renew_tasks)
        for task in tasks:
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
        if tasks:
            await within_deadline(asyncio.gather(*tasks, return_exceptions=True))
        await within_deadline(self._checkpoint_pool.close())
        await within_deadline(self._coordination_pool.close())

    async def __aenter__(self) -> "DurableExecutionRuntime":
        if self._closed:
            raise DurableExecutionUsageError() from None
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> bool:
        del traceback
        try:
            await self.close()
        except BaseException:
            if exc_value is not None:
                return False
            raise
        return False

    def _check_resume_lifecycle(self, row: Mapping[str, object]) -> None:
        if row["lifecycle"] == "closed":
            raise DurableExecutionResumeRejectedError() from None
        if row["lifecycle"] == "blocked":
            raise DurableExecutionRunBlockedError() from None

    def _check_waiting_pending(self, row: Mapping[str, object], pending: Mapping[str, object] | None) -> None:
        pointer = _pointer(row)
        if pending is None or pending["status"] != "awaiting_input" or pointer is None or _pending_pointer(pending) != pointer:
            raise DurableExecutionStateError() from None
        context_bytes = pending["pause_context_bytes"]
        if (
            int(pending["pause_generation"]) < 1
            or pending["command_bytes"] is not None
            or pending["command_type_id"] is not None
            or type(context_bytes) is not bytes
            or pending["pause_context_digest"] != _digest(context_bytes)
            or int(pending["pause_context_size_bytes"]) != len(context_bytes)
            or pending["pause_token_digest"] is None
            or pending["pause_context_type"] not in {"requirement_subgraph.pause", "durable_execution.root_pause"}
            or pending["pause_context_version"] != "1.0"
        ):
            raise DurableExecutionStateError() from None

    def _check_accepted_pending(self, row: Mapping[str, object], pending: Mapping[str, object]) -> None:
        if pending["status"] != "accepted":
            raise DurableExecutionStateError() from None
        generation = int(pending["pause_generation"])
        pointer = _pointer(row)
        command_bytes = pending["command_bytes"]
        if (
            type(command_bytes) is not bytes
            or pending["command_digest"] != _digest(command_bytes)
            or int(pending["command_size_bytes"]) != len(command_bytes)
            or type(pending["command_type_id"]) is not str
            or type(pending["command_type_version"]) is not str
            or pending["pre_checkpoint_ns"] != (None if pointer is None else pointer[0])
            or pending["pre_checkpoint_id"] != (None if pointer is None else pointer[1])
            or pending["pre_checkpoint_digest"] != (None if pointer is None else pointer[2])
        ):
            raise DurableExecutionStateError() from None
        if generation == 0:
            if (
                row["pause_generation"] != 0
                or row["bootstrap_state"] != "bootstrap_pending"
                or pointer is not None
                or pending["command_kind"] != "initial"
                or pending["node_name"] != "__start__"
                or any(
                    pending[name] is not None
                    for name in (
                        "pause_token_digest",
                        "pause_context_type",
                        "pause_context_version",
                        "pause_context_bytes",
                        "pause_context_digest",
                        "pause_context_size_bytes",
                        "pre_checkpoint_ns",
                        "pre_checkpoint_id",
                        "pre_checkpoint_digest",
                    )
                )
            ):
                raise DurableExecutionStateError() from None
            return
        if (
            row["pause_generation"] != generation
            or pointer is None
            or pending["command_kind"] != "resume"
            or type(pending["node_name"]) is not str
            or type(pending["pause_token_digest"]) is not str
            or type(pending["pause_context_type"]) is not str
            or pending["pause_context_version"] != "1.0"
            or type(pending["pause_context_bytes"]) is not bytes
            or pending["pause_context_digest"] != _digest(pending["pause_context_bytes"])
            or int(pending["pause_context_size_bytes"]) != len(pending["pause_context_bytes"])
            or pending["pause_context_type"] not in {"requirement_subgraph.pause", "durable_execution.root_pause"}
        ):
            raise DurableExecutionStateError() from None

    def _decode_pause_context(self, run_id: UUID, row: Mapping[str, object], pending: Mapping[str, object]) -> object:
        data = bytes(pending["pause_context_bytes"])
        if pending["pause_context_type"] == "requirement_subgraph.pause":
            context = _fresh_model(
                RequirementSubgraphDurablePause,
                TypeAdapter(RequirementSubgraphDurablePause).validate_json(data, strict=True),
                DurableExecutionStateError,
            )
            if context.run_id != run_id or context.trace_id != row["trace_id"] or context.pause_token_digest != pending["pause_token_digest"]:
                raise DurableExecutionStateError() from None
            return context
        try:
            value = json.loads(data.decode("utf-8"))
        except Exception:
            raise DurableExecutionStateError() from None
        if type(value) is not dict or value.get("schema_version") != "1.0" or value.get("run_id") != str(run_id) or value.get("trace_id") != str(row["trace_id"]):
            raise DurableExecutionStateError() from None
        return value

    def _validate_pause_context(
        self,
        *,
        run_id: UUID,
        row: Mapping[str, object],
        pending: Mapping[str, object],
        state: TravelSessionState,
        context: object,
        generation: int,
    ) -> None:
        if type(state) is not TravelSessionState or state.run_id != run_id or state.trace_id != row["trace_id"]:
            raise DurableExecutionStateError() from None
        expected_state_digest = _digest(_canonical_bytes(state))
        if isinstance(context, RequirementSubgraphDurablePause):
            if context.state_digest != expected_state_digest:
                raise DurableExecutionStateError() from None
            return
        if type(context) is not dict:
            raise DurableExecutionStateError() from None
        expected_pointer = _pending_pointer(pending)
        actual_pointer = context.get("pre_checkpoint")
        if (
            context.get("state_digest") != expected_state_digest
            or context.get("node_name") != "__root__"
            or context.get("pause_generation") != generation
            or type(actual_pointer) is not list
            or tuple(actual_pointer) != expected_pointer
        ):
            raise DurableExecutionStateError() from None

    async def _recover_accepted(self, row, pending) -> DurableExecutionResult:
        run_id = row["run_id"]
        try:
            self._check_accepted_pending(row, pending)
            self._check_codec()
            encoded = _fresh_encoded(DurableExecutionEncodedCommand(
                command_kind=str(pending["command_kind"]), type_id=str(pending["command_type_id"]),
                type_version=str(pending["command_type_version"]), canonical_bytes=pending["command_bytes"],
                digest=str(pending["command_digest"]), size_bytes=int(pending["command_size_bytes"]),
            ))
            generation = int(pending["pause_generation"])
            if generation == 0:
                evidence = await self._repository.get_managed_evidence_counts(run_id=run_id)
                if evidence["operation_count"] != 0 or evidence["journal_count"] != 0:
                    raise DurableExecutionStateError() from None
                decoded = self._command_codec.decode_initial(run_id=run_id, trace_id=row["trace_id"], encoded=encoded)
                if _fresh_encoded(self._command_codec.encode_initial(
                    run_id=run_id, trace_id=row["trace_id"], command=decoded,
                )) != encoded:
                    raise DurableExecutionStateError() from None
                state = _fresh_state(self._command_codec.initial_state(run_id=run_id, trace_id=row["trace_id"], decoded_command=decoded))
                command = self._command_codec.requirement_turn(run_id=run_id, trace_id=row["trace_id"], decoded_command=decoded)
                node_name, kind, pause, driver = "requirements", "initial", None, self._capabilities.drivers.get("requirements")
            else:
                pause = self._decode_pause_context(run_id, row, pending)
                node_name = str(pending["node_name"])
                decoded = self._command_codec.decode_resume(run_id=run_id, pause_generation=generation, node_name=node_name, encoded=encoded)
                if _fresh_encoded(self._command_codec.encode_resume(
                    run_id=run_id, pause_generation=generation, node_name=node_name, command=decoded,
                )) != encoded:
                    raise DurableExecutionStateError() from None
                state, _ = await self._read_graph_state(run_id=run_id, owner=uuid4(), fencing_token=1)
                self._validate_pause_context(
                    run_id=run_id, row=row, pending=pending, state=state,
                    context=pause, generation=generation,
                )
                driver = self._capabilities.drivers.get("requirements") if pending["pause_context_type"] == "requirement_subgraph.pause" else None
                command = self._command_codec.requirement_resume(run_id=run_id, pause_generation=generation, node_name=node_name, decoded_command=decoded) if driver is not None else decoded
                kind = "resume"
        except DurableExecutionRunBusyError:
            raise
        except Exception:
            await self._block(run_id, _BlockCause.pending_command_invalid())
            raise DurableExecutionStateError() from None
        owner = self._owner
        token = await self._repository.acquire_lease(run_id=run_id, owner=owner)
        try:
            return await self._execute_pending(
                run_id=run_id, pause_generation=generation, encoded=encoded, decoded=decoded,
                state=state, command=command, owner=owner, fencing_token=token,
                kind=kind, node_name=node_name, durable_pause=pause if driver is not None else None,
                driver=driver, accepted_already=True,
            )
        except BaseException:
            await self._safe_release(run_id, owner, token)
            raise

    async def _execute_pending(self, *, run_id, pause_generation, encoded, decoded, state, command,
                               owner, fencing_token, kind, node_name, durable_pause=None,
                               driver=None, accepted_already=False) -> DurableExecutionResult:
        current_task = asyncio.current_task()
        if current_task is not None:
            self._operations.add(current_task)
        saver = None
        renew_task = None
        try:
            saver = await create_fenced_checkpoint_saver(
                settings=self._settings, repository=self._repository, run_id=run_id,
                owner=owner, fencing_token=fencing_token, limits=self._limits,
                registered_node_names=self._registered_node_names(),
                checkpoint_pool=self._checkpoint_pool,
            )
            graph = self._compile(saver)
            config = self._config(run_id)
            pointer = _pointer(await self._repository.get_run_row(run_id=run_id))
            if kind == "initial" and driver is not None:
                operation = driver.start_operation(state=state, turn=command)
            elif driver is not None:
                operation = driver.resume_accepted_operation(state=state, durable_pause=durable_pause, resume=command)
            else:
                operation = _NativeOperation(state)
            with self._capabilities.bind(
                run_id=run_id, owner=owner, fencing_token=fencing_token,
                command=decoded, node_name=node_name, checkpoint=pointer,
                checkpoint_accessor=saver,
            ):
                try:
                    async with operation as graph_input:
                        if driver is not None:
                            self._requirement_contexts[run_id] = contextvars.copy_context()
                        try:
                            if self._is_contract_node(node_name):
                                await self._capabilities.ensure_phase_budget(
                                    run_id=run_id,
                                    owner=owner,
                                    fencing_token=fencing_token,
                                    node_name=node_name,
                                    state=state,
                                    checkpoint_accessor=saver,
                                )
                            await self._repository.transition_pending_command(
                                run_id=run_id, pause_generation=pause_generation,
                                expected_status="accepted", new_status="dispatch_started",
                                owner=owner, fencing_token=fencing_token, encoded_command=encoded,
                            )
                            renew_task = self._start_renewal(run_id, owner, fencing_token)
                            graph_context = contextvars.copy_context()
                            graph_task = asyncio.create_task(
                                graph.ainvoke(graph_input, config=config, durability="sync"),
                                context=graph_context,
                            )
                            output = await graph_task
                            snapshot = await graph.aget_state(config, subgraphs=True)
                            if type(snapshot) is not StateSnapshot:
                                raise DurableExecutionStateError() from None
                            await saver._confirm_graph_state(snapshot)
                            result = self._resolve_graph_result(output=output, snapshot=snapshot, driver=driver)
                        finally:
                            if driver is not None:
                                self._requirement_contexts.pop(run_id, None)
                                self._requirement_results.pop(run_id, None)
                except BaseException:
                    if driver is not None:
                        self._poisoned.add(run_id)
                    raise
            return await self._settle(
                run_id=run_id, pause_generation=pause_generation, result=result,
                owner=owner, fencing_token=fencing_token,
            )
        finally:
            if renew_task is not None:
                renew_task.cancel()
                await asyncio.gather(renew_task, return_exceptions=True)
                self._renew_tasks.discard(renew_task)
            if saver is not None:
                await saver.aclose()
            if current_task is not None:
                self._operations.discard(current_task)

    def _compile(self, saver):
        compiler = getattr(self._graph_template, "compile", None)
        if callable(compiler):
            try:
                return compiler(checkpointer=saver)
            except Exception:
                raise DurableExecutionConfigurationError() from None
        if callable(getattr(self._graph_template, "ainvoke", None)):
            return self._graph_template
        raise DurableExecutionConfigurationError() from None

    @staticmethod
    def _config(run_id: UUID) -> dict[str, object]:
        return {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}, "recursion_limit": 256}

    def _snapshot_state(self, snapshot: StateSnapshot, output: object, driver=None) -> tuple[TravelSessionState, object | None]:
        for task in snapshot.tasks:
            if driver is not None and getattr(task, "name", None) not in {None, "requirements"}:
                continue
            child = getattr(task, "state", None)
            if child is not None:
                try:
                    return _fresh_state(child.values), (task.interrupts[0].value if task.interrupts else None)
                except DurableExecutionStateError:
                    pass
        return _fresh_state(output if output is not None else snapshot.values), None

    def _resolve_graph_result(self, *, output, snapshot, driver):
        state, interrupt = self._snapshot_state(snapshot, output, driver=driver)
        child_evidence = any(
            (
                getattr(task, "name", None) in {None, "requirements"}
                and (
                    getattr(task, "state", None) is not None
                    or getattr(task, "interrupts", ())
                )
            )
            for task in snapshot.tasks
        )
        if driver is not None and child_evidence:
            resolved = driver.resolve_result(state=state, interrupt_value=interrupt)
            if type(resolved) is RequirementSubgraphPaused:
                return resolved
        elif driver is not None:
            child_result = self._requirement_results.get(state.run_id)
            if child_result is not None:
                child_state = _fresh_state(child_result)
                resolved = driver.resolve_result(state=child_state, interrupt_value=None)
                if type(resolved) is RequirementSubgraphPaused:
                    return resolved
        if state.delivery_status.value == "awaiting_user":
            return ("native", state, secrets.token_hex(16))
        if state.delivery_status.value not in {"in_progress", "awaiting_user"}:
            return DurableExecutionTerminal(state=state)
        raise DurableExecutionStateError() from None

    async def _settle(
        self,
        *,
        run_id,
        pause_generation,
        result,
        owner,
        fencing_token,
        pending_repaired=False,
    ) -> DurableExecutionResult:
        row = await self._repository.get_run_row(run_id=run_id)
        pointer = _pointer(row)
        if pointer is None:
            raise DurableExecutionStateError() from None
        if type(result) is RequirementSubgraphPaused:
            await self._confirm_managed_operations(
                run_id=run_id, state=result.state, pointer=pointer,
                owner=owner, fencing_token=fencing_token,
            )
            context_bytes = _canonical_bytes(result.durable_pause)
            next_generation = await self._repository.advance_pause_generation(run_id=run_id, owner=owner, fencing_token=fencing_token)
            if not pending_repaired:
                await self._mark_completed(run_id, pause_generation, owner, fencing_token)
            await self._repository.create_pending_command(
                run_id=run_id, pause_generation=next_generation, command_kind="resume", node_name="requirements",
                owner=owner, fencing_token=fencing_token, pause_token_digest=result.durable_pause.pause_token_digest,
                pause_context_type="requirement_subgraph.pause", pause_context_version="1.0",
                pause_context_bytes=context_bytes, pause_context_digest=_digest(context_bytes), pre_checkpoint=pointer,
            )
            await self._repository.release_lease(run_id=run_id, owner=owner, fencing_token=fencing_token)
            return DurableExecutionPaused(
                state=result.state,
                pause=DurableExecutionPauseEnvelope(pause_token=result.pause.pause_token),
            )
        if isinstance(result, tuple) and len(result) == 3 and result[0] == "native":
            _, state, token = result
            await self._confirm_managed_operations(
                run_id=run_id, state=state, pointer=pointer,
                owner=owner, fencing_token=fencing_token,
            )
            next_generation = await self._repository.advance_pause_generation(run_id=run_id, owner=owner, fencing_token=fencing_token)
            context = {
                "schema_version": "1.0", "run_id": str(run_id), "trace_id": str(state.trace_id),
                "state_digest": _digest(_canonical_bytes(state)), "node_name": "__root__",
                "pause_generation": next_generation, "pre_checkpoint": pointer,
            }
            context_bytes = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if not pending_repaired:
                await self._mark_completed(run_id, pause_generation, owner, fencing_token)
            await self._repository.create_pending_command(
                run_id=run_id, pause_generation=next_generation, command_kind="resume", node_name="__root__",
                owner=owner, fencing_token=fencing_token, pause_token_digest=_native_token_digest(run_id, token),
                pause_context_type="durable_execution.root_pause", pause_context_version="1.0",
                pause_context_bytes=context_bytes, pause_context_digest=_digest(context_bytes), pre_checkpoint=pointer,
            )
            await self._repository.release_lease(run_id=run_id, owner=owner, fencing_token=fencing_token)
            return DurableExecutionPaused(state=state, pause=DurableExecutionPauseEnvelope(pause_token=token))
        if type(result) is not DurableExecutionTerminal:
            raise DurableExecutionStateError() from None
        await self._reconcile_state_artifacts(result.state)
        await self._confirm_managed_operations(
            run_id=run_id, state=result.state, pointer=pointer,
            owner=owner, fencing_token=fencing_token,
        )
        if not pending_repaired:
            await self._mark_completed(run_id, pause_generation, owner, fencing_token)
        await self._repository.close_run(run_id=run_id, owner=owner, fencing_token=fencing_token, pointer=pointer)
        return result

    async def _mark_completed(self, run_id, pause_generation, owner, fencing_token) -> None:
        await self._repository.transition_pending_command(
            run_id=run_id, pause_generation=pause_generation, expected_status="dispatch_started",
            new_status="checkpointed", owner=owner, fencing_token=fencing_token,
        )
        await self._repository.transition_pending_command(
            run_id=run_id, pause_generation=pause_generation, expected_status="checkpointed",
            new_status="cleared", owner=owner, fencing_token=fencing_token,
        )

    async def _read_graph_state(self, *, run_id, owner, fencing_token):
        saver = await create_fenced_checkpoint_saver(
            settings=self._settings, repository=self._repository, run_id=run_id,
            owner=owner, fencing_token=fencing_token, limits=self._limits,
            registered_node_names=self._registered_node_names(),
            checkpoint_pool=self._checkpoint_pool,
        )
        try:
            graph = self._compile(saver)
            snapshot = await graph.aget_state(self._config(run_id), subgraphs=True)
            if type(snapshot) is not StateSnapshot:
                raise DurableExecutionStateError() from None
            state, _ = self._snapshot_state(snapshot, snapshot.values)
            return state, snapshot
        finally:
            await saver.aclose()

    async def _read_verified_graph_state(self, *, run_id: UUID):
        """只读读取 checkpoint state，并验证 bounded lineage 的真实 pointer。"""

        saver = await create_fenced_checkpoint_saver(
            settings=self._settings,
            repository=self._repository,
            run_id=run_id,
            owner=uuid4(),
            fencing_token=1,
            limits=self._limits,
            registered_node_names=self._registered_node_names(),
            checkpoint_pool=self._checkpoint_pool,
        )
        try:
            graph = self._compile(saver)
            snapshot = await graph.aget_state(self._config(run_id), subgraphs=True)
            if type(snapshot) is not StateSnapshot:
                raise DurableExecutionStateError() from None
            state, _ = self._snapshot_state(snapshot, snapshot.values)
            pointer = await saver._verified_snapshot_pointer(snapshot)
            return state, snapshot, pointer
        finally:
            await saver.aclose()

    @staticmethod
    def _snapshot_next_node(snapshot: StateSnapshot) -> str:
        """只接受唯一真实 pending node，并递归识别嵌入 Graph 的 qualified name。"""

        def descend(current: StateSnapshot, prefix: str) -> str:
            if type(current) is not StateSnapshot or len(current.next) != 1:
                raise DurableExecutionStateError() from None
            name = current.next[0]
            if type(name) is not str or not name or "/" in name or "|" in name or ":" in name:
                raise DurableExecutionStateError() from None
            qualified = name if not prefix else f"{prefix}/{name}"
            nested = [
                task.state
                for task in current.tasks
                if getattr(task, "name", None) == name
                and type(getattr(task, "state", None)) is StateSnapshot
            ]
            if len(nested) > 1:
                raise DurableExecutionStateError() from None
            if nested:
                return descend(nested[0], qualified)
            return qualified

        return descend(snapshot, "")

    def _snapshot_completed_nodes(self, snapshot: StateSnapshot) -> frozenset[str]:
        """从已验证 checkpoint metadata 提取本次产生 checkpoint 的注册节点名。"""

        registered = set(self._registered_node_names())
        completed: set[str] = set()

        def visit(current: StateSnapshot, prefix: str) -> None:
            writes = current.metadata.get("writes") if isinstance(current.metadata, Mapping) else None
            if isinstance(writes, Mapping):
                for name in writes:
                    if type(name) is not str:
                        continue
                    qualified = name if not prefix else f"{prefix}/{name}"
                    if qualified in registered:
                        completed.add(qualified)
                    elif name in registered:
                        completed.add(name)
            for task in current.tasks:
                child = getattr(task, "state", None)
                if type(child) is StateSnapshot:
                    task_name = getattr(task, "name", None)
                    if type(task_name) is str:
                        child_prefix = task_name if not prefix else f"{prefix}/{task_name}"
                        visit(child, child_prefix)

        visit(snapshot, "")
        return frozenset(completed)

    def _start_renewal(self, run_id, owner, fencing_token):
        async def renew():
            try:
                while True:
                    await asyncio.sleep(self._limits.lease_renew_interval_seconds)
                    await self._repository.renew_lease(run_id=run_id, owner=owner, fencing_token=fencing_token)
            except asyncio.CancelledError:
                raise
            except Exception:
                return
        task = asyncio.create_task(renew())
        self._renew_tasks.add(task)
        return task

    def _is_contract_node(self, node_name: object) -> bool:
        return type(node_name) is str and any(
            rule.node_name == node_name for rule in self._contract.node_rules
        )

    def _registered_node_names(self) -> tuple[str, ...]:
        names = {rule.node_name for rule in self._contract.node_rules}
        if "requirements" in self._capabilities.drivers:
            names.update(
                {
                    "requirements/process_initial_turn",
                    "requirements/await_clarification",
                    "requirements/await_confirmation",
                }
            )
        return tuple(sorted(names))

    async def _safe_release(self, run_id, owner, fencing_token):
        try:
            await self._repository.release_lease(run_id=run_id, owner=owner, fencing_token=fencing_token)
        except Exception:
            pass

    async def _block(self, run_id, cause, *, recovery_guard=None):
        kwargs = {"run_id": run_id, "facts": _BlockFacts(cause=cause)}
        if recovery_guard is not None:
            kwargs["recovery_guard"] = recovery_guard
        await self._repository.block_run(**kwargs)

    async def _recover_dispatch_started(self, row, pending) -> DurableExecutionResult:
        run_id = row["run_id"]
        recovery_guard = (
            _pointer(row),
            int(pending["pause_generation"]),
            "dispatch_started",
        )
        try:
            state, snapshot, actual_pointer = await self._read_verified_graph_state(run_id=run_id)
        except (DurableExecutionRunBusyError, DurableExecutionRunBlockedError,
                DurableExecutionDependencyError,
                DurableExecutionConfigurationError, DurableExecutionIncompatibleRunError):
            raise
        except Exception:
            await self._block(run_id, _BlockCause.checkpoint_lineage_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        if state.delivery_status.value not in {"in_progress", "awaiting_user"}:
            try:
                return await self._repair_terminal(run_id, state, actual_pointer)
            except (DurableExecutionRunBusyError, DurableExecutionRunBlockedError,
                    DurableExecutionDependencyError,
                    DurableExecutionConfigurationError, DurableExecutionIncompatibleRunError):
                raise
            except Exception:
                await self._block(run_id, _BlockCause.artifact_invalid(), recovery_guard=recovery_guard)
                raise DurableExecutionStateError() from None
        if state.delivery_status.value == "awaiting_user" and not (
            pending["command_kind"] == "resume"
            and int(pending["pause_generation"]) > 0
            and actual_pointer == _pending_pointer(pending)
        ):
            await self._block(run_id, _BlockCause.pending_command_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None

        stored_pointer = _pointer(row)
        pre_pointer = _pending_pointer(pending)
        if stored_pointer not in {pre_pointer, actual_pointer}:
            await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        try:
            actual_node = self._snapshot_next_node(snapshot)
        except DurableExecutionStateError:
            await self._block(run_id, _BlockCause.node_classification_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        source = snapshot.metadata.get("source") if isinstance(snapshot.metadata, Mapping) else None
        crossed_node = actual_pointer != pre_pointer and source == "loop"
        pending_node = str(pending["node_name"])
        expected_root = "requirements" if pending_node == "__start__" else pending_node
        actual_root = actual_node.split("/", 1)[0]
        if not crossed_node and pending_node not in {"__root__", "__start__"} and actual_root != expected_root:
            await self._block(run_id, _BlockCause.node_classification_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None

        await self._verify_recovery_budget_activations(run_id, recovery_guard=recovery_guard)
        try:
            decoded = self._decode_dispatch_command(row=row, pending=pending)
        except (DurableExecutionRunBusyError, DurableExecutionRunBlockedError,
                DurableExecutionDependencyError,
                DurableExecutionConfigurationError, DurableExecutionIncompatibleRunError):
            raise
        except Exception:
            await self._block(run_id, _BlockCause.pending_command_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        evidence = await self._repository.get_recovery_operation_evidence(run_id=run_id)
        if crossed_node:
            try:
                await self._reconcile_state_artifacts(state)
            except DurableExecutionDependencyError:
                raise
            except Exception:
                await self._block(run_id, _BlockCause.artifact_invalid(), recovery_guard=recovery_guard)
                raise DurableExecutionStateError() from None
            try:
                journal_repairs = await self._checkpoint_journal_repairs(
                    run_id=run_id,
                    state=state,
                    snapshot=snapshot,
                    evidence=evidence,
                    checkpoint_pointer=actual_pointer,
                    recovery_guard=recovery_guard,
                )
            except (DurableExecutionRunBusyError, DurableExecutionRunBlockedError,
                    DurableExecutionDependencyError,
                    DurableExecutionConfigurationError, DurableExecutionIncompatibleRunError,
                    DurableExecutionStateError, OperationResultIndeterminateError):
                raise
            except Exception:
                await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
                raise DurableExecutionStateError() from None
            try:
                await self._repository.repair_dispatch_checkpoint(
                    run_id=run_id,
                    pause_generation=int(pending["pause_generation"]),
                    expected_stored_pointer=stored_pointer,
                    pointer=actual_pointer,
                    journal_repairs=journal_repairs,
                )
            except (DurableExecutionRunBusyError, DurableExecutionRunBlockedError,
                    DurableExecutionDependencyError, DurableExecutionConfigurationError,
                    DurableExecutionIncompatibleRunError):
                raise
            except Exception:
                await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
                raise DurableExecutionStateError() from None
            recovery_guard = (actual_pointer, int(pending["pause_generation"]), "cleared")
            row = await self._repository.get_run_row(run_id=run_id)
            pending = await self._repository.get_pending_command(run_id=run_id)
            if pending is None or pending["status"] != "cleared":
                await self._block(run_id, _BlockCause.pending_command_invalid(), recovery_guard=recovery_guard)
                raise DurableExecutionStateError() from None
            try:
                state, snapshot, rechecked_pointer = await self._read_verified_graph_state(run_id=run_id)
                if rechecked_pointer != actual_pointer:
                    raise DurableExecutionStateError() from None
                actual_node = self._snapshot_next_node(snapshot)
            except (DurableExecutionRunBusyError, DurableExecutionRunBlockedError,
                    DurableExecutionDependencyError,
                    DurableExecutionConfigurationError, DurableExecutionIncompatibleRunError):
                raise
            except Exception:
                await self._block(run_id, _BlockCause.checkpoint_lineage_invalid(), recovery_guard=recovery_guard)
                raise DurableExecutionStateError() from None
            evidence = await self._repository.get_recovery_operation_evidence(run_id=run_id)
            await self._check_stored_contract(run_id)
            await self._verify_recovery_budget_activations(run_id, recovery_guard=recovery_guard)

        related = await self._validate_recovery_node(
            run_id=run_id,
            node_name=actual_node,
            state=state,
            evidence=evidence,
            recovery_guard=recovery_guard,
        )
        if self._capabilities.drivers and actual_node.split("/", 1)[0] == "requirements":
            indeterminate = await self._block_dispatch_for_node(
                run_id=run_id,
                node_name=actual_node,
                recovery_class=NodeRecoveryClass.NON_RECOVERABLE,
                evidence=related,
                recovery_guard=recovery_guard,
            )
            if indeterminate:
                raise OperationResultIndeterminateError() from None
            raise DurableExecutionStateError() from None
        owner = self._owner
        expected_pending_status = "cleared" if crossed_node else "dispatch_started"
        try:
            token = await self._repository.acquire_recovery_lease(
                run_id=run_id,
                owner=owner,
                expected_pointer=actual_pointer,
                pause_generation=int(pending["pause_generation"]),
                expected_pending_status=expected_pending_status,
            )
        except DurableExecutionRunBusyError:
            raise
        try:
            return await self._execute_recovered_graph(
                run_id=run_id,
                pause_generation=int(pending["pause_generation"]),
                state=state,
                command=decoded,
                node_name=actual_node,
                owner=owner,
                fencing_token=token,
                pending_repaired=crossed_node,
            )
        except BaseException:
            await self._safe_release(run_id, owner, token)
            raise

    def _decode_dispatch_command(self, *, row, pending):
        self._check_codec()
        data = pending["command_bytes"]
        if type(data) is not bytes:
            raise DurableExecutionStateError() from None
        encoded = _fresh_encoded(DurableExecutionEncodedCommand(
            command_kind=str(pending["command_kind"]),
            type_id=str(pending["command_type_id"]),
            type_version=str(pending["command_type_version"]),
            canonical_bytes=data,
            digest=str(pending["command_digest"]),
            size_bytes=int(pending["command_size_bytes"]),
        ))
        generation = int(pending["pause_generation"])
        if generation == 0:
            if pending["command_kind"] != "initial" or pending["node_name"] != "__start__":
                raise DurableExecutionStateError() from None
            decoded = self._command_codec.decode_initial(
                run_id=row["run_id"], trace_id=row["trace_id"], encoded=encoded,
            )
            if _fresh_encoded(self._command_codec.encode_initial(
                run_id=row["run_id"], trace_id=row["trace_id"], command=decoded,
            )) != encoded:
                raise DurableExecutionStateError() from None
            return decoded
        node_name = str(pending["node_name"])
        if pending["command_kind"] != "resume" or pending["pause_context_bytes"] is None:
            raise DurableExecutionStateError() from None
        decoded = self._command_codec.decode_resume(
            run_id=row["run_id"], pause_generation=generation,
            node_name=node_name, encoded=encoded,
        )
        if _fresh_encoded(self._command_codec.encode_resume(
            run_id=row["run_id"], pause_generation=generation,
            node_name=node_name, command=decoded,
        )) != encoded:
            raise DurableExecutionStateError() from None
        return decoded

    @staticmethod
    def _related_recovery_rows(node_name: str, evidence):
        return tuple(
            row for row in evidence
            if type(row.get("node_name")) is str
            and (
                row["node_name"] == node_name
                or node_name.startswith(str(row["node_name"]) + "/")
            )
        )

    @staticmethod
    def _evidence_operation_id(run_id: UUID, row: Mapping[str, object]) -> OperationId:
        try:
            return OperationId(
                run_id=run_id,
                phase=WorkflowPhase(str(row["operation_phase"])),
                logical_step=str(row["operation_logical_step"]),
            )
        except Exception:
            raise DurableExecutionStateError() from None

    async def _lookup_recovery_artifact(
        self,
        *,
        run_id: UUID,
        row: Mapping[str, object],
        expected_ref: ArtifactRef | None = None,
    ) -> ArtifactRef | None:
        operation_id = self._evidence_operation_id(run_id, row)
        try:
            outcome = await self._artifact_repository.lookup(operation_id=operation_id)
        except ArtifactRepositoryDependencyError:
            raise DurableExecutionDependencyError() from None
        if isinstance(outcome, ArtifactAbsent):
            if outcome.operation_id != operation_id:
                raise DurableExecutionStateError() from None
            return None
        if not isinstance(outcome, ArtifactFound):
            raise DurableExecutionStateError() from None
        ref = _fresh_model(ArtifactRef, outcome.ref, DurableExecutionStateError)
        if ref.operation_id != operation_id:
            raise DurableExecutionStateError() from None
        if expected_ref is not None and ref != expected_ref:
            raise DurableExecutionStateError() from None
        if row.get("artifact_id") is not None and (
            ref.artifact_id != row.get("artifact_id")
            or ref.artifact_type != row.get("artifact_type")
            or ref.schema_version != row.get("artifact_schema_version")
            or ref.content_hash != row.get("artifact_content_hash")
            or ref.created_at != row.get("artifact_created_at")
            or ref.size_bytes != row.get("artifact_size_bytes")
        ):
            raise DurableExecutionStateError() from None
        return ref

    async def _validate_recovery_node(self, *, run_id, node_name, state, evidence, recovery_guard=None):
        if any(row.get("node_name") is None or row.get("journal_state") is None for row in evidence):
            await self._block(run_id, _BlockCause.journal_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        rule = next((item for item in self._contract.node_rules if item.node_name == node_name), None)
        if rule is None:
            await self._block(run_id, _BlockCause.node_classification_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        related = self._related_recovery_rows(node_name, evidence)
        if rule.recovery_class is NodeRecoveryClass.NON_RECOVERABLE:
            indeterminate = await self._block_dispatch_for_node(
                run_id=run_id, node_name=node_name,
                recovery_class=rule.recovery_class, evidence=related,
                recovery_guard=recovery_guard,
            )
            if indeterminate:
                raise OperationResultIndeterminateError() from None
            raise DurableExecutionStateError() from None
        if rule.recovery_class is NodeRecoveryClass.REPLAY_SAFE:
            if related:
                await self._block(run_id, _BlockCause.node_classification_invalid(), recovery_guard=recovery_guard)
                raise DurableExecutionStateError() from None
            return related

        if len(related) > 1:
            await self._block(run_id, _BlockCause.node_classification_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        in_flight = tuple(
            row for row in related
            if row.get("journal_state") in {
                "execution_permitted", "artifact_confirmed", "checkpoint_confirmed",
                "completed", "budget_rejected", "indeterminate",
            }
        )
        if len(in_flight) > 1:
            await self._block(run_id, _BlockCause.node_classification_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        if not in_flight:
            return related
        candidate = in_flight[0]
        latest = str(candidate["journal_state"])
        if latest == "indeterminate" or latest == "budget_rejected":
            await self._block(run_id, _BlockCause.journal_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        if latest in {"artifact_confirmed", "checkpoint_confirmed", "completed"} and candidate.get("artifact_id") is None:
            await self._block(run_id, _BlockCause.journal_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        try:
            state_refs = {
                ref.operation_id: ref
                for ref in (_fresh_model(ArtifactRef, item, DurableExecutionStateError) for item in state.artifact_refs)
            }
            expected_ref = state_refs.get(self._evidence_operation_id(run_id, candidate))
            artifact = await self._lookup_recovery_artifact(
                run_id=run_id, row=candidate, expected_ref=expected_ref,
            )
        except DurableExecutionDependencyError:
            raise
        except Exception:
            await self._block(run_id, _BlockCause.artifact_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        if artifact is None:
            if latest == "execution_permitted":
                indeterminate = await self._block_dispatch_for_node(
                    run_id=run_id, node_name=node_name,
                    recovery_class=rule.recovery_class,
                    evidence=related, artifact_missing=True,
                    recovery_guard=recovery_guard,
                )
                if indeterminate:
                    raise OperationResultIndeterminateError() from None
                raise DurableExecutionStateError() from None
            else:
                await self._block(run_id, _BlockCause.artifact_invalid(), recovery_guard=recovery_guard)
                raise DurableExecutionStateError() from None
        return related

    async def _block_dispatch_for_node(
        self,
        *,
        run_id: UUID,
        node_name: str,
        recovery_class: NodeRecoveryClass,
        evidence,
        artifact_missing: bool = False,
        recovery_guard=None,
    ) -> bool:
        related = tuple(
            row for row in evidence
            if row.get("journal_state") == "execution_permitted"
        )
        all_permitted = await self._repository.get_recovery_operation_evidence(run_id=run_id)
        all_permitted = tuple(
            row for row in all_permitted if row.get("journal_state") == "execution_permitted"
        )
        if len(related) == 1 and len(all_permitted) == 1:
            try:
                operation_id = self._evidence_operation_id(run_id, related[0])
            except DurableExecutionStateError:
                await self._block(run_id, _BlockCause.journal_invalid(), recovery_guard=recovery_guard)
                raise
            if artifact_missing:
                kwargs = {
                    "run_id": run_id,
                    "facts": _BlockFacts(
                        artifact_missing_after_permission=True,
                        operation_id=operation_id,
                    ),
                }
                if recovery_guard is not None:
                    kwargs["recovery_guard"] = recovery_guard
                await self._repository.block_run(**kwargs)
                return True
            kwargs = {
                "run_id": run_id,
                "facts": _BlockFacts(
                    external_result_unknown=True,
                    cause=_BlockCause.operation_indeterminate(),
                    operation_id=operation_id,
                ),
            }
            if recovery_guard is not None:
                kwargs["recovery_guard"] = recovery_guard
            await self._repository.block_run(**kwargs)
            return True
        if recovery_class is NodeRecoveryClass.NON_RECOVERABLE:
            cause = _BlockCause.non_recoverable_dispatch()
        elif recovery_class is NodeRecoveryClass.JOURNAL_GUARDED:
            cause = _BlockCause.journal_invalid()
        else:
            cause = _BlockCause.node_classification_invalid()
        await self._block(run_id, cause, recovery_guard=recovery_guard)
        return recovery_class is NodeRecoveryClass.NON_RECOVERABLE

    async def _checkpoint_journal_repairs(
        self,
        *,
        run_id,
        state,
        snapshot,
        evidence,
        checkpoint_pointer,
        recovery_guard=None,
    ):
        try:
            completed_nodes = self._snapshot_completed_nodes(snapshot)
            related_rows = tuple(
                row for row in evidence
                if type(row.get("node_name")) is str
                and any(
                    row["node_name"] == name or name.startswith(str(row["node_name"]) + "/")
                    for name in completed_nodes
                )
                and row.get("journal_state") in {
                    "execution_permitted", "artifact_confirmed", "budget_rejected", "checkpoint_confirmed",
                }
            )
            state_refs = {
                ref.operation_id: ref
                for ref in (_fresh_model(ArtifactRef, item, DurableExecutionStateError) for item in state.artifact_refs)
            }
        except Exception:
            await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
            raise DurableExecutionStateError() from None
        repairs = []
        for row in related_rows:
            node_name = str(row["node_name"])
            rule = next((item for item in self._contract.node_rules if item.node_name == node_name), None)
            if node_name == "requirements" and "requirements" in self._capabilities.drivers and any(
                item.node_name.startswith("requirements/")
                and item.recovery_class is NodeRecoveryClass.NON_RECOVERABLE
                for item in self._contract.node_rules
            ):
                rule = next(
                    item for item in self._contract.node_rules
                    if item.node_name.startswith("requirements/")
                    and item.recovery_class is NodeRecoveryClass.NON_RECOVERABLE
                )
            if rule is None or rule.recovery_class is NodeRecoveryClass.REPLAY_SAFE:
                await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
                raise DurableExecutionStateError() from None
            latest = str(row["journal_state"])
            artifact = None
            transitions = []
            if latest == "execution_permitted" and rule.recovery_class is NodeRecoveryClass.JOURNAL_GUARDED:
                try:
                    operation_id = self._evidence_operation_id(run_id, row)
                except DurableExecutionStateError:
                    await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
                    raise
                expected_ref = state_refs.get(operation_id)
                try:
                    artifact_ref = await self._lookup_recovery_artifact(
                        run_id=run_id, row=row, expected_ref=expected_ref,
                    )
                except DurableExecutionDependencyError:
                    raise
                except Exception:
                    await self._block(run_id, _BlockCause.artifact_invalid(), recovery_guard=recovery_guard)
                    raise DurableExecutionStateError() from None
                if artifact_ref is None:
                    indeterminate = await self._block_dispatch_for_node(
                        run_id=run_id, node_name=node_name,
                        recovery_class=rule.recovery_class,
                        evidence=(row,), artifact_missing=True,
                        recovery_guard=recovery_guard,
                    )
                    if indeterminate:
                        raise OperationResultIndeterminateError() from None
                    raise DurableExecutionStateError() from None
                artifact = {
                    "artifact_id": artifact_ref.artifact_id,
                    "artifact_type": artifact_ref.artifact_type,
                    "artifact_schema_version": artifact_ref.schema_version,
                    "artifact_content_hash": artifact_ref.content_hash,
                    "artifact_created_at": artifact_ref.created_at,
                    "artifact_size_bytes": artifact_ref.size_bytes,
                }
                transitions.append({"journal_state": "artifact_confirmed", "artifact": artifact})
            elif latest == "execution_permitted" and rule.recovery_class is NodeRecoveryClass.NON_RECOVERABLE:
                # 真实 post-node checkpoint 证明 node 已结束；其业务 Artifact 仍只属于
                # checkpoint state，不得复制进 non-recoverable 的 journal 字段。
                artifact = None
            elif latest == "artifact_confirmed":
                try:
                    operation_id = self._evidence_operation_id(run_id, row)
                except DurableExecutionStateError:
                    await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
                    raise
                expected_ref = state_refs.get(operation_id)
                try:
                    artifact_ref = await self._lookup_recovery_artifact(
                        run_id=run_id, row=row, expected_ref=expected_ref,
                    )
                except DurableExecutionDependencyError:
                    raise
                except Exception:
                    await self._block(run_id, _BlockCause.artifact_invalid(), recovery_guard=recovery_guard)
                    raise DurableExecutionStateError() from None
                if artifact_ref is None:
                    await self._block(run_id, _BlockCause.artifact_invalid(), recovery_guard=recovery_guard)
                    raise DurableExecutionStateError() from None
                artifact = {
                    "artifact_id": artifact_ref.artifact_id,
                    "artifact_type": artifact_ref.artifact_type,
                    "artifact_schema_version": artifact_ref.schema_version,
                    "artifact_content_hash": artifact_ref.content_hash,
                    "artifact_created_at": artifact_ref.created_at,
                    "artifact_size_bytes": artifact_ref.size_bytes,
                }
            elif latest == "checkpoint_confirmed":
                confirmed_pointer = (
                    str(row.get("checkpoint_ns")),
                    str(row.get("checkpoint_id")),
                    str(row.get("checkpoint_digest")),
                )
                if confirmed_pointer != checkpoint_pointer:
                    await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
                    raise DurableExecutionStateError() from None
                if row.get("artifact_id") is not None:
                    if rule.recovery_class is NodeRecoveryClass.NON_RECOVERABLE:
                        await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
                        raise DurableExecutionStateError() from None
                    try:
                        operation_id = self._evidence_operation_id(run_id, row)
                    except DurableExecutionStateError:
                        await self._block(run_id, _BlockCause.checkpoint_invalid(), recovery_guard=recovery_guard)
                        raise
                    expected_ref = state_refs.get(operation_id)
                    try:
                        artifact_ref = await self._lookup_recovery_artifact(
                            run_id=run_id, row=row, expected_ref=expected_ref,
                        )
                    except DurableExecutionDependencyError:
                        raise
                    except Exception:
                        await self._block(run_id, _BlockCause.artifact_invalid(), recovery_guard=recovery_guard)
                        raise DurableExecutionStateError() from None
                    if artifact_ref is None:
                        await self._block(run_id, _BlockCause.artifact_invalid(), recovery_guard=recovery_guard)
                        raise DurableExecutionStateError() from None
                    artifact = {
                        "artifact_id": artifact_ref.artifact_id,
                        "artifact_type": artifact_ref.artifact_type,
                        "artifact_schema_version": artifact_ref.schema_version,
                        "artifact_content_hash": artifact_ref.content_hash,
                        "artifact_created_at": artifact_ref.created_at,
                        "artifact_size_bytes": artifact_ref.size_bytes,
                    }
                transitions.append({"journal_state": "completed", "artifact": artifact})
            else:
                transitions.extend((
                    {"journal_state": "checkpoint_confirmed", "artifact": artifact},
                    {"journal_state": "completed", "artifact": artifact},
                ))
            repairs.append({
                "operation_phase": row["operation_phase"],
                "operation_logical_step": row["operation_logical_step"],
                "node_name": node_name,
                "expected_state": latest,
                "transitions": tuple(transitions),
            })
        return tuple(repairs)

    async def _execute_recovered_graph(
        self,
        *,
        run_id,
        pause_generation,
        state,
        command,
        node_name,
        owner,
        fencing_token,
        pending_repaired,
    ):
        current_task = asyncio.current_task()
        if current_task is not None:
            self._operations.add(current_task)
        saver = None
        renew_task = None
        try:
            saver = await create_fenced_checkpoint_saver(
                settings=self._settings, repository=self._repository, run_id=run_id,
                owner=owner, fencing_token=fencing_token, limits=self._limits,
                registered_node_names=self._registered_node_names(),
                checkpoint_pool=self._checkpoint_pool,
            )
            graph = self._compile(saver)
            config = self._config(run_id)
            pointer = _pointer(await self._repository.get_run_row(run_id=run_id))
            if pointer is None:
                raise DurableExecutionStateError() from None
            with self._capabilities.bind(
                run_id=run_id, owner=owner, fencing_token=fencing_token,
                command=command, node_name=node_name, checkpoint=pointer,
                checkpoint_accessor=saver,
            ):
                if self._is_contract_node(node_name):
                    await self._capabilities.ensure_phase_budget(
                        run_id=run_id, owner=owner, fencing_token=fencing_token,
                        node_name=node_name, state=state, checkpoint_accessor=saver,
                    )
                renew_task = self._start_renewal(run_id, owner, fencing_token)
                output = await graph.ainvoke(None, config=config, durability="sync")
                snapshot = await graph.aget_state(config, subgraphs=True)
                if type(snapshot) is not StateSnapshot:
                    raise DurableExecutionStateError() from None
                await saver._confirm_graph_state(snapshot)
                result = self._resolve_graph_result(output=output, snapshot=snapshot, driver=None)
            return await self._settle(
                run_id=run_id, pause_generation=pause_generation, result=result,
                owner=owner, fencing_token=fencing_token,
                pending_repaired=pending_repaired,
            )
        finally:
            if renew_task is not None:
                renew_task.cancel()
                await asyncio.gather(renew_task, return_exceptions=True)
                self._renew_tasks.discard(renew_task)
            if saver is not None:
                await saver.aclose()
            if current_task is not None:
                self._operations.discard(current_task)

    async def _block_for_dispatch(self, run_id, pending):
        rule = next((item for item in self._contract.node_rules if item.node_name == pending["node_name"]), None)
        if rule is None or rule.recovery_class is NodeRecoveryClass.NON_RECOVERABLE:
            cause = _BlockCause.non_recoverable_dispatch()
        elif rule.recovery_class is NodeRecoveryClass.JOURNAL_GUARDED:
            cause = _BlockCause.journal_invalid()
        else:
            cause = _BlockCause.node_classification_invalid()
        await self._block(run_id, cause)

    async def _repair_terminal(self, run_id, state, pointer):
        await self._reconcile_state_artifacts(state)
        artifact_by_operation = {
            (ref.operation_id.phase.value, ref.operation_id.logical_step): {
                "artifact_id": ref.artifact_id,
                "artifact_type": ref.artifact_type,
                "artifact_schema_version": ref.schema_version,
                "artifact_content_hash": ref.content_hash,
                "artifact_created_at": ref.created_at,
                "artifact_size_bytes": ref.size_bytes,
            }
            for ref in state.artifact_refs
        }
        journal_nodes = tuple(
            rule.node_name
            for rule in self._contract.node_rules
            if rule.recovery_class is NodeRecoveryClass.JOURNAL_GUARDED
        )
        await self._repository.repair_terminal_close(
            run_id=run_id,
            pointer=pointer,
            artifact_by_operation=artifact_by_operation,
            artifact_journal_nodes=journal_nodes,
        )
        return DurableExecutionTerminal(state=state)

    async def _read_terminal_state(self, run_id):
        state, _ = await self._read_graph_state(run_id=run_id, owner=uuid4(), fencing_token=1)
        if state.delivery_status.value in {"in_progress", "awaiting_user"}:
            raise DurableExecutionStateError() from None
        return DurableExecutionTerminal(state=state)

    async def _reconcile_state_artifacts(self, state: TravelSessionState) -> None:
        """只按 checkpoint 中可达的 ArtifactRef 做 Module 10 对账。"""

        for ref in state.artifact_refs:
            fresh_ref = _fresh_model(ArtifactRef, ref, DurableExecutionStateError)
            try:
                outcome = await self._artifact_repository.lookup(
                    operation_id=fresh_ref.operation_id,
                )
            except ArtifactRepositoryDependencyError:
                raise DurableExecutionDependencyError() from None
            if type(outcome) is not ArtifactFound or outcome.ref != fresh_ref:
                raise DurableExecutionStateError() from None

    async def _confirm_managed_operations(
        self,
        *,
        run_id: UUID,
        state: TravelSessionState,
        pointer: tuple[str, str, str],
        owner: UUID,
        fencing_token: int,
    ) -> None:
        """把本次合法 checkpoint 反映的受管 operation 逐个闭合。

        预算 consume 与 Graph checkpoint 不属于同一个数据库事务；这里仅对
        已存在的 operation ledger 做追加式 checkpoint_confirmed/completed，
        不从 StateSnapshot 反向生成 operation，也不把旁侧 Artifact 当作许可。
        """

        fresh_state = _fresh_state(state)
        if fresh_state.run_id != run_id:
            raise DurableExecutionStateError() from None
        refs = {ref.operation_id: ref for ref in fresh_state.artifact_refs}
        rows = await self._repository.get_latest_operation_evidence(
            run_id=run_id, owner=owner, fencing_token=fencing_token,
        )
        for row in rows:
            operation_id = OperationId(
                run_id=run_id,
                phase=WorkflowPhase(str(row["operation_phase"])),
                logical_step=str(row["operation_logical_step"]),
            )
            if row["journal_state"] is None:
                raise DurableExecutionStateError() from None
            latest = str(row["journal_state"])
            if latest in {"completed", "indeterminate"}:
                if latest == "indeterminate":
                    raise DurableExecutionStateError() from None
                continue
            artifact = None
            if row["artifact_id"] is not None:
                ref = refs.get(operation_id)
                if ref is None:
                    raise DurableExecutionStateError() from None
                artifact = {
                    "artifact_id": ref.artifact_id,
                    "artifact_type": ref.artifact_type,
                    "artifact_schema_version": ref.schema_version,
                    "artifact_content_hash": ref.content_hash,
                    "artifact_created_at": ref.created_at,
                    "artifact_size_bytes": ref.size_bytes,
                }
                if (
                    row["artifact_id"], row["artifact_type"],
                    row["artifact_schema_version"], row["artifact_content_hash"],
                    row["artifact_created_at"], row["artifact_size_bytes"],
                ) != tuple(artifact.values()):
                    raise DurableExecutionStateError() from None
            elif operation_id in refs:
                # A non-recoverable operation may have a side Artifact only when
                # it was not journal-confirmed; never promote that side row.
                if latest not in {"execution_permitted", "budget_rejected"}:
                    raise DurableExecutionStateError() from None
            if latest == "artifact_confirmed":
                node_name = row["node_name"]
                rule = next(
                    (
                        item for item in self._contract.node_rules
                        if type(node_name) is str and item.node_name == node_name
                    ),
                    None,
                )
                if (
                    type(node_name) is not str
                    or rule is None
                    or rule.recovery_class is not NodeRecoveryClass.JOURNAL_GUARDED
                    or artifact is None
                ):
                    raise DurableExecutionStateError() from None
            await self._repository.append_journal_transition(
                run_id=run_id,
                operation_phase=operation_id.phase.value,
                logical_step=operation_id.logical_step,
                node_name=str(row["node_name"]),
                journal_state="checkpoint_confirmed",
                owner=owner,
                fencing_token=fencing_token,
                artifact=artifact,
                checkpoint=pointer,
            )
            await self._repository.append_journal_transition(
                run_id=run_id,
                operation_phase=operation_id.phase.value,
                logical_step=operation_id.logical_step,
                node_name=str(row["node_name"]),
                journal_state="completed",
                owner=owner,
                fencing_token=fencing_token,
                artifact=artifact,
                checkpoint=pointer,
            )

    async def _check_stored_contract(self, run_id):
        stored = await self._repository.get_run_contract(run_id=run_id)
        current = contract_canonical_bytes(self._contract)
        if (
            stored["contract_id"] != self._contract.contract_id
            or stored["contract_schema_version"] != self._contract.schema_version
            or stored["contract_digest"] != self._contract.contract_digest
            or stored["contract_bytes"] != current
            or int(stored["contract_size_bytes"]) != len(current)
        ):
            raise DurableExecutionIncompatibleRunError() from None

    async def _verify_budget_activations(self, run_id: UUID) -> None:
        """只读核对全部已激活 phase 与不可变数据库投影。"""

        plan = _fresh_model(
            ExecutionBudgetPlan,
            self._budget_plan_provider.plan_for_start(run_id=run_id),
            DurableExecutionStateError,
        )
        if plan.run_id != run_id or not plan.limits:
            raise DurableExecutionStateError() from None
        expected = {
            (
                entry.key.scope.value,
                entry.key.name,
                "" if entry.key.phase is None else entry.key.phase.value,
            ): int(entry.limit)
            for entry in plan.limits
        }
        rows = await self._repository.get_budget_activation_rows(run_id=run_id)
        ordinal_zero = tuple(row for row in rows if int(row["activation_ordinal"]) == 0)
        actual = {
            (str(row["scope"]), str(row["name"]), str(row["phase_code"])): int(row["limit_value"])
            for row in ordinal_zero
        }
        if (
            not ordinal_zero
            or len(actual) != len(ordinal_zero)
            or actual != expected
            or any(
                row["activation_digest"] != _plan_digest(plan)
                or row["activation_checkpoint_ns"] is not None
                or row["activation_checkpoint_id"] is not None
                or row["activation_checkpoint_digest"] is not None
                for row in ordinal_zero
            )
        ):
            raise DurableExecutionStateError() from None
        grouped: dict[tuple[int, str], list[Mapping[str, object]]] = {}
        for row in rows:
            ordinal = int(row["activation_ordinal"])
            if ordinal > 0:
                grouped.setdefault((ordinal, str(row["phase_code"])), []).append(row)
        if not grouped:
            return
        saver = await create_fenced_checkpoint_saver(
            settings=self._settings, repository=self._repository, run_id=run_id,
            owner=uuid4(), fencing_token=1, limits=self._limits,
            registered_node_names=self._registered_node_names(),
            checkpoint_pool=self._checkpoint_pool,
        )
        try:
            for (ordinal, phase_code), group in grouped.items():
                try:
                    phase = WorkflowPhase(phase_code)
                except ValueError:
                    raise DurableExecutionStateError() from None
                if _PHASE_ORDINAL[phase.value] != ordinal:
                    raise DurableExecutionStateError() from None
                checkpoint = _GraphCapabilities._activation_pointer(group[0])
                plan_state = await saver.activation_state(checkpoint)
                if plan_state.run_id != run_id or plan_state.phase is not phase:
                    raise DurableExecutionStateError() from None
                phase_plan = _fresh_model(
                    ExecutionBudgetPlan,
                    self._budget_plan_provider.plan_for_phase(
                        run_id=run_id, phase=phase, state=plan_state
                    ),
                    DurableExecutionStateError,
                )
                if phase_plan.run_id != run_id or not phase_plan.limits or any(
                    entry.key.scope is not ExecutionBudgetScope.PHASE
                    or entry.key.phase is not phase
                    for entry in phase_plan.limits
                ):
                    raise DurableExecutionStateError() from None
                expected_phase = {
                    (entry.key.scope.value, entry.key.name, entry.key.phase.value): int(entry.limit)
                    for entry in phase_plan.limits
                }
                actual_phase = {
                    (str(row["scope"]), str(row["name"]), str(row["phase_code"])): int(row["limit_value"])
                    for row in group
                }
                digest = _plan_digest(phase_plan)
                if len(actual_phase) != len(group) or actual_phase != expected_phase:
                    raise DurableExecutionStateError() from None
                for row in group:
                    if (
                        int(row["activation_ordinal"]) != ordinal
                        or row["activation_digest"] != digest
                        or _GraphCapabilities._activation_pointer(row) != checkpoint
                    ):
                        raise DurableExecutionStateError() from None
        finally:
            await saver.aclose()


def _coordination_pool_kwargs(settings: DurableExecutionSettings) -> dict[str, object]:
    return {
        "host": settings.host, "port": settings.port, "dbname": settings.database,
        "user": settings.user, "password": settings.password.get_secret_value(),
        "autocommit": False, "prepare_threshold": 0,
        "connect_timeout": 10,
        "options": "-c search_path=durable_execution,pg_catalog -c timezone=UTC -c synchronous_commit=off -c statement_timeout=5000 -c lock_timeout=2000",
    }


async def create_durable_execution_runtime(*, settings: DurableExecutionSettings,
                                           artifact_repository: ArtifactRepository,
                                           contract: DurableExecutionContract,
                                           command_codec: DurableExecutionCommandCodec,
                                           budget_plan_provider: DurableExecutionBudgetPlanProvider,
                                           graph_factory: Callable[[DurableExecutionGraphCapabilities], StateGraph],
                                           limits: DurableExecutionLimits = DEMO_DURABLE_EXECUTION_LIMITS) -> DurableExecutionRuntime:
    if type(settings) is not DurableExecutionSettings or type(contract) is not DurableExecutionContract:
        raise DurableExecutionUsageError() from None
    if type(limits) is not DurableExecutionLimits or not callable(graph_factory):
        raise DurableExecutionUsageError() from None
    if not callable(getattr(budget_plan_provider, "plan_for_start", None)) or not callable(getattr(budget_plan_provider, "plan_for_phase", None)):
        raise DurableExecutionConfigurationError() from None
    coordination_pool = AsyncConnectionPool("", kwargs=_coordination_pool_kwargs(settings), min_size=1, max_size=4, open=False, timeout=10.0)
    checkpoint_pool = AsyncConnectionPool("", kwargs=_checkpoint_pool_kwargs(settings), min_size=1, max_size=1, open=False, timeout=10.0)
    try:
        await coordination_pool.open(wait=True)
        async with coordination_pool.connection() as connection:
            await check_durable_execution_schema(connection)
        repository = DurableExecutionRepository(coordination_pool=coordination_pool, limits=limits)
        await repository.initialize_capacity()
        await checkpoint_pool.open(wait=True)
        capabilities = _GraphCapabilities(
            repository=repository, artifact_repository=artifact_repository,
            contract=contract, provider=budget_plan_provider, limits=limits,
        )
        graph_template = graph_factory(capabilities)
        if graph_template is None:
            raise DurableExecutionConfigurationError() from None
        nodes = getattr(graph_template, "nodes", None)
        requirement_contexts: dict[UUID, contextvars.Context] = {}
        requirement_results: dict[UUID, object] = {}
        if capabilities.drivers:
            if not isinstance(nodes, Mapping) or "requirements" not in nodes:
                raise DurableExecutionConfigurationError() from None
            _install_requirement_context_bridge(
                graph_template,
                capabilities.drivers["requirements"],
                requirement_contexts,
                requirement_results,
            )
        _install_root_node_task_contexts(graph_template, capabilities)
        return DurableExecutionRuntime(
            settings=settings, artifact_repository=artifact_repository, contract=contract,
            command_codec=command_codec, budget_plan_provider=budget_plan_provider,
            graph_factory=graph_factory, limits=limits,
            coordination_pool=coordination_pool, checkpoint_pool=checkpoint_pool,
            repository=repository, capabilities=capabilities, graph_template=graph_template,
            requirement_contexts=requirement_contexts,
            requirement_results=requirement_results,
        )
    except DurableExecutionError:
        await checkpoint_pool.close()
        await coordination_pool.close()
        raise
    except Exception:
        await checkpoint_pool.close()
        await coordination_pool.close()
        raise DurableExecutionConfigurationError() from None


__all__ = ["DurableExecutionRuntime", "create_durable_execution_runtime"]
