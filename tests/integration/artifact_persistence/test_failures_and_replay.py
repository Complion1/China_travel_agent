"""AP-T04 transaction rollback and unique-violation replay tests."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock
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
    ArtifactRepositoryDependencyError,
    ArtifactRepositoryError,
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


def _request(operation_id: OperationId, *, label: str = "failure") -> ArtifactWriteRequest:
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


def _drop_test_object(
    connection,
    trigger_name: str,
    function_name: str,
    table_name: str = "artifact_store.artifacts",
) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}")
            cursor.execute(f"DROP FUNCTION IF EXISTS artifact_store.{function_name}()")


@pytest.mark.asyncio
async def test_insert_failure_rolls_back_artifact_and_capacity(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """A failing Artifact INSERT cannot leave a capacity increment."""

    function_name = "m10_test_fail_insert"
    trigger_name = "m10_test_fail_insert_trigger"
    with db_connection.transaction():
        with db_connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE FUNCTION artifact_store.{function_name}()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$ BEGIN RAISE EXCEPTION 'synthetic insert failure'; END; $$
                """
            )
            cursor.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE INSERT ON artifact_store.artifacts
                FOR EACH ROW EXECUTE FUNCTION artifact_store.{function_name}()
                """
            )
    try:
        repository = _repository(artifact_pool, test_repository_limits)
        with pytest.raises(ArtifactRepositoryDependencyError) as error:
            await repository.put(_request(_operation("insert_failure")))
        assert str(error.value) == "ArtifactRepositoryDependencyError"
        assert error.value.__cause__ is None
    finally:
        _drop_test_object(
            db_connection,
            trigger_name,
            function_name,
        )

    assert _counts(db_connection) == (0, 0, 0)


@pytest.mark.asyncio
async def test_capacity_update_failure_rolls_back_insert_and_capacity(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """A failing capacity UPDATE rolls the already executed INSERT back."""

    function_name = "m10_test_fail_capacity_update"
    trigger_name = "m10_test_fail_capacity_update_trigger"
    with db_connection.transaction():
        with db_connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE FUNCTION artifact_store.{function_name}()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$ BEGIN RAISE EXCEPTION 'synthetic capacity failure'; END; $$
                """
            )
            cursor.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE UPDATE OF artifact_count, total_content_bytes
                ON artifact_store.capacity_state
                FOR EACH ROW EXECUTE FUNCTION artifact_store.{function_name}()
                """
            )
    try:
        repository = _repository(artifact_pool, test_repository_limits)
        with pytest.raises(ArtifactRepositoryDependencyError):
            await repository.put(_request(_operation("capacity_failure")))
    finally:
        _drop_test_object(
            db_connection,
            trigger_name,
            function_name,
            "artifact_store.capacity_state",
        )

    assert _counts(db_connection) == (0, 0, 0)


@pytest.mark.asyncio
async def test_capacity_failure_leaves_lookup_absent_and_conflict_preserves_state(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """Capacity rejection and conflict do not create or mutate extra rows."""

    limits = ArtifactRepositoryLimits(
        max_artifact_bytes=4096,
        max_artifacts=1,
        max_total_content_bytes=32_768,
    )
    _set_capacity(db_connection, limits)
    repository = _repository(artifact_pool, limits)
    original_request = _request(_operation("capacity_original"), label="original")
    original_ref = await repository.put(original_request)
    before = _counts(db_connection)

    with pytest.raises(ArtifactConflictError):
        await repository.put(
            _request(original_request.operation_id, label="conflict")
        )
    assert _counts(db_connection) == before

    rejected_request = _request(_operation("capacity_rejected"))
    with pytest.raises(ArtifactCapacityExceededError):
        await repository.put(rejected_request)
    assert _counts(db_connection) == before
    absent = await repository.lookup(operation_id=rejected_request.operation_id)
    assert absent.outcome_type == "absent"
    assert (
        await repository.get(original_ref, expected_model=StrictSampleArtifact)
    ).content.label == "original"


@pytest.mark.asyncio
async def test_conflict_precedes_oversized_and_prepare_runs_once_per_put(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate operation is resolved before size/capacity rejection."""

    operation_id = _operation("oversized_conflict")
    original_request = _request(operation_id, label="original")
    oversized_request = _request(operation_id, label="x" * 512)
    probe_registry = _registry()
    original_size = probe_registry.prepare(original_request).size_bytes
    oversized_size = probe_registry.prepare(oversized_request).size_bytes
    limits = ArtifactRepositoryLimits(
        max_artifact_bytes=original_size + 1,
        max_artifacts=1,
        max_total_content_bytes=original_size + 1,
    )
    assert original_size <= limits.max_artifact_bytes
    assert oversized_size > limits.max_artifact_bytes
    _set_capacity(db_connection, limits)

    registry = _registry()
    prepare_spy = Mock(wraps=registry.prepare)
    monkeypatch.setattr(registry, "prepare", prepare_spy)
    repository = _PostgresArtifactRepository(
        pool=artifact_pool,
        registry=registry,
        limits=limits,
    )

    original_ref = await repository.put(original_request)
    before = _counts(db_connection)
    original_content = (
        await repository.get(original_ref, expected_model=StrictSampleArtifact)
    ).content

    with pytest.raises(ArtifactConflictError):
        await repository.put(oversized_request)

    replayed_content = (
        await repository.get(original_ref, expected_model=StrictSampleArtifact)
    ).content
    assert replayed_content.model_dump() == original_content.model_dump()
    assert _counts(db_connection) == before
    assert prepare_spy.call_count == 2
    assert [call.args[0] for call in prepare_spy.call_args_list] == [
        original_request,
        oversized_request,
    ]


@pytest.mark.asyncio
async def test_unique_primary_key_violation_rolls_back_then_reports_dependency(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """A UUID collision is not retried with a newly generated UUID."""

    function_name = "m10_test_force_artifact_id"
    trigger_name = "m10_test_force_artifact_id_trigger"
    fixed_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    with db_connection.transaction():
        with db_connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE FUNCTION artifact_store.{function_name}()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$ BEGIN NEW.artifact_id := '{fixed_id}'; RETURN NEW; END; $$
                """
            )
            cursor.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE INSERT ON artifact_store.artifacts
                FOR EACH ROW EXECUTE FUNCTION artifact_store.{function_name}()
                """
            )
    try:
        repository = _repository(artifact_pool, test_repository_limits)
        first_request = _request(_operation("uuid_first"))
        first_ref = await repository.put(first_request)
        assert str(first_ref.artifact_id) == fixed_id
        second_request = _request(_operation("uuid_second"))
        with pytest.raises(ArtifactRepositoryDependencyError):
            await repository.put(second_request)
    finally:
        _drop_test_object(db_connection, trigger_name, function_name)

    assert _counts(db_connection)[0:2] == (1, 1)
    assert (await repository.lookup(operation_id=second_request.operation_id)).outcome_type == "absent"


@pytest.mark.asyncio
@pytest.mark.parametrize("same_content", [True, False])
async def test_unique_operation_violation_rolls_back_and_rechecks_new_transaction(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
    monkeypatch: pytest.MonkeyPatch,
    same_content: bool,
) -> None:
    """An external first winner is observed only after rollback in a short read transaction."""

    operation_id = _operation("unique_operation_recheck")
    request = _request(operation_id, label="repository")
    external_request = request if same_content else _request(operation_id, label="external")
    repository = _repository(artifact_pool, test_repository_limits)
    registry = _registry()
    prepared = registry.prepare(external_request)

    original_fetch = _PostgresArtifactRepository._fetch_by_operation
    fetch_count = 0
    second_query_seen = asyncio.Event()
    release_repository = asyncio.Event()

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
            if fetch_count == 2:
                second_query_seen.set()
                await release_repository.wait()
        return row

    monkeypatch.setattr(_PostgresArtifactRepository, "_fetch_by_operation", gated_fetch)
    task = asyncio.create_task(repository.put(request))
    try:
        await asyncio.wait_for(second_query_seen.wait(), timeout=5)
        async with artifact_pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO artifact_store.artifacts (
                            run_id, phase, logical_step, artifact_type, schema_version,
                            content_hash, canonical_bytes, size_bytes
                        )
                        VALUES (%(run_id)s, %(phase)s, %(logical_step)s,
                                %(artifact_type)s, %(schema_version)s,
                                %(content_hash)s, %(canonical_bytes)s, %(size_bytes)s)
                        """,
                        {
                            "run_id": prepared.operation_id.run_id,
                            "phase": prepared.operation_id.phase.value,
                            "logical_step": prepared.operation_id.logical_step,
                            "artifact_type": prepared.artifact_type,
                            "schema_version": prepared.schema_version,
                            "content_hash": prepared.content_hash,
                            "canonical_bytes": prepared.canonical_bytes,
                            "size_bytes": prepared.size_bytes,
                        },
                    )
        release_repository.set()
        try:
            result = await task
        except ArtifactRepositoryError as error:
            result = error
    finally:
        release_repository.set()
        if not task.done():
            await task

    if same_content:
        assert not isinstance(result, Exception)
        found = await repository.lookup(operation_id=operation_id)
        assert found.outcome_type == "found"
        assert result.model_dump() == found.ref.model_dump()
    else:
        assert isinstance(result, ArtifactConflictError)
    assert _counts(db_connection)[1:] == (0, 0)
