"""Short-lived recording scopes and a transparent public repository observer."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, TypeVar
from uuid import UUID

from pydantic import BaseModel
from app.workflow_contracts import (ArtifactRepository, ArtifactSchemaRegistration, ArtifactWriteRequest,
    ArtifactRef, ArtifactReadResult, ArtifactLookupOutcome, OperationId, TravelSessionState)
from .models import PublicRecord, PublicRecordBody
from .projection import project_artifact, project_requirement
from .store import PublicProgressStore


@dataclass
class _Binding:
    run_id: UUID
    trace_id: UUID
    valid: Callable[[], bool] | None
    active: bool = True


class PublicProgressRecorder:
    def __init__(self, *, store: PublicProgressStore,
            registrations: tuple[ArtifactSchemaRegistration, ...], secrets: tuple[str, ...]):
        self._store, self._registrations, self._secrets = store, registrations, secrets
        self._binding: ContextVar[_Binding | None] = ContextVar("public_progress_binding", default=None)

    @property
    def store(self) -> PublicProgressStore:
        return self._store

    @contextmanager
    def operation(self, *, run_id: UUID, trace_id: UUID, valid: Callable[[], bool] | None = None):
        if type(run_id) is not UUID or type(trace_id) is not UUID:
            raise ValueError("progress identity required")
        binding = _Binding(run_id, trace_id, valid)
        token = self._binding.set(binding)
        try:
            yield
        finally:
            binding.active = False
            self._binding.reset(token)

    def _matches(self, run_id, trace_id):
        binding = self._binding.get()
        return (binding is not None and binding.active and binding.run_id == run_id and binding.trace_id == trace_id
            and (binding.valid is None or binding.valid()))

    async def _limited(self, run_id, trace_id):
        try:
            async with asyncio.timeout(0.04):
                if self._matches(run_id, trace_id):
                    await self._store.mark_limited(run_id=run_id, trace_id=trace_id)
        except Exception:
            pass

    async def publish(self, *, run_id: UUID, trace_id: UUID, event_key: str, body: PublicRecordBody) -> PublicRecord | None:
        try:
            if not self._matches(run_id, trace_id):
                return None
            async with asyncio.timeout(0.20):
                return await self._store.append(run_id=run_id, trace_id=trace_id, event_key=event_key, body=body)
        except Exception:
            await self._limited(run_id, trace_id)
            return None

    async def observe(self, *, request: ArtifactWriteRequest, ref: ArtifactRef) -> None:
        binding = self._binding.get()
        if binding is None:
            return
        try:
            if not self._matches(request.operation_id.run_id, binding.trace_id):
                return
            body = project_artifact(request=request, ref=ref, registrations=self._registrations, secrets=self._secrets)
            if body is not None:
                await self.publish(run_id=binding.run_id, trace_id=binding.trace_id,
                    event_key=f"artifact:{ref.artifact_id}:{body.kind}", body=body)
        except Exception:
            await self._limited(binding.run_id, binding.trace_id)

    async def record_requirement(self, *, state: TravelSessionState, confirmed: bool, event_key: str) -> None:
        try:
            if not self._matches(state.run_id, state.trace_id):
                return
            body = project_requirement(state=state, confirmed=confirmed, secrets=self._secrets)
            if body is not None:
                await self.publish(run_id=state.run_id, trace_id=state.trace_id, event_key=event_key, body=body)
        except Exception:
            await self._limited(state.run_id, state.trace_id)

    async def settle(self, *, run_id: UUID, trace_id: UUID) -> None:
        try:
            if self._matches(run_id, trace_id):
                async with asyncio.timeout(0.20):
                    await self._store.settle(run_id=run_id, trace_id=trace_id)
        except Exception:
            await self._limited(run_id, trace_id)


TArtifact = TypeVar("TArtifact", bound=BaseModel)


class ObservedArtifactRepository:
    def __init__(self, *, repository: ArtifactRepository, recorder: PublicProgressRecorder):
        self._repository, self._recorder = repository, recorder

    async def put(self, request: ArtifactWriteRequest) -> ArtifactRef:
        ref = await self._repository.put(request)
        await self._recorder.observe(request=request, ref=ref)
        return ref

    async def lookup(self, *, operation_id: OperationId) -> ArtifactLookupOutcome:
        return await self._repository.lookup(operation_id=operation_id)

    async def get(self, ref: ArtifactRef, *, expected_model: type[TArtifact]) -> ArtifactReadResult[TArtifact]:
        return await self._repository.get(ref, expected_model=expected_model)
