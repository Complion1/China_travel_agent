import asyncio
from dataclasses import replace
from pathlib import Path
import os
from uuid import uuid4
import pytest
import psycopg

from app.web_application import WebStartRequest,WebApplicationConfigurationError,WebApplicationDependencyError,WebApplicationUsageError
from app.web_application.store import DecisionError,WebMetadataStore
from app.artifact_persistence import create_artifact_persistence_runtime,ArtifactPersistenceConfigurationError
from app.workflow_contracts import ArtifactSchemaRegistry
from tests.unit.web_application.fakes import offline_boundaries,offline_factory,web_settings,wait_for_view,wait_for_summary
from .test_workflow_api import workflow_context,start_to_confirmation,accept_to_hotel,complete_run
from .test_migrations import scratch,legacy,migrate
from .conftest import connection_kwargs


@pytest.mark.asyncio
async def test_sequential_real_workflows_resource_profile(pg_settings,monkeypatch):
    """C17: five real completed graphs and two retained pauses, with measurements."""
    import gc,json,time,weakref
    from datetime import datetime,timezone,timedelta
    from app.web_application import assembly,WebControlRequest
    from app.workflow_contracts import EventEmitted
    from tests.unit.web_application.fakes import FixedClock
    refs=[];created_refs=[]
    import app.demo_workflow.diagnostics as diagnostics_module
    original_event=diagnostics_module.SafeWorkflowEvent
    def observed_event(*args,**kwargs):
        event=original_event(*args,**kwargs)
        created_refs.append(weakref.ref(event))
        return event
    monkeypatch.setattr(diagnostics_module,"SafeWorkflowEvent",observed_event)
    class Sink:
        async def emit(self,event):
            refs.append(weakref.ref(event))
            return EventEmitted(disposition="applied",event_id=event.event_id)
    monkeypatch.setattr(assembly,"NoOpSafeWorkflowEventSink",Sink)
    clock=FixedClock(datetime(2026,8,20,tzinfo=timezone.utc))
    def rss():
        return int(next(line.split()[1] for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("VmRSS:")))*1024
    def cgroup(name): return int((Path("/sys/fs/cgroup")/name).read_text().strip())
    start_bytes=cgroup("memory.current");rss_values=[rss()];maximum_connections=0;completed=[]
    async with workflow_context(pg_settings,monkeypatch,clock=clock) as (service,owner,_,provider,maps,database,_):
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as observer:
            disk_before=(await (await observer.execute("SELECT pg_database_size(current_database())")).fetchone())[0]
            for _ in range(5):
                receipt,terminal,result,summary=await complete_run(service,owner,provider)
                assert terminal.run.terminal_code=="workflow_completed" and summary.status=="saved" and len(result.days)==1
                completed.append(receipt.run_id);gc.collect();rss_values.append(rss())
                count=(await (await observer.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND datname=current_database() AND pid<>pg_backend_pid()")).fetchone())[0]
                maximum_connections=max(maximum_connections,count)
            assert all(ref() is None for ref in refs)
            clock.value+=timedelta(hours=1,seconds=1)
            first,_=await start_to_confirmation(service,owner,provider)
            second,_=await start_to_confirmation(service,owner,provider)
            gc.collect();retained=sum(ref() is not None for ref in created_refs);assert retained>=4
            for item in [first,second]:
                await service.cancel(owner=owner,run_id=item.run_id,request=WebControlRequest(schema_version="1.0",request_id=uuid4()))
                assert (await wait_for_summary(service,owner,item.run_id)).summary.terminal_code=="cancelled_by_user"
            gc.collect();assert all(ref() is None for ref in refs+created_refs)
            rss_values.append(rss())
            disk_after=(await (await observer.execute("SELECT pg_database_size(current_database())")).fetchone())[0]
            result=await service.read_result(owner=owner,run_id=completed[-1])
            response_bytes=len(result.model_dump_json().encode())
            assert maximum_connections<=12 and max(rss_values)<1024**3
            events=dict(line.split() for line in Path("/sys/fs/cgroup/memory.events").read_text().splitlines())
            assert int(events["oom_kill"])==0
            report=dict(status="PASS",offline=True,completed_runs=5,paused_runs=2,retained_pause_events=retained,
                rss_bytes=rss_values,cgroup_start_bytes=start_bytes,cgroup_peak_bytes=cgroup("memory.peak"),
                oom_kill=int(events["oom_kill"]),maximum_app_connections=maximum_connections,
                database_growth_bytes=disk_after-disk_before,response_json_bytes=response_bytes,
                provider_calls=provider.calls,map_calls=maps.calls)
            stamp=time.monotonic()
    report["close_seconds"]=time.monotonic()-stamp
    assert report["close_seconds"]<22 and service._closed and provider.closed and maps.closed
    (Path(os.environ["MODULE14_EVIDENCE_DIR"])/"capacity.json").write_text(json.dumps(report,sort_keys=True),encoding="utf-8")
    print(json.dumps(report,sort_keys=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("free,accepted",[(5368709119,False),(5368709120,True)])
async def test_low_disk_exact_boundary_does_not_change_existing_read_or_quota(pg_settings,monkeypatch,free,accepted):
    async with workflow_context(pg_settings,monkeypatch,disk_probe=lambda:free) as (service,owner,_,provider,_,database,_):
        admission=await service.get_admission(owner=owner,client_ip="192.0.2.1")
        assert admission.can_start == accepted
        request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message=provider.full_text)
        if accepted:
            receipt=await service.start(owner=owner,request=request,client_ip="192.0.2.1")
            await wait_for_view(service,owner,receipt.run_id,activity="awaiting_user")
        else:
            assert admission.reason == "capacity_unavailable"
            with pytest.raises(DecisionError) as error: await service.start(owner=owner,request=request,client_ip="192.0.2.1")
            assert error.value.code == "capacity_unavailable" and provider.calls == 0
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT count(*) FROM web_application.admissions")).fetchone() == (int(accepted),)


@pytest.mark.asyncio
async def test_probe_failure_is_dependency_not_fake_zero_or_quota(pg_settings,monkeypatch):
    def probe(): raise OSError("synthetic probe failure")
    async with workflow_context(pg_settings,monkeypatch,disk_probe=probe) as (service,owner,_,provider,_,_,_):
        with pytest.raises(WebApplicationDependencyError): await service.get_admission(owner=owner,client_ip="192.0.2.1")
        assert provider.calls == 0


@pytest.mark.asyncio
async def test_second_instance_refused_and_waiting_shutdown_creates_no_fake_terminal(pg_settings,monkeypatch):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,cookie,provider,maps,database,factory):
        receipt,_=await start_to_confirmation(service,owner,provider)
        another,maps2=await offline_boundaries()
        other_factory=offline_factory(monkeypatch=monkeypatch,settings=web_settings(pause_key_file=Path(os.environ["MODULE14_EVIDENCE_DIR"])/"isolated-pause-key.json"),
            pg_settings=database,provider=another,maps=maps2)
        with pytest.raises(WebApplicationConfigurationError):
            async with other_factory(): pass
        assert another.calls == maps2.calls == 0
        assert (await service.get_run(owner=owner,run_id=receipt.run_id)).run.activity == "awaiting_user"
    assert provider.closed and maps.closed
    with pytest.raises(WebApplicationUsageError): await service.get_run(owner=owner,run_id=receipt.run_id)
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
        assert await (await connection.execute("SELECT count(*) FROM web_application.run_summaries WHERE run_id=%s",(receipt.run_id,))).fetchone() == (0,)


@pytest.mark.asyncio
async def test_lost_dedicated_connection_stops_new_admission_and_closes_owned_resources(pg_settings,monkeypatch):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,database,_):
        lock=18951345809454160
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            result=await connection.execute("SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=%s AND objid=%s AND objsubid=1 AND database=(SELECT oid FROM pg_database WHERE datname=current_database())",(lock>>32,lock&0xffffffff))
            rows=await result.fetchall()
            assert len(rows) == 1
            await connection.execute("SELECT pg_terminate_backend(%s)",(rows[0][0],))
        with pytest.raises(WebApplicationDependencyError): await service._ready()
        with pytest.raises((DecisionError,WebApplicationUsageError)):
            await service.start(owner=owner,request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message=provider.full_text),client_ip="192.0.2.1")
        for _ in range(200):
            if provider.closed and maps.closed: break
            await asyncio.sleep(.05)
        assert provider.closed and maps.closed and provider.calls == maps.calls == 0


@pytest.mark.asyncio
async def test_completed_fault_supervisor_exception_is_consumed_and_cleanup_continues(pg_settings,monkeypatch):
    """C17/A-W05-N1: the real root owns even an already-failed monitor."""
    import gc
    from tests.unit.web_application.fakes import OfflineProvider
    loop=asyncio.get_running_loop()
    previous=loop.get_exception_handler()
    unhandled=[]
    loop.set_exception_handler(lambda _loop,context:unhandled.append(context))
    original_close=OfflineProvider.aclose
    closes=[]
    async def fail_after_close(self):
        await original_close(self)
        closes.append(self)
        raise RuntimeError("synthetic supplier close fault")
    monkeypatch.setattr(OfflineProvider,"aclose",fail_after_close)
    try:
        with pytest.raises(WebApplicationDependencyError):
            async with workflow_context(pg_settings,monkeypatch) as (service,_,_,provider,maps,database,_):
                async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
                    lock=18951345809454160
                    cursor=await connection.execute("SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=%s AND objid=%s AND objsubid=1 AND database=(SELECT oid FROM pg_database WHERE datname=current_database())",(lock>>32,lock&0xffffffff))
                    rows=await cursor.fetchall()
                    assert len(rows)==1
                    await connection.execute("SELECT pg_terminate_backend(%s)",(rows[0][0],))
                    async with asyncio.timeout(8):
                        while not provider.closed:
                            await asyncio.sleep(.05)
                    # Monitor shutdown reaches the supplier failure first, then
                    # still closes artifact pool, own metadata pool and lock.
                    async with asyncio.timeout(5):
                        while (await (await connection.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()")).fetchone())[0]:
                            await asyncio.sleep(.05)
                    await asyncio.sleep(.05)
                assert service._closed and maps.closed and provider.calls==maps.calls==0
        assert closes==[provider] and service._deps.artifacts._closed
        gc.collect()
        await asyncio.sleep(0)
        assert not unhandled
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.asyncio
async def test_new_profile_200000_four_gib_and_old_default_capacity_refused(pg_settings,monkeypatch):
    database=await scratch(pg_settings,"p")
    legacy(database); assert migrate(database).returncode == 0
    old=await create_artifact_persistence_runtime(settings=database,registry=ArtifactSchemaRegistry(()))
    snapshot=await old.get_capacity_snapshot()
    assert (snapshot.max_artifacts,snapshot.max_total_content_bytes) == (2048,64*1024**2)
    await old.close()
    provider,maps=await offline_boundaries()
    settings=web_settings(pause_key_file=Path(os.environ["MODULE14_EVIDENCE_DIR"])/"isolated-pause-key.json")
    factory=offline_factory(monkeypatch=monkeypatch,settings=settings,pg_settings=database,provider=provider,maps=maps)
    with pytest.raises(ArtifactPersistenceConfigurationError):
        async with factory(): pass
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
        assert await (await connection.execute("SELECT max_artifacts,max_total_content_bytes FROM artifact_store.capacity_state WHERE singleton_id=1")).fetchone() == (2048,64*1024**2)
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,_,new_database,_):
        snapshot=await service._deps.artifacts.get_capacity_snapshot()
        assert (snapshot.max_artifacts,snapshot.max_total_content_bytes) == (200000,4294967296)


@pytest.mark.asyncio
async def test_summary_save_failure_keeps_true_complete_result_and_releases_events(pg_settings,monkeypatch):
    from app.web_application import WebHotelRequest
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,_,_,_):
        receipt,view=await start_to_confirmation(service,owner,provider)
        view=await accept_to_hotel(service,owner,receipt.run_id,view)
        original=WebMetadataStore.save_summary
        attempts=[]
        async def fail(self,*,receipt):
            attempts.append(receipt.run_id)
            raise WebApplicationDependencyError()
        monkeypatch.setattr(WebMetadataStore,"save_summary",fail)
        await service.resume(owner=owner,run_id=receipt.run_id,request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),
            pause_generation=view.run.pause_generation,decision="select",ordinal=1))
        terminal=await wait_for_view(service,owner,receipt.run_id,activity="terminal")
        assert terminal.result_available, (terminal.run.phase, terminal.run.terminal_code)
        result=await service.read_result(owner=owner,run_id=receipt.run_id)
        assert len(result.days) == 1 and "2026-09-03" in result.days[0].markdown
        assert "2026-09-02" in result.markdown and "2026-09-04" in result.markdown
        for _ in range(200):
            status=(await service.read_summary(owner=owner,run_id=receipt.run_id)).status
            if status == "not_saved" and len(attempts) == 3: break
            await asyncio.sleep(.05)
        assert status == "not_saved" and len(attempts) == 3
        assert await service.read_result(owner=owner,run_id=receipt.run_id) == result


@pytest.mark.asyncio
async def test_stopped_start_metadata_write_failure_does_not_skip_real_summary_or_event_release(pg_settings,monkeypatch):
    import gc
    import weakref
    from app.web_application import assembly,WebStartRequest
    from app.workflow_contracts import EventEmitted
    event_refs=[]
    class Sink:
        async def emit(self,event):
            event_refs.append(weakref.ref(event))
            return EventEmitted(disposition="applied",event_id=event.event_id)
    monkeypatch.setattr(assembly,"NoOpSafeWorkflowEventSink",Sink)
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,database,_):
        stopped,release_post=asyncio.Event(),asyncio.Event()
        original_post=service._after_execution
        async def delayed_post(*,record,safe_absent):
            stopped.set(); await release_post.wait()
            return await original_post(record=record,safe_absent=safe_absent)
        monkeypatch.setattr(service,"_after_execution",delayed_post)
        request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message=provider.full_text)
        receipt=await service.start(owner=owner,request=request,client_ip="192.0.2.1")
        await wait_for_view(service,owner,receipt.run_id,activity="awaiting_user")
        await asyncio.wait_for(stopped.wait(),timeout=3)
        # Complete the real, checkpointed pause via public cancellation APIs
        # while its already-stopped start post is held at an explicit gate.
        assert (await service._deps.durable.request_cancel(run_id=receipt.run_id)).status == "requested"
        assert (await service._deps.durable.settle_cancel(run_id=receipt.run_id)).status == "cancelled"
        original=WebMetadataStore.mark_request
        failures=[]
        async def fail_post(self,**kwargs):
            record=kwargs["record"]
            if record.kind == "start" and service._execution_stopped(record.run_id) and not failures:
                failures.append(record.request_id)
                raise WebApplicationDependencyError()
            return await original(self,**kwargs)
        monkeypatch.setattr(WebMetadataStore,"mark_request",fail_post)
        release_post.set()
        terminal=await wait_for_view(service,owner,receipt.run_id,activity="terminal")
        summary=await wait_for_summary(service,owner,receipt.run_id)
        assert terminal.run.terminal_code == summary.summary.terminal_code == "cancelled_by_user"
        assert failures == [request.request_id] and provider.calls == 1 and maps.calls == 0
        assert (await service.start(owner=owner,request=request,client_ip="192.0.2.1")).run_id == receipt.run_id
        assert provider.calls == 1
        gc.collect()
        assert len(event_refs) >= 3 and all(ref() is None for ref in event_refs)
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT status FROM web_application.admissions")).fetchall() == [("counted",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("activity",["awaiting_user","executing"])
async def test_cancel_caller_disconnect_after_durable_intent_keeps_unique_maintenance(pg_settings,monkeypatch,activity):
    from app.web_application import WebControlRequest
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,database,_):
        original_execution=None
        if activity == "executing":
            provider.block=True
            receipt=await service.start(owner=owner,request=WebStartRequest(schema_version="1.0",request_id=uuid4(),
                user_message=provider.full_text),client_ip="192.0.2.1")
            await asyncio.wait_for(provider.entered.wait(),timeout=2)
            original_execution=next(iter(service._jobs))
        else:
            receipt,_=await start_to_confirmation(service,owner,provider)
        original_settle=service._deps.durable.settle_cancel
        settlement=[]
        active=[0,0]
        async def observed_settle(self,*,run_id):
            if original_execution is not None: assert original_execution.done()
            active[0]+=1; active[1]=max(active)
            try:
                settlement.append(run_id)
                return await original_settle(run_id=run_id)
            finally: active[0]-=1
        monkeypatch.setattr(type(service._deps.durable),"settle_cancel",observed_settle)
        entered,release=asyncio.Event(),asyncio.Event()
        original=WebMetadataStore.mark_request
        writes=[]
        async def delayed(self,*,record,status,reason=None):
            if record.kind == "cancel" and status == "dispatched":
                writes.append(record.request_id)
                entered.set()
                await release.wait()
            return await original(self,record=record,status=status,reason=reason)
        monkeypatch.setattr(WebMetadataStore,"mark_request",delayed)
        request=WebControlRequest(schema_version="1.0",request_id=uuid4())
        caller=asyncio.create_task(service.cancel(owner=owner,run_id=receipt.run_id,request=request))
        await asyncio.wait_for(entered.wait(),timeout=3)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError): await caller
        # The server-owned intent/maintenance survive the HTTP-like caller.
        release.set()
        second=WebControlRequest(schema_version="1.0",request_id=uuid4())
        await service.cancel(owner=owner,run_id=receipt.run_id,request=second)
        if activity == "executing":
            await asyncio.sleep(1.1)
            if not original_execution.done():
                assert not settlement and (await service.get_admission(owner=owner,client_ip="192.0.2.1")).reason == "busy"
            provider.release.set()
            # Cancelling an indeterminate initial provider call leaves M11's
            # original lease intact. Maintenance must classify it safely after
            # expiry; it cannot manufacture a cancellation terminal/summary.
            async with asyncio.timeout(75):
                while True:
                    settled=await service.get_run(owner=owner,run_id=receipt.run_id)
                    if settled.run.activity in {"blocked","terminal"}: break
                    await asyncio.sleep(.1)
            assert not settled.result_available
            if settled.run.activity == "blocked":
                assert settled.run.lifecycle == "blocked"
                assert (await service.read_summary(owner=owner,run_id=receipt.run_id)).status == "not_finalized"
            else:
                assert settled.run.lifecycle == "closed" and settled.run.terminal_code == "cancelled_by_user"
                async with asyncio.timeout(10):
                    while True:
                        summary=await service.read_summary(owner=owner,run_id=receipt.run_id)
                        if summary.status in {"saved","process_history_unavailable"}: break
                        await asyncio.sleep(.05)
                if summary.status == "saved":
                    assert summary.summary.terminal_code == "cancelled_by_user"
                    assert {item.name:item.value for item in summary.summary.counters}["workflow.started"] == 1
        else:
            terminal=await wait_for_view(service,owner,receipt.run_id,activity="terminal")
            summary=await wait_for_summary(service,owner,receipt.run_id)
            assert terminal.run.terminal_code == summary.summary.terminal_code == "cancelled_by_user"
        for _ in range(40):
            if (await service.get_request(owner=owner,request_id=request.request_id)).status == "dispatched": break
            await asyncio.sleep(.05)
        assert (await service.cancel(owner=owner,run_id=receipt.run_id,request=request)).status == "dispatched"
        assert writes == [request.request_id,second.request_id] and provider.calls == 1 and maps.calls == 0
        assert active[1] <= 1 and all(run == receipt.run_id for run in settlement)
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT count(*) FROM web_application.requests WHERE kind='cancel'")).fetchone() == (2,)
            assert await (await connection.execute("SELECT status FROM web_application.admissions")).fetchall() == [("counted",)]


@pytest.mark.asyncio
async def test_cancel_busy_backoff_releases_slot_for_another_actual_run(pg_settings,monkeypatch):
    from app.durable_execution import DurableExecutionRunBusyError
    from app.web_application import WebControlRequest
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,_,_,_):
        first,_=await start_to_confirmation(service,owner,provider)
        original=service._deps.durable.settle_cancel
        busy_seen=asyncio.Event()
        attempts=[]
        async def once_busy(self,*,run_id):
            attempts.append(run_id)
            if len(attempts) == 1:
                busy_seen.set()
                raise DurableExecutionRunBusyError()
            return await original(run_id=run_id)
        monkeypatch.setattr(type(service._deps.durable),"settle_cancel",once_busy)
        await service.cancel(owner=owner,run_id=first.run_id,
            request=WebControlRequest(schema_version="1.0",request_id=uuid4()))
        await asyncio.wait_for(busy_seen.wait(),timeout=3)
        for _ in range(30):
            if service._execution_stopped(first.run_id): break
            await asyncio.sleep(.01)
        # Backoff belongs to maintenance; it cannot reserve an execution slot.
        async with asyncio.timeout(.8):
            second=await service.start(owner=owner,request=WebStartRequest(schema_version="1.0",request_id=uuid4(),
                user_message=provider.full_text),client_ip="192.0.2.2")
        await wait_for_view(service,owner,second.run_id,activity="awaiting_user")
        assert (await wait_for_view(service,owner,first.run_id,activity="terminal")).run.terminal_code == "cancelled_by_user"
        assert len(attempts) == 2 and provider.calls == 2


@pytest.mark.asyncio
async def test_eight_cancel_handoffs_keep_one_maintenance_until_capacity_returns(pg_settings,monkeypatch):
    from app.web_application import WebControlRequest
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,database,_):
        first,_=await start_to_confirmation(service,owner,provider)
        async with asyncio.timeout(3):
            while service._posts: await asyncio.sleep(.01)
        entered,intents,writes,release=(asyncio.Event() for _ in range(4))
        original_cancel=service._deps.durable.request_cancel
        original_settle=service._deps.durable.settle_cancel
        original_write=WebMetadataStore.mark_request
        arrivals=[]; settlements=[]; metadata=[]
        async def gated_intent(self,*,run_id):
            arrivals.append(run_id)
            if len(arrivals)==8: entered.set()
            await intents.wait()
            return await original_cancel(run_id=run_id)
        async def gated_metadata(self,*,record,status,reason=None,**kwargs):
            if record.kind=="cancel" and status=="dispatched":
                metadata.append(record.request_id)
                if len(metadata)==8: writes.set()
                await release.wait()
            return await original_write(self,record=record,status=status,reason=reason,**kwargs)
        async def observed_settle(self,*,run_id):
            settlements.append(run_id)
            return await original_settle(run_id=run_id)
        monkeypatch.setattr(type(service._deps.durable),"request_cancel",gated_intent)
        monkeypatch.setattr(type(service._deps.durable),"settle_cancel",observed_settle)
        monkeypatch.setattr(WebMetadataStore,"mark_request",gated_metadata)
        requests=[WebControlRequest(schema_version="1.0",request_id=uuid4()) for _ in range(8)]
        callers=[asyncio.create_task(service.cancel(owner=owner,run_id=first.run_id,request=req)) for req in requests]
        try:
            await asyncio.wait_for(entered.wait(),timeout=5)
            intents.set()
            await asyncio.wait_for(writes.wait(),timeout=5)
            maintenance=service._maintenance[first.run_id]
            await asyncio.sleep(1.2)
            assert not maintenance.done() and not settlements and service._slot_run is None
            assert (await service.get_run(owner=owner,run_id=first.run_id)).run.cancel_requested
            release.set()
            assert all(item.status=="dispatched" for item in await asyncio.gather(*callers))
            terminal=await wait_for_view(service,owner,first.run_id,activity="terminal")
            summary=await wait_for_summary(service,owner,first.run_id)
            assert terminal.run.terminal_code==summary.summary.terminal_code=="cancelled_by_user"
            assert settlements==[first.run_id] and provider.calls==1 and maps.calls==0
            assert maintenance.done()
            async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
                assert await (await connection.execute("SELECT count(*) FROM web_application.requests WHERE kind='cancel' AND status='dispatched'")).fetchone()==(8,)
        finally:
            intents.set(); release.set()
            await asyncio.gather(*callers,return_exceptions=True)


@pytest.mark.asyncio
async def test_terminal_post_with_late_same_run_preparation_still_saves_and_releases(pg_settings,monkeypatch):
    import gc,weakref
    from app.web_application import assembly,WebHotelRequest
    from app.workflow_contracts import EventEmitted
    refs=[]
    class Sink:
        async def emit(self,event):
            refs.append(weakref.ref(event))
            return EventEmitted(disposition="applied",event_id=event.event_id)
    monkeypatch.setattr(assembly,"NoOpSafeWorkflowEventSink",Sink)
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,_,_):
        receipt,view=await start_to_confirmation(service,owner,provider)
        hotel=await accept_to_hotel(service,owner,receipt.run_id,view)
        stopped,post_release,preparing,prepare_release=(asyncio.Event() for _ in range(4))
        original_post=service._after_execution
        async def gated_post(*,record,safe_absent):
            if record.kind == "hotel":
                stopped.set(); await post_release.wait()
            return await original_post(record=record,safe_absent=safe_absent)
        monkeypatch.setattr(service,"_after_execution",gated_post)
        await service.resume(owner=owner,run_id=receipt.run_id,request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),
            pause_generation=hotel.run.pause_generation,decision="select",ordinal=1))
        await asyncio.wait_for(stopped.wait(),timeout=25)
        original_healthy=WebMetadataStore.healthy
        late=None
        async def gated_health(self):
            if asyncio.current_task() is late:
                preparing.set(); await prepare_release.wait()
            return await original_healthy(self)
        monkeypatch.setattr(WebMetadataStore,"healthy",gated_health)
        calls=(provider.calls,maps.calls)
        late=asyncio.create_task(service.resume(owner=owner,run_id=receipt.run_id,request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),
            pause_generation=hotel.run.pause_generation,decision="select",ordinal=1)))
        await asyncio.wait_for(preparing.wait(),timeout=3)
        post_release.set()
        saved=await wait_for_summary(service,owner,receipt.run_id)
        assert saved.status == "saved"
        gc.collect()
        counts={item.name:item.value for item in saved.summary.counters}
        assert counts["workflow.started"] == counts["workflow.terminal"] == counts["extraction.extract"] == 1
        assert counts["workflow.resumed"] == 2
        assert len(refs) == 5 and all(ref() is None for ref in refs)
        prepare_release.set()
        with pytest.raises(DecisionError) as rejected: await late
        assert rejected.value.code == "stale_pause" and (provider.calls,maps.calls) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("late_return",[False,True])
async def test_close_stops_last_control_preparation_before_background_handoff(pg_settings,monkeypatch,late_return):
    import app.web_application.service as module
    from app.web_application import WebHotelRequest
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,database,_):
        receipt,view=await start_to_confirmation(service,owner,provider)
        hotel=await accept_to_hotel(service,owner,receipt.run_id,view)
        entered,cancelled,release=(asyncio.Event() for _ in range(3))
        original=module.hotel_candidates
        async def last_read(**kwargs):
            result=await original(**kwargs)
            entered.set()
            try: await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                if not late_return: raise
                await release.wait()
            return result
        monkeypatch.setattr(module,"hotel_candidates",last_read)
        request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),pause_generation=hotel.run.pause_generation,
            decision="select",ordinal=1)
        calls=(provider.calls,maps.calls)
        caller=asyncio.create_task(service.resume(owner=owner,run_id=receipt.run_id,request=request))
        await asyncio.wait_for(entered.wait(),timeout=3)
        closing=asyncio.create_task(service.close())
        await asyncio.wait_for(cancelled.wait(),timeout=1)
        release.set()
        if late_return:
            with pytest.raises(DecisionError) as rejected: await caller
            assert rejected.value.code == "service_closing"
        else:
            with pytest.raises(asyncio.CancelledError): await caller
        await asyncio.wait_for(closing,timeout=4)
        assert (provider.calls,maps.calls) == calls
        durable_view=await service._deps.durable.get_run(run_id=receipt.run_id)
        assert durable_view.activity == "awaiting_user" and not durable_view.cancel_requested
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT status,reason FROM web_application.requests WHERE request_id=%s",
                (request.request_id,))).fetchone() == ("rejected","service_closing")
            assert await (await connection.execute("SELECT count(*) FROM web_application.run_summaries")).fetchone() == (0,)
