"""V §6.3: consume an explicitly isolated PostgreSQL; never control Docker."""

import hashlib

import psycopg
import pytest
import pytest_asyncio

from tests.integration.result_formatting.restart_worker import isolated_settings, open_runtime


@pytest.fixture(scope="session")
def module12_settings():
    return isolated_settings()


@pytest_asyncio.fixture
async def runtime_factory(module12_settings):
    runtimes = []

    async def create():
        runtime = await open_runtime(module12_settings)
        runtimes.append(runtime)
        return runtime

    try:
        yield create
    finally:
        for runtime in reversed(runtimes):
            await runtime.close()


@pytest_asyncio.fixture
async def database_snapshot(module12_settings):
    """Observe counts and canonical hashes using read-only SQL, without resets."""
    settings = module12_settings
    connection = await psycopg.AsyncConnection.connect(
        host=settings.host, port=settings.port, dbname=settings.database,
        user=settings.user, password=settings.password.get_secret_value(),
        connect_timeout=10, autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    )

    async def snapshot(run_id):
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT artifact_id, artifact_type, content_hash, size_bytes, canonical_bytes "
                "FROM artifact_store.artifacts WHERE run_id = %s ORDER BY artifact_id",
                (run_id,),
            )
            rows = await cursor.fetchall()
            records = {str(identity): {
                "type": kind, "content_hash": content_hash, "size_bytes": size_bytes,
                "stored_hash": "sha256:" + hashlib.sha256(canonical).hexdigest(),
                "stored_size": len(canonical),
            } for identity, kind, content_hash, size_bytes, canonical in rows}
            await cursor.execute(
                "SELECT artifact_count, total_content_bytes FROM artifact_store.capacity_state WHERE singleton_id = 1"
            )
            capacity = await cursor.fetchone()
        return {"records": records, "capacity": capacity}

    try:
        yield snapshot
    finally:
        await connection.close()
