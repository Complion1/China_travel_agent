from __future__ import annotations

import hashlib
from functools import cache
from importlib.resources import files


PROMPT_VERSION = "requirement-extraction-v1"
EXTRACT_PROMPT_RESOURCE = "extract_v1.txt"
REPAIR_PROMPT_RESOURCE = "repair_v1.txt"
EXTRACT_PROMPT_SHA256 = "f0fba9f60d9066912890a040f5cba197024ee4242c8a820104a05ce15776dcdb"
REPAIR_PROMPT_SHA256 = "7ad7e547fbb34f01b662fa74f1cb616447027a7920bed2a7d156a148508f695d"


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
