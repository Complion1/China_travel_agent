"""Fixed queries for M14 metadata; borrowed pool, short transactions, no business SQL."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import hashlib
import hmac
import re
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg_pool import AsyncConnectionPool

from app.workflow_contracts import RunSummary, UtcClock
from .errors import (WebApplicationError, WebApplicationUsageError,
    WebApplicationDependencyError, WebApplicationConflictError)
from .models import WebVisitorContext, WebCommandReceipt, fresh, utc, MESSAGES
from .security import (cookie_hash, new_cookie, csrf_token, check_csrf, body_fingerprint,
    encrypt_pause, decrypt_pause, canonical_json, make_cursor, read_cursor)
from .settings import WebApplicationSettings

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_ADMISSION_LOCK = 18951345809454158
_CONTROL_LOCK = 18951345809454159
_KINDS = {"start", "clarify", "confirm", "hotel", "cancel", "recover"}
_PAUSE_KINDS = {"clarify", "confirm", "hotel"}


class DecisionError(WebApplicationError):
    """Internal finite decision; never carries a body, SQL, credential or exception."""
    __slots__ = ("code", "retry_after_seconds")

    def __init__(self, code: str, retry_after_seconds: int | None = None):
        if code not in MESSAGES or (retry_after_seconds is not None and
                (type(retry_after_seconds) is not int or retry_after_seconds < 1)):
            raise WebApplicationUsageError()
        super().__init__()
        self.code, self.retry_after_seconds = code, retry_after_seconds


@dataclass(frozen=True, slots=True)
class RequestRecord:
    visitor_hash: str
    request_id: UUID
    run_id: UUID
    kind: str
    fingerprint: str
    generation: int | None
    status: str
    reason: str | None

    def receipt(self) -> WebCommandReceipt:
        return WebCommandReceipt(schema_version="1.0", request_id=self.request_id,
            run_id=None if self.kind == "start" and self.status == "rejected" else self.run_id,
            status="confirming" if self.status == "claimed" else self.status, reason=self.reason)


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    global_remaining: int
    visitor_remaining: int
    ip_remaining: int
    retry_after_seconds: int | None


@dataclass(frozen=True, slots=True, repr=False)
class SummaryReceipt:
    run_id: UUID
    trace_id: UUID
    terminal_code: str
    availability: str
    content_hash: str | None
    content_bytes: bytes | None
    size_bytes: int | None

    @classmethod
    def freeze(cls, *, run_id: UUID, trace_id: UUID, terminal_code: str,
               summary: RunSummary | None) -> SummaryReceipt:
        _uuid(run_id); _uuid(trace_id)
        if type(terminal_code) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", terminal_code):
            raise WebApplicationUsageError()
        if summary is None:
            return cls(run_id, trace_id, terminal_code, "process_history_unavailable", None, None, None)
        try:
            copy = fresh(summary, RunSummary)
            if (copy.run_id, copy.trace_id, copy.terminal_code) != (run_id, trace_id, terminal_code):
                raise ValueError()
            payload = canonical_json(copy.model_dump(mode="json"))
            if not 1 <= len(payload) <= 1048576:
                raise ValueError()
            return cls(run_id, trace_id, terminal_code, "available",
                "sha256:" + hashlib.sha256(payload).hexdigest(), payload, len(payload))
        except Exception:
            raise WebApplicationUsageError() from None

    def summary(self) -> RunSummary | None:
        try:
            if self.availability == "process_history_unavailable":
                if any(value is not None for value in (self.content_hash, self.content_bytes, self.size_bytes)):
                    raise ValueError()
                return None
            if (self.availability != "available" or type(self.content_bytes) is not bytes or
                    type(self.size_bytes) is not int or self.size_bytes != len(self.content_bytes) or
                    not 1 <= self.size_bytes <= 1048576 or
                    self.content_hash != "sha256:" + hashlib.sha256(self.content_bytes).hexdigest()):
                raise ValueError()
            summary = RunSummary.model_validate_json(self.content_bytes, strict=True)
            if (summary.run_id, summary.trace_id, summary.terminal_code) != (self.run_id, self.trace_id, self.terminal_code):
                raise ValueError()
            if canonical_json(summary.model_dump(mode="json")) != self.content_bytes:
                raise ValueError()
            return summary
        except Exception:
            raise WebApplicationDependencyError() from None


def _uuid(value):
    if type(value) is not UUID:
        raise WebApplicationUsageError()


def _fingerprint(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise WebApplicationUsageError()


def _record(row) -> RequestRecord | None:
    if row is None:
        return None
    visitor, request, run, kind, fingerprint, generation, status, reason = row
    _fingerprint(visitor); _fingerprint(fingerprint); _uuid(request); _uuid(run)
    if (kind not in _KINDS or status not in {"claimed", "dispatched", "rejected", "uncertain"} or
            ((kind in _PAUSE_KINDS) != (generation is not None)) or
            (generation is not None and (type(generation) is not int or not 0 <= generation <= 2**53-1)) or
            ((status in {"rejected", "uncertain"}) != (reason is not None)) or
            (reason is not None and reason not in MESSAGES)):
        raise WebApplicationDependencyError()
    return RequestRecord(*row)


class WebMetadataStore:
    def __init__(self, *, settings: WebApplicationSettings, pool: AsyncConnectionPool,
                 key: bytes, clock: UtcClock):
        try:
            self._settings = fresh(settings, WebApplicationSettings)
            if type(key) is not bytes or len(key) != 32 or not callable(clock.now):
                raise ValueError()
            self._pool, self._key, self._clock = pool, key, clock
            self._loop = asyncio.get_running_loop()
            self._guard = None
            self._now()
        except Exception:
            raise WebApplicationUsageError() from None

    def __repr__(self):
        return "WebMetadataStore()"

    def _ensure(self):
        if asyncio.get_running_loop() is not self._loop:
            raise WebApplicationUsageError()

    def _now(self) -> datetime:
        value = self._clock.now()
        if type(value) is not datetime:
            raise WebApplicationUsageError()
        try:
            return utc(value)
        except Exception:
            raise WebApplicationUsageError() from None

    @asynccontextmanager
    async def _connection(self):
        self._ensure()
        try:
            async with self._pool.connection(timeout=2) as connection:
                yield connection
        except WebApplicationError:
            raise
        except Exception:
            raise WebApplicationDependencyError() from None

    def _set_guard(self, guard):
        self._ensure()
        if self._guard is not None or not callable(guard):
            raise WebApplicationUsageError()
        self._guard = guard

    async def healthy(self) -> None:
        self._ensure()
        if self._guard is not None:
            await self._guard()
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT 1")
            if await cursor.fetchone() != (1,):
                raise WebApplicationDependencyError()

    async def bootstrap(self, *, cookie: str | None) -> tuple[WebVisitorContext, str | None]:
        owner = await self.validate_cookie(cookie=cookie)
        if owner is not None:
            return owner, None
        raw = new_cookie()
        now = self._now()
        owner = WebVisitorContext(schema_version="1.0", visitor_hash=cookie_hash(raw),
            expires_at=now + timedelta(seconds=2592000))
        async with self._connection() as connection:
            await connection.execute("INSERT INTO web_application.visitors(visitor_hash,issued_at,expires_at) VALUES (%s,%s,%s)",
                (owner.visitor_hash, now, owner.expires_at))
        return owner, raw

    async def validate_cookie(self, *, cookie: str | None) -> WebVisitorContext | None:
        self._ensure()
        if cookie is None:
            return None
        try:
            digest = cookie_hash(cookie)
        except (WebApplicationUsageError, TypeError, AttributeError):
            return None
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT expires_at FROM web_application.visitors WHERE visitor_hash=%s AND expires_at>%s", (digest, self._now()))
            row = await cursor.fetchone()
        return None if row is None else WebVisitorContext(schema_version="1.0", visitor_hash=digest, expires_at=utc(row[0]))

    async def validate_owner(self, *, owner: WebVisitorContext) -> WebVisitorContext:
        try:
            owner = fresh(owner, WebVisitorContext)
        except Exception:
            raise WebApplicationUsageError() from None
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT expires_at FROM web_application.visitors WHERE visitor_hash=%s AND expires_at>%s", (owner.visitor_hash, self._now()))
            row = await cursor.fetchone()
        if row is None or utc(row[0]) != owner.expires_at:
            raise DecisionError("csrf_rejected")
        return owner

    async def owns(self, *, owner: WebVisitorContext, run_id: UUID) -> None:
        owner = await self.validate_owner(owner=owner); _uuid(run_id)
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT 1 FROM web_application.run_owners WHERE visitor_hash=%s AND run_id=%s", (owner.visitor_hash, run_id))
            if await cursor.fetchone() != (1,):
                raise DecisionError("not_found")

    def csrf(self, *, owner: WebVisitorContext) -> str:
        self._ensure()
        return csrf_token(self._key, fresh(owner, WebVisitorContext).visitor_hash)

    def verify_csrf(self, *, owner: WebVisitorContext, supplied: str) -> bool:
        self._ensure()
        return check_csrf(self._key, fresh(owner, WebVisitorContext).visitor_hash, supplied)

    def fingerprint(self, *, route, run_id, generation, body) -> str:
        self._ensure()
        return body_fingerprint(self._key, route=route, run_id=run_id, generation=generation, body=body)

    async def _request(self, connection, visitor, request_id):
        cursor = await connection.execute("SELECT visitor_hash,request_id,run_id,kind,body_fingerprint,pause_generation,status,reason FROM web_application.requests WHERE visitor_hash=%s AND request_id=%s", (visitor, request_id))
        return _record(await cursor.fetchone())

    async def get_request(self, *, owner: WebVisitorContext, request_id: UUID) -> RequestRecord | None:
        owner = await self.validate_owner(owner=owner); _uuid(request_id)
        async with self._connection() as connection:
            return await self._request(connection, owner.visitor_hash, request_id)

    @staticmethod
    def compare(record: RequestRecord, *, kind, fingerprint, run_id=None) -> None:
        if record.kind != kind or not hmac.compare_digest(record.fingerprint, fingerprint) or (run_id is not None and run_id != record.run_id):
            raise DecisionError("request_conflict")

    async def _quota(self, connection, visitor, ip, now) -> QuotaSnapshot:
        day = now.astimezone(_SHANGHAI).date()
        cursor = await connection.execute("SELECT count(*) FILTER (WHERE day_key=%s),count(*) FILTER (WHERE day_key=%s AND visitor_hash=%s) FROM web_application.admissions WHERE status IN ('reserved','counted')", (day, day, visitor))
        global_count, visitor_count = await cursor.fetchone()
        cursor = await connection.execute("SELECT reserved_at FROM web_application.admissions WHERE status IN ('reserved','counted') AND ip_hash=%s AND reserved_at>%s AND reserved_at<=%s ORDER BY reserved_at,run_id", (ip, now - timedelta(hours=1), now))
        ip_times = [utc(row[0]) for row in await cursor.fetchall()]
        limits = self._settings
        remaining = (max(0, limits.daily_global_limit - global_count), max(0, limits.daily_visitor_limit - visitor_count), max(0, limits.hourly_ip_limit - len(ip_times)))
        deadlines = []
        if not remaining[0] or not remaining[1]:
            deadlines.append(datetime.combine(day + timedelta(days=1), time.min, _SHANGHAI).astimezone(timezone.utc))
        if not remaining[2]:
            deadlines.append(ip_times[len(ip_times) - limits.hourly_ip_limit] + timedelta(hours=1))
        retry = max(1, int((max(deadlines) - now).total_seconds() + 0.999999)) if deadlines else None
        return QuotaSnapshot(*remaining, retry)

    async def quota(self, *, owner, ip) -> QuotaSnapshot:
        owner = await self.validate_owner(owner=owner); _fingerprint(ip)
        async with self._connection() as connection:
            return await self._quota(connection, owner.visitor_hash, ip, self._now())

    async def reserve(self, *, owner, request_id, run_id, fingerprint, ip) -> tuple[RequestRecord, bool]:
        owner = await self.validate_owner(owner=owner)
        _uuid(request_id); _uuid(run_id); _fingerprint(fingerprint); _fingerprint(ip)
        async with self._connection() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(%s)", (_ADMISSION_LOCK,))
                record = await self._request(connection, owner.visitor_hash, request_id)
                if record is not None:
                    self.compare(record, kind="start", fingerprint=fingerprint)
                    return record, False
                now = self._now()
                cursor = await connection.execute("SELECT 1 FROM web_application.visitors WHERE visitor_hash=%s AND expires_at>%s", (owner.visitor_hash, now))
                if await cursor.fetchone() != (1,):
                    raise DecisionError("csrf_rejected")
                quota = await self._quota(connection, owner.visitor_hash, ip, now)
                if quota.retry_after_seconds is not None:
                    raise DecisionError("quota_exceeded", quota.retry_after_seconds)
                await connection.execute("INSERT INTO web_application.run_owners(run_id,visitor_hash,bound_at) VALUES (%s,%s,%s)", (run_id, owner.visitor_hash, now))
                await connection.execute("INSERT INTO web_application.requests(visitor_hash,request_id,run_id,kind,body_fingerprint,pause_generation,status,reason,created_at) VALUES (%s,%s,%s,'start',%s,NULL,'claimed',NULL,%s)", (owner.visitor_hash, request_id, run_id, fingerprint, now))
                await connection.execute("INSERT INTO web_application.admissions(run_id,visitor_hash,ip_hash,reserved_at,day_key,status) VALUES (%s,%s,%s,%s,%s,'reserved')", (run_id, owner.visitor_hash, ip, now, now.astimezone(_SHANGHAI).date()))
                return RequestRecord(owner.visitor_hash, request_id, run_id, "start", fingerprint, None, "claimed", None), True

    async def claim_control(self, *, owner, request_id, run_id, kind, fingerprint, generation) -> tuple[RequestRecord, bool]:
        await self.owns(owner=owner, run_id=run_id)
        _uuid(request_id); _fingerprint(fingerprint)
        if kind not in _KINDS - {"start"} or ((kind in _PAUSE_KINDS) != (generation is not None)) or (generation is not None and (type(generation) is not int or not 0 <= generation <= 2**53-1)):
            raise WebApplicationUsageError()
        async with self._connection() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(%s)", (_CONTROL_LOCK,))
                record = await self._request(connection, owner.visitor_hash, request_id)
                if record is not None:
                    self.compare(record, kind=kind, fingerprint=fingerprint, run_id=run_id)
                    return record, False
                if generation is not None:
                    cursor = await connection.execute("SELECT 1 FROM web_application.requests WHERE run_id=%s AND pause_generation=%s AND kind IN ('clarify','confirm','hotel') AND status IN ('claimed','dispatched','uncertain')", (run_id, generation))
                    if await cursor.fetchone() is not None:
                        raise DecisionError("stale_pause")
                await connection.execute("INSERT INTO web_application.requests(visitor_hash,request_id,run_id,kind,body_fingerprint,pause_generation,status,reason,created_at) VALUES (%s,%s,%s,%s,%s,%s,'claimed',NULL,%s)", (owner.visitor_hash, request_id, run_id, kind, fingerprint, generation, self._now()))
                return RequestRecord(owner.visitor_hash, request_id, run_id, kind, fingerprint, generation, "claimed", None), True

    async def mark_request(self, *, record, status, reason=None, release_admission=False) -> RequestRecord:
        if status not in {"dispatched", "rejected", "uncertain"} or ((status in {"rejected", "uncertain"}) != (reason is not None)) or (reason is not None and reason not in MESSAGES) or (release_admission and (record.kind != "start" or status != "rejected")):
            raise WebApplicationUsageError()
        async with self._connection() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(%s)", (_ADMISSION_LOCK if record.kind == "start" else _CONTROL_LOCK,))
                current = await self._request(connection, record.visitor_hash, record.request_id)
                if current is None or current.run_id != record.run_id or current.fingerprint != record.fingerprint:
                    raise WebApplicationConflictError()
                if current.status == "dispatched" and (record.kind == "start" or status == "dispatched"):
                    return current
                if current.status == "rejected" and status != "rejected":
                    raise WebApplicationConflictError()
                if current.status == "rejected" or (current.status == status and current.reason == reason):
                    return current
                await connection.execute("UPDATE web_application.requests SET status=%s,reason=%s WHERE visitor_hash=%s AND request_id=%s", (status, reason, record.visitor_hash, record.request_id))
                if record.kind == "start" and (status == "dispatched" or release_admission):
                    await connection.execute("UPDATE web_application.admissions SET status=%s WHERE run_id=%s AND status='reserved'", ("counted" if status == "dispatched" else "released", record.run_id))
                return RequestRecord(current.visitor_hash, current.request_id, current.run_id, current.kind, current.fingerprint, current.generation, status, reason)

    async def unconfirmed(self, *, after=None) -> tuple[RequestRecord, ...]:
        async with self._connection() as connection:
            cursor = await connection.execute("SELECT visitor_hash,request_id,run_id,kind,body_fingerprint,pause_generation,status,reason FROM web_application.requests WHERE kind='start' AND status IN ('claimed','uncertain') AND (%s::uuid IS NULL OR request_id>%s::uuid) ORDER BY request_id LIMIT 20", (after, after))
            return tuple(_record(row) for row in await cursor.fetchall())

    async def history(self, *, owner, cursor=None):
        owner = await self.validate_owner(owner=owner)
        anchor = (None, None) if cursor is None else read_cursor(self._key, cursor=cursor, visitor_hash=owner.visitor_hash)
        async with self._connection() as connection:
            result = await connection.execute("SELECT bound_at,run_id FROM web_application.run_owners WHERE visitor_hash=%s AND (%s::timestamptz IS NULL OR (bound_at,run_id)<(%s::timestamptz,%s::uuid)) ORDER BY bound_at DESC,run_id DESC LIMIT 21", (owner.visitor_hash, anchor[0], anchor[0], anchor[1]))
            rows = await result.fetchall()
        page = tuple((utc(when), run) for when, run in rows[:20])
        next_cursor = make_cursor(self._key, visitor_hash=owner.visitor_hash, bound_at=page[-1][0], run_id=page[-1][1]) if len(rows) > 20 else None
        return page, next_cursor

    async def save_pause(self, *, run_id, pause_generation, contract_digest, pause_token):
        nonce, ciphertext = encrypt_pause(self._key, run_id=run_id, pause_generation=pause_generation,
            contract_digest=contract_digest, pause_token=pause_token)
        async with self._connection() as connection:
            async with connection.transaction():
                await connection.execute("INSERT INTO web_application.pause_credentials(run_id,pause_generation,contract_digest,cipher_version,nonce,ciphertext,saved_at) VALUES (%s,%s,%s,'aesgcm-v1',%s,%s,%s) ON CONFLICT(run_id,pause_generation) DO NOTHING", (run_id, pause_generation, contract_digest, nonce, ciphertext, self._now()))
                token = await self._pause(connection, run_id, pause_generation, contract_digest)
                if not hmac.compare_digest(token.encode("utf-8"), pause_token.encode("utf-8")):
                    raise WebApplicationConflictError()

    async def _pause(self, connection, run_id, generation, digest):
        result = await connection.execute("SELECT contract_digest,cipher_version,nonce,ciphertext FROM web_application.pause_credentials WHERE run_id=%s AND pause_generation=%s", (run_id, generation))
        row = await result.fetchone()
        if row is None or row[0] != digest or row[1] != "aesgcm-v1":
            raise DecisionError("pause_unavailable")
        try:
            return decrypt_pause(self._key, run_id=run_id, pause_generation=generation,
                contract_digest=digest, nonce=bytes(row[2]), ciphertext=bytes(row[3]))
        except WebApplicationUsageError:
            raise DecisionError("pause_unavailable") from None

    async def read_pause(self, *, run_id, generation, digest):
        _uuid(run_id)
        async with self._connection() as connection:
            return await self._pause(connection, run_id, generation, digest)

    async def get_summary(self, *, run_id, trace_id, terminal_code) -> SummaryReceipt | None:
        _uuid(run_id); _uuid(trace_id)
        async with self._connection() as connection:
            result = await connection.execute("SELECT run_id,trace_id,terminal_code,availability,content_hash,content_bytes,size_bytes FROM web_application.run_summaries WHERE run_id=%s", (run_id,))
            row = await result.fetchone()
        if row is None:
            return None
        receipt = SummaryReceipt(*row)
        if receipt.trace_id != trace_id or receipt.terminal_code != terminal_code:
            raise WebApplicationDependencyError()
        receipt.summary()
        return receipt

    async def save_summary(self, *, receipt: SummaryReceipt) -> None:
        if type(receipt) is not SummaryReceipt:
            raise WebApplicationUsageError()
        receipt.summary()
        async with self._connection() as connection:
            async with connection.transaction():
                await connection.execute("INSERT INTO web_application.run_summaries(run_id,trace_id,terminal_code,availability,content_hash,content_bytes,size_bytes,saved_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id) DO NOTHING", (receipt.run_id, receipt.trace_id, receipt.terminal_code, receipt.availability, receipt.content_hash, receipt.content_bytes, receipt.size_bytes, self._now()))
                result = await connection.execute("SELECT run_id,trace_id,terminal_code,availability,content_hash,content_bytes,size_bytes FROM web_application.run_summaries WHERE run_id=%s", (receipt.run_id,))
                existing = SummaryReceipt(*(await result.fetchone()))
                existing.summary()
                if existing != receipt:
                    raise WebApplicationConflictError()
