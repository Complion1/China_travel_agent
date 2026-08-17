from __future__ import annotations

from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from app.requirement_subgraph import (
    RequirementCheckpointSerializer,
    RequirementSubgraphCoordinator,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphStateError,
    RequirementSubgraphBudgetExceeded,
    RequirementSubgraphPaused,
    build_requirement_subgraph,
)
from app.requirement_subgraph.errors import RequirementSubgraphRunFaultedError
from app.requirement_subgraph.models import (
    REQUIREMENTS_CLARIFICATION_ROUND_KEY,
    RequirementClarificationResume,
    RequirementConfirmationInterrupt,
    RequirementPauseEnvelope,
    RequirementResumeEnvelope,
)
from app.requirements import RequirementErrorCode
from app.workflow_contracts import (
    BudgetConsumed,
    BudgetExceeded,
    ExecutionBudgetKey,
    ExecutionBudgetScope,
    ExecutionBudgetSnapshot,
    OperationId,
    OperationResultIndeterminateError,
    DeliveryStatus,
    WorkflowPhase,
)
from tests.requirement_subgraph_fakes import (
    PerRunBudgetManagerFactory,
    RecordingRequirementSubgraphSignalSink,
    ScriptedRequirementDomainService,
    make_extraction_failed_transition,
    make_confirmed_transition,
    make_initial_state,
    make_need_clarification_transition,
    make_need_confirmation_transition,
    make_turn,
)


class _OutcomeManager:
    def __init__(self, builder):
        self.builder = builder
        self.requests = []

    async def consume(self, request):
        self.requests.append(request)
        return self.builder(request)

    async def get_snapshot(self, *, key):
        raise AssertionError("unexpected snapshot read")

    async def get_all_snapshots(self):
        return ()


def _snapshot(request, *, run_id=None, key=None):
    return ExecutionBudgetSnapshot(
        schema_version="1.0",
        run_id=run_id or request.operation_id.run_id,
        key=key or request.key,
        limit=1,
        consumed=0,
        remaining=1,
    )


def _runtime(service, builder, sink):
    factory = PerRunBudgetManagerFactory(lambda _run_id: _OutcomeManager(builder))
    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=factory,
        signal_sink=sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    graph = build_requirement_subgraph(
        coordinator=coordinator,
        checkpointer=InMemorySaver(serde=RequirementCheckpointSerializer()),
    )
    from app.requirement_subgraph import RequirementSubgraphRuntime

    return RequirementSubgraphRuntime(graph=graph, coordinator=coordinator), factory


def _paused_runtime(builder):
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[
            make_need_clarification_transition(initial),
            make_extraction_failed_transition(initial),
        ]
    )
    sink = RecordingRequirementSubgraphSignalSink()
    runtime, factory = _runtime(service, builder, sink)
    return initial, service, sink, runtime, factory


def _resume(result):
    return RequirementResumeEnvelope(
        pause_token=result.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(result.state)),
    )


@pytest.mark.parametrize("outcome_type", ["consumed", "exceeded"])
@pytest.mark.asyncio
async def test_replayed_budget_outcomes_preserve_upstream_indeterminate_error(outcome_type):
    def builder(request):
        snapshot = _snapshot(request)
        if outcome_type == "consumed":
            return BudgetConsumed(
                disposition="replayed",
                operation_id=request.operation_id,
                key=request.key,
                amount=1,
                snapshot=snapshot,
            )
        return BudgetExceeded(
            disposition="replayed",
            operation_id=request.operation_id,
            key=request.key,
            amount=1,
            snapshot=snapshot,
        )

    initial, service, sink, runtime, _factory = _paused_runtime(builder)
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    with pytest.raises(OperationResultIndeterminateError) as caught:
        await runtime.resume(run_id=initial.run_id, envelope=_resume(paused))
    assert caught.value.args == ()
    assert service.process_calls and len(service.process_calls) == 1
    assert all(type(signal).__name__ not in {"BudgetConsumed", "BudgetExceeded"} for signal in sink.calls)
    with pytest.raises(RequirementSubgraphRunFaultedError):
        await runtime.get_state(run_id=initial.run_id)


@pytest.mark.parametrize("mutation", ["operation", "key", "phase", "amount", "snapshot_run", "snapshot_key"])
@pytest.mark.asyncio
async def test_budget_correlation_is_checked_before_disposition(mutation):
    other_key = ExecutionBudgetKey(
        scope=ExecutionBudgetScope.PHASE,
        name="requirements.other",
        phase=WorkflowPhase.REQUIREMENTS,
    )

    def builder(request):
        operation_id = request.operation_id
        key = request.key
        amount = request.amount
        snapshot_run = request.operation_id.run_id
        snapshot_key = request.key
        if mutation == "operation":
            operation_id = OperationId(
                run_id=uuid4(),
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step=request.operation_id.logical_step,
            )
        elif mutation == "key":
            key = other_key
        elif mutation == "phase":
            operation_id = OperationId(
                run_id=request.operation_id.run_id,
                phase=WorkflowPhase.HOTEL_SELECTION,
                logical_step=request.operation_id.logical_step,
            )
        elif mutation == "amount":
            amount = 2
        elif mutation == "snapshot_run":
            snapshot_run = uuid4()
        elif mutation == "snapshot_key":
            snapshot_key = other_key
        return BudgetConsumed(
            disposition="applied",
            operation_id=operation_id,
            key=key,
            amount=amount,
            snapshot=_snapshot(request, run_id=snapshot_run, key=snapshot_key),
        )

    initial, service, sink, runtime, _factory = _paused_runtime(builder)
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    with pytest.raises(RequirementSubgraphStateError):
        await runtime.resume(run_id=initial.run_id, envelope=_resume(paused))
    assert len(service.process_calls) == 1
    assert all(type(signal).__name__ != "BudgetConsumed" for signal in sink.calls)
    with pytest.raises(RequirementSubgraphRunFaultedError):
        await runtime.get_state(run_id=initial.run_id)


@pytest.mark.asyncio
async def test_applied_budget_service_exception_is_original_and_not_retried():
    original = RuntimeError("service-original")
    initial = make_initial_state()

    class FailingSecondService(ScriptedRequirementDomainService):
        async def process_turn(self, input):
            if self.process_calls:
                self.process_error = original
            return await super().process_turn(input)

    service = FailingSecondService(
        process_results=[make_need_clarification_transition(initial)],
    )
    sink = RecordingRequirementSubgraphSignalSink()

    def builder(request):
        return BudgetConsumed(
            disposition="applied",
            operation_id=request.operation_id,
            key=request.key,
            amount=request.amount,
            snapshot=_snapshot(request),
        )

    runtime, _factory = _runtime(service, builder, sink)
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    with pytest.raises(RuntimeError) as caught:
        await runtime.resume(run_id=initial.run_id, envelope=_resume(paused))
    assert caught.value is original
    assert len(service.process_calls) == 2


def _valid_budget_result():
    initial = make_initial_state()
    transition = make_need_confirmation_transition(initial, summary="right")
    state = initial.__class__(
        schema_version=initial.schema_version,
        run_id=initial.run_id,
        trace_id=initial.trace_id,
        phase=initial.phase,
        delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=transition.state,
        requirement_outcome=transition.outcome,
        artifact_refs=(),
    )
    operation = OperationId(
        run_id=state.run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="clarification_round_" + "a" * 32,
    )
    outcome = BudgetExceeded(
        disposition="rejected",
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
        snapshot=ExecutionBudgetSnapshot(
            schema_version="1.0",
            run_id=state.run_id,
            key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
            limit=1,
            consumed=0,
            remaining=1,
        ),
    )
    return state, outcome


@pytest.mark.parametrize(
    "mutation",
    ["operation", "key", "phase", "amount", "snapshot_run", "snapshot_key", "prefix", "replayed"],
)
def test_budget_exceeded_result_rejects_each_single_contract_mutation(mutation):
    state, valid = _valid_budget_result()
    other_key = ExecutionBudgetKey(
        scope=ExecutionBudgetScope.PHASE,
        name="requirements.other",
        phase=WorkflowPhase.REQUIREMENTS,
    )
    operation = valid.operation_id
    key = valid.key
    amount = valid.amount
    snapshot = valid.snapshot
    disposition = valid.disposition
    if mutation == "operation":
        operation = OperationId(
            run_id=uuid4(),
            phase=operation.phase,
            logical_step=operation.logical_step,
        )
    elif mutation == "key":
        key = other_key
    elif mutation == "phase":
        operation = OperationId(
            run_id=operation.run_id,
            phase=WorkflowPhase.HOTEL_SELECTION,
            logical_step=operation.logical_step,
        )
    elif mutation == "amount":
        amount = 2
    elif mutation == "snapshot_run":
        snapshot = snapshot.model_copy(update={"run_id": uuid4()})
    elif mutation == "snapshot_key":
        snapshot = snapshot.model_copy(update={"key": other_key})
    elif mutation == "prefix":
        operation = OperationId(
            run_id=operation.run_id,
            phase=operation.phase,
            logical_step="wrong_" + "a" * 32,
        )
    elif mutation == "replayed":
        disposition = "replayed"
    with pytest.raises(ValidationError):
        RequirementSubgraphBudgetExceeded(
            state=state,
            budget_outcome=BudgetExceeded(
                disposition=disposition,
                operation_id=operation,
                key=key,
                amount=amount,
                snapshot=snapshot,
            ),
        )


@pytest.mark.parametrize(
    "source,summary",
    [("invalidated", "wrong"), ("need_confirmation", "wrong")],
)
def test_paused_confirmation_source_and_summary_must_match_outcome(source, summary):
    initial = make_initial_state()
    transition = make_need_confirmation_transition(initial, summary="right")
    state = initial.__class__(
        schema_version="2.0",
        run_id=initial.run_id,
        trace_id=initial.trace_id,
        phase=initial.phase,
        delivery_status=DeliveryStatus.AWAITING_USER,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=transition.state,
        requirement_outcome=transition.outcome,
        artifact_refs=(),
    )
    with pytest.raises(ValidationError):
        RequirementSubgraphPaused(
            state=state,
            pause=RequirementPauseEnvelope(
                pause_token="a" * 32,
                interrupt=RequirementConfirmationInterrupt(
                    source_outcome_type=source,
                    summary=summary,
                ),
            ),
        )


def test_confirmed_and_domain_failed_results_reject_wrong_outcome_discriminators():
    initial = make_initial_state()
    confirmed = make_confirmed_transition(initial)
    failed = make_extraction_failed_transition(initial)
    confirmed_state = initial.__class__(
        schema_version="2.0",
        run_id=initial.run_id,
        trace_id=initial.trace_id,
        phase=initial.phase,
        delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=confirmed.state,
        requirement_outcome=confirmed.outcome,
        artifact_refs=(),
    )
    failed_state = initial.__class__(
        schema_version="2.0",
        run_id=initial.run_id,
        trace_id=initial.trace_id,
        phase=initial.phase,
        delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=failed.state,
        requirement_outcome=failed.outcome,
        artifact_refs=(),
    )
    from app.requirement_subgraph import RequirementSubgraphDomainFailed, RequirementSubgraphConfirmed

    with pytest.raises(ValidationError):
        RequirementSubgraphConfirmed(state=failed_state)
    with pytest.raises(ValidationError):
        RequirementSubgraphDomainFailed(state=confirmed_state)
