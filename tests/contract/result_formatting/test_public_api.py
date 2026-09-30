"""RF-API: D §4 is the complete supported Module 12 public interface."""

import inspect

import pytest
from pydantic import TypeAdapter, ValidationError

import app.result_formatting as formatting
from tests.unit.result_formatting.fakes import make_scenario


EXPECTED_EXPORTS = (
    "RESULT_FORMATTING_SCHEMA_VERSION", "RESULT_FORMATTING_TEMPLATE_VERSION",
    "RESULT_FORMATTING_ARTIFACT_REGISTRATIONS", "ResultFormattingRequest",
    "ResultReadRequest", "ResultFormattingPrepared", "ResultIndexReady",
    "ResultFormattingLoaded", "ResultFormattingFailed", "ResultFormattingOutcome",
    "ResultIndexOutcome", "ResultReadOutcome", "ResultFormattingFailureCode",
    "ResultDayArtifact", "ResultIndexArtifact", "ResultFormattingService",
    "ResultFormattingUsageError", "build_result_artifact_write_request",
)

EXPECTED_FIELDS = {
    "ResultFormattingRequest": ("schema_version", "state", "itinerary_ref"),
    "ResultReadRequest": ("schema_version", "state", "index_ref"),
    "ResultFormattingPrepared": ("outcome_type", "schema_version", "days"),
    "ResultIndexReady": ("outcome_type", "schema_version", "index"),
    "ResultFormattingLoaded": ("outcome_type", "schema_version", "index_ref", "index", "days", "markdown"),
    "ResultFormattingFailed": ("outcome_type", "schema_version", "code"),
}


def test_exact_exports_versions_and_no_internal_dtos():
    assert formatting.__all__ == EXPECTED_EXPORTS
    assert all(hasattr(formatting, name) for name in EXPECTED_EXPORTS)
    assert formatting.RESULT_FORMATTING_SCHEMA_VERSION == "2.0"
    assert formatting.RESULT_FORMATTING_TEMPLATE_VERSION == "zh-guide-v1"
    for name in ("ResultCore", "ResultDayData", "ResultSource", "render_day", "project_result", "ArtifactRepository"):
        assert not hasattr(formatting, name)
    assert {code.value for code in formatting.ResultFormattingFailureCode} == {
        "input_not_ready", "itinerary_not_completed", "source_mismatch",
        "artifact_not_found", "artifact_integrity_error", "artifact_contract_error",
        "artifact_dependency_error", "result_too_large", "unsupported_result_version",
        "unsafe_text", "internal_error",
    }


def test_service_and_write_helper_have_exact_calling_convention():
    service = formatting.ResultFormattingService
    expected = {
        "__init__": (("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                     ("artifact_repository", inspect.Parameter.KEYWORD_ONLY)),
        "prepare": (("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                    ("request", inspect.Parameter.POSITIONAL_OR_KEYWORD)),
        "build_index": (("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                        ("request", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                        ("day_refs", inspect.Parameter.KEYWORD_ONLY)),
        "load": (("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                 ("request", inspect.Parameter.POSITIONAL_OR_KEYWORD)),
    }
    for name, parameters in expected.items():
        method = getattr(service, name)
        actual = inspect.signature(method).parameters
        assert tuple((item.name, item.kind) for item in actual.values()) == parameters
        assert all(item.default is inspect.Parameter.empty for item in actual.values())
        assert inspect.iscoroutinefunction(method) is (name != "__init__")
    assert {name for name, member in vars(service).items() if callable(member) and not name.startswith("_")} == {
        "prepare", "build_index", "load",
    }
    helper = inspect.signature(formatting.build_result_artifact_write_request)
    assert tuple(helper.parameters) == ("operation_id", "artifact")
    assert all(item.kind is inspect.Parameter.KEYWORD_ONLY and item.default is inspect.Parameter.empty
               for item in helper.parameters.values())


@pytest.mark.parametrize("name", EXPECTED_FIELDS)
def test_request_and_outcome_models_are_strict_frozen_required_and_closed(name):
    model = getattr(formatting, name)
    assert tuple(model.model_fields) == EXPECTED_FIELDS[name]
    assert model.model_config["strict"] is True
    assert model.model_config["frozen"] is True
    assert model.model_config["extra"] == "forbid"
    assert all(field.is_required() for field in model.model_fields.values())
    for name, field in model.model_fields.items():
        if name not in {"schema_version", "outcome_type", "code"}:
            assert field.repr is False


@pytest.mark.parametrize(("outcome", "success"), [
    (formatting.ResultFormattingOutcome, "prepared"),
    (formatting.ResultIndexOutcome, "index_ready"),
    (formatting.ResultReadOutcome, "loaded"),
])
def test_outcome_unions_have_the_fixed_discriminator(outcome, success):
    adapter = TypeAdapter(outcome)
    discriminator = adapter.json_schema()["discriminator"]
    assert discriminator["propertyName"] == "outcome_type"
    assert set(discriminator["mapping"]) == {success, "failed"}
    failed = adapter.validate_json('{"outcome_type":"failed","schema_version":"2.0","code":"source_mismatch"}', strict=True)
    assert type(failed) is formatting.ResultFormattingFailed
    with pytest.raises(ValidationError):
        adapter.validate_json('{"outcome_type":"failed","schema_version":"2.0","code":"source_mismatch","detail":"private"}', strict=True)


def test_constructor_rejects_missing_read_port_without_owning_resources():
    for repository in (None, object(), type("InvalidRepository", (), {"get": 1})()):
        with pytest.raises(formatting.ResultFormattingUsageError) as caught:
            formatting.ResultFormattingService(artifact_repository=repository)
        assert str(caught.value) == repr(caught.value) == "ResultFormattingUsageError"
        assert caught.value.__cause__ is None
    assert tuple(inspect.signature(formatting.ResultFormattingUsageError).parameters) == ()


@pytest.mark.asyncio
async def test_request_payload_is_hidden_and_cannot_be_extended_or_reassigned():
    scenario = await make_scenario()
    request = scenario.request
    assert repr(request) == "ResultFormattingRequest()"
    with pytest.raises(ValidationError):
        request.schema_version = "1.0"
    with pytest.raises(ValidationError):
        formatting.ResultFormattingRequest(**request.model_dump(mode="python"), secret="private")
