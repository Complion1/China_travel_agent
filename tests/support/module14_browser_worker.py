"""Isolated HTTP test server/capacity worker; never copied into runtime image."""
from __future__ import annotations
import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime,timezone,timedelta
import json
import logging
import os
from pathlib import Path
import socket
import sys
import pytest
import uvicorn
from fastapi import Request
from starlette.responses import HTMLResponse

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from app.artifact_persistence import load_artifact_persistence_settings
from app.web_application import create_web_application
from tests.unit.web_application.fakes import offline_boundaries,offline_factory,web_settings,FixedClock


def isolated_settings():
    prefix=os.environ.get("MODULE14_TEST_PREFIX","")
    if not prefix.startswith("cta-m14-") or os.environ.get("MODULE14_TEST_IDENTITY_TOKEN")!=prefix+"-identity":
        raise RuntimeError("isolated identity required")
    pg=load_artifact_persistence_settings(environ=os.environ)
    if prefix.replace("-","_") not in pg.database: raise RuntimeError("isolated database required")
    return pg


def configure_pending_candidate_supplement(provider, maps):
    """Keep a purpose-qualified, unqueried details target in the real directory."""
    from app.candidate_research import NoUsefulActionProposed, ProposedActionBatch, ProposedDetailsAction
    from tests.support.candidate_research_fakes import structured
    assert provider.research.call_count == 0
    # 公共合法夹具已经用自身定向午餐证据确认 r2，判断仍由实际工作单生成。
    assert any(item.candidate_handle == "c:8" and item.meal_intent.value == "lunch"
               and item.verdict.value == "confirmed"
               and tuple(value.handle_id for value in item.evidence_handles) == ("e:2",)
               for item in provider.research.base_patch.meal_updates)
    def no_progress_details(_):
        objectives=json.loads(provider.research.prompts[-1].user_text)["objectives"]
        objective=next(item for item in objectives if item["kind"]=="details_maturity")
        return structured(ProposedActionBatch(actions=(ProposedDetailsAction(
            ordinal=0,objective_id=objective["objective_id"],uid="r3"),)))
    # Retain the three visit details and their original evidence positions,
    # leave qualified r2 pending, then make one real empty details query.
    provider.research.responses[4:4]=[no_progress_details,provider.research.responses[3],
        structured(NoUsefulActionProposed(reason_code="prior_no_progress"))]
    maps.research.responses.append(maps.research.responses[-1])


async def serve(port):
    pg=isolated_settings()
    ip=socket.gethostbyname(socket.gethostname())
    settings=web_settings(public_origin=f"http://{ip}:{port}",port=port,
        pause_key_file=Path(os.environ["MODULE14_EVIDENCE_DIR"])/"isolated-pause-key.json")
    provider,maps=await offline_boundaries()
    patch=pytest.MonkeyPatch()
    # Test-only external-boundary barriers hold actual graph nodes in flight.
    # The production observer and public DTO are never substituted.
    phase_blocks = {}
    phases = {"requirements", "hotel_selection", "candidate_research", "itinerary_planning", "output_formatting"}
    async def phase_wait(phase):
        event = phase_blocks.get(phase)
        if event is not None: await event.wait()
    from app.llm_runtime import ModelRole
    from app.result_formatting import ResultFormattingService
    original_json,original_tools,original_continue = provider.ainvoke_json,provider.ainvoke_tools,provider.ainvoke_tool_continuation
    original_search,original_details,original_prepare = maps.search_places,maps.get_place_details,ResultFormattingService.prepare
    tool_trace = []
    supplement_target = None
    def observe_tools(kind, response):
        # Bounded fixture metadata only: no prompts, arguments or response bodies.
        tool_trace.append({"kind":kind,"supplement":provider.planning.supplement,
            "names":[call[1] for call in response.tool_calls]})
        del tool_trace[:-16]
        return response
    async def json_call(request):
        await phase_wait("requirements" if request.profile.role is ModelRole.REQUIREMENT_EXTRACTION else "candidate_research")
        return await original_json(request)
    async def tool_call(request):
        nonlocal supplement_target
        await phase_wait("itinerary_planning")
        response = await original_tools(request)
        if response.tool_calls and response.tool_calls[0][1] == "request_candidate_supplement":
            # Observe the actual fixture target without changing model arguments.
            encoded = request.user.partition("\nBEGIN_TYPED_PLANNING_STATE\n")[2].partition("\nEND_TYPED_PLANNING_STATE")[0]
            candidates = [candidate for observation in json.loads(encoded)["candidates"] for candidate in observation["candidates"]]
            handle=response.tool_calls[0][2]["proposed_gaps"][0]["target"]["candidate_handles"][0]
            selected=next(candidate for candidate in candidates if candidate["candidate_handle"]==handle)
            supplement_target={"details_status":selected["details_status"],
                "initial_details_success_count":sum(candidate["details_status"]=="success" for candidate in candidates)}
        return observe_tools("initial",response)
    async def continuation(request):
        await phase_wait("itinerary_planning")
        return observe_tools("continuation",await original_continue(request))
    async def search(request):
        if request.query in {"酒店", "宾馆", "住宿"} or request.query.endswith(" 酒店"):
            await phase_wait("hotel_selection")
        return await original_search(request)
    async def details(request):
        await phase_wait("candidate_research")
        response=await original_details(request)
        if provider.planning.supplement:
            from app.baidu_map import PlaceDetailsSuccess,PlaceDetailsNoResult,PlaceDetails,BaiduCoordinate
            assert type(response) is PlaceDetailsSuccess
            if request.uid in {"a1","a2","a3","a4","a5","r3"}:
                return PlaceDetailsNoResult(provenance=response.provenance)
            # This scenario requests details in supplemental order. Match the
            # typed external reply to that request instead of a fixed queue UID.
            ordinal=int(request.uid[1:])
            return PlaceDetailsSuccess(provenance=response.provenance,details=PlaceDetails(uid=request.uid,
                name=request.uid,address=f"{request.uid} Road",
                coordinate=BaiduCoordinate(latitude=30.1+ordinal/1000,longitude=120.1+ordinal/1000),
                category="museum" if request.uid.startswith("a") else "restaurant",brand=None,telephone=None,
                rating=4.0,business_hours=("10:00-20:00",)))
        return response
    async def prepare(instance, request):
        await phase_wait("output_formatting")
        return await original_prepare(instance, request)
    patch.setattr(provider,"ainvoke_json",json_call)
    patch.setattr(provider,"ainvoke_tools",tool_call)
    patch.setattr(provider,"ainvoke_tool_continuation",continuation)
    patch.setattr(maps,"search_places",search)
    patch.setattr(maps,"get_place_details",details)
    patch.setattr(ResultFormattingService,"prepare",prepare)
    disk=[20*1024**3]
    clock=FixedClock(datetime(2026,8,20,tzinfo=timezone.utc))
    factory=offline_factory(monkeypatch=patch,settings=settings,pg_settings=pg,provider=provider,maps=maps,disk_probe=lambda:disk[0],clock=clock)
    owned=[]
    @asynccontextmanager
    async def observed_factory():
        async with factory() as service:
            owned.append(service)
            yield service
    app=create_web_application(settings=settings,service_factory=observed_factory)
    # Only this test worker marks its actual pages as offline. Runtime has no switch.
    for route in app.routes:
        if getattr(route,"path",None) in {"/","/tasks/{run_id}","/history","/samples"}:
            original=route.dependant.call
            def wrapper(call):
                async def marked(**kwargs):
                    response=await call(**kwargs)
                    html=response.body.decode("utf-8").replace("<main>","<main><p id=offline-badge>离线资格测试：模型和地图为替身，业务主图与数据库为真实模块。</p>",1)
                    return HTMLResponse(html)
                return marked
            route.dependant.call=wrapper(original)
    @app.get("/__test/info")
    async def info():
        return {"provider_calls":provider.calls,"map_calls":maps.calls,"full_text":provider.full_text,
                "offline":True,"sse_count":len(app.state.streams.entries),"fixture_tool_trace":tool_trace,
                "fixture_supplement_target":supplement_target,"fixture_research_calls":provider.research.call_count}
    @app.post("/__test/control")
    async def control(request:Request):
        payload=await request.json()
        if set(payload)-{"block","release","disk","fail","fail_session","advance_seconds","phase_blocks","phase_release","supplement"}: raise RuntimeError("unknown fixture control")
        if "phase_release" in payload:
            phase = payload["phase_release"]
            if phase not in phases: raise RuntimeError("invalid phase barrier")
            event = phase_blocks.pop(phase,None)
            if event is not None: event.set()
        if "phase_blocks" in payload:
            values = payload["phase_blocks"]
            if type(values) is not list or len(values)>5 or any(value not in phases for value in values):
                raise RuntimeError("invalid phase barriers")
            for phase in values: phase_blocks.setdefault(phase,asyncio.Event())
        if "supplement" in payload:
            if type(payload["supplement"]) is not bool: raise RuntimeError("invalid supplement fixture")
            provider.planning.supplement = payload["supplement"]
            if payload["supplement"]:
                configure_pending_candidate_supplement(provider, maps)
        if "advance_seconds" in payload:
            value=payload["advance_seconds"]
            if type(value) is not int or not 0<=value<=172800: raise RuntimeError("invalid fixture clock")
            clock.value+=timedelta(seconds=value)
        if "block" in payload:
            provider.block=payload["block"]
            if provider.block: provider.entered.clear();provider.release.clear()
        if payload.get("release"): provider.release.set();provider.block=False
        if "disk" in payload: disk[0]=payload["disk"]
        if "fail" in payload: provider.fail=payload["fail"]
        if "fail_session" in payload: maps.fail_session=payload["fail_session"]
        return {"status":"fixture_updated"}
    root=Path(os.environ["MODULE14_EVIDENCE_DIR"])
    (root/"browser-origin.json").write_text(json.dumps({"origin":settings.public_origin,"offline":True}),encoding="utf-8")
    from app.web_application.__main__ import _serve_application
    try: await _serve_application(app=app,settings=settings)
    finally:
        patch.undo()
        (root/"browser-shutdown.json").write_text(json.dumps({"service_closed":bool(owned and owned[0]._closed),
            "provider_closed":provider.closed,"map_closed":maps.closed,"sse_count":len(app.state.streams.entries)}),encoding="utf-8")


def shutdown_probe():
    import urllib.request,time
    root=Path(os.environ["MODULE14_EVIDENCE_DIR"])
    value=json.loads((root/"sse-shutdown-fixture.json").read_text())
    origin=json.loads((root/"browser-origin.json").read_text())["origin"]
    request=urllib.request.Request(origin+f"/api/runs/{value['run_id']}/events",headers={"Cookie":"cta_visitor="+value["cookie"]})
    with urllib.request.urlopen(request,timeout=70) as response:
        assert response.status==200
        first=response.readline();data=response.readline();response.readline()
        assert first==b"event: snapshot\n"
        snapshot=json.loads(data.decode()[6:]);assert snapshot["run"]["activity"]=="awaiting_user" and snapshot["card"] is None
        (root/"shutdown-probe-ready.json").write_text('{"nonterminal_sse":true}',encoding="utf-8")
        stamp=time.monotonic()
        while response.readline(): pass
        (root/"shutdown-probe-result.json").write_text(json.dumps({"status":"PASS","eof":True,"seconds":time.monotonic()-stamp}),encoding="utf-8")
    return 0


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--mode",choices=["serve","capacity","shutdown-probe"],required=True);parser.add_argument("--port",type=int,default=8080)
    args=parser.parse_args();logging.disable(logging.CRITICAL)
    if args.mode=="serve": asyncio.run(serve(args.port));return 0
    isolated_settings()
    if args.mode=="shutdown-probe": return shutdown_probe()
    return int(pytest.main(["-v","--tb=short","--maxfail=1","-p","no:cacheprovider","tests/integration/web_application/test_capacity_shutdown.py::test_sequential_real_workflows_resource_profile"]))


if __name__=="__main__": raise SystemExit(main())
