import asyncio
from datetime import datetime,timezone
import gc
from types import SimpleNamespace
from uuid import uuid4
import weakref
import pytest

from app.demo_workflow import make_cancelled_state
from app.durable_execution import DurableRunView
from app.web_application import WebApplicationDependencyError
from app.web_application.summary import SummaryFinalizer, MAX_PENDING, MAX_ATTEMPTS, ATTEMPT_SECONDS, LIFETIME_SECONDS, BACKOFF
from app.workflow_contracts import EventEmitted
from tests.requirement_subgraph_fakes import make_initial_state, make_confirmed_transition
from tests.support.demo_workflow_fakes import diagnostics


class CaptureSink:
    def __init__(self): self.refs=[]
    async def emit(self,event):
        self.refs.append(weakref.ref(event))
        return EventEmitted(disposition="applied",event_id=event.event_id)


class SummaryStore:
    def __init__(self, *, failures=0, delay=0, ambiguous=False):
        self.rows={}; self.attempts=0; self.failures=failures; self.delay=delay; self.ambiguous=ambiguous
    async def get_summary(self, *, run_id,trace_id,terminal_code): return self.rows.get(run_id)
    async def save_summary(self, *, receipt):
        self.attempts+=1
        if self.delay: await asyncio.sleep(self.delay)
        if self.ambiguous:
            self.rows[receipt.run_id]=receipt
            raise WebApplicationDependencyError()
        if self.attempts <= self.failures: raise WebApplicationDependencyError()
        self.rows[receipt.run_id]=receipt


async def finalizer_fixture(store=None):
    initial=make_initial_state()
    transition=make_confirmed_transition(initial)
    state=type(initial).model_validate({**initial.model_dump(mode="python"),
        "requirement_state":transition.state,"requirement_outcome":transition.outcome},strict=True)
    state=make_cancelled_state(state)
    sink=CaptureSink()
    diagnostic=diagnostics(sink)
    diagnostic.start_run(run_id=state.run_id,trace_id=state.trace_id)
    calls=[]; releases=[]; closed=[False]
    async def get_state(*,run_id):
        assert not closed[0]; calls.append("state"); return state
    async def budgets(*,run_id):
        assert not closed[0]; calls.append("budgets"); return ()
    async def release(run): releases.append(run)
    deps=SimpleNamespace(durable=SimpleNamespace(get_state=get_state,get_terminal_budget_snapshots=budgets),
        store=store or SummaryStore(),diagnostics=diagnostic,definition=SimpleNamespace(release_run=release))
    view=DurableRunView(run_id=state.run_id,trace_id=state.trace_id,created_at=datetime.now(timezone.utc),
        finalized_at=datetime.now(timezone.utc),lifecycle="closed",activity="terminal",phase=state.phase,
        delivery_status=state.delivery_status,terminal_code=state.terminal_code,pause_generation=None,cancel_requested=True)
    return SummaryFinalizer(dependencies=deps),view,deps,sink,calls,releases,closed


@pytest.mark.asyncio
async def test_one_finalizer_actual_events_receipt_and_release():
    finalizer,view,deps,sink,calls,releases,_=await finalizer_fixture()
    await finalizer.begin(view=view,execution_stopped=True)
    await finalizer.begin(view=view,execution_stopped=True)
    await finalizer.before_durable_close()
    assert deps.store.attempts == 1 and calls == ["state","budgets"] and releases == [view.run_id]
    receipt=deps.store.rows[view.run_id]
    assert receipt.summary().run_id == view.run_id
    assert len(sink.refs) >= 2
    gc.collect()
    assert all(ref() is None for ref in sink.refs)


@pytest.mark.asyncio
@pytest.mark.parametrize("failures",[1,2,3])
async def test_small_receipt_retry_bound_and_events_released_even_on_failure(failures):
    store=SummaryStore(failures=failures)
    finalizer,view,_,sink,_,releases,_=await finalizer_fixture(store)
    started=asyncio.get_running_loop().time()
    await finalizer.begin(view=view,execution_stopped=True)
    await finalizer.before_durable_close()
    assert store.attempts == min(failures+1,3)
    assert (view.run_id in store.rows) == (failures < 3)
    assert asyncio.get_running_loop().time()-started < 8
    assert releases == [view.run_id] and not finalizer.pending(run_id=view.run_id)
    gc.collect()
    assert all(ref() is None for ref in sink.refs)


@pytest.mark.asyncio
async def test_unknown_commit_readback_confirms_exact_receipt_without_retry():
    store=SummaryStore(ambiguous=True)
    finalizer,view,_,_,_,releases,_=await finalizer_fixture(store)
    await finalizer.begin(view=view,execution_stopped=True)
    await finalizer.before_durable_close()
    assert store.attempts == 1 and store.rows[view.run_id].summary() is not None and releases == [view.run_id]


@pytest.mark.asyncio
async def test_real_per_attempt_timeout_three_attempts_under_eight_seconds():
    store=SummaryStore(delay=3)
    finalizer,view,_,_,_,releases,_=await finalizer_fixture(store)
    started=asyncio.get_running_loop().time()
    await finalizer.begin(view=view,execution_stopped=True)
    await finalizer.before_durable_close()
    elapsed=asyncio.get_running_loop().time()-started
    assert store.attempts == 3 and 6 <= elapsed < 8 and releases == [view.run_id] and not store.rows
    assert (MAX_PENDING,MAX_ATTEMPTS,ATTEMPT_SECONDS,LIFETIME_SECONDS,BACKOFF) == (8,3,2.0,8.0,(.25,1.0))


@pytest.mark.asyncio
async def test_post_close_only_frozen_save_and_original_lifetime():
    store=SummaryStore(failures=2)
    finalizer,view,_,_,calls,releases,closed=await finalizer_fixture(store)
    await finalizer.begin(view=view,execution_stopped=True)
    while store.attempts < 1: await asyncio.sleep(.01)
    closed[0]=True
    await finalizer.after_durable_close()
    assert calls == ["state","budgets"] and store.attempts == 3 and releases == [view.run_id]


@pytest.mark.asyncio
async def test_preclose_cancels_unfinished_collection_without_fake_summary():
    finalizer,view,deps,_,calls,releases,closed=await finalizer_fixture()
    entered=asyncio.Event()
    async def waiting(*,run_id):
        entered.set()
        await asyncio.Event().wait()
    deps.durable.get_state=waiting
    await finalizer.begin(view=view,execution_stopped=True)
    await entered.wait()
    # Cancelled collection must release and propagate, rather than create a receipt.
    task=next(iter(finalizer._tasks.values()))
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    closed[0]=True
    await finalizer.after_durable_close()
    assert not deps.store.rows and releases == [view.run_id]


@pytest.mark.asyncio
async def test_pending_receipts_at_most_eight_no_overflow_queue():
    finalizer,view,deps,_,_,releases,_=await finalizer_fixture()
    gate=asyncio.Event()
    async def waiting(*,run_id): await gate.wait(); raise WebApplicationDependencyError()
    deps.durable.get_state=waiting
    views=[view.model_copy(update={"run_id":uuid4()}) for _ in range(9)]
    for item in views: await finalizer.begin(view=item,execution_stopped=True)
    assert sum(finalizer.pending(run_id=item.run_id) for item in views) == 8 and releases == [views[-1].run_id]
    gate.set()
    await finalizer.before_durable_close()
    assert len(releases) == 9 and not deps.store.rows


@pytest.mark.asyncio
async def test_live_or_unstopped_run_does_not_finalize():
    from app.web_application import WebApplicationUsageError
    finalizer,view,deps,_,calls,_,_=await finalizer_fixture()
    with pytest.raises(WebApplicationUsageError): await finalizer.begin(view=view,execution_stopped=False)
    await finalizer.begin(view=view.model_copy(update={"lifecycle":"open","activity":"awaiting_user","terminal_code":None}),execution_stopped=True)
    assert not calls and not deps.store.rows


@pytest.mark.asyncio
@pytest.mark.parametrize("resists_cancel", [False, True])
async def test_expired_collection_window_requires_confirmed_stop(resists_cancel):
    finalizer,view,deps,_,_,releases,_=await finalizer_fixture()
    original=deps.durable.get_state
    entered,gate=asyncio.Event(),asyncio.Event()
    async def waiting(*,run_id):
        entered.set()
        try:
            await gate.wait()
        except asyncio.CancelledError:
            if not resists_cancel:
                raise
            await gate.wait()
        return await original(run_id=run_id)
    deps.durable.get_state=waiting
    await finalizer.begin(view=view,execution_stopped=True)
    await entered.wait()
    started=asyncio.get_running_loop().time()
    try:
        if resists_cancel:
            with pytest.raises(WebApplicationDependencyError):
                await finalizer.before_durable_close(deadline=started)
            assert not deps.store.rows and not releases
        else:
            await finalizer.before_durable_close(deadline=started)
            assert not deps.store.rows and releases == [view.run_id]
        assert asyncio.get_running_loop().time()-started < 2.5
    finally:
        gate.set()
        await finalizer.after_durable_close()
