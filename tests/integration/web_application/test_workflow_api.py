"""Service-stage real M01/M03/M02/M13/M11/M12 chain, with typed external boundaries."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
import psycopg

from app.web_application import (WebStartRequest,WebClarifyRequest,WebConfirmRequest,WebHotelRequest,
    WebControlRequest,WebApplicationDependencyError)
from app.web_application.store import DecisionError, WebMetadataStore
from tests.unit.web_application.fakes import (web_settings,offline_boundaries,offline_factory,
    wait_for_view,wait_for_summary)
from .test_migrations import scratch,legacy,migrate
from .conftest import connection_kwargs


@asynccontextmanager
async def workflow_context(pg_settings,monkeypatch,*,settings=None,disk_probe=None,clock=None):
    database=await scratch(pg_settings,"w")
    legacy(database)
    assert migrate(database).returncode == 0
    root=Path(os.environ["MODULE14_EVIDENCE_DIR"])
    settings=settings or web_settings(pause_key_file=root/"isolated-pause-key.json")
    provider,maps=await offline_boundaries()
    factory=offline_factory(monkeypatch=monkeypatch,settings=settings,pg_settings=database,
        provider=provider,maps=maps,disk_probe=disk_probe,clock=clock)
    async with factory() as service:
        _,cookie=await service.bootstrap_session(cookie=None)
        owner=await service._authenticate(cookie=cookie)
        yield service,owner,cookie,provider,maps,database,factory


async def start_to_confirmation(service,owner,provider):
    receipt=await service.start(owner=owner,request=WebStartRequest(schema_version="1.0",request_id=uuid4(),
        user_message=provider.full_text),client_ip="192.0.2.1")
    view=await wait_for_view(service,owner,receipt.run_id,activity="awaiting_user")
    assert view.card.kind == "confirmation"
    return receipt,view


async def accept_to_hotel(service,owner,run_id,view):
    await service.resume(owner=owner,run_id=run_id,request=WebConfirmRequest(schema_version="1.0",request_id=uuid4(),
        pause_generation=view.run.pause_generation,decision="accept",revision_text=None,map_requested=False))
    async with asyncio.timeout(25):
        while True:
            view=await wait_for_view(service,owner,run_id,activity="awaiting_user")
            if view.card is not None and view.card.kind == "hotel": return view
            await asyncio.sleep(.05)


async def complete_run(service,owner,provider):
    receipt,view=await start_to_confirmation(service,owner,provider)
    view=await accept_to_hotel(service,owner,receipt.run_id,view)
    await service.resume(owner=owner,run_id=receipt.run_id,request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),
        pause_generation=view.run.pause_generation,decision="select",ordinal=1))
    terminal=await wait_for_view(service,owner,receipt.run_id,activity="terminal")
    result=await service.read_result(owner=owner,run_id=receipt.run_id)
    summary=await wait_for_summary(service,owner,receipt.run_id)
    return receipt,terminal,result,summary


@pytest.mark.asyncio
async def test_ambiguous_origin_hub_reasks_before_hotel_and_evidenced_form_completes_real_graph(pg_settings,monkeypatch):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,_,_):
        # Reproduce the historical extraction mistake at the provider boundary;
        # actual M02/M03/M01 must block it before any hotel/research/map call.
        old_name = provider.payload["departure_hub_name"]
        name_span = f'departure_hub_name: {json.dumps(old_name,ensure_ascii=False)}'
        provider.full_text = provider.full_text.replace(name_span, 'departure_hub_name: "上海"')
        provider.payload["departure_hub_name"] = "上海"
        provider.payload["departure_hub_type"] = None
        provider.payload["evidence"] = [dict(item, text='departure_hub_name: "上海"')
            if item["field_path"] == "time_window.departure.hub_name" else item
            for item in provider.payload["evidence"] if item["field_path"] != "time_window.departure.hub_type"]
        receipt = await service.start(owner=owner,request=WebStartRequest(schema_version="1.0",request_id=uuid4(),
            user_message=provider.full_text),client_ip="192.0.2.1")
        waiting = await wait_for_view(service,owner,receipt.run_id,activity="awaiting_user")
        assert waiting.card.kind == "clarification" and maps.calls == 0
        questions = {item.field.value:item for item in waiting.card.questions}
        assert questions["time_window.departure.hub_name"].label == "离开杭州的机场/车站"
        assert questions["time_window.departure.hub_type"].kind == "single"
        generation = waiting.run.pause_generation
        await service.resume(owner=owner,run_id=receipt.run_id,request=WebClarifyRequest(schema_version="1.0",
            request_id=uuid4(),pause_generation=generation,user_message="补充旅行需求：\n离开杭州的机场/车站：杭州萧山机场\n离开枢纽类型：机场"))
        async with asyncio.timeout(25):
            while True:
                ready = await service.get_run(owner=owner,run_id=receipt.run_id)
                if ready.card is not None and ready.card.kind == "confirmation" and ready.run.pause_generation != generation:
                    break
                await asyncio.sleep(.05)
        assert maps.calls == 0 and provider.calls == 2
        hotel = await accept_to_hotel(service,owner,receipt.run_id,ready)
        await service.resume(owner=owner,run_id=receipt.run_id,request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),
            pause_generation=hotel.run.pause_generation,decision="select",ordinal=1))
        done = await wait_for_view(service,owner,receipt.run_id,activity="terminal")
        assert done.run.terminal_code in {"workflow_completed","workflow_degraded"}
        assert done.result_available and done.input_issues == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_responses", [1, 2, 3])
async def test_generic_argument_recovery_real_graph_pg_and_read_only_reopen(pg_settings,monkeypatch,bad_responses):
    from dataclasses import replace
    from app.demo_workflow.artifacts import DemoControlArtifact
    from app.itinerary_planning import ItineraryPlanningArtifact, ItineraryPlanningModelActionArtifact
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,cookie,provider,maps,_,factory):
        original_first, original_next = provider.ainvoke_tools, provider.ainvoke_tool_continuation
        saved = {}
        async def first(request):
            good = await original_first(request)
            assert good.tool_calls and all(name == "geocode_hub" for _, name, _ in good.tool_calls)
            bad = list(good.raw_tool_calls)
            bad[0] = (bad[0][0], bad[0][1], '{}')
            saved.update(good=good, bad=replace(good, tool_calls=(), raw_tool_calls=tuple(bad)),
                         remaining=bad_responses - 1, map_calls=maps.calls)
            return saved["bad"]
        async def continuation(request):
            if saved.get("good") is None:
                return await original_next(request)
            # M02-generated rejection feedback covers every ID. No maps ran.
            assert maps.calls == saved["map_calls"]
            feedback = [json.loads(message["content"]) for message in request.messages if message["role"] == "tool"]
            assert len(feedback) >= len(saved["bad"].raw_tool_calls)
            assert all(item["status"] == "not_executed" for item in feedback)
            provider.calls += 1
            if saved["remaining"]:
                saved["remaining"] -= 1
                return saved["bad"]
            good, saved["good"] = saved["good"], None
            return good
        monkeypatch.setattr(provider,"ainvoke_tools",first)
        monkeypatch.setattr(provider,"ainvoke_tool_continuation",continuation)
        receipt,view=await start_to_confirmation(service,owner,provider)
        hotel=await accept_to_hotel(service,owner,receipt.run_id,view)
        await service.resume(owner=owner,run_id=receipt.run_id,request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),
            pause_generation=hotel.run.pause_generation,decision="select",ordinal=1))
        terminal=await wait_for_view(service,owner,receipt.run_id,activity="terminal")
        assert terminal.result_available == (bad_responses < 3)
        assert terminal.run.terminal_code in ({"workflow_completed","workflow_degraded"}
            if bad_responses < 3 else {"workflow_model_output_invalid"})
        summary=await wait_for_summary(service,owner,receipt.run_id)
        state=await service._deps.durable.get_state(run_id=receipt.run_id)
        control_ref=max((ref for ref in state.artifact_refs if ref.artifact_type=="demo.control"),
            key=lambda ref: ref.operation_id.logical_step)
        repository=service._deps.artifacts.repository
        control=(await repository.get(control_ref,expected_model=DemoControlArtifact)).content
        planning_refs=[ref for ref in state.artifact_refs if ref.artifact_type=="itinerary_planning.itinerary"
            and ref.operation_id.logical_step.endswith("_terminal")]
        assert len(planning_refs)==1
        planning_ref=planning_refs[0]
        if bad_responses < 3:
            assert control.planning_ref==planning_ref
        else:
            # Failed outcomes carry their failure_artifact_ref in state; the
            # control's planning_ref continues to mean a consistent itinerary.
            assert control.planning_ref is None and control.last_outcome=="failed"
        planning=(await repository.get(planning_ref,expected_model=ItineraryPlanningArtifact)).content
        assert planning.schema_version == planning_ref.schema_version == "4.0"
        budget=planning.record.budgets
        assert budget.format_repair_used == min(bad_responses,2)
        assert budget.model_invocations_consumed == sum((budget.preparation_used,budget.transport_used,
            budget.submit_repair_used,budget.supplement_request_used,budget.format_repair_used))
        actions=[(await repository.get(ref,expected_model=ItineraryPlanningModelActionArtifact)).content
            for ref in planning.directories.model_action_refs]
        assert sum(action.budget_category=="format_repair" for action in actions)==min(bad_responses,2)
        assert all(action.action_kind=="llm_failure" and action.invocation_metadata is None
            and not action.call_summaries for action in actions[:bad_responses])
        if bad_responses==3:
            assert planning.record.failure.error.code == "tool_arguments_invalid"
            assert not planning.record.retryable and maps.calls==saved["map_calls"]
        result=await service.read_result(owner=owner,run_id=receipt.run_id) if bad_responses < 3 else None
        before_calls=(provider.calls,maps.calls)
        source_bytes=planning.model_dump_json()
    # New DB pools and runtime read terminal artifacts/summary; no live history.
    async with factory() as reopened:
        reopened_owner=await reopened._authenticate(cookie=cookie)
        assert (await reopened.get_run(owner=reopened_owner,run_id=receipt.run_id)).run==terminal.run
        assert (await reopened.read_summary(owner=reopened_owner,run_id=receipt.run_id)).summary==summary.summary
        source=await reopened._deps.artifacts.repository.get(planning_ref,expected_model=ItineraryPlanningArtifact)
        assert source.ref==planning_ref and source.content.model_dump_json()==source_bytes
        if result is not None:
            assert await reopened.read_result(owner=reopened_owner,run_id=receipt.run_id)==result
        assert (provider.calls,maps.calls)==before_calls


@pytest.mark.asyncio
async def test_http_real_workflow_all_owner_routes_strict_dtos_and_zero_call_reads(pg_settings,monkeypatch):
    import httpx
    from app.web_application import create_web_application
    from app.web_application.models import WebRunView,WebResultView,WebSummaryView,WebSamplesView
    async with workflow_context(pg_settings,monkeypatch) as (service,_,_,provider,maps,_,_):
        @asynccontextmanager
        async def borrowed(): yield service
        app=create_web_application(settings=service._settings,service_factory=borrowed)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=("192.0.2.9",1200)),base_url="http://127.0.0.1:8080") as client:
                missing=str(uuid4())
                for suffix in ["","/events","/result","/summary"]:
                    assert (await client.get(f"/api/runs/{missing}"+suffix)).status_code==401
                assert (await client.get("/api/runs")).status_code==401
                client.cookies.set("cta_visitor","unknown-cookie")
                assert (await client.get("/api/runs")).status_code==401
                client.cookies.clear()
                session=(await client.get("/api/session")).json()
                headers={"Origin":"http://127.0.0.1:8080","X-CSRF-Token":session["csrf_token"]}
                owner=await service._authenticate(cookie=client.cookies.get("cta_visitor"))
                request_id=str(uuid4())
                response=await client.post("/api/runs",json={"schema_version":"1.0","request_id":request_id,"user_message":"我想去杭州"},headers=headers|{"X-Forwarded-For":"203.0.113.7"})
                assert response.status_code==202
                run_id=response.json()["run_id"];run=__import__('uuid').UUID(run_id)
                view=await wait_for_view(service,owner,run,activity="awaiting_user")
                assert view.card.kind=="clarification"
                assert (await client.get("/api/admission")).json()["ip_remaining"]==4
                assert (await client.get(f"/api/requests/{request_id}")).json()["status"]=="dispatched"
                response=await client.post(f"/api/runs/{run_id}/clarify",json={"schema_version":"1.0","request_id":str(uuid4()),"pause_generation":view.run.pause_generation,"user_message":provider.full_text},headers=headers)
                assert response.status_code==202
                async with asyncio.timeout(25):
                    while True:
                        data=await client.get(f"/api/runs/{run_id}");view=WebRunView.model_validate_json(data.content,strict=True)
                        if view.card and view.card.kind=="confirmation": break
                        await asyncio.sleep(.05)
                response=await client.post(f"/api/runs/{run_id}/confirm",json={"schema_version":"1.0","request_id":str(uuid4()),"pause_generation":view.run.pause_generation,"decision":"accept","revision_text":None,"map_requested":False},headers=headers)
                assert response.status_code==202
                async with asyncio.timeout(25):
                    while True:
                        view=WebRunView.model_validate_json((await client.get(f"/api/runs/{run_id}")).content,strict=True)
                        if view.card and view.card.kind=="hotel": break
                        await asyncio.sleep(.05)
                response=await client.post(f"/api/runs/{run_id}/hotel",json={"schema_version":"1.0","request_id":str(uuid4()),"pause_generation":view.run.pause_generation,"decision":"select","ordinal":1},headers=headers)
                assert response.status_code==202
                await wait_for_view(service,owner,run,activity="terminal");await wait_for_summary(service,owner,run)
                before=(provider.calls,maps.calls)
                result=WebResultView.model_validate_json((await client.get(f"/api/runs/{run_id}/result")).content,strict=True)
                summary=WebSummaryView.model_validate_json((await client.get(f"/api/runs/{run_id}/summary")).content,strict=True)
                assert len(result.days)==1 and summary.status=="saved"
                event=await client.get(f"/api/runs/{run_id}/events",headers={"Accept-Encoding":"gzip","Last-Event-ID":"ignored"})
                assert event.status_code==200 and '"card":null' in event.text and "content-encoding" not in event.headers
                assert event.headers["cache-control"]=="no-store" and event.text.count("event: snapshot")==1
                assert len((await client.get("/api/runs")).json()["items"])==1
                assert (await client.get("/health/ready")).status_code==200
                assert WebSamplesView.model_validate_json((await client.get("/api/samples")).content,strict=True).items[0].source_kind=="synthetic"
                assert (provider.calls,maps.calls)==before
                # Swap the cookie through the sole bootstrap route; every owner
                # endpoint gives exactly the same unknown/unauthorized response.
                client.cookies.clear();other=(await client.get("/api/session")).json()
                other_headers={"Origin":"http://127.0.0.1:8080","X-CSRF-Token":other["csrf_token"]}
                for suffix in ["","/events","/result","/summary"]:
                    denied=await client.get(f"/api/runs/{run_id}"+suffix)
                    assert denied.status_code==404 and set(denied.json())=={"schema_version","code","message","request_id","retry_after_seconds"}
                for action in ["cancel","recover"]:
                    denied=await client.post(f"/api/runs/{run_id}/{action}",headers=other_headers,json={"schema_version":"1.0","request_id":str(uuid4())})
                    assert denied.status_code==404
                assert not (await client.get("/api/runs")).json()["items"]
                assert (provider.calls,maps.calls)==before
                from datetime import timedelta
                service._deps.clock.value+=timedelta(days=30)
                assert (await client.get("/api/runs")).status_code==401
                assert (await client.get(f"/api/runs/{run_id}/events")).status_code==401
                assert (await client.post("/api/runs",json={"schema_version":"1.0","request_id":str(uuid4()),"user_message":"杭州"},headers=other_headers)).status_code==401
                assert (provider.calls,maps.calls)==before


@pytest.mark.asyncio
async def test_real_extract_repair_clarify_internal_revise_full_graph_summary_and_restart_fixture(pg_settings,monkeypatch):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,cookie,provider,maps,database,_):
        provider.repair=True
        first=await service.start(owner=owner,request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message="我想去杭州"),client_ip="192.0.2.1")
        view=await wait_for_view(service,owner,first.run_id,activity="awaiting_user")
        assert view.card.kind == "clarification"
        generation=view.run.pause_generation
        await service.resume(owner=owner,run_id=first.run_id,request=WebClarifyRequest(schema_version="1.0",request_id=uuid4(),
            pause_generation=generation,user_message=provider.full_text))
        for _ in range(150):
            view=await service.get_run(owner=owner,run_id=first.run_id)
            if view.card is not None and view.card.kind == "confirmation" and view.run.pause_generation != generation: break
            await asyncio.sleep(.05)
        assert view.card.kind == "confirmation"
        generation=view.run.pause_generation
        await service.resume(owner=owner,run_id=first.run_id,request=WebConfirmRequest(schema_version="1.0",request_id=uuid4(),
            pause_generation=generation,decision="revise",revision_text=provider.full_text,map_requested=False))
        for _ in range(150):
            view=await service.get_run(owner=owner,run_id=first.run_id)
            if view.card is not None and view.card.kind == "confirmation" and view.run.pause_generation != generation: break
            await asyncio.sleep(.05)
        assert view.card.kind == "confirmation" and view.run.pause_generation != generation
        hotel=await accept_to_hotel(service,owner,first.run_id,view)
        assert len(hotel.card.choices) == 3 and "uid" not in hotel.model_dump_json() and "pause_token" not in hotel.model_dump_json()
        await service.resume(owner=owner,run_id=first.run_id,request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),
            pause_generation=hotel.run.pause_generation,decision="select",ordinal=1))
        terminal=await wait_for_view(service,owner,first.run_id,activity="terminal")
        assert terminal.run.terminal_code in {"workflow_completed","workflow_degraded"} and terminal.result_available
        result=await service.read_result(owner=owner,run_id=first.run_id)
        assert len(result.days) == 1 and result.days[0].ordinal == 1 and "2026-09-03" in result.days[0].markdown
        assert "杭州" in result.markdown and "2026-09-02" in result.markdown and "2026-09-04" in result.markdown
        summary=await wait_for_summary(service,owner,first.run_id)
        counters={item.name:item.value for item in summary.summary.counters}
        assert counters["extraction.extract"] == counters["extraction.repair"] == 3
        assert counters["workflow.started"] == counters["workflow.terminal"] == 1 and counters["workflow.resumed"] == 4
        calls=(provider.calls,maps.calls)
        for _ in range(2):
            assert await service.read_result(owner=owner,run_id=first.run_id) == result
            assert (await service.read_summary(owner=owner,run_id=first.run_id)).summary == summary.summary
            await service.list_runs(owner=owner)
        assert (provider.calls,maps.calls) == calls
        second,waiting=await start_to_confirmation(service,owner,provider)
        waiting=await accept_to_hotel(service,owner,second.run_id,waiting)
        root=Path(os.environ["MODULE14_EVIDENCE_DIR"])
        import hashlib
        (root/"workflow-restart-fixture.json").write_text(json.dumps(dict(database=database.database,cookie=cookie,
            run_id=str(first.run_id),trace_id=str(summary.summary.trace_id),summary_hash="sha256:"+hashlib.sha256(
                __import__('app.web_application.security',fromlist=['canonical_json']).canonical_json(summary.summary.model_dump(mode="json"))).hexdigest(),
            result_hash=hashlib.sha256(result.markdown.encode()).hexdigest(),pause_run_id=str(second.run_id),
            pause_generation=waiting.run.pause_generation)),encoding="utf-8")


@pytest.mark.asyncio
async def test_same_id_no_new_run_or_quota_and_stale_other_owner_controls(pg_settings,monkeypatch):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,cookie,provider,maps,database,_):
        request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message=provider.full_text)
        first=await service.start(owner=owner,request=request,client_ip="192.0.2.1")
        waiting=await wait_for_view(service,owner,first.run_id,activity="awaiting_user")
        calls=provider.calls
        assert (await service.start(owner=owner,request=request,client_ip="198.51.100.1")).run_id == first.run_id
        with pytest.raises(DecisionError) as error:
            await service.start(owner=owner,request=request.model_copy(update={"user_message":"不同正文"}),client_ip="192.0.2.1")
        assert error.value.code == "request_conflict" and provider.calls == calls
        _,other_cookie=await service.bootstrap_session(cookie=None)
        other=await service._authenticate(cookie=other_cookie)
        for run in (first.run_id,uuid4()):
            with pytest.raises(DecisionError) as error:
                await service.read_summary(owner=other,run_id=run)
            assert error.value.code == "not_found"
        with pytest.raises(DecisionError) as error:
            await service.resume(owner=owner,run_id=first.run_id,request=WebConfirmRequest(schema_version="1.0",request_id=uuid4(),
                pause_generation=waiting.run.pause_generation+1,decision="accept",revision_text=None,map_requested=False))
        assert error.value.code == "stale_pause"
        with pytest.raises(DecisionError) as error:
            await service.recover(owner=owner,run_id=first.run_id,request=WebControlRequest(schema_version="1.0",request_id=uuid4()))
        assert error.value.code == "recovery_unavailable"
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT count(*) FROM web_application.admissions WHERE status='counted'")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_disconnect_does_not_cancel_start_busy_has_no_quota(pg_settings,monkeypatch):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,_,database,_):
        provider.block=True
        gate=asyncio.Event()
        original=WebMetadataStore.mark_request
        async def delayed(self,**kwargs):
            result=await original(self,**kwargs)
            if kwargs["record"].kind == "start" and kwargs["status"] == "dispatched": await gate.wait()
            return result
        monkeypatch.setattr(WebMetadataStore,"mark_request",delayed)
        request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message=provider.full_text)
        caller=asyncio.create_task(service.start(owner=owner,request=request,client_ip="192.0.2.1"))
        await asyncio.wait_for(provider.entered.wait(),5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError): await caller
        with pytest.raises(DecisionError) as error:
            await service.start(owner=owner,request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message=provider.full_text),client_ip="192.0.2.1")
        assert error.value.code == "busy" and provider.calls == 1
        gate.set(); provider.release.set()
        receipt=await service.get_request(owner=owner,request_id=request.request_id)
        await wait_for_view(service,owner,receipt.run_id,activity="awaiting_user")
        assert (await service.start(owner=owner,request=request,client_ip="192.0.2.1")).run_id == receipt.run_id
        assert provider.calls == 1
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT count(*) FROM web_application.admissions")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_unknown_metadata_commit_is_read_only_until_safe_reconciliation_no_resend(pg_settings,monkeypatch):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,database,_):
        request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message=provider.full_text)
        original=WebMetadataStore.reserve
        async def ambiguous(self,**kwargs):
            await original(self,**kwargs)
            raise WebApplicationDependencyError()
        with monkeypatch.context() as patch:
            patch.setattr(WebMetadataStore,"reserve",ambiguous)
            with pytest.raises(WebApplicationDependencyError):
                await service.start(owner=owner,request=request,client_ip="192.0.2.1")
        assert (await service.get_request(owner=owner,request_id=request.request_id)).status == "confirming"
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT status,reason FROM web_application.requests")).fetchone() == ("claimed",None)
        await service._startup_reconcile()
        receipt=await service.get_request(owner=owner,request_id=request.request_id)
        assert receipt.status == "uncertain" and receipt.reason == "admission_unconfirmed"
        assert await service.start(owner=owner,request=request,client_ip="192.0.2.1") == receipt
        assert provider.calls == maps.calls == 0
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT status FROM web_application.admissions")).fetchall() == [("reserved",)]
            assert await (await connection.execute("SELECT count(*) FROM web_application.requests")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_startup_confirms_existing_m11_without_reexecuting_or_extra_quota(pg_settings,monkeypatch):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,database,_):
        request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message=provider.full_text)
        receipt=await service.start(owner=owner,request=request,client_ip="192.0.2.1")
        await wait_for_view(service,owner,receipt.run_id,activity="awaiting_user")
        calls=(provider.calls,maps.calls)
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            await connection.execute("UPDATE web_application.requests SET status='claimed',reason=NULL WHERE request_id=%s",(request.request_id,))
            await connection.execute("UPDATE web_application.admissions SET status='reserved' WHERE run_id=%s",(receipt.run_id,))
        assert (await service.get_request(owner=owner,request_id=request.request_id)).status == "confirming"
        await service._startup_reconcile()
        assert (await service.get_request(owner=owner,request_id=request.request_id)).status == "dispatched"
        assert (provider.calls,maps.calls) == calls
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT status FROM web_application.admissions")).fetchall() == [("counted",)]


@pytest.mark.asyncio
async def test_extraction_provider_exception_requires_explicit_safe_recovery_no_fake_terminal(pg_settings,monkeypatch):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,cookie,provider,maps,database,_):
        provider.fail_extraction=True
        request=WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message=provider.full_text)
        receipt=await service.start(owner=owner,request=request,client_ip="192.0.2.1")
        # M11 retains its fixed 60-second lease after an indeterminate initial
        # invocation. Reads must not shorten it or silently reexecute the graph.
        view=await wait_for_view(service,owner,receipt.run_id,activity="recovery_required",timeout=75)
        assert view.run.lifecycle == "open" and view.card is None and "recover" in view.actions
        assert (await service.read_summary(owner=owner,run_id=receipt.run_id)).status == "not_finalized"
        assert provider.calls == 1 and maps.calls == 0
    # A new root must first expose the persisted state with no automatic work.
    # Explicit recovery then applies M11's non-recoverable requirements rule;
    # it must not retry the already dispatched provider request.
    new_provider,new_maps=await offline_boundaries()
    settings=web_settings(pause_key_file=Path(os.environ["MODULE14_EVIDENCE_DIR"])/"isolated-pause-key.json")
    new_factory=offline_factory(monkeypatch=monkeypatch,settings=settings,pg_settings=database,
        provider=new_provider,maps=new_maps)
    async with new_factory() as service:
        owner=await service._authenticate(cookie=cookie)
        assert (await service.get_run(owner=owner,run_id=receipt.run_id)).run.activity == "recovery_required"
        assert new_provider.calls == new_maps.calls == 0
        await service.recover(owner=owner,run_id=receipt.run_id,
            request=WebControlRequest(schema_version="1.0",request_id=uuid4()))
        blocked=await wait_for_view(service,owner,receipt.run_id,activity="blocked")
        assert blocked.run.lifecycle == "blocked" and not blocked.result_available and blocked.actions == ()
        assert new_provider.calls == new_maps.calls == 0
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT status FROM web_application.admissions")).fetchall() == [("counted",)]
            assert await (await connection.execute("SELECT count(*) FROM web_application.run_summaries")).fetchone() == (0,)


@pytest.mark.asyncio
@pytest.mark.parametrize("action",["decline","cancel","provider_failure"])
async def test_real_terminal_failure_decline_cancel_summary_no_formal_result(pg_settings,monkeypatch,action):
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,_,_):
        if action == "provider_failure": provider.fail=True
        receipt,view=await start_to_confirmation(service,owner,provider)
        if action in {"decline","provider_failure"}:
            hotel=await accept_to_hotel(service,owner,receipt.run_id,view)
            await service.resume(owner=owner,run_id=receipt.run_id,request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),
                pause_generation=hotel.run.pause_generation,decision="decline" if action == "decline" else "select",
                ordinal=None if action == "decline" else 1))
        elif action == "cancel":
            request=WebControlRequest(schema_version="1.0",request_id=uuid4())
            assert (await service.cancel(owner=owner,run_id=receipt.run_id,request=request)).status == "dispatched"
            assert (await service.cancel(owner=owner,run_id=receipt.run_id,request=request)).run_id == receipt.run_id
        terminal=await wait_for_view(service,owner,receipt.run_id,activity="terminal")
        assert not terminal.result_available
        if action == "provider_failure": assert terminal.run.terminal_code == "workflow_provider_failed"
        summary=await wait_for_summary(service,owner,receipt.run_id)
        assert summary.summary.terminal_code == terminal.run.terminal_code
        with pytest.raises(DecisionError) as error: await service.read_result(owner=owner,run_id=receipt.run_id)
        assert error.value.code == "result_not_ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption",["index_version","index_digest","day_digest"])
async def test_actual_m12_rejects_corrupt_index_or_day_without_partial_delivery(pg_settings,monkeypatch,corruption):
    from dataclasses import replace
    from app.result_formatting import ResultFormattingService,ResultIndexArtifact,ResultDayArtifact
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,_,provider,maps,_,_):
        receipt,_,result,summary=await complete_run(service,owner,provider)
        actual=service._deps
        reads=[]
        class CorruptRead:
            async def get(self,ref,*,expected_model):
                returned=await actual.artifacts.repository.get(ref,expected_model=expected_model)
                reads.append(expected_model)
                changed=None
                if expected_model is ResultIndexArtifact and corruption.startswith("index_"):
                    changed=returned.content.model_copy(update={"schema_version":"0.9"} if corruption == "index_version"
                        else {"bundle_digest":"sha256:"+"0"*64})
                if expected_model is ResultDayArtifact and corruption == "day_digest":
                    changed=returned.content.model_copy(update={"bundle_digest":"sha256:"+"0"*64})
                return returned if changed is None else returned.model_copy(update={"content":changed})
        calls=(provider.calls,maps.calls)
        service._deps=replace(actual,result_service=ResultFormattingService(artifact_repository=CorruptRead()))
        try:
            with pytest.raises(WebApplicationDependencyError): await service.read_result(owner=owner,run_id=receipt.run_id)
            with pytest.raises(WebApplicationDependencyError): await service.get_run(owner=owner,run_id=receipt.run_id)
            assert ResultIndexArtifact in reads and (corruption != "day_digest" or ResultDayArtifact in reads)
            assert await service.read_summary(owner=owner,run_id=receipt.run_id) == summary
        finally: service._deps=actual
        assert await service.read_result(owner=owner,run_id=receipt.run_id) == result
        assert (provider.calls,maps.calls) == calls
@pytest.mark.asyncio
async def test_real_executing_phase_is_ephemeral_owner_safe_and_reads_do_not_run_graph(pg_settings,monkeypatch):
    import httpx
    from app.web_application import create_web_application,WebRunView
    from app.durable_execution import DurableExecutionStateError
    from app.workflow_contracts import WorkflowPhase
    async with workflow_context(pg_settings,monkeypatch) as (service,owner,cookie,provider,maps,_,factory):
        owner_loop=asyncio.get_running_loop()
        original_observe=service._deps.diagnostics._observe_phase
        def observe_on_owner_loop(state):
            assert asyncio.get_running_loop() is owner_loop
            original_observe(state)
        monkeypatch.setattr(service._deps.diagnostics,"_observe_phase",observe_on_owner_loop)
        provider.block=True
        provider.entered.clear();provider.release.clear()
        receipt=await service.start(owner=owner,request=WebStartRequest(schema_version="1.0",request_id=uuid4(),
            user_message=provider.full_text),client_ip="192.0.2.21")
        await asyncio.wait_for(provider.entered.wait(),10)
        # The initial call has not produced a checkpoint pointer yet. M11
        # rejects that read as StateError before its active-lease Busy guard.
        with pytest.raises(DurableExecutionStateError):
            await service._deps.durable.get_state(run_id=receipt.run_id)
        @asynccontextmanager
        async def borrowed(): yield service
        app=create_web_application(settings=service._settings,service_factory=borrowed)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=("192.0.2.21",1200)),
                    base_url="http://127.0.0.1:8080",cookies={"cta_visitor":cookie}) as client:
                before=(provider.calls,maps.calls)
                for _ in range(3):
                    response=await client.get(f"/api/runs/{receipt.run_id}")
                    assert response.status_code==200
                    view=WebRunView.model_validate_json(response.content,strict=True)
                    assert view.run.activity=="executing" and view.run.phase is None and view.card is None
                    assert view.progress_phase is WorkflowPhase.REQUIREMENTS
                with service._snapshot_scope():
                    snapshot=await service.get_run(owner=owner,run_id=receipt.run_id)
                assert snapshot.progress_phase is WorkflowPhase.REQUIREMENTS and snapshot.card is None
                assert (provider.calls,maps.calls)==before
                client.cookies.clear();await client.get("/api/session")
                assert (await client.get(f"/api/runs/{receipt.run_id}")).status_code==404
                assert (provider.calls,maps.calls)==before
        # Complete the observed operation to its real, verified pause. M11
        # cannot promise cancelled_by_user before an initial checkpoint exists.
        provider.release.set();provider.block=False
        paused=await wait_for_view(service,owner,receipt.run_id,activity="awaiting_user")
        assert paused.card.kind=="confirmation" and paused.progress_phase is None
        assert service._deps.diagnostics.get_active_phase(run_id=receipt.run_id,trace_id=paused.run.trace_id) is None
        before=(provider.calls,maps.calls)
        # This borrowed API lifespan did not close the service. Public cancel
        # must hide the marker and safely finish from the verified checkpoint.
        await service.cancel(owner=owner,run_id=receipt.run_id,
            request=WebControlRequest(schema_version="1.0",request_id=uuid4()))
        cancelling=await service.get_run(owner=owner,run_id=receipt.run_id)
        assert cancelling.progress_phase is None and cancelling.run.cancel_requested
        terminal=await wait_for_view(service,owner,receipt.run_id,activity="terminal")
        assert terminal.progress_phase is None and terminal.run.terminal_code=="cancelled_by_user"
        assert service._deps.diagnostics.get_active_phase(run_id=receipt.run_id,trace_id=terminal.run.trace_id) is None
        assert (provider.calls,maps.calls)==before
    async with factory() as reopened:
        old_owner=await reopened._authenticate(cookie=cookie)
        assert (await reopened.get_run(owner=old_owner,run_id=receipt.run_id)).progress_phase is None
        assert (provider.calls,maps.calls)==before
