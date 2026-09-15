"""AP-T05/AP-T06 real restart, runtime recreation, and reconnect evidence."""

from __future__ import annotations

import asyncio
from uuid import UUID

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.artifact_persistence import create_artifact_persistence_runtime
from app.artifact_persistence.settings import ArtifactPersistenceSettings
from app.workflow_contracts.artifacts.models import (
    ArtifactFound,
    ArtifactReadResult,
    ArtifactRepositoryLimits,
    ArtifactWriteRequest,
)
from app.workflow_contracts.artifacts.registry import (
    ArtifactSchemaRegistration,
    ArtifactSchemaRegistry,
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase
from tests.unit.workflow_contracts.fakes import StrictSampleArtifact


_RESTART_RUN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_RESTART_OPERATION = OperationId(
    run_id=_RESTART_RUN_ID,
    phase=WorkflowPhase.REQUIREMENTS,
    logical_step="restart_persistence",
)
_RECONNECT_OPERATION = OperationId(
    run_id=_RESTART_RUN_ID,
    phase=WorkflowPhase.REQUIREMENTS,
    logical_step="reconnect_after_disconnect",
)


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


def _request(operation_id: OperationId, label: str) -> ArtifactWriteRequest:
    return ArtifactWriteRequest(
        operation_id=operation_id,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={
            "label": label,
            "count": 10,
            "ratio": 2.5,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": "fixed restart seed",
        },
    )


async def _runtime(
    settings: ArtifactPersistenceSettings,
    limits: ArtifactRepositoryLimits,
):
    return await create_artifact_persistence_runtime(
        settings=settings,
        registry=_registry(),
        limits=limits,
    )


async def _assert_persisted(runtime, operation_id: OperationId, label: str) -> None:
    outcome = await runtime.repository.lookup(operation_id=operation_id)
    assert isinstance(outcome, ArtifactFound)
    read = await runtime.repository.get(
        outcome.ref,
        expected_model=StrictSampleArtifact,
    )
    assert isinstance(read, ArtifactReadResult)
    assert read.content.label == label


@pytest.mark.asyncio
async def test_restart_seed(
    artifact_persistence_settings: ArtifactPersistenceSettings,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """The host-run seed node writes one fixed operation for the later verify node."""

    runtime = await _runtime(artifact_persistence_settings, test_repository_limits)
    try:
        ref = await runtime.repository.put(
            _request(_RESTART_OPERATION, "restart-seed")
        )
        assert ref.operation_id == _RESTART_OPERATION
        await _assert_persisted(runtime, _RESTART_OPERATION, "restart-seed")
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_restart_verify(
    artifact_persistence_settings: ArtifactPersistenceSettings,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """A new process after the real database restart can read the seed unchanged."""

    first_runtime = await _runtime(
        artifact_persistence_settings,
        test_repository_limits,
    )
    try:
        await _assert_persisted(first_runtime, _RESTART_OPERATION, "restart-seed")
    finally:
        await first_runtime.close()

    second_runtime = await _runtime(
        artifact_persistence_settings,
        test_repository_limits,
    )
    try:
        await _assert_persisted(second_runtime, _RESTART_OPERATION, "restart-seed")
    finally:
        await second_runtime.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("artifact_store_isolated")
async def test_runtime_reconnects_after_real_backend_disconnect(
    artifact_persistence_settings: ArtifactPersistenceSettings,
    test_repository_limits: ArtifactRepositoryLimits,
    db_connection: psycopg.Connection,
) -> None:
    """Terminate a real pooled backend and prove the next repository call reconnects."""

    runtime = await _runtime(artifact_persistence_settings, test_repository_limits)
    try:
        await runtime.repository.put(
            _request(_RECONNECT_OPERATION, "reconnect-proof")
        )
        pool = getattr(runtime, "_pool")
        assert isinstance(pool, AsyncConnectionPool)
        try:
            async with pool.connection(timeout=5) as held_connection:
                async with held_connection.cursor() as cursor:
                    await cursor.execute("SELECT pg_backend_pid()")
                    backend_pid = (await cursor.fetchone())[0]
                with db_connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_terminate_backend(%s)",
                        (backend_pid,),
                    )
                    assert cursor.fetchone()[0] is True
                db_connection.commit()
                try:
                    await held_connection.execute("SELECT 1")
                except psycopg.Error:
                    pass
        except psycopg.Error:
            # The pool may surface the terminated connection while returning it.
            pass

        outcome = await asyncio.wait_for(
            runtime.repository.lookup(operation_id=_RECONNECT_OPERATION),
            timeout=10,
        )
        assert isinstance(outcome, ArtifactFound)
        assert outcome.ref.operation_id == _RECONNECT_OPERATION
    finally:
        await runtime.close()
