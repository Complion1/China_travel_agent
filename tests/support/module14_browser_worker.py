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


async def serve(port):
    pg=isolated_settings()
    ip=socket.gethostbyname(socket.gethostname())
    settings=web_settings(public_origin=f"http://{ip}:{port}",port=port,
        pause_key_file=Path(os.environ["MODULE14_EVIDENCE_DIR"])/"isolated-pause-key.json")
    provider,maps=await offline_boundaries()
    patch=pytest.MonkeyPatch()
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
                "offline":True,"sse_count":len(app.state.streams.entries)}
    @app.post("/__test/control")
    async def control(request:Request):
        payload=await request.json()
        if set(payload)-{"block","release","disk","fail","fail_session","advance_seconds"}: raise RuntimeError("unknown fixture control")
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
