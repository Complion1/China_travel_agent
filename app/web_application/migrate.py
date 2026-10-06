"""Explicit M14-only migration and exact read-only structural validation."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import os
from pathlib import Path
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import AsyncConnection
from sqlalchemy import text
from app.artifact_persistence import load_artifact_persistence_settings
from .errors import WebApplicationConfigurationError, WebApplicationDependencyError
from .security import canonical_json

HEAD="m14_0001_web_application"
# Exact PG16 structure: columns/defaults, CHECK/PK/FK, indexes and user triggers.
SCHEMA_FINGERPRINT="a7352e56af3589f6db160db280a77e73af3d8034b5e2541b4eea49ca21ee988e"

_SCHEMA_QUERIES=(
        ("relations","SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','S') ORDER BY c.relname"),
        ("columns","SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum"),
        ("constraints","SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname,k.conname"),
        ("indexes","SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=%s ORDER BY tablename,indexname"),
        ("triggers","SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"))

async def schema_fingerprint(connection, *, schema: str = "web_application") -> str:
    if schema not in {"web_application","artifact_store","durable_execution"}:
        raise WebApplicationConfigurationError()
    payload=[]
    for name,query in _SCHEMA_QUERIES:
        cursor=await connection.execute(query,(schema,))
        payload.append((name,await cursor.fetchall()))
    return hashlib.sha256(canonical_json(payload)).hexdigest()

def _verify_schema_transaction(connection) -> None:
    """Check the prospective schema before the surrounding DDL transaction commits."""
    rows=connection.execute(text("SELECT version_num FROM web_application.alembic_version ORDER BY version_num")).fetchall()
    payload=[]
    for name,query in _SCHEMA_QUERIES:
        result=connection.execute(text(query.replace("%s", ":schema")),{"schema":"web_application"})
        payload.append((name,[tuple(row) for row in result.fetchall()]))
    if [tuple(row) for row in rows]!=[(HEAD,)] or hashlib.sha256(canonical_json(payload)).hexdigest()!=SCHEMA_FINGERPRINT:
        raise WebApplicationConfigurationError()

async def verify_schema(connection) -> None:
    try:
        cursor=await connection.execute("SELECT version_num FROM web_application.alembic_version ORDER BY version_num")
        if await cursor.fetchall()!=[(HEAD,)] or await schema_fingerprint(connection)!=SCHEMA_FINGERPRINT:
            raise WebApplicationConfigurationError()
    except WebApplicationConfigurationError:
        raise
    except Exception:
        raise WebApplicationConfigurationError() from None

async def check() -> None:
    settings=load_artifact_persistence_settings(environ=os.environ)
    try:
        async with await AsyncConnection.connect(host=settings.host,port=settings.port,dbname=settings.database,
                user=settings.user,password=settings.password.get_secret_value(),
                autocommit=True,options="-c timezone=UTC -c statement_timeout=10000") as connection:
            await verify_schema(connection)
    except WebApplicationConfigurationError:
        raise
    except Exception:
        raise WebApplicationDependencyError() from None

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    args=parser.parse_args()
    try:
        config=Config(str(Path(__file__).with_name("alembic.ini")))
        if ScriptDirectory.from_config(config).get_heads()!=[HEAD]:
            raise WebApplicationConfigurationError()
        if not args.check:
            command.upgrade(config,"head")
        asyncio.run(check())
    except Exception:
        print("WebApplicationConfigurationError")
        return 2
    print("M14 schema verified")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
