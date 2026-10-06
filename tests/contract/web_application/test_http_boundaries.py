"""HTTP protocol tests; business qualification separately uses the real PG graph."""
from contextlib import asynccontextmanager,contextmanager
from datetime import datetime,timezone
import json
import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest
from starlette.testclient import TestClient
from app.web_application import (create_web_application, WebSessionView, WebVisitorContext,
    WebAdmissionView,WebCommandReceipt,WebHistoryPage,WebRunView,WebSummaryView)
from app.web_application.store import DecisionError
from app.durable_execution import DurableRunView
from app.workflow_contracts import DeliveryStatus,WorkflowPhase
from tests.unit.web_application.fakes import web_settings


class BoundaryService:
    def __init__(self): self.calls=[]; self.entered=0; self.exited=0; self.error=None; self._closing=False; self._closed=False
    async def bootstrap_session(self,*,cookie):
        self.calls.append("bootstrap")
        return WebSessionView(schema_version="1.0",csrf_token="safe-csrf",expires_at=datetime(2026,11,4,tzinfo=timezone.utc),visitor_ttl_seconds=2592000),None if cookie=="known" else "known"
    async def _authenticate(self,*,cookie):
        if cookie!="known": return None
        return WebVisitorContext(schema_version="1.0",visitor_hash="a"*64,expires_at=datetime(2026,11,4,tzinfo=timezone.utc))
    def _verify_csrf(self,*,owner,supplied): return supplied=="safe-csrf"
    async def start(self,*,owner,request,client_ip):
        self.calls.append((request,client_ip))
        if self.error: raise self.error
        return WebCommandReceipt(schema_version="1.0",request_id=request.request_id,run_id=uuid4(),status="dispatched",reason=None)
    async def get_admission(self,*,owner,client_ip):
        return WebAdmissionView(schema_version="1.0",can_start=True,global_remaining=200,visitor_remaining=20,ip_remaining=5,reason=None,retry_after_seconds=None)
    async def get_run(self,*,owner,run_id): raise DecisionError("not_found")
    async def get_request(self,*,owner,request_id): raise DecisionError("not_found")
    async def list_runs(self,*,owner,cursor):
        return WebHistoryPage(schema_version="1.0",items=(),next_cursor=None)
    async def _ready(self):
        if self.error: raise self.error
    @contextmanager
    def _snapshot_scope(self): yield


@pytest.fixture
def boundary():
    service=BoundaryService()
    @asynccontextmanager
    async def factory():
        service.entered+=1
        try: yield service
        finally: service.exited+=1
    app=create_web_application(settings=web_settings(),service_factory=factory)
    with TestClient(app,base_url="http://127.0.0.1:8080",client=("192.0.2.3",1200),raise_server_exceptions=False) as client:
        yield client,service,app
    assert service.entered==service.exited==1


def headers(): return {"Origin":"http://127.0.0.1:8080","X-CSRF-Token":"safe-csrf"}
def body(**changes): return {"schema_version":"1.0","request_id":str(uuid4()),"user_message":"杭州",**changes}


def test_session_cookie_no_sliding_no_secret_and_security_headers(boundary):
    client,service,_=boundary
    first=client.get("/api/session")
    assert first.status_code==200 and set(first.json())=={"schema_version","csrf_token","expires_at","visitor_ttl_seconds"}
    cookie=first.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=2592000" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie and "Domain" not in cookie
    assert "set-cookie" not in client.get("/api/session").headers
    assert first.headers["cache-control"]=="no-store" and "script-src 'self'" in first.headers["content-security-policy"]
    assert client.get("/api/admission").json()["ip_remaining"]==5


@pytest.mark.parametrize("path",["/api/admission","/api/runs","/api/runs/"+str(uuid4()),"/api/requests/"+str(uuid4()),"/api/runs/"+str(uuid4())+"/events"])
def test_missing_identity_is_401_not_bootstrap(boundary,path):
    client,service,_=boundary
    response=client.get(path)
    assert response.status_code==401 and "set-cookie" not in response.headers and not service.calls


@pytest.mark.parametrize("change",[{"Host":"localhost:8080"},{"Origin":"http://evil.invalid"},{"Origin":"null"},{"Origin":""},{"X-CSRF-Token":"wrong"}])
def test_fixed_host_origin_and_csrf_reject_before_business(boundary,change):
    client,service,_=boundary;client.get("/api/session")
    response=client.post("/api/runs",json=body(),headers=headers()|change)
    assert response.status_code==403 and len(service.calls)==1
    assert "evil.invalid" not in response.text and "wrong" not in response.text


@pytest.mark.parametrize("change",[{"request_id":"ABC"},{"unexpected":"canary-private"},{"user_message":""},{"user_message":"x"*8001},{"user_message":True},{"schema_version":1}])
def test_strict_body_fixed_error_and_no_business(boundary,change):
    client,service,_=boundary;client.get("/api/session")
    response=client.post("/api/runs",json=body(**change),headers=headers())
    assert response.status_code==422 and response.json()["message"]=="请求格式不正确" and "canary-private" not in response.text
    assert len(service.calls)==1


def test_characters_are_not_utf8_bytes_and_forwarded_ip_ignored(boundary):
    client,service,_=boundary;client.get("/api/session")
    response=client.post("/api/runs",json=body(user_message="中"*8000),headers=headers()|{"X-Forwarded-For":"203.0.113.8"})
    assert response.status_code==202 and len(service.calls[-1][0].user_message)==8000
    assert service.calls[-1][1]=="192.0.2.3"


def test_oversized_invalid_utf8_surrogate_and_no_unlimited_body(boundary):
    client,service,_=boundary;client.get("/api/session")
    for content,expected in [(b"x"*65537,413),(b"\xff",422),(json.dumps(body(user_message="\ud800")).encode(),422)]:
        response=client.post("/api/runs",content=content,headers=headers()|{"Content-Type":"application/json"})
        assert response.status_code==expected and "Traceback" not in response.text
    assert len(service.calls)==1


@pytest.mark.parametrize("code,status,retry",[("busy",409,None),("quota_exceeded",429,3600),("capacity_unavailable",503,None),("admission_unconfirmed",503,None)])
def test_safe_error_mapping_original_id_and_retry_after(boundary,code,status,retry):
    client,service,_=boundary;client.get("/api/session");service.error=DecisionError(code,retry)
    request=body();response=client.post("/api/runs",json=request,headers=headers())
    assert response.status_code==status and response.json()["request_id"]==request["request_id"]
    assert response.headers.get("retry-after")== (str(retry) if retry else None)


def test_uniform_unknown_routes_no_docs_or_arbitrary_artifacts(boundary):
    client,service,_=boundary;client.get("/api/session")
    for path in ["/docs","/openapi.json","/redoc","/api/artifacts/secret","/static/../../pyproject.toml","/api/runs/"+str(uuid4())]:
        assert client.get(path).status_code==404
    assert client.get("/api/runs",params={"cursor":"a"*513}).status_code==422
    assert client.get("/api/runs",params={"visitor_id":"a"}).status_code==422
    service.error=RuntimeError("canary-secret-dsn-user-input")
    response=client.post("/api/runs",json=body(),headers=headers())
    assert response.status_code==503 and "canary" not in response.text


def test_public_pages_and_gzip_private_nostore(boundary):
    client,_,_=boundary
    for path in ["/","/history","/samples","/tasks/"+str(uuid4())]:
        response=client.get(path,headers={"Accept-Encoding":"gzip"})
        assert response.status_code==200 and response.headers["content-encoding"]=="gzip"
        assert response.headers["x-content-type-options"]=="nosniff"
    assert client.get("/history").headers["cache-control"]=="no-store"
    assert client.get("/static/app.js").headers["cache-control"].startswith("public")


def test_clarification_questions_are_readonly_projection_and_not_a_patch_write_api(boundary):
    from app.web_application import WebInteractionCard, WebClarificationQuestion
    from app.requirements import RequirementFieldPath
    client,service,_=boundary;client.get("/api/session");run_id=uuid4()
    async def view(**kwargs):
        return WebRunView(schema_version="1.0",run=DurableRunView(run_id=run_id,trace_id=run_id,
            created_at=datetime(2026,10,5,tzinfo=timezone.utc),finalized_at=None,lifecycle="open",activity="awaiting_user",
            phase=WorkflowPhase.REQUIREMENTS,delivery_status=DeliveryStatus.AWAITING_USER,terminal_code=None,pause_generation=1,cancel_requested=False),
            card=WebInteractionCard(schema_version="1.0",kind="clarification",title="补充旅行需求",paragraphs=("原领域问题",),facts=(),choices=(),
                questions=(WebClarificationQuestion(schema_version="1.0",field=RequirementFieldPath.CHILDREN_COUNT,
                    label="儿童数量",kind="number",minimum=0,maximum=100),)),
            actions=("clarify",),result_available=False,summary_status="not_finalized")
    service.get_run=view
    response=client.get(f"/api/runs/{run_id}")
    assert response.status_code==200 and response.json()["card"]["questions"][0]["field"]=="party.children_count"
    assert set(response.json()["card"])=={"schema_version","kind","title","paragraphs","facts","choices","questions"}
    before=len(service.calls)
    for extra in ({"answers":{"party.children_count":2}},{"questions":[]},{"current":{"requirement":"canary-private"}}):
        response=client.post(f"/api/runs/{run_id}/clarify",json=body(pause_generation=1,**extra),headers=headers())
        assert response.status_code==422 and "canary-private" not in response.text
    assert len(service.calls)==before


def test_terminal_sse_snapshot_has_no_card_and_not_compressed(boundary):
    client,service,app=boundary;client.get("/api/session")
    run=uuid4()
    async def terminal_view(**kwargs):
        return WebRunView(schema_version="1.0",run=DurableRunView(run_id=run,trace_id=uuid4(),
            created_at=datetime(2026,10,5,tzinfo=timezone.utc),finalized_at=datetime(2026,10,5,tzinfo=timezone.utc),
            lifecycle="closed",activity="terminal",phase=WorkflowPhase.REQUIREMENTS,
            delivery_status=DeliveryStatus.CANCELLED,terminal_code="cancelled_by_user",pause_generation=None,cancel_requested=True),
            card=None,actions=(),result_available=False,summary_status="not_saved")
    service.get_run=terminal_view
    response=client.get(f"/api/runs/{run}/events",headers={"Accept-Encoding":"gzip","Last-Event-ID":"ignored"})
    assert response.status_code==200 and "content-encoding" not in response.headers and response.headers["cache-control"]=="no-store"
    assert response.text.count("event: snapshot")==1 and '"card":null' in response.text
    assert not app.state.streams.entries


@pytest.mark.asyncio
async def test_sse_real_asgi_32_limit_replacement_read_cadence_and_identity_expiry():
    service=BoundaryService();reads=[];expired=False
    original_auth=service._authenticate
    async def authenticate(**kwargs):
        if expired: return None
        return await original_auth(**kwargs)
    service._authenticate=authenticate
    async def view(*,owner,run_id):
        reads.append((run_id,time.monotonic()))
        return WebRunView(schema_version="1.0",run=DurableRunView(run_id=run_id,trace_id=run_id,
            created_at=datetime(2026,10,5,tzinfo=timezone.utc),finalized_at=None,lifecycle="open",activity="awaiting_user",
            phase=WorkflowPhase.REQUIREMENTS,delivery_status=DeliveryStatus.AWAITING_USER,terminal_code=None,pause_generation=1,cancel_requested=False),
            card=None,actions=("cancel",),result_available=False,summary_status="not_finalized")
    service.get_run=view
    @asynccontextmanager
    async def factory(): yield service
    app=create_web_application(settings=web_settings(),service_factory=factory)
    def connect(run_id):
        output=[];first=asyncio.Event()
        scope={"type":"http","asgi":{"version":"3.0","spec_version":"2.4"},"http_version":"1.1","scheme":"http","method":"GET",
            "path":f"/api/runs/{run_id}/events","raw_path":f"/api/runs/{run_id}/events".encode(),"root_path":"","query_string":b"",
            "headers":[(b"host",b"127.0.0.1:8080"),(b"cookie",b"cta_visitor=known"),(b"accept-encoding",b"gzip")],"client":("192.0.2.1",123),"server":("127.0.0.1",8080)}
        async def receive(): await asyncio.Event().wait()
        async def send(message):
            output.append(message)
            if message["type"]=="http.response.body": first.set()
        return asyncio.create_task(app(scope,receive,send)),output,first
    tasks=[]
    async with app.router.lifespan_context(app):
        try:
            runs=[uuid4() for _ in range(32)]
            for run_id in runs:
                task,output,first=connect(run_id);tasks.append(task);await asyncio.wait_for(first.wait(),2)
                assert output[0]["status"]==200
            assert len(app.state.streams.entries)==32
            denied,output,first=connect(uuid4());tasks.append(denied);await asyncio.wait_for(denied,2)
            assert output[0]["status"]==503 and len(app.state.streams.entries)==32
            replaced,output,first=connect(runs[0]);tasks.append(replaced);await asyncio.wait_for(first.wait(),3)
            assert tasks[0].done() and output[0]["status"]==200 and len(app.state.streams.entries)==32
            await asyncio.sleep(2.15)
            for run_id in runs[1:]:
                stamps=[stamp for identity,stamp in reads if identity==run_id]
                assert len(stamps)>=2 and all(b-a>=1.95 for a,b in zip(stamps,stamps[1:]))
            assert all("content-encoding" not in {name.decode() for name,_ in entry.get("headers",[])} for entry in output if entry["type"]=="http.response.start")
            expired=True
            await asyncio.wait_for(asyncio.gather(*tasks),3)
            assert not app.state.streams.entries
        finally:
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)


@pytest.mark.asyncio
async def test_streamed_body_cap_before_full_read():
    service=BoundaryService()
    @asynccontextmanager
    async def factory(): yield service
    app=create_web_application(settings=web_settings(),service_factory=factory)
    chunks=[b"x"*32768,b"x"*32768,b"x",b"must-not-read"]
    consumed=[];output=[]
    scope={"type":"http","asgi":{"version":"3.0"},"http_version":"1.1","scheme":"http","method":"POST","path":"/api/runs","raw_path":b"/api/runs","root_path":"","query_string":b"",
        "headers":[(b"host",b"127.0.0.1:8080"),(b"origin",b"http://127.0.0.1:8080"),(b"cookie",b"cta_visitor=known"),(b"content-type",b"application/json"),(b"x-csrf-token",b"safe-csrf")],"client":("192.0.2.1",123),"server":("127.0.0.1",8080)}
    async def receive():
        chunk=chunks[len(consumed)];consumed.append(len(chunk))
        return {"type":"http.request","body":chunk,"more_body":True}
    async def send(message): output.append(message)
    async with app.router.lifespan_context(app): await app(scope,receive,send)
    assert output[0]["status"]==413 and consumed==[32768,32768,1] and not service.calls


@pytest.mark.asyncio
async def test_actual_uvicorn_does_not_report_zero_when_factory_shutdown_fails():
    import socket
    from app.web_application.__main__ import _serve_application
    from app.web_application import WebApplicationDependencyError
    service=BoundaryService()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1",0));port=probe.getsockname()[1]
    settings=web_settings(public_origin=f"http://127.0.0.1:{port}",port=port)
    @asynccontextmanager
    async def failed_exit():
        yield service
        raise WebApplicationDependencyError()
    app=create_web_application(settings=settings,service_factory=failed_exit)
    task=asyncio.create_task(_serve_application(app=app,settings=settings))
    async with asyncio.timeout(5):
        while not hasattr(app.state,"service"): await asyncio.sleep(.01)
    service._closing=True
    with pytest.raises(RuntimeError): await asyncio.wait_for(task,5)
    assert app.state.lifecycle_failed


def test_production_logging_retains_safe_codes_without_raw_error_or_secret():
    import io,logging
    from app.web_application.__main__ import _SafeCodes
    output=io.StringIO();handler=_SafeCodes(output)
    for name,text,args in [("app.web_application.service","workflow_execution_failed",()),
        ("provider.vendor","canary-key-dsn-private-body",()),("app.web_application.api","raw-%s",("canary-key",))]:
        record=logging.LogRecord(name,logging.ERROR,"fixture",1,text,args,None)
        handler.handle(record)
    assert output.getvalue()=="workflow_execution_failed\n"
