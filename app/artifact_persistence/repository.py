"""Private PostgreSQL implementation of the Module 04 artifact repository."""

from __future__ import annotations

import asyncio
import json
import warnings
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any, TypeVar, cast
from uuid import UUID

from psycopg import OperationalError
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool, PoolTimeout
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
from app.workflow_contracts.artifacts.registry import (
    ArtifactSchemaRegistry,
    PreparedArtifact,
)
from app.workflow_contracts.errors import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactRepositoryDependencyError,
    ArtifactRepositoryError,
    ArtifactRepositoryUsageError,
    ArtifactRequestValidationError,
    ArtifactCapacityExceededError,
    ArtifactTooLargeError,
)
from app.workflow_contracts.identifiers import OperationId


TArtifact = TypeVar("TArtifact", bound=BaseModel)


_ARTIFACT_TABLE = "artifact_store.artifacts"
_CAPACITY_TABLE = "artifact_store.capacity_state"
_REF_COLUMN_COUNT = 9
_REF_WITH_BYTES_COLUMN_COUNT = 10

_LOOKUP_SQL = f"""
SELECT artifact_id, run_id, phase, logical_step, artifact_type,
       schema_version, content_hash, created_at, size_bytes
FROM {_ARTIFACT_TABLE}
WHERE run_id = %(run_id)s
  AND phase = %(phase)s
  AND logical_step = %(logical_step)s
"""

_GET_SQL = f"""
SELECT artifact_id, run_id, phase, logical_step, artifact_type,
       schema_version, content_hash, canonical_bytes, created_at, size_bytes
FROM {_ARTIFACT_TABLE}
WHERE artifact_id = %(artifact_id)s
"""

_CAPACITY_SQL = f"""
SELECT singleton_id, max_artifact_bytes, max_artifacts,
       max_total_content_bytes, artifact_count, total_content_bytes
FROM {_CAPACITY_TABLE}
WHERE singleton_id = 1
FOR UPDATE
"""

_INSERT_SQL = f"""
INSERT INTO {_ARTIFACT_TABLE} (
    run_id, phase, logical_step, artifact_type, schema_version,
    content_hash, canonical_bytes, size_bytes
)
VALUES (
    %(run_id)s, %(phase)s, %(logical_step)s, %(artifact_type)s,
    %(schema_version)s, %(content_hash)s, %(canonical_bytes)s, %(size_bytes)s
)
RETURNING artifact_id, run_id, phase, logical_step, artifact_type,
          schema_version, content_hash, created_at, size_bytes
"""

_UPDATE_CAPACITY_SQL = f"""
UPDATE {_CAPACITY_TABLE}
SET artifact_count = artifact_count + %(artifact_count_delta)s,
    total_content_bytes = total_content_bytes + %(content_bytes_delta)s
WHERE singleton_id = 1
RETURNING artifact_count, total_content_bytes
"""


def _strict_copy(
    model: BaseModel,
    model_type: type[TArtifact],
    error_type: type[ArtifactRepositoryError],
) -> TArtifact:
    """Rebuild a DTO through both strict Python and strict JSON validation."""

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
    return cast(TArtifact, second)


def _operation_params(operation_id: OperationId) -> dict[str, object]:
    return {
        "run_id": operation_id.run_id,
        "phase": operation_id.phase.value,
        "logical_step": operation_id.logical_step,
    }


def _artifact_id_params(artifact_id: object) -> dict[str, object]:
    return {"artifact_id": artifact_id}


def _row_to_ref(row: Sequence[object], *, includes_bytes: bool) -> tuple[ArtifactRef, bytes | None]:
    expected_count = (
        _REF_WITH_BYTES_COLUMN_COUNT if includes_bytes else _REF_COLUMN_COUNT
    )
    try:
        if len(row) != expected_count:
            raise ValueError
        artifact_id, run_id, phase, logical_step, artifact_type, schema_version, content_hash = row[:7]
        if includes_bytes:
            canonical_bytes = row[7]
            created_at = row[8]
            size_bytes = row[9]
            if type(canonical_bytes) is not bytes:
                raise ValueError
        else:
            canonical_bytes = None
            created_at = row[7]
            size_bytes = row[8]
        if type(created_at) is not datetime:
            raise ValueError
        if type(run_id) is not UUID or type(phase) is not str or type(logical_step) is not str:
            raise ValueError
        operation_id = TypeAdapter(OperationId).validate_json(
            json.dumps(
                {
                    "run_id": str(run_id),
                    "phase": phase,
                    "logical_step": logical_step,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            strict=True,
        )
        ref = ArtifactRef(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            schema_version=schema_version,
            content_hash=content_hash,
            operation_id=operation_id,
            created_at=created_at,
            size_bytes=size_bytes,
        )
    except Exception:
        raise ArtifactIntegrityError() from None
    return ref, cast(bytes | None, canonical_bytes)


def _same_prepared_content(
    ref: ArtifactRef,
    canonical_bytes: bytes,
    prepared: PreparedArtifact,
) -> bool:
    return (
        ref.artifact_type == prepared.artifact_type
        and ref.schema_version == prepared.schema_version
        and ref.content_hash == prepared.content_hash
        and ref.size_bytes == prepared.size_bytes
        and canonical_bytes == prepared.canonical_bytes
    )


def _capacity_failure() -> None:
    raise ArtifactRepositoryDependencyError() from None


def _validate_capacity_row(
    row: Sequence[object] | None,
    limits: ArtifactRepositoryLimits,
) -> tuple[int, int]:
    try:
        if row is None or len(row) != 6 or any(type(value) is not int for value in row):
            _capacity_failure()
        singleton_id, max_artifact_bytes, max_artifacts, max_total_content_bytes, artifact_count, total_content_bytes = row
        if singleton_id != 1:
            _capacity_failure()
        if (
            max_artifact_bytes != limits.max_artifact_bytes
            or max_artifacts != limits.max_artifacts
            or max_total_content_bytes != limits.max_total_content_bytes
        ):
            _capacity_failure()
        if not 0 <= artifact_count <= max_artifacts:
            _capacity_failure()
        if not 0 <= total_content_bytes <= max_total_content_bytes:
            _capacity_failure()
        return artifact_count, total_content_bytes
    except ArtifactRepositoryDependencyError:
        raise
    except Exception:
        raise ArtifactRepositoryDependencyError() from None


class _PostgresArtifactRepository:
    """Private append-only PostgreSQL repository for Module 04 artifacts."""

    __slots__ = ("_pool", "_registry", "_limits", "_loop", "_closed")

    def __init__(
        self,
        *,
        pool: AsyncConnectionPool,
        registry: ArtifactSchemaRegistry,
        limits: ArtifactRepositoryLimits,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if not isinstance(pool, AsyncConnectionPool):
            raise ArtifactRepositoryUsageError() from None
        if not isinstance(registry, ArtifactSchemaRegistry):
            raise ArtifactRepositoryUsageError() from None
        self._pool = pool
        self._registry = registry
        self._limits = _strict_copy(
            limits,
            ArtifactRepositoryLimits,
            ArtifactRepositoryUsageError,
        )
        self._loop = loop
        self._closed = False

    def _mark_closed(self) -> None:
        self._closed = True

    def _ensure_lifecycle(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise ArtifactRepositoryUsageError() from None
        if self._closed or getattr(self._pool, "closed", False):
            raise ArtifactRepositoryUsageError() from None
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise ArtifactRepositoryUsageError() from None

    async def _run_with_retry(
        self,
        operation: Callable[[], Awaitable[TArtifact]],
    ) -> TArtifact:
        for attempt in range(2):
            try:
                return await operation()
            except ArtifactRepositoryError:
                raise
            except Exception as error:
                sqlstate = getattr(error, "sqlstate", None)
                retryable = isinstance(error, PoolTimeout) or (
                    isinstance(error, OperationalError)
                    and (sqlstate is None or (type(sqlstate) is str and sqlstate.startswith("08")))
                )
                if retryable and attempt == 0:
                    continue
                raise ArtifactRepositoryDependencyError() from None
        raise ArtifactRepositoryDependencyError() from None

    async def _fetch_by_operation(
        self,
        cursor: Any,
        operation_id: OperationId,
        *,
        includes_bytes: bool,
    ) -> Sequence[object] | None:
        if includes_bytes:
            sql = f"""
SELECT artifact_id, run_id, phase, logical_step, artifact_type,
       schema_version, content_hash, canonical_bytes, created_at, size_bytes
FROM {_ARTIFACT_TABLE}
WHERE run_id = %(run_id)s
  AND phase = %(phase)s
  AND logical_step = %(logical_step)s
"""
        else:
            sql = _LOOKUP_SQL
        await cursor.execute(sql, _operation_params(operation_id))
        return await cursor.fetchone()

    async def _fetch_by_id(
        self,
        cursor: Any,
        artifact_id: object,
    ) -> Sequence[object] | None:
        await cursor.execute(_GET_SQL, _artifact_id_params(artifact_id))
        return await cursor.fetchone()

    def _existing_or_conflict(
        self,
        row: Sequence[object],
        prepared: PreparedArtifact,
    ) -> ArtifactRef:
        stored_ref, canonical_bytes = _row_to_ref(row, includes_bytes=True)
        if canonical_bytes is None:
            raise ArtifactIntegrityError() from None
        if _same_prepared_content(stored_ref, canonical_bytes, prepared):
            return stored_ref
        raise ArtifactConflictError() from None

    def _validate_inserted_ref(
        self,
        ref: ArtifactRef,
        prepared: PreparedArtifact,
    ) -> ArtifactRef:
        if (
            ref.operation_id != prepared.operation_id
            or ref.artifact_type != prepared.artifact_type
            or ref.schema_version != prepared.schema_version
            or ref.content_hash != prepared.content_hash
            or ref.size_bytes != prepared.size_bytes
        ):
            raise ArtifactIntegrityError() from None
        return ref

    async def _put_transaction(
        self,
        connection: Any,
        prepared: PreparedArtifact,
    ) -> ArtifactRef:
        async with connection.transaction():
            await connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            async with connection.cursor() as cursor:
                first_row = await self._fetch_by_operation(
                    cursor,
                    prepared.operation_id,
                    includes_bytes=True,
                )
                if first_row is not None:
                    return self._existing_or_conflict(first_row, prepared)

                await cursor.execute(_CAPACITY_SQL)
                capacity_row = await cursor.fetchone()
                artifact_count, total_content_bytes = _validate_capacity_row(
                    capacity_row,
                    self._limits,
                )

                second_row = await self._fetch_by_operation(
                    cursor,
                    prepared.operation_id,
                    includes_bytes=True,
                )
                if second_row is not None:
                    return self._existing_or_conflict(second_row, prepared)

                if prepared.size_bytes > self._limits.max_artifact_bytes:
                    raise ArtifactTooLargeError() from None
                if artifact_count >= self._limits.max_artifacts:
                    raise ArtifactCapacityExceededError() from None
                if (
                    total_content_bytes + prepared.size_bytes
                    > self._limits.max_total_content_bytes
                ):
                    raise ArtifactCapacityExceededError() from None

                await cursor.execute(
                    _INSERT_SQL,
                    {
                        **_operation_params(prepared.operation_id),
                        "artifact_type": prepared.artifact_type,
                        "schema_version": prepared.schema_version,
                        "content_hash": prepared.content_hash,
                        "canonical_bytes": prepared.canonical_bytes,
                        "size_bytes": prepared.size_bytes,
                    },
                )
                inserted_row = await cursor.fetchone()
                if inserted_row is None:
                    raise ArtifactRepositoryDependencyError() from None
                inserted_ref, _ = _row_to_ref(inserted_row, includes_bytes=False)
                inserted_ref = self._validate_inserted_ref(inserted_ref, prepared)

                await cursor.execute(
                    _UPDATE_CAPACITY_SQL,
                    {
                        "artifact_count_delta": 1,
                        "content_bytes_delta": prepared.size_bytes,
                    },
                )
                updated_capacity = await cursor.fetchone()
                if updated_capacity is None or len(updated_capacity) != 2:
                    raise ArtifactRepositoryDependencyError() from None
                return inserted_ref

    async def _resolve_unique_violation(
        self,
        connection: Any,
        prepared: PreparedArtifact,
    ) -> ArtifactRef:
        async with connection.transaction():
            async with connection.cursor() as cursor:
                row = await self._fetch_by_operation(
                    cursor,
                    prepared.operation_id,
                    includes_bytes=True,
                )
                if row is None:
                    raise ArtifactRepositoryDependencyError() from None
                return self._existing_or_conflict(row, prepared)

    async def _put_with_connection(self, prepared: PreparedArtifact) -> ArtifactRef:
        async with self._pool.connection() as connection:
            try:
                return await self._put_transaction(connection, prepared)
            except UniqueViolation:
                return await self._resolve_unique_violation(connection, prepared)

    async def put(self, request: ArtifactWriteRequest) -> ArtifactRef:
        self._ensure_lifecycle()
        try:
            prepared = self._registry.prepare(request)
        except ArtifactRepositoryError:
            raise
        except Exception:
            raise ArtifactRepositoryDependencyError() from None
        return await self._run_with_retry(
            lambda: self._put_with_connection(prepared)
        )

    async def _lookup_once(
        self,
        operation_id: OperationId,
    ) -> ArtifactLookupOutcome:
        async with self._pool.connection(timeout=10) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    row = await self._fetch_by_operation(
                        cursor,
                        operation_id,
                        includes_bytes=False,
                    )
                    if row is None:
                        return ArtifactAbsent(operation_id=operation_id)
                    ref, _ = _row_to_ref(row, includes_bytes=False)
                    return ArtifactFound(ref=ref)

    async def lookup(
        self,
        *,
        operation_id: OperationId,
    ) -> ArtifactLookupOutcome:
        self._ensure_lifecycle()
        fresh_operation_id = _strict_copy(
            operation_id,
            OperationId,
            ArtifactRequestValidationError,
        )
        return await self._run_with_retry(
            lambda: self._lookup_once(fresh_operation_id)
        )

    async def _get_once(
        self,
        fresh_ref: ArtifactRef,
        *,
        expected_model: type[TArtifact],
    ) -> ArtifactReadResult[TArtifact]:
        async with self._pool.connection(timeout=10) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    row = await self._fetch_by_id(cursor, fresh_ref.artifact_id)
                    if row is None:
                        raise ArtifactNotFoundError() from None
                    stored_ref, canonical_bytes = _row_to_ref(
                        row,
                        includes_bytes=True,
                    )
                    if canonical_bytes is None:
                        raise ArtifactIntegrityError() from None
                    if any(
                        getattr(fresh_ref, field_name)
                        != getattr(stored_ref, field_name)
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
            content = self._registry.decode(
                ref=stored_ref,
                canonical_bytes=canonical_bytes,
                expected_model=expected_model,
            )
            return ArtifactReadResult(ref=stored_ref, content=content)

    async def get(
        self,
        ref: ArtifactRef,
        *,
        expected_model: type[TArtifact],
    ) -> ArtifactReadResult[TArtifact]:
        self._ensure_lifecycle()
        fresh_ref = _strict_copy(
            ref,
            ArtifactRef,
            ArtifactRequestValidationError,
        )
        return await self._run_with_retry(
            lambda: self._get_once(fresh_ref, expected_model=expected_model)
        )
