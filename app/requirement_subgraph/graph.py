"""LangGraph nodes and builder for the requirement subgraph."""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from typing import Literal, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import TypeAdapter

from app.requirements import (
    ConfirmedTravelRequirement,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainOutcome,
    RequirementDomainTransition,
    RequirementExtractionFailed,
    RequirementInvalidated,
    RequirementTurnInput,
)
from app.workflow_contracts import (
    BudgetConsumeRequest,
    BudgetConsumed as WorkflowBudgetConsumed,
    BudgetExceeded as WorkflowBudgetExceeded,
    DeliveryStatus,
    OperationId,
    TravelSessionState,
    WorkflowPhase,
)

from app.requirement_subgraph.coordinator import (
    _REQUIREMENT_RESUME_SENTINEL,
    RequirementSubgraphCoordinator,
)
from app.requirement_subgraph.errors import RequirementSubgraphStateError
from app.requirement_subgraph.models import (
    REQUIREMENTS_CLARIFICATION_ROUND_KEY,
    RequirementClarificationInterrupt,
    RequirementClarificationResume,
    RequirementConfirmationInterrupt,
    RequirementConfirmationResume,
    RequirementInterrupt,
    RequirementResumeEnvelope,
    RequirementSubgraphBudgetExceeded,
    RequirementSubgraphConfirmed,
    RequirementSubgraphDomainFailed,
    RequirementSubgraphResult,
    _fresh_model,
)


RequirementDestination = Literal[
    "await_clarification",
    "await_confirmation",
    "__end__",
]


@dataclass(slots=True)
class _GraphBinding:
    coordinator: RequirementSubgraphCoordinator
    mode: Literal["standalone", "embedded"]
    checkpointer: BaseCheckpointSaver[str] | None
    runtime: weakref.ReferenceType[object] | None = None


_BINDINGS: weakref.WeakKeyDictionary[object, _GraphBinding] = weakref.WeakKeyDictionary()


def _binding_for(graph: object) -> _GraphBinding | None:
    return _BINDINGS.get(graph)


def _claim_runtime(graph: object, runtime: object) -> None:
    binding = _BINDINGS.get(graph)
    if binding is None:
        raise RequirementSubgraphStateError() from None
    if binding.runtime is not None and binding.runtime() is not None:
        raise RequirementSubgraphStateError() from None
    try:
        binding.runtime = weakref.ref(runtime)
    except TypeError:
        raise RequirementSubgraphStateError() from None


def _fresh_transition(value: object) -> RequirementDomainTransition:
    if type(value) is not RequirementDomainTransition:
        raise RequirementSubgraphStateError() from None
    try:
        return cast(
            RequirementDomainTransition,
            _fresh_model(RequirementDomainTransition, value),
        )
    except Exception:
        raise RequirementSubgraphStateError() from None


def _destination_for_outcome(
    outcome: RequirementDomainOutcome,
) -> RequirementDestination:
    if type(outcome) is NeedClarification:
        return "await_clarification"
    if type(outcome) in (NeedConfirmation, RequirementInvalidated):
        return "await_confirmation"
    if type(outcome) in (ConfirmedTravelRequirement, RequirementExtractionFailed):
        return END
    raise RequirementSubgraphStateError() from None


def _state_after_transition(
    state: TravelSessionState,
    transition: RequirementDomainTransition,
) -> TravelSessionState:
    outcome = transition.outcome
    if type(outcome) in (NeedClarification, NeedConfirmation, RequirementInvalidated):
        delivery_status = DeliveryStatus.AWAITING_USER
    elif type(outcome) in (ConfirmedTravelRequirement, RequirementExtractionFailed):
        delivery_status = DeliveryStatus.IN_PROGRESS
    else:
        raise RequirementSubgraphStateError() from None
    try:
        return cast(
            TravelSessionState,
            _fresh_model(
                TravelSessionState,
                TravelSessionState(
                    schema_version=state.schema_version,
                    run_id=state.run_id,
                    trace_id=state.trace_id,
                    phase=state.phase,
                    delivery_status=delivery_status,
                    terminal_code=state.terminal_code,
                    terminal_outcome=state.terminal_outcome,
                    requirement_state=transition.state,
                    requirement_outcome=transition.outcome,
                    artifact_refs=state.artifact_refs,
                ),
            ),
        )
    except Exception:
        raise RequirementSubgraphStateError() from None


def _budget_rejected_state(state: TravelSessionState) -> TravelSessionState:
    try:
        return cast(
            TravelSessionState,
            _fresh_model(
                TravelSessionState,
                TravelSessionState(
                    schema_version=state.schema_version,
                    run_id=state.run_id,
                    trace_id=state.trace_id,
                    phase=state.phase,
                    delivery_status=DeliveryStatus.IN_PROGRESS,
                    terminal_code=state.terminal_code,
                    terminal_outcome=state.terminal_outcome,
                    requirement_state=state.requirement_state,
                    requirement_outcome=state.requirement_outcome,
                    artifact_refs=state.artifact_refs,
                ),
            ),
        )
    except Exception:
        raise RequirementSubgraphStateError() from None


def _budget_operation(run_id, nonce: str) -> OperationId:
    return OperationId(
        run_id=run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step=f"clarification_round_{nonce}",
    )


def _pause_payload(interrupt_dto: RequirementInterrupt) -> dict[str, object]:
    try:
        tree = interrupt_dto.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        if type(tree) is not dict:
            raise TypeError
        return tree
    except Exception:
        raise RequirementSubgraphStateError() from None


def _result_command(
    coordinator: RequirementSubgraphCoordinator,
    state: TravelSessionState,
    result: RequirementSubgraphResult,
) -> Command[RequirementDestination]:
    coordinator._set_latest_state(state)
    coordinator._set_local_result(result)
    return Command(update=state, goto=END)


def _terminal_result_command(
    coordinator: RequirementSubgraphCoordinator,
    state: TravelSessionState,
) -> Command[RequirementDestination]:
    if type(state.requirement_outcome) is ConfirmedTravelRequirement:
        result: RequirementSubgraphResult = RequirementSubgraphConfirmed(state=state)
    elif type(state.requirement_outcome) is RequirementExtractionFailed:
        result = RequirementSubgraphDomainFailed(state=state)
    else:
        raise RequirementSubgraphStateError() from None
    return _result_command(coordinator, state, result)


async def _process_initial_turn(
    state: TravelSessionState,
    *,
    coordinator: RequirementSubgraphCoordinator,
) -> Command[RequirementDestination]:
    turn = coordinator._take_turn()
    try:
        transition = _fresh_transition(
            await coordinator._domain_service.process_turn(turn)
        )
        next_state = _state_after_transition(state, transition)
    except Exception:
        raise
    coordinator._set_latest_state(next_state)
    destination = _destination_for_outcome(transition.outcome)
    if destination == END:
        return _terminal_result_command(coordinator, next_state)
    return Command(update=next_state, goto=destination)


def _interrupt_for_state(state: TravelSessionState) -> RequirementInterrupt:
    outcome = state.requirement_outcome
    if type(outcome) is NeedClarification:
        return RequirementClarificationInterrupt(message=outcome.message)
    if type(outcome) is NeedConfirmation:
        return RequirementConfirmationInterrupt(
            source_outcome_type="need_confirmation",
            summary=outcome.summary,
        )
    if type(outcome) is RequirementInvalidated:
        return RequirementConfirmationInterrupt(
            source_outcome_type="invalidated",
            summary=outcome.summary,
        )
    raise RequirementSubgraphStateError() from None


async def _replay_pause(
    state: TravelSessionState,
    *,
    coordinator: RequirementSubgraphCoordinator,
) -> tuple[str, RequirementInterrupt, object | None]:
    interrupt_dto = _interrupt_for_state(state)
    _, expected_nonce, expected_descriptor = coordinator._ensure_current_pause(
        state=state,
        interrupt=interrupt_dto,
    )
    response = interrupt(_pause_payload(expected_descriptor))
    if type(response) is not str or response != _REQUIREMENT_RESUME_SENTINEL:
        raise RequirementSubgraphStateError() from None
    resume_envelope = coordinator._take_resume()
    nonce, descriptor = coordinator._take_pause_for_replay()
    if nonce != expected_nonce or descriptor != expected_descriptor:
        raise RequirementSubgraphStateError() from None
    coordinator._node_replayed_operation()
    return nonce, descriptor, resume_envelope


async def _await_clarification(
    state: TravelSessionState,
    *,
    coordinator: RequirementSubgraphCoordinator,
) -> Command[RequirementDestination]:
    nonce, _, resume_envelope = await _replay_pause(state, coordinator=coordinator)
    if resume_envelope is None:
        raise RequirementSubgraphStateError() from None
    resume = cast(RequirementResumeEnvelope, resume_envelope).resume
    if type(resume) is not RequirementClarificationResume:
        raise RequirementSubgraphStateError() from None
    budget = await coordinator._consume_budget(
        BudgetConsumeRequest(
            operation_id=_budget_operation(state.run_id, nonce),
            key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
            amount=1,
        )
    )
    if type(budget) is WorkflowBudgetExceeded and budget.disposition == "rejected":
        next_state = _budget_rejected_state(state)
        return _result_command(
            coordinator,
            next_state,
            RequirementSubgraphBudgetExceeded(
                state=next_state,
                budget_outcome=budget,
            ),
        )
    if type(budget) is not WorkflowBudgetConsumed or budget.disposition != "applied":
        raise RequirementSubgraphStateError() from None
    transition = _fresh_transition(
        await coordinator._domain_service.process_turn(resume.turn)
    )
    next_state = _state_after_transition(state, transition)
    coordinator._set_latest_state(next_state)
    destination = _destination_for_outcome(transition.outcome)
    if destination == END:
        return _terminal_result_command(coordinator, next_state)
    return Command(update=next_state, goto=destination)


async def _await_confirmation(
    state: TravelSessionState,
    *,
    coordinator: RequirementSubgraphCoordinator,
) -> Command[RequirementDestination]:
    nonce, _, resume_envelope = await _replay_pause(state, coordinator=coordinator)
    if resume_envelope is None:
        raise RequirementSubgraphStateError() from None
    resume = cast(RequirementResumeEnvelope, resume_envelope).resume
    if type(resume) is not RequirementConfirmationResume:
        raise RequirementSubgraphStateError() from None
    if type(resume.action) is not RequirementConfirmation:
        raise RequirementSubgraphStateError() from None
    if resume.action.decision == "revise":
        budget = await coordinator._consume_budget(
            BudgetConsumeRequest(
                operation_id=_budget_operation(state.run_id, nonce),
                key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
                amount=1,
            )
        )
        if type(budget) is WorkflowBudgetExceeded and budget.disposition == "rejected":
            next_state = _budget_rejected_state(state)
            return _result_command(
                coordinator,
                next_state,
                RequirementSubgraphBudgetExceeded(
                    state=next_state,
                    budget_outcome=budget,
                ),
            )
        if type(budget) is not WorkflowBudgetConsumed or budget.disposition != "applied":
            raise RequirementSubgraphStateError() from None
    transition = _fresh_transition(
        await coordinator._domain_service.confirm(
            state.requirement_state,
            resume.action,
        )
    )
    next_state = _state_after_transition(state, transition)
    coordinator._set_latest_state(next_state)
    destination = _destination_for_outcome(transition.outcome)
    if destination == END:
        return _terminal_result_command(coordinator, next_state)
    return Command(update=next_state, goto=destination)


def build_requirement_subgraph(
    *,
    coordinator: RequirementSubgraphCoordinator,
    checkpointer: BaseCheckpointSaver[str] | None,
) -> CompiledStateGraph:
    if not isinstance(coordinator, RequirementSubgraphCoordinator):
        raise RequirementSubgraphStateError() from None

    async def process_initial_turn_node(
        state: TravelSessionState,
    ) -> Command[RequirementDestination]:
        return await _process_initial_turn(state, coordinator=coordinator)

    async def await_clarification_node(
        state: TravelSessionState,
    ) -> Command[RequirementDestination]:
        return await _await_clarification(state, coordinator=coordinator)

    async def await_confirmation_node(
        state: TravelSessionState,
    ) -> Command[RequirementDestination]:
        return await _await_confirmation(state, coordinator=coordinator)

    builder = StateGraph(TravelSessionState)
    builder.add_node(
        "process_initial_turn",
        process_initial_turn_node,
        destinations=("await_clarification", "await_confirmation", END),
    )
    builder.add_node(
        "await_clarification",
        await_clarification_node,
        destinations=("await_clarification", "await_confirmation", END),
    )
    builder.add_node(
        "await_confirmation",
        await_confirmation_node,
        destinations=("await_clarification", "await_confirmation", END),
    )
    builder.add_edge(START, "process_initial_turn")
    graph = builder.compile(checkpointer=checkpointer)
    _BINDINGS[graph] = _GraphBinding(
        coordinator=coordinator,
        mode="standalone" if checkpointer is not None else "embedded",
        checkpointer=checkpointer,
    )
    return graph


__all__ = ["build_requirement_subgraph"]
