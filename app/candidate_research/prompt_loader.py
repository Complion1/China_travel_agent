"""唯一的 Module 08 prompt 资源加载器；每次读取都校验冻结哈希。"""

from __future__ import annotations

import hashlib
import re
from importlib.resources import files

from .errors import CandidateResearchConfigurationError


ACTION_PROMPT_VERSION = "action-v1"
OBSERVATION_PROMPT_VERSION = "observation-v1"
ACTION_PROMPT_RESOURCE = "action_v1.txt"
OBSERVATION_PROMPT_RESOURCE = "observation_v1.txt"


def _read_prompt(resource_name: str, expected_sha256: str) -> str:
    try:
        resource = files("app.candidate_research.prompts").joinpath(resource_name)
    except (AttributeError, FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise CandidateResearchConfigurationError() from None
    try:
        raw = resource.read_bytes()
    except (AttributeError, FileNotFoundError, OSError) as read_error:
        try:
            raw = resource.read_text(encoding="utf-8").encode("utf-8")
        except (AttributeError, FileNotFoundError, OSError, UnicodeError, RecursionError) as exc:
            raise CandidateResearchConfigurationError() from None
    actual = hashlib.sha256(raw).hexdigest()
    normalized = expected_sha256.removeprefix("sha256:")
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise CandidateResearchConfigurationError()
    if actual != normalized:
        raise CandidateResearchConfigurationError()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CandidateResearchConfigurationError() from None
    if text.startswith("\ufeff") or "\r" in text or not text.endswith("\n") or text.endswith("\n\n"):
        raise CandidateResearchConfigurationError()
    return text


# Values are intentionally literals: changing a resource without changing code fails closed.
ACTION_PROMPT_SHA256 = "b618a9840d52a1fa26b74906f6b3eceac370ec550cc2e7600672ee9ee5539b82"
OBSERVATION_PROMPT_SHA256 = "51780cc5a21c5ee7d85c254417a56aa38f9222aee879771834e1e704a3f095ea"
ACTION_PROMPT_CONTENT_DIGEST = "sha256:" + ACTION_PROMPT_SHA256
OBSERVATION_PROMPT_CONTENT_DIGEST = "sha256:" + OBSERVATION_PROMPT_SHA256


def load_action_prompt() -> str:
    return _read_prompt(ACTION_PROMPT_RESOURCE, ACTION_PROMPT_SHA256)


def load_observation_prompt() -> str:
    return _read_prompt(OBSERVATION_PROMPT_RESOURCE, OBSERVATION_PROMPT_SHA256)


def _load_prompt(resource_name: str, expected_sha256: str) -> str:
    """兼容测试/工具使用的私有旧式名称。"""
    return _read_prompt(resource_name, expected_sha256)


__all__ = (
    "ACTION_PROMPT_VERSION", "OBSERVATION_PROMPT_VERSION", "ACTION_PROMPT_RESOURCE",
    "OBSERVATION_PROMPT_RESOURCE", "ACTION_PROMPT_SHA256", "OBSERVATION_PROMPT_SHA256",
    "ACTION_PROMPT_CONTENT_DIGEST", "OBSERVATION_PROMPT_CONTENT_DIGEST",
    "load_action_prompt", "load_observation_prompt",
)
