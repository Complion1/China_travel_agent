"""LLM-T04: Retry and error tests"""
import pytest
from pydantic import BaseModel, ConfigDict
from app.llm_runtime.models import LLMErrorCode, LLMRuntimeError, ModelRole
from app.llm_runtime.protocols import ProviderFailure, ProviderResponse
from app.llm_runtime.provider import _classify_exception, _classify_status


class DummyError(Exception):
    pass


def test_classify_401():
    code, retryable = _classify_status(401)
    assert code == "authentication_failed"
    assert not retryable


def test_classify_429():
    code, retryable = _classify_status(429)
    assert code == "rate_limited"
    assert retryable


def test_classify_503():
    code, retryable = _classify_status(503)
    assert code == "provider_unavailable"
    assert retryable


def test_classify_unknown():
    code, retryable = _classify_status(418)
    assert code == "malformed_provider_response"
    assert not retryable


def test_llm_runtime_error_no_message_field():
    err = LLMRuntimeError(code=LLMErrorCode.QUOTA_EXHAUSTED, role=ModelRole.REQUIREMENT_EXTRACTION, retryable=False, possible_duplicate=False)
    assert not hasattr(err, "message")


def test_llm_error_code_has_22_values():
    assert len(LLMErrorCode) == 22
