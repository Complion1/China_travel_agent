from __future__ import annotations

from types import MappingProxyType
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from app.llm_runtime.models import ModelRole

MODEL_NAME = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
THINKING_TYPE = "enabled"
REASONING_EFFORT = "high"
CONNECT_TIMEOUT_SECONDS = 10
WRITE_TIMEOUT_SECONDS = 30
POOL_TIMEOUT_SECONDS = 10
PROVIDER_ATTEMPTS_PER_INVOCATION = 2


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    role: ModelRole
    model_name: Literal["deepseek-v4-flash"] = MODEL_NAME
    base_url: Literal["https://api.deepseek.com"] = BASE_URL
    thinking_type: Literal["enabled"] = THINKING_TYPE
    reasoning_effort: Literal["high"] = REASONING_EFFORT
    streaming: Literal[False] = False
    connect_timeout_seconds: Literal[10] = CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: Annotated[int, Field(gt=0)] = 120
    write_timeout_seconds: Literal[30] = WRITE_TIMEOUT_SECONDS
    pool_timeout_seconds: Literal[10] = POOL_TIMEOUT_SECONDS
    provider_attempts_per_invocation: Literal[2] = PROVIDER_ATTEMPTS_PER_INVOCATION
    semantic_invocation_limit: Annotated[int, Field(gt=0)] = 2
    max_request_utf8_bytes: Annotated[int, Field(gt=0)] = 32_768
    max_output_tokens: Annotated[int, Field(gt=0)] = 16_384


REQUIREMENT_EXTRACTION_PROFILE = ModelProfile(
    role=ModelRole.REQUIREMENT_EXTRACTION,
    read_timeout_seconds=120,
    semantic_invocation_limit=2,
    max_request_utf8_bytes=32_768,
    max_output_tokens=32_768,
)

CANDIDATE_RESEARCH_PROFILE = ModelProfile(
    role=ModelRole.CANDIDATE_RESEARCH,
    read_timeout_seconds=120,
    semantic_invocation_limit=12,
    max_request_utf8_bytes=262_144,
    max_output_tokens=32_768,
)

ITINERARY_PLANNING_PROFILE = ModelProfile(
    role=ModelRole.ITINERARY_PLANNING,
    read_timeout_seconds=120,
    semantic_invocation_limit=17,
    max_request_utf8_bytes=2_097_152,
    max_output_tokens=49_152,
)

REPLANNING_PROFILE = ModelProfile(
    role=ModelRole.REPLANNING,
    read_timeout_seconds=120,
    semantic_invocation_limit=8,
    max_request_utf8_bytes=262_144,
    max_output_tokens=32_768,
)

_PROFILE_MAP: dict[ModelRole, ModelProfile] = {
    ModelRole.REQUIREMENT_EXTRACTION: REQUIREMENT_EXTRACTION_PROFILE,
    ModelRole.CANDIDATE_RESEARCH: CANDIDATE_RESEARCH_PROFILE,
    ModelRole.ITINERARY_PLANNING: ITINERARY_PLANNING_PROFILE,
    ModelRole.REPLANNING: REPLANNING_PROFILE,
}

PROFILE_REGISTRY: Mapping[ModelRole, ModelProfile] = MappingProxyType(_PROFILE_MAP)


def get_profile(role: ModelRole) -> ModelProfile:
    if not isinstance(role, ModelRole):
        raise TypeError(f"Expected ModelRole, got {type(role).__name__}")
    return PROFILE_REGISTRY[role]
