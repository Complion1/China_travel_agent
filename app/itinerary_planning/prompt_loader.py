"""Module 09 的固定 Prompt 资源与 fail-closed 完整性校验。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources

from .errors import ItineraryPlanningConfigurationError


PLANNING_PROMPT_VERSION = "planning-v5"
_PROMPT_RESOURCE_NAME = "planning_v5.txt"


@dataclass(frozen=True, slots=True)
class PromptResource:
    version: str
    sha256: str
    text: str


def _prompt_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _read_prompt_bytes() -> bytes:
    try:
        return resources.files("app.itinerary_planning").joinpath(
            "prompts", _PROMPT_RESOURCE_NAME
        ).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        raise ItineraryPlanningConfigurationError() from None


def _validate_prompt_bytes(payload: bytes) -> PromptResource:
    if type(payload) is not bytes:
        raise ItineraryPlanningConfigurationError()
    digest = _prompt_digest(payload)
    if digest != PLANNING_PROMPT_SHA256:
        raise ItineraryPlanningConfigurationError()
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ItineraryPlanningConfigurationError() from None
    if not text.strip() or "\x00" in text:
        raise ItineraryPlanningConfigurationError()
    return PromptResource(PLANNING_PROMPT_VERSION, digest, text)


# This value is intentionally filled from the immutable resource bytes after the
# resource is created. It is a content contract, not a runtime-discovered hash.
PLANNING_PROMPT_SHA256 = "sha256:e45c22aad9f0013964c158d91c4b9713c3e6f3c7890efdb9783f69bd1e0b39d9"
PLANNING_PROMPT_HASH = PLANNING_PROMPT_SHA256


def load_planning_prompt() -> PromptResource:
    """Load and verify the packaged UTF-8 Prompt, failing closed on any drift."""

    return _validate_prompt_bytes(_read_prompt_bytes())


def validate_prompt_integrity() -> PromptResource:
    return load_planning_prompt()


__all__ = ()
