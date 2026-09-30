"""Explicitly managed PostgreSQL runtime for Module 10."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from app.artifact_persistence.errors import (
    ArtifactPersistenceConfigurationError,
    ArtifactPersistenceDependencyError,
)
from app.artifact_persistence.repository import _PostgresArtifactRepository
from app.artifact_persistence.settings import ArtifactPersistenceSettings
from app.artifact_persistence.interaction_records import (
    DEMO_INTERACTION_STORE_LIMITS, InteractionStoreLimits,
    _PostgresInteractionRecorder, _diagnostic_schema_available,
)
from app.llm_runtime import InteractionRecorder
from app.workflow_contracts.artifacts.in_memory import (
    DEMO_ARTIFACT_REPOSITORY_LIMITS,
)
from app.workflow_contracts.artifacts.models import ArtifactRepositoryLimits
from app.workflow_contracts.artifacts.registry import ArtifactSchemaRegistry
from app.workflow_contracts.artifacts.repository import ArtifactRepository
from app.workflow_contracts.errors import ArtifactRepositoryUsageError


_ALEMBIC_CONFIG = Path(__file__).with_name("alembic.ini")
_EXPECTED_HEAD = "m10_0002"
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 4
_POOL_TIMEOUT = 10.0
_OPEN_TIMEOUT = 10.0
_CLOSE_TIMEOUT = 5.0
_CONNECTION_TIMEOUT = 10
_POOL_OPTIONS = "-c timezone=UTC -c statement_timeout=10000 -c lock_timeout=5000"

_EXPECTED_TABLES = ("alembic_version", "artifacts", "capacity_state")
_EXPECTED_ARTIFACT_COLUMNS = (
    ("artifact_id", "uuid", "uuid", "NO", "gen_random_uuid()", None, "NO", None, "NEVER", None),
    ("run_id", "uuid", "uuid", "NO", None, None, "NO", None, "NEVER", None),
    ("phase", "text", "text", "NO", None, None, "NO", None, "NEVER", None),
    ("logical_step", "character varying", "varchar", "NO", None, 64, "NO", None, "NEVER", None),
    ("artifact_type", "character varying", "varchar", "NO", None, 65, "NO", None, "NEVER", None),
    ("schema_version", "character varying", "varchar", "NO", None, 16, "NO", None, "NEVER", None),
    ("content_hash", "character", "bpchar", "NO", None, 71, "NO", None, "NEVER", None),
    ("canonical_bytes", "bytea", "bytea", "NO", None, None, "NO", None, "NEVER", None),
    ("size_bytes", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
    ("created_at", "timestamp with time zone", "timestamptz", "NO", "CURRENT_TIMESTAMP", None, "NO", None, "NEVER", None),
)
_EXPECTED_CAPACITY_COLUMNS = (
    ("singleton_id", "smallint", "int2", "NO", None, None, "NO", None, "NEVER", None),
    ("max_artifact_bytes", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
    ("max_artifacts", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
    ("max_total_content_bytes", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
    ("artifact_count", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
    ("total_content_bytes", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
)
_EXPECTED_ARTIFACT_CONSTRAINTS = {
    "ck_artifacts_artifact_type": r"CHECK (((artifact_type)::text ~ '^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$'::text))",
    "ck_artifacts_content_hash": r"CHECK ((content_hash ~ '^sha256:[0-9a-f]{64}$'::text))",
    "ck_artifacts_logical_step_format": r"CHECK (((logical_step)::text ~ '^[a-z][a-z0-9_]*$'::text))",
    "ck_artifacts_logical_step_length": "CHECK (((char_length((logical_step)::text) >= 1) AND (char_length((logical_step)::text) <= 64)))",
    "ck_artifacts_phase": "CHECK ((phase = ANY (ARRAY['requirements'::text, 'hotel_selection'::text, 'candidate_research'::text, 'itinerary_planning'::text, 'validation'::text, 'budget_evaluation'::text, 'output_formatting'::text])))",
    "ck_artifacts_schema_version": r"CHECK (((schema_version)::text ~ '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'::text))",
    "ck_artifacts_size_bytes_matches_content": "CHECK ((size_bytes = octet_length(canonical_bytes)))",
    "ck_artifacts_size_bytes_range": "CHECK (((size_bytes >= 1) AND (size_bytes <= '9007199254740991'::bigint)))",
    "pk_artifacts": "PRIMARY KEY (artifact_id)",
    "uq_artifacts_operation_id": "UNIQUE (run_id, phase, logical_step)",
}
_EXPECTED_CAPACITY_CONSTRAINTS = {
    "ck_capacity_state_artifact_count": "CHECK (((artifact_count >= 0) AND (artifact_count <= max_artifacts)))",
    "ck_capacity_state_max_artifact_bytes": "CHECK (((max_artifact_bytes >= 1) AND (max_artifact_bytes <= '9007199254740991'::bigint)))",
    "ck_capacity_state_max_artifacts": "CHECK (((max_artifacts >= 1) AND (max_artifacts <= '9007199254740991'::bigint)))",
    "ck_capacity_state_max_total_content_bytes": "CHECK (((max_total_content_bytes >= 1) AND (max_total_content_bytes <= '9007199254740991'::bigint)))",
    "ck_capacity_state_singleton": "CHECK ((singleton_id = 1))",
    "ck_capacity_state_total_at_least_single": "CHECK ((max_total_content_bytes >= max_artifact_bytes))",
    "ck_capacity_state_total_content_bytes": "CHECK (((total_content_bytes >= 0) AND (total_content_bytes <= max_total_content_bytes)))",
    "pk_capacity_state": "PRIMARY KEY (singleton_id)",
}


def _configuration_failure() -> None:
    raise ArtifactPersistenceConfigurationError() from None


def _dependency_failure() -> None:
    raise ArtifactPersistenceDependencyError() from None


def _usage_failure() -> None:
    raise ArtifactRepositoryUsageError() from None


def _is_dependency_database_error(error: BaseException) -> bool:
    if isinstance(error, PoolTimeout):
        return True
    if isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError)):
        return True
    sqlstate = getattr(error, "sqlstate", None)
    if type(sqlstate) is str:
        return sqlstate.startswith("08") or sqlstate in {
            "28P01",
            "28000",
            "3D000",
            "42501",
            "53300",
        }
    return False


def _copy_settings(settings: ArtifactPersistenceSettings) -> ArtifactPersistenceSettings:
    if type(settings) is not ArtifactPersistenceSettings:
        _usage_failure()
    try:
        values = settings.model_dump(mode="python", exclude_none=False)
        password = values["password"]
        if type(password) is not type(settings.password):
            _usage_failure()
        secret = password.get_secret_value()
        if type(secret) is not str or secret == "":
            _usage_failure()
        return ArtifactPersistenceSettings(
            host=values["host"],
            port=values["port"],
            database=values["database"],
            user=values["user"],
            password=type(password)(secret),
        )
    except ArtifactRepositoryUsageError:
        raise
    except Exception:
        _usage_failure()


def _copy_limits(limits: ArtifactRepositoryLimits) -> ArtifactRepositoryLimits:
    if type(limits) is not ArtifactRepositoryLimits:
        _usage_failure()
    try:
        values = limits.model_dump(mode="python", exclude_none=False)
        return ArtifactRepositoryLimits(
            max_artifact_bytes=values["max_artifact_bytes"],
            max_artifacts=values["max_artifacts"],
            max_total_content_bytes=values["max_total_content_bytes"],
        )
    except Exception:
        _usage_failure()


def _validate_registry(registry: ArtifactSchemaRegistry) -> None:
    if type(registry) is not ArtifactSchemaRegistry:
        _usage_failure()


def _pool_kwargs(settings: ArtifactPersistenceSettings) -> dict[str, object]:
    return {
        "host": settings.host,
        "port": settings.port,
        "dbname": settings.database,
        "user": settings.user,
        "password": settings.password.get_secret_value(),
        "connect_timeout": _CONNECTION_TIMEOUT,
        "options": _POOL_OPTIONS,
    }


async def _check_session(pool: AsyncConnectionPool) -> None:
    try:
        async with pool.connection(timeout=_POOL_TIMEOUT) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT current_setting('TimeZone'),
                               current_setting('statement_timeout')::interval = interval '10 seconds',
                               current_setting('lock_timeout')::interval = interval '5 seconds',
                               current_setting('transaction_isolation')
                        """
                    )
                    row = await cursor.fetchone()
                    if row != ("UTC", True, True, "read committed"):
                        _configuration_failure()
    except (ArtifactPersistenceConfigurationError, ArtifactPersistenceDependencyError):
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        _dependency_failure()


def _code_head() -> str:
    try:
        script = ScriptDirectory.from_config(Config(str(_ALEMBIC_CONFIG)))
        heads = script.get_heads()
    except Exception:
        _configuration_failure()
    if heads != [_EXPECTED_HEAD]:
        _configuration_failure()
    return heads[0]


async def _check_schema(pool: AsyncConnectionPool, head: str) -> None:
    try:
        async with pool.connection(timeout=_POOL_TIMEOUT) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "SELECT version_num FROM artifact_store.alembic_version ORDER BY version_num"
                    )
                    if await cursor.fetchall() != [(head,)]:
                        _configuration_failure()
                    await cursor.execute("SELECT to_regclass('public.alembic_version')")
                    if (await cursor.fetchone())[0] is not None:
                        _configuration_failure()
                    await cursor.execute(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'artifact_store'
                        ORDER BY table_name
                        """
                    )
                    business_tables = tuple(row[0] for row in await cursor.fetchall() if row[0] != "interaction_records")
                    if business_tables != _EXPECTED_TABLES:
                        _configuration_failure()
                    for table_name, expected in (
                        ("artifacts", _EXPECTED_ARTIFACT_COLUMNS),
                        ("capacity_state", _EXPECTED_CAPACITY_COLUMNS),
                    ):
                        await cursor.execute(
                            """
                            SELECT column_name, data_type, udt_name, is_nullable, column_default,
                                   character_maximum_length, is_identity, identity_generation,
                                   is_generated, generation_expression
                            FROM information_schema.columns
                            WHERE table_schema = 'artifact_store' AND table_name = %(table_name)s
                            ORDER BY ordinal_position
                            """,
                            {"table_name": table_name},
                        )
                        columns = tuple(await cursor.fetchall())
                        if columns != expected:
                            _configuration_failure()
                    for table_name, expected in (
                        ("artifacts", _EXPECTED_ARTIFACT_CONSTRAINTS),
                        ("capacity_state", _EXPECTED_CAPACITY_CONSTRAINTS),
                    ):
                        await cursor.execute(
                            """
                            SELECT conname, pg_get_constraintdef(oid)
                            FROM pg_constraint
                            WHERE connamespace = 'artifact_store'::regnamespace
                              AND conrelid = to_regclass(%(table_name)s)
                            ORDER BY conname
                            """,
                            {"table_name": f"artifact_store.{table_name}"},
                        )
                        constraints = {name: definition for name, definition in await cursor.fetchall()}
                        if constraints != expected:
                            _configuration_failure()
                    await cursor.execute(
                        """
                        SELECT sequence_name
                        FROM information_schema.sequences
                        WHERE sequence_schema = 'artifact_store'
                        ORDER BY sequence_name
                        """
                    )
                    if await cursor.fetchall():
                        _configuration_failure()
                    await cursor.execute(
                        "SELECT to_regclass('artifact_store.capacity_state_singleton_id_seq')"
                    )
                    if (await cursor.fetchone())[0] is not None:
                        _configuration_failure()
    except (ArtifactPersistenceConfigurationError, ArtifactPersistenceDependencyError):
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        if _is_dependency_database_error(error):
            _dependency_failure()
        _configuration_failure()


async def _initialize_capacity(
    pool: AsyncConnectionPool,
    limits: ArtifactRepositoryLimits,
) -> None:
    try:
        async with pool.connection(timeout=_POOL_TIMEOUT) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT singleton_id, max_artifact_bytes, max_artifacts,
                               max_total_content_bytes, artifact_count, total_content_bytes
                        FROM artifact_store.capacity_state
                        WHERE singleton_id = 1
                        """
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        await cursor.execute(
                            "SELECT EXISTS(SELECT 1 FROM artifact_store.artifacts LIMIT 1)"
                        )
                        if (await cursor.fetchone())[0]:
                            _configuration_failure()
                        await cursor.execute(
                            """
                            INSERT INTO artifact_store.capacity_state (
                                singleton_id, max_artifact_bytes, max_artifacts,
                                max_total_content_bytes, artifact_count, total_content_bytes
                            )
                            VALUES (1, %(max_artifact_bytes)s, %(max_artifacts)s,
                                    %(max_total_content_bytes)s, 0, 0)
                            ON CONFLICT (singleton_id) DO NOTHING
                            """,
                            limits.model_dump(mode="python"),
                        )
                        await cursor.execute(
                            """
                            SELECT singleton_id, max_artifact_bytes, max_artifacts,
                                   max_total_content_bytes, artifact_count, total_content_bytes
                            FROM artifact_store.capacity_state
                            WHERE singleton_id = 1
                            """
                        )
                        row = await cursor.fetchone()
                    if row is None or len(row) != 6 or any(type(value) is not int for value in row):
                        _configuration_failure()
                    singleton_id, max_artifact_bytes, max_artifacts, max_total_content_bytes, artifact_count, total_content_bytes = row
                    if (
                        singleton_id != 1
                        or max_artifact_bytes != limits.max_artifact_bytes
                        or max_artifacts != limits.max_artifacts
                        or max_total_content_bytes != limits.max_total_content_bytes
                        or not 0 <= artifact_count <= max_artifacts
                        or not 0 <= total_content_bytes <= max_total_content_bytes
                    ):
                        _configuration_failure()
    except (ArtifactPersistenceConfigurationError, ArtifactPersistenceDependencyError):
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        if _is_dependency_database_error(error):
            _dependency_failure()
        _configuration_failure()


class ArtifactPersistenceRuntime:
    """Own one PostgreSQL pool and the repository bound to its event loop."""

    __slots__ = ("_pool", "_repository", "_loop", "_closed", "_interaction_recorder")

    def __init__(
        self,
        *,
        pool: AsyncConnectionPool,
        repository: _PostgresArtifactRepository,
        loop: asyncio.AbstractEventLoop,
        interaction_recorder: InteractionRecorder | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._loop = loop
        self._closed = False
        self._interaction_recorder = interaction_recorder or _PostgresInteractionRecorder(pool=pool, limits=DEMO_INTERACTION_STORE_LIMITS, fallback_dir=None, available=False)

    @property
    def repository(self) -> ArtifactRepository:
        return self._repository

    @property
    def interaction_recorder(self) -> InteractionRecorder:
        return self._interaction_recorder

    def _ensure_lifecycle(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise ArtifactRepositoryUsageError() from None
        if loop is not self._loop or self._closed:
            raise ArtifactRepositoryUsageError() from None

    async def close(self) -> None:
        if self._closed:
            return
        self._ensure_lifecycle()
        self._closed = True
        self._repository._mark_closed()
        try:
            await self._pool.close(timeout=_CLOSE_TIMEOUT)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise ArtifactPersistenceDependencyError() from None

    async def __aenter__(self) -> "ArtifactPersistenceRuntime":
        self._ensure_lifecycle()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> bool:
        try:
            await self.close()
        except BaseException:
            if exc_value is not None:
                return False
            raise
        return False


async def _close_pool_after_failure(pool: AsyncConnectionPool) -> None:
    try:
        await pool.close(timeout=_CLOSE_TIMEOUT)
    except BaseException:
        pass


async def create_artifact_persistence_runtime(
    *,
    settings: ArtifactPersistenceSettings,
    registry: ArtifactSchemaRegistry,
    limits: ArtifactRepositoryLimits = DEMO_ARTIFACT_REPOSITORY_LIMITS,
    interaction_limits: InteractionStoreLimits = DEMO_INTERACTION_STORE_LIMITS,
    interaction_fallback_dir: Path | None = None,
) -> ArtifactPersistenceRuntime:
    """Create a fully checked runtime without changing database structure."""

    settings_copy = _copy_settings(settings)
    _validate_registry(registry)
    limits_copy = _copy_limits(limits)
    try:
        if type(interaction_limits) is not InteractionStoreLimits or (interaction_fallback_dir is not None and not isinstance(interaction_fallback_dir, Path)):
            _usage_failure()
        diagnostic_limits = InteractionStoreLimits.model_validate_json(interaction_limits.model_dump_json())
    except Exception:
        _usage_failure()
    pool: AsyncConnectionPool | None = None
    try:
        pool = AsyncConnectionPool(
            "",
            kwargs=_pool_kwargs(settings_copy),
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            open=False,
            timeout=_POOL_TIMEOUT,
        )
        await pool.open(wait=False, timeout=_OPEN_TIMEOUT)
        await pool.wait(timeout=_OPEN_TIMEOUT)
        await _check_session(pool)
        head = _code_head()
        await _check_schema(pool, head)
        await _initialize_capacity(pool, limits_copy)
        loop = asyncio.get_running_loop()
        repository = _PostgresArtifactRepository(
            pool=pool,
            registry=registry,
            limits=limits_copy,
            loop=loop,
        )
        recorder = _PostgresInteractionRecorder(pool=pool, limits=diagnostic_limits, fallback_dir=interaction_fallback_dir, available=await _diagnostic_schema_available(pool), secrets=(settings.password.get_secret_value().encode("utf8"), json.dumps(settings.password.get_secret_value(), ensure_ascii=True)[1:-1].encode("utf8")))
        return ArtifactPersistenceRuntime(pool=pool, repository=repository, loop=loop, interaction_recorder=recorder)
    except BaseException as error:
        if pool is not None:
            await _close_pool_after_failure(pool)
        if isinstance(
            error,
            (
                ArtifactPersistenceConfigurationError,
                ArtifactPersistenceDependencyError,
                ArtifactRepositoryUsageError,
            ),
        ):
            raise
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        raise ArtifactPersistenceDependencyError() from None
