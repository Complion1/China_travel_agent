from datetime import date
from uuid import uuid4

from app.llm_runtime import ModelRole
from app.requirement_extraction import (
    DeepSeekRequirementExtractionSession,
    DeepSeekRequirementExtractor,
    NoOpExtractionInvocationSink,
)
from app.requirements import ExtractionInput, RequirementIntent, TravelRequirement


class RecordingRuntime:
    def __init__(self):
        self.roles = []
        self.operations = []

    def start_operation(self, role):
        operation = object()
        self.roles.append(role)
        self.operations.append(operation)
        return operation


def request():
    return ExtractionInput(
        turn_id=uuid4(),
        user_message="杭州",
        session_date=date(2026, 7, 14),
        timezone="Asia/Shanghai",
        current_requirement=TravelRequirement(),
        intent=RequirementIntent.INITIAL,
    )


def test_constructor_does_not_start_operation():
    runtime = RecordingRuntime()
    DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=NoOpExtractionInvocationSink())
    assert runtime.roles == []


def test_start_session_uses_requirement_role():
    runtime = RecordingRuntime()
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=NoOpExtractionInvocationSink())
    assert isinstance(adapter.start_session(request()), DeepSeekRequirementExtractionSession)
    assert runtime.roles == [ModelRole.REQUIREMENT_EXTRACTION]


def test_every_session_gets_a_new_operation():
    runtime = RecordingRuntime()
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=NoOpExtractionInvocationSink())
    first = adapter.start_session(request())
    second = adapter.start_session(request())
    assert first is not second
    assert runtime.operations[0] is not runtime.operations[1]


def test_start_session_does_not_call_operation():
    runtime = RecordingRuntime()
    adapter = DeepSeekRequirementExtractor(runtime=runtime, invocation_sink=NoOpExtractionInvocationSink())
    adapter.start_session(request())
    assert len(runtime.operations) == 1


def test_adapter_has_no_environment_or_key_constructor_parameters():
    import inspect

    parameters = inspect.signature(DeepSeekRequirementExtractor).parameters
    assert tuple(parameters) == ("runtime", "invocation_sink")


def test_public_exports_are_exact():
    import app.requirement_extraction as package

    assert set(package.__all__) == {
        "DeepSeekRequirementExtractor",
        "DeepSeekRequirementExtractionSession",
        "RequirementExtractionRuntimeError",
        "ExtractionInvocationEvent",
        "ExtractionInvocationSink",
        "NoOpExtractionInvocationSink",
        "PROMPT_VERSION",
        "EXTRACT_PROMPT_SHA256",
        "REPAIR_PROMPT_SHA256",
    }
