"""Process-local ArtifactRepository reference implementation."""

from __future__ import annotations

import asyncio
import json
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, TypeAdapter

from app.workflow_contracts.artifacts.models import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactLookupOutcome,
    ArtifactReadResult,
    ArtifactRef,
    ArtifactRepositoryLimits,
    ArtifactWriteRequest,
)
from app.workflow_contracts.artifacts.registry import ArtifactSchemaRegistry
from app.workflow_contracts.errors import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactRepositoryDependencyError,
    ArtifactRepositoryUsageError,
    ArtifactRequestValidationError,
    ArtifactCapacityExceededError,
    ArtifactTooLargeError,
)
from app.workflow_contracts.identifiers import OperationId
from app.workflow_contracts.providers import UUIDFactory, UtcClock


DEMO_ARTIFACT_REPOSITORY_LIMITS = ArtifactRepositoryLimits(
    max_artifact_bytes=1_048_576,
    max_artifacts=2_048,
    max_total_content_bytes=67_108_864,
)


TModel = TypeVar("TModel", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class _ArtifactRecord:
    ref: ArtifactRef
    canonical_bytes: bytes


def _strict_copy(
    model: BaseModel,
    model_type: type[TModel],
    error_type: type[Exception],
) -> TModel:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            python_tree = model.model_dump(
                mode="python",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            first = TypeAdapter(model_type).validate_python(
                python_tree,
                strict=True,
            )
            json_tree = first.model_dump(
                mode="json",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            )
            encoded = json.dumps(
                json_tree,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            second = TypeAdapter(model_type).validate_json(encoded, strict=True)
    except Exception:
        raise error_type() from None
    return cast(TModel, second)


def _copy_ref(ref: ArtifactRef) -> ArtifactRef:
    return _strict_copy(ref, ArtifactRef, ArtifactRepositoryDependencyError)


class InMemoryArtifactRepository:
    """A bounded, process-local repository with strict replay semantics."""

    __slots__ = (
        "_registry",
        "_utc_clock",
        "_uuid_factory",
        "_limits",
        "_by_operation",
        "_by_artifact_id",
        "_total_content_bytes",
        "_lock",
        "_loop",
    )

    def __init__(
        self,
        *,
        registry: ArtifactSchemaRegistry,
        utc_clock: UtcClock,
        uuid_factory: UUIDFactory,
        limits: ArtifactRepositoryLimits,
    ) -> None:
        self._registry = registry
        self._utc_clock = utc_clock
        self._uuid_factory = uuid_factory
        self._limits = _strict_copy(
            limits,
            ArtifactRepositoryLimits,
            ArtifactRepositoryUsageError,
        )
        self._by_operation: dict[OperationId, ArtifactRef] = {}
        self._by_artifact_id: dict[UUID, _ArtifactRecord] = {}
        self._total_content_bytes = 0
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _ensure_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise ArtifactRepositoryUsageError() from None
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise ArtifactRepositoryUsageError() from None

    async def put(self, request: ArtifactWriteRequest) -> ArtifactRef:
        self._ensure_loop()
        prepared = self._registry.prepare(request)

        async with self._lock:
            existing_ref = self._by_operation.get(prepared.operation_id)
            if existing_ref is not None:
                existing_record = self._by_artifact_id[existing_ref.artifact_id]
                same_content = (
                    existing_ref.artifact_type == prepared.artifact_type
                    and existing_ref.schema_version == prepared.schema_version
                    and existing_ref.content_hash == prepared.content_hash
                    and existing_record.canonical_bytes == prepared.canonical_bytes
                )
                if same_content:
                    return _copy_ref(existing_ref)
                raise ArtifactConflictError() from None

            if prepared.size_bytes > self._limits.max_artifact_bytes:
                raise ArtifactTooLargeError() from None
            if len(self._by_artifact_id) >= self._limits.max_artifacts:
                raise ArtifactCapacityExceededError() from None
            if (
                self._total_content_bytes + prepared.size_bytes
                > self._limits.max_total_content_bytes
            ):
                raise ArtifactCapacityExceededError() from None

            try:
                artifact_id = self._uuid_factory.new()
                if type(artifact_id) is not UUID:
                    raise ValueError
                if artifact_id in self._by_artifact_id:
                    raise ArtifactRepositoryDependencyError()

                created_at = self._utc_clock.now()
                if (
                    type(created_at) is not datetime
                    or created_at.tzinfo is None
                    or created_at.utcoffset() != timedelta(0)
                ):
                    raise ValueError
                ref = ArtifactRef(
                    artifact_id=artifact_id,
                    artifact_type=prepared.artifact_type,
                    schema_version=prepared.schema_version,
                    content_hash=prepared.content_hash,
                    operation_id=prepared.operation_id,
                    created_at=created_at,
                    size_bytes=prepared.size_bytes,
                )
            except ArtifactRepositoryDependencyError:
                raise
            except Exception:
                raise ArtifactRepositoryDependencyError() from None

            record = _ArtifactRecord(
                ref=ref,
                canonical_bytes=prepared.canonical_bytes,
            )
            self._by_operation[prepared.operation_id] = ref
            self._by_artifact_id[artifact_id] = record
            self._total_content_bytes += prepared.size_bytes
            return _copy_ref(ref)

    async def lookup(
        self,
        *,
        operation_id: OperationId,
    ) -> ArtifactLookupOutcome:
        self._ensure_loop()
        fresh_operation_id = _strict_copy(
            operation_id,
            OperationId,
            ArtifactRequestValidationError,
        )
        async with self._lock:
            ref = self._by_operation.get(fresh_operation_id)
            if ref is None:
                return ArtifactAbsent(
                    operation_id=_strict_copy(
                        fresh_operation_id,
                        OperationId,
                        ArtifactRequestValidationError,
                    )
                )
            return ArtifactFound(ref=_copy_ref(ref))

    async def get(
        self,
        ref: ArtifactRef,
        *,
        expected_model: type[TModel],
    ) -> ArtifactReadResult[TModel]:
        self._ensure_loop()
        fresh_ref = _strict_copy(
            ref,
            ArtifactRef,
            ArtifactRequestValidationError,
        )
        async with self._lock:
            record = self._by_artifact_id.get(fresh_ref.artifact_id)
            if record is None:
                raise ArtifactNotFoundError() from None
            stored_ref = record.ref
            if any(
                getattr(fresh_ref, field_name) != getattr(stored_ref, field_name)
                for field_name in (
                    "artifact_id",
                    "artifact_type",
                    "schema_version",
                    "content_hash",
                    "operation_id",
                    "created_at",
                    "size_bytes",
                )
            ):
                raise ArtifactIntegrityError() from None
            stored_ref_snapshot = _copy_ref(stored_ref)
            canonical_bytes = record.canonical_bytes

        content = self._registry.decode(
            ref=stored_ref_snapshot,
            canonical_bytes=canonical_bytes,
            expected_model=expected_model,
        )
        return ArtifactReadResult(
            ref=_copy_ref(stored_ref_snapshot),
            content=content,
        )
