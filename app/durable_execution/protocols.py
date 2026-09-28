"""Module 11 的依赖注入与受控能力 Protocol。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from langgraph.graph import StateGraph

from app.requirement_subgraph import (
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphEmbeddedDriver,
    RequirementResume,
)
from app.workflow_contracts import (
    ArtifactRepository,
    ContentHash,
    ExecutionBudgetManager,
    ExecutionBudgetPlan,
    TravelSessionState,
    WorkflowPhase,
)
from app.durable_execution.models import (
    DurableExecutionCommandCodecDescriptor,
    DurableExecutionEncodedCommand,
    DurableExecutionLimits,
)


class DurableExecutionCommandCodec(Protocol):
    @property
    def descriptor(self) -> DurableExecutionCommandCodecDescriptor: ...

    def encode_initial(
        self, *, run_id: UUID, trace_id: UUID, command: object
    ) -> DurableExecutionEncodedCommand: ...

    def encode_resume(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        node_name: str,
        command: object,
    ) -> DurableExecutionEncodedCommand: ...

    def decode_initial(
        self,
        *,
        run_id: UUID,
        trace_id: UUID,
        encoded: DurableExecutionEncodedCommand,
    ) -> object: ...

    def decode_resume(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        node_name: str,
        encoded: DurableExecutionEncodedCommand,
    ) -> object: ...

    def initial_state(
        self, *, run_id: UUID, trace_id: UUID, decoded_command: object
    ) -> TravelSessionState: ...

    def requirement_turn(
        self, *, run_id: UUID, trace_id: UUID, decoded_command: object
    ) -> object: ...

    def requirement_resume(
        self,
        *,
        run_id: UUID,
        pause_generation: int,
        node_name: str,
        decoded_command: object,
    ) -> RequirementResume: ...


class DurableExecutionBudgetPlanProvider(Protocol):
    def plan_for_start(self, *, run_id: UUID) -> ExecutionBudgetPlan: ...

    def plan_for_phase(
        self,
        *,
        run_id: UUID,
        phase: WorkflowPhase,
        state: TravelSessionState,
    ) -> ExecutionBudgetPlan: ...


class DurablePauseCredentialSink(Protocol):
    async def store(
        self, *, run_id: UUID, pause_generation: int,
        contract_digest: ContentHash, pause_token: str,
    ) -> None: ...


class DurableExecutionNodeCapabilities(Protocol):
    @property
    def node_name(self) -> str: ...

    @property
    def artifact_repository(self) -> ArtifactRepository: ...

    @property
    def read_only_artifact_repository(self) -> ArtifactRepository: ...

    @property
    def execution_budget_manager(self) -> ExecutionBudgetManager: ...

    def current_command(self) -> object: ...

    async def ensure_phase_budget(self, *, state: TravelSessionState) -> None: ...


class DurableExecutionGraphCapabilities(Protocol):
    @property
    def requirement_subgraph_limits(self) -> RequirementSubgraphCoordinatorLimits: ...

    def for_node(self, *, node_name: str) -> DurableExecutionNodeCapabilities: ...

    def execution_budget_manager_factory(
        self, *, node_name: str
    ) -> Callable[[UUID], ExecutionBudgetManager]: ...

    def register_requirement_subgraph(
        self, *, node_name: str, driver: RequirementSubgraphEmbeddedDriver
    ) -> None: ...


__all__ = [
    "DurableExecutionCommandCodec",
    "DurableExecutionBudgetPlanProvider",
    "DurableExecutionGraphCapabilities",
    "DurableExecutionNodeCapabilities",
    "DurablePauseCredentialSink",
]
