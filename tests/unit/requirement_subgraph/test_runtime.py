from __future__ import annotations

import asyncio
import gc
import weakref
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.requirement_subgraph import (
    RequirementCheckpointSerializer,
    RequirementSubgraphConfigurationError,
    RequirementSubgraphCoordinator,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphInputError,
    RequirementSubgraphResumeRejectedError,
    RequirementSubgraphRunFaultedError,
    RequirementSubgraphRuntime,
    RequirementClarificationResume,
    RequirementResumeEnvelope,
    build_requirement_subgraph,
    create_demo_requirement_subgraph_runtime,
)
from app.requirement_subgraph.errors import RequirementSubgraphStateError
from app.requirements import RequirementDomainService
from tests.requirement_subgraph_fakes import (
    PerRunBudgetManagerFactory,
    RecordingRequirementSubgraphSignalSink,
    ScriptedRequirementDomainService,
    ScriptedExecutionBudgetManager,
    make_initial_state,
    make_extraction_failed_transition,
    make_need_clarification_transition,
    make_turn,
)


def _coordinator(service: ScriptedRequirementDomainService) -> RequirementSubgraphCoordinator:
    return RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=PerRunBudgetManagerFactory(
            lambda _run_id: ScriptedExecutionBudgetManager()
        ),
        signal_sink=RecordingRequirementSubgraphSignalSink(),
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )


def test_demo_factory_owns_one_strict_saver_and_runtime_public_surface():
    state = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_clarification_transition(state)]
    )
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service)
    assert set(name for name in dir(runtime) if not name.startswith("_")).issuperset(
        {"start", "resume", "get_state"}
    )
    assert type(runtime._graph.checkpointer) is InMemorySaver
    assert type(runtime._graph.checkpointer.serde) is RequirementCheckpointSerializer


def test_default_saver_and_embedded_graph_cannot_construct_runtime():
    service = ScriptedRequirementDomainService()
    coordinator = _coordinator(service)
    default_graph = build_requirement_subgraph(
        coordinator=coordinator,
        checkpointer=InMemorySaver(),
    )
    with pytest.raises(RequirementSubgraphConfigurationError) as caught:
        RequirementSubgraphRuntime(graph=default_graph, coordinator=coordinator)
    assert caught.value.args == ()

    embedded_graph = build_requirement_subgraph(coordinator=coordinator, checkpointer=None)
    with pytest.raises(RequirementSubgraphConfigurationError):
        RequirementSubgraphRuntime(graph=embedded_graph, coordinator=coordinator)


def test_mismatched_coordinator_and_duplicate_runtime_are_configuration_errors():
    service = ScriptedRequirementDomainService()
    coordinator = _coordinator(service)
    graph = build_requirement_subgraph(
        coordinator=coordinator,
        checkpointer=InMemorySaver(serde=RequirementCheckpointSerializer()),
    )
    runtime = RequirementSubgraphRuntime(graph=graph, coordinator=coordinator)
    with pytest.raises(RequirementSubgraphConfigurationError):
        RequirementSubgraphRuntime(graph=graph, coordinator=_coordinator(service))
    with pytest.raises(RequirementSubgraphConfigurationError):
        RequirementSubgraphRuntime(graph=graph, coordinator=coordinator)
    assert runtime._graph is graph


@pytest.mark.asyncio
async def test_runtime_get_state_unknown_and_capacity_full_existing_run():
    state = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_clarification_transition(state)]
    )
    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=PerRunBudgetManagerFactory(
            lambda _run_id: ScriptedExecutionBudgetManager()
        ),
        signal_sink=RecordingRequirementSubgraphSignalSink(),
        limits=RequirementSubgraphCoordinatorLimits(max_runs=1),
    )
    graph = build_requirement_subgraph(
        coordinator=coordinator,
        checkpointer=InMemorySaver(serde=RequirementCheckpointSerializer()),
    )
    runtime = RequirementSubgraphRuntime(graph=graph, coordinator=coordinator)
    paused = await runtime.start(state=state, turn=make_turn(state))
    current = await runtime.get_state(run_id=state.run_id)
    assert current == paused.state
    with pytest.raises(RequirementSubgraphInputError):
        await runtime.get_state(run_id=uuid4())


@pytest.mark.asyncio
async def test_runtime_unknown_resume_is_rejected_without_side_effects():
    state = make_initial_state()
    sink = RecordingRequirementSubgraphSignalSink()
    service = ScriptedRequirementDomainService()
    runtime = create_demo_requirement_subgraph_runtime(
        domain_service=service,
        signal_sink=sink,
    )
    envelope = RequirementResumeEnvelope(
        pause_token="a" * 32,
        resume=RequirementClarificationResume(turn=make_turn(state)),
    )

    with pytest.raises(RequirementSubgraphResumeRejectedError):
        await runtime.resume(run_id=uuid4(), envelope=envelope)

    assert sink.calls == []
    assert service.process_calls == []
    assert service.confirm_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_values", [None, {}], ids=["missing", "distorted"])
async def test_registered_checkpoint_distortion_maps_to_state_error_without_side_effects(
    monkeypatch,
    broken_values,
):
    state = make_initial_state()
    sink = RecordingRequirementSubgraphSignalSink()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_clarification_transition(state)]
    )
    managers = []

    def manager_factory(_run_id):
        manager = ScriptedExecutionBudgetManager()
        managers.append(manager)
        return manager

    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=manager_factory,
        signal_sink=sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=1),
    )
    graph = build_requirement_subgraph(
        coordinator=coordinator,
        checkpointer=InMemorySaver(serde=RequirementCheckpointSerializer()),
    )
    runtime = RequirementSubgraphRuntime(graph=graph, coordinator=coordinator)
    paused = await runtime.start(state=state, turn=make_turn(state))
    envelope = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(paused.state)),
    )
    signal_count = len(sink.calls)
    process_count = len(service.process_calls)
    confirm_count = len(service.confirm_calls)

    async def distorted_aget_state(_config):
        return SimpleNamespace(values=broken_values, tasks=())

    monkeypatch.setattr(runtime._graph, "aget_state", distorted_aget_state)
    with pytest.raises(RequirementSubgraphStateError) as get_caught:
        await runtime.get_state(run_id=state.run_id)
    with pytest.raises(RequirementSubgraphStateError) as resume_caught:
        await runtime.resume(run_id=state.run_id, envelope=envelope)

    assert get_caught.value.args == ()
    assert resume_caught.value.args == ()
    assert len(sink.calls) == signal_count
    assert len(service.process_calls) == process_count
    assert len(service.confirm_calls) == confirm_count
    assert managers[0].requests == []
    assert sink.calls[-1].signal_type == "start_accepted"


@pytest.mark.asyncio
async def test_runtime_resume_uses_public_coordinator_resume_operation(monkeypatch):
    state = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[
            make_need_clarification_transition(state),
            make_extraction_failed_transition(state),
        ]
    )
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service)
    paused = await runtime.start(state=state, turn=make_turn(state))
    envelope = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(paused.state)),
    )
    original_resume = RequirementSubgraphCoordinator.resume_operation
    calls = []

    def traced_resume(self, *, state, envelope):
        calls.append((state, envelope))
        return original_resume(self, state=state, envelope=envelope)

    monkeypatch.setattr(
        RequirementSubgraphCoordinator,
        "resume_operation",
        traced_resume,
    )
    result = await runtime.resume(run_id=state.run_id, envelope=envelope)

    assert result.result_type == "domain_failed"
    assert len(calls) == 1
    assert calls[0][0] == paused.state
    assert calls[0][1] == envelope
    assert not hasattr(runtime._coordinator, "_runtime_resume_operation")
    assert not hasattr(runtime._coordinator, "_prepare_runtime_resume")


@pytest.mark.asyncio
async def test_runtime_get_state_waits_for_same_run_resume_lease(monkeypatch):
    state = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[
            make_need_clarification_transition(state),
            make_extraction_failed_transition(state),
        ]
    )
    sink = RecordingRequirementSubgraphSignalSink()
    runtime = create_demo_requirement_subgraph_runtime(
        domain_service=service,
        signal_sink=sink,
    )
    paused = await runtime.start(state=state, turn=make_turn(state))
    envelope = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(paused.state)),
    )

    original_ainvoke = runtime._graph.ainvoke
    resume_ainvoke_entered = asyncio.Event()
    release_resume_ainvoke = asyncio.Event()
    call_count = 0

    async def blocked_ainvoke(command, *, config):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            resume_ainvoke_entered.set()
            await release_resume_ainvoke.wait()
        return await original_ainvoke(command, config=config)

    monkeypatch.setattr(runtime._graph, "ainvoke", blocked_ainvoke)
    resume_task = asyncio.create_task(
        runtime.resume(run_id=state.run_id, envelope=envelope)
    )
    await asyncio.wait_for(resume_ainvoke_entered.wait(), timeout=1)

    get_task = asyncio.create_task(runtime.get_state(run_id=state.run_id))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(get_task), timeout=0.05)

    release_resume_ainvoke.set()
    result = await asyncio.wait_for(resume_task, timeout=2)
    assert result.result_type == "domain_failed"
    current = await asyncio.wait_for(get_task, timeout=1)
    assert current.delivery_status.value == "in_progress"


@pytest.mark.asyncio
async def test_runtime_two_resumes_same_run_are_serial_and_late_old_resume_is_rejected(
    monkeypatch,
):
    state = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[
            make_need_clarification_transition(state),
            make_extraction_failed_transition(state),
        ]
    )
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service)
    paused = await runtime.start(state=state, turn=make_turn(state))
    envelope = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(paused.state)),
    )
    original_ainvoke = runtime._graph.ainvoke
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_ainvoke(command, *, config):
        entered.set()
        await release.wait()
        return await original_ainvoke(command, config=config)

    monkeypatch.setattr(runtime._graph, "ainvoke", blocked_ainvoke)
    first = asyncio.create_task(runtime.resume(run_id=state.run_id, envelope=envelope))
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(runtime.resume(run_id=state.run_id, envelope=envelope))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(second), timeout=0.05)

    release.set()
    first_result = await asyncio.wait_for(first, timeout=2)
    assert first_result.result_type == "domain_failed"
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        await asyncio.wait_for(second, timeout=1)
    assert len(service.process_calls) == 2


@pytest.mark.asyncio
async def test_runtime_different_run_get_is_not_blocked_by_other_run_resume(monkeypatch):
    first_state = make_initial_state()
    second_state = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[
            make_need_clarification_transition(first_state),
            make_need_clarification_transition(second_state),
            make_extraction_failed_transition(first_state),
            make_extraction_failed_transition(second_state),
        ]
    )
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service)
    first_paused = await runtime.start(state=first_state, turn=make_turn(first_state))
    second_paused = await runtime.start(state=second_state, turn=make_turn(second_state))
    envelope = RequirementResumeEnvelope(
        pause_token=first_paused.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(first_paused.state)),
    )
    second_envelope = RequirementResumeEnvelope(
        pause_token=second_paused.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(second_paused.state)),
    )
    original_ainvoke = runtime._graph.ainvoke
    entered = asyncio.Event()
    release = asyncio.Event()

    async def block_only_first_run(command, *, config):
        if config["configurable"]["thread_id"] == str(first_state.run_id):
            entered.set()
            await release.wait()
        return await original_ainvoke(command, config=config)

    monkeypatch.setattr(runtime._graph, "ainvoke", block_only_first_run)
    resume_task = asyncio.create_task(
        runtime.resume(run_id=first_state.run_id, envelope=envelope)
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    current = await asyncio.wait_for(
        runtime.get_state(run_id=second_state.run_id),
        timeout=1,
    )
    assert current == second_paused.state
    other_resume = asyncio.create_task(
        runtime.resume(run_id=second_state.run_id, envelope=second_envelope)
    )
    other_result = await asyncio.wait_for(other_resume, timeout=2)
    assert other_result.result_type == "domain_failed"
    release.set()
    result = await asyncio.wait_for(resume_task, timeout=2)
    assert result.result_type == "domain_failed"


@pytest.mark.asyncio
async def test_runtime_faulted_run_maps_resume_and_get_state_exactly():
    state = make_initial_state()
    service = ScriptedRequirementDomainService(process_error=ValueError("domain"))
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service)
    with pytest.raises(ValueError):
        await runtime.start(state=state, turn=make_turn(state))

    envelope = RequirementResumeEnvelope(
        pause_token="a" * 32,
        resume=RequirementClarificationResume(turn=make_turn(state)),
    )
    with pytest.raises(RequirementSubgraphRunFaultedError):
        await runtime.resume(run_id=state.run_id, envelope=envelope)
    with pytest.raises(RequirementSubgraphRunFaultedError):
        await runtime.get_state(run_id=state.run_id)


def test_runtime_binding_does_not_keep_graph_or_runtime_alive():
    service = ScriptedRequirementDomainService()
    coordinator = _coordinator(service)
    graph = build_requirement_subgraph(
        coordinator=coordinator,
        checkpointer=InMemorySaver(serde=RequirementCheckpointSerializer()),
    )
    runtime = RequirementSubgraphRuntime(graph=graph, coordinator=coordinator)
    runtime_ref = weakref.ref(runtime)
    graph_ref = weakref.ref(graph)

    del runtime
    del graph
    gc.collect()

    assert runtime_ref() is None
    assert graph_ref() is None


@pytest.mark.asyncio
async def test_start_rejects_non_exact_public_dto_before_graph_side_effects():
    service = ScriptedRequirementDomainService()
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service)
    state = make_initial_state()
    with pytest.raises(RequirementSubgraphInputError):
        await runtime.start(state=state.model_dump(), turn=make_turn(state))  # type: ignore[arg-type]
    assert service.process_calls == []
