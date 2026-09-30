"""Append-only diagnostic storage borrowing M10's pool (M10 §23)."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm_runtime import InteractionRecord, InteractionWriteResult
from app.llm_runtime.interaction_recording import body_snapshot

_DIAGNOSTIC_LOCK = 130230930
_WRITE_TIMEOUT = 2.0
_COLUMNS = ("record_id", "run_id", "trace_id", "call_id", "record_kind", "captured_at", "content_hash", "content_bytes", "size_bytes")
_TYPES = ("uuid", "uuid", "uuid", "uuid", "text", "timestamptz", "bpchar", "bytea", "int8")
_CONSTRAINTS = {
    "ck_interaction_records_hash": "CHECK ((content_hash ~ '^sha256:[0-9a-f]{64}$'::text))",
    "ck_interaction_records_kind": "CHECK ((record_kind = ANY (ARRAY['request'::text, 'response'::text, 'transport_failure'::text, 'validation'::text, 'tool_result'::text, 'call_summary'::text, 'run_summary'::text])))",
    "ck_interaction_records_size": "CHECK (((size_bytes = octet_length(content_bytes)) AND ((size_bytes >= 1) AND (size_bytes <= 8388608))))",
    "pk_interaction_records": "PRIMARY KEY (record_id)",
}


class InteractionStoreLimits(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    max_record_bytes: int = Field(ge=1, le=8 * 1024 * 1024)
    max_total_content_bytes: int = Field(ge=1, le=1024 * 1024 * 1024)

    @model_validator(mode="after")
    def _consistent(self):
        if self.max_total_content_bytes < self.max_record_bytes:
            raise ValueError("interaction_limits_invalid")
        return self


DEMO_INTERACTION_STORE_LIMITS = InteractionStoreLimits(max_record_bytes=8 * 1024 * 1024, max_total_content_bytes=1024 * 1024 * 1024)


class InteractionStoreReadError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("interaction_store_read_failed")


def canonical_record(record: InteractionRecord) -> bytes:
    # Revalidate even model_construct/unchecked model_copy before SQL or file I/O.
    validated = InteractionRecord.model_validate_json(record.model_dump_json())
    return json.dumps(validated.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _decode_row(row: tuple) -> InteractionRecord:
    record_id, run_id, trace_id, call_id, kind, captured_at, digest, content, size = row
    content = bytes(content)
    if len(content) != size or _hash(content) != digest:
        raise InteractionStoreReadError()
    record = InteractionRecord.model_validate_json(content)
    if canonical_record(record) != content or (record.record_id, record.run_id, record.trace_id, record.call_id, record.record_kind, record.captured_at) != (record_id, run_id, trace_id, call_id, kind, captured_at):
        raise InteractionStoreReadError()
    return record


async def _diagnostic_schema_available(pool: AsyncConnectionPool) -> bool:
    try:
        async with asyncio.timeout(_WRITE_TIMEOUT):
            async with pool.connection(timeout=_WRITE_TIMEOUT) as connection:
                async with connection.transaction():
                    async with connection.cursor() as cursor:
                        await cursor.execute("SET LOCAL statement_timeout = '1500ms'")
                        await cursor.execute("""SELECT column_name,udt_name,is_nullable,character_maximum_length
                            FROM information_schema.columns WHERE table_schema='artifact_store' AND table_name='interaction_records' ORDER BY ordinal_position""")
                        rows = await cursor.fetchall()
                        expected = tuple((name, kind, "YES" if name == "call_id" else "NO", 71 if name == "content_hash" else None) for name, kind in zip(_COLUMNS, _TYPES, strict=True))
                        if tuple(rows) != expected:
                            return False
                        await cursor.execute("""SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=to_regclass('artifact_store.interaction_records') ORDER BY conname""")
                        if dict(await cursor.fetchall()) != _CONSTRAINTS:
                            return False
                        await cursor.execute("""SELECT has_table_privilege(current_user,'artifact_store.interaction_records','SELECT'), has_table_privilege(current_user,'artifact_store.interaction_records','INSERT'),
                            EXISTS(SELECT 1 FROM pg_indexes WHERE schemaname='artifact_store' AND tablename='interaction_records' AND indexname='ix_interaction_records_run'
                                AND indexdef='CREATE INDEX ix_interaction_records_run ON artifact_store.interaction_records USING btree (run_id, captured_at, record_id)')""")
                        return await cursor.fetchone() == (True, True, True)
    except Exception:
        return False


class _PostgresInteractionRecorder:
    def __init__(self, *, pool: AsyncConnectionPool, limits: InteractionStoreLimits, fallback_dir: Path | None, available: bool = True, secrets: tuple[bytes, ...] = ()) -> None:
        self._pool = pool
        self._limits = limits
        self._fallback_dir = fallback_dir
        self._available = available
        self._secrets = secrets

    async def _insert(self, record: InteractionRecord, raw: bytes) -> None:
        async with self._pool.connection(timeout=_WRITE_TIMEOUT) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute("SET LOCAL statement_timeout = '1500ms'")
                    await cursor.execute("SET LOCAL lock_timeout = '1500ms'")
                    await cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_DIAGNOSTIC_LOCK,))
                    await cursor.execute("SELECT content_hash,content_bytes FROM artifact_store.interaction_records WHERE record_id=%s", (record.record_id,))
                    existing = await cursor.fetchone()
                    if existing is not None:
                        if existing != (_hash(raw), raw):
                            raise ValueError("record_conflict")
                        return
                    await cursor.execute("SELECT COALESCE(SUM(size_bytes),0) FROM artifact_store.interaction_records")
                    if (await cursor.fetchone())[0] + len(raw) > self._limits.max_total_content_bytes:
                        raise ValueError("interaction_capacity_exhausted")
                    await cursor.execute("""INSERT INTO artifact_store.interaction_records
                        (record_id,run_id,trace_id,call_id,record_kind,captured_at,content_hash,content_bytes,size_bytes)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (record.record_id, record.run_id, record.trace_id, record.call_id, record.record_kind, record.captured_at, _hash(raw), raw, len(raw)))

    def _fallback(self, record: InteractionRecord, raw: bytes) -> bool:
        root = self._fallback_dir
        if root is None or not root.is_dir():
            return False
        directory = root / str(record.run_id)
        directory.mkdir(exist_ok=True)
        with (directory / "interactions.jsonl").open("ab") as stream:
            stream.write(raw + b"\n")
        return True

    async def append(self, record: InteractionRecord) -> InteractionWriteResult:
        deadline = asyncio.get_running_loop().time() + _WRITE_TIMEOUT
        code = "interaction_write_failed"
        raw = None
        try:
            record = InteractionRecord.model_validate_json(canonical_record(record))
            field_name = "body" if record.record_kind in {"request", "response"} else "feedback" if record.record_kind == "tool_result" else None
            if field_name is not None:
                import base64
                body = getattr(record.payload, field_name)
                retained = body.content.encode("utf8") if body.encoding == "utf8" else base64.b64decode(body.content, validate=True)
                safe = body_snapshot(retained, self._secrets)
                safe = safe.model_copy(update={"redacted": body.redacted or safe.redacted})
                record = record.model_copy(update={"payload": record.payload.model_copy(update={field_name: safe})})
            raw = canonical_record(record)
            if len(raw) > self._limits.max_record_bytes:
                code = "interaction_record_too_large"
            elif self._available:
                # Reserve a bounded fraction for the explicitly configured local fallback.
                async with asyncio.timeout_at(deadline - 0.2):
                    for attempt in (1, 2):
                        try:
                            await self._insert(record, raw)
                            return InteractionWriteResult(record_id=record.record_id, status="saved", code=None)
                        except (psycopg.OperationalError, psycopg.InterfaceError):
                            if attempt == 2:
                                raise
            else:
                code = "interaction_store_unavailable"
        except ValueError as error:
            # Fixed internal messages only. Never include driver/validation text.
            code = error.args[0] if error.args in (("record_conflict",), ("interaction_capacity_exhausted",)) else "interaction_record_invalid"
            if code == "record_conflict":
                return InteractionWriteResult(record_id=record.record_id, status="missing", code=code)
        except Exception:
            pass
        try:
            async with asyncio.timeout_at(deadline):
                if raw is not None and await asyncio.to_thread(self._fallback, record, raw):
                    return InteractionWriteResult(record_id=record.record_id, status="fallback", code=code)
        except Exception:
            pass
        return InteractionWriteResult(record_id=record.record_id, status="missing", code=code)

    async def read_run(self, run_id: UUID) -> tuple[InteractionRecord, ...]:
        try:
            if type(run_id) is not UUID or not self._available:
                raise InteractionStoreReadError()
            async with asyncio.timeout(_WRITE_TIMEOUT):
                async with self._pool.connection(timeout=_WRITE_TIMEOUT) as connection:
                    async with connection.transaction():
                        async with connection.cursor() as cursor:
                            await cursor.execute("SET LOCAL statement_timeout = '1500ms'")
                            await cursor.execute("""SELECT record_id,run_id,trace_id,call_id,record_kind,captured_at,content_hash,content_bytes,size_bytes
                                FROM artifact_store.interaction_records WHERE run_id=%s ORDER BY captured_at,record_id""", (run_id,))
                            records = tuple(_decode_row(row) for row in await cursor.fetchall())
                            if any(record.run_id != run_id for record in records):
                                raise InteractionStoreReadError()
                            return records
        except Exception:
            raise InteractionStoreReadError() from None
