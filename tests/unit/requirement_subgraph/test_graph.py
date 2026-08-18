from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START

from app.requirement_subgraph import (
    RequirementCheckpointSerializer,
    RequirementClarificationResume,
    RequirementConfirmationResume,
    RequirementSubgraphBudgetExceeded,
    RequirementSubgraphConfirmed,
    RequirementSubgraphCoordinator,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphRuntime,
    RequirementSubgraphResumeRejectedError,
    build_requirement_subgraph,
)
from app.requirement_subgraph.errors import RequirementSubgraphStateError
import app.requirement_subgraph.graph as graph_module
from app.requirement_subgraph.coordinator import _REQUIREMENT_RESUME_SENTINEL
from app.requirement_subgraph.graph import _destination_for_outcome
from app.requirement_subgraph.models import RequirementResumeEnvelope
from app.requirement_subgraph.signals import BudgetConsumed, NodeReplayed
from app.requirements import (
    ConfirmedTravelRequirement,
    NeedClarification,
    NeedConfirmation,
    RequirementErrorCode,
    RequirementExtractionFailed,
    RequirementDomainService,
    RequirementConfirmation,
    RequirementFieldPath,
    RequirementIntent,
    RequirementInvalidated,
    RequirementTurnInput,
)
from app.requirements.extraction_contract import ATTR_TO_PATH, StrictFieldEvidenceDTO
from app.workflow_contracts import (
    DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetSnapshot,
    InMemoryExecutionBudgetManager,
    OperationId,
    WorkflowPhase,
)
from tests.requirement_subgraph_fakes import (
    PerRunBudgetManagerFactory,
    RecordingRequirementSubgraphSignalSink,
    ScriptedRequirementDomainService,
    make_confirmed_transition,
    make_extraction_failed_transition,
    make_initial_state,
    make_need_clarification_transition,
    make_need_confirmation_transition,
    make_turn,
)
from tests.unit.requirements.fakes import (
    FixedClock,
    InMemoryEvidenceSink,
    RecordingRequirementExtractor,
    strict_dto,
)


def _runtime(
    service: ScriptedRequirementDomainService,
    *,
    sink: RecordingRequirementSubgraphSignalSink | None = None,
    budget_limit: int = 6,
) -> tuple[RequirementSubgraphRuntime, RecordingRequirementSubgraphSignalSink, PerRunBudgetManagerFactory]:
    signal_sink = sink or RecordingRequirementSubgraphSignalSink()

    def make_manager(run_id: UUID) -> InMemoryExecutionBudgetManager:
        from app.requirement_subgraph.models import REQUIREMENTS_CLARIFICATION_ROUND_KEY

        plan = ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=run_id,
            limits=(
                ExecutionBudgetLimit(
                    key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
                    limit=budget_limit,
                ),
            ),
        )
        return InMemoryExecutionBudgetManager(
            plan=plan,
            limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
        )

    factory = PerRunBudgetManagerFactory(make_manager)
    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=factory,
        signal_sink=signal_sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=256),
    )
    saver = InMemorySaver(serde=RequirementCheckpointSerializer())
    graph = build_requirement_subgraph(coordinator=coordinator, checkpointer=saver)
    return RequirementSubgraphRuntime(graph=graph, coordinator=coordinator), signal_sink, factory


def _real_patch(**values):
    evidence = []
    for name, value in values.items():
        if name not in ATTR_TO_PATH or value is None:
            continue
        if name == "destination":
            text = str(value)
        elif name == "budget_total_cny":
            text = "5000"
        elif name in {"arrival_datetime", "departure_datetime", "sightseeing_start_date", "sightseeing_end_date"}:
            text = str(value)
        else:
            text = "行程"
        evidence.append(
            StrictFieldEvidenceDTO(
                field_path=RequirementFieldPath(ATTR_TO_PATH[name]),
                text=text,
            )
        )
    return strict_dto(**values, evidence=evidence)


def _real_turn(state, *, intent: RequirementIntent, message: str) -> RequirementTurnInput:
    return RequirementTurnInput(
        schema_version="1.0",
        turn_id=UUID(int=0),
        user_message=message,
        session_date=date(2026, 8, 17),
        timezone="Asia/Shanghai",
        intent=intent,
        current=state.requirement_state,
    )


def test_router_has_exact_five_outcome_destinations():
    state = make_initial_state()
    outcomes = (
        NeedClarification(
            requirement=state.requirement_state.requirement,
            metadata=state.requirement_state.metadata,
            issues=(),
            message="m",
        ),
        NeedConfirmation(
            requirement=state.requirement_state.requirement,
            metadata=state.requirement_state.metadata,
            summary="s",
        ),
        RequirementInvalidated(
            changed_fields=(),
            before_digest="a",
            after_digest="b",
            reason="r",
            summary="s",
        ),
        ConfirmedTravelRequirement(
            schema_version="1.0",
            requirement=state.requirement_state.requirement,
            metadata_without_raw_evidence=state.requirement_state.metadata,
            confirmation_summary_digest="d",
        ),
        RequirementExtractionFailed(code=RequirementErrorCode.INPUT_TOO_LONG),
    )
    assert [_destination_for_outcome(outcome) for outcome in outcomes] == [
        "await_clarification",
        "await_confirmation",
        "await_confirmation",
        END,
        END,
    ]


def test_builder_has_only_start_static_edge_and_no_conditional_edges():
    state = make_initial_state()
    service = ScriptedRequirementDomainService()
    sink = RecordingRequirementSubgraphSignalSink()
    factory = PerRunBudgetManagerFactory(lambda _run_id: object())
    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=factory,
        signal_sink=sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=1),
    )
    graph = build_requirement_subgraph(
        coordinator=coordinator,
        checkpointer=InMemorySaver(serde=RequirementCheckpointSerializer()),
    )
    assert graph.builder.edges == {(START, "process_initial_turn")}
    assert graph.builder.branches == {}
    assert set(graph.builder.nodes) == {
        "process_initial_turn",
        "await_clarification",
        "await_confirmation",
    }
    assert state.run_id not in factory.created


@pytest.mark.asyncio
async def test_real_graph_pause_resume_emits_replay_before_budget_and_domain():
    initial = make_initial_state()
    first = make_need_clarification_transition(initial)
    second = make_extraction_failed_transition(initial)
    service = ScriptedRequirementDomainService(process_results=[first, second])
    runtime, sink, factory = _runtime(service)

    paused = await runtime.start(state=initial, turn=make_turn(initial))
    envelope = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(paused.state)),
    )
    failed = await runtime.resume(run_id=initial.run_id, envelope=envelope)

    assert failed.result_type == "domain_failed"
    assert len(service.process_calls) == 2
    assert [type(signal) for signal in sink.calls] == [
        type(sink.calls[0]),
        type(sink.calls[1]),
        NodeReplayed,
        BudgetConsumed,
    ]
    assert isinstance(sink.calls[2], NodeReplayed)
    assert isinstance(sink.calls[3], BudgetConsumed)


@pytest.mark.asyncio
async def test_resume_reentry_keeps_action_until_interrupt_sentinel_and_orders_effects(
    monkeypatch,
):
    initial = make_initial_state()
    first = make_need_clarification_transition(initial)
    second = make_extraction_failed_transition(initial)
    service = ScriptedRequirementDomainService(process_results=[first, second])
    runtime, _sink, _factory = _runtime(service)
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    envelope = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(paused.state)),
    )
    coordinator = runtime._coordinator
    events: list[tuple[str, bool]] = []

    original_ensure = coordinator._ensure_current_pause
    original_take_resume = coordinator._take_resume
    original_take_pause = coordinator._take_pause_for_replay
    original_replayed = coordinator._node_replayed_operation
    original_consume = coordinator._consume_budget
    original_process = service.process_turn
    original_interrupt = graph_module.interrupt

    def wrapped_ensure(*, state, interrupt):
        events.append(("ensure", coordinator._current_record().action_slot is not None))
        return original_ensure(state=state, interrupt=interrupt)

    def wrapped_take_resume():
        events.append(("take_before", coordinator._current_record().action_slot is not None))
        result = original_take_resume()
        events.append(("take_after", coordinator._current_record().action_slot is not None))
        return result

    def wrapped_take_pause():
        events.append(("take_pause", coordinator._current_record().action_slot is not None))
        return original_take_pause()

    def wrapped_interrupt(value):
        events.append(("interrupt", coordinator._current_record().action_slot is not None))
        result = original_interrupt(value)
        if result == _REQUIREMENT_RESUME_SENTINEL:
            events.append(("sentinel", coordinator._current_record().action_slot is not None))
        return result

    def wrapped_replayed():
        events.append(("replayed", coordinator._current_record().action_slot is not None))
        return original_replayed()

    async def wrapped_consume(request):
        events.append(("budget", coordinator._current_record().action_slot is not None))
        return await original_consume(request)

    async def wrapped_process(turn):
        events.append(("domain", coordinator._current_record().action_slot is not None))
        return await original_process(turn)

    monkeypatch.setattr(coordinator, "_ensure_current_pause", wrapped_ensure)
    monkeypatch.setattr(coordinator, "_take_resume", wrapped_take_resume)
    monkeypatch.setattr(coordinator, "_take_pause_for_replay", wrapped_take_pause)
    monkeypatch.setattr(coordinator, "_node_replayed_operation", wrapped_replayed)
    monkeypatch.setattr(coordinator, "_consume_budget", wrapped_consume)
    monkeypatch.setattr(service, "process_turn", wrapped_process)
    monkeypatch.setattr(graph_module, "interrupt", wrapped_interrupt)

    result = await runtime.resume(run_id=initial.run_id, envelope=envelope)

    assert result.result_type == "domain_failed"
    names = [name for name, _has_action in events]
    assert names == [
        "ensure",
        "interrupt",
        "sentinel",
        "take_before",
        "take_after",
        "take_pause",
        "replayed",
        "budget",
        "domain",
    ]
    assert events[0][1] is True
    assert events[1][1] is True
    assert events[2][1] is True
    assert events[3][1] is True
    assert events[4][1] is False


@pytest.mark.asyncio
async def test_confirmation_accept_has_no_budget_and_reaches_confirmed():
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_confirmation_transition(initial)],
        confirm_results=[make_confirmed_transition(initial)],
    )
    runtime, sink, factory = _runtime(service)
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    envelope = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementConfirmationResume(
            action={"decision": "accept", "revision_text": None},  # type: ignore[arg-type]
        ),
    )
    result = await runtime.resume(run_id=initial.run_id, envelope=envelope)
    assert isinstance(result, RequirementSubgraphConfirmed)
    assert len(service.confirm_calls) == 1
    assert all(type(signal).__name__ != "BudgetConsumed" for signal in sink.calls)


@pytest.mark.asyncio
async def test_rejected_budget_returns_without_second_interrupt():
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_clarification_transition(initial)],
    )
    runtime, sink, factory = _runtime(service, budget_limit=0)
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    envelope = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(paused.state)),
    )
    result = await runtime.resume(run_id=initial.run_id, envelope=envelope)
    assert isinstance(result, RequirementSubgraphBudgetExceeded)
    assert len(service.process_calls) == 1
    assert [type(signal).__name__ for signal in sink.calls].count("BudgetExceeded") == 1
    assert result.state.delivery_status.value == "in_progress"
    snapshot = await runtime._graph.aget_state(
        {"configurable": {"thread_id": str(initial.run_id)}}
    )
    assert snapshot.next == ()
    assert snapshot.tasks == ()
    assert result.state.requirement_outcome == paused.state.requirement_outcome
    assert result.state.terminal_code is None
    signal_count = len(sink.calls)
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        await runtime.resume(run_id=initial.run_id, envelope=envelope)
    assert len(service.process_calls) == 1
    assert len(sink.calls) == signal_count


@pytest.mark.asyncio
async def test_real_module01_service_covers_clarification_confirmation_revise_and_accept():
    complete_values = {
        "destination": "杭州",
        "arrival_hub_name": "杭州东站",
        "arrival_datetime": "2026-08-17 09:00",
        "departure_hub_name": "杭州东站",
        "departure_datetime": "2026-08-19 18:00",
        "sightseeing_start_date": "2026-08-17",
        "sightseeing_end_date": "2026-08-19",
        "sightseeing_days": 3,
        "budget_total_cny": 5000.0,
        "adults_count": 2,
        "children_count": 0,
        "children_ages": [],
        "seniors_count": 0,
        "room_count": 1,
        "room_type_requirements": "标准间",
        "mobility_constraints": "无",
        "dietary_restrictions": "无",
        "agent_decides_interests": True,
        "agent_decides_pace": True,
    }
    extractor = RecordingRequirementExtractor(
        [
            _real_patch(destination="杭州"),
            _real_patch(**complete_values),
            _real_patch(destination="上海", is_correction=True),
        ]
    )
    service = RequirementDomainService(
        extractor=extractor,
        evidence_sink=InMemoryEvidenceSink(),
        clock=FixedClock(date(2026, 8, 17)),
    )
    runtime, _sink, _factory = _runtime(service)
    initial = make_initial_state()

    first = await runtime.start(
        state=initial,
        turn=_real_turn(initial, intent=RequirementIntent.INITIAL, message="杭州"),
    )
    assert first.result_type == "paused"
    assert type(first.state.requirement_outcome) is NeedClarification

    second = await runtime.resume(
        run_id=initial.run_id,
        envelope=RequirementResumeEnvelope(
            pause_token=first.pause.pause_token,
            resume=RequirementClarificationResume(
                turn=_real_turn(
                    first.state,
                    intent=RequirementIntent.SUPPLEMENT,
                    message="杭州 杭州东站 2026-08-17 09:00 2026-08-19 18:00 2026-08-17 2026-08-19 5000 行程",
                )
            ),
        ),
    )
    assert second.result_type == "paused"
    assert type(second.state.requirement_outcome) is NeedConfirmation

    revised = await runtime.resume(
        run_id=initial.run_id,
        envelope=RequirementResumeEnvelope(
            pause_token=second.pause.pause_token,
            resume=RequirementConfirmationResume(
                action=RequirementConfirmation(
                    decision="revise",
                    revision_text="改为上海",
                )
            ),
        ),
    )
    assert revised.result_type == "paused"
    assert type(revised.state.requirement_outcome) in (
        NeedConfirmation,
        RequirementInvalidated,
    )
    assert revised.result_type != "confirmed"

    confirmed = await runtime.resume(
        run_id=initial.run_id,
        envelope=RequirementResumeEnvelope(
            pause_token=revised.pause.pause_token,
            resume=RequirementConfirmationResume(
                action=RequirementConfirmation(decision="accept", revision_text=None),
            ),
        ),
    )
    assert confirmed.result_type == "confirmed"
    assert extractor.sessions[0].extract_calls == 1
    assert len(extractor.calls) == 3


@pytest.mark.asyncio
async def test_real_module01_extraction_failure_routes_to_domain_failed():
    bad_patch = strict_dto(destination="杭州", evidence=[])
    extractor = RecordingRequirementExtractor([bad_patch, bad_patch])
    service = RequirementDomainService(
        extractor=extractor,
        evidence_sink=InMemoryEvidenceSink(),
        clock=FixedClock(date(2026, 8, 17)),
    )
    runtime, _sink, _factory = _runtime(service)
    initial = make_initial_state()
    result = await runtime.start(
        state=initial,
        turn=_real_turn(
            initial,
            intent=RequirementIntent.INITIAL,
            message="杭州",
        ),
    )
    assert result.result_type == "domain_failed"
    assert result.state.requirement_outcome.code is RequirementErrorCode.EVIDENCE_MISSING


def test_graph_binding_and_transition_defensive_contracts():
    state = make_initial_state()
    service = ScriptedRequirementDomainService()
    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=PerRunBudgetManagerFactory(lambda _run_id: object()),
        signal_sink=RecordingRequirementSubgraphSignalSink(),
        limits=RequirementSubgraphCoordinatorLimits(max_runs=1),
    )
    graph = build_requirement_subgraph(
        coordinator=coordinator,
        checkpointer=InMemorySaver(serde=RequirementCheckpointSerializer()),
    )
    class RuntimeMarker:
        pass

    marker = RuntimeMarker()
    assert graph_module._binding_for(marker) is None
    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._claim_runtime(RuntimeMarker(), marker)  # type: ignore[arg-type]
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._claim_runtime(graph, object())
    assert caught.value.args == ()

    graph_module._claim_runtime(graph, marker)
    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._claim_runtime(graph, RuntimeMarker())
    assert caught.value.args == ()

    transition = make_need_clarification_transition(state)
    assert graph_module._fresh_transition(transition) == transition
    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._fresh_transition(object())
    assert caught.value.args == ()


def test_graph_private_state_construction_maps_invalid_outcomes_and_fresh_failures(monkeypatch):
    state = make_initial_state()
    transition = make_need_clarification_transition(state)
    next_state = graph_module._state_after_transition(state, transition)
    assert next_state.delivery_status.value == "awaiting_user"
    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._destination_for_outcome(object())  # type: ignore[arg-type]
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._state_after_transition(
            state,
            SimpleNamespace(outcome=object(), state=state.requirement_state),  # type: ignore[arg-type]
        )
    assert caught.value.args == ()

    def fail_fresh(_model_type, _value):
        raise ValueError("invalid fresh state")

    monkeypatch.setattr(graph_module, "_fresh_model", fail_fresh)
    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._state_after_transition(state, transition)
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._budget_rejected_state(state)
    assert caught.value.args == ()


def test_graph_pause_and_result_helpers_fail_closed_on_invalid_shapes(monkeypatch):
    state = make_initial_state()
    waiting = graph_module._state_after_transition(
        state,
        make_need_clarification_transition(state),
    )
    interrupt_dto = graph_module._interrupt_for_state(waiting)
    assert graph_module._pause_payload(interrupt_dto)["interrupt_type"] == "clarification"

    class WrongTree:
        def model_dump(self, **_kwargs):
            return []

    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._pause_payload(WrongTree())  # type: ignore[arg-type]
    assert caught.value.args == ()

    class BrokenTree:
        def model_dump(self, **_kwargs):
            raise ValueError("broken")

    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._pause_payload(BrokenTree())  # type: ignore[arg-type]
    assert caught.value.args == ()

    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._interrupt_for_state(state)
    assert caught.value.args == ()

    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._terminal_result_command(object(), waiting)  # type: ignore[arg-type]
    assert caught.value.args == ()

    monkeypatch.setattr(graph_module, "_fresh_model", lambda *_args: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(RequirementSubgraphStateError) as caught:
        graph_module._fresh_transition(make_need_clarification_transition(state))
    assert caught.value.args == ()


@pytest.mark.asyncio
async def test_graph_replay_and_wait_nodes_reject_protocol_distortions(monkeypatch):
    state = make_initial_state()
    waiting = graph_module._state_after_transition(
        state,
        make_need_clarification_transition(state),
    )
    interrupt_dto = graph_module._interrupt_for_state(waiting)

    class ReplayStub:
        def _ensure_current_pause(self, *, state, interrupt):
            return "a" * 32, "b" * 32, interrupt

        def _take_resume(self):
            return None

        def _take_pause_for_replay(self):
            return "b" * 32, interrupt_dto

        def _node_replayed_operation(self):
            return None

    stub = ReplayStub()
    monkeypatch.setattr(graph_module, "interrupt", lambda _value: "wrong")
    with pytest.raises(RequirementSubgraphStateError) as caught:
        await graph_module._replay_pause(waiting, coordinator=stub)  # type: ignore[arg-type]
    assert caught.value.args == ()

    monkeypatch.setattr(graph_module, "interrupt", lambda _value: _REQUIREMENT_RESUME_SENTINEL)
    stub._take_pause_for_replay = lambda: ("c" * 32, interrupt_dto)
    with pytest.raises(RequirementSubgraphStateError) as caught:
        await graph_module._replay_pause(waiting, coordinator=stub)  # type: ignore[arg-type]
    assert caught.value.args == ()

    class WaitStub:
        async def _consume_budget(self, _request):
            raise AssertionError("budget must not be called")

    async def no_resume(_state, *, coordinator):
        return "b" * 32, interrupt_dto, None

    monkeypatch.setattr(graph_module, "_replay_pause", no_resume)
    with pytest.raises(RequirementSubgraphStateError) as caught:
        await graph_module._await_clarification(waiting, coordinator=WaitStub())  # type: ignore[arg-type]
    assert caught.value.args == ()

    async def wrong_resume(_state, *, coordinator):
        return (
            "b" * 32,
            interrupt_dto,
            RequirementResumeEnvelope.model_construct(
                resume=RequirementConfirmationResume.model_construct()
            ),
        )

    monkeypatch.setattr(graph_module, "_replay_pause", wrong_resume)
    with pytest.raises(RequirementSubgraphStateError) as caught:
        await graph_module._await_clarification(waiting, coordinator=WaitStub())  # type: ignore[arg-type]
    assert caught.value.args == ()


def test_graph_builder_requires_exact_coordinator():
    with pytest.raises(RequirementSubgraphStateError) as caught:
        build_requirement_subgraph(coordinator=object(), checkpointer=None)  # type: ignore[arg-type]
    assert caught.value.args == ()


@pytest.mark.asyncio
async def test_graph_wait_nodes_reject_wrong_resume_and_budget_outcome_shapes():
    initial = make_initial_state()
    clarification_state = graph_module._state_after_transition(
        initial,
        make_need_clarification_transition(initial),
    )
    confirmation_state = graph_module._state_after_transition(
        initial,
        make_need_confirmation_transition(initial),
    )
    clarification_interrupt = graph_module._interrupt_for_state(clarification_state)
    confirmation_interrupt = graph_module._interrupt_for_state(confirmation_state)

    async def no_resume(_state, *, coordinator):
        return "b" * 32, confirmation_interrupt, None

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(graph_module, "_replay_pause", no_resume)

        class Stub:
            async def _consume_budget(self, _request):
                return graph_module.WorkflowBudgetConsumed.model_construct(disposition="wrong")

            def _set_latest_state(self, _state):
                return None

            def _set_local_result(self, _result):
                return None

        async def bad_clarification_resume(_state, *, coordinator):
            return (
                "b" * 32,
                clarification_interrupt,
                RequirementResumeEnvelope(
                    pause_token="a" * 32,
                    resume=RequirementClarificationResume(turn=make_turn(clarification_state)),
                ),
            )

        monkeypatch.setattr(graph_module, "_replay_pause", bad_clarification_resume)
        with pytest.raises(RequirementSubgraphStateError) as caught:
            await graph_module._await_clarification(clarification_state, coordinator=Stub())  # type: ignore[arg-type]
        assert caught.value.args == ()

        with pytest.raises(RequirementSubgraphStateError) as caught:
            await graph_module._await_confirmation(confirmation_state, coordinator=Stub())  # type: ignore[arg-type]
        assert caught.value.args == ()

        async def clarification_resume(_state, *, coordinator):
            return (
                "b" * 32,
                confirmation_interrupt,
                RequirementResumeEnvelope(
                    pause_token="a" * 32,
                    resume=RequirementClarificationResume(turn=make_turn(confirmation_state)),
                ),
            )

        monkeypatch.setattr(graph_module, "_replay_pause", clarification_resume)
        with pytest.raises(RequirementSubgraphStateError) as caught:
            await graph_module._await_confirmation(confirmation_state, coordinator=Stub())  # type: ignore[arg-type]
        assert caught.value.args == ()

        async def invalid_action(_state, *, coordinator):
            return (
                "b" * 32,
                confirmation_interrupt,
                RequirementResumeEnvelope.model_construct(
                    resume=RequirementConfirmationResume.model_construct(action=object())
                ),
            )

        monkeypatch.setattr(graph_module, "_replay_pause", invalid_action)
        with pytest.raises(RequirementSubgraphStateError) as caught:
            await graph_module._await_confirmation(confirmation_state, coordinator=Stub())  # type: ignore[arg-type]
        assert caught.value.args == ()

        async def revise_with_bad_budget(_state, *, coordinator):
            return (
                "b" * 32,
                confirmation_interrupt,
                RequirementResumeEnvelope(
                    pause_token="a" * 32,
                    resume=RequirementConfirmationResume(
                        action=RequirementConfirmation(decision="revise", revision_text="改"),
                    ),
                ),
            )

        monkeypatch.setattr(graph_module, "_replay_pause", revise_with_bad_budget)
        with pytest.raises(RequirementSubgraphStateError) as caught:
            await graph_module._await_confirmation(confirmation_state, coordinator=Stub())  # type: ignore[arg-type]
        assert caught.value.args == ()

        operation = OperationId(
            run_id=confirmation_state.run_id,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="clarification_round_" + "b" * 32,
        )
        rejected = graph_module.WorkflowBudgetExceeded(
            disposition="rejected",
            operation_id=operation,
            key=graph_module.REQUIREMENTS_CLARIFICATION_ROUND_KEY,
            amount=1,
            snapshot=ExecutionBudgetSnapshot(
                schema_version="1.0",
                run_id=confirmation_state.run_id,
                key=graph_module.REQUIREMENTS_CLARIFICATION_ROUND_KEY,
                limit=0,
                consumed=0,
                remaining=0,
            ),
        )

        class RejectedStub(Stub):
            async def _consume_budget(self, _request):
                return rejected

        result = await graph_module._await_confirmation(
            confirmation_state,
            coordinator=RejectedStub(),  # type: ignore[arg-type]
        )
        assert result.goto == END
    finally:
        monkeypatch.undo()
