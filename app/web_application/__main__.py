"""Explicit production entry; no .env parsing or automatic migrations."""
import asyncio
import logging
import os
import signal
import sys
import threading
import uvicorn
from .api import create_web_application
from .assembly import production_service
from .settings import load_web_application_settings

_SAFE_CODES={"http_dependency_failed","resource_cleanup_failed","workflow_execution_failed",
    "workflow_metadata_reconciliation_failed","cancel_metadata_unconfirmed","cancel_maintenance_failed",
    "summary_not_saved","summary_finalization_failed"}


class _SafeCodes(logging.StreamHandler):
    def emit(self,record):
        if (record.name.startswith("app.web_application.") and type(record.msg) is str and
                record.msg in _SAFE_CODES and not record.args and not record.exc_info and not record.stack_info):
            self.stream.write(record.msg+"\n")
            self.flush()


async def serve(*,settings,environ):
    app=create_web_application(settings=settings,service_factory=lambda:production_service(settings=settings,environ=environ))
    await _serve_application(app=app,settings=settings)


async def _serve_application(*,app,settings):
    server=uvicorn.Server(uvicorn.Config(app,host="0.0.0.0",port=settings.port,workers=1,
        reload=False,proxy_headers=False,access_log=False,server_header=False,
        ws="none",log_config=None,log_level="critical",timeout_graceful_shutdown=30))
    async def monitor():
        while not server.should_exit:
            service=getattr(app.state,"service",None)
            if service is not None and (service._closing or service._closed):
                server.should_exit=True
                break
            await asyncio.sleep(.1)
        # Signal handling first stops HTTP snapshots; their connections must
        # not consume the entire Uvicorn grace before lifespan cleanup starts.
        await app.state.streams.close()
    watcher=asyncio.create_task(monitor())
    original_signals={}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM,signal.SIGINT):
            original_signals[signum]=signal.getsignal(signum)
            # Uvicorn replays captured signals after restoring these handlers.
            # The application must finish its own checked shutdown first.
            signal.signal(signum,lambda *_:None)
    try:
        await server.serve()
        if not server.started or app.state.lifecycle_failed or not app.state.lifecycle_completed: raise RuntimeError()
    finally:
        try:
            watcher.cancel()
            results=await asyncio.gather(watcher,return_exceptions=True)
            if any(isinstance(value,Exception) for value in results): raise RuntimeError()
        finally:
            for signum,handler in original_signals.items(): signal.signal(signum,handler)


def main():
    logging.disable(logging.NOTSET)
    logging.getLogger().handlers=[_SafeCodes()]
    logging.getLogger().setLevel(logging.WARNING)
    try:
        environment=dict(os.environ)
        settings=load_web_application_settings(environ=environment)
        asyncio.run(serve(settings=settings,environ=environment))
        return 0
    except (Exception,KeyboardInterrupt):
        print("WebApplicationStartupOrShutdownError",file=sys.stderr)
        return 2


if __name__=="__main__": raise SystemExit(main())
