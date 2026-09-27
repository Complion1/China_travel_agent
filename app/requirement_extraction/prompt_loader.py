from __future__ import annotations

import hashlib
from functools import cache
from importlib.resources import files


PROMPT_VERSION = "requirement-extraction-v3-explicit-negatives"
EXTRACT_PROMPT_RESOURCE = "extract_v3_explicit_negatives.txt"
REPAIR_PROMPT_RESOURCE = "repair_v3_explicit_negatives.txt"
EXTRACT_PROMPT_SHA256 = "1398108807fe64cd6c49d65c0a95e88a500c09842c1c43a09b904706e3ee2e0b"
REPAIR_PROMPT_SHA256 = "ea2f9faa8c9e513b42fd10cd5b593d766ddae83dd4261c7baa3abf5ccc3ff496"


def _load_prompt(resource_name: str, expected_sha256: str) -> str:
    content = (
        files("app.requirement_extraction.prompts")
        .joinpath(resource_name)
        .read_text(encoding="utf-8")
    )
    actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError("prompt_hash_mismatch")
    return content


@cache
def load_extract_prompt() -> str:
    return _load_prompt(EXTRACT_PROMPT_RESOURCE, EXTRACT_PROMPT_SHA256)


@cache
def load_repair_prompt() -> str:
    return _load_prompt(REPAIR_PROMPT_RESOURCE, REPAIR_PROMPT_SHA256)
