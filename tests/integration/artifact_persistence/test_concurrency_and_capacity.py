"""AP-T04 real PostgreSQL concurrency and capacity gates."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from psycopg_pool import AsyncConnectionPool

from app.artifact_persistence.repository import _PostgresArtifactRepository
from app.workflow_contracts.artifacts.models import (
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
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase
from tests.unit.workflow_contracts.fakes import StrictSampleArtifact


pytestmark = pytest.mark.usefixtures("artifact_store_isolated")


def _registry() -> ArtifactSchemaRegistry:
    return ArtifactSchemaRegistry(
        (
            ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.0",
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
    label: str = "concurrency",
) -> ArtifactWriteRequest:
    return ArtifactWriteRequest(
        operation_id=operation_id,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={
            "label": label,
            "count": 1,
            "ratio": 1.0,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": None,
        },
    )


def _repository(
    pool: AsyncConnectionPool,
    limits: ArtifactRepositoryLimits,
) -> _PostgresArtifactRepository:
    return _PostgresArtifactRepository(
        pool=pool,
        registry=_registry(),
        limits=limits,
    )


def _set_capacity(connection, limits: ArtifactRepositoryLimits) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE TABLE artifact_store.artifacts"
            )
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


def _counts(connection) -> tuple[int, int, int]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM artifact_store.artifacts")
        row_count = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT artifact_count, total_content_bytes
            FROM artifact_store.capacity_state
            WHERE singleton_id = 1
            """
        )
        artifact_count, total_content_bytes = cursor.fetchone()
    return row_count, artifact_count, total_content_bytes


async def _parallel_puts(
    repositories: tuple[_PostgresArtifactRepository, ...],
    requests: tuple[ArtifactWriteRequest, ...],
) -> list[object]:
    barrier = asyncio.Barrier(len(repositories))

    async def run(repository, request):
        await barrier.wait()
        try:
            return await repository.put(request)
        except Exception as error:
            return error

    return list(await asyncio.gather(*(run(r, q) for r, q in zip(repositories, requests, strict=True))))


@pytest.mark.asyncio
async def test_concurrency_uses_two_real_backend_connections(
    artifact_pool: AsyncConnectionPool,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """The qualification gate uses a real pool with independent PostgreSQL sessions."""

    async with artifact_pool.connection() as first, artifact_pool.connection() as second:
        async with first.cursor() as first_cursor, second.cursor() as second_cursor:
            await first_cursor.execute("SELECT pg_backend_pid()")
            await second_cursor.execute("SELECT pg_backend_pid()")
            assert (await first_cursor.fetchone())[0] != (await second_cursor.fetchone())[0]

    repositories = (
        _repository(artifact_pool, test_repository_limits),
        _repository(artifact_pool, test_repository_limits),
    )
    results = await _parallel_puts(
        repositories,
        (
            _request(_operation("two_sessions_one")),
            _request(_operation("two_sessions_two")),
        ),
    )
    assert all(not isinstance(result, Exception) for result in results)


@pytest.mark.asyncio
async def test_same_operation_same_content_has_one_first_winner(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """Same OperationId/content: one row, equal refs, one capacity increment."""

    operation_id = _operation("same_operation_same_content")
    requests = (_request(operation_id), _request(operation_id))
    results = await _parallel_puts(
        (
            _repository(artifact_pool, test_repository_limits),
            _repository(artifact_pool, test_repository_limits),
        ),
        requests,
    )
    assert all(not isinstance(result, Exception) for result in results)
    assert results[0].model_dump() == results[1].model_dump()
    assert _counts(db_connection) == (1, 1, results[0].size_bytes)


@pytest.mark.asyncio
async def test_same_operation_different_content_is_conflict_and_preserves_state(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """Same OperationId/different content: one winner, one conflict, no overwrite."""

    operation_id = _operation("same_operation_different_content")
    results = await _parallel_puts(
        (
            _repository(artifact_pool, test_repository_limits),
            _repository(artifact_pool, test_repository_limits),
        ),
        (_request(operation_id, label="winner-a"), _request(operation_id, label="winner-b")),
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ArtifactConflictError) for result in results) == 1
    assert _counts(db_connection)[0:2] == (1, 1)


@pytest.mark.asyncio
async def test_different_operations_with_same_body_are_not_hash_deduplicated(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """Different OperationIds may share a hash but must receive different UUIDs."""

    requests = (
        _request(_operation("different_operation_one")),
        _request(_operation("different_operation_two")),
    )
    results = await _parallel_puts(
        (
            _repository(artifact_pool, test_repository_limits),
            _repository(artifact_pool, test_repository_limits),
        ),
        requests,
    )
    assert all(not isinstance(result, Exception) for result in results)
    assert results[0].artifact_id != results[1].artifact_id
    assert results[0].content_hash == results[1].content_hash
    assert _counts(db_connection) == (2, 2, results[0].size_bytes + results[1].size_bytes)


@pytest.mark.asyncio
async def test_last_count_slot_allows_only_one_real_commit(
    artifact_pool: AsyncConnectionPool,
    db_connection,
) -> None:
    """Many independent connections compete for one quantity slot."""

    limits = ArtifactRepositoryLimits(
        max_artifact_bytes=4096,
        max_artifacts=1,
        max_total_content_bytes=32_768,
    )
    _set_capacity(db_connection, limits)
    requests = tuple(_request(_operation(f"count_race_{index}")) for index in range(8))
    results = await _parallel_puts(
        tuple(_repository(artifact_pool, limits) for _ in requests),
        requests,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ArtifactCapacityExceededError) for result in results) == 7
    assert _counts(db_connection) == (1, 1, next(result for result in results if not isinstance(result, Exception)).size_bytes)


@pytest.mark.asyncio
async def test_last_total_byte_slot_never_exceeds_total_capacity(
    artifact_pool: AsyncConnectionPool,
    db_connection,
) -> None:
    """Many independent connections compete for one total-byte boundary."""

    registry = _registry()
    probe = registry.prepare(_request(_operation("total_race_probe"))).size_bytes
    limits = ArtifactRepositoryLimits(
        max_artifact_bytes=probe,
        max_artifacts=16,
        max_total_content_bytes=probe,
    )
    _set_capacity(db_connection, limits)
    requests = tuple(_request(_operation(f"total_race_{index}")) for index in range(8))
    results = await _parallel_puts(
        tuple(_repository(artifact_pool, limits) for _ in requests),
        requests,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ArtifactCapacityExceededError) for result in results) == 7
    winner = next(result for result in results if not isinstance(result, Exception))
    assert _counts(db_connection) == (1, 1, winner.size_bytes)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_content", [True, False])
async def test_capacity_lock_wait_rechecks_operation_before_size_or_capacity(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
    monkeypatch: pytest.MonkeyPatch,
    same_content: bool,
) -> None:
    """An explicit post-first-query gate proves the lock-winner recheck order."""

    operation_id = _operation("capacity_lock_recheck")
    waiter_request = _request(operation_id, label="waiter")
    winner_request = waiter_request if same_content else _request(operation_id, label="winner")
    waiter = _repository(artifact_pool, test_repository_limits)
    winner = _repository(artifact_pool, test_repository_limits)

    original_fetch = _PostgresArtifactRepository._fetch_by_operation
    first_query_seen = asyncio.Event()
    release_waiter = asyncio.Event()
    fetch_count = 0

    async def gated_fetch(self, cursor, current_operation_id, *, includes_bytes):
        nonlocal fetch_count
        row = await original_fetch(
            self,
            cursor,
            current_operation_id,
            includes_bytes=includes_bytes,
        )
        if current_operation_id == operation_id and includes_bytes:
            fetch_count += 1
            if fetch_count == 1:
                first_query_seen.set()
                await release_waiter.wait()
        return row

    monkeypatch.setattr(_PostgresArtifactRepository, "_fetch_by_operation", gated_fetch)
    waiter_task = asyncio.create_task(waiter.put(waiter_request))
    try:
        await asyncio.wait_for(first_query_seen.wait(), timeout=5)
        winner_ref = await winner.put(winner_request)
        release_waiter.set()
        try:
            waiter_result = await waiter_task
        except Exception as error:
            waiter_result = error
    finally:
        release_waiter.set()
        if not waiter_task.done():
            await waiter_task

    if same_content:
        assert waiter_result.model_dump() == winner_ref.model_dump()
    else:
        assert isinstance(waiter_result, ArtifactConflictError)
    assert _counts(db_connection)[0:2] == (1, 1)
