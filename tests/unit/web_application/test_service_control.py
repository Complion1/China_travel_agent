import asyncio
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.requirement_extraction import ExtractionInvocationEvent
from app.web_application import (RunBoundExtractionInvocationSink, WebApplicationDependencies,
    WebApplicationService, WebApplicationUsageError, WebStartRequest)
from app.web_application.store import DecisionError
from tests.support.demo_workflow_fakes import diagnostics
from .fakes import web_settings, FixedClock


def event(turn, *, repair=False):
    return ExtractionInvocationEvent(turn_id=turn,prompt_version="requirement-extraction-v3-explicit-negatives",
        attempt_kind="repair" if repair else "extract",semantic_call_number=2 if repair else 1,
        outcome_kind="repair_required",invocation_metadata=None,runtime_error=None)


@pytest.mark.asyncio
async def test_known_and_internal_turn_bindings_are_exact_and_fresh():
    diagnostic=diagnostics()
    sink=RunBoundExtractionInvocationSink(diagnostics=diagnostic)
    run,trace,turn=uuid4(),uuid4(),uuid4()
    with pytest.raises(WebApplicationUsageError): sink.accept(event(turn))
    with sink.bind(run_id=run,trace_id=trace,kind="initial",expected_turn_id=turn,allow_internal_turn=False):
        sink.accept(event(turn))
        await asyncio.create_task(asyncio.to_thread(lambda: None))
        with pytest.raises(WebApplicationUsageError): sink.accept(event(uuid4()))
        bad=event(turn).model_copy(update={"semantic_call_number":True})
        with pytest.raises(WebApplicationUsageError): sink.accept(bad)
    with sink.bind(run_id=run,trace_id=trace,kind="resume",expected_turn_id=None,allow_internal_turn=True):
        sink.accept(event(turn))
        sink.accept(event(turn,repair=True))
        with pytest.raises(WebApplicationUsageError): sink.accept(event(uuid4()))
    with sink.bind(run_id=run,trace_id=trace,kind="resume",expected_turn_id=None,allow_internal_turn=False):
        with pytest.raises(WebApplicationUsageError): sink.accept(event(turn))


@pytest.mark.asyncio
async def test_inherited_child_binding_active_then_rejected_after_context_exits():
    sink=RunBoundExtractionInvocationSink(diagnostics=diagnostics())
    run,trace,turn=uuid4(),uuid4(),uuid4()
    gate=asyncio.Event()
    async def late():
        await gate.wait()
        sink.accept(event(turn))
    with sink.bind(run_id=run,trace_id=trace,kind="recover",expected_turn_id=None,allow_internal_turn=True):
        async def legitimate(): sink.accept(event(turn))
        await asyncio.create_task(legitimate())
        task=asyncio.create_task(late())
    gate.set()
    with pytest.raises(WebApplicationUsageError): await task


@pytest.mark.asyncio
async def test_dependencies_frozen_single_diagnostics_and_loop_lifecycle():
    diagnostic=diagnostics()
    sink=RunBoundExtractionInvocationSink(diagnostics=diagnostic)
    dependencies=WebApplicationDependencies(artifacts=None,durable=None,definition=None,diagnostics=diagnostic,
        extraction_sink=sink,result_service=None,store=None,clock=FixedClock(),disk_probe=lambda:10**10)
    assert tuple(field.name for field in fields(dependencies)) == ("artifacts","durable","definition","diagnostics","extraction_sink","result_service","store","clock","disk_probe")
    with pytest.raises(FrozenInstanceError): dependencies.store=None
    service=WebApplicationService(settings=web_settings(),dependencies=dependencies)
    async def cross_loop():
        await service.get_samples()
    with pytest.raises(WebApplicationUsageError): await asyncio.to_thread(lambda: asyncio.run(cross_loop()))
    await service.close()
    await service.close()
    with pytest.raises(WebApplicationUsageError): await service.get_samples()


@pytest.mark.asyncio
async def test_bad_construct_rejected_before_store_or_upstream_call():
    diagnostic=diagnostics()
    deps=WebApplicationDependencies(artifacts=None,durable=None,definition=None,diagnostics=diagnostic,
        extraction_sink=RunBoundExtractionInvocationSink(diagnostics=diagnostic),result_service=None,
        store=None,clock=FixedClock(),disk_probe=lambda:10**10)
    service=WebApplicationService(settings=web_settings(),dependencies=deps)
    request=WebStartRequest.model_construct(schema_version="1.0",request_id=uuid4(),user_message=99)
    with pytest.raises(DecisionError) as error: await service.start(owner=None,request=request,client_ip="192.0.2.1")
    assert error.value.code == "invalid_request"
    await service.close()


@pytest.mark.asyncio
async def test_no_wait_slot_and_foreign_extraction_binding_rejected():
    diagnostic=diagnostics()
    sink=RunBoundExtractionInvocationSink(diagnostics=diagnostic)
    deps=WebApplicationDependencies(artifacts=None,durable=None,definition=None,diagnostics=diagnostic,
        extraction_sink=sink,result_service=None,store=None,clock=FixedClock(),disk_probe=lambda:10**10)
    service=WebApplicationService(settings=web_settings(),dependencies=deps)
    run,trace=uuid4(),uuid4()
    service._try_slot(run,trace)
    with pytest.raises(DecisionError) as error: service._try_slot(uuid4(),uuid4())
    assert error.value.code == "busy"
    with pytest.raises(WebApplicationUsageError):
        with sink.bind(run_id=uuid4(),trace_id=trace,kind="initial",expected_turn_id=uuid4(),allow_internal_turn=False): pass
    service._release_slot(run,asyncio.current_task())
    await service.close()


@pytest.mark.asyncio
async def test_read_race_recomputes_actions_from_the_final_view():
    from datetime import datetime,timezone
    from app.durable_execution import DurableRunView,DurableExecutionRunBusyError
    from app.workflow_contracts import WorkflowPhase,DeliveryStatus
    diagnostic=diagnostics()
    sink=RunBoundExtractionInvocationSink(diagnostics=diagnostic)
    run,trace=uuid4(),uuid4()
    view=DurableRunView(run_id=run,trace_id=trace,created_at=datetime.now(timezone.utc),finalized_at=None,
        lifecycle="open",activity="awaiting_user",phase=WorkflowPhase.REQUIREMENTS,
        delivery_status=DeliveryStatus.AWAITING_USER,terminal_code=None,pause_generation=1,cancel_requested=False)
    current=view.model_copy(update={"lifecycle":"closed","activity":"terminal","finalized_at":datetime.now(timezone.utc),
        "delivery_status":DeliveryStatus.CANCELLED,"terminal_code":"cancelled_by_user","pause_generation":None,"cancel_requested":True})
    async def pause(**kwargs): return "a"*32
    async def busy(**kwargs): raise DurableExecutionRunBusyError()
    async def latest(**kwargs): return current
    async def summary(**kwargs): return None
    deps=WebApplicationDependencies(artifacts=None,durable=SimpleNamespace(get_state=busy,get_run=latest),
        definition=SimpleNamespace(contract=SimpleNamespace(contract_digest="sha256:"+"a"*64)),
        diagnostics=diagnostic,extraction_sink=sink,result_service=None,
        store=SimpleNamespace(read_pause=pause,get_summary=summary),clock=FixedClock(),disk_probe=lambda:10**10)
    service=WebApplicationService(settings=web_settings(),dependencies=deps)
    projected=await service._project(view=view,details=True)
    assert projected.run == current and projected.actions == () and projected.card is None
    await service.close()


@pytest.mark.asyncio
async def test_noncooperative_preparation_blocks_close_until_stopped():
    from app.web_application import WebApplicationDependencyError
    diagnostic=diagnostics()
    deps=WebApplicationDependencies(artifacts=None,durable=None,definition=None,diagnostics=diagnostic,
        extraction_sink=RunBoundExtractionInvocationSink(diagnostics=diagnostic),result_service=None,
        store=None,clock=FixedClock(),disk_probe=lambda:10**10)
    service=WebApplicationService(settings=web_settings(),dependencies=deps)
    entered,release=asyncio.Event(),asyncio.Event()
    async def preparing():
        run=uuid4()
        service._try_slot(run,uuid4())
        entered.set()
        try:
            try: await release.wait()
            except asyncio.CancelledError: await release.wait()
        finally: service._release_slot(run,asyncio.current_task())
    caller=asyncio.create_task(preparing())
    await entered.wait()
    with pytest.raises(WebApplicationDependencyError): await service.close()
    assert not caller.done() and not service._closed and service._closing
    release.set(); await caller
    await service.close()
