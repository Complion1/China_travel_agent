"""Controlled adapter for composing the requirement child graph durably."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Callable
from uuid import UUID

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.requirements import RequirementDomainService, RequirementTurnInput
from app.workflow_contracts import ExecutionBudgetManager, TravelSessionState

from app.requirement_subgraph.coordinator import RequirementSubgraphCoordinator
from app.requirement_subgraph.errors import RequirementSubgraphConfigurationError
from app.requirement_subgraph.graph import (
    _binding_for,
    _claim_runtime,
    build_requirement_subgraph,
)
from app.requirement_subgraph.models import (
    DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS,
    PauseToken,
    RequirementResume,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphDurablePause,
    RequirementSubgraphResult,
)
from app.requirement_subgraph.signals import (
    NoOpRequirementSubgraphSignalSink,
    RequirementSubgraphSignalSink,
)


class RequirementSubgraphEmbeddedDriver:
    """Own one embedded child/coordinator pair without owning a checkpointer."""

    def __init__(
        self,
        *,
        graph: CompiledStateGraph,
        coordinator: RequirementSubgraphCoordinator,
    ) -> None:
        binding = _binding_for(graph)
        if (
            binding is None
            or binding.coordinator is not coordinator
            or binding.mode != "embedded"
            or binding.checkpointer is not None
            or getattr(graph, "checkpointer", None) is not None
        ):
            raise RequirementSubgraphConfigurationError() from None
        try:
            _claim_runtime(graph, self)
        except Exception:
            raise RequirementSubgraphConfigurationError() from None
        self._graph = graph
        self._coordinator = coordinator

    @property
    def graph(self) -> CompiledStateGraph:
        return self._graph

    @property
    def limits(self) -> RequirementSubgraphCoordinatorLimits:
        return self._coordinator._limits

    def start_operation(
        self,
        *,
        state: TravelSessionState,
        turn: RequirementTurnInput,
    ) -> AbstractAsyncContextManager[TravelSessionState]:
        return self._coordinator.start_operation(state=state, turn=turn)

    def verify_pause_token(
        self,
        *,
        durable_pause: RequirementSubgraphDurablePause,
        pause_token: PauseToken,
    ) -> None:
        self._coordinator._verify_durable_pause_token(
            durable_pause=durable_pause,
            pause_token=pause_token,
        )

    def resume_accepted_operation(
        self,
        *,
        state: TravelSessionState,
        durable_pause: RequirementSubgraphDurablePause,
        resume: RequirementResume,
    ) -> AbstractAsyncContextManager[Command]:
        return self._coordinator._resume_durable_operation(
            state=state,
            durable_pause=durable_pause,
            resume=resume,
        )

    def resolve_result(
        self,
        *,
        state: TravelSessionState,
        interrupt_value: object | None,
    ) -> RequirementSubgraphResult:
        return self._coordinator.resolve_result(
            state=state,
            interrupt_value=interrupt_value,
        )


def create_requirement_subgraph_embedded_driver(
    *,
    domain_service: RequirementDomainService,
    execution_budget_manager_factory: Callable[[UUID], ExecutionBudgetManager],
    signal_sink: RequirementSubgraphSignalSink = NoOpRequirementSubgraphSignalSink(),
    limits: RequirementSubgraphCoordinatorLimits = DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS,
) -> RequirementSubgraphEmbeddedDriver:
    coordinator = RequirementSubgraphCoordinator(
        domain_service=domain_service,
        execution_budget_manager_factory=execution_budget_manager_factory,
        signal_sink=signal_sink,
        limits=limits,
    )
    graph = build_requirement_subgraph(coordinator=coordinator, checkpointer=None)
    return RequirementSubgraphEmbeddedDriver(graph=graph, coordinator=coordinator)


__all__ = [
    "RequirementSubgraphEmbeddedDriver",
    "create_requirement_subgraph_embedded_driver",
]
