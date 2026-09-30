from uuid import uuid4

import pytest

from app.artifact_persistence import (
    create_artifact_persistence_runtime, DEMO_INTERACTION_STORE_LIMITS,
    InteractionStoreLimits, InteractionStoreReadError,
)
from app.artifact_persistence.interaction_records import _PostgresInteractionRecorder, canonical_record
from app.workflow_contracts.artifacts.registry import ArtifactSchemaRegistry
from tests.unit.llm_runtime.test_interaction_recording import sample_record
from tests.integration.artifact_persistence.conftest import TEST_REPOSITORY_LIMITS


@pytest.mark.asyncio
async def test_global_capacity_concurrent_append_never_overcommits(artifact_pool, db_connection, tmp_path):
    import asyncio
    records = tuple(sample_record() for _ in range(3))
    per_record = max(len(canonical_record(record)) for record in records)
    with db_connection.transaction():
        with db_connection.cursor() as cursor:
            cursor.execute("TRUNCATE artifact_store.interaction_records")
    limits = InteractionStoreLimits(max_record_bytes=per_record, max_total_content_bytes=2 * per_record)
    recorder = _PostgresInteractionRecorder(pool=artifact_pool, limits=limits, fallback_dir=tmp_path)
    results = await asyncio.gather(*(recorder.append(record) for record in records))
    assert sorted(result.status for result in results) == ["fallback", "saved", "saved"]
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*),SUM(size_bytes) FROM artifact_store.interaction_records")
        count, size = cursor.fetchone()
        assert count == 2 and size <= limits.max_total_content_bytes


@pytest.mark.asyncio
async def test_append_replay_conflict_restart_read_and_independent_capacity(artifact_pool, db_connection, tmp_path):
    recorder = _PostgresInteractionRecorder(pool=artifact_pool, limits=DEMO_INTERACTION_STORE_LIMITS, fallback_dir=tmp_path)
    record = sample_record()
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT artifact_count,total_content_bytes FROM artifact_store.capacity_state")
        before = cursor.fetchall()
    assert (await recorder.append(record)).status == "saved"
    assert (await recorder.append(record)).status == "saved"
    conflict = record.model_copy(update={"trace_id": uuid4()})
    assert (await recorder.append(conflict)).code == "record_conflict"
    restarted = _PostgresInteractionRecorder(pool=artifact_pool, limits=DEMO_INTERACTION_STORE_LIMITS, fallback_dir=None)
    assert await restarted.read_run(record.run_id) == (record,)
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT artifact_count,total_content_bytes FROM artifact_store.capacity_state")
        assert cursor.fetchall() == before
        cursor.execute("SELECT COUNT(*) FROM artifact_store.interaction_records WHERE record_id=%s", (record.record_id,))
        assert cursor.fetchone() == (1,)
    limited = _PostgresInteractionRecorder(pool=artifact_pool, limits=InteractionStoreLimits(max_record_bytes=1, max_total_content_bytes=1), fallback_dir=tmp_path)
    second = sample_record()
    assert (await limited.append(second)).status == "fallback"
    assert (tmp_path / str(second.run_id) / "interactions.jsonl").read_bytes() == canonical_record(second) + b"\n"


@pytest.mark.asyncio
async def test_diagnostic_table_missing_business_runtime_still_opens(artifact_persistence_settings, db_connection, artifact_store_isolated, tmp_path):
    with db_connection.transaction():
        with db_connection.cursor() as cursor:
            cursor.execute("ALTER TABLE artifact_store.interaction_records RENAME TO hidden_interaction_records")
            # A non-diagnostic unexpected table must not be accepted either.
            cursor.execute("ALTER TABLE artifact_store.hidden_interaction_records SET SCHEMA public")
    try:
        async with await create_artifact_persistence_runtime(settings=artifact_persistence_settings, registry=ArtifactSchemaRegistry(()), limits=TEST_REPOSITORY_LIMITS, interaction_fallback_dir=tmp_path) as runtime:
            assert runtime.repository is not None
            record = sample_record()
            assert (await runtime.interaction_recorder.append(record)).status == "fallback"
            with pytest.raises(InteractionStoreReadError):
                await runtime.interaction_recorder.read_run(record.run_id)
    finally:
        with db_connection.transaction():
            with db_connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.hidden_interaction_records SET SCHEMA artifact_store")
                cursor.execute("ALTER TABLE artifact_store.hidden_interaction_records RENAME TO interaction_records")


@pytest.mark.asyncio
async def test_read_rejects_body_column_identity_corruption(artifact_pool, db_connection):
    recorder = _PostgresInteractionRecorder(pool=artifact_pool, limits=DEMO_INTERACTION_STORE_LIMITS, fallback_dir=None)
    record = sample_record()
    assert (await recorder.append(record)).status == "saved"
    with db_connection.transaction():
        with db_connection.cursor() as cursor:
            cursor.execute("UPDATE artifact_store.interaction_records SET trace_id=%s WHERE record_id=%s", (uuid4(), record.record_id))
    with pytest.raises(InteractionStoreReadError):
        await recorder.read_run(record.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('available', [True, False])
async def test_storage_redacts_exact_and_json_escaped_credentials_before_pg_or_fallback(artifact_pool, tmp_path, available):
    import json
    from app.llm_runtime import InteractionRecord
    credential = 'OFFLINE_DB_QUOTE_"_\\_SENTINEL'
    escaped = json.dumps(credential, ensure_ascii=True)[1:-1]
    recorder = _PostgresInteractionRecorder(pool=artifact_pool, limits=DEMO_INTERACTION_STORE_LIMITS,
        fallback_dir=tmp_path, available=available, secrets=(credential.encode(), escaped.encode()))
    record = sample_record((credential + ' ' + escaped).encode())
    result = await recorder.append(record)
    assert result.status == ('saved' if available else 'fallback')
    if available:
        retained = (await recorder.read_run(record.run_id))[0]
    else:
        retained = InteractionRecord.model_validate_json((tmp_path / str(record.run_id) / 'interactions.jsonl').read_bytes())
    assert retained.payload.body.redacted and retained.record_id == record.record_id
    assert credential not in retained.payload.body.content and escaped not in retained.payload.body.content
    assert 'SENTINEL' not in retained.payload.body.content
