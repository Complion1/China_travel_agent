import asyncio
from uuid import uuid4
import pytest
from app.frontend_experience import PublicProgressRecorder, ObservedArtifactRepository
from tests.unit.frontend_experience.test_models import body


class Store:
    def __init__(self): self.calls=[]; self.fail=False; self.slow=False
    async def append(self,**kwargs):
        self.calls.append(("append",kwargs))
        if self.fail: raise RuntimeError("private failure")
        if self.slow: await asyncio.sleep(10)
        return None
    async def mark_limited(self,**kwargs): self.calls.append(("limited",kwargs))
    async def settle(self,**kwargs): self.calls.append(("settle",kwargs))


@pytest.mark.asyncio
async def test_scope_identity_expiry_inherited_tasks_and_fencing():
    store=Store(); recorder=PublicProgressRecorder(store=store,registrations=(),secrets=())
    run,trace=uuid4(),uuid4()
    async def publish(run_id=run): await recorder.publish(run_id=run_id,trace_id=trace,event_key="test",body=body())
    await publish()
    gate=asyncio.Event()
    async def delayed(): await gate.wait(); await publish()
    with recorder.operation(run_id=run,trace_id=trace):
        await publish(uuid4()); await publish()
        task=asyncio.create_task(delayed())
    gate.set(); await task
    with recorder.operation(run_id=run,trace_id=trace,valid=lambda:False): await publish()
    assert [name for name,_ in store.calls]==["append"]
    with recorder.operation(run_id=run,trace_id=trace): await recorder.settle(run_id=run,trace_id=trace)
    assert store.calls[-1][0]=="settle"


@pytest.mark.asyncio
async def test_optional_failures_are_bounded_and_cancel_propagates():
    store=Store(); recorder=PublicProgressRecorder(store=store,registrations=(),secrets=()); run,trace=uuid4(),uuid4()
    with recorder.operation(run_id=run,trace_id=trace):
        store.fail=True
        await recorder.publish(run_id=run,trace_id=trace,event_key="test",body=body())
        store.fail=False; store.slow=True
        started=asyncio.get_running_loop().time()
        await recorder.publish(run_id=run,trace_id=trace,event_key="slow",body=body())
        assert asyncio.get_running_loop().time()-started < 0.5
        task=asyncio.create_task(recorder.publish(run_id=run,trace_id=trace,event_key="cancel",body=body()))
        await asyncio.sleep(0); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    assert sum(name=="limited" for name,_ in store.calls)==2


@pytest.mark.asyncio
async def test_repository_delegation_and_business_error_unchanged():
    class Repository:
        async def put(self,request): raise LookupError("business failure")
        async def lookup(self,**kwargs): return "lookup"
        async def get(self,*args,**kwargs): return "get"
    class Recorder:
        async def observe(self,**kwargs): raise AssertionError("failed writes must not be observed")
    repository=ObservedArtifactRepository(repository=Repository(),recorder=Recorder())
    with pytest.raises(LookupError,match="business failure"): await repository.put(object())
    assert await repository.lookup(operation_id=object())=="lookup"
    assert await repository.get(object(),expected_model=object)=="get"
