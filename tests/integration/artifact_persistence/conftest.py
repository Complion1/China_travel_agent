"""只消费宿主编排器提供的隔离 PostgreSQL 身份，不控制 Docker。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool
import pytest

from app.artifact_persistence.settings import (
    ArtifactPersistenceSettings,
    load_artifact_persistence_settings,
)
from app.workflow_contracts.artifacts.models import ArtifactRepositoryLimits


_IDENTITY_TOKEN = "MODULE10_TEST_IDENTITY_TOKEN"
_PREFIX = "MODULE10_TEST_PREFIX"
_EXPECTED_REVISION = "m10_0002"

TEST_REPOSITORY_LIMITS = ArtifactRepositoryLimits(
    max_artifact_bytes=1_000_000,
    max_artifacts=1_000,
    max_total_content_bytes=10_000_000,
)


def _test_identity() -> tuple[str, str]:
    token = os.environ.get(_IDENTITY_TOKEN, "")
    prefix = os.environ.get(_PREFIX, "")
    if type(token) is not str or token == "" or type(prefix) is not str or prefix == "":
        pytest.fail("missing Module 10 isolated test identity")
    if token != f"{prefix}-identity":
        pytest.fail("invalid Module 10 isolated test identity")
    return token, prefix


@pytest.fixture(scope="session")
def artifact_persistence_settings() -> ArtifactPersistenceSettings:
    _, prefix = _test_identity()
    settings = load_artifact_persistence_settings()
    if prefix not in settings.database:
        pytest.fail("test database name does not contain the isolated prefix")
    return settings


@pytest.fixture(scope="session")
def migrated_artifact_database(
    artifact_persistence_settings: ArtifactPersistenceSettings,
) -> None:
    with psycopg.connect(
        host=artifact_persistence_settings.host,
        port=artifact_persistence_settings.port,
        dbname=artifact_persistence_settings.database,
        user=artifact_persistence_settings.user,
        password=artifact_persistence_settings.password.get_secret_value(),
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT version_num
                FROM artifact_store.alembic_version
                ORDER BY version_num
                """
            )
            revisions = [row[0] for row in cursor.fetchall()]
    if revisions != [_EXPECTED_REVISION]:
        pytest.fail("isolated database is not migrated to m10_0002")


@pytest.fixture
def db_connection(
    artifact_persistence_settings: ArtifactPersistenceSettings,
    migrated_artifact_database: None,
) -> Iterator[psycopg.Connection]:
    with psycopg.connect(
        host=artifact_persistence_settings.host,
        port=artifact_persistence_settings.port,
        dbname=artifact_persistence_settings.database,
        user=artifact_persistence_settings.user,
        password=artifact_persistence_settings.password.get_secret_value(),
    ) as connection:
        yield connection


@pytest.fixture
def artifact_store_isolated(
    db_connection: psycopg.Connection,
) -> Iterator[None]:
    """Reset only the explicitly isolated artifact test database state."""

    def reset() -> None:
        with db_connection.transaction():
            with db_connection.cursor() as cursor:
                cursor.execute("TRUNCATE TABLE artifact_store.artifacts")
                cursor.execute(
                    """
                    INSERT INTO artifact_store.capacity_state (
                        singleton_id,
                        max_artifact_bytes,
                        max_artifacts,
                        max_total_content_bytes,
                        artifact_count,
                        total_content_bytes
                    )
                    VALUES (1, %(max_artifact_bytes)s, %(max_artifacts)s,
                            %(max_total_content_bytes)s, 0, 0)
                    ON CONFLICT (singleton_id) DO UPDATE SET
                        max_artifact_bytes = EXCLUDED.max_artifact_bytes,
                        max_artifacts = EXCLUDED.max_artifacts,
                        max_total_content_bytes = EXCLUDED.max_total_content_bytes,
                        artifact_count = 0,
                        total_content_bytes = 0
                    """,
                    TEST_REPOSITORY_LIMITS.model_dump(mode="python"),
                )

    reset()
    try:
        yield
    finally:
        reset()


@pytest_asyncio.fixture
async def artifact_pool(
    artifact_persistence_settings: ArtifactPersistenceSettings,
    migrated_artifact_database: None,
) -> AsyncIterator[AsyncConnectionPool]:
    """Provide an opened pool; Docker lifecycle remains host-owned."""

    settings = artifact_persistence_settings
    pool = AsyncConnectionPool(
        conninfo="",
        kwargs={
            "host": settings.host,
            "port": settings.port,
            "dbname": settings.database,
            "user": settings.user,
            "password": settings.password.get_secret_value(),
            "connect_timeout": 10,
            "options": "-c timezone=UTC -c statement_timeout=10000 -c lock_timeout=5000",
        },
        min_size=1,
        max_size=4,
        timeout=10,
        open=False,
    )
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def test_repository_limits() -> ArtifactRepositoryLimits:
    return TEST_REPOSITORY_LIMITS
