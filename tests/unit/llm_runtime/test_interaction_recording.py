from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.llm_runtime import InteractionRecord, interaction_call, interaction_run_context
from app.llm_runtime.interaction_recording import (
    _MemoryRecorder, _resolve_call, _run_context, body_snapshot, raw_usage,
    RequestPayload, missing_usage, RawUsage,
)
from app.llm_runtime import ModelRole


def sample_record(raw=b"fixture-only"):
    return InteractionRecord(schema_version="1.0", record_id=uuid4(), run_id=uuid4(), trace_id=uuid4(), call_id=uuid4(),
        model_operation_id=None, captured_at=datetime.now(timezone.utc), role=ModelRole.ITINERARY_PLANNING, purpose="planning",
        logical_call_index=0, provider_attempt=1, request_stage="transport", budget_category=None,
        record_kind="request", payload=RequestPayload(kind="request", body=body_snapshot(raw)))


def test_wire_bytes_redaction_base64_integrity_and_repr():
    body = body_snapshot(b"\xffSECRET_SENTINEL", (b"SECRET_SENTINEL",))
    assert body.encoding == "base64" and body.redacted
    assert "SECRET_SENTINEL" not in body.content
    record = sample_record(b"FIXTURE_PRIVATE_BODY")
    assert "FIXTURE_PRIVATE_BODY" not in repr(record)
    assert InteractionRecord.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValidationError):
        type(body).model_validate_json(body.model_dump_json().replace(body.content_hash, "sha256:" + "0" * 64))
    with pytest.raises(ValidationError):
        InteractionRecord.model_validate_json(record.model_dump_json(exclude={"budget_category"}))
    with pytest.raises(ValidationError):
        InteractionRecord.model_validate_json(record.model_dump_json().replace('"provider_attempt":1', '"provider_attempt":true'))


def test_usage_missing_is_not_normalized_zero_or_inferred():
    usage = raw_usage(b'{"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15,"prompt_cache_hit_tokens":8}}')
    assert usage.cache_miss_tokens is None and usage.reasoning_tokens is None and not usage.complete
    assert raw_usage(b"not JSON") == missing_usage()
    assert raw_usage(b'{"usage":{"prompt_tokens":10,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":2,"completion_tokens":5,"reasoning_tokens":3,"total_tokens":15}}').complete
    with pytest.raises(ValidationError):
        RawUsage.model_validate_json(missing_usage().model_dump_json().replace('"complete":false', '"complete":true'))


@pytest.mark.asyncio
async def test_context_scopes_restore_on_failure_and_siblings_do_not_mix():
    import asyncio
    recorder = _MemoryRecorder()
    ids = [uuid4(), uuid4()]

    async def sibling(run_id):
        with interaction_run_context(run_id=run_id, trace_id=uuid4(), recorder=recorder):
            with interaction_call("planning") as identity:
                await asyncio.sleep(0)
                assert _run_context.get().run_id == run_id
                assert _resolve_call().identity == identity
            assert _resolve_call() is None
        assert _run_context.get() is None

    await asyncio.gather(*(sibling(run_id) for run_id in ids))
    with pytest.raises(RuntimeError):
        with interaction_call("extract"):
            raise RuntimeError("fixture")
    assert _resolve_call() is None


def test_payload_category_and_secret_context_repr_are_safe():
    from app.llm_runtime.interaction_recording import ValidationPayload
    record = sample_record().model_copy(update={"role": ModelRole.REQUIREMENT_EXTRACTION, "purpose": "extract", "request_stage": None,
        "provider_attempt": None, "record_kind": "validation", "payload": ValidationPayload(kind="validation", stage="controller", passed=True, code=None, field=None, budget_category="supplement_request")})
    with pytest.raises(ValidationError):
        InteractionRecord.model_validate_json(record.model_dump_json())
    with interaction_run_context(run_id=uuid4(), trace_id=uuid4(), recorder=_MemoryRecorder(), secrets=(b"SECRET_SENTINEL",)):
        assert "SECRET_SENTINEL" not in repr(_run_context.get())
