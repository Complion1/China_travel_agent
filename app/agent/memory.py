from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import settings


@dataclass
class CacheItem:
    value: Any
    expires_at: float


class CacheBackend(Protocol):
    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ...

    def get(self, key: str) -> Any | None:
        ...


class MemoryBackend(Protocol):
    result_cache: CacheBackend

    def append_session(self, session_id: str, item: dict[str, Any]) -> None:
        ...

    def get_session(self, session_id: str) -> list[dict[str, Any]]:
        ...

    def set_job(self, job_id: str, value: dict[str, Any]) -> None:
        ...

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        ...


class TTLCache:
    def __init__(self, default_ttl_seconds: int = 300):
        self.default_ttl_seconds = default_ttl_seconds
        self._items: dict[str, CacheItem] = {}
        self._lock = threading.RLock()

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        with self._lock:
            self._items[key] = CacheItem(value=value, expires_at=time.time() + ttl)

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if item.expires_at < time.time():
                self._items.pop(key, None)
                return None
            return item.value


class AgentMemory:
    """Local in-process memory backend.

    Later this can be swapped for Redis or a LangGraph checkpointer.
    """

    def __init__(self, result_ttl_seconds: int = 300):
        self.sessions: dict[str, list[dict[str, Any]]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.result_cache = TTLCache(default_ttl_seconds=result_ttl_seconds)
        self._lock = threading.RLock()

    def append_session(self, session_id: str, item: dict[str, Any]) -> None:
        with self._lock:
            self.sessions.setdefault(session_id, []).append(item)

    def get_session(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.sessions.get(session_id, []))

    def set_job(self, job_id: str, value: dict[str, Any]) -> None:
        with self._lock:
            self.jobs[job_id] = value

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None


def build_memory_backend() -> MemoryBackend:
    return AgentMemory(result_ttl_seconds=settings.result_ttl_seconds)
