"""AP-T03/AP-T04 PostgreSQL mapping of the Module 04 repository contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict

from app.artifact_persistence.repository import _PostgresArtifactRepository
from app.workflow_contracts.artifacts.models import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactRepositoryLimits,
    ArtifactWriteRequest,
)
from app.workflow_contracts.artifacts.registry import (
    ArtifactSchemaRegistration,
    ArtifactSchemaRegistry,
)
from app.workflow_contracts.errors import (
    ArtifactCapacityExceededError,
    ArtifactConflictError,
    ArtifactExpectedModelMismatchError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactRequestValidationError,
    ArtifactTooLargeError,
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase
from tests.unit.workflow_contracts.fakes import StrictSampleArtifact


pytestmark = pytest.mark.usefixtures("artifact_store_isolated")


class _OtherExpectedArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    value: int


def _registry() -> ArtifactSchemaRegistry:
    return ArtifactSchemaRegistry(
        (
            ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.0",
                model=StrictSampleArtifact,
            ),
            ArtifactSchemaRegistration(
                artifact_type="test.other_artifact",
                schema_version="1.0",
                model=StrictSampleArtifact,
            ),
            ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.1",
                model=StrictSampleArtifact,
            ),
        )
    )


def _operation(step: str, *, run_id: UUID | None = None) -> OperationId:
    return OperationId(
        run_id=run_id or uuid4(),
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step=step,
    )


def _request(
    operation_id: OperationId,
    *,
    artifact_type: str = "test.sample_artifact",
    schema_version: str = "1.0",
    content: dict[str, object] | None = None,
) -> ArtifactWriteRequest:
    return ArtifactWriteRequest(
        operation_id=operation_id,
        artifact_type=artifact_type,
        schema_version=schema_version,
        content={
            "label": "contract",
            "count": 1,
            "ratio": 1.0,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": None,
        }
        if content is None
        else content,
    )


def _repository(
    pool: AsyncConnectionPool,
    *,
    limits: ArtifactRepositoryLimits,
    registry: ArtifactSchemaRegistry | None = None,
) -> _PostgresArtifactRepository:
    return _PostgresArtifactRepository(
        pool=pool,
        registry=registry or _registry(),
        limits=limits,
    )


def _set_capacity(
    connection,
    limits: ArtifactRepositoryLimits,
) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE artifact_store.capacity_state
                SET max_artifact_bytes = %(max_artifact_bytes)s,
                    max_artifacts = %(max_artifacts)s,
                    max_total_content_bytes = %(max_total_content_bytes)s,
                    artifact_count = 0,
                    total_content_bytes = 0
                WHERE singleton_id = 1
                """,
                limits.model_dump(mode="python"),
            )


def _truncate_artifacts(connection) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE artifact_store.artifacts")


@pytest.mark.asyncio
async def test_m04_01_absent_put_found_get_and_fresh_typed_results(
    artifact_pool: AsyncConnectionPool,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """M04 1-4: absent → put → found → get, fresh DTOs, exact replay."""

    repository = _repository(artifact_pool, limits=test_repository_limits)
    request = _request(_operation("m04_basic"))

    absent = await repository.lookup(operation_id=request.operation_id)
    assert isinstance(absent, ArtifactAbsent)
    assert absent.operation_id == request.operation_id

    first_ref = await repository.put(request)
    found = await repository.lookup(operation_id=request.operation_id)
    assert isinstance(found, ArtifactFound)
    assert found.ref is not first_ref
    assert found.ref.model_dump() == first_ref.model_dump()

    first_read = await repository.get(first_ref, expected_model=StrictSampleArtifact)
    second_read = await repository.get(first_ref, expected_model=StrictSampleArtifact)
    assert type(first_read.content) is StrictSampleArtifact
    assert first_read.content == second_read.content
    assert first_read.content is not second_read.content
    assert first_read.ref is not first_ref

    replay = await repository.put(request)
    assert replay.model_dump() == first_ref.model_dump()


@pytest.mark.asyncio
async def test_m04_05_conflicts_preserve_original_and_do_not_add_methods(
    artifact_pool: AsyncConnectionPool,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """M04 5, 6, 17: content/type/version conflict preserves the original."""

    repository = _repository(artifact_pool, limits=test_repository_limits)
    operation_id = _operation("m04_conflict")
    original = await repository.put(_request(operation_id))

    conflicting_content = _request(
        operation_id,
        content={
            "label": "changed",
            "count": 1,
            "ratio": 1.0,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": None,
        },
    )
    with pytest.raises(ArtifactConflictError):
        await repository.put(conflicting_content)
    with pytest.raises(ArtifactConflictError):
        await repository.put(
            _request(operation_id, artifact_type="test.other_artifact")
        )
    with pytest.raises(ArtifactConflictError):
        await repository.put(_request(operation_id, schema_version="1.1"))

    preserved = await repository.get(original, expected_model=StrictSampleArtifact)
    assert preserved.content.label == "contract"
    assert preserved.ref.model_dump() == original.model_dump()


@pytest.mark.asyncio
async def test_m04_07_different_operations_do_not_deduplicate_by_hash(
    artifact_pool: AsyncConnectionPool,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """M04 7: identical canonical bytes under different operations get new refs."""

    repository = _repository(artifact_pool, limits=test_repository_limits)
    first = await repository.put(_request(_operation("m04_same_body_one")))
    second = await repository.put(_request(_operation("m04_same_body_two")))
    assert second.artifact_id != first.artifact_id
    assert second.content_hash == first.content_hash


@pytest.mark.asyncio
async def test_m04_08_unknown_id_ref_field_tampering_and_expected_model(
    artifact_pool: AsyncConnectionPool,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """M04 8-10: unknown IDs, all seven ref fields, and model identity."""

    repository = _repository(artifact_pool, limits=test_repository_limits)
    first = await repository.put(_request(_operation("m04_integrity_first")))
    other = await repository.put(_request(_operation("m04_integrity_other")))
    version = await repository.put(
        _request(_operation("m04_integrity_version"), schema_version="1.1")
    )
    different_hash = await repository.put(
        _request(
            _operation("m04_integrity_hash"),
            content={
                "label": "different-hash",
                "count": 1,
                "ratio": 1.0,
                "occurred_on": "2026-08-11",
                "observed_at": "2026-08-11T09:30:00Z",
                "note": None,
            },
        )
    )

    unknown = first.model_copy(update={"artifact_id": uuid4()})
    with pytest.raises(ArtifactNotFoundError):
        await repository.get(unknown, expected_model=StrictSampleArtifact)

    tampered = {
        "artifact_id": first.model_copy(update={"artifact_id": other.artifact_id}),
        "artifact_type": first.model_copy(update={"artifact_type": "test.other_artifact"}),
        "schema_version": first.model_copy(update={"schema_version": version.schema_version}),
        "content_hash": first.model_copy(update={"content_hash": different_hash.content_hash}),
        "operation_id": first.model_copy(update={"operation_id": other.operation_id}),
        "created_at": first.model_copy(
            update={"created_at": first.created_at + timedelta(seconds=1)}
        ),
        "size_bytes": first.model_copy(update={"size_bytes": first.size_bytes + 1}),
    }
    for field_name, candidate in tampered.items():
        assert getattr(candidate, field_name) != getattr(first, field_name)
        with pytest.raises(ArtifactIntegrityError):
            await repository.get(candidate, expected_model=StrictSampleArtifact)

    with pytest.raises(ArtifactExpectedModelMismatchError):
        await repository.get(first, expected_model=_OtherExpectedArtifact)


@pytest.mark.asyncio
async def test_m04_11_validation_size_count_total_and_replay_priority(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """M04 11-16: invalid writes and all capacity/error-priority semantics."""

    invalid_repository = _repository(artifact_pool, limits=test_repository_limits)
    invalid_request = ArtifactWriteRequest.model_construct(
        operation_id=_operation("m04_invalid"),
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={"bad": object()},
    )
    with pytest.raises(ArtifactRequestValidationError):
        await invalid_repository.put(invalid_request)
    assert isinstance(
        await invalid_repository.lookup(operation_id=invalid_request.operation_id),
        ArtifactAbsent,
    )

    too_small_limits = ArtifactRepositoryLimits(
        max_artifact_bytes=1,
        max_artifacts=8,
        max_total_content_bytes=32_768,
    )
    _truncate_artifacts(db_connection)
    _set_capacity(db_connection, too_small_limits)
    too_small = _repository(artifact_pool, limits=too_small_limits)
    too_large_request = _request(_operation("m04_too_large"))
    with pytest.raises(ArtifactTooLargeError):
        await too_small.put(too_large_request)
    assert isinstance(
        await too_small.lookup(operation_id=too_large_request.operation_id),
        ArtifactAbsent,
    )

    count_limits = ArtifactRepositoryLimits(
        max_artifact_bytes=4096,
        max_artifacts=1,
        max_total_content_bytes=32_768,
    )
    _truncate_artifacts(db_connection)
    _set_capacity(db_connection, count_limits)
    count_repository = _repository(artifact_pool, limits=count_limits)
    count_first = _request(_operation("m04_count_first"))
    count_ref = await count_repository.put(count_first)
    assert (await count_repository.put(count_first)).model_dump() == count_ref.model_dump()
    with pytest.raises(ArtifactCapacityExceededError):
        await count_repository.put(_request(_operation("m04_count_second")))

    total_probe_request = _request(_operation("m04_total_probe"))
    total_size = _registry().prepare(total_probe_request).size_bytes
    total_limits = ArtifactRepositoryLimits(
        max_artifact_bytes=total_size,
        max_artifacts=2,
        max_total_content_bytes=total_size,
    )
    _truncate_artifacts(db_connection)
    _set_capacity(db_connection, total_limits)
    total_repository = _repository(artifact_pool, limits=total_limits)
    total_first = _request(_operation("m04_total_first"))
    total_ref = await total_repository.put(total_first)
    with pytest.raises(ArtifactCapacityExceededError):
        await total_repository.put(_request(_operation("m04_total_second")))
    assert (
        await total_repository.get(total_ref, expected_model=StrictSampleArtifact)
    ).content.label == "contract"
