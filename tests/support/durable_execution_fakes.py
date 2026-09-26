"""Module 11 阶段测试使用的无副作用合同 fake。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

from langgraph.graph import StateGraph
from pydantic import BaseModel, TypeAdapter

from app.requirement_subgraph import RequirementClarificationResume
from app.requirement_subgraph import RequirementSubgraphEmbeddedDriver
from app.requirement_subgraph import RequirementSubgraphCoordinatorLimits
from app.requirements import RequirementTurnInput
from app.durable_execution.errors import DurableExecutionUsageError
from app.durable_execution.models import (
    DurableExecutionCommandCodecDescriptor,
    DurableExecutionContract,
    DurableExecutionEncodedCommand,
    DurableExecutionLimits,
    DurableExecutionNodeRecoveryRule,
    NodeRecoveryClass,
)
from app.durable_execution.settings import DurableExecutionSettings
from app.workflow_contracts import (
    ArtifactAbsent,
    ArtifactFound,
    BudgetConsumeOutcome,
    BudgetConsumeRequest,
    ArtifactRepository,
    ExecutionBudgetManager,
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    ExecutionBudgetSnapshot,
    TravelSessionState,
    WorkflowPhase,
)
from tests.requirement_subgraph_fakes import make_initial_state, make_turn


RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
TRACE_ID = UUID("22222222-2222-4222-8222-222222222222")


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def make_settings() -> DurableExecutionSettings:
    return DurableExecutionSettings(
        host="postgres",
        port=5432,
        database="durable_execution_test",
        user="durable_execution_test",
        password="test-only-secret",
    )


def make_contract() -> DurableExecutionContract:
    descriptor = DurableExecutionCommandCodecDescriptor(
        codec_id="test.codec",
        codec_version="1.0",
        codec_digest="sha256:" + "1" * 64,
    )
    rules = (
        DurableExecutionNodeRecoveryRule(
            node_name="requirements",
            phase=WorkflowPhase.REQUIREMENTS,
            recovery_class=NodeRecoveryClass.JOURNAL_GUARDED,
        ),
        DurableExecutionNodeRecoveryRule(
            node_name="requirements/城市",
            phase=WorkflowPhase.REQUIREMENTS,
            recovery_class=NodeRecoveryClass.REPLAY_SAFE,
        ),
    )
    payload = {
        "budgeted_phases": ["requirements", "hotel_selection"],
        "command_codec": descriptor.model_dump(mode="json"),
        "contract_id": "test.contract",
        "node_rules": [rule.model_dump(mode="json") for rule in rules],
        "schema_version": "1.0",
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return DurableExecutionContract(
        contract_id="test.contract",
        command_codec=descriptor,
        budgeted_phases=(WorkflowPhase.REQUIREMENTS, WorkflowPhase.HOTEL_SELECTION),
        node_rules=rules,
        contract_digest=_digest(canonical),
    )


def make_recovery_contract(
    *,
    nodes: tuple[tuple[str, NodeRecoveryClass], ...] = (
        ("safe", NodeRecoveryClass.REPLAY_SAFE),
        ("finish", NodeRecoveryClass.REPLAY_SAFE),
    ),
) -> DurableExecutionContract:
    descriptor = FakeCommandCodec.descriptor
    rules = tuple(sorted(
        (
            DurableExecutionNodeRecoveryRule(
                node_name=node_name,
                phase=WorkflowPhase.REQUIREMENTS,
                recovery_class=recovery_class,
            )
            for node_name, recovery_class in nodes
        ),
        key=lambda rule: rule.node_name,
    ))
    payload = {
        "budgeted_phases": ["requirements"],
        "command_codec": descriptor.model_dump(mode="json"),
        "contract_id": "test.recovery.contract",
        "node_rules": [rule.model_dump(mode="json") for rule in rules],
        "schema_version": "1.0",
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return DurableExecutionContract(
        contract_id="test.recovery.contract",
        command_codec=descriptor,
        budgeted_phases=(WorkflowPhase.REQUIREMENTS,),
        node_rules=rules,
        contract_digest=_digest(canonical),
    )


def make_requirement_recovery_contract() -> DurableExecutionContract:
    base = make_contract()
    rules = tuple(sorted(base.node_rules + tuple(
        DurableExecutionNodeRecoveryRule(
            node_name=node_name,
            phase=WorkflowPhase.REQUIREMENTS,
            recovery_class=NodeRecoveryClass.NON_RECOVERABLE,
        )
        for node_name in (
            "requirements/process_initial_turn",
            "requirements/await_clarification",
            "requirements/await_confirmation",
        )
    ), key=lambda rule: rule.node_name))
    payload = {
        "budgeted_phases": [phase.value for phase in base.budgeted_phases],
        "command_codec": base.command_codec.model_dump(mode="json"),
        "contract_id": base.contract_id,
        "node_rules": [rule.model_dump(mode="json") for rule in rules],
        "schema_version": base.schema_version,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return DurableExecutionContract(
        contract_id=base.contract_id,
        schema_version=base.schema_version,
        command_codec=base.command_codec,
        budgeted_phases=base.budgeted_phases,
        node_rules=rules,
        contract_digest=_digest(canonical),
    )


class FakeArtifactRepository:
    """只用于注入检查，不执行任何持久化。"""

    def __init__(self, *, outcomes=None) -> None:
        self.outcomes = outcomes
        self.lookup_calls: list[object] = []
        self.put_calls: list[object] = []
        self.get_calls: list[object] = []

    async def put(self, request):
        self.put_calls.append(request)
        raise AssertionError("fake repository must not be called")

    async def lookup(self, *, operation_id):
        self.lookup_calls.append(operation_id)
        if self.outcomes is None:
            raise AssertionError("fake repository must not be called")
        return self.outcomes.get(operation_id, ArtifactAbsent(operation_id=operation_id))

    async def get(self, ref, *, expected_model):
        self.get_calls.append((ref, expected_model))
        raise AssertionError("fake repository must not be called")


class FakeBudgetPlanProvider:
    _PHASE_PLANS = (WorkflowPhase.HOTEL_SELECTION,)

    def __init__(self) -> None:
        self.start_calls: list[UUID] = []
        self.phase_calls: list[tuple[UUID, WorkflowPhase, UUID]] = []

    def plan_for_start(self, *, run_id: UUID) -> ExecutionBudgetPlan:
        if type(run_id) is not UUID:
            raise TypeError("run identity must be a UUID")
        self.start_calls.append(run_id)
        return ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=run_id,
            limits=(
                ExecutionBudgetLimit(
                    key=ExecutionBudgetKey(
                        scope=ExecutionBudgetScope.PHASE,
                        name="requirements.clarification_round",
                        phase=WorkflowPhase.REQUIREMENTS,
                    ),
                    limit=6,
                ),
            ),
        )

    def plan_for_phase(
        self,
        *,
        run_id: UUID,
        phase: WorkflowPhase,
        state: TravelSessionState,
    ) -> ExecutionBudgetPlan:
        if (
            type(run_id) is not UUID
            or type(phase) is not WorkflowPhase
            or type(state) is not TravelSessionState
        ):
            raise DurableExecutionUsageError() from None
        if phase not in self._PHASE_PLANS or state.run_id != run_id:
            raise DurableExecutionUsageError() from None
        self.phase_calls.append((run_id, phase, state.run_id))
        if state.run_id != run_id:
            raise ValueError("state run identity mismatch")
        return ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=run_id,
            limits=(
                ExecutionBudgetLimit(
                    key=ExecutionBudgetKey(
                        scope=ExecutionBudgetScope.PHASE,
                        name=f"{phase.value}.step",
                        phase=phase,
                    ),
                    limit=1,
                ),
            ),
        )


class FakeCommandCodec:
    registry = {"test.command": str}
    _KNOWN_NODES = frozenset({"requirements"})
    descriptor = DurableExecutionCommandCodecDescriptor(
        codec_id="test.codec",
        codec_version="1.0",
        codec_digest="sha256:" + "1" * 64,
    )

    def encode_initial(
        self, *, run_id: UUID, trace_id: UUID, command: object
    ) -> DurableExecutionEncodedCommand:
        if type(run_id) is not UUID or type(trace_id) is not UUID or type(command) is not str:
            raise TypeError("test codec exact initial input required")
        data = _encode_payload(
            {
                "command": command,
                "kind": "initial",
                "run_id": str(run_id),
                "trace_id": str(trace_id),
            }
        )
        return DurableExecutionEncodedCommand(
            command_kind="initial",
            type_id="test.command",
            type_version="1.0",
            canonical_bytes=data,
            digest=_digest(data),
            size_bytes=len(data),
        )

    def encode_resume(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        node_name: str,
        command: object,
    ) -> DurableExecutionEncodedCommand:
        if (
            type(run_id) is not UUID
            or type(pause_generation) is not int
            or pause_generation < 1
            or type(node_name) is not str
            or node_name not in self._KNOWN_NODES
            or type(command) is not str
        ):
            raise TypeError("test codec exact resume input required")
        data = _encode_payload(
            {
                "command": command,
                "kind": "resume",
                "node_name": node_name,
                "pause_generation": pause_generation,
                "run_id": str(run_id),
            }
        )
        return DurableExecutionEncodedCommand(
            command_kind="resume",
            type_id="test.command",
            type_version="1.0",
            canonical_bytes=data,
            digest=_digest(data),
            size_bytes=len(data),
        )

    def decode_initial(self, *, run_id: UUID, trace_id: UUID, encoded):
        payload = _validate_encoded(encoded, kind="initial")
        if (
            payload.get("kind") != "initial"
            or payload.get("run_id") != str(run_id)
            or payload.get("trace_id") != str(trace_id)
        ):
            raise ValueError("test codec initial envelope mismatch")
        command = _command_from_payload(payload)
        _assert_reencoded(
            encoded,
            self.encode_initial(run_id=run_id, trace_id=trace_id, command=command),
        )
        return command

    def decode_resume(self, *, run_id: UUID, pause_generation: int, node_name: str, encoded):
        if node_name not in self._KNOWN_NODES:
            raise ValueError("test codec resume node is not registered")
        payload = _validate_encoded(encoded, kind="resume")
        if (
            payload.get("kind") != "resume"
            or payload.get("run_id") != str(run_id)
            or payload.get("pause_generation") != pause_generation
            or payload.get("node_name") != node_name
        ):
            raise ValueError("test codec resume envelope mismatch")
        command = _command_from_payload(payload)
        _assert_reencoded(
            encoded,
            self.encode_resume(
                run_id=run_id,
                pause_generation=pause_generation,
                node_name=node_name,
                command=command,
            ),
        )
        return command

    def initial_state(self, *, run_id: UUID, trace_id: UUID, decoded_command):
        if type(run_id) is not UUID or type(trace_id) is not UUID or type(decoded_command) is not str:
            raise TypeError("test codec initial state input required")
        return _fresh_model(
            TravelSessionState,
            make_initial_state(run_id=run_id, trace_id=trace_id),
        )

    def requirement_turn(self, *, run_id: UUID, trace_id: UUID, decoded_command):
        if type(run_id) is not UUID or type(trace_id) is not UUID or type(decoded_command) is not str:
            raise TypeError("test codec turn input required")
        state = self.initial_state(
            run_id=run_id,
            trace_id=trace_id,
            decoded_command=decoded_command,
        )
        return _fresh_model(
            RequirementTurnInput,
            make_turn(state, user_message=decoded_command),
        )

    def requirement_resume(self, *, run_id: UUID, pause_generation: int, node_name: str, decoded_command):
        if (
            type(run_id) is not UUID
            or type(pause_generation) is not int
            or pause_generation < 1
            or type(node_name) is not str
            or node_name not in self._KNOWN_NODES
            or type(decoded_command) is not str
        ):
            raise TypeError("test codec resume context required")
        state = self.initial_state(
            run_id=run_id,
            trace_id=TRACE_ID,
            decoded_command=decoded_command,
        )
        return _fresh_model(
            RequirementClarificationResume,
            RequirementClarificationResume(
                turn=make_turn(state, user_message=decoded_command)
            ),
        )


class FakeNodeCapabilities:
    """可执行的任务上下文 fake；默认拒绝脱离绑定上下文的访问。"""

    def __init__(self, *, node_name: str, artifact_repository: ArtifactRepository) -> None:
        self._node_name = node_name
        self._artifact_repository = artifact_repository
        self._context: tuple[UUID, object, ExecutionBudgetManager] | None = None
        self.budget_checks: list[UUID] = []

    @property
    def node_name(self) -> str:
        return self._node_name

    @property
    def artifact_repository(self) -> ArtifactRepository:
        self._require_context()
        return self._artifact_repository

    @property
    def execution_budget_manager(self) -> ExecutionBudgetManager:
        return self._require_context()[2]

    def current_command(self) -> object:
        return self._require_context()[1]

    async def ensure_phase_budget(self, *, state: TravelSessionState) -> None:
        context = self._require_context()
        if type(state) is not TravelSessionState or state.run_id != context[0]:
            raise DurableExecutionUsageError() from None
        self.budget_checks.append(state.run_id)

    def _require_context(self) -> tuple[UUID, object, ExecutionBudgetManager]:
        if self._context is None:
            raise DurableExecutionUsageError() from None
        return self._context

    @contextmanager
    def task_context(
        self,
        *,
        run_id: UUID,
        command: object,
        manager: ExecutionBudgetManager,
    ) -> Iterator["FakeNodeCapabilities"]:
        if type(run_id) is not UUID:
            raise DurableExecutionUsageError() from None
        if self._context is not None:
            raise DurableExecutionUsageError() from None
        self._context = (run_id, command, manager)
        try:
            yield self
        finally:
            self._context = None


class _RunBoundExecutionBudgetManager:
    """测试专用的 run-bound manager；每次调用都重新验证节点上下文。"""

    def __init__(
        self,
        *,
        manager: ExecutionBudgetManager,
        node: FakeNodeCapabilities,
        node_name: str,
        run_id: UUID,
    ) -> None:
        self._manager = manager
        self._node = node
        self._node_name = node_name
        self._run_id = run_id

    def _require_task_context(self) -> None:
        context = self._node._context
        if (
            self._node._node_name != self._node_name
            or context is None
            or context[0] != self._run_id
            or context[2] is not self
        ):
            raise DurableExecutionUsageError() from None

    async def consume(self, request: BudgetConsumeRequest) -> BudgetConsumeOutcome:
        self._require_task_context()
        return await self._manager.consume(request)

    async def get_snapshot(
        self,
        *,
        key: ExecutionBudgetKey,
    ) -> ExecutionBudgetSnapshot:
        self._require_task_context()
        return await self._manager.get_snapshot(key=key)

    async def get_all_snapshots(self) -> tuple[ExecutionBudgetSnapshot, ...]:
        self._require_task_context()
        return await self._manager.get_all_snapshots()


class FakeGraphCapabilities:
    """覆盖节点、run 绑定 manager、任务上下文和驱动登记的行为 fake。"""

    def __init__(
        self,
        *,
        artifact_repository: ArtifactRepository,
        manager_factory: Callable[[UUID], ExecutionBudgetManager],
        limits: RequirementSubgraphCoordinatorLimits,
        durable_limits: DurableExecutionLimits | None = None,
    ) -> None:
        self._artifact_repository = artifact_repository
        self._manager_factory = manager_factory
        self._limits = limits
        if durable_limits is not None and limits.max_runs != durable_limits.max_stored_runs:
            raise DurableExecutionUsageError() from None
        self._nodes: dict[str, FakeNodeCapabilities] = {}
        self._managers: dict[tuple[str, UUID], _RunBoundExecutionBudgetManager] = {}
        self.registered_drivers: dict[str, object] = {}

    @property
    def requirement_subgraph_limits(self) -> RequirementSubgraphCoordinatorLimits:
        return RequirementSubgraphCoordinatorLimits(
            max_runs=self._limits.max_runs
        )

    def for_node(self, *, node_name: str) -> FakeNodeCapabilities:
        if type(node_name) is not str or node_name not in {"requirements", "requirements/城市"}:
            raise DurableExecutionUsageError() from None
        node = self._nodes.get(node_name)
        if node is None:
            node = FakeNodeCapabilities(
                node_name=node_name,
                artifact_repository=self._artifact_repository,
            )
            self._nodes[node_name] = node
        return node

    def execution_budget_manager_factory(
        self, *, node_name: str
    ) -> Callable[[UUID], ExecutionBudgetManager]:
        self.for_node(node_name=node_name)

        def build(run_id: UUID) -> ExecutionBudgetManager:
            if type(run_id) is not UUID:
                raise DurableExecutionUsageError() from None
            key = (node_name, run_id)
            manager = self._managers.get(key)
            if manager is None:
                manager = _RunBoundExecutionBudgetManager(
                    manager=self._manager_factory(run_id),
                    node=self.for_node(node_name=node_name),
                    node_name=node_name,
                    run_id=run_id,
                )
                self._managers[key] = manager
            return manager

        return build

    @contextmanager
    def task_context(
        self,
        *,
        node_name: str,
        run_id: UUID,
        command: object,
    ) -> Iterator[FakeNodeCapabilities]:
        node = self.for_node(node_name=node_name)
        manager = self.execution_budget_manager_factory(node_name=node_name)(run_id)
        with node.task_context(run_id=run_id, command=command, manager=manager):
            yield node

    def register_requirement_subgraph(
        self, *, node_name: str, driver: object
    ) -> None:
        if node_name != "requirements":
            raise DurableExecutionUsageError() from None
        self.for_node(node_name=node_name)
        if node_name in self.registered_drivers:
            raise DurableExecutionUsageError() from None
        if type(driver) is not RequirementSubgraphEmbeddedDriver:
            raise DurableExecutionUsageError() from None
        if driver.limits != self._limits or getattr(driver.graph, "checkpointer", None) is not None:
            raise DurableExecutionUsageError() from None
        if not all(
            callable(getattr(driver, name, None))
            for name in ("start_operation", "resume_accepted_operation", "resolve_result")
        ):
            raise DurableExecutionUsageError() from None
        self.registered_drivers[node_name] = driver


def _encode_payload(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _validate_encoded(encoded: object, *, kind: str) -> dict[str, object]:
    if type(encoded) is not DurableExecutionEncodedCommand:
        raise TypeError("test codec encoded command type mismatch")
    if (
        encoded.command_kind != kind
        or encoded.type_id not in FakeCommandCodec.registry
        or encoded.type_version != FakeCommandCodec.descriptor.codec_version
    ):
        raise ValueError("test codec encoded command envelope mismatch")
    payload = _decode_payload(encoded)
    expected_keys = (
        {"command", "kind", "run_id", "trace_id"}
        if kind == "initial"
        else {"command", "kind", "node_name", "pause_generation", "run_id"}
    )
    if set(payload) != expected_keys or payload.get("kind") != kind:
        raise ValueError("test codec payload envelope mismatch")
    canonical = _encode_payload(payload)
    if (
        canonical != encoded.canonical_bytes
        or encoded.digest != _digest(canonical)
        or encoded.size_bytes != len(canonical)
    ):
        raise ValueError("test codec payload is not canonical")
    return payload


def _decode_payload(encoded: DurableExecutionEncodedCommand) -> dict[str, object]:
    try:
        payload = json.loads(encoded.canonical_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("test codec payload is not canonical") from None
    if type(payload) is not dict:
        raise ValueError("test codec payload is not an object")
    return payload


def _assert_reencoded(
    encoded: DurableExecutionEncodedCommand,
    reencoded: DurableExecutionEncodedCommand,
) -> None:
    if (
        encoded.command_kind != reencoded.command_kind
        or encoded.type_id != reencoded.type_id
        or encoded.type_version != reencoded.type_version
        or encoded.canonical_bytes != reencoded.canonical_bytes
        or encoded.digest != reencoded.digest
        or encoded.size_bytes != reencoded.size_bytes
    ):
        raise ValueError("test codec re-encoding mismatch")


def _fresh_model(model_type: type[BaseModel], value: BaseModel) -> BaseModel:
    return TypeAdapter(model_type).validate_python(
        value.model_dump(mode="python", exclude_none=False),
        strict=True,
    )


def _command_from_payload(payload: dict[str, object]) -> str:
    command = payload.get("command")
    if type(command) is not str or not command:
        raise ValueError("test codec command type mismatch")
    return command


def fake_graph_factory(
    capabilities,
) -> StateGraph:
    del capabilities
    raise AssertionError("graph must not be built in DE-T01")


def fake_limits() -> DurableExecutionLimits:
    return DurableExecutionLimits(
        max_active_runs=4,
        max_stored_runs=4,
        max_managed_operations_per_run=1,
        max_journal_transitions_per_operation=4,
        max_journal_rows_per_run=4,
        max_payload_bytes=1024,
        max_lineage_checkpoints=256,
        recursion_limit=256,
        lease_duration_seconds=60,
        lease_renew_interval_seconds=15,
    )


__all__ = [
    "RUN_ID",
    "TRACE_ID",
    "FakeArtifactRepository",
    "FakeBudgetPlanProvider",
    "FakeCommandCodec",
    "fake_graph_factory",
    "fake_limits",
    "make_contract",
    "make_settings",
]
