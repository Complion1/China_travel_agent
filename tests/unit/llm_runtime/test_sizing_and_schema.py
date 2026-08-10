"""LLM-T03: Schema validation and sizing tests"""
import pytest
from pydantic import BaseModel, ConfigDict
from app.llm_runtime.sizing import (
    validate_consumer_schema, validate_tool_contract, canonical_request_bytes,
    check_byte_gate, JSON_ONLY_INSTRUCTION,
)
from app.llm_runtime.models import PromptEnvelope, ModelRole, LLMErrorCode
from app.llm_runtime.profiles import REQUIREMENT_EXTRACTION_PROFILE


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    city: str


class LaxSchema(BaseModel):
    city: str


class NotAModel:
    pass


class BrokenSchema(StrictSchema):
    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        raise RuntimeError("broken")


class NonObjectSchema(StrictSchema):
    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return {"type": "array"}


class NonJsonSchema(StrictSchema):
    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return {"type": "object", "bad": object()}


def test_validate_good_schema():
    assert len(validate_consumer_schema(StrictSchema)) == 0


def test_validate_lax_schema_rejected():
    assert len(validate_consumer_schema(LaxSchema)) > 0


def test_validate_not_a_model():
    assert len(validate_consumer_schema(NotAModel)) > 0


@pytest.mark.parametrize("schema", [BrokenSchema, NonObjectSchema, NonJsonSchema])
def test_validate_schema_rejects_generation_root_and_json_errors(schema):
    assert validate_consumer_schema(schema)


def test_validate_tool_contract_good():
    assert len(validate_tool_contract("test_tool", "A test tool", StrictSchema)) == 0


def test_validate_tool_contract_bad_name():
    assert len(validate_tool_contract("", "A test tool", StrictSchema)) > 0


def test_validate_tool_contract_long_name():
    assert len(validate_tool_contract("a" * 65, "desc", StrictSchema)) > 0


def test_validate_tool_contract_bad_description():
    assert len(validate_tool_contract("ok", "", StrictSchema)) > 0


def test_validate_tool_contract_not_pydantic():
    assert len(validate_tool_contract("ok", "desc", NotAModel)) > 0


@pytest.mark.parametrize("name,description,schema", [
    ("bad name", "desc", StrictSchema),
    ("ok", "x" * 1025, StrictSchema),
    ("ok", "desc", LaxSchema),
    ("ok", "desc", BrokenSchema),
    ("ok", "desc", NonObjectSchema),
])
def test_validate_tool_contract_rejects_every_contract_edge(name, description, schema):
    assert validate_tool_contract(name, description, schema)


def test_canonical_request_contains_model():
    prompt = PromptEnvelope(system_text="Helpful", user_text="Hello")
    canon = canonical_request_bytes(profile=REQUIREMENT_EXTRACTION_PROFILE, prompt=prompt, json_schema=None)
    assert b"deepseek-v4-flash" in canon


def test_canonical_request_no_api_key():
    prompt = PromptEnvelope(system_text="Helpful", user_text="Hello")
    canon = canonical_request_bytes(profile=REQUIREMENT_EXTRACTION_PROFILE, prompt=prompt, json_schema=None)
    assert b"api_key" not in canon


def test_check_byte_gate_ok():
    assert check_byte_gate(500, 1000, ModelRole.REQUIREMENT_EXTRACTION) is None


def test_check_byte_gate_too_large():
    err = check_byte_gate(1001, 1000, ModelRole.REQUIREMENT_EXTRACTION)
    assert err is not None
    assert err.code == LLMErrorCode.INPUT_TOO_LARGE


def test_check_byte_gate_exact_limit_ok():
    assert check_byte_gate(1000, 1000, ModelRole.REQUIREMENT_EXTRACTION) is None


def test_canonical_request_deterministic():
    prompt = PromptEnvelope(system_text="Helpful", user_text="Hello")
    c1 = canonical_request_bytes(profile=REQUIREMENT_EXTRACTION_PROFILE, prompt=prompt, json_schema=None)
    c2 = canonical_request_bytes(profile=REQUIREMENT_EXTRACTION_PROFILE, prompt=prompt, json_schema=None)
    assert c1 == c2


def test_canonical_request_with_schema_larger():
    prompt = PromptEnvelope(system_text="Helpful", user_text="Hello")
    without = canonical_request_bytes(profile=REQUIREMENT_EXTRACTION_PROFILE, prompt=prompt, json_schema=None)
    with_schema = canonical_request_bytes(profile=REQUIREMENT_EXTRACTION_PROFILE, prompt=prompt, json_schema={"type": "object", "properties": {"x": {"type": "string"}}})
    assert len(with_schema) > len(without)


def test_json_only_instruction_not_empty():
    assert len(JSON_ONLY_INSTRUCTION) > 10
