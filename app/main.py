from __future__ import annotations

from typing import Any

from app.agent.harness import AgentHarness

try:
    from fastapi import FastAPI
    from pydantic import BaseModel
except ImportError:  # pragma: no cover - optional runtime dependency
    FastAPI = None
    BaseModel = object


class AgentRequest(BaseModel):
    message: str
    session_id: str = "default"


def create_app(harness: AgentHarness | None = None) -> Any:
    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed. Install optional API dependencies first.")

    harness = harness or AgentHarness()
    app = FastAPI(title="China Travel Agent Harness", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "memory_backend": type(harness.memory).__name__}

    @app.post("/agent/run")
    def run_agent(request: AgentRequest) -> dict[str, Any]:
        response = harness.run(request.message, session_id=request.session_id)
        return response.__dict__

    @app.get("/agent/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        items = harness.memory.get_session(session_id)
        return {"session_id": session_id, "count": len(items), "items": items}

    return app


app = create_app() if FastAPI is not None else None
