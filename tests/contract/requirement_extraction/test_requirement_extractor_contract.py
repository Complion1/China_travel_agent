import json
from datetime import date
from uuid import UUID, uuid4

import pytest

from app.llm_runtime import (
    InvocationMetadata,
    LLMErrorCode,
    LLMInvocationFailure,
    LLMRuntimeError,
    LLMUsage,
    ModelRole,
    StructuredInvocationSuccess,
)
from app.requirement_extraction import (
    DeepSeekRequirementExtractor,
    RequirementExtractionRuntimeError,
)
from app.requirements import (
    EvidenceReceipt,
    ExtractionAttemptSucceeded,
    ExtractionInput,
    ExtractionRepairRequired,
    NeedClarification,
    RequirementDomainService,
    RequirementDomainState,
    RequirementFieldPath,
    RequirementIntent,
    RequirementTurnInput,
    StrictFieldEvidenceDTO,
    StrictRequirementPatchDTO,
    TravelRequirement,
)


def dto(**overrides):
    payload = {name: None for name in StrictRequirementPatchDTO.model_fields}
    payload.update(is_correction=False, evidence=[])
    payload.update(overrides)
    return StrictRequirementPatchDTO(**payload)


def metadata():
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


class ScriptedOperation:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def ainvoke_structured(self, prompt, schema):
        self.calls.append((prompt, schema))
        if not self.outcomes:
            raise AssertionError("unexpected_semantic_call")
        return self.outcomes.pop(0)


class RecordingRuntime:
    def __init__(self, *operation_outcomes):
        self.operation_outcomes = list(operation_outcomes)
        self.roles = []
        self.operations = []

    def start_operation(self, role):
        self.roles.append(role)
        outcomes = self.operation_outcomes.pop(0)
        operation = ScriptedOperation(outcomes)
        self.operations.append(operation)
        return operation


class RecordingInvocationSink:
    def __init__(self):
        self.events = []

    def accept(self, event):
        self.events.append(event)


class MemoryEvidenceSink:
    def accept(self, *, turn_id, evidence):
        return tuple(
            EvidenceReceipt(
                field_path=item.field_path,
                evidence_ref=f"ref:{item.field_path.value}",
                evidence_digest="digest",
            )
            for item in evidence
        )


class FixedClock:
    def today(self):
        return date(2026, 7, 14)


def request(message="杭州", intent=RequirementIntent.INITIAL):
    return ExtractionInput(
        turn_id=UUID("00000000-0000-0000-0000-000000000001"),
        user_message=message,
        session_date=date(2026, 7, 14),
        timezone="Asia/Shanghai",
        current_requirement=TravelRequirement(),
        intent=intent,
    )


def success(value):
    return StructuredInvocationSuccess(value=value, metadata=metadata())


async def test_adapter_conforms_to_module_01_async_protocol():
    runtime = RecordingRuntime((success(dto()),))
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=RecordingInvocationSink())
    outcome = await adapter.start_session(request()).extract()
    assert isinstance(outcome, ExtractionAttemptSucceeded)


async def test_full_domain_repair_loop_uses_one_session_and_operation():
    bad = dto(destination="杭州")
    good = dto(
        destination="杭州",
        evidence=[StrictFieldEvidenceDTO(field_path=RequirementFieldPath.DESTINATION, text="杭州")],
    )
    runtime = RecordingRuntime((success(bad), success(good)))
    sink = RecordingInvocationSink()
    service = RequirementDomainService(
        extractor=DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=sink),
        evidence_sink=MemoryEvidenceSink(),
        clock=FixedClock(),
    )
    outcome = await service.process_turn(
        RequirementTurnInput(
            turn_id=uuid4(),
            user_message="杭州",
            session_date=date(2026, 7, 14),
            timezone="Asia/Shanghai",
            intent=RequirementIntent.INITIAL,
            current=RequirementDomainState(),
        )
    )
    assert isinstance(outcome, NeedClarification)
    assert len(runtime.operations) == 1
    assert len(runtime.operations[0].calls) == 2
    assert [event.semantic_call_number for event in sink.events] == [1, 2]


async def test_structured_failure_is_typed_repair_required():
    error = LLMRuntimeError(
        code=LLMErrorCode.STRUCTURED_OUTPUT_INVALID,
        role=ModelRole.REQUIREMENT_EXTRACTION,
        retryable=False,
        possible_duplicate=False,
    )
    runtime = RecordingRuntime((LLMInvocationFailure(error=error),))
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=RecordingInvocationSink())
    outcome = await adapter.start_session(request()).extract()
    assert isinstance(outcome, ExtractionRepairRequired)
    assert outcome.feedback.code == "structured_output_invalid"


async def test_runtime_failure_preserves_exact_error_object():
    error = LLMRuntimeError(
        code=LLMErrorCode.TIMEOUT,
        role=ModelRole.REQUIREMENT_EXTRACTION,
        retryable=True,
        possible_duplicate=True,
        provider_status_code=504,
    )
    runtime = RecordingRuntime((LLMInvocationFailure(error=error),))
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=RecordingInvocationSink())
    with pytest.raises(RequirementExtractionRuntimeError) as caught:
        await adapter.start_session(request()).extract()
    assert caught.value.runtime_error is error


async def test_each_turn_gets_distinct_session_operation():
    runtime = RecordingRuntime((success(dto()),), (success(dto()),))
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=RecordingInvocationSink())
    first = adapter.start_session(request())
    second = adapter.start_session(request("上海"))
    await first.extract()
    await second.extract()
    assert runtime.operations[0] is not runtime.operations[1]


async def test_serialized_payload_contains_full_current_requirement_but_no_turn_id():
    runtime = RecordingRuntime((success(dto()),))
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=RecordingInvocationSink())
    await adapter.start_session(request()).extract()
    payload = json.loads(runtime.operations[0].calls[0][0].user_text)
    assert payload["current_requirement"]["destination"] is None
    assert "turn_id" not in payload


async def test_empty_candidate_is_valid_without_repair():
    runtime = RecordingRuntime((success(dto()),))
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=RecordingInvocationSink())
    outcome = await adapter.start_session(request("我再想想")).extract()
    assert isinstance(outcome, ExtractionAttemptSucceeded)
    assert outcome.value.evidence == []


async def test_correction_candidate_remains_typed():
    runtime = RecordingRuntime((success(dto(is_correction=True)),))
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=RecordingInvocationSink())
    outcome = await adapter.start_session(request("预算改成八千", RequirementIntent.CORRECTION)).extract()
    assert outcome.value.is_correction is True


async def test_event_contains_no_user_payload_or_dto():
    sink = RecordingInvocationSink()
    runtime = RecordingRuntime((success(dto()),))
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=sink)
    await adapter.start_session(request()).extract()
    dumped = sink.events[0].model_dump(mode="json")
    assert "user_message" not in dumped
    assert "value" not in dumped
    assert "evidence" not in dumped


async def test_repair_cannot_exceed_two_semantic_calls():
    error = LLMRuntimeError(
        code=LLMErrorCode.STRUCTURED_OUTPUT_INVALID,
        role=ModelRole.REQUIREMENT_EXTRACTION,
        retryable=False,
        possible_duplicate=False,
    )
    runtime = RecordingRuntime((LLMInvocationFailure(error=error), LLMInvocationFailure(error=error)))
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=RecordingInvocationSink())
    session = adapter.start_session(request())
    first = await session.extract()
    second = await session.repair(first.feedback)
    assert isinstance(second, ExtractionRepairRequired)
    with pytest.raises(RuntimeError, match="repair_not_allowed"):
        await session.repair(second.feedback)
