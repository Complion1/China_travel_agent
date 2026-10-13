from __future__ import annotations

import hashlib
from functools import cache
from importlib.resources import files


PROMPT_VERSION = "requirement-extraction-v4-hub-roles"
EXTRACT_PROMPT_RESOURCE = "extract_v4_hub_roles.txt"
REPAIR_PROMPT_RESOURCE = "repair_v4_hub_roles.txt"
EXTRACT_PROMPT_SHA256 = "7d61442919c3bb859e41f2617a21a77710277e67f0580c8d108d16ac197d8463"
REPAIR_PROMPT_SHA256 = "35024c063ea64d5c7422b2aee1964cc7cc200462c94ef86cf8fd34ff362a4481"
ACTIVE_PROMPT_VERSION = "requirement-extraction-v5-active-inputs"
ACTIVE_EXTRACT_PROMPT_SHA256 = "cf7b1dfd6954104bb8bbfbd1ffb42d744e9906534ce3e188379d07e0a8aefad7"
ACTIVE_REPAIR_PROMPT_SHA256 = "0b4acf2a12ecf17a1a180fe374c4587fbde5d1172100a4c9b3a79b6824e5d129"


def prompt_version_for(collection_version: str) -> str:
    return ACTIVE_PROMPT_VERSION if collection_version == "1.1" else PROMPT_VERSION


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
def load_extract_prompt(collection_version: str = "1.0") -> str:
    if collection_version == "1.1":
        return _load_prompt("extract_v5_active_inputs.txt", ACTIVE_EXTRACT_PROMPT_SHA256)
    return _load_prompt(EXTRACT_PROMPT_RESOURCE, EXTRACT_PROMPT_SHA256)


@cache
def load_repair_prompt(collection_version: str = "1.0") -> str:
    if collection_version == "1.1":
        return _load_prompt("repair_v5_active_inputs.txt", ACTIVE_REPAIR_PROMPT_SHA256)
    return _load_prompt(REPAIR_PROMPT_RESOURCE, REPAIR_PROMPT_SHA256)
