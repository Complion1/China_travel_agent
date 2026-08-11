"""Artifact repository protocol boundary."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.workflow_contracts.artifacts.models import (
    ArtifactLookupOutcome,
    ArtifactReadResult,
    ArtifactRef,
    ArtifactWriteRequest,
)
from app.workflow_contracts.identifiers import OperationId


TArtifact = TypeVar("TArtifact", bound=BaseModel)


class ArtifactRepository(Protocol):
    async def put(self, request: ArtifactWriteRequest) -> ArtifactRef: ...

    async def lookup(
        self,
        *,
        operation_id: OperationId,
    ) -> ArtifactLookupOutcome: ...

    async def get(
        self,
        ref: ArtifactRef,
        *,
        expected_model: type[TArtifact],
    ) -> ArtifactReadResult[TArtifact]: ...
