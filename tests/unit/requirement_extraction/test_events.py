from uuid import UUID

import pytest
from pydantic import ValidationError

from app.llm_runtime import (
    InvocationMetadata,
    LLMErrorCode,
    LLMRuntimeError,
    LLMUsage,
    ModelRole,
)
from app.requirement_extraction.events import (
    ExtractionInvocationEvent,
    NoOpExtractionInvocationSink,
)


TURN_ID = UUID("00000000-0000-0000-0000-000000000001")


def metadata() -> InvocationMetadata:
    return InvocationMetadata(
        role=ModelRole.REQUIREMENT_EXTRACTION,
        model_name="deepseek-v4-flash",
        finish_reason="stop",
        provider_attempts=1,
        possible_duplicate=False,
        usage=LLMUsage(
            prompt_tokens=2,
            completion_tokens=1,
            total_tokens=3,
            reasoning_tokens=0,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=2,
        ),
    )


def runtime_error() -> LLMRuntimeError:
    return LLMRuntimeError(
        code=LLMErrorCode.TIMEOUT,
        role=ModelRole.REQUIREMENT_EXTRACTION,
        retryable=True,
        possible_duplicate=True,
    )


def event(**overrides) -> ExtractionInvocationEvent:
    values = dict(
        turn_id=TURN_ID,
        prompt_version="requirement-extraction-v1",
        attempt_kind="extract",
        outcome_kind="succeeded",
        semantic_call_number=1,
        invocation_metadata=metadata(),
        runtime_error=None,
    )
    values.update(overrides)
    return ExtractionInvocationEvent(**values)


def test_success_requires_metadata_only():
    assert event().invocation_metadata == metadata()


def test_repair_required_has_no_runtime_fields():
    assert event(outcome_kind="repair_required", invocation_metadata=None)


def test_runtime_failure_requires_error_only():
    assert event(outcome_kind="runtime_failure", invocation_metadata=None, runtime_error=runtime_error())


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome_kind": "succeeded", "invocation_metadata": None},
        {"outcome_kind": "succeeded", "runtime_error": runtime_error()},
        {"outcome_kind": "repair_required", "invocation_metadata": metadata()},
        {"outcome_kind": "runtime_failure", "invocation_metadata": None},
        {"attempt_kind": "repair", "semantic_call_number": 1},
    ],
)
def test_invalid_event_combinations_fail(overrides):
    with pytest.raises(ValidationError):
        event(**overrides)


def test_event_forbids_sensitive_and_extra_fields():
    with pytest.raises(ValidationError):
        event(user_message="secret")
    assert "prompt" not in ExtractionInvocationEvent.model_fields
    assert "reasoning" not in ExtractionInvocationEvent.model_fields


def test_noop_sink_returns_none():
    assert NoOpExtractionInvocationSink().accept(event()) is None
