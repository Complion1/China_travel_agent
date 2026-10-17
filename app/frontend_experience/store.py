"""Bounded append-only public records, independent of the business journal."""
from __future__ import annotations

import hashlib
import re
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from app.workflow_contracts import UtcClock
from .models import PublicProgressError, PublicProgressPage, PublicRecord, PublicRecordBody, PublicTrip, canonical


class PublicProgressStore:
    def __init__(self, *, pool: AsyncConnectionPool, clock: UtcClock):
        self._pool, self._clock = pool, clock

    @staticmethod
    def _identity(run_id, trace_id):
        if type(run_id) is not UUID or type(trace_id) is not UUID:
            raise PublicProgressError()

    @staticmethod
    def _record(row, *, run_id, trace_id):
        saved_run, saved_trace, sequence, occurred_at, content, digest = row
        content = bytes(content)
        if (saved_run != run_id or saved_trace != trace_id or not 1 <= len(content) <= 16384
                or hashlib.sha256(content).hexdigest() != digest):
            raise PublicProgressError()
        body = PublicRecordBody.model_validate_json(content, strict=True)
        if canonical(body) != content:
            raise PublicProgressError()
        return PublicRecord(sequence=sequence, occurred_at=occurred_at, body=body)

    async def _lock(self, connection, *, run_id, trace_id):
        capacity = await (await connection.execute(
            "SELECT record_count FROM frontend_experience.capacity WHERE singleton=true FOR UPDATE")).fetchone()
        if capacity is None or not 0 <= capacity[0] <= 16384:
            raise PublicProgressError()
        await connection.execute("INSERT INTO frontend_experience.progress_runs(run_id,trace_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (run_id, trace_id))
        run = await (await connection.execute(
            "SELECT trace_id,record_count,limited,settled FROM frontend_experience.progress_runs WHERE run_id=%s FOR UPDATE", (run_id,))).fetchone()
        if run is None or run[0] != trace_id or not 0 <= run[1] <= 512:
            raise PublicProgressError()
        return capacity[0], run

    async def append(self, *, run_id: UUID, trace_id: UUID, event_key: str, body: PublicRecordBody) -> PublicRecord | None:
        try:
            self._identity(run_id, trace_id)
            if type(event_key) is not str or re.fullmatch(r"[a-z0-9:_-]{1,256}", event_key) is None:
                raise PublicProgressError()
            body = PublicRecordBody.model_validate_json(body.model_dump_json(), strict=True)
            content = canonical(body)
            digest = hashlib.sha256(content).hexdigest()
            async with self._pool.connection() as connection, connection.transaction():
                capacity, run = await self._lock(connection, run_id=run_id, trace_id=trace_id)
                old = await (await connection.execute("SELECT run_id,trace_id,sequence,occurred_at,content_bytes,content_hash FROM frontend_experience.public_records WHERE run_id=%s AND event_key=%s", (run_id, event_key))).fetchone()
                if old is not None:
                    record = self._record(old, run_id=run_id, trace_id=trace_id)
                    if bytes(old[4]) == content:
                        return record
                    await connection.execute("UPDATE frontend_experience.progress_runs SET limited=true WHERE run_id=%s", (run_id,))
                    return None
                if run[3] or run[1] >= 512 or capacity >= 16384:
                    await connection.execute("UPDATE frontend_experience.progress_runs SET limited=true WHERE run_id=%s", (run_id,))
                    return None
                record = PublicRecord(sequence=run[1] + 1, occurred_at=self._clock.now(), body=body)
                await connection.execute("INSERT INTO frontend_experience.public_records(run_id,trace_id,sequence,event_key,occurred_at,content_bytes,content_hash) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (run_id, trace_id, record.sequence, event_key, record.occurred_at, content, digest))
                await connection.execute("UPDATE frontend_experience.progress_runs SET record_count=record_count+1 WHERE run_id=%s", (run_id,))
                await connection.execute("UPDATE frontend_experience.capacity SET record_count=record_count+1 WHERE singleton=true")
                return record
        except PublicProgressError:
            raise
        except Exception:
            raise PublicProgressError() from None

    async def _flag(self, *, run_id, trace_id, settled):
        try:
            self._identity(run_id, trace_id)
            async with self._pool.connection() as connection, connection.transaction():
                await self._lock(connection, run_id=run_id, trace_id=trace_id)
                if settled:
                    await connection.execute("UPDATE frontend_experience.progress_runs SET settled=true WHERE run_id=%s", (run_id,))
                else:
                    await connection.execute("UPDATE frontend_experience.progress_runs SET limited=true WHERE run_id=%s", (run_id,))
        except Exception:
            raise PublicProgressError() from None

    async def mark_limited(self, *, run_id: UUID, trace_id: UUID) -> None:
        await self._flag(run_id=run_id, trace_id=trace_id, settled=False)

    async def settle(self, *, run_id: UUID, trace_id: UUID) -> None:
        await self._flag(run_id=run_id, trace_id=trace_id, settled=True)

    async def _read_all(self, *, run_id, trace_id):
        self._identity(run_id, trace_id)
        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            run = await (await connection.execute("SELECT trace_id,record_count,limited,settled FROM frontend_experience.progress_runs WHERE run_id=%s", (run_id,))).fetchone()
            if run is None:
                return None, ()
            if run[0] != trace_id or not 0 <= run[1] <= 512:
                raise PublicProgressError()
            rows = await (await connection.execute("SELECT run_id,trace_id,sequence,occurred_at,content_bytes,content_hash FROM frontend_experience.public_records WHERE run_id=%s ORDER BY sequence LIMIT 513", (run_id,))).fetchall()
            records = tuple(self._record(row, run_id=run_id, trace_id=trace_id) for row in rows)
            if len(records) != run[1] or tuple(item.sequence for item in records) != tuple(range(1, run[1]+1)):
                raise PublicProgressError()
            return run, records

    async def read(self, *, run_id: UUID, trace_id: UUID, after: int = 0) -> PublicProgressPage:
        try:
            if type(after) is not int or not 0 <= after <= 512:
                raise PublicProgressError()
            run, records = await self._read_all(run_id=run_id, trace_id=trace_id)
            if run is None:
                return PublicProgressPage(run_id=run_id, items=(), latest_sequence=0, coverage="unavailable")
            items = tuple(item for item in records if item.sequence > after)[:50]
            return PublicProgressPage(run_id=run_id, items=items, latest_sequence=run[1],
                next_after=items[-1].sequence if items and items[-1].sequence < run[1] else None,
                coverage="limited" if run[2] else "recorded", settled=run[3])
        except Exception:
            raise PublicProgressError() from None

    async def trip(self, *, run_id: UUID, trace_id: UUID) -> PublicTrip | None:
        try:
            _, records = await self._read_all(run_id=run_id, trace_id=trace_id)
            return next((item.body.trip for item in reversed(records) if item.body.trip is not None), None)
        except Exception:
            raise PublicProgressError() from None
