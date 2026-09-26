from __future__ import annotations

import asyncio
import hashlib
import traceback
from uuid import UUID

import pytest

from app.durable_execution import repository as repository_module
from app.durable_execution.errors import DurableExecutionConfigurationError
from app.durable_execution.errors import DurableExecutionDependencyError
from app.durable_execution import migrate as migration_module
from app.durable_execution.repository import (
    EXPECTED_BUSINESS_TABLES,
    EXPECTED_REVISION,
    EXPECTED_SAVER_TABLES,
    EXPECTED_TABLES,
    SCHEMA_NAME,
    migration_advisory_key,
)


def test_schema_constants_freeze_the_twelve_expected_tables() -> None:
    assert SCHEMA_NAME == "durable_execution"
    assert EXPECTED_REVISION == "m11_0001"
    assert EXPECTED_BUSINESS_TABLES == (
        "capacity_state",
        "runs",
        "run_leases",
        "budget_limits",
        "budget_operations",
        "operation_journal",
        "pending_commands",
    )
    assert EXPECTED_SAVER_TABLES == (
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    )
    assert EXPECTED_TABLES == ("alembic_version",) + EXPECTED_BUSINESS_TABLES + EXPECTED_SAVER_TABLES


def test_advisory_key_matches_the_cross_process_signed_bigint_algorithm() -> None:
    payload = b""
    expected = int.from_bytes(
        hashlib.sha256(
            b"durable-execution-advisory-v1\x00migration\x00" + payload
        ).digest()[:8],
        "big",
        signed=True,
    )
    assert migration_advisory_key(domain="migration") == expected
    assert migration_advisory_key(domain="migration") == migration_advisory_key(domain="migration")
    assert migration_advisory_key(domain="capacity") != expected
    assert migration_advisory_key(
        domain="run",
        payload=b"11111111-1111-4111-8111-111111111111",
    ) != expected


@pytest.mark.parametrize(
    "domain,payload",
    [
        ("other", b""),
        ("migration", b"target"),
        ("capacity", b"target"),
        ("run", b"11111111-1111-4111-8111-11111111111"),
        ("run", b"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".upper()),
    ],
)
def test_advisory_key_rejects_noncanonical_inputs(domain: str, payload: bytes) -> None:
    with pytest.raises(DurableExecutionConfigurationError):
        migration_advisory_key(domain=domain, payload=payload)


def test_run_key_uses_canonical_lowercase_hyphen_uuid() -> None:
    run_id = UUID("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA")
    assert migration_advisory_key(domain="run", payload=str(run_id).lower().encode("ascii")) == migration_advisory_key(
        domain="run", payload=b"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )


class _FakeConnection:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[object, ...]] = []
        self.closed = False

    async def execute(self, *args: object) -> None:
        self.execute_calls.append(args)

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        await self.close()


class _SqlCanaryCursor:
    def __init__(self, canary: str) -> None:
        self.canary = canary
        self.execute_calls: list[tuple[object, object]] = []

    async def __aenter__(self) -> "_SqlCanaryCursor":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback_value) -> None:
        del exc_type, exc_value, traceback_value

    async def execute(self, query: object, params: object) -> None:
        self.execute_calls.append((query, params))
        raise RuntimeError(f"driver SQL failure: {self.canary}")

    async def fetchall(self) -> list[tuple[object, ...]]:
        return []


class _SqlCanaryConnection:
    def __init__(self, canary: str) -> None:
        self.cursor_instance = _SqlCanaryCursor(canary)

    def cursor(self) -> _SqlCanaryCursor:
        return self.cursor_instance


@pytest.mark.asyncio
async def test_parameterized_query_canary_is_redacted_at_module_boundary(
    caplog,
    capsys,
) -> None:
    canary = "m11-r04-r01-sql-parameter-canary"
    connection = _SqlCanaryConnection(canary)
    query = "SELECT value FROM durable_execution.runs WHERE run_id = %s"

    with pytest.raises(DurableExecutionDependencyError) as caught:
        await repository_module._fetchall(connection, query, (canary,))

    error = caught.value
    assert connection.cursor_instance.execute_calls == [(query, (canary,))]
    assert error.args == ()
    assert str(error) == "DurableExecutionDependencyError"
    assert repr(error) == "DurableExecutionDependencyError()"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(error))
    captured = capsys.readouterr()
    for output in (rendered, captured.out, captured.err, caplog.text):
        assert canary not in output
        assert "driver SQL failure" not in output


def test_migration_failure_closes_lock_and_never_reports_completion(monkeypatch) -> None:
    connections: list[_FakeConnection] = []

    async def connect(*args: object, **kwargs: object) -> _FakeConnection:
        del args, kwargs
        connection = _FakeConnection()
        connections.append(connection)
        return connection

    def fail_upgrade(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("test-only failure")

    monkeypatch.setattr(migration_module.AsyncConnection, "connect", connect)
    monkeypatch.setattr(migration_module.command, "upgrade", fail_upgrade)
    settings = migration_module.DurableExecutionSettings(
        host="postgres",
        port=5432,
        database="db",
        user="user",
        password="test-only-secret",
    )
    with pytest.raises(DurableExecutionDependencyError):
        asyncio.run(migration_module._run_migration(settings))
    assert len(connections) == 1
    assert connections[0].closed is True
    assert any("pg_advisory_unlock" in str(call[0]) for call in connections[0].execute_calls)


def test_saver_setup_failure_closes_both_connections(monkeypatch) -> None:
    connections: list[_FakeConnection] = []

    async def connect(*args: object, **kwargs: object) -> _FakeConnection:
        del args, kwargs
        connection = _FakeConnection()
        connections.append(connection)
        return connection

    async def fail_setup(self) -> None:
        del self
        raise RuntimeError("test-only saver failure")

    monkeypatch.setattr(migration_module.AsyncConnection, "connect", connect)
    monkeypatch.setattr(migration_module.command, "upgrade", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration_module.AsyncPostgresSaver, "setup", fail_setup)
    settings = migration_module.DurableExecutionSettings(
        host="postgres",
        port=5432,
        database="db",
        user="user",
        password="test-only-secret",
    )
    with pytest.raises(DurableExecutionDependencyError):
        asyncio.run(migration_module._run_migration(settings))
    assert len(connections) == 2
    assert all(connection.closed for connection in connections)
