"""LLM-T01 + LLM-T02: models and profiles tests"""
import pytest
from pydantic import ValidationError
from app.llm_runtime.models import (
    ModelRole, PromptEnvelope, LLMUsage, InvocationMetadata,
    StructuredInvocationSuccess, LLMInvocationFailure, LLMErrorCode,
    LLMRuntimeError, CapabilityProbeReport,
)
from app.llm_runtime.profiles import (
    get_profile, PROFILE_REGISTRY, REQUIREMENT_EXTRACTION_PROFILE,
    CANDIDATE_RESEARCH_PROFILE, ITINERARY_PLANNING_PROFILE,
    REPLANNING_PROFILE,
    MODEL_NAME, THINKING_TYPE, REASONING_EFFORT,
    ModelProfile,
)


class TestModels:
    def test_model_role_values(self):
        assert ModelRole.REQUIREMENT_EXTRACTION.value == "requirement_extraction"
        assert len(ModelRole) == 4

    def test_prompt_envelope_rejects_empty(self):
        with pytest.raises(ValidationError):
            PromptEnvelope(system_text="", user_text="hello")
        with pytest.raises(ValidationError):
            PromptEnvelope(system_text="hello", user_text="")
        with pytest.raises(ValidationError):
            PromptEnvelope(system_text="   ", user_text="hello")
        with pytest.raises(ValidationError):
            PromptEnvelope(system_text="hello", user_text="   ")

    def test_usage_consistency(self):
        u = LLMUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30,
                      reasoning_tokens=5, prompt_cache_hit_tokens=3, prompt_cache_miss_tokens=7)
        assert u.total_tokens == 30
        assert u.prompt_tokens == 10

    def test_profile_values(self):
        p = REQUIREMENT_EXTRACTION_PROFILE
        assert p.model_name == "deepseek-v4-flash"
        assert p.thinking_type == "enabled"
        assert p.reasoning_effort == "high"

    def test_get_profile(self):
        p = get_profile(ModelRole.REQUIREMENT_EXTRACTION)
        assert p.role == ModelRole.REQUIREMENT_EXTRACTION

    def test_get_profile_rejects_non_role(self):
        with pytest.raises(TypeError):
            get_profile("invalid")  # type: ignore

    def test_all_profiles_have_thinking(self):
        for role, profile in PROFILE_REGISTRY.items():
            assert profile.thinking_type == "enabled"

    def test_capability_report_defaults(self):
        r = CapabilityProbeReport()
        assert r.passed is False
        assert r.models_endpoint_ok is False


class TestProfiles:
    def test_four_profiles(self):
        assert len(PROFILE_REGISTRY) == 4

    def test_approved_output_limits_and_invocation_budgets(self):
        expected = {
            ModelRole.REQUIREMENT_EXTRACTION: (32_768, 2, 32_768),
            ModelRole.CANDIDATE_RESEARCH: (32_768, 12, 262_144),
            ModelRole.ITINERARY_PLANNING: (49_152, 12, 2_097_152),
            ModelRole.REPLANNING: (32_768, 8, 262_144),
        }
        for role, (output_tokens, semantic_limit, request_bytes) in expected.items():
            profile = get_profile(role)
            assert profile.max_output_tokens == output_tokens
            assert profile.semantic_invocation_limit == semantic_limit
            assert profile.max_request_utf8_bytes == request_bytes
            assert profile.provider_attempts_per_invocation == 2
        assert ModelProfile(role=ModelRole.REQUIREMENT_EXTRACTION).max_output_tokens == 16_384
        assert ModelProfile(role=ModelRole.ITINERARY_PLANNING).max_request_utf8_bytes == 32_768

    def test_profile_timeouts(self):
        assert REQUIREMENT_EXTRACTION_PROFILE.read_timeout_seconds == 120
        assert CANDIDATE_RESEARCH_PROFILE.read_timeout_seconds == 120
        assert ITINERARY_PLANNING_PROFILE.read_timeout_seconds == 120
        assert REPLANNING_PROFILE.read_timeout_seconds == 120
