from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import psycopg
from psycopg.errors import CheckViolation
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.durable_execution.errors import DurableExecutionConfigurationError
from app.durable_execution.repository import (
    EXPECTED_BUSINESS_TABLES,
    EXPECTED_REVISION,
    EXPECTED_SAVER_MIGRATION_VERSIONS,
    EXPECTED_SAVER_TABLES,
    EXPECTED_TABLES,
    check_durable_execution_schema,
)


@pytest.fixture(name="durable_execution_settings")
def migration_settings_for_this_file(migration_settings):
    """Keep every migration subprocess and lock worker on the structure test DB."""
    return migration_settings


@pytest.fixture(name="db_connection")
def migration_connection_for_this_file(migration_db_connection):
    """Keep structure DDL and rollback checks off the business database."""
    return migration_db_connection


async def _fetchall(
    connection,
    query: str,
    params: tuple[object, ...] = (),
) -> list[tuple[object, ...]]:
    async with connection.cursor() as cursor:
        await cursor.execute(query, params)
        return [tuple(row) for row in await cursor.fetchall()]


def _module_environment(settings, database: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_HOST": settings.host,
            "POSTGRES_PORT": str(settings.port),
            "POSTGRES_DB": database,
            "POSTGRES_USER": settings.user,
            "POSTGRES_PASSWORD": settings.password.get_secret_value(),
        }
    )
    return environment


def _database_name(name_key: str, prefix: str) -> str:
    database = os.environ.get(name_key, "")
    if type(database) is not str or not database or prefix not in database:
        pytest.fail("missing isolated database identity")
    return database


def _run_migration(settings, database: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.durable_execution.migrate"],
        cwd=Path(__file__).parents[3],
        env=_module_environment(settings, database),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


async def _schema_fingerprint(connection, schema: str) -> str:
    tables = await _fetchall(
        connection,
        """
        SELECT c.relname, c.relkind
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
        """,
        (schema,),
    )
    payload: dict[str, object] = {"schema": schema, "tables": []}
    for table, relkind in tables:
        columns = await _fetchall(
            connection,
            """
            SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod),
                   a.attnotnull, pg_catalog.pg_get_expr(d.adbin, d.adrelid)
            FROM pg_catalog.pg_attribute AS a
            JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_attrdef AS d
              ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE n.nspname = %s AND c.relname = %s
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            (schema, table),
        )
        constraints = await _fetchall(
            connection,
            """
            SELECT con.conname, con.contype, pg_catalog.pg_get_constraintdef(con.oid)
            FROM pg_catalog.pg_constraint AS con
            JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
            ORDER BY con.conname
            """,
            (schema, table),
        )
        indexes = await _fetchall(
            connection,
            """
            SELECT indexname, indexdef FROM pg_catalog.pg_indexes
            WHERE schemaname = %s AND tablename = %s
            ORDER BY indexname
            """,
            (schema, table),
        )
        cast_table = {
            "name": table,
            "kind": relkind,
            "columns": columns,
            "constraints": constraints,
            "indexes": indexes,
        }
        cast_payload = payload["tables"]
        assert isinstance(cast_payload, list)
        cast_payload.append(cast_table)
    payload["versions"] = await _fetchall(
        connection,
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name IN ('alembic_version', 'checkpoint_migrations')
        ORDER BY table_name, column_name
        """,
        (schema,),
    )
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _relation_identities(
    connection,
    schema: str,
    relations: tuple[str, ...],
) -> dict[str, int]:
    identities: dict[str, int] = {}
    for relation in relations:
        rows = await _fetchall(
            connection,
            """
            SELECT c.oid
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p')
            """,
            (schema, relation),
        )
        assert len(rows) == 1
        identities[relation] = int(rows[0][0])
    return identities


async def _relation_structure_fingerprint(
    connection,
    schema: str,
    relations: tuple[str, ...],
) -> str:
    payload: list[dict[str, object]] = []
    for relation in relations:
        columns = await _fetchall(
            connection,
            """
            SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod),
                   a.attnotnull, pg_catalog.pg_get_expr(d.adbin, d.adrelid)
            FROM pg_catalog.pg_attribute AS a
            JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_attrdef AS d
              ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE n.nspname = %s AND c.relname = %s
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            (schema, relation),
        )
        constraints = await _fetchall(
            connection,
            """
            SELECT con.conname, con.contype, pg_catalog.pg_get_constraintdef(con.oid)
            FROM pg_catalog.pg_constraint AS con
            JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
            ORDER BY con.conname
            """,
            (schema, relation),
        )
        indexes = await _fetchall(
            connection,
            """
            SELECT indexname, indexdef
            FROM pg_catalog.pg_indexes
            WHERE schemaname = %s AND tablename = %s
            ORDER BY indexname
            """,
            (schema, relation),
        )
        payload.append(
            {
                "relation": relation,
                "columns": columns,
                "constraints": constraints,
                "indexes": indexes,
            }
        )
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _non_system_object_fingerprint(connection) -> str:
    schemas = await _fetchall(
        connection,
        """
        SELECT nspname
        FROM pg_catalog.pg_namespace
        WHERE nspname <> 'information_schema' AND nspname NOT LIKE 'pg_%%'
        ORDER BY nspname
        """,
    )
    objects = await _fetchall(
        connection,
        """
        SELECT n.nspname, c.relname, c.relkind
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg_%%'
        ORDER BY n.nspname, c.relname, c.relkind
        """,
    )
    payload = {"schemas": schemas, "objects": objects}
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _migration_lock_script(*, domain: str, payload: bytes) -> str:
    return f"""
import os
import sys
import psycopg
from app.durable_execution.repository import migration_advisory_key

lock_key = migration_advisory_key(domain={domain!r}, payload={payload!r})

conn = psycopg.connect(
    host=os.environ['POSTGRES_HOST'], port=int(os.environ['POSTGRES_PORT']),
    dbname=os.environ['POSTGRES_DB'], user=os.environ['POSTGRES_USER'],
    password=os.environ['POSTGRES_PASSWORD'], autocommit=True,
)
print('CONNECTED', flush=True)
print(f'KEY {{lock_key}}', flush=True)
with conn.cursor() as cur:
    cur.execute('SELECT pg_advisory_lock(%s)', (lock_key,))
    print('ACQUIRED', flush=True)
    sys.stdin.readline()
conn.close()
"""


async def _assert_drift_rejected_and_rolled_back(connection, statement: str) -> None:
    await connection.execute(statement, prepare=False)
    with pytest.raises(DurableExecutionConfigurationError):
        await check_durable_execution_schema(connection)
    await connection.rollback()
    await check_durable_execution_schema(connection)
    await connection.rollback()


async def test_t02_empty_shape_revision_capacity_and_saver_are_exact(db_connection) -> None:
    tables = await _fetchall(
        db_connection,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'durable_execution' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """,
    )
    assert tuple(row[0] for row in tables) == tuple(sorted(EXPECTED_TABLES))
    assert await _fetchall(
        db_connection,
        "SELECT version_num FROM durable_execution.alembic_version ORDER BY version_num",
    ) == [(EXPECTED_REVISION,)]
    assert await _fetchall(
        db_connection,
        "SELECT v FROM durable_execution.checkpoint_migrations ORDER BY v",
    ) == [(version,) for version in EXPECTED_SAVER_MIGRATION_VERSIONS]
    assert await _fetchall(db_connection, "SELECT count(*) FROM durable_execution.capacity_state") == [(0,)]
    assert await _fetchall(db_connection, "SELECT to_regclass('public.alembic_version')") == [(None,)]
    assert await _fetchall(db_connection, "SELECT to_regclass('artifact_store.alembic_version')") == [("artifact_store.alembic_version",)]
    await check_durable_execution_schema(db_connection)
    await db_connection.rollback()


async def test_t02_reentry_and_scope_are_isolated(
    db_connection,
    durable_execution_settings,
) -> None:
    result = _run_migration(durable_execution_settings, durable_execution_settings.database)
    assert result.returncode == 0
    schemas = await _fetchall(
        db_connection,
        """
        SELECT DISTINCT table_schema
        FROM information_schema.tables
        WHERE table_name IN (
            'capacity_state', 'runs', 'run_leases', 'budget_limits',
            'budget_operations', 'operation_journal', 'pending_commands',
            'checkpoint_migrations', 'checkpoints', 'checkpoint_blobs',
            'checkpoint_writes'
        )
        ORDER BY table_schema
        """,
    )
    assert schemas == [("artifact_store",), ("durable_execution",)]
    assert await _fetchall(
        db_connection,
        "SELECT version_num FROM artifact_store.alembic_version ORDER BY version_num",
    ) == [("m10_0001",)]
    assert await _fetchall(
        db_connection,
        """
        SELECT count(*)
        FROM pg_constraint AS c
        JOIN pg_namespace AS n ON n.oid = c.connamespace
        JOIN pg_class AS target_table ON target_table.oid = c.confrelid
        JOIN pg_namespace AS target_schema ON target_schema.oid = target_table.relnamespace
        WHERE n.nspname = 'durable_execution'
          AND c.contype = 'f'
          AND target_schema.nspname <> 'durable_execution'
        """,
    ) == [(0,)]
    await check_durable_execution_schema(db_connection)
    await db_connection.rollback()


async def test_t02_scope_preserves_module10_artifact_store(
    scope_db_connection,
    durable_execution_settings,
) -> None:
    before = await _schema_fingerprint(scope_db_connection, "artifact_store")
    await scope_db_connection.rollback()
    database = _database_name(
        "MODULE11_TEST_SCOPE_DB",
        os.environ["MODULE11_TEST_PREFIX"],
    )
    result = _run_migration(durable_execution_settings, database)
    assert result.returncode == 0
    after = await _schema_fingerprint(scope_db_connection, "artifact_store")
    assert after == before
    assert await _fetchall(
        scope_db_connection,
        "SELECT version_num FROM artifact_store.alembic_version ORDER BY version_num",
    ) == [("m10_0001",)]
    await scope_db_connection.rollback()


async def test_t02_noauto_import_does_not_create_database_objects(
    noauto_db_connection,
    durable_execution_settings,
) -> None:
    before = await _non_system_object_fingerprint(noauto_db_connection)
    database = _database_name(
        "MODULE11_TEST_NOAUTO_DB",
        os.environ["MODULE11_TEST_PREFIX"],
    )
    result = subprocess.run(
        [sys.executable, "-c", "import app.durable_execution"],
        cwd=Path(__file__).parents[3],
        env=_module_environment(durable_execution_settings, database),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    after_import = await _non_system_object_fingerprint(noauto_db_connection)
    assert after_import == before
    with pytest.raises(DurableExecutionConfigurationError) as error:
        await check_durable_execution_schema(noauto_db_connection)
    assert error.value.args == ()
    assert await _fetchall(
        noauto_db_connection,
        "SELECT to_regclass('durable_execution.runs'), to_regclass('durable_execution.checkpoint_migrations')",
    ) == [(None, None)]
    assert await _non_system_object_fingerprint(noauto_db_connection) == before
    await noauto_db_connection.rollback()


async def test_t02_failed_migration_leaves_no_partial_module11_schema(
    fail_db_connection,
    durable_execution_settings,
) -> None:
    await fail_db_connection.execute("DROP SCHEMA IF EXISTS durable_execution CASCADE")
    await fail_db_connection.execute("CREATE SCHEMA durable_execution")
    await fail_db_connection.execute(
        "CREATE TABLE durable_execution.runs (marker integer NOT NULL)"
    )
    await fail_db_connection.commit()
    database = _database_name(
        "MODULE11_TEST_FAIL_DB",
        os.environ["MODULE11_TEST_PREFIX"],
    )
    result = _run_migration(durable_execution_settings, database)
    assert result.returncode != 0
    assert await _fetchall(
        fail_db_connection,
        "SELECT to_regclass('durable_execution.runs'), to_regclass('durable_execution.capacity_state')",
    ) == [("runs", None)]
    await fail_db_connection.execute("DROP SCHEMA durable_execution CASCADE")
    await fail_db_connection.commit()


async def test_t02_saver_setup_failure_after_alembic_is_reentrant(
    fail_db_connection,
    durable_execution_settings,
) -> None:
    await fail_db_connection.execute("DROP SCHEMA IF EXISTS durable_execution CASCADE")
    await fail_db_connection.execute("CREATE SCHEMA durable_execution")
    await fail_db_connection.execute(
        "CREATE TABLE durable_execution.checkpoint_migrations (v boolean PRIMARY KEY)"
    )
    await fail_db_connection.commit()
    database = _database_name(
        "MODULE11_TEST_FAIL_DB",
        os.environ["MODULE11_TEST_PREFIX"],
    )

    failed = _run_migration(durable_execution_settings, database)
    assert failed.returncode != 0
    assert await _fetchall(
        fail_db_connection,
        "SELECT to_regclass('durable_execution.runs'), "
        "to_regclass('durable_execution.alembic_version'), "
        "to_regclass('durable_execution.checkpoint_migrations')",
    ) == [
        ("runs", "alembic_version", "checkpoint_migrations")
    ]
    assert await _fetchall(
        fail_db_connection,
        "SELECT version_num FROM durable_execution.alembic_version",
    ) == [(EXPECTED_REVISION,)]
    stable_relations = ("alembic_version",) + EXPECTED_BUSINESS_TABLES
    failed_identities = await _relation_identities(
        fail_db_connection,
        "durable_execution",
        stable_relations,
    )
    failed_structure = await _relation_structure_fingerprint(
        fail_db_connection,
        "durable_execution",
        stable_relations,
    )
    assert tuple(
        row[0]
        for row in await _fetchall(
            fail_db_connection,
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'durable_execution'
              AND table_name = ANY(%s)
            ORDER BY table_name
            """,
            (list(EXPECTED_BUSINESS_TABLES),),
        )
    ) == tuple(sorted(EXPECTED_BUSINESS_TABLES))
    assert await _fetchall(
        fail_db_connection,
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = 'durable_execution'
          AND table_name = 'checkpoint_migrations'
          AND column_name = 'v'
        """,
    ) == [("boolean",)]
    with pytest.raises(DurableExecutionConfigurationError):
        await check_durable_execution_schema(fail_db_connection)
    await fail_db_connection.rollback()

    await fail_db_connection.execute(
        "DROP TABLE durable_execution.checkpoint_migrations"
    )
    await fail_db_connection.commit()
    recovered = _run_migration(durable_execution_settings, database)
    assert recovered.returncode == 0
    assert await _relation_identities(
        fail_db_connection,
        "durable_execution",
        stable_relations,
    ) == failed_identities
    assert await _relation_structure_fingerprint(
        fail_db_connection,
        "durable_execution",
        stable_relations,
    ) == failed_structure
    assert tuple(
        row[0]
        for row in await _fetchall(
            fail_db_connection,
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'durable_execution'
            ORDER BY table_name
            """,
        )
    ) == tuple(sorted(EXPECTED_TABLES))
    assert await _fetchall(
        fail_db_connection,
        "SELECT v FROM durable_execution.checkpoint_migrations ORDER BY v",
    ) == [(version,) for version in EXPECTED_SAVER_MIGRATION_VERSIONS]
    assert tuple(EXPECTED_SAVER_TABLES) == (
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    )
    await check_durable_execution_schema(fail_db_connection)
    await fail_db_connection.rollback()
    reentered = _run_migration(durable_execution_settings, database)
    assert reentered.returncode == 0
    assert await _relation_identities(
        fail_db_connection,
        "durable_execution",
        stable_relations,
    ) == failed_identities
    assert await _fetchall(
        fail_db_connection,
        "SELECT version_num FROM durable_execution.alembic_version",
    ) == [(EXPECTED_REVISION,)]
    await check_durable_execution_schema(fail_db_connection)
    await fail_db_connection.rollback()


async def test_t02_migration_lock_is_observable_without_sleep(
    durable_execution_settings,
) -> None:
    environment = _module_environment(
        durable_execution_settings,
        durable_execution_settings.database,
    )
    code = _migration_lock_script(domain="migration", payload=b"")
    holder = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[3],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    blocked = None
    try:
        assert holder.stdout is not None
        assert holder.stdin is not None
        assert holder.stdout.readline().strip() == "CONNECTED"
        holder_key = holder.stdout.readline().strip()
        assert holder_key.startswith("KEY ")
        assert holder.stdout.readline().strip() == "ACQUIRED"
        blocked = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=Path(__file__).parents[3],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert blocked.stdout is not None
        assert blocked.stdin is not None
        assert blocked.stdout.readline().strip() == "CONNECTED"
        assert blocked.stdout.readline().strip() == holder_key
        holder.stdin.write("release\n")
        holder.stdin.flush()
        assert blocked.stdout.readline().strip() == "ACQUIRED"
        blocked.stdin.write("release\n")
        blocked.stdin.flush()
        assert holder.wait(timeout=10) == 0
        assert blocked.wait(timeout=10) == 0
    finally:
        for process in (blocked, holder):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)


async def test_t02_advisory_key_workers_import_and_compute_each_domain(
    durable_execution_settings,
) -> None:
    environment = _module_environment(
        durable_execution_settings,
        durable_execution_settings.database,
    )
    cases = (
        ("migration", b""),
        ("capacity", b""),
        ("run", b"33333333-3333-4333-8333-333333333333"),
    )
    observed: dict[str, str] = {}
    for domain, payload in cases:
        holder = subprocess.Popen(
            [sys.executable, "-c", _migration_lock_script(domain=domain, payload=payload)],
            cwd=Path(__file__).parents[3],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        contender = None
        try:
            assert holder.stdout is not None
            assert holder.stdin is not None
            assert holder.stdout.readline().strip() == "CONNECTED"
            holder_key = holder.stdout.readline().strip()
            assert holder_key.startswith("KEY ")
            assert holder.stdout.readline().strip() == "ACQUIRED"
            contender = subprocess.Popen(
                [sys.executable, "-c", _migration_lock_script(domain=domain, payload=payload)],
                cwd=Path(__file__).parents[3],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert contender.stdout is not None
            assert contender.stdin is not None
            assert contender.stdout.readline().strip() == "CONNECTED"
            assert contender.stdout.readline().strip() == holder_key
            holder.stdin.write("release\n")
            holder.stdin.flush()
            assert contender.stdout.readline().strip() == "ACQUIRED"
            contender.stdin.write("release\n")
            contender.stdin.flush()
            assert holder.wait(timeout=10) == 0
            assert contender.wait(timeout=10) == 0
            observed[domain] = holder_key
        finally:
            for process in (contender, holder):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
    assert len(set(observed.values())) == len(cases)


async def test_t02_migration_lock_fails_at_five_second_boundary(
    db_connection,
    durable_execution_settings,
) -> None:
    environment = _module_environment(
        durable_execution_settings,
        durable_execution_settings.database,
    )
    holder_code = _migration_lock_script(domain="migration", payload=b"")
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        cwd=Path(__file__).parents[3],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    migration = None
    try:
        assert holder.stdout is not None
        assert holder.stdin is not None
        assert holder.stdout.readline().strip() == "CONNECTED"
        assert holder.stdout.readline().startswith("KEY ")
        assert holder.stdout.readline().strip() == "ACQUIRED"

        started = time.monotonic()
        migration = subprocess.Popen(
            [sys.executable, "-m", "app.durable_execution.migrate"],
            cwd=Path(__file__).parents[3],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = migration.communicate(timeout=15)
        elapsed = time.monotonic() - started
        assert migration.returncode != 0
        assert 4.5 <= elapsed < 15
        assert "POSTGRES_PASSWORD" not in stdout + stderr
        assert "postgresql://" not in stdout + stderr

        holder.stdin.write("release\n")
        holder.stdin.flush()
        assert holder.wait(timeout=10) == 0
        result = _run_migration(
            durable_execution_settings,
            durable_execution_settings.database,
        )
        assert result.returncode == 0
        await check_durable_execution_schema(db_connection)
        await db_connection.rollback()
    finally:
        if migration is not None and migration.poll() is None:
            migration.kill()
            migration.wait(timeout=10)
        if holder.poll() is None:
            assert holder.stdin is not None
            holder.stdin.write("release\n")
            holder.stdin.flush()
        if holder.poll() is None:
            holder.wait(timeout=10)


async def test_t02_security_failure_does_not_echo_secret_or_dsn(
    durable_execution_settings,
) -> None:
    canary = "m11-r02-secret-canary"
    sql_canary = "m11-r03-sql-parameter-canary"
    environment = _module_environment(
        durable_execution_settings,
        durable_execution_settings.database,
    )
    environment["POSTGRES_PASSWORD"] = canary
    environment["POSTGRES_DB"] = sql_canary
    environment["POSTGRES_USER"] = sql_canary
    environment["POSTGRES_HOST"] = sql_canary
    environment["POSTGRES_PORT"] = "not-a-port"
    from app.durable_execution.settings import load_durable_execution_settings

    with pytest.raises(DurableExecutionConfigurationError) as configuration_error:
        load_durable_execution_settings(environ=environment)
    assert canary not in str(configuration_error.value)
    assert sql_canary not in str(configuration_error.value)
    result = subprocess.run(
        [sys.executable, "-m", "app.durable_execution.migrate"],
        cwd=Path(__file__).parents[3],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert canary not in combined
    assert sql_canary not in combined
    assert "postgresql://" not in combined
    assert "POSTGRES_PASSWORD" not in combined


_DRIFT_CASES = [
    *[
        (
            f"extra_column_{table}",
            f"ALTER TABLE durable_execution.{table} ADD COLUMN drift_extra integer",
        )
        for table in (
            "capacity_state", "runs", "run_leases", "budget_limits",
            "budget_operations", "operation_journal", "pending_commands",
            "alembic_version", "checkpoint_migrations", "checkpoints",
            "checkpoint_blobs", "checkpoint_writes",
        )
    ],
    *[
        (f"type_{table}_{column}", statement)
        for table, column, statement in (
            ("capacity_state", "max_active_runs", "ALTER TABLE durable_execution.capacity_state ALTER COLUMN max_active_runs TYPE bigint"),
            ("runs", "contract_size_bytes", "ALTER TABLE durable_execution.runs ALTER COLUMN contract_size_bytes TYPE bigint"),
            ("run_leases", "fencing_token", "ALTER TABLE durable_execution.run_leases ALTER COLUMN fencing_token TYPE integer"),
            ("budget_limits", "limit_value", "ALTER TABLE durable_execution.budget_limits ALTER COLUMN limit_value TYPE integer"),
            ("budget_operations", "amount", "ALTER TABLE durable_execution.budget_operations ALTER COLUMN amount TYPE integer"),
            ("operation_journal", "artifact_size_bytes", "ALTER TABLE durable_execution.operation_journal ALTER COLUMN artifact_size_bytes TYPE integer"),
            ("pending_commands", "command_size_bytes", "ALTER TABLE durable_execution.pending_commands ALTER COLUMN command_size_bytes TYPE bigint"),
            ("alembic_version", "version_num", "ALTER TABLE durable_execution.alembic_version ALTER COLUMN version_num TYPE text"),
            ("checkpoint_migrations", "v", "ALTER TABLE durable_execution.checkpoint_migrations ALTER COLUMN v TYPE bigint"),
            ("checkpoints", "checkpoint", "ALTER TABLE durable_execution.checkpoints ALTER COLUMN checkpoint TYPE json USING checkpoint::json"),
            ("checkpoint_blobs", "blob", "ALTER TABLE durable_execution.checkpoint_blobs ALTER COLUMN blob TYPE text USING convert_from(blob, 'UTF8')"),
            ("checkpoint_writes", "blob", "ALTER TABLE durable_execution.checkpoint_writes ALTER COLUMN blob TYPE text USING convert_from(blob, 'UTF8')"),
        )
    ],
    *[
        (
            f"nullability_{table}_{column}",
            f"ALTER TABLE durable_execution.{table} ALTER COLUMN {column} DROP NOT NULL",
        )
        for table, column in (
            ("capacity_state", "max_active_runs"), ("runs", "trace_id"),
            ("run_leases", "fencing_token"), ("budget_limits", "limit_value"),
            ("budget_operations", "amount"), ("operation_journal", "node_name"),
            ("pending_commands", "status"), ("checkpoints", "checkpoint"),
            ("checkpoint_blobs", "type"), ("checkpoint_writes", "channel"),
        )
    ],
    *[
        (f"default_{table}_{column}", statement)
        for table, column, statement in (
            ("capacity_state", "max_active_runs", "ALTER TABLE durable_execution.capacity_state ALTER COLUMN max_active_runs SET DEFAULT 4095"),
            ("runs", "pause_generation", "ALTER TABLE durable_execution.runs ALTER COLUMN pause_generation SET DEFAULT 1"),
            ("run_leases", "fencing_token", "ALTER TABLE durable_execution.run_leases ALTER COLUMN fencing_token SET DEFAULT 1"),
            ("budget_limits", "consumed_value", "ALTER TABLE durable_execution.budget_limits ALTER COLUMN consumed_value SET DEFAULT 1"),
            ("budget_operations", "amount", "ALTER TABLE durable_execution.budget_operations ALTER COLUMN amount SET DEFAULT 1"),
            ("operation_journal", "created_at", "ALTER TABLE durable_execution.operation_journal ALTER COLUMN created_at SET DEFAULT (clock_timestamp() + interval '1 second')"),
            ("pending_commands", "created_at", "ALTER TABLE durable_execution.pending_commands ALTER COLUMN created_at SET DEFAULT (clock_timestamp() + interval '1 second')"),
            ("alembic_version", "version_num", "ALTER TABLE durable_execution.alembic_version ALTER COLUMN version_num SET DEFAULT 'drift'"),
            ("checkpoint_migrations", "v", "ALTER TABLE durable_execution.checkpoint_migrations ALTER COLUMN v SET DEFAULT 99"),
            ("checkpoints", "metadata", "ALTER TABLE durable_execution.checkpoints ALTER COLUMN metadata SET DEFAULT '{\"drift\":true}'::jsonb"),
            ("checkpoint_blobs", "checkpoint_ns", "ALTER TABLE durable_execution.checkpoint_blobs ALTER COLUMN checkpoint_ns SET DEFAULT 'drift'"),
            ("checkpoint_writes", "task_path", "ALTER TABLE durable_execution.checkpoint_writes ALTER COLUMN task_path SET DEFAULT 'drift'"),
        )
    ],
    *[
        (
            f"check_{table}_{constraint}",
            f"ALTER TABLE durable_execution.{table} DROP CONSTRAINT {constraint}",
        )
        for table, constraint in (
            ("capacity_state", "ck_capacity_state_max_active_runs"),
            ("runs", "ck_runs_contract_version"),
            ("run_leases", "ck_run_leases_fencing_token"),
            ("budget_limits", "ck_budget_limits_limit"),
            ("budget_operations", "ck_budget_operations_amount"),
            ("operation_journal", "ck_operation_journal_state"),
            ("pending_commands", "ck_pending_commands_status"),
        )
    ],
    *[
        (
            f"check_definition_{table}_{constraint}",
            f"ALTER TABLE durable_execution.{table} DROP CONSTRAINT {constraint}; ALTER TABLE durable_execution.{table} ADD CONSTRAINT {constraint} CHECK (1 = 0)",
        )
        for table, constraint in (
            ("capacity_state", "ck_capacity_state_max_active_runs"),
            ("runs", "ck_runs_contract_version"),
            ("run_leases", "ck_run_leases_fencing_token"),
            ("budget_limits", "ck_budget_limits_limit"),
            ("budget_operations", "ck_budget_operations_amount"),
            ("operation_journal", "ck_operation_journal_state"),
            ("pending_commands", "ck_pending_commands_status"),
        )
    ],
    (
        "unique_missing",
        "ALTER TABLE durable_execution.operation_journal "
        "DROP CONSTRAINT uq_operation_journal_state",
    ),
    (
        "unique_same_name_wrong_columns",
        "ALTER TABLE durable_execution.operation_journal "
        "DROP CONSTRAINT uq_operation_journal_state; "
        "ALTER TABLE durable_execution.operation_journal "
        "ADD CONSTRAINT uq_operation_journal_state UNIQUE "
        "(run_id, operation_phase, operation_logical_step, node_name)",
    ),
    *[
        (
            f"primary_key_{constraint}",
            f"ALTER TABLE durable_execution.{table} DROP CONSTRAINT {constraint}",
        )
        for table, constraint in (
            ("capacity_state", "pk_capacity_state"), ("runs", "pk_runs CASCADE"),
            ("run_leases", "pk_run_leases"), ("budget_limits", "pk_budget_limits CASCADE"),
            ("budget_operations", "pk_budget_operations"),
            ("operation_journal", "pk_operation_journal"),
            ("pending_commands", "pk_pending_commands"),
            ("alembic_version", "alembic_version_pkc"),
            ("checkpoint_migrations", "checkpoint_migrations_pkey"),
            ("checkpoints", "checkpoints_pkey"),
            ("checkpoint_blobs", "checkpoint_blobs_pkey"),
            ("checkpoint_writes", "checkpoint_writes_pkey"),
        )
    ],
    (
        "primary_key_same_name_wrong_columns",
        "ALTER TABLE durable_execution.operation_journal "
        "DROP CONSTRAINT pk_operation_journal; "
        "ALTER TABLE durable_execution.operation_journal "
        "ADD CONSTRAINT pk_operation_journal PRIMARY KEY "
        "(run_id, operation_phase, operation_logical_step, node_name)",
    ),
    *[
        (
            f"foreign_key_{constraint}",
            f"ALTER TABLE durable_execution.{table} DROP CONSTRAINT {constraint}",
        )
        for table, constraint in (
            ("run_leases", "fk_run_leases_run"),
            ("budget_limits", "fk_budget_limits_run"),
            ("budget_operations", "fk_budget_operations_run"),
            ("budget_operations", "fk_budget_operations_limit"),
            ("operation_journal", "fk_operation_journal_run"),
            ("pending_commands", "fk_pending_commands_run"),
        )
    ],
    (
        "foreign_key_same_name_wrong_delete_action",
        "ALTER TABLE durable_execution.run_leases "
        "DROP CONSTRAINT fk_run_leases_run; "
        "ALTER TABLE durable_execution.run_leases "
        "ADD CONSTRAINT fk_run_leases_run FOREIGN KEY (run_id) "
        "REFERENCES durable_execution.runs(run_id) ON DELETE CASCADE",
    ),
    *[
        (f"index_{index}", f"DROP INDEX durable_execution.{index}")
        for index in (
            "ix_runs_lifecycle", "ix_run_leases_expires_at",
            "ix_operation_journal_operation", "ix_pending_commands_run_generation",
            "checkpoint_blobs_thread_id_idx", "checkpoint_writes_thread_id_idx",
            "checkpoints_thread_id_idx",
        )
    ],
    ("index_definition_ix_runs_lifecycle", "DROP INDEX durable_execution.ix_runs_lifecycle; CREATE INDEX ix_runs_lifecycle ON durable_execution.runs (trace_id)"),
    ("index_definition_ix_run_leases_expires_at", "DROP INDEX durable_execution.ix_run_leases_expires_at; CREATE INDEX ix_run_leases_expires_at ON durable_execution.run_leases (expires_at)"),
    ("index_definition_ix_operation_journal_operation", "DROP INDEX durable_execution.ix_operation_journal_operation; CREATE INDEX ix_operation_journal_operation ON durable_execution.operation_journal (operation_logical_step, run_id)"),
    ("index_definition_ix_pending_commands_run_generation", "DROP INDEX durable_execution.ix_pending_commands_run_generation; CREATE INDEX ix_pending_commands_run_generation ON durable_execution.pending_commands (pause_generation, run_id)"),
    ("extra_index", "CREATE INDEX drift_extra_index ON durable_execution.runs (trace_id)"),
    ("saver_primary_key_same_name_wrong_columns", "ALTER TABLE durable_execution.checkpoint_blobs DROP CONSTRAINT checkpoint_blobs_pkey; ALTER TABLE durable_execution.checkpoint_blobs ADD CONSTRAINT checkpoint_blobs_pkey PRIMARY KEY (thread_id, checkpoint_ns, channel, type)"),
    ("saver_index_same_name_wrong_columns", "DROP INDEX durable_execution.checkpoint_blobs_thread_id_idx; CREATE INDEX checkpoint_blobs_thread_id_idx ON durable_execution.checkpoint_blobs (version)"),
    ("version", "UPDATE durable_execution.alembic_version SET version_num = 'drifted'"),
    ("version_extra", "INSERT INTO durable_execution.alembic_version (version_num) VALUES ('m11_extra')"),
    ("saver_version_0", "DELETE FROM durable_execution.checkpoint_migrations WHERE v = 0"),
    ("saver_version_9", "DELETE FROM durable_execution.checkpoint_migrations WHERE v = 9"),
    ("saver_version_extra", "INSERT INTO durable_execution.checkpoint_migrations (v) VALUES (10)"),
    ("saver_version_negative", "INSERT INTO durable_execution.checkpoint_migrations (v) VALUES (-1)"),
    *[
        (
            f"limits_{constraint}",
            f"ALTER TABLE durable_execution.capacity_state DROP CONSTRAINT {constraint}",
        )
        for constraint in (
            "ck_capacity_state_max_active_runs", "ck_capacity_state_max_stored_runs",
            "ck_capacity_state_max_operations", "ck_capacity_state_max_transitions",
            "ck_capacity_state_max_journal_rows", "ck_capacity_state_journal_rows_range",
            "ck_capacity_state_max_payload", "ck_capacity_state_max_lineage",
            "ck_capacity_state_recursion", "ck_capacity_state_lease_duration",
            "ck_capacity_state_lease_renew", "ck_capacity_state_active_count",
            "ck_capacity_state_stored_count",
        )
    ],
]


@pytest.mark.parametrize(("case_id", "statement"), _DRIFT_CASES)
async def test_t02_drift_matrix_rejects_every_structural_dimension(
    db_connection,
    case_id: str,
    statement: str,
) -> None:
    del case_id
    await _assert_drift_rejected_and_rolled_back(db_connection, statement)


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("capacity_state", "max_active_runs"),
        ("runs", "contract_size_bytes"),
        ("run_leases", "renewed_at"),
        ("budget_limits", "activation_ordinal"),
        ("budget_operations", "amount"),
        ("operation_journal", "reason_code"),
        ("pending_commands", "status"),
        ("alembic_version", "version_num"),
        ("checkpoint_migrations", "v"),
        ("checkpoints", "checkpoint"),
        ("checkpoint_blobs", "blob"),
        ("checkpoint_writes", "blob"),
    ],
)
async def test_t02_missing_column_is_rejected_for_each_business_table(
    db_connection,
    table: str,
    column: str,
) -> None:
    statement = f'ALTER TABLE durable_execution."{table}" DROP COLUMN "{column}" CASCADE'
    await _assert_drift_rejected_and_rolled_back(db_connection, statement)


async def test_t02_lineage_limits_and_reason_closed_sets_reject_invalid_writes(db_connection) -> None:
    for value in (0, 257):
        with pytest.raises(CheckViolation):
            await db_connection.execute(
                "INSERT INTO durable_execution.capacity_state (singleton_id, max_lineage_checkpoints) VALUES (1, %s)",
                (value,),
            )
        await db_connection.rollback()

    run_id = "11111111-1111-4111-8111-111111111111"
    base_run = (
        "INSERT INTO durable_execution.runs "
        "(run_id, trace_id, lifecycle, bootstrap_state, contract_id, contract_schema_version, "
        "contract_bytes, contract_digest, contract_size_bytes, finalized_at, blocked_reason_code) "
        "VALUES (%s, '22222222-2222-4222-8222-222222222222', 'blocked', 'bootstrap_pending', "
        "'test.contract', '1.0', %s, 'sha256:' || repeat('0', 64), 1, clock_timestamp(), 'state_invalid')"
    )
    valid_reasons = (
        "contract_mismatch", "budget_mismatch", "checkpoint_invalid", "checkpoint_lineage_invalid",
        "journal_invalid", "artifact_invalid", "pending_command_invalid", "node_classification_invalid",
        "non_recoverable_dispatch", "operation_indeterminate", "recursion_limit_exceeded", "state_invalid",
    )
    for reason in valid_reasons:
        await db_connection.execute(base_run, (run_id, b"x"))
        await db_connection.execute(
            "UPDATE durable_execution.runs SET blocked_reason_code = %s WHERE run_id = %s",
            (reason, run_id),
        )
        await db_connection.rollback()

    with pytest.raises(CheckViolation):
        await db_connection.execute(base_run, (run_id, b"x"))
        await db_connection.execute(
            "UPDATE durable_execution.runs SET blocked_reason_code = 'free error text' WHERE run_id = %s",
            (run_id,),
        )
    await db_connection.rollback()

    await db_connection.execute(base_run, (run_id, b"x"))
    valid_journal_rows = [
        ("execution_permitted", None, None, None, None, None),
        ("budget_rejected", None, None, None, None, None),
        ("artifact_confirmed", "00000000-0000-4000-8000-000000000001", None, None, None, None),
        ("checkpoint_confirmed", None, "", "checkpoint-1", "sha256:" + "0" * 64, None),
        ("completed", None, "", "checkpoint-2", "sha256:" + "0" * 64, None),
        ("indeterminate", None, None, None, None, "external_result_unknown"),
    ]
    for ordinal, (state, artifact_id, checkpoint_ns, checkpoint_id, checkpoint_digest, reason) in enumerate(valid_journal_rows, 1):
        artifact_type = "artifact.test" if artifact_id is not None else None
        artifact_schema_version = "1.0" if artifact_id is not None else None
        artifact_content_hash = "sha256:" + "0" * 64 if artifact_id is not None else None
        artifact_created_at = datetime.now(timezone.utc) if artifact_id is not None else None
        artifact_size_bytes = 1 if artifact_id is not None else None
        await db_connection.execute(
            """
            INSERT INTO durable_execution.operation_journal
                (run_id, operation_phase, operation_logical_step, transition_ordinal, node_name,
                 journal_state, artifact_id, artifact_type, artifact_schema_version,
                 artifact_content_hash, artifact_created_at, artifact_size_bytes,
                 checkpoint_ns, checkpoint_id, checkpoint_digest, reason_code)
            VALUES (%s, 'requirements', %s, %s, 'requirements', %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id, f"step_{ordinal}", 1, state, artifact_id,
                artifact_type, artifact_schema_version, artifact_content_hash,
                artifact_created_at, artifact_size_bytes,
                checkpoint_ns, checkpoint_id,
                checkpoint_digest,
                reason,
            ),
        )
    await db_connection.rollback()

    await db_connection.execute(base_run, (run_id, b"x"))
    with pytest.raises(CheckViolation):
        await db_connection.execute(
            "INSERT INTO durable_execution.operation_journal "
            "(run_id, operation_phase, operation_logical_step, transition_ordinal, node_name, journal_state, reason_code) "
            "VALUES (%s, 'requirements', 'bad_reason', 1, 'requirements', 'indeterminate', 'free error text')",
            (run_id,),
        )
    await db_connection.rollback()


async def _insert_pending_test_run(connection, run_id: str) -> None:
    await connection.execute(
        """
        INSERT INTO durable_execution.runs
            (run_id, trace_id, lifecycle, bootstrap_state, contract_id,
             contract_schema_version, contract_bytes, contract_digest,
             contract_size_bytes)
        VALUES (%s, %s, 'open', 'bootstrap_pending', 'test.contract', '1.0',
                %s, %s, 1)
        """,
        (
            run_id,
            "22222222-2222-4222-8222-222222222222",
            b"x",
            "sha256:" + "0" * 64,
        ),
    )


async def _insert_pending_case(connection, values: tuple[object, ...]) -> None:
    await connection.execute(
        """
        INSERT INTO durable_execution.pending_commands
            (run_id, pause_generation, command_kind, node_name,
             command_type_id, command_type_version, command_bytes,
             command_digest, command_size_bytes, status, pause_token_digest,
             pause_context_type, pause_context_version, pause_context_bytes,
             pause_context_digest, pause_context_size_bytes, pre_checkpoint_ns,
             pre_checkpoint_id, pre_checkpoint_digest, accepted_at,
             dispatch_started_at, checkpointed_at, cleared_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        values,
    )


@pytest.mark.parametrize(
    ("case_id", "values", "valid"),
    [
        (
            "gen0_accepted",
            ("11111111-1111-4111-8111-111111111111", 0, "initial", "__start__", "test.command", "1.0", b"x", "sha256:" + "0" * 64, 1, "accepted", None, None, None, None, None, None, None, None, None, datetime(2026, 9, 18, tzinfo=timezone.utc), None, None, None),
            True,
        ),
        (
            "gen0_dispatch_started",
            ("11111111-1111-4111-8111-111111111111", 0, "initial", "__start__", "test.command", "1.0", b"x", "sha256:" + "0" * 64, 1, "dispatch_started", None, None, None, None, None, None, None, None, None, datetime(2026, 9, 18, tzinfo=timezone.utc), datetime(2026, 9, 18, tzinfo=timezone.utc), None, None),
            True,
        ),
        (
            "gen0_checkpointed",
            ("11111111-1111-4111-8111-111111111111", 0, "initial", "__start__", "test.command", "1.0", None, "sha256:" + "0" * 64, 1, "checkpointed", None, None, None, None, None, None, None, None, None, datetime(2026, 9, 18, tzinfo=timezone.utc), datetime(2026, 9, 18, tzinfo=timezone.utc), datetime(2026, 9, 18, tzinfo=timezone.utc), None),
            True,
        ),
        (
            "gen0_cleared_retained",
            ("11111111-1111-4111-8111-111111111111", 0, "initial", "__start__", "test.command", "1.0", None, "sha256:" + "0" * 64, 1, "cleared", None, None, None, None, None, None, None, None, None, datetime(2026, 9, 18, tzinfo=timezone.utc), None, None, datetime(2026, 9, 18, tzinfo=timezone.utc)),
            True,
        ),
        (
            "gen0_cleared_empty",
            ("11111111-1111-4111-8111-111111111111", 0, "initial", "__start__", None, None, None, None, None, "cleared", None, None, None, None, None, None, None, None, None, None, None, None, datetime(2026, 9, 18, tzinfo=timezone.utc)),
            True,
        ),
        (
            "gen0_awaiting_rejected",
            ("11111111-1111-4111-8111-111111111111", 0, "initial", "__start__", None, None, None, None, None, "awaiting_input", None, None, None, None, None, None, None, None, None, None, None, None, None),
            False,
        ),
        (
            "gen0_retained_without_acceptance_rejected",
            ("11111111-1111-4111-8111-111111111111", 0, "initial", "__start__", "test.command", "1.0", None, "sha256:" + "0" * 64, 1, "cleared", None, None, None, None, None, None, None, None, None, None, None, None, datetime(2026, 9, 18, tzinfo=timezone.utc)),
            False,
        ),
        (
            "gen1_awaiting",
            ("11111111-1111-4111-8111-111111111111", 1, "resume", "requirements", None, None, None, None, None, "awaiting_input", "sha256:" + "1" * 64, "durable_execution.root_pause", "1.0", b"c", "sha256:" + "2" * 64, 1, "", "checkpoint-1", "sha256:" + "3" * 64, None, None, None, None),
            True,
        ),
        (
            "gen1_checkpointed",
            ("11111111-1111-4111-8111-111111111111", 1, "resume", "requirements", "test.command", "1.0", None, "sha256:" + "0" * 64, 1, "checkpointed", "sha256:" + "1" * 64, "durable_execution.root_pause", "1.0", None, "sha256:" + "2" * 64, 1, "", "checkpoint-1", "sha256:" + "3" * 64, datetime(2026, 9, 18, tzinfo=timezone.utc), datetime(2026, 9, 18, tzinfo=timezone.utc), datetime(2026, 9, 18, tzinfo=timezone.utc), None),
            True,
        ),
        (
            "gen1_cleared_retained",
            ("11111111-1111-4111-8111-111111111111", 1, "resume", "requirements", "test.command", "1.0", None, "sha256:" + "0" * 64, 1, "cleared", "sha256:" + "1" * 64, "durable_execution.root_pause", "1.0", None, "sha256:" + "2" * 64, 1, "", "checkpoint-1", "sha256:" + "3" * 64, datetime(2026, 9, 18, tzinfo=timezone.utc), datetime(2026, 9, 18, tzinfo=timezone.utc), None, datetime(2026, 9, 18, tzinfo=timezone.utc)),
            True,
        ),
        (
            "gen1_cleared_empty",
            ("11111111-1111-4111-8111-111111111111", 1, "resume", "requirements", None, None, None, None, None, "cleared", "sha256:" + "1" * 64, "durable_execution.root_pause", "1.0", None, "sha256:" + "2" * 64, 1, "", "checkpoint-1", "sha256:" + "3" * 64, None, None, None, datetime(2026, 9, 18, tzinfo=timezone.utc)),
            True,
        ),
    ],
)
async def test_t02_pending_generation_and_lifecycle_matrix(
    db_connection,
    case_id: str,
    values: tuple[object, ...],
    valid: bool,
) -> None:
    del case_id
    await _insert_pending_test_run(db_connection, "11111111-1111-4111-8111-111111111111")
    if valid:
        await _insert_pending_case(db_connection, values)
    else:
        with pytest.raises(CheckViolation):
            await _insert_pending_case(db_connection, values)
    await db_connection.rollback()


def _run_alembic_at_revision(settings, database: str, revision: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "/workspace/app/durable_execution/alembic.ini", "upgrade", revision],
        cwd=Path(__file__).parents[3],
        env=_module_environment(settings, database),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


async def _reset_module11_test_schema(connection) -> None:
    await connection.execute("DROP SCHEMA IF EXISTS durable_execution CASCADE")
    await connection.commit()


async def _seed_legacy_runs(connection) -> None:
    digest = "sha256:" + "0" * 64
    rows = (
        ("11111111-1111-4111-8111-111111111111", "open", "bootstrap_pending", None, None),
        ("33333333-3333-4333-8333-333333333333", "closed", "bootstrap_checkpointed", "checkpoint-closed", None),
        ("44444444-4444-4444-8444-444444444444", "blocked", "bootstrap_pending", None, "state_invalid"),
    )
    for run_id, lifecycle, bootstrap, checkpoint_id, blocked_reason in rows:
        await connection.execute(
            """
            INSERT INTO durable_execution.runs
                (run_id, trace_id, lifecycle, bootstrap_state, contract_id,
                 contract_schema_version, contract_bytes, contract_digest,
                 contract_size_bytes, latest_root_checkpoint_ns,
                 latest_root_checkpoint_id, latest_root_checkpoint_digest,
                 blocked_reason_code, finalized_at)
            VALUES (%s, '22222222-2222-4222-8222-222222222222', %s, %s,
                    'test.contract', '1.0', %s, %s, 1, %s, %s, %s, %s,
                    CASE WHEN %s = 'open' THEN NULL ELSE clock_timestamp() END)
            """,
            (run_id, lifecycle, bootstrap, b"x", digest,
             "" if checkpoint_id else None, checkpoint_id,
             digest if checkpoint_id else None, blocked_reason, lifecycle),
        )
    await connection.execute(
        """
        INSERT INTO durable_execution.pending_commands
            (run_id, pause_generation, command_kind, node_name,
             command_type_id, command_type_version, command_bytes,
             command_digest, command_size_bytes, status, accepted_at)
        VALUES ('11111111-1111-4111-8111-111111111111', 0, 'initial',
                '__start__', 'test.command', '1.0', %s, %s, 1,
                'accepted', clock_timestamp())
        """,
        (b"x", digest),
    )
    await connection.execute(
        """
        INSERT INTO durable_execution.operation_journal
            (run_id, operation_phase, operation_logical_step,
             transition_ordinal, node_name, journal_state)
        VALUES ('11111111-1111-4111-8111-111111111111', 'requirements',
                'legacy_step', 1, 'requirements', 'execution_permitted')
        """,
    )
    await connection.commit()


async def test_w01_legacy_rows_upgrade_without_rewriting_evidence(
    scope_db_connection, durable_execution_settings,
) -> None:
    connection = scope_db_connection
    database = _database_name("MODULE11_TEST_SCOPE_DB", os.environ["MODULE11_TEST_PREFIX"])
    await _reset_module11_test_schema(connection)
    assert _run_alembic_at_revision(durable_execution_settings, database, "m11_0001").returncode == 0
    async with await psycopg.AsyncConnection.connect(
        host=durable_execution_settings.host, port=durable_execution_settings.port,
        dbname=database, user=durable_execution_settings.user,
        password=durable_execution_settings.password.get_secret_value(),
        autocommit=True, row_factory=dict_row,
        options="-c search_path=durable_execution,pg_catalog -c timezone=UTC",
    ) as saver_connection:
        await AsyncPostgresSaver(saver_connection).setup()
    await _seed_legacy_runs(connection)
    old_runs = await _fetchall(connection, "SELECT * FROM durable_execution.runs ORDER BY run_id")
    old_pending = await _fetchall(connection, "SELECT * FROM durable_execution.pending_commands")
    old_journal = await _fetchall(connection, "SELECT * FROM durable_execution.operation_journal")
    old_saver_versions = await _fetchall(
        connection, "SELECT v FROM durable_execution.checkpoint_migrations ORDER BY v",
    )
    old_artifact = await _schema_fingerprint(connection, "artifact_store")
    await connection.rollback()

    result = _run_migration(durable_execution_settings, database)
    assert result.returncode == 0, result.stderr[-1000:]
    assert await _fetchall(connection, "SELECT version_num FROM durable_execution.alembic_version") == [(EXPECTED_REVISION,)]
    upgraded_runs = await _fetchall(connection, "SELECT * FROM durable_execution.runs ORDER BY run_id")
    assert len(upgraded_runs) == len(old_runs)
    assert [row[:len(old_runs[0])] for row in upgraded_runs] == old_runs
    assert await _fetchall(connection, "SELECT * FROM durable_execution.pending_commands") == old_pending
    assert await _fetchall(connection, "SELECT * FROM durable_execution.operation_journal") == old_journal
    assert await _fetchall(connection, "SELECT cancel_requested_at, cancel_terminal_bytes, cancel_terminal_digest, cancel_base_checkpoint_id, cancel_base_checkpoint_digest FROM durable_execution.runs") == [(None, None, None, None, None)] * 3
    assert await _schema_fingerprint(connection, "artifact_store") == old_artifact
    assert await _fetchall(
        connection, "SELECT v FROM durable_execution.checkpoint_migrations ORDER BY v",
    ) == old_saver_versions
    await check_durable_execution_schema(connection)
    await connection.rollback()

    before = await _schema_fingerprint(connection, "durable_execution")
    await connection.rollback()
    assert _run_migration(durable_execution_settings, database).returncode == 0
    assert await _schema_fingerprint(connection, "durable_execution") == before
    await check_durable_execution_schema(connection)
    await connection.rollback()


async def test_w01_new_cancel_checks_and_drift_are_enforced(db_connection) -> None:
    digest = "sha256:" + "0" * 64
    run_id = "55555555-5555-4555-8555-555555555555"
    await db_connection.execute(
        """
        INSERT INTO durable_execution.runs
            (run_id, trace_id, lifecycle, bootstrap_state, contract_id,
             contract_schema_version, contract_bytes, contract_digest,
             contract_size_bytes, latest_root_checkpoint_ns,
             latest_root_checkpoint_id, latest_root_checkpoint_digest,
             finalized_at)
        VALUES (%s, '22222222-2222-4222-8222-222222222222',
                'closed', 'bootstrap_checkpointed', 'test.contract', '1.0',
                %s, %s, 1, '', 'checkpoint-closed', %s, clock_timestamp())
        """,
        (run_id, b"x", digest, digest),
    )
    invalid_updates = (
        ("cancel_terminal_bytes = %s", (b"x",)),
        ("cancel_terminal_bytes = %s, cancel_terminal_digest = %s, cancel_base_checkpoint_id = %s, cancel_base_checkpoint_digest = %s", (b"x", digest, "checkpoint-closed", digest)),
        ("cancel_requested_at = clock_timestamp(), cancel_terminal_bytes = %s, cancel_terminal_digest = %s, cancel_base_checkpoint_id = %s, cancel_base_checkpoint_digest = %s", (b"x", "bad", "checkpoint-closed", digest)),
        ("cancel_requested_at = clock_timestamp()", ()),
        ("cancel_requested_at = clock_timestamp(), cancel_terminal_bytes = %s, cancel_terminal_digest = %s, cancel_base_checkpoint_id = %s, cancel_base_checkpoint_digest = %s", (b"", digest, "checkpoint-closed", digest)),
        ("cancel_requested_at = clock_timestamp(), cancel_terminal_bytes = %s, cancel_terminal_digest = %s, cancel_base_checkpoint_id = %s, cancel_base_checkpoint_digest = %s", (b"x", digest, "", digest)),
        ("cancel_requested_at = clock_timestamp(), cancel_terminal_bytes = %s, cancel_terminal_digest = %s, cancel_base_checkpoint_id = %s, cancel_base_checkpoint_digest = %s", (b"x", digest, "checkpoint-closed", "bad")),
        ("cancel_requested_at = clock_timestamp(), cancel_terminal_bytes = %s, cancel_terminal_digest = %s, cancel_base_checkpoint_id = %s, cancel_base_checkpoint_digest = %s", (b"x", None, "checkpoint-closed", digest)),
        ("cancel_requested_at = clock_timestamp(), cancel_terminal_bytes = %s, cancel_terminal_digest = %s, cancel_base_checkpoint_id = %s, cancel_base_checkpoint_digest = %s", (b"x", digest, None, digest)),
        ("cancel_requested_at = clock_timestamp(), cancel_terminal_bytes = %s, cancel_terminal_digest = %s, cancel_base_checkpoint_id = %s, cancel_base_checkpoint_digest = %s", (b"x", digest, "checkpoint-closed", None)),
    )
    for assignment, params in invalid_updates:
        await db_connection.execute("SAVEPOINT invalid_cancel_update")
        with pytest.raises(CheckViolation):
            await db_connection.execute(
                f"UPDATE durable_execution.runs SET {assignment} WHERE run_id = %s",
                (*params, run_id),
            )
        await db_connection.execute("ROLLBACK TO SAVEPOINT invalid_cancel_update")
        await db_connection.execute("RELEASE SAVEPOINT invalid_cancel_update")
    await db_connection.execute(
        """
        UPDATE durable_execution.runs
        SET cancel_requested_at = clock_timestamp(), cancel_terminal_bytes = %s,
            cancel_terminal_digest = %s, cancel_base_checkpoint_id = %s,
            cancel_base_checkpoint_digest = %s
        WHERE run_id = %s
        """,
        (b"x", digest, "checkpoint-closed", digest, run_id),
    )
    assert await _fetchall(db_connection, "SELECT cancel_terminal_bytes FROM durable_execution.runs WHERE run_id = %s", (run_id,)) == [(b"x",)]
    await db_connection.rollback()
    await _assert_drift_rejected_and_rolled_back(
        db_connection,
        "ALTER TABLE durable_execution.runs DROP COLUMN cancel_requested_at CASCADE",
    )
    await _assert_drift_rejected_and_rolled_back(
        db_connection,
        "ALTER TABLE durable_execution.runs DROP CONSTRAINT ck_runs_cancel_terminal_fields, "
        "ADD CONSTRAINT ck_runs_cancel_terminal_fields CHECK (true)",
    )
    await _assert_drift_rejected_and_rolled_back(
        db_connection,
        "DROP INDEX durable_execution.ix_runs_created_at_run_id_desc",
    )


async def test_w01_downgrade_refuses_data_loss(db_connection, durable_execution_settings) -> None:
    before = await _schema_fingerprint(db_connection, "durable_execution")
    await db_connection.rollback()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "/workspace/app/durable_execution/alembic.ini", "downgrade", "m11_0001"],
        cwd=Path(__file__).parents[3],
        env=_module_environment(durable_execution_settings, durable_execution_settings.database),
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode != 0
    assert await _schema_fingerprint(db_connection, "durable_execution") == before
    assert await _fetchall(db_connection, "SELECT version_num FROM durable_execution.alembic_version") == [(EXPECTED_REVISION,)]
    await db_connection.rollback()
