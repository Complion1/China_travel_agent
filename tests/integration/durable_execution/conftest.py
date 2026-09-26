"""只消费宿主编排器提供的 Module 11 隔离身份，不控制 Docker。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import psycopg
import pytest
import pytest_asyncio

from app.durable_execution.settings import (
    DurableExecutionSettings,
    load_durable_execution_settings,
)


def _identity() -> str:
    token = os.environ.get("MODULE11_TEST_IDENTITY_TOKEN", "")
    prefix = os.environ.get("MODULE11_TEST_PREFIX", "")
    if type(token) is not str or type(prefix) is not str or not token or not prefix:
        pytest.fail("missing Module 11 isolated test identity")
    if token != f"{prefix}-identity":
        pytest.fail("invalid Module 11 isolated test identity")
    return prefix


_DATABASE_ROLES = {
    "main": "POSTGRES_DB",
    "migration": "MODULE11_TEST_MIGRATION_DB",
    "capacity": "MODULE11_TEST_CUSTOM_CAPACITY_DB",
    "scope": "MODULE11_TEST_SCOPE_DB",
    "noauto": "MODULE11_TEST_NOAUTO_DB",
    "fail": "MODULE11_TEST_FAIL_DB",
}


def _isolated_database_names(settings: DurableExecutionSettings) -> dict[str, str]:
    prefix = _identity()
    names = {role: os.environ.get(key, "") for role, key in _DATABASE_ROLES.items()}
    if any(
        type(database) is not str or database != f"{prefix}-identity-{role}"
        for role, database in names.items()
    ) or len(set(names.values())) != len(names):
        pytest.fail("missing, conflicting, or invalid Module 11 database identity")
    if settings.host != "postgres" or settings.database != names["main"]:
        pytest.fail("Module 11 settings are not bound to the isolated main database")
    return names


@pytest.fixture(scope="session")
def module11_main_settings() -> DurableExecutionSettings:
    settings = load_durable_execution_settings()
    _isolated_database_names(settings)
    return settings


@pytest.fixture(scope="session")
def isolated_databases(module11_main_settings: DurableExecutionSettings) -> dict[str, str]:
    return _isolated_database_names(module11_main_settings)


@pytest.fixture(scope="session")
def durable_execution_settings(
    module11_main_settings: DurableExecutionSettings,
) -> DurableExecutionSettings:
    return module11_main_settings


@pytest.fixture(scope="session")
def migration_settings(
    module11_main_settings: DurableExecutionSettings,
    isolated_databases: dict[str, str],
) -> DurableExecutionSettings:
    return module11_main_settings.model_copy(
        update={"database": isolated_databases["migration"]}
    )


@pytest.fixture(scope="session")
def custom_capacity_settings(
    module11_main_settings: DurableExecutionSettings,
    isolated_databases: dict[str, str],
) -> DurableExecutionSettings:
    return module11_main_settings.model_copy(
        update={"database": isolated_databases["capacity"]}
    )


async def _connect_database(
    settings: DurableExecutionSettings,
    database: str,
) -> psycopg.AsyncConnection:
    connection = await psycopg.AsyncConnection.connect(
        host=settings.host,
        port=settings.port,
        dbname=database,
        user=settings.user,
        password=settings.password.get_secret_value(),
        autocommit=False,
        prepare_threshold=0,
        options="-c search_path=durable_execution,pg_catalog -c timezone=UTC",
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT current_database()")
            current_database = await cursor.fetchone()
        await connection.rollback()
        if current_database != (database,):
            pytest.fail("Module 11 connection reached the wrong isolated database")
    except BaseException:
        await connection.close()
        raise
    return connection


@pytest_asyncio.fixture
async def scope_db_connection(
    module11_main_settings: DurableExecutionSettings,
    isolated_databases: dict[str, str],
) -> AsyncIterator[psycopg.AsyncConnection]:
    connection = await _connect_database(
        module11_main_settings, isolated_databases["scope"]
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def noauto_db_connection(
    module11_main_settings: DurableExecutionSettings,
    isolated_databases: dict[str, str],
) -> AsyncIterator[psycopg.AsyncConnection]:
    connection = await _connect_database(
        module11_main_settings, isolated_databases["noauto"]
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def fail_db_connection(
    module11_main_settings: DurableExecutionSettings,
    isolated_databases: dict[str, str],
) -> AsyncIterator[psycopg.AsyncConnection]:
    connection = await _connect_database(
        module11_main_settings, isolated_databases["fail"]
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def db_connection(
    durable_execution_settings: DurableExecutionSettings,
) -> AsyncIterator[psycopg.AsyncConnection]:
    connection = await _connect_database(
        durable_execution_settings, durable_execution_settings.database
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def migration_db_connection(
    migration_settings: DurableExecutionSettings,
) -> AsyncIterator[psycopg.AsyncConnection]:
    connection = await _connect_database(migration_settings, migration_settings.database)
    try:
        yield connection
    finally:
        await connection.close()
