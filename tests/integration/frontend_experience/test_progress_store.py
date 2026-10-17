import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4
import pytest
from app.frontend_experience import PublicProgressError, PublicProgressPage, PublicProgressRecorder
from app.web_application.migrate import schema_fingerprint
from tests.unit.frontend_experience.test_models import body
from tests.integration.web_application.test_migrations import scratch, legacy, migrate, environment
from tests.integration.web_application.conftest import connection_kwargs


@pytest.mark.asyncio
async def test_first_idempotent_migration_and_old_schema_unchanged(pg_settings):
    import psycopg
    settings=await scratch(pg_settings,"m24")
    legacy(settings); assert migrate(settings).returncode==0
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(settings)) as connection:
        schemas=("artifact_store","durable_execution","web_application")
        before=[await schema_fingerprint(connection,schema=name) for name in schemas]
        for _ in range(2):
            result=migrate(settings,["app.frontend_experience.migrate"])
            assert result.returncode==0,(result.stdout,result.stderr)
        assert [await schema_fingerprint(connection,schema=name) for name in schemas]==before
        assert (await (await connection.execute("SELECT version_num FROM frontend_experience.alembic_version")).fetchone())[0]=="m24_0001_public_progress"
        await connection.execute("ALTER TABLE frontend_experience.capacity ADD COLUMN accidental text")
        assert migrate(settings,["app.frontend_experience.migrate","--check"]).returncode==2
        assert migrate(settings,["app.frontend_experience.migrate"]).returncode==2


@pytest.mark.asyncio
async def test_append_order_pagination_reuse_conflict_and_settle(progress_store):
    run,trace=uuid4(),uuid4()
    old=await progress_store.read(run_id=run,trace_id=trace)
    assert old.coverage=="unavailable" and not old.settled
    for index in range(53):
        record=await progress_store.append(run_id=run,trace_id=trace,event_key=f"event:{index}",body=body())
        assert record.sequence==index+1
    assert (await progress_store.append(run_id=run,trace_id=trace,event_key="event:0",body=body())).sequence==1
    first=await progress_store.read(run_id=run,trace_id=trace)
    assert len(first.items)==50 and first.next_after==50 and first.latest_sequence==53
    last=await progress_store.read(run_id=run,trace_id=trace,after=50)
    assert [item.sequence for item in last.items]==[51,52,53] and last.next_after is None
    assert await progress_store.append(run_id=run,trace_id=trace,event_key="event:0",body=body(status="accepted")) is None
    assert (await progress_store.read(run_id=run,trace_id=trace)).coverage=="limited"
    await progress_store.settle(run_id=run,trace_id=trace)
    assert await progress_store.append(run_id=run,trace_id=trace,event_key="late",body=body()) is None
    assert (await progress_store.read(run_id=run,trace_id=trace)).settled


@pytest.mark.asyncio
async def test_concurrent_append_and_capacity_limits(progress_store,pg_connection):
    run,trace=uuid4(),uuid4()
    await asyncio.gather(*(progress_store.append(run_id=run,trace_id=trace,event_key=f"parallel:{index}",body=body()) for index in range(8)))
    for index in range(8,512):
        await progress_store.append(run_id=run,trace_id=trace,event_key=f"parallel:{index}",body=body())
    assert await progress_store.append(run_id=run,trace_id=trace,event_key="overflow",body=body()) is None
    assert (await progress_store.read(run_id=run,trace_id=trace,after=500)).latest_sequence==512
    # Fault injection of a full capacity counter exercises the production hard stop.
    await pg_connection.execute("UPDATE frontend_experience.capacity SET record_count=16384")
    other=uuid4()
    assert await progress_store.append(run_id=other,trace_id=trace,event_key="global-full",body=body()) is None
    assert (await progress_store.read(run_id=other,trace_id=trace)).coverage=="limited"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage",["hash","identity","missing"])
async def test_corruption_is_not_empty_history(progress_store,pg_connection,damage):
    run,trace=uuid4(),uuid4()
    await progress_store.append(run_id=run,trace_id=trace,event_key="saved",body=body())
    if damage=="hash":
        await pg_connection.execute("UPDATE frontend_experience.public_records SET content_hash=%s WHERE run_id=%s",("0"*64,run))
    elif damage=="identity":
        with pytest.raises(PublicProgressError): await progress_store.read(run_id=run,trace_id=uuid4())
        return
    else:
        await pg_connection.execute("DELETE FROM frontend_experience.public_records WHERE run_id=%s",(run,))
    with pytest.raises(PublicProgressError): await progress_store.read(run_id=run,trace_id=trace)
    with pytest.raises(PublicProgressError): await progress_store.trip(run_id=run,trace_id=trace)


@pytest.mark.asyncio
async def test_limited_identity_and_new_process_readback(progress_store,pg_settings):
    run,trace=uuid4(),uuid4()
    await progress_store.append(run_id=run,trace_id=trace,event_key="saved",body=body())
    with pytest.raises(PublicProgressError):
        await progress_store.append(run_id=run,trace_id=uuid4(),event_key="wrong",body=body())
    for after in (-1,True,513):
        with pytest.raises(PublicProgressError): await progress_store.read(run_id=run,trace_id=trace,after=after)
    await progress_store.mark_limited(run_id=run,trace_id=trace)
    code="""
import asyncio,json,os
from psycopg_pool import AsyncConnectionPool
from uuid import UUID
from app.frontend_experience import PublicProgressStore
from tests.unit.web_application.fakes import FixedClock
async def main():
 async with AsyncConnectionPool('',kwargs=dict(host=os.environ['POSTGRES_HOST'],port=int(os.environ['POSTGRES_PORT']),dbname=os.environ['POSTGRES_DB'],user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],autocommit=True,options='-c timezone=UTC')) as pool:
  page=await PublicProgressStore(pool=pool,clock=FixedClock()).read(run_id=UUID(os.environ['M24_RUN']),trace_id=UUID(os.environ['M24_TRACE']))
  print(page.model_dump_json())
asyncio.run(main())
"""
    result=subprocess.run([sys.executable,"-c",code],env=environment(pg_settings)|{"M24_RUN":str(run),"M24_TRACE":str(trace)},capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    restored=PublicProgressPage.model_validate_json(result.stdout,strict=True)
    assert restored.coverage=="limited" and restored.items[0].body==body()


@pytest.mark.asyncio
async def test_real_production_graph_records_all_phases_and_read_is_side_effect_free(pg_settings,monkeypatch):
    from tests.integration.web_application.test_workflow_api import workflow_context, complete_run
    from app.web_application import WebApplicationService
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,_,factory):
        receipt,terminal,result,_=await complete_run(service,owner,provider)
        async with asyncio.timeout(5):
            while True:
                first=await service.read_progress(owner=owner,run_id=receipt.run_id)
                if first.settled: break
                await asyncio.sleep(.05)
        records=list(first.items); cursor=first.next_after
        while cursor:
            page=await service.read_progress(owner=owner,run_id=receipt.run_id,after=cursor)
            records.extend(page.items); cursor=page.next_after
        kinds={item.body.kind for item in records}
        assert {"requirement","hotel_search","hotel_candidates","hotel_selection","research_action","place_observation",
            "research_judgment","research_state","planning_action","hub_observation","directions","planning_state","result_day","result_index"} <= kinds,kinds
        assert first.coverage=="recorded"
        titled=await service.get_run(owner=owner,run_id=receipt.run_id)
        assert titled.trip is not None and titled.trip.city=="杭州"
        from app.result_formatting import ResultReadRequest
        state=await service._deps.durable.get_state(run_id=receipt.run_id)
        index=next(ref for ref in state.artifact_refs if ref.artifact_type=="result_formatting.index")
        loaded=await service._deps.result_service.load(ResultReadRequest(schema_version="2.0", state=state, index_ref=index))
        assert result.markdown==loaded.markdown
        for public,source in zip(result.days, loaded.days, strict=True):
            assert public.markdown==source.markdown
            assert public.reading.date==source.data.date and public.reading.theme==source.data.theme
            assert public.reading.visit_count==len(source.data.visits)
            assert public.reading.route_count==len(source.data.route_legs)
            assert public.reading.transport_minutes==sum(route.scheduled_minutes for route in source.data.route_legs)
        before=(provider.calls,maps.calls)
        await service.read_progress(owner=owner,run_id=receipt.run_id)
        assert before==(provider.calls,maps.calls)
        assert "trace_id" not in first.model_dump_json() and "artifact_id" not in first.model_dump_json()
        evidence=Path(os.environ["MODULE14_EVIDENCE_DIR"])
        (evidence/"production-progress.json").write_text(json.dumps({"kinds":sorted(kinds),"count":len(records),"settled":first.settled,"coverage":first.coverage}),encoding="utf-8")
