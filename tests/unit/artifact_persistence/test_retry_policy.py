"""AP-T05 retry boundaries and immutable input snapshot tests."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from psycopg import InterfaceError, OperationalError
from psycopg.errors import (
    ConnectionFailure,
    InsufficientPrivilege,
    InvalidPassword,
    LockNotAvailable,
    QueryCanceled,
    SyntaxError as PostgresSyntaxError,
)
from psycopg_pool import AsyncConnectionPool, PoolTimeout

import app.artifact_persistence.repository as repository_module
from app.artifact_persistence.repository import _PostgresArtifactRepository
from app.workflow_contracts.artifacts.models import (
    ArtifactAbsent,
    ArtifactReadResult,
    ArtifactRef,
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
    ArtifactIntegrityError,
    ArtifactRepositoryDependencyError,
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase
from tests.unit.workflow_contracts.fakes import StrictSampleArtifact


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


class _OpenPool(AsyncConnectionPool):
    def __init__(self) -> None:
        self.is_closed = False

    @property
    def closed(self) -> bool:
        return self.is_closed


def _request() -> ArtifactWriteRequest:
    return ArtifactWriteRequest(
        operation_id=OperationId(
            run_id=uuid4(),
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="retry_test",
        ),
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={
            "label": "retry",
            "count": 1,
            "ratio": 1.0,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": None,
        },
    )


def _repository(registry: ArtifactSchemaRegistry | None = None):
    return _PostgresArtifactRepository(
        pool=_OpenPool(),
        registry=registry or _registry(),
        limits=ArtifactRepositoryLimits(
            max_artifact_bytes=4096,
            max_artifacts=8,
            max_total_content_bytes=32768,
        ),
    )


@pytest.mark.asyncio
async def test_put_retries_pool_timeout_once_and_prepares_once(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry()
    prepare = Mock(wraps=registry.prepare)
    monkeypatch.setattr(registry, "prepare", prepare)
    repository = _repository(registry)
    request = _request()
    attempts = []

    async def fake_put(self, prepared):
        attempts.append(prepared)
        if len(attempts) == 1:
            raise PoolTimeout()
        return prepared

    monkeypatch.setattr(_PostgresArtifactRepository, "_put_with_connection", fake_put)
    prepared = await repository.put(request)

    assert len(attempts) == 2
    assert attempts[0] is attempts[1]
    assert prepared.operation_id == request.operation_id
    assert prepare.call_count == 1


@pytest.mark.asyncio
async def test_put_reuses_snapshot_after_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry()
    prepare = Mock(wraps=registry.prepare)
    monkeypatch.setattr(registry, "prepare", prepare)
    repository = _repository(registry)
    request = _request()
    attempts = []

    async def fake_put(self, prepared):
        attempts.append(prepared)
        if len(attempts) == 1:
            request.content["label"] = "mutated-after-prepare"
            raise OperationalError("connection interrupted")
        return prepared

    monkeypatch.setattr(_PostgresArtifactRepository, "_put_with_connection", fake_put)
    result = await repository.put(request)

    assert len(attempts) == 2
    assert attempts[0] is attempts[1]
    assert b"mutated-after-prepare" not in attempts[1].canonical_bytes
    assert result is attempts[0]
    assert prepare.call_count == 1


@pytest.mark.asyncio
async def test_lookup_and_get_each_retry_once(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository()
    operation_id = _request().operation_id
    lookup_attempts = 0

    async def fake_lookup(self, current_operation_id):
        nonlocal lookup_attempts
        lookup_attempts += 1
        if lookup_attempts == 1:
            raise OperationalError("connection interrupted")
        return ArtifactAbsent(operation_id=current_operation_id)

    monkeypatch.setattr(_PostgresArtifactRepository, "_lookup_once", fake_lookup)
    absent = await repository.lookup(operation_id=operation_id)
    assert isinstance(absent, ArtifactAbsent)
    assert lookup_attempts == 2

    get_attempts = 0

    async def fake_get(self, fresh_ref, *, expected_model):
        nonlocal get_attempts
        get_attempts += 1
        if get_attempts == 1:
            raise PoolTimeout()
        return ArtifactReadResult(
            ref=fresh_ref,
            content=StrictSampleArtifact(
                label="retry",
                count=1,
                ratio=1.0,
                occurred_on=date(2026, 8, 11),
                observed_at=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
                note=None,
            ),
        )

    monkeypatch.setattr(_PostgresArtifactRepository, "_get_once", fake_get)
    prepared = repository._registry.prepare(_request())
    from app.workflow_contracts.artifacts.models import ArtifactRef

    ref_value = ArtifactRef(
        artifact_id=uuid4(),
        artifact_type=prepared.artifact_type,
        schema_version=prepared.schema_version,
        content_hash=prepared.content_hash,
        operation_id=prepared.operation_id,
        created_at=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
        size_bytes=prepared.size_bytes,
    )
    result = await repository.get(ref_value, expected_model=StrictSampleArtifact)
    assert result.ref == ref_value
    assert get_attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "expected_attempts"),
    [
        pytest.param(lambda: PoolTimeout(), 2, id="pool-timeout-retries-once"),
        pytest.param(
            lambda: ConnectionFailure("connection class 08"),
            2,
            id="sqlstate-08-retries-once",
        ),
        pytest.param(
            lambda: OperationalError("connection without sqlstate"),
            2,
            id="operational-error-without-sqlstate-retries-once",
        ),
        pytest.param(lambda: QueryCanceled("statement timeout"), 1, id="sqlstate-57014-once"),
        pytest.param(lambda: LockNotAvailable("lock timeout"), 1, id="sqlstate-55p03-once"),
        pytest.param(
            lambda: InsufficientPrivilege("permission denied"),
            1,
            id="sqlstate-42501-once",
        ),
        pytest.param(lambda: InvalidPassword("authentication failed"), 1, id="sqlstate-28p01-once"),
        pytest.param(InterfaceError, 1, id="interface-error-once"),
        pytest.param(
            lambda: PostgresSyntaxError("syntax error"),
            1,
            id="other-database-error-once",
        ),
    ],
)
async def test_retry_policy_classifies_real_database_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_factory,
    expected_attempts: int,
) -> None:
    repository = _repository()
    attempts = 0

    async def fake_put(self, prepared):
        nonlocal attempts
        attempts += 1
        raise error_factory()

    monkeypatch.setattr(_PostgresArtifactRepository, "_put_with_connection", fake_put)
    with pytest.raises(ArtifactRepositoryDependencyError) as error:
        await repository.put(_request())

    assert attempts == expected_attempts
    assert str(error.value) == "ArtifactRepositoryDependencyError"
    assert error.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "business_error",
    [ArtifactConflictError, ArtifactIntegrityError, ArtifactCapacityExceededError],
)
async def test_module04_business_and_integrity_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    business_error: type[Exception],
) -> None:
    repository = _repository()
    attempts = 0

    async def fake_put(self, prepared):
        nonlocal attempts
        attempts += 1
        raise business_error()

    monkeypatch.setattr(_PostgresArtifactRepository, "_put_with_connection", fake_put)
    with pytest.raises(business_error):
        await repository.put(_request())
    assert attempts == 1


@pytest.mark.asyncio
async def test_lookup_and_get_each_create_one_strict_input_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry()
    repository = _repository(registry)
    strict_copy = Mock(wraps=repository_module._strict_copy)
    monkeypatch.setattr(repository_module, "_strict_copy", strict_copy)

    operation_id = _request().operation_id

    async def fake_lookup(self, current_operation_id):
        return ArtifactAbsent(operation_id=current_operation_id)

    monkeypatch.setattr(_PostgresArtifactRepository, "_lookup_once", fake_lookup)
    await repository.lookup(operation_id=operation_id)

    prepared = registry.prepare(_request())
    ref_value = ArtifactRef(
        artifact_id=uuid4(),
        artifact_type=prepared.artifact_type,
        schema_version=prepared.schema_version,
        content_hash=prepared.content_hash,
        operation_id=prepared.operation_id,
        created_at=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
        size_bytes=prepared.size_bytes,
    )

    async def fake_get(self, fresh_ref, *, expected_model):
        return ArtifactReadResult(
            ref=fresh_ref,
            content=StrictSampleArtifact(
                label="retry",
                count=1,
                ratio=1.0,
                occurred_on=date(2026, 8, 11),
                observed_at=datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc),
                note=None,
            ),
        )

    monkeypatch.setattr(_PostgresArtifactRepository, "_get_once", fake_get)
    await repository.get(ref_value, expected_model=StrictSampleArtifact)

    assert strict_copy.call_count == 2
    assert strict_copy.call_args_list[0].args[0] is operation_id
    assert strict_copy.call_args_list[0].args[1] is OperationId
    assert strict_copy.call_args_list[1].args[0] is ref_value
    assert strict_copy.call_args_list[1].args[1] is ArtifactRef


@pytest.mark.asyncio
async def test_second_transient_failure_maps_dependency_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository()
    attempts = 0

    async def fake_put(self, prepared):
        nonlocal attempts
        attempts += 1
        raise PoolTimeout()

    monkeypatch.setattr(_PostgresArtifactRepository, "_put_with_connection", fake_put)
    with pytest.raises(ArtifactRepositoryDependencyError) as error:
        await repository.put(_request())
    assert attempts == 2
    assert str(error.value) == "ArtifactRepositoryDependencyError"
    assert error.value.__cause__ is None


@pytest.mark.asyncio
async def test_non_retryable_error_attempts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository()
    attempts = 0

    async def fake_put(self, prepared):
        nonlocal attempts
        attempts += 1
        raise ValueError("not a connection failure")

    monkeypatch.setattr(_PostgresArtifactRepository, "_put_with_connection", fake_put)
    with pytest.raises(ArtifactRepositoryDependencyError):
        await repository.put(_request())
    assert attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("base_error", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
async def test_base_exception_is_not_wrapped_or_retried(
    monkeypatch: pytest.MonkeyPatch,
    base_error: type[BaseException],
) -> None:
    repository = _repository()
    attempts = 0

    async def fake_put(self, prepared):
        nonlocal attempts
        attempts += 1
        raise base_error()

    monkeypatch.setattr(_PostgresArtifactRepository, "_put_with_connection", fake_put)
    with pytest.raises(base_error):
        await repository.put(_request())
    assert attempts == 1
