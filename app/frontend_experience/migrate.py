"""Explicit M24 migration entry and read-only structural verification."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import AsyncConnection
from sqlalchemy import text
from app.artifact_persistence import load_artifact_persistence_settings
from .models import PublicProgressError

HEAD = "m24_0001_public_progress"
SCHEMA_FINGERPRINT = "cf6c72da77af2d74f7f6570e955ff81f487500a5e7e8f757ab4a0ac36503992c"
SHAPE_QUERIES = (
    "SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='frontend_experience' AND c.relkind IN ('r','p','v','m','S') ORDER BY c.relname",
    "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname='frontend_experience' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
    "SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='frontend_experience' ORDER BY c.relname,k.conname",
    "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='frontend_experience' ORDER BY tablename,indexname",
    "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='frontend_experience' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
)


def _digest(shape):
    return hashlib.sha256(json.dumps(shape, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def verify_transaction(connection):
    shape = [[tuple(row) for row in connection.execute(text(query)).fetchall()] for query in SHAPE_QUERIES]
    head = connection.execute(text("SELECT version_num FROM frontend_experience.alembic_version")).fetchall()
    if head != [(HEAD,)] or _digest(shape) != SCHEMA_FINGERPRINT:
        raise PublicProgressError()


async def verify_schema(connection) -> None:
    try:
        head = await (await connection.execute("SELECT version_num FROM frontend_experience.alembic_version")).fetchall()
        shape = [await (await connection.execute(query)).fetchall() for query in SHAPE_QUERIES]
        if head != [(HEAD,)] or _digest(shape) != SCHEMA_FINGERPRINT:
            raise PublicProgressError()
    except Exception:
        raise PublicProgressError() from None


async def check():
    settings = load_artifact_persistence_settings()
    async with await AsyncConnection.connect(host=settings.host, port=settings.port, dbname=settings.database,
            user=settings.user, password=settings.password.get_secret_value(), autocommit=True,
            options="-c timezone=UTC -c statement_timeout=5000") as connection:
        await verify_schema(connection)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        config = Config(str(Path(__file__).with_name("alembic.ini")))
        if ScriptDirectory.from_config(config).get_heads() != [HEAD]:
            raise PublicProgressError()
        if not args.check:
            command.upgrade(config, "head")
        asyncio.run(check())
    except Exception:
        print("PublicProgressConfigurationError")
        return 2
    print("M24 schema verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
