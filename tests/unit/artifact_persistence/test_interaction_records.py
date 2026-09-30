import asyncio
import time

import pytest

from app.artifact_persistence.interaction_records import (
    _PostgresInteractionRecorder, DEMO_INTERACTION_STORE_LIMITS, canonical_record,
    InteractionStoreReadError,
)
from tests.unit.llm_runtime.test_interaction_recording import sample_record


@pytest.mark.asyncio
async def test_whole_write_timeout_fallback_and_external_cancel(monkeypatch, tmp_path):
    recorder = _PostgresInteractionRecorder(pool=object(), limits=DEMO_INTERACTION_STORE_LIMITS, fallback_dir=tmp_path)
    entered = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(recorder, "_insert", blocked)
    record = sample_record()
    start = time.monotonic()
    result = await recorder.append(record)
    assert result.status == "fallback" and time.monotonic() - start < 2.5
    assert (tmp_path / str(record.run_id) / "interactions.jsonl").read_bytes() == canonical_record(record) + b"\n"
    task = asyncio.create_task(recorder.append(record))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_unavailable_no_fallback_explicit_missing_and_read_error():
    recorder = _PostgresInteractionRecorder(pool=object(), limits=DEMO_INTERACTION_STORE_LIMITS, fallback_dir=None, available=False)
    record = sample_record()
    assert (await recorder.append(record)).status == "missing"
    with pytest.raises(InteractionStoreReadError, match="^interaction_store_read_failed$"):
        await recorder.read_run(record.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("database_wait", [False, True])
async def test_slow_fallback_is_within_whole_append_deadline(monkeypatch, tmp_path, database_wait):
    recorder = _PostgresInteractionRecorder(pool=object(), limits=DEMO_INTERACTION_STORE_LIMITS, fallback_dir=tmp_path, available=database_wait)

    async def blocked_database(*args):
        await asyncio.sleep(10)

    def blocked_disk(*args):
        time.sleep(2.2)
        return False

    monkeypatch.setattr(recorder, "_insert", blocked_database)
    monkeypatch.setattr(recorder, "_fallback", blocked_disk)
    start = time.monotonic()
    assert (await recorder.append(sample_record())).status == "missing"
    assert time.monotonic() - start < 2.2


@pytest.mark.asyncio
async def test_same_id_retry_shares_deadline_and_conflict_is_never_overwritten(monkeypatch, tmp_path):
    import psycopg
    recorder = _PostgresInteractionRecorder(pool=object(), limits=DEMO_INTERACTION_STORE_LIMITS, fallback_dir=tmp_path)
    calls = []

    async def failed(record, raw):
        calls.append(record.record_id)
        await asyncio.sleep(1.2)
        raise psycopg.OperationalError()

    monkeypatch.setattr(recorder, "_insert", failed)
    record = sample_record()
    start = time.monotonic()
    assert (await recorder.append(record)).status == "fallback"
    assert calls == [record.record_id, record.record_id] and time.monotonic() - start < 2.5

    async def conflict(*args):
        raise ValueError("record_conflict")

    monkeypatch.setattr(recorder, "_insert", conflict)
    assert (await recorder.append(record)).code == "record_conflict"
    assert len((tmp_path / str(record.run_id) / "interactions.jsonl").read_bytes().splitlines()) == 1
