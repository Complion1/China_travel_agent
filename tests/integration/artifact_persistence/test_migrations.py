"""AP-T02 真实 PostgreSQL 迁移结构与安全边界测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy
from alembic import command, op
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

from app.artifact_persistence.errors import (
    ArtifactPersistenceConfigurationError,
    ArtifactPersistenceDependencyError,
)


MIGRATION_CONFIG = Path("app/artifact_persistence/alembic.ini")


def _alembic_config() -> Config:
    return Config(str(MIGRATION_CONFIG))


def test_revision_is_in_artifact_store_and_public_has_no_version_table(
    db_connection,
) -> None:
    with db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'artifact_store'
            ORDER BY table_name
            """
        )
        assert [row[0] for row in cursor.fetchall()] == [
            "alembic_version",
            "artifacts",
            "capacity_state",
            "interaction_records",
        ]
        cursor.execute(
            """
            SELECT version_num
            FROM artifact_store.alembic_version
            ORDER BY version_num
            """
        )
        assert [row[0] for row in cursor.fetchall()] == ["m10_0002"]
        cursor.execute("SELECT to_regclass('public.alembic_version')")
        assert cursor.fetchone()[0] is None


def test_artifact_columns_and_constraints_match_the_frozen_schema(db_connection) -> None:
    with db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type, udt_name, is_nullable, column_default,
                   character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = 'artifact_store' AND table_name = 'artifacts'
            ORDER BY ordinal_position
            """
        )
        columns = cursor.fetchall()
        names = [row[0] for row in columns]
        assert names == [
            "artifact_id",
            "run_id",
            "phase",
            "logical_step",
            "artifact_type",
            "schema_version",
            "content_hash",
            "canonical_bytes",
            "size_bytes",
            "created_at",
        ]
        by_name = {row[0]: row[1:] for row in columns}
        assert by_name == {
            "artifact_id": ("uuid", "uuid", "NO", "gen_random_uuid()", None),
            "run_id": ("uuid", "uuid", "NO", None, None),
            "phase": ("text", "text", "NO", None, None),
            "logical_step": (
                "character varying",
                "varchar",
                "NO",
                None,
                64,
            ),
            "artifact_type": (
                "character varying",
                "varchar",
                "NO",
                None,
                65,
            ),
            "schema_version": (
                "character varying",
                "varchar",
                "NO",
                None,
                16,
            ),
            "content_hash": ("character", "bpchar", "NO", None, 71),
            "canonical_bytes": ("bytea", "bytea", "NO", None, None),
            "size_bytes": ("bigint", "int8", "NO", None, None),
            "created_at": (
                "timestamp with time zone",
                "timestamptz",
                "NO",
                "CURRENT_TIMESTAMP",
                None,
            ),
        }

        cursor.execute(
            """
            SELECT conname, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = 'artifact_store'::regnamespace
              AND conrelid = 'artifact_store.artifacts'::regclass
            ORDER BY conname
            """
        )
        constraints = {name: definition for name, definition in cursor.fetchall()}
        assert constraints == {
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


def test_capacity_columns_and_constraints_match_the_frozen_schema(db_connection) -> None:
    # Keep this structural assertion independent of pytest's file ordering.
    with db_connection.transaction():
        with db_connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE TABLE artifact_store.artifacts, artifact_store.capacity_state"
            )
    with db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type, udt_name, is_nullable, column_default,
                   character_maximum_length, is_identity, identity_generation,
                   is_generated, generation_expression
            FROM information_schema.columns
            WHERE table_schema = 'artifact_store' AND table_name = 'capacity_state'
            ORDER BY ordinal_position
            """
        )
        assert cursor.fetchall() == [
            (
                "singleton_id",
                "smallint",
                "int2",
                "NO",
                None,
                None,
                "NO",
                None,
                "NEVER",
                None,
            ),
            ("max_artifact_bytes", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
            ("max_artifacts", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
            ("max_total_content_bytes", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
            ("artifact_count", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
            ("total_content_bytes", "bigint", "int8", "NO", None, None, "NO", None, "NEVER", None),
        ]
        cursor.execute(
            """
            SELECT sequence_name
            FROM information_schema.sequences
            WHERE sequence_schema = 'artifact_store'
            ORDER BY sequence_name
            """
        )
        assert cursor.fetchall() == []
        cursor.execute("SELECT to_regclass('artifact_store.capacity_state_singleton_id_seq')")
        assert cursor.fetchone()[0] is None
        cursor.execute("SELECT COUNT(*) FROM artifact_store.capacity_state")
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            """
            SELECT conname, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = 'artifact_store'::regnamespace
              AND conrelid = 'artifact_store.capacity_state'::regclass
            ORDER BY conname
            """
        )
        constraints = {name: definition for name, definition in cursor.fetchall()}
        assert constraints == {
            "ck_capacity_state_artifact_count": "CHECK (((artifact_count >= 0) AND (artifact_count <= max_artifacts)))",
            "ck_capacity_state_max_artifact_bytes": "CHECK (((max_artifact_bytes >= 1) AND (max_artifact_bytes <= '9007199254740991'::bigint)))",
            "ck_capacity_state_max_artifacts": "CHECK (((max_artifacts >= 1) AND (max_artifacts <= '9007199254740991'::bigint)))",
            "ck_capacity_state_max_total_content_bytes": "CHECK (((max_total_content_bytes >= 1) AND (max_total_content_bytes <= '9007199254740991'::bigint)))",
            "ck_capacity_state_singleton": "CHECK ((singleton_id = 1))",
            "ck_capacity_state_total_at_least_single": "CHECK ((max_total_content_bytes >= max_artifact_bytes))",
            "ck_capacity_state_total_content_bytes": "CHECK (((total_content_bytes >= 0) AND (total_content_bytes <= max_total_content_bytes)))",
            "pk_capacity_state": "PRIMARY KEY (singleton_id)",
        }


def test_repeated_upgrade_is_idempotent(migrated_artifact_database) -> None:
    command.upgrade(_alembic_config(), "head")


def test_downgrade_is_rejected_without_changing_the_schema(
    db_connection,
    migrated_artifact_database,
) -> None:
    with pytest.raises(ArtifactPersistenceConfigurationError):
        command.downgrade(_alembic_config(), "-1")
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM artifact_store.alembic_version")
        assert cursor.fetchone() == ("m10_0002",)
        cursor.execute(
            "SELECT to_regclass('artifact_store.artifacts'), "
            "to_regclass('artifact_store.capacity_state')"
        )
        assert cursor.fetchone() == (
            "artifact_store.artifacts",
            "artifact_store.capacity_state",
        )


def test_failed_first_revision_rolls_back_schema_version_and_business_tables(
    db_connection,
    migrated_artifact_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db_connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS artifact_store CASCADE")
    db_connection.commit()

    original_create_table = op.create_table
    failure = {"artifacts_created": False}

    def fail_after_artifacts_create(table_name, *args, **kwargs):
        result = original_create_table(table_name, *args, **kwargs)
        if table_name == "artifacts":
            failure["artifacts_created"] = True
            raise RuntimeError("intentional isolated migration failure")
        return result

    monkeypatch.setattr(op, "create_table", fail_after_artifacts_create)
    try:
        with pytest.raises(ArtifactPersistenceDependencyError):
            command.upgrade(_alembic_config(), "head")
        assert failure["artifacts_created"] is True
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT to_regnamespace('artifact_store')")
            assert cursor.fetchone()[0] is None
            cursor.execute("SELECT to_regclass('artifact_store.alembic_version')")
            assert cursor.fetchone()[0] is None
            cursor.execute("SELECT to_regclass('artifact_store.artifacts')")
            assert cursor.fetchone()[0] is None
            cursor.execute("SELECT to_regclass('artifact_store.capacity_state')")
            assert cursor.fetchone()[0] is None
    finally:
        monkeypatch.undo()
        command.upgrade(_alembic_config(), "head")
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM artifact_store.alembic_version")
        assert cursor.fetchall() == [("m10_0002",)]


class _DisposeFailingEngine:
    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.dispose_calls = 0
        self.real_dispose_called = False

    def connect(self):
        return self._delegate.connect()

    def dispose(self) -> None:
        self.dispose_calls += 1
        self._delegate.dispose()
        self.real_dispose_called = True
        raise RuntimeError("synthetic dispose failure")


@pytest.mark.parametrize(
    "main_exception",
    [
        None,
        ArtifactPersistenceConfigurationError,
        ArtifactPersistenceDependencyError,
        KeyboardInterrupt,
        SystemExit,
    ],
)
def test_engine_dispose_failure_preserves_main_exception_or_maps_success(
    monkeypatch: pytest.MonkeyPatch,
    main_exception,
) -> None:
    original_create_engine = sqlalchemy.create_engine
    holder: dict[str, _DisposeFailingEngine] = {}

    def create_engine_with_failing_dispose(*args, **kwargs):
        delegate = original_create_engine(*args, **kwargs)
        engine = _DisposeFailingEngine(delegate)
        holder["engine"] = engine
        return engine

    monkeypatch.setattr(sqlalchemy, "create_engine", create_engine_with_failing_dispose)
    if main_exception is not None:
        def raise_main(_context, *args, **kwargs):
            raise main_exception()

        monkeypatch.setattr(
            MigrationContext,
            "run_migrations",
            raise_main,
        )

    if main_exception is None:
        with pytest.raises(ArtifactPersistenceDependencyError) as error:
            command.upgrade(_alembic_config(), "head")
        assert error.value.__cause__ is None
    else:
        with pytest.raises(main_exception):
            command.upgrade(_alembic_config(), "head")

    assert holder["engine"].dispose_calls == 1
    assert holder["engine"].real_dispose_called is True


def test_engine_creation_failure_does_not_dispose_missing_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_calls = 0

    def fail_create_engine(*args, **kwargs):
        nonlocal create_calls
        create_calls += 1
        raise RuntimeError("synthetic engine creation failure")

    monkeypatch.setattr(sqlalchemy, "create_engine", fail_create_engine)
    with pytest.raises(ArtifactPersistenceDependencyError) as error:
        command.upgrade(_alembic_config(), "head")
    assert error.value.__cause__ is None
    assert create_calls == 1


def test_alembic_ini_has_no_connection_url_or_credentials() -> None:
    content = MIGRATION_CONFIG.read_text(encoding="utf-8")
    lowered = content.lower()
    assert "sqlalchemy.url" not in lowered
    assert "postgresql://" not in lowered
    assert "password" not in lowered
    assert "username" not in lowered


def test_offline_migration_mode_is_rejected() -> None:
    with pytest.raises(ArtifactPersistenceConfigurationError):
        command.upgrade(_alembic_config(), "head", sql=True)


def test_online_env_uses_the_shared_settings_loader_and_rejects_offline_mode() -> None:
    source = Path("app/artifact_persistence/migrations/env.py").read_text(encoding="utf-8")
    assert "load_artifact_persistence_settings" in source
    assert "URL.create" in source
    assert "version_table_schema=SCHEMA_NAME" in source
    assert "CREATE SCHEMA IF NOT EXISTS artifact_store" in source
    assert "transactional_ddl=True" in source
    assert "is_offline_mode" in source
