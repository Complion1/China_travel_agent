"""确定性 Prompt envelope 构造；不记录完整 prompt。"""

from __future__ import annotations

from collections.abc import Mapping

from app.llm_runtime import PromptEnvelope

from .prompt_loader import load_action_prompt, load_observation_prompt
from .fingerprints import canonical_json


def canonical_dynamic_json(value: object) -> str:
    return canonical_json(value)


def build_action_prompt(dynamic_payload: Mapping[str, object]) -> PromptEnvelope:
    return PromptEnvelope(system_text=load_action_prompt(), user_text=canonical_dynamic_json(dynamic_payload))


def build_observation_prompt(dynamic_payload: Mapping[str, object]) -> PromptEnvelope:
    return PromptEnvelope(system_text=load_observation_prompt(), user_text=canonical_dynamic_json(dynamic_payload))


def build_prompt_envelope(*, kind: str, dynamic_payload: Mapping[str, object]) -> PromptEnvelope:
    if kind == "action":
        return build_action_prompt(dynamic_payload)
    if kind == "observation" or kind == "judgment":
        return build_observation_prompt(dynamic_payload)
    raise ValueError("unknown prompt kind")


__all__ = ("canonical_dynamic_json", "build_action_prompt", "build_observation_prompt", "build_prompt_envelope")
