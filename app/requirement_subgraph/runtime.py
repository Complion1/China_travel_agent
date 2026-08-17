"""Standalone Runtime and Demo assembly for the requirement subgraph."""

from __future__ import annotations

import json
from typing import cast
from uuid import UUID

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt
from pydantic import BaseModel, TypeAdapter

from app.requirements import RequirementDomainService, RequirementTurnInput
from app.workflow_contracts import (
    DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    InMemoryExecutionBudgetManager,
    TravelSessionState,
)

from app.requirement_subgraph.checkpoint import RequirementCheckpointSerializer
from app.requirement_subgraph.coordinator import RequirementSubgraphCoordinator
from app.requirement_subgraph.errors import (
    RequirementSubgraphConfigurationError,
    RequirementSubgraphInputError,
    RequirementSubgraphResumeRejectedError,
    RequirementSubgraphStateError,
)
from app.requirement_subgraph.graph import (
    _binding_for,
    _claim_runtime,
    build_requirement_subgraph,
)
from app.requirement_subgraph.models import (
    DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS,
    DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT,
    REQUIREMENTS_CLARIFICATION_ROUND_KEY,
    RequirementResumeEnvelope,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphResult,
)
from app.requirement_subgraph.signals import (
    NoOpRequirementSubgraphSignalSink,
    RequirementSubgraphSignalSink,
)


def _fresh_state(value: object) -> TravelSessionState:
    adapter = TypeAdapter(TravelSessionState)
    try:
        if type(value) is TravelSessionState:
            tree = value.model_dump(
                mode="python",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
        elif type(value) is dict:
            tree = value
        else:
            raise TypeError
        first = adapter.validate_python(tree, strict=True)
        json_tree = first.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        second = adapter.validate_json(
            json.dumps(
                json_tree,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
            strict=True,
        )
        return cast(TravelSessionState, second)
    except Exception:
        raise RequirementSubgraphStateError() from None


def _thread_config(run_id: UUID) -> dict[str, dict[str, str]]:
    if type(run_id) is not UUID:
        raise RequirementSubgraphInputError() from None
    return {"configurable": {"thread_id": str(run_id)}}


def _interrupt_from_snapshot(snapshot: object) -> object | None:
    tasks = getattr(snapshot, "tasks", ())
    for task in tasks:
        for value in getattr(task, "interrupts", ()):
            if type(value) is Interrupt:
                return value.value
            raise RequirementSubgraphStateError() from None
    return None


class RequirementSubgraphRuntime:
    def __init__(
        self,
        *,
        graph: CompiledStateGraph,
        coordinator: RequirementSubgraphCoordinator,
    ) -> None:
        binding = _binding_for(graph)
        checkpointer = getattr(graph, "checkpointer", None)
        if (
            binding is None
            or binding.coordinator is not coordinator
            or binding.mode != "standalone"
            or type(binding.checkpointer) is not InMemorySaver
            or type(checkpointer) is not InMemorySaver
            or type(getattr(binding.checkpointer, "serde", None))
            is not RequirementCheckpointSerializer
            or type(getattr(checkpointer, "serde", None))
            is not RequirementCheckpointSerializer
        ):
            raise RequirementSubgraphConfigurationError() from None
        try:
            _claim_runtime(graph, self)
        except Exception:
            raise RequirementSubgraphConfigurationError() from None
        self._graph = graph
        self._coordinator = coordinator

    async def _snapshot(self, run_id: UUID) -> object:
        config = _thread_config(run_id)
        try:
            snapshot = await self._graph.aget_state(config)
        except Exception:
            raise RequirementSubgraphStateError() from None
        if getattr(snapshot, "values", None) is None:
            raise RequirementSubgraphStateError() from None
        return snapshot

    async def _resolve_checkpoint(self, run_id: UUID) -> RequirementSubgraphResult:
        snapshot = await self._snapshot(run_id)
        state = _fresh_state(getattr(snapshot, "values"))
        interrupt_value = _interrupt_from_snapshot(snapshot)
        return self._coordinator.resolve_result(
            state=state,
            interrupt_value=interrupt_value,
        )

    async def start(
        self,
        *,
        state: TravelSessionState,
        turn: RequirementTurnInput,
    ) -> RequirementSubgraphResult:
        if type(state) is not TravelSessionState or type(turn) is not RequirementTurnInput:
            raise RequirementSubgraphInputError() from None
        config = _thread_config(state.run_id)
        async with self._coordinator.start_operation(state=state, turn=turn) as start_state:
            await self._graph.ainvoke(start_state, config=config)
            return await self._resolve_checkpoint(state.run_id)

    async def resume(
        self,
        *,
        run_id: UUID,
        envelope: RequirementResumeEnvelope,
    ) -> RequirementSubgraphResult:
        if type(run_id) is not UUID or type(envelope) is not RequirementResumeEnvelope:
            raise RequirementSubgraphInputError() from None
        try:
            async with self._coordinator._runtime_read_scope(run_id):
                snapshot = await self._snapshot(run_id)
        except RequirementSubgraphInputError:
            raise RequirementSubgraphResumeRejectedError() from None
        state = _fresh_state(getattr(snapshot, "values"))
        async with self._coordinator.resume_operation(
            state=state,
            envelope=envelope,
        ) as command:
            if type(command) is not Command or command.resume != "requirement_resume_v1":
                raise RequirementSubgraphStateError() from None
            config = _thread_config(run_id)
            await self._graph.ainvoke(command, config=config)
            return await self._resolve_checkpoint(run_id)

    async def get_state(self, *, run_id: UUID) -> TravelSessionState:
        if type(run_id) is not UUID:
            raise RequirementSubgraphInputError() from None
        async with self._coordinator._runtime_read_scope(run_id):
            snapshot = await self._snapshot(run_id)
            return _fresh_state(getattr(snapshot, "values"))


def create_demo_requirement_subgraph_runtime(
    *,
    domain_service: RequirementDomainService,
    signal_sink: RequirementSubgraphSignalSink = NoOpRequirementSubgraphSignalSink(),
    limits: RequirementSubgraphCoordinatorLimits = DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS,
) -> RequirementSubgraphRuntime:
    def manager_factory(run_id: UUID) -> InMemoryExecutionBudgetManager:
        plan = ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=run_id,
            limits=(
                ExecutionBudgetLimit(
                    key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
                    limit=DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT,
                ),
            ),
        )
        return InMemoryExecutionBudgetManager(
            plan=plan,
            limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
        )

    coordinator = RequirementSubgraphCoordinator(
        domain_service=domain_service,
        execution_budget_manager_factory=manager_factory,
        signal_sink=signal_sink,
        limits=limits,
    )
    serializer = RequirementCheckpointSerializer()
    saver = InMemorySaver(serde=serializer)
    graph = build_requirement_subgraph(coordinator=coordinator, checkpointer=saver)
    return RequirementSubgraphRuntime(graph=graph, coordinator=coordinator)


__all__ = [
    "RequirementSubgraphRuntime",
    "create_demo_requirement_subgraph_runtime",
]
