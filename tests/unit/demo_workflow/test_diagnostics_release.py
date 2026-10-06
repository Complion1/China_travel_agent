import pytest
import gc
import weakref
from uuid import uuid4
from app.demo_workflow import DemoWorkflowUsageError, make_cancelled_state
from tests.support.demo_workflow_fakes import diagnostics
from tests.requirement_subgraph_fakes import make_initial_state, make_confirmed_transition
from app.workflow_contracts import EventEmitted

def state():
    initial=make_initial_state()
    transition=make_confirmed_transition(initial)
    return type(initial).model_validate({**initial.model_dump(mode="python"),
        "requirement_state":transition.state,"requirement_outcome":transition.outcome},strict=True)

@pytest.mark.asyncio
async def test_release_keeps_returned_summary_and_does_not_recreate_history():
    current=state()
    diag=diagnostics()
    diag.start_run(run_id=current.run_id,trace_id=current.trace_id)
    terminal=make_cancelled_state(current)
    saved=await diag.finish(state=terminal,budget_snapshots=())
    serialized=saved.model_dump_json()
    diag.release_run(run_id=current.run_id)
    diag.release_run(run_id=current.run_id)
    diag.release_run(run_id=uuid4())
    missing=await diag.finish(state=terminal,budget_snapshots=())
    assert missing.unavailable_reason=="process_history_unavailable"
    assert saved.model_dump_json()==serialized

@pytest.mark.asyncio
async def test_release_frees_real_delivered_event_objects():
    class WeakSink:
        def __init__(self):
            self.references=[]
        async def emit(self, event):
            self.references.append(weakref.ref(event))
            return EventEmitted(event_id=event.event_id,disposition="applied")
    sink=WeakSink()
    diag=diagnostics(sink)
    current=state()
    diag.start_run(run_id=current.run_id,trace_id=current.trace_id)
    summary=await diag.finish(state=make_cancelled_state(current),budget_snapshots=())
    assert all(reference() is not None for reference in sink.references)
    diag.release_run(run_id=current.run_id)
    gc.collect()
    assert all(reference() is None for reference in sink.references)
    assert summary.summary.run_id==current.run_id

def test_active_nested_operation_refuses_release_and_invalid_identity():
    diag=diagnostics()
    run,trace=uuid4(),uuid4()
    with diag.operation(run_id=run,trace_id=trace,turn_id=None,kind="recover"):
        with diag.operation(run_id=run,trace_id=trace,turn_id=None,kind="recover"):
            with pytest.raises(DemoWorkflowUsageError):
                diag.release_run(run_id=run)
        with pytest.raises(DemoWorkflowUsageError):
            diag.release_run(run_id=run)
    diag.release_run(run_id=run)
    with pytest.raises(DemoWorkflowUsageError):
        diag.release_run(run_id=str(run))
