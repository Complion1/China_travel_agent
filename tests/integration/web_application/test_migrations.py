"""True PostgreSQL migration, preservation and rollback gate."""
from __future__ import annotations
import asyncio
from datetime import date, datetime, timezone
import hashlib
import os
import subprocess
import sys
from uuid import uuid4
import psycopg
import pytest
from psycopg import sql
from app.artifact_persistence import create_artifact_persistence_runtime
from app.workflow_contracts import ArtifactSchemaRegistry, ArtifactSchemaRegistration, ArtifactWriteRequest, OperationId, WorkflowPhase
from app.web_application.migrate import HEAD, SCHEMA_FINGERPRINT, schema_fingerprint, verify_schema
from tests.unit.workflow_contracts.fakes import StrictSampleArtifact
from .conftest import connection_kwargs

def environment(settings):
    return os.environ.copy() | {"POSTGRES_HOST":settings.host,"POSTGRES_PORT":str(settings.port),
        "POSTGRES_DB":settings.database,"POSTGRES_USER":settings.user,"POSTGRES_PASSWORD":settings.password.get_secret_value()}

def migrate(settings, command=None):
    return subprocess.run([sys.executable,"-m",*(command or ["app.web_application.migrate"])],
        env=environment(settings),capture_output=True,text=True,timeout=45)

async def scratch(settings, code):
    name=settings.database+"_"+code+"_"+uuid4().hex[:6]
    assert len(name)<=63 and name.startswith(settings.database+"_")
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(settings)) as connection:
        await connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return settings.model_copy(update={"database":name})

def legacy(settings, wrong=None):
    first=migrate(settings,["alembic","-c","app/artifact_persistence/alembic.ini","upgrade",
                            "m10_0001" if wrong=="artifact_store" else "head"])
    assert first.returncode==0, first.stderr
    command=["alembic","-c","app/durable_execution/alembic.ini","upgrade","m11_0001"] if wrong=="durable_execution" else ["app.durable_execution.migrate"]
    second=migrate(settings,command)
    assert second.returncode==0, second.stderr

async def data_hash(connection, schema):
    cursor=await connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name",(schema,))
    tables=await cursor.fetchall()
    digest=hashlib.sha256()
    for (table,) in tables:
        cursor=await connection.execute(sql.SQL("SELECT row_to_json(t)::text FROM {}.{} t ORDER BY row_to_json(t)::text").format(sql.Identifier(schema),sql.Identifier(table)))
        for (row,) in await cursor.fetchall():
            digest.update(row.encode())
    return digest.hexdigest()

async def legacy_snapshot(connection):
    snapshots=[]
    for schema in ("artifact_store","durable_execution"):
        snapshots.append((await schema_fingerprint(connection,schema=schema),await data_hash(connection,schema)))
    return tuple(snapshots)

async def original_fixture(settings):
    registry=ArtifactSchemaRegistry((ArtifactSchemaRegistration(artifact_type="webtest.fixture",schema_version="1.0",model=StrictSampleArtifact),))
    resource=await create_artifact_persistence_runtime(settings=settings,registry=registry)
    try:
        fixture=StrictSampleArtifact(label="isolated-migration-fixture",count=7,ratio=1.0,
            occurred_on=date(2026,10,5),observed_at=datetime(2026,10,5,tzinfo=timezone.utc),note=None)
        return await resource.repository.put(ArtifactWriteRequest(
            operation_id=OperationId(run_id=uuid4(),phase=WorkflowPhase.REQUIREMENTS,logical_step="migration_fixture"),
            artifact_type="webtest.fixture",schema_version="1.0",content=fixture.model_dump(mode="json")))
    finally:
        await resource.close()

@pytest.mark.asyncio
async def test_exact_six_table_structure_types_constraints_and_indexes(pg_connection):
    await verify_schema(pg_connection)
    cursor=await pg_connection.execute("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='web_application' ORDER BY table_name,ordinal_position")
    columns=await cursor.fetchall()
    expected={
        "visitors":[("visitor_hash","character","NO"),("issued_at","timestamp with time zone","NO"),("expires_at","timestamp with time zone","NO")],
        "run_owners":[("run_id","uuid","NO"),("visitor_hash","character","NO"),("bound_at","timestamp with time zone","NO")],
        "requests":[("visitor_hash","character","NO"),("request_id","uuid","NO"),("run_id","uuid","NO"),("kind","text","NO"),("body_fingerprint","character","NO"),("pause_generation","bigint","YES"),("status","text","NO"),("reason","text","YES"),("created_at","timestamp with time zone","NO")],
        "admissions":[("run_id","uuid","NO"),("visitor_hash","character","NO"),("ip_hash","character","NO"),("reserved_at","timestamp with time zone","NO"),("day_key","date","NO"),("status","text","NO")],
        "pause_credentials":[("run_id","uuid","NO"),("pause_generation","bigint","NO"),("contract_digest","text","NO"),("cipher_version","text","NO"),("nonce","bytea","NO"),("ciphertext","bytea","NO"),("saved_at","timestamp with time zone","NO")],
        "run_summaries":[("run_id","uuid","NO"),("trace_id","uuid","NO"),("terminal_code","text","NO"),("availability","text","NO"),("content_hash","text","YES"),("content_bytes","bytea","YES"),("size_bytes","bigint","YES"),("saved_at","timestamp with time zone","NO")],
        "alembic_version":[("version_num","character varying","NO")]}
    assert columns==[(table,*column) for table in sorted(expected) for column in expected[table]]
    cursor=await pg_connection.execute("SELECT conname,confupdtype::text,confdeltype::text FROM pg_constraint WHERE connamespace='web_application'::regnamespace AND contype='f' ORDER BY conname")
    assert await cursor.fetchall()==[(name,"a","a") for name in ("fk_admission_owner","fk_owner_visitor","fk_pause_owner","fk_request_owner","fk_summary_owner")]
    cursor=await pg_connection.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='web_application' ORDER BY indexname")
    indexes=dict(await cursor.fetchall())
    assert "bound_at DESC, run_id DESC" in indexes["ix_owner_history"]
    assert all(name in indexes for name in ("ix_admission_global","ix_admission_visitor","ix_admission_ip","uq_pause_claim"))
    assert "WHERE" in indexes["uq_pause_claim"] and "uncertain" in indexes["uq_pause_claim"]

@pytest.mark.asyncio
async def test_first_repeat_new_process_preserve_old_structures_and_fixture(pg_settings):
    settings=await scratch(pg_settings,"a")
    legacy(settings)
    ref=await original_fixture(settings)
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(settings)) as connection:
        before=await legacy_snapshot(connection)
        assert migrate(settings).returncode==0
        await verify_schema(connection)
        assert await legacy_snapshot(connection)==before
        assert migrate(settings).returncode==0
        assert migrate(settings,["app.web_application.migrate","--check"]).returncode==0
        assert await legacy_snapshot(connection)==before
        cursor=await connection.execute("SELECT content_hash FROM artifact_store.artifacts WHERE artifact_id=%s",(ref.artifact_id,))
        assert (await cursor.fetchone())[0]==ref.content_hash
        print("first/repeat/new-process: legacy structure and fixture hashes unchanged")

@pytest.mark.asyncio
async def test_concurrent_migrations_serialize_without_partial_schema(pg_settings):
    settings=await scratch(pg_settings,"b")
    legacy(settings)
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(settings)) as connection:
        before=await legacy_snapshot(connection)
        first,second=await asyncio.gather(asyncio.to_thread(migrate,settings),asyncio.to_thread(migrate,settings))
        assert first.returncode==0 or second.returncode==0
        assert first.returncode in (0,2) and second.returncode in (0,2)
        await verify_schema(connection)
        assert await legacy_snapshot(connection)==before

@pytest.mark.asyncio
async def test_conflicting_structure_rolls_back_all_new_ddl(pg_settings):
    settings=await scratch(pg_settings,"c")
    legacy(settings)
    await original_fixture(settings)
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(settings)) as connection:
        await connection.execute("CREATE SCHEMA web_application")
        await connection.execute("CREATE TABLE web_application.run_owners (poison INTEGER NOT NULL)")
        before=await legacy_snapshot(connection)
        web_before=await schema_fingerprint(connection)
        result=migrate(settings)
        assert result.returncode==2
        assert "WebApplicationConfigurationError" in result.stdout
        assert await schema_fingerprint(connection)==web_before
        assert await legacy_snapshot(connection)==before
        cursor=await connection.execute("SELECT to_regclass('web_application.visitors'),to_regclass('web_application.alembic_version')")
        assert await cursor.fetchone()==(None,None)
        assert migrate(settings,["app.web_application.migrate","--check"]).returncode==2

@pytest.mark.asyncio
async def test_extra_non_conflicting_table_rejects_before_commit(pg_settings):
    settings=await scratch(pg_settings,"e")
    legacy(settings)
    ref=await original_fixture(settings)
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(settings)) as connection:
        await connection.execute("CREATE SCHEMA web_application")
        await connection.execute("CREATE TABLE web_application.extra_table (marker INTEGER NOT NULL)")
        await connection.execute("INSERT INTO web_application.extra_table VALUES (7)")
        before=await legacy_snapshot(connection)
        web_before=(await schema_fingerprint(connection),await data_hash(connection,"web_application"))
        result=migrate(settings)
        assert result.returncode==2
        assert (await schema_fingerprint(connection),await data_hash(connection,"web_application"))==web_before
        assert await legacy_snapshot(connection)==before
        cursor=await connection.execute("SELECT relname FROM pg_class WHERE relnamespace='web_application'::regnamespace AND relkind='r' ORDER BY relname")
        assert await cursor.fetchall()==[("extra_table",)]
        cursor=await connection.execute("SELECT content_hash FROM artifact_store.artifacts WHERE artifact_id=%s",(ref.artifact_id,))
        assert (await cursor.fetchone())[0]==ref.content_hash
        assert migrate(settings,["app.web_application.migrate","--check"]).returncode==2

@pytest.mark.asyncio
@pytest.mark.parametrize("wrong",["artifact_store","durable_execution"])
async def test_wrong_prerequisite_head_rejects_without_mutation(pg_settings,wrong):
    settings=await scratch(pg_settings,"d")
    legacy(settings,wrong=wrong)
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(settings)) as connection:
        before=await legacy_snapshot(connection)
        assert migrate(settings).returncode==2
        assert await legacy_snapshot(connection)==before
        cursor=await connection.execute("SELECT to_regnamespace('web_application')")
        assert await cursor.fetchone()==(None,)

@pytest.mark.asyncio
async def test_check_constraints_reject_invalid_data_atomically(pg_connection):
    now=datetime(2026,10,5,tzinfo=timezone.utc)
    visitor=uuid4().hex*2
    with pytest.raises(psycopg.errors.CheckViolation):
        async with pg_connection.transaction():
            await pg_connection.execute("INSERT INTO web_application.visitors VALUES (%s,%s,%s)",(visitor,now,now))
    cursor=await pg_connection.execute("SELECT count(*) FROM web_application.visitors WHERE visitor_hash=%s",(visitor,))
    assert await cursor.fetchone()==(0,)
    from datetime import timedelta
    await pg_connection.execute("INSERT INTO web_application.visitors VALUES (%s,%s,%s)",(visitor,now,now+timedelta(days=30)))
    run=uuid4()
    await pg_connection.execute("INSERT INTO web_application.run_owners VALUES (%s,%s,%s)",(run,visitor,now))
    for kind,generation,status,reason in [("clarify",None,"claimed",None),("start",1,"claimed",None),
        ("cancel",None,"claimed","busy"),("recover",None,"uncertain",None),("bad",None,"claimed",None)]:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with pg_connection.transaction():
                await pg_connection.execute("INSERT INTO web_application.requests VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (visitor,uuid4(),run,kind,"a"*64,generation,status,reason,now))
    with pytest.raises(psycopg.errors.CheckViolation):
        async with pg_connection.transaction():
            await pg_connection.execute("INSERT INTO web_application.pause_credentials VALUES (%s,0,%s,'aesgcm-v1',%s,%s,%s)",
                (run,"sha256:"+"a"*64,b"short",bytes(16),now))
    with pytest.raises(psycopg.errors.CheckViolation):
        async with pg_connection.transaction():
            await pg_connection.execute("INSERT INTO web_application.run_summaries VALUES (%s,%s,'completed','available',NULL,NULL,NULL,%s)",(run,uuid4(),now))
