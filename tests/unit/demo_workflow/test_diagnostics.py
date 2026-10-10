from uuid import uuid4

import pytest

from app.demo_workflow.diagnostics import DemoSummaryView
from app.demo_workflow.terminal import make_cancelled_state
from app.requirement_extraction import ExtractionInvocationEvent
from app.workflow_contracts import EventDeliveryFailed, STANDARD_EVENT_DELIVERY_FAILURE_CODES
from tests.requirement_subgraph_fakes import make_initial_state, make_confirmed_transition
from tests.support.demo_workflow_fakes import diagnostics


def safe_state():
    state = make_initial_state()
    transition = make_confirmed_transition(state)
    return type(state).model_validate({**state.model_dump(mode="python"),
        "requirement_state": transition.state, "requirement_outcome": transition.outcome}, strict=True)


@pytest.mark.asyncio
async def test_local_complete_summary_and_restart_are_distinct():
    state = safe_state()
    diagnostic = diagnostics()
    diagnostic.start_run(run_id=state.run_id, trace_id=state.trace_id)
    turn_id = uuid4()
    with diagnostic.operation(run_id=state.run_id, trace_id=state.trace_id, turn_id=turn_id, kind="initial"):
        diagnostic.accept(ExtractionInvocationEvent(turn_id=turn_id,
            prompt_version="requirement-extraction-v3-explicit-negatives", attempt_kind="extract",
            semantic_call_number=1, outcome_kind="repair_required", invocation_metadata=None, runtime_error=None))
    state = make_cancelled_state(state)
    result = await diagnostic.finish(state=state, budget_snapshots=())
    counters = {counter.name: counter.value for counter in result.summary.counters}
    assert counters["workflow.started"] == counters["workflow.terminal"] == counters["extraction.extract"] == 1
    assert result == await diagnostic.finish(state=state, budget_snapshots=())
    missing = await diagnostics().finish(state=state, budget_snapshots=())
    assert missing.summary is None and missing.unavailable_reason == "process_history_unavailable"


@pytest.mark.asyncio
async def test_known_delivery_failure_keeps_local_counts():
    class Sink:
        async def emit(self, event):
            return EventDeliveryFailed(event_id=event.event_id, failure_code=STANDARD_EVENT_DELIVERY_FAILURE_CODES[0])
    state = safe_state()
    diagnostic = diagnostics(Sink())
    diagnostic.start_run(run_id=state.run_id, trace_id=state.trace_id)
    result = await diagnostic.finish(state=make_cancelled_state(state), budget_snapshots=())
    assert result.summary.event_delivery_failures == 2
    assert next(c.value for c in result.summary.counters if c.name == "workflow.started") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", ["extract", "repair"])
@pytest.mark.parametrize("failed", [False, True])
async def test_extraction_and_repair_events_use_real_registry(attempt, failed):
    from tests.unit.requirement_extraction.test_events import metadata, runtime_error
    state = safe_state()
    diagnostic = diagnostics()
    diagnostic.start_run(run_id=state.run_id,trace_id=state.trace_id)
    turn = uuid4()
    with diagnostic.operation(run_id=state.run_id,trace_id=state.trace_id,turn_id=turn,kind="initial"):
        diagnostic.accept(ExtractionInvocationEvent(turn_id=turn,prompt_version="requirement-extraction-v3-explicit-negatives",
            attempt_kind=attempt,semantic_call_number=1 if attempt == "extract" else 2,
            outcome_kind="runtime_failure" if failed else "succeeded",invocation_metadata=None if failed else metadata(),
            runtime_error=runtime_error() if failed else None))
    result = await diagnostic.finish(state=make_cancelled_state(state),budget_snapshots=())
    counts = {c.name:c.value for c in result.summary.counters}
    assert counts[f"extraction.{attempt}"] == 1
    assert counts["extraction.failed"] == int(failed)


@pytest.mark.asyncio
async def test_wrong_event_identity_and_terminal_identity_are_rejected():
    from app.demo_workflow import DemoWorkflowUsageError
    from app.workflow_contracts import EventEmitted
    class WrongSink:
        async def emit(self, event):
            return EventEmitted(event_id=uuid4(),disposition="applied")
    state = safe_state()
    diagnostic = diagnostics(WrongSink())
    diagnostic.start_run(run_id=state.run_id,trace_id=state.trace_id)
    with pytest.raises(DemoWorkflowUsageError):
        await diagnostic.finish(state=make_cancelled_state(state),budget_snapshots=())
    with pytest.raises(DemoWorkflowUsageError):
        await diagnostic.finish(state=make_cancelled_state(state.model_copy(update={"trace_id":uuid4()})),budget_snapshots=())
def test_active_phase_scope_nested_identity_and_summary_counts():
    from app.demo_workflow import DemoWorkflowUsageError
    from app.workflow_contracts import WorkflowPhase
    state = safe_state()
    diagnostic = diagnostics()
    diagnostic.start_run(run_id=state.run_id, trace_id=state.trace_id)
    read = lambda: diagnostic.get_active_phase(run_id=state.run_id, trace_id=state.trace_id)
    assert read() is None
    diagnostic._observe_phase(state)  # Standalone M11 use has no web scope.
    assert read() is None
    with diagnostic.operation(run_id=state.run_id, trace_id=state.trace_id, turn_id=None, kind="initial"):
        assert read() is None
        diagnostic._observe_phase(state)
        assert read() is WorkflowPhase.REQUIREMENTS
        with diagnostic.operation(run_id=state.run_id, trace_id=state.trace_id, turn_id=None, kind="resume"):
            diagnostic._observe_phase(state.model_copy(update={"phase": WorkflowPhase.CANDIDATE_RESEARCH}))
            assert read() is WorkflowPhase.CANDIDATE_RESEARCH
        assert read() is WorkflowPhase.CANDIDATE_RESEARCH
        assert diagnostic.get_active_phase(run_id=uuid4(), trace_id=state.trace_id) is None
        assert diagnostic.get_active_phase(run_id=state.run_id, trace_id=uuid4()) is None
        with pytest.raises(DemoWorkflowUsageError):
            diagnostic.get_active_phase(run_id=str(state.run_id), trace_id=state.trace_id)
        with pytest.raises(DemoWorkflowUsageError):
            diagnostic._observe_phase(state.model_copy(update={"trace_id": uuid4()}))
        with pytest.raises(DemoWorkflowUsageError):
            diagnostic._observe_phase(state.model_copy(update={"phase": "requirements"}))
    assert read() is None


@pytest.mark.asyncio
async def test_phase_child_scope_expires_across_next_execution_and_errors_release():
    import asyncio
    from app.workflow_contracts import WorkflowPhase
    state = safe_state()
    diagnostic = diagnostics()
    diagnostic.start_run(run_id=state.run_id, trace_id=state.trace_id)
    read = lambda: diagnostic.get_active_phase(run_id=state.run_id, trace_id=state.trace_id)
    gate = asyncio.Event()
    async def late():
        await gate.wait()
        diagnostic._observe_phase(state.model_copy(update={"phase": WorkflowPhase.OUTPUT_FORMATTING}))
    with diagnostic.operation(run_id=state.run_id, trace_id=state.trace_id, turn_id=None, kind="initial"):
        async def legitimate(): diagnostic._observe_phase(state)
        await asyncio.create_task(legitimate())
        assert read() is WorkflowPhase.REQUIREMENTS
        child = asyncio.create_task(late())
    with diagnostic.operation(run_id=state.run_id, trace_id=state.trace_id, turn_id=None, kind="resume"):
        diagnostic._observe_phase(state.model_copy(update={"phase": WorkflowPhase.ITINERARY_PLANNING}))
        gate.set()
        await child
        assert read() is WorkflowPhase.ITINERARY_PLANNING
    for error in (RuntimeError(), asyncio.CancelledError()):
        with pytest.raises(type(error)):
            with diagnostic.operation(run_id=state.run_id, trace_id=state.trace_id, turn_id=None, kind="recover"):
                diagnostic._observe_phase(state)
                raise error
        assert read() is None
    summary = (await diagnostic.finish(state=make_cancelled_state(state), budget_snapshots=())).summary
    counts = {item.name: item.value for item in summary.counters}
    assert counts["workflow.started"] == counts["workflow.terminal"] == 1
    assert sum(counts.values()) == 2  # Observation adds no diagnostics events.
    diagnostic.release_run(run_id=state.run_id)
    assert read() is None
