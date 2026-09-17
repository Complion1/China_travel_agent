from __future__ import annotations

import asyncio
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import TypeAdapter

import app.requirement_subgraph.coordinator as coordinator_module

from app.requirement_subgraph import (
    RequirementCheckpointSerializer,
    RequirementClarificationResume,
    RequirementConfirmationResume,
    RequirementSubgraphCoordinator,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphResumeRejectedError,
    RequirementSubgraphStateError,
    build_requirement_subgraph,
    create_requirement_subgraph_embedded_driver,
)
from app.requirement_subgraph.models import RequirementResumeEnvelope
from app.requirements import RequirementConfirmation
from app.workflow_contracts import (
    DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    InMemoryExecutionBudgetManager,
    OperationResultIndeterminateError,
    TravelSessionState,
)
from tests.requirement_subgraph_fakes import (
    PerRunBudgetManagerFactory,
    RecordingRequirementSubgraphSignalSink,
    ScriptedRequirementDomainService,
    SequenceSecretHex,
    make_extraction_failed_transition,
    make_confirmed_transition,
    make_initial_state,
    make_need_clarification_transition,
    make_need_confirmation_transition,
    make_turn,
)
from app.requirement_subgraph.models import REQUIREMENTS_CLARIFICATION_ROUND_KEY


def _parent_graph(
    state: TravelSessionState,
    service,
    counter: list[int],
    *,
    budget_limit: int = 6,
    route_results: list[str] | None = None,
    route_targets: dict[str, list[int]] | None = None,
    manager_canary: str = "manager-canary",
    manager_canary_seen: list[str] | None = None,
):
    sink = RecordingRequirementSubgraphSignalSink()

    def manager_factory(run_id):
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
        manager = InMemoryExecutionBudgetManager(
            plan=plan,
            limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
        )

        class CanaryBudgetManager:
            def __init__(self):
                self.canary = manager_canary

            async def consume(self, request):
                if manager_canary_seen is not None:
                    manager_canary_seen.append(self.canary)
                return await manager.consume(request)

            async def get_snapshot(self, *, key):
                return await manager.get_snapshot(key=key)

            async def get_all_snapshots(self):
                return await manager.get_all_snapshots()

        return CanaryBudgetManager()

    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=manager_factory,
        signal_sink=sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    child = build_requirement_subgraph(coordinator=coordinator, checkpointer=None)

    def continuation(value):
        counter.append(1)
        return value

    def route(value):
        if route_results is None:
            return Command(update=value, goto="continuation")
        result = coordinator.resolve_result(state=value, interrupt_value=None)
        route_results.append(result.result_type)
        target = {
            "confirmed": "confirmed_target",
            "domain_failed": "domain_failed_target",
            "budget_exceeded": "budget_exceeded_target",
        }.get(result.result_type)
        if target is None:
            raise AssertionError(result.result_type)
        return Command(update=value, goto=target)

    def target(name):
        def _target(value):
            if route_targets is not None:
                route_targets[name].append(1)
            return value

        return _target

    builder = StateGraph(TravelSessionState)
    builder.add_node("child", child)
    builder.add_node(
        "route",
        route,
        destinations=(
            "confirmed_target",
            "domain_failed_target",
            "budget_exceeded_target",
            "continuation",
        ),
    )
    builder.add_node("confirmed_target", target("confirmed"))
    builder.add_node("domain_failed_target", target("domain_failed"))
    builder.add_node("budget_exceeded_target", target("budget_exceeded"))
    builder.add_node("continuation", continuation)
    builder.add_edge(START, "child")
    builder.add_edge("child", "route")
    builder.add_edge("confirmed_target", "continuation")
    builder.add_edge("domain_failed_target", "continuation")
    builder.add_edge("budget_exceeded_target", "continuation")
    builder.add_edge("continuation", END)
    parent_saver = InMemorySaver(serde=RequirementCheckpointSerializer())
    return (
        coordinator,
        child,
        builder.compile(checkpointer=parent_saver),
        sink,
    )


async def _child_checkpoint(child, task):
    if not hasattr(task.state, "values"):
        raise AssertionError("parent did not expose child snapshot")
    values = task.state.values
    if type(values) is dict:
        values = TypeAdapter(TravelSessionState).validate_python(values, strict=True)
    return values, task.interrupts[0].value


def _durable_parent_graph(driver, saver, resolved_results):
    def route(value):
        result = driver.resolve_result(state=value, interrupt_value=None)
        resolved_results.append(result.result_type)
        return value

    builder = StateGraph(TravelSessionState)
    builder.add_node("child", driver.graph)
    builder.add_node("route", route)
    builder.add_edge(START, "child")
    builder.add_edge("child", "route")
    builder.add_edge("route", END)
    return builder.compile(checkpointer=saver)


def _checkpoint_wire_payloads(value):
    if type(value) is tuple and len(value) == 2 and type(value[0]) is str and type(value[1]) is bytes:
        yield value[0], value[1]
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _checkpoint_wire_payloads(item)
    elif type(value) in (list, tuple):
        for item in value:
            yield from _checkpoint_wire_payloads(item)


def _all_checkpoint_wire_payloads(saver):
    for container in (saver.storage, saver.blobs, saver.writes):
        yield from _checkpoint_wire_payloads(container)


@pytest.mark.asyncio
async def test_parent_saver_is_inherited_and_public_control_drives_child_lifecycle(
    monkeypatch,
):
    secret_values = SequenceSecretHex(("a" * 32, "b" * 32))
    monkeypatch.setattr(coordinator_module, "_new_secret_hex", secret_values)
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_confirmation_transition(initial)],
        confirm_results=[make_confirmed_transition(initial)],
    )
    continuation_calls: list[int] = []
    manager_canary_seen: list[str] = []
    coordinator, child, parent, sink = _parent_graph(
        initial,
        service,
        continuation_calls,
        manager_canary_seen=manager_canary_seen,
    )
    config = {"configurable": {"thread_id": str(initial.run_id)}}

    async with coordinator.start_operation(
        state=initial,
        turn=make_turn(initial, user_message="turn-canary"),
    ) as entered:
        first_output = await parent.ainvoke(
            entered,
            config=config,
        )
        first_snapshot = await parent.aget_state(config, subgraphs=True)
        history = [item async for item in parent.aget_state_history(config)]
        assert first_snapshot.next == ("child",)
        assert history
        child_state, interrupt_value = await _child_checkpoint(child, first_snapshot.tasks[0])
        paused = coordinator.resolve_result(
            state=child_state,
            interrupt_value=interrupt_value,
        )
        assert paused.result_type == "paused"
        assert first_output["delivery_status"].value == "in_progress"
        payloads = tuple(_all_checkpoint_wire_payloads(parent.checkpointer))
        blob = b"".join(payload for _tag, payload in payloads)
        wire_payloads = tuple(_all_checkpoint_wire_payloads(parent.checkpointer))
        allowed_tags = {
            "requirement_subgraph.travel_session_state.2.0",
            "requirement_subgraph.requirement_domain_state.1.0",
            "requirement_subgraph.requirement_domain_outcome.1.0",
            "requirement_subgraph.terminal_outcome.1.0",
            "requirement_subgraph.artifact_ref_tuple.1.0",
            "requirement_subgraph.interrupt_tuple.1.0",
            "null",
            "bytes",
            "bytearray",
            "json",
            "msgpack",
            "empty",
        }
        forbidden_fields = (
            b"user_message",
            b"revision_text",
            b"pause_token",
            b"operation_nonce",
            b"RequirementResumeEnvelope",
            b"RequirementTurnInput",
            b"RequirementConfirmation",
        )
        assert all(tag in allowed_tags for tag, _payload in wire_payloads), {
            tag for tag, _payload in wire_payloads if tag not in allowed_tags
        }
        assert all(
            field not in payload
            for _tag, payload in wire_payloads
            for field in forbidden_fields
        )
        for canary in (
            b"turn-canary",
            paused.pause.pause_token.encode(),
            b"b" * 32,
            b"revision-canary",
            b"manager-canary",
        ):
            assert canary not in blob
        assert paused.pause.pause_token == "a" * 32
        assert secret_values.calls == 2

    envelope = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementConfirmationResume(
            action=RequirementConfirmation(decision="revise", revision_text="revision-canary"),
        ),
    )
    async with coordinator.resume_operation(state=paused.state, envelope=envelope) as command:
        assert isinstance(command, Command)
        assert command.resume == "requirement_resume_v1"
        await parent.ainvoke(command, config=config)
        resumed_blob = b"".join(
            payload for _tag, payload in _all_checkpoint_wire_payloads(parent.checkpointer)
        )
        assert b"requirement_resume_v1" in resumed_blob
        assert all(
            canary not in resumed_blob
            for canary in (
                b"turn-canary",
                b"a" * 32,
                b"b" * 32,
                b"revision-canary",
                b"manager-canary",
            )
        )
        second_snapshot = await parent.aget_state(config)
        assert second_snapshot.next == ()
        confirmed_state = second_snapshot.values
        if type(confirmed_state) is dict:
            confirmed_state = TypeAdapter(TravelSessionState).validate_python(
                confirmed_state,
                strict=True,
            )
        result = coordinator.resolve_result(state=confirmed_state, interrupt_value=None)
        assert result.result_type == "confirmed"

    assert continuation_calls == [1]
    assert len(service.process_calls) == 1
    assert len(service.confirm_calls) == 1
    assert manager_canary_seen == ["manager-canary"]
    assert parent.checkpointer is not child.checkpointer
    assert type(parent.checkpointer.serde) is RequirementCheckpointSerializer
    assert type(child.checkpointer) is type(None)


@pytest.mark.asyncio
async def test_embedded_driver_rehydrates_pause_with_a_fresh_driver_simulation():
    initial = make_initial_state()
    shared_saver = InMemorySaver(serde=RequirementCheckpointSerializer())

    def manager_factory(run_id):
        return InMemoryExecutionBudgetManager(
            plan=ExecutionBudgetPlan(
                schema_version="1.0",
                run_id=run_id,
                limits=(
                    ExecutionBudgetLimit(
                        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
                        limit=6,
                    ),
                ),
            ),
            limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
        )

    first_sink = RecordingRequirementSubgraphSignalSink()
    first_service = ScriptedRequirementDomainService(
        process_results=[make_need_confirmation_transition(initial)],
    )
    first_driver = create_requirement_subgraph_embedded_driver(
        domain_service=first_service,
        execution_budget_manager_factory=manager_factory,
        signal_sink=first_sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    first_results: list[str] = []
    first_parent = _durable_parent_graph(first_driver, shared_saver, first_results)
    config = {"configurable": {"thread_id": str(initial.run_id)}}

    async with first_driver.start_operation(
        state=initial,
        turn=make_turn(initial),
    ) as entered:
        await first_parent.ainvoke(entered, config=config)
        snapshot = await first_parent.aget_state(config, subgraphs=True)
        paused_state, interrupt_value = await _child_checkpoint(
            first_driver.graph,
            snapshot.tasks[0],
        )
        paused = first_driver.resolve_result(
            state=paused_state,
            interrupt_value=interrupt_value,
        )

    first_driver.verify_pause_token(
        durable_pause=paused.durable_pause,
        pause_token=paused.pause.pause_token,
    )
    process_count_before_token_rejection = len(first_service.process_calls)
    signal_count_before_token_rejection = len(first_sink.calls)
    with pytest.raises(RequirementSubgraphResumeRejectedError) as caught:
        first_driver.verify_pause_token(
            durable_pause=paused.durable_pause.model_copy(
                update={"operation_nonce": paused.pause.pause_token},
            ),
            pause_token=paused.pause.pause_token,
        )
    assert caught.value.args == ()
    assert len(first_service.process_calls) == process_count_before_token_rejection
    assert len(first_sink.calls) == signal_count_before_token_rejection
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        first_driver.verify_pause_token(
            durable_pause=paused.durable_pause,
            pause_token="f" * 32,
        )
    first_process_call_count = len(first_service.process_calls)
    del first_parent, first_driver, first_service, first_sink

    second_sink = RecordingRequirementSubgraphSignalSink()
    second_service = ScriptedRequirementDomainService(
        confirm_results=[make_confirmed_transition(initial)],
    )
    second_driver = create_requirement_subgraph_embedded_driver(
        domain_service=second_service,
        execution_budget_manager_factory=manager_factory,
        signal_sink=second_sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    second_results: list[str] = []
    second_parent = _durable_parent_graph(second_driver, shared_saver, second_results)
    accepted_resume = RequirementConfirmationResume(
        action=RequirementConfirmation(
            decision="revise",
            revision_text="请调整需求",
        ),
    )

    tampered_pause = paused.durable_pause.model_copy(
        update={"state_digest": "sha256:" + "0" * 64},
    )
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        async with second_driver.resume_accepted_operation(
            state=paused.state,
            durable_pause=tampered_pause,
            resume=accepted_resume,
        ):
            raise AssertionError("tampered durable pause entered operation scope")
    assert second_sink.calls == []
    assert second_service.process_calls == []
    assert second_service.confirm_calls == []

    async with second_driver.resume_accepted_operation(
        state=paused.state,
        durable_pause=paused.durable_pause,
        resume=accepted_resume,
    ) as command:
        await second_parent.ainvoke(command, config=config)

    assert second_results == ["confirmed"]
    assert first_results == []
    assert first_process_call_count == 1
    assert second_service.process_calls == []
    assert len(second_service.confirm_calls) == 1
    replayed = [
        signal
        for signal in second_sink.calls
        if signal.signal_type == "node_replayed"
    ]
    assert len(replayed) == 1
    assert replayed[0].operation_id.logical_step == (
        f"requirement_pause_{paused.durable_pause.operation_nonce}"
    )
    budgeted = [
        signal
        for signal in second_sink.calls
        if signal.signal_type == "budget_consumed"
    ]
    assert len(budgeted) == 1
    assert budgeted[0].operation_id.logical_step == (
        f"clarification_round_{paused.durable_pause.operation_nonce}"
    )
    assert budgeted[0].amount == 1


@pytest.mark.asyncio
async def test_fresh_driver_replay_uses_durable_nonce_and_fails_indeterminate_before_domain_call():
    initial = make_initial_state()
    shared_saver = InMemorySaver(serde=RequirementCheckpointSerializer())
    managers: dict[object, InMemoryExecutionBudgetManager] = {}

    def manager_factory(run_id):
        manager = managers.get(run_id)
        if manager is None:
            manager = InMemoryExecutionBudgetManager(
                plan=ExecutionBudgetPlan(
                    schema_version="1.0",
                    run_id=run_id,
                    limits=(
                        ExecutionBudgetLimit(
                            key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
                            limit=6,
                        ),
                    ),
                ),
                limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
            )
            managers[run_id] = manager
        return manager

    first_driver = create_requirement_subgraph_embedded_driver(
        domain_service=ScriptedRequirementDomainService(
            process_results=[make_need_confirmation_transition(initial)],
        ),
        execution_budget_manager_factory=manager_factory,
        signal_sink=RecordingRequirementSubgraphSignalSink(),
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    first_parent = _durable_parent_graph(first_driver, shared_saver, [])
    config = {"configurable": {"thread_id": str(initial.run_id)}}

    async with first_driver.start_operation(
        state=initial,
        turn=make_turn(initial),
    ) as entered:
        await first_parent.ainvoke(entered, config=config)
        snapshot = await first_parent.aget_state(config, subgraphs=True)
        paused_state, interrupt_value = await _child_checkpoint(
            first_driver.graph,
            snapshot.tasks[0],
        )
        paused = first_driver.resolve_result(
            state=paused_state,
            interrupt_value=interrupt_value,
        )
    del first_parent, first_driver

    accepted_resume = RequirementConfirmationResume(
        action=RequirementConfirmation(
            decision="revise",
            revision_text="请调整需求",
        ),
    )
    second_service = ScriptedRequirementDomainService(
        confirm_results=[make_confirmed_transition(initial)],
    )
    second_sink = RecordingRequirementSubgraphSignalSink(fail_on_call=3)
    second_driver = create_requirement_subgraph_embedded_driver(
        domain_service=second_service,
        execution_budget_manager_factory=manager_factory,
        signal_sink=second_sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    second_parent = _durable_parent_graph(second_driver, shared_saver, [])

    with pytest.raises(RuntimeError, match="sink failure"):
        async with second_driver.resume_accepted_operation(
            state=paused.state,
            durable_pause=paused.durable_pause,
            resume=accepted_resume,
        ) as command:
            await second_parent.ainvoke(command, config=config)

    assert second_service.process_calls == []
    assert second_service.confirm_calls == []
    assert [signal.signal_type for signal in second_sink.calls] == [
        "resume_accepted",
        "node_replayed",
    ]

    third_service = ScriptedRequirementDomainService(
        confirm_results=[make_confirmed_transition(initial)],
    )
    third_sink = RecordingRequirementSubgraphSignalSink()
    third_driver = create_requirement_subgraph_embedded_driver(
        domain_service=third_service,
        execution_budget_manager_factory=manager_factory,
        signal_sink=third_sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    third_parent = _durable_parent_graph(third_driver, shared_saver, [])

    with pytest.raises(OperationResultIndeterminateError) as caught:
        async with third_driver.resume_accepted_operation(
            state=paused.state,
            durable_pause=paused.durable_pause,
            resume=accepted_resume,
        ) as command:
            await third_parent.ainvoke(command, config=config)

    assert caught.value.args == ()
    assert third_service.process_calls == []
    assert third_service.confirm_calls == []
    replayed = [
        signal
        for signal in third_sink.calls
        if signal.signal_type == "node_replayed"
    ]
    assert len(replayed) == 1
    assert replayed[0].operation_id.logical_step == (
        f"requirement_pause_{paused.durable_pause.operation_nonce}"
    )
    assert all(
        signal.signal_type != "budget_consumed" for signal in third_sink.calls
    )


@pytest.mark.asyncio
async def test_operation_scope_missing_resolve_faults_and_original_body_error_is_preserved():
    initial = make_initial_state()
    service = ScriptedRequirementDomainService()
    coordinator, _child, _parent, _sink = _parent_graph(initial, service, [])
    with pytest.raises(RequirementSubgraphStateError):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            pass

    original = ValueError("body-original")
    second = make_initial_state()
    coordinator2, _child2, _parent2, _sink2 = _parent_graph(second, service, [])
    with pytest.raises(ValueError) as caught:
        async with coordinator2.start_operation(state=second, turn=make_turn(second)):
            raise original
    assert caught.value is original


@pytest.mark.asyncio
async def test_parent_operation_scope_drive_without_resolve_faults_and_double_resolve_faults():
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_confirmation_transition(initial)]
    )
    coordinator, child, parent, _sink = _parent_graph(initial, service, [])
    config = {"configurable": {"thread_id": str(initial.run_id)}}

    with pytest.raises(RequirementSubgraphStateError):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)) as entered:
            await parent.ainvoke(entered, config=config)

    second = make_initial_state()
    service2 = ScriptedRequirementDomainService(
        process_results=[make_need_confirmation_transition(second)]
    )
    coordinator2, child2, parent2, _sink2 = _parent_graph(second, service2, [])
    config2 = {"configurable": {"thread_id": str(second.run_id)}}
    with pytest.raises(RequirementSubgraphStateError):
        async with coordinator2.start_operation(state=second, turn=make_turn(second)) as entered:
            await parent2.ainvoke(entered, config=config2)
            snapshot = await parent2.aget_state(config2, subgraphs=True)
            child_state, interrupt_value = await _child_checkpoint(child2, snapshot.tasks[0])
            coordinator2.resolve_result(state=child_state, interrupt_value=interrupt_value)
            coordinator2.resolve_result(state=child_state, interrupt_value=interrupt_value)


@pytest.mark.asyncio
async def test_parent_operation_scope_preserves_body_exception_after_successful_resolve():
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_confirmation_transition(initial)]
    )
    coordinator, child, parent, _sink = _parent_graph(initial, service, [])
    config = {"configurable": {"thread_id": str(initial.run_id)}}
    original = ValueError("after-resolve-body")

    with pytest.raises(ValueError) as caught:
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)) as entered:
            await parent.ainvoke(entered, config=config)
            snapshot = await parent.aget_state(config, subgraphs=True)
            child_state, interrupt_value = await _child_checkpoint(child, snapshot.tasks[0])
            coordinator.resolve_result(state=child_state, interrupt_value=interrupt_value)
            raise original
    assert caught.value is original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("process_results", "budget_limit", "expected"),
    [
        (
            "domain_failed",
            6,
            "domain_failed",
        ),
        (
            "budget_exceeded",
            0,
            "budget_exceeded",
        ),
    ],
)
async def test_parent_layer_routes_child_domain_and_budget_results(
    process_results,
    budget_limit,
    expected,
):
    initial = make_initial_state()
    transition = (
        make_need_clarification_transition(initial)
        if process_results == "budget_exceeded"
        else make_extraction_failed_transition(initial)
    )
    service = ScriptedRequirementDomainService(process_results=[transition])
    continuation_calls: list[int] = []
    routed: list[str] = []
    target_calls = {"confirmed": [], "domain_failed": [], "budget_exceeded": []}
    coordinator, child, parent, _sink = _parent_graph(
        initial,
        service,
        continuation_calls,
        budget_limit=budget_limit,
        route_results=routed,
        route_targets=target_calls,
    )
    config = {"configurable": {"thread_id": str(initial.run_id)}}

    if process_results == "domain_failed":
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)) as entered:
            await parent.ainvoke(entered, config=config)
    else:
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)) as entered:
            await parent.ainvoke(entered, config=config)
            first_snapshot = await parent.aget_state(config, subgraphs=True)
            child_state, interrupt_value = await _child_checkpoint(child, first_snapshot.tasks[0])
            paused = coordinator.resolve_result(
                state=child_state,
                interrupt_value=interrupt_value,
            )
        async with coordinator.resume_operation(
            state=paused.state,
            envelope=RequirementResumeEnvelope(
                pause_token=paused.pause.pause_token,
                resume=(
                    RequirementClarificationResume(turn=make_turn(paused.state))
                    if process_results == "budget_exceeded"
                    else RequirementConfirmationResume(
                        action=RequirementConfirmation(decision="accept", revision_text=None),
                    )
                ),
            ),
        ) as command:
            await parent.ainvoke(command, config=config)

    assert routed == [expected]
    assert target_calls[expected] == [1]
    assert sum(len(calls) for calls in target_calls.values()) == 1
    assert continuation_calls == [1]


@pytest.mark.asyncio
async def test_parent_layer_routes_confirmed_result_to_its_own_target_once():
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_confirmation_transition(initial)],
        confirm_results=[make_confirmed_transition(initial)],
    )
    continuation_calls: list[int] = []
    routed: list[str] = []
    target_calls = {"confirmed": [], "domain_failed": [], "budget_exceeded": []}
    coordinator, child, parent, _sink = _parent_graph(
        initial,
        service,
        continuation_calls,
        route_results=routed,
        route_targets=target_calls,
    )
    config = {"configurable": {"thread_id": str(initial.run_id)}}

    async with coordinator.start_operation(state=initial, turn=make_turn(initial)) as entered:
        await parent.ainvoke(entered, config=config)
        snapshot = await parent.aget_state(config, subgraphs=True)
        child_state, interrupt_value = await _child_checkpoint(child, snapshot.tasks[0])
        paused = coordinator.resolve_result(
            state=child_state,
            interrupt_value=interrupt_value,
        )

    async with coordinator.resume_operation(
        state=paused.state,
        envelope=RequirementResumeEnvelope(
            pause_token=paused.pause.pause_token,
            resume=RequirementConfirmationResume(
                action=RequirementConfirmation(decision="accept", revision_text=None),
            ),
        ),
    ) as command:
        await parent.ainvoke(command, config=config)

    assert routed == ["confirmed"]
    assert target_calls == {"confirmed": [1], "domain_failed": [], "budget_exceeded": []}
    assert continuation_calls == [1]
