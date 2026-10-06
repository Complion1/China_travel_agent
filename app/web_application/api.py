"""Fixed HTTP boundary: bounded input, owner authorization, safe projections."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager,AbstractAsyncContextManager
from dataclasses import dataclass, field
import logging
from pathlib import Path
from uuid import UUID
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .errors import (WebApplicationError, WebApplicationUsageError,
                     WebApplicationConflictError, WebApplicationCapacityError)
from .models import (MESSAGES, WebApiError, WebStartRequest, WebClarifyRequest,
    WebConfirmRequest, WebHotelRequest, WebControlRequest, WebSessionView,
    WebAdmissionView, WebCommandReceipt, WebHistoryPage, WebRunView,
    WebResultView, WebSummaryView, WebSamplesView, fresh)
from .store import DecisionError
from .settings import WebApplicationSettings
from .service import WebApplicationService

_LOG = logging.getLogger(__name__)
_STATIC = Path(__file__).with_name("static")
_STATUS = {"invalid_request":422, "body_too_large":413, "origin_rejected":403,
    "csrf_rejected":403, "not_found":404, "busy":409, "quota_exceeded":429,
    "request_conflict":409, "stale_pause":409, "pause_unavailable":409,
    "recovery_unavailable":409, "result_not_ready":409}
_SECURITY = {"Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "Referrer-Policy":"no-referrer", "X-Content-Type-Options":"nosniff"}


class _HttpFailure(Exception):
    def __init__(self, code, *, status=None, request_id=None, retry=None):
        self.code, self.status, self.request_id, self.retry = code, status, request_id, retry


def _failure(code, *, status=None, request_id=None, retry=None):
    body=WebApiError(schema_version="1.0", code=code, message=MESSAGES[code],
        request_id=request_id, retry_after_seconds=retry)
    headers={**_SECURITY, "Cache-Control":"no-store"}
    if retry is not None: headers["Retry-After"]=str(retry)
    return JSONResponse(body.model_dump(mode="json"), status_code=status or _STATUS.get(code,503), headers=headers)


def _json(value, expected, *, status=200):
    return JSONResponse(fresh(value,expected).model_dump(mode="json"),status_code=status)


def _header(request, name):
    values=request.headers.getlist(name)
    if len(values)>1: raise _HttpFailure("invalid_request")
    return values[0] if values else None


def _cookie(request, settings):
    raw=_header(request,"cookie")
    if raw is None: return None
    pairs=[piece.strip().split("=",1) for piece in raw.split(";")]
    values=[pair[1] for pair in pairs if len(pair)==2 and pair[0]==settings.cookie_name]
    if len(values)>1: raise _HttpFailure("csrf_rejected",status=401)
    return values[0] if values else None


def _uuid(value):
    try:
        parsed=UUID(value)
        if str(parsed)!=value: raise ValueError()
        return parsed
    except Exception: raise _HttpFailure("invalid_request") from None


def _ip(request):
    if request.client is None: raise _HttpFailure("invalid_request")
    return request.client.host


async def _owner(request):
    try:
        owner=await request.app.state.service._authenticate(cookie=_cookie(request,request.app.state.settings))
        if owner is None: raise _HttpFailure("csrf_rejected",status=401)
        return owner
    except DecisionError as error:
        if error.code=="csrf_rejected": raise _HttpFailure("csrf_rejected",status=401) from None
        raise


async def _body(request, expected):
    if _header(request,"content-type") not in {"application/json","application/json; charset=utf-8"}:
        raise _HttpFailure("invalid_request")
    length=_header(request,"content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal(): raise _HttpFailure("invalid_request")
        if int(length)>65536: raise _HttpFailure("body_too_large")
    chunks=bytearray()
    async for chunk in request.stream():
        if len(chunks)+len(chunk)>65536: raise _HttpFailure("body_too_large")
        chunks.extend(chunk)
    try:
        value=expected.model_validate_json(bytes(chunks),strict=True)
        # JSON permits isolated surrogate escapes; downstream UTF-8 storage does not.
        value.model_dump_json().encode("utf-8",errors="strict")
        for name in ("user_message","revision_text"):
            text=getattr(value,name,None)
            if text is not None: text.encode("utf-8",errors="strict")
    except Exception: raise _HttpFailure("invalid_request") from None
    request.state.request_id=value.request_id
    return value


class _Boundary:
    def __init__(self, app, *, settings): self.app,self.settings=app,settings
    async def __call__(self, scope, receive, send):
        if scope["type"]!="http": return await self.app(scope,receive,send)
        request=Request(scope)
        try:
            if request.headers.getlist("host") != [self.settings.host]:
                raise _HttpFailure("origin_rejected")
            if scope["method"]=="POST" and request.headers.getlist("origin") != [self.settings.public_origin]:
                raise _HttpFailure("origin_rejected")
        except _HttpFailure as error:
            return await _failure(error.code,status=error.status)(scope,receive,send)
        async def safe_send(message):
            if message["type"]=="http.response.start":
                headers=list(message["headers"])
                for key,value in _SECURITY.items(): headers.append((key.lower().encode(),value.encode()))
                private=scope["path"].startswith(("/api/","/tasks/","/history","/health/")) and scope["path"]!="/api/samples"
                if private or message["status"]>=400:
                    headers=[pair for pair in headers if pair[0].lower()!=b"cache-control"]
                    headers.append((b"cache-control",b"no-store"))
                message={**message,"headers":headers}
            await send(message)
        await self.app(scope,receive,safe_send)


@dataclass(eq=False)
class _Stream:
    key: tuple
    task: asyncio.Task | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)


class _Streams:
    def __init__(self): self.entries={}; self.lock=asyncio.Lock()
    async def acquire(self,key):
        async with self.lock:
            old=self.entries.get(key)
            if old is not None:
                old.stop.set()
                if old.task is not None: old.task.cancel()
                try: await asyncio.wait_for(old.done.wait(),timeout=2)
                except TimeoutError: raise _HttpFailure("capacity_unavailable") from None
            if len(self.entries)>=32: raise _HttpFailure("capacity_unavailable")
            entry=_Stream(key,task=asyncio.current_task())
            self.entries[key]=entry
            return entry
    def drop(self,entry):
        if self.entries.get(entry.key) is entry: self.entries.pop(entry.key)
        entry.done.set()
    async def close(self):
        entries=tuple(self.entries.values())
        for entry in entries:
            entry.stop.set()
            if entry.task is not None: entry.task.cancel()
        if entries:
            await asyncio.wait_for(asyncio.gather(*(entry.done.wait() for entry in entries)),timeout=3)


class _EventResponse(StreamingResponse):
    def __init__(self,body,streams,entry):
        super().__init__(body,media_type="text/event-stream",headers={"Cache-Control":"no-store","X-Accel-Buffering":"no"})
        self.streams,self.entry=streams,entry
    async def __call__(self,scope,receive,send):
        self.entry.task=asyncio.current_task()
        try:
            await super().__call__(scope,receive,send)
        except asyncio.CancelledError:
            if not self.entry.stop.is_set(): raise
        finally: self.streams.drop(self.entry)


def create_web_application(*,settings:WebApplicationSettings,service_factory:Callable[[],AbstractAsyncContextManager[WebApplicationService]]) -> FastAPI:
    """Factory is mandatory; production resources enter exactly once in lifespan."""
    settings=fresh(settings,WebApplicationSettings)
    streams=_Streams()
    @asynccontextmanager
    async def lifespan(app):
        try:
            async with service_factory() as service:
                app.state.service=service
                try: yield
                finally: await streams.close()
            app.state.lifecycle_completed=True
        except BaseException:
            app.state.lifecycle_failed=True
            raise
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.settings=settings
    app.state.lifecycle_failed=False
    app.state.lifecycle_completed=False
    app.state.streams=streams
    app.add_middleware(GZipMiddleware,minimum_size=256,compresslevel=5)
    app.add_middleware(_Boundary,settings=settings)

    @app.exception_handler(_HttpFailure)
    async def http_failure(request,error):
        return _failure(error.code,status=error.status,request_id=error.request_id,retry=error.retry)
    @app.exception_handler(DecisionError)
    async def decision(request,error):
        return _failure(error.code,request_id=getattr(request.state,"request_id",None),retry=error.retry_after_seconds)
    @app.exception_handler(WebApplicationError)
    async def application_error(request,error):
        code="invalid_request" if isinstance(error,WebApplicationUsageError) else "request_conflict" if isinstance(error,WebApplicationConflictError) else "capacity_unavailable" if isinstance(error,WebApplicationCapacityError) else "dependency_unavailable"
        return _failure(code,request_id=getattr(request.state,"request_id",None))
    @app.exception_handler(RequestValidationError)
    async def validation(request,error): return _failure("invalid_request")
    @app.exception_handler(HTTPException)
    async def framework(request,error): return _failure("not_found" if error.status_code==404 else "invalid_request",status=error.status_code)
    @app.exception_handler(Exception)
    async def unexpected(request,error):
        _LOG.error("http_dependency_failed")
        return _failure("dependency_unavailable",request_id=getattr(request.state,"request_id",None))

    async def write(request,expected):
        owner=await _owner(request)
        token=_header(request,"x-csrf-token")
        if not request.app.state.service._verify_csrf(owner=owner,supplied=token):
            raise _HttpFailure("csrf_rejected")
        return owner,await _body(request,expected)

    def receipt(value, *, status=202):
        value=fresh(value,WebCommandReceipt)
        if value.status in {"confirming","uncertain","rejected"}:
            return _failure(value.reason or "admission_unconfirmed",request_id=value.request_id)
        return _json(value,WebCommandReceipt,status=status)

    @app.get("/api/session")
    async def session(request: Request):
        view,raw=await app.state.service.bootstrap_session(cookie=_cookie(request,settings))
        response=_json(view,WebSessionView)
        if raw is not None:
            response.set_cookie(settings.cookie_name,raw,max_age=2592000,httponly=True,samesite="lax",secure=False,path="/")
        return response
    @app.get("/api/admission")
    async def admission(request: Request): return _json(await app.state.service.get_admission(owner=await _owner(request),client_ip=_ip(request)),WebAdmissionView)
    @app.post("/api/runs")
    async def start(request: Request):
        owner,body=await write(request,WebStartRequest)
        return receipt(await app.state.service.start(owner=owner,request=body,client_ip=_ip(request)))
    @app.get("/api/requests/{request_id}")
    async def request_receipt(request: Request,request_id: str):
        owner=await _owner(request)
        return _json(await app.state.service.get_request(owner=owner,request_id=_uuid(request_id)),WebCommandReceipt)
    @app.get("/api/runs")
    async def history(request: Request):
        owner=await _owner(request)
        if set(request.query_params)-{"cursor"} or len(request.query_params.getlist("cursor"))>1: raise _HttpFailure("invalid_request")
        cursor=request.query_params.get("cursor")
        if cursor is not None and len(cursor)>512: raise _HttpFailure("invalid_request")
        return _json(await app.state.service.list_runs(owner=owner,cursor=cursor),WebHistoryPage)
    @app.get("/api/runs/{run_id}")
    async def run(request: Request,run_id: str):
        owner=await _owner(request)
        return _json(await app.state.service.get_run(owner=owner,run_id=_uuid(run_id)),WebRunView)
    async def control(request,run_id,expected,kind):
        owner,body=await write(request,expected)
        run_id=_uuid(run_id)
        if kind in {"cancel","recover"}: value=await getattr(app.state.service,kind)(owner=owner,run_id=run_id,request=body)
        else: value=await app.state.service.resume(owner=owner,run_id=run_id,request=body)
        status=202
        if kind=="cancel" and value.status=="dispatched":
            with app.state.service._snapshot_scope(): view=await app.state.service.get_run(owner=owner,run_id=run_id)
            if view.run.activity=="terminal" and view.run.lifecycle=="closed": status=200
        return receipt(value,status=status)
    @app.post("/api/runs/{run_id}/clarify")
    async def clarify(request: Request,run_id: str): return await control(request,run_id,WebClarifyRequest,"clarify")
    @app.post("/api/runs/{run_id}/confirm")
    async def confirm(request: Request,run_id: str): return await control(request,run_id,WebConfirmRequest,"confirm")
    @app.post("/api/runs/{run_id}/hotel")
    async def hotel(request: Request,run_id: str): return await control(request,run_id,WebHotelRequest,"hotel")
    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(request: Request,run_id: str): return await control(request,run_id,WebControlRequest,"cancel")
    @app.post("/api/runs/{run_id}/recover")
    async def recover(request: Request,run_id: str): return await control(request,run_id,WebControlRequest,"recover")
    @app.get("/api/runs/{run_id}/result")
    async def result(request: Request,run_id: str): return _json(await app.state.service.read_result(owner=await _owner(request),run_id=_uuid(run_id)),WebResultView)
    @app.get("/api/runs/{run_id}/summary")
    async def summary(request: Request,run_id: str): return _json(await app.state.service.read_summary(owner=await _owner(request),run_id=_uuid(run_id)),WebSummaryView)
    @app.get("/api/samples")
    async def samples(): return _json(await app.state.service.get_samples(),WebSamplesView)
    @app.get("/health/live")
    async def live(): return JSONResponse({"status":"live"})
    @app.get("/health/ready")
    async def ready():
        await app.state.service._ready()
        return JSONResponse({"status":"ready"})
    @app.get("/api/runs/{run_id}/events")
    async def events(request: Request,run_id: str):
        owner=await _owner(request); run_id=_uuid(run_id)
        with app.state.service._snapshot_scope(): first=fresh(await app.state.service.get_run(owner=owner,run_id=run_id),WebRunView)
        entry=await streams.acquire((owner.visitor_hash,run_id))
        async def snapshots():
            previous=None; heartbeat=asyncio.get_running_loop().time(); view=first
            try:
                while not entry.stop.is_set():
                    payload=view.model_dump_json()
                    if payload!=previous:
                        yield "event: snapshot\ndata: "+payload+"\n\n"
                        previous=payload
                    if view.run.activity=="terminal": return
                    now=asyncio.get_running_loop().time()
                    if now-heartbeat>=15:
                        yield ": heartbeat\n\n"; heartbeat=now
                    try: await asyncio.wait_for(entry.stop.wait(),timeout=2)
                    except TimeoutError: pass
                    if entry.stop.is_set() or await request.is_disconnected(): return
                    try:
                        owner_now=await _owner(request)
                        with app.state.service._snapshot_scope(): view=fresh(await app.state.service.get_run(owner=owner_now,run_id=run_id),WebRunView)
                    except (DecisionError,WebApplicationError,_HttpFailure): return
            finally: streams.drop(entry)
        return _EventResponse(snapshots(),streams,entry)
    async def page(filename): return HTMLResponse((_STATIC/filename).read_text(encoding="utf-8"))
    @app.get("/")
    async def home(): return await page("index.html")
    @app.get("/tasks/{run_id}")
    async def task_page(run_id: str):
        _uuid(run_id)
        return await page("task.html")
    @app.get("/history")
    async def history_page(): return await page("history.html")
    @app.get("/samples")
    async def sample_page(): return await page("samples.html")
    @app.get("/static/{filename}")
    async def asset(filename: str):
        media={"app.js":"text/javascript","site.css":"text/css"}
        if filename not in media: raise _HttpFailure("not_found")
        return Response((_STATIC/filename).read_bytes(),media_type=media[filename],headers={"Cache-Control":"public, max-age=3600"})
    return app
