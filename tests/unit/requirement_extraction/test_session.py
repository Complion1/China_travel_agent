import asyncio
from datetime import date
from uuid import UUID

import pytest
from pydantic import BaseModel

from app.llm_runtime import (
    InvocationMetadata,
    LLMErrorCode,
    LLMInvocationFailure,
    LLMRuntimeError,
    LLMUsage,
    ModelRole,
    StructuredInvocationSuccess,
)
from app.requirement_extraction.events import ExtractionInvocationEvent
from app.requirement_extraction.session import (
    DeepSeekRequirementExtractionSession,
    RequirementExtractionRuntimeError,
)
from app.requirements import (
    ExtractionAttemptSucceeded,
    ExtractionInput,
    ExtractionRepairFeedback,
    ExtractionRepairRequired,
    RequirementIntent,
    StrictRequirementPatchDTO,
    TravelRequirement,
)


TURN_ID = UUID("00000000-0000-0000-0000-000000000001")


def strict_dto(**overrides) -> StrictRequirementPatchDTO:
    payload = {name: None for name in StrictRequirementPatchDTO.model_fields}
    payload["is_correction"] = False
    payload["evidence"] = []
    payload.update(overrides)
    return StrictRequirementPatchDTO(**payload)


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


def success(value: BaseModel | None = None) -> StructuredInvocationSuccess:
    return StructuredInvocationSuccess(value=value or strict_dto(), metadata=metadata())


def failure(code: LLMErrorCode) -> LLMInvocationFailure:
    return LLMInvocationFailure(
        error=LLMRuntimeError(
            code=code,
            role=ModelRole.REQUIREMENT_EXTRACTION,
            retryable=False,
            possible_duplicate=False,
        )
    )


class ScriptedModelOperation:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def ainvoke_structured(self, prompt, schema):
        self.calls.append((prompt, schema))
        if not self.outcomes:
            raise AssertionError("unexpected_semantic_call")
        return self.outcomes.pop(0)

    def invoke_structured(self, prompt, schema):
        raise AssertionError("sync invocation is forbidden")


class RecordingSink:
    def __init__(self):
        self.events: list[ExtractionInvocationEvent] = []

    def accept(self, event: ExtractionInvocationEvent) -> None:
        self.events.append(event)


def request() -> ExtractionInput:
    return ExtractionInput(
        turn_id=TURN_ID,
        user_message="我想去杭州",
        session_date=date(2026, 7, 14),
        timezone="Asia/Shanghai",
        current_requirement=TravelRequirement(),
        intent=RequirementIntent.INITIAL,
    )


def session(operation, sink=None):
    return DeepSeekRequirementExtractionSession(
        request=request(),
        operation=operation,
        sink=sink or RecordingSink(),
        extract_prompt="extract prompt",
        repair_prompt="repair prompt",
    )


async def test_extract_success_returns_typed_attempt_and_safe_event():
    operation = ScriptedModelOperation(success())
    sink = RecordingSink()
    result = await session(operation, sink).extract()
    assert isinstance(result, ExtractionAttemptSucceeded)
    assert operation.calls[0][1] is StrictRequirementPatchDTO
    assert sink.events[0].outcome_kind == "succeeded"
    assert sink.events[0].semantic_call_number == 1


async def test_structured_invalid_returns_repair_required():
    sink = RecordingSink()
    result = await session(ScriptedModelOperation(failure(LLMErrorCode.STRUCTURED_OUTPUT_INVALID)), sink).extract()
    assert isinstance(result, ExtractionRepairRequired)
    assert result.feedback.code == "structured_output_invalid"
    assert sink.events[0].outcome_kind == "repair_required"


@pytest.mark.parametrize(
    "code",
    tuple(code for code in LLMErrorCode if code is not LLMErrorCode.STRUCTURED_OUTPUT_INVALID),
)
async def test_all_other_runtime_codes_raise_lossless_wrapper(code):
    sink = RecordingSink()
    value = failure(code).error
    with pytest.raises(RequirementExtractionRuntimeError) as caught:
        await session(ScriptedModelOperation(LLMInvocationFailure(error=value)), sink).extract()
    assert caught.value.runtime_error is value
    assert str(caught.value) == code.value
    assert sink.events[0].runtime_error is value


async def test_repair_uses_same_operation_and_second_call_number():
    operation = ScriptedModelOperation(
        failure(LLMErrorCode.STRUCTURED_OUTPUT_INVALID),
        success(),
    )
    sink = RecordingSink()
    value = session(operation, sink)
    first = await value.extract()
    result = await value.repair(first.feedback)
    assert isinstance(result, ExtractionAttemptSucceeded)
    assert len(operation.calls) == 2
    assert sink.events[1].attempt_kind == "repair"
    assert sink.events[1].semantic_call_number == 2
    assert "repair_feedback" in operation.calls[1][0].user_text


async def test_repair_can_return_repair_required_but_cannot_be_called_third_time():
    operation = ScriptedModelOperation(
        failure(LLMErrorCode.STRUCTURED_OUTPUT_INVALID),
        failure(LLMErrorCode.STRUCTURED_OUTPUT_INVALID),
    )
    value = session(operation)
    first = await value.extract()
    second = await value.repair(first.feedback)
    assert isinstance(second, ExtractionRepairRequired)
    with pytest.raises(RuntimeError, match="repair_not_allowed"):
        await value.repair(second.feedback)


async def test_repair_before_extract_is_rejected_without_call():
    operation = ScriptedModelOperation(success())
    with pytest.raises(RuntimeError, match="repair_before_extract"):
        await session(operation).repair(
            ExtractionRepairFeedback(code="intent_mismatch", field_paths=())
        )
    assert operation.calls == []


async def test_extract_twice_is_rejected_without_second_call():
    operation = ScriptedModelOperation(success())
    value = session(operation)
    await value.extract()
    with pytest.raises(RuntimeError, match="extract_already_called"):
        await value.extract()
    assert len(operation.calls) == 1


async def test_runtime_failure_is_terminal():
    value = session(ScriptedModelOperation(failure(LLMErrorCode.TIMEOUT)))
    with pytest.raises(RequirementExtractionRuntimeError):
        await value.extract()
    with pytest.raises(RuntimeError, match="repair_not_allowed"):
        await value.repair(
            ExtractionRepairFeedback(code="intent_mismatch", field_paths=())
        )


async def test_unexpected_structured_value_is_programming_error():
    class OtherValue(BaseModel):
        value: str

    value = session(ScriptedModelOperation(success(OtherValue(value="x"))))
    with pytest.raises(TypeError, match="unexpected_structured_value"):
        await value.extract()


async def test_concurrent_duplicate_extract_is_rejected():
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingOperation:
        calls = 0

        async def ainvoke_structured(self, prompt, schema):
            self.calls += 1
            started.set()
            await release.wait()
            return success()

    operation = BlockingOperation()
    value = session(operation)
    first = asyncio.create_task(value.extract())
    await started.wait()
    with pytest.raises(RuntimeError, match="extract_already_called"):
        await value.extract()
    release.set()
    await first
    assert operation.calls == 1


async def test_sink_contract_violation_propagates():
    class BrokenSink:
        def accept(self, event):
            raise RuntimeError("sink_broken")

    with pytest.raises(RuntimeError, match="sink_broken"):
        await session(ScriptedModelOperation(success()), BrokenSink()).extract()


def test_known_runtime_code_set_is_exactly_22():
    assert len(tuple(LLMErrorCode)) == 22
