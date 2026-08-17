from __future__ import annotations

import asyncio

import pytest

from app.requirement_subgraph import (
    RequirementCheckpointSerializer,
    RequirementSubgraphCapacityExceededError,
    RequirementSubgraphCoordinator,
    RequirementSubgraphCoordinatorLimits,
    RequirementClarificationResume,
    RequirementConfirmationResume,
    RequirementSubgraphResumeRejectedError,
    RequirementSubgraphRunFaultedError,
    create_demo_requirement_subgraph_runtime,
    build_requirement_subgraph,
)
from app.requirement_subgraph.models import RequirementResumeEnvelope
from tests.requirement_subgraph_fakes import (
    RecordingRequirementSubgraphSignalSink,
    ScriptedRequirementDomainService,
    make_extraction_failed_transition,
    make_initial_state,
    make_need_clarification_transition,
    make_need_confirmation_transition,
    make_invalidated_transition,
    make_turn,
    PerRunBudgetManagerFactory,
    ScriptedExecutionBudgetManager,
)
from langgraph.checkpoint.memory import InMemorySaver
from app.requirements import RequirementConfirmation


def _clarification_envelope(result, *, token: str | None = None):
    return RequirementResumeEnvelope(
        pause_token=token or result.pause.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(result.state)),
    )


@pytest.mark.asyncio
async def test_resume_rejection_matrix_preserves_pause_and_has_zero_graph_side_effects():
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_clarification_transition(initial)]
    )
    sink = RecordingRequirementSubgraphSignalSink()
    runtime = create_demo_requirement_subgraph_runtime(
        domain_service=service,
        signal_sink=sink,
    )
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    accepted_count = len([item for item in sink.calls if type(item).__name__ == "ResumeAccepted"])

    invalid_branch = RequirementResumeEnvelope(
        pause_token=paused.pause.pause_token,
        resume=RequirementConfirmationResume(
            action=RequirementConfirmation(decision="accept", revision_text=None),
        ),
    )
    wrong_token = _clarification_envelope(paused, token="f" * 32)
    for envelope in (invalid_branch, wrong_token):
        with pytest.raises(RequirementSubgraphResumeRejectedError):
            await runtime.resume(run_id=initial.run_id, envelope=envelope)
        assert len(service.process_calls) == 1
        assert len(service.confirm_calls) == 0
        assert len([item for item in sink.calls if type(item).__name__ == "ResumeAccepted"]) == accepted_count
        assert (await runtime.get_state(run_id=initial.run_id)) == paused.state


@pytest.mark.asyncio
async def test_confirmation_pause_rejects_clarification_resume():
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_need_confirmation_transition(initial)]
    )
    sink = RecordingRequirementSubgraphSignalSink()
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service, signal_sink=sink)
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    envelope = _clarification_envelope(paused)
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        await runtime.resume(run_id=initial.run_id, envelope=envelope)
    assert len(service.confirm_calls) == 0
    assert (await runtime.get_state(run_id=initial.run_id)) == paused.state


@pytest.mark.asyncio
async def test_invalidated_confirmation_pause_also_rejects_clarification_resume():
    initial = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[make_invalidated_transition(initial)]
    )
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service)
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        await runtime.resume(
            run_id=initial.run_id,
            envelope=_clarification_envelope(paused),
        )
    assert len(service.confirm_calls) == 0


@pytest.mark.asyncio
async def test_cross_run_and_rotated_old_token_are_rejected():
    first_state = make_initial_state()
    second_state = make_initial_state()
    service = ScriptedRequirementDomainService(
        process_results=[
            make_need_clarification_transition(first_state),
            make_need_clarification_transition(second_state),
            make_need_clarification_transition(first_state),
        ]
    )
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service)
    first = await runtime.start(state=first_state, turn=make_turn(first_state))
    second = await runtime.start(state=second_state, turn=make_turn(second_state))
    cross = _clarification_envelope(first, token=second.pause.pause_token)
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        await runtime.resume(run_id=first_state.run_id, envelope=cross)

    next_pause = await runtime.resume(
        run_id=first_state.run_id,
        envelope=_clarification_envelope(first),
    )
    assert next_pause.pause.pause_token != first.pause.pause_token
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        await runtime.resume(
            run_id=first_state.run_id,
            envelope=_clarification_envelope(next_pause, token=first.pause.pause_token),
        )
    assert (await runtime.get_state(run_id=first_state.run_id)) == next_pause.state


@pytest.mark.asyncio
async def test_two_resume_callers_are_serialized_and_late_caller_cannot_overwrite_terminal():
    initial = make_initial_state()
    entered = asyncio.Event()
    release = asyncio.Event()

    class GatedService(ScriptedRequirementDomainService):
        async def process_turn(self, input):
            result = await super().process_turn(input)
            if len(self.process_calls) == 2:
                entered.set()
                await release.wait()
            return result

    service = GatedService(
        process_results=[
            make_need_clarification_transition(initial),
            make_extraction_failed_transition(initial),
        ]
    )
    sink = RecordingRequirementSubgraphSignalSink()
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service, signal_sink=sink)
    paused = await runtime.start(state=initial, turn=make_turn(initial))
    envelope = _clarification_envelope(paused)
    first_task = asyncio.create_task(runtime.resume(run_id=initial.run_id, envelope=envelope))
    second_task = asyncio.create_task(runtime.resume(run_id=initial.run_id, envelope=envelope))
    await asyncio.wait_for(entered.wait(), timeout=2)
    release.set()
    first_result, second_result = await asyncio.wait_for(
        asyncio.gather(first_task, second_task, return_exceptions=True),
        timeout=2,
    )
    assert first_result.result_type == "domain_failed"
    assert isinstance(second_result, RequirementSubgraphResumeRejectedError)
    assert len([item for item in sink.calls if type(item).__name__ == "ResumeAccepted"]) == 1
    assert len(service.process_calls) == 2


@pytest.mark.asyncio
async def test_faulted_run_remains_faulted_after_graph_service_exception():
    initial = make_initial_state()
    original = ValueError("original-service-error")
    service = ScriptedRequirementDomainService(process_error=original)
    runtime = create_demo_requirement_subgraph_runtime(domain_service=service)
    with pytest.raises(ValueError) as caught:
        await runtime.start(state=initial, turn=make_turn(initial))
    assert caught.value is original
    with pytest.raises(RequirementSubgraphRunFaultedError):
        await runtime.get_state(run_id=initial.run_id)


@pytest.mark.asyncio
async def test_exact_capacity_256_commits_and_257_has_zero_side_effects():
    template = make_initial_state()
    transition = make_need_clarification_transition(template)
    service = ScriptedRequirementDomainService(process_results=[transition] * 256)
    sink = RecordingRequirementSubgraphSignalSink()
    factory = PerRunBudgetManagerFactory(lambda _run_id: ScriptedExecutionBudgetManager())
    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=factory,
        signal_sink=sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=256),
    )
    graph = build_requirement_subgraph(
        coordinator=coordinator,
        checkpointer=InMemorySaver(serde=RequirementCheckpointSerializer()),
    )
    from app.requirement_subgraph import RequirementSubgraphRuntime

    runtime = RequirementSubgraphRuntime(graph=graph, coordinator=coordinator)
    runs = []
    for _ in range(256):
        state = make_initial_state()
        runs.append(await runtime.start(state=state, turn=make_turn(state)))
    assert len(factory.created) == 256
    assert len(service.process_calls) == 256
    assert len([item for item in sink.calls if type(item).__name__ == "StartAccepted"]) == 256
    assert (await runtime.get_state(run_id=runs[0].state.run_id)) == runs[0].state

    extra = make_initial_state()
    with pytest.raises(RequirementSubgraphCapacityExceededError):
        await runtime.start(state=extra, turn=make_turn(extra))
    assert len(factory.created) == 256
    assert len(service.process_calls) == 256
    assert len([item for item in sink.calls if type(item).__name__ == "StartAccepted"]) == 256
