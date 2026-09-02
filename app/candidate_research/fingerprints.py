"""Module 08 稳定摘要、句柄和 Action 指纹。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from enum import Enum
from collections.abc import Mapping, MutableMapping
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from app.requirements import InterestTag

from .models import (
    ActionFingerprint, CandidateResearchGap, CandidateResearchInitialRequest,
    CandidateResearchSupplementalRequest, CandidateAnchorRef, HotelAnchorRef,
    MealIntent, ProposedDetailsAction, ProposedSearchAction, ResearchAnchorRef,
    ResearchRole, SearchAreaMode, StableId, ResearchObjectiveKind,
)


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_json_bytes(value: object) -> bytes:
    return canonical_json(value).encode("utf-8")


def content_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _short_id(prefix: str, tree: object, *, directory: MutableMapping[str, str] | None = None) -> str:
    """Return a digest16 id while optionally guarding its full digest directory."""
    full_digest = "sha256:" + hashlib.sha256(canonical_json_bytes(tree)).hexdigest()
    short_id = f"{prefix}:{full_digest[7:23]}"
    if directory is not None:
        previous = directory.get(short_id)
        if previous is not None and previous != full_digest:
            raise ValueError("fingerprint_collision")
        directory[short_id] = full_digest
    return short_id


def requirement_item_id(confirmed_requirement_digest: str, field: str, index: int, text: str, *, directory: MutableMapping[str, str]) -> StableId:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", confirmed_requirement_digest) is None or field not in {"must_visit", "avoid"} or type(index) is not int or not 0 <= index <= 99 or type(text) is not str or not text.strip() or len(text.strip()) > 512:
        raise ValueError("invalid requirement item")
    if not isinstance(directory, MutableMapping):
        raise ValueError("directory must be a mutable mapping")
    tree = {"confirmed_requirement_digest": confirmed_requirement_digest, "field": field, "index": index, "text": text}
    return _short_id(f"reqitem:{field}:{index}", tree, directory=directory)


def requirement_group_id(confirmed_requirement_digest: str, field: str, item_ids: tuple[str, ...], first_index: int, text: str, *, directory: MutableMapping[str, str]) -> StableId:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", confirmed_requirement_digest) is None or field not in {"must_visit", "avoid"} or type(item_ids) is not tuple or not item_ids or any(type(item_id) is not str or not item_id.strip() or len(item_id.strip()) > 128 for item_id in item_ids) or len(set(item_ids)) != len(item_ids) or type(first_index) is not int or not 0 <= first_index <= 99 or type(text) is not str or not text.strip() or len(text.strip()) > 512:
        raise ValueError("invalid requirement group")
    if not isinstance(directory, MutableMapping):
        raise ValueError("directory must be a mutable mapping")
    tree = {"confirmed_requirement_digest": confirmed_requirement_digest, "field": field, "item_ids": list(item_ids), "text": text}
    return _short_id(f"reqgroup:{field}:{first_index}", tree, directory=directory)


def _anchor_tree(anchor: ResearchAnchorRef) -> dict[str, object]:
    if isinstance(anchor, HotelAnchorRef):
        return {"anchor_type": "hotel"}
    if isinstance(anchor, CandidateAnchorRef):
        return {"anchor_type": "candidate", "uid": anchor.uid}
    raise ValueError("unknown anchor")


def search_action_fingerprint(query: str, tag: str | None, area_mode: SearchAreaMode, destination: str | None = None, anchor: ResearchAnchorRef | None = None, radius_km: int | None = None) -> ActionFingerprint:
    if type(query) is not str or not query.strip() or (tag is not None and (type(tag) is not str or not tag.strip())):
        raise ValueError("query and tag must be non-empty when present")
    if area_mode is SearchAreaMode.CITY:
        if destination is None or not destination.strip() or anchor is not None or radius_km is not None:
            raise ValueError("city fingerprint requires destination only")
        area: dict[str, object] = {"area_type": "city", "region": destination}
    else:
        if anchor is None or radius_km not in {1, 3, 5, 10}:
            raise ValueError("nearby fingerprint requires anchor and radius")
        area = {"area_type": "nearby", "anchor": _anchor_tree(anchor), "radius_m": radius_km * 1000}
    tree = {"action_type": "search_places", "query": query.strip(), "tag": None if tag is None else tag.strip(), "area": area, "result_limit": 50}
    return "sha256:" + hashlib.sha256(canonical_json_bytes(tree)).hexdigest()


def details_action_fingerprint(uid: str) -> ActionFingerprint:
    if type(uid) is not str or not uid.strip() or len(uid.strip()) > 512:
        raise ValueError("invalid place uid")
    return "sha256:" + hashlib.sha256(canonical_json_bytes({"action_type": "get_place_details", "uid": uid})).hexdigest()


def action_fingerprint(action: ProposedSearchAction | ProposedDetailsAction, destination: str | None = None) -> ActionFingerprint:
    if isinstance(action, ProposedDetailsAction):
        return details_action_fingerprint(uid=action.uid)
    return search_action_fingerprint(query=action.query, tag=action.tag, area_mode=action.area_mode, destination=destination, anchor=action.anchor, radius_km=action.radius_km)


def supplemental_gap_fingerprint(confirmed_requirement_digest: str, hotel_selection_content_hash: str, gap: CandidateResearchGap) -> str:
    for digest in (confirmed_requirement_digest, hotel_selection_content_hash):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise ValueError("invalid content digest")
    payload = TypeAdapter(CandidateResearchGap).dump_python(
        TypeAdapter(CandidateResearchGap).validate_json(canonical_json(gap).encode("utf-8"), strict=True),
        mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True,
    )
    payload.pop("gap_id", None)
    payload.pop("evidence_refs", None)
    payload.pop("schema_version", None)
    gap_type = payload.pop("gap_type")
    # The current observed count is evidence, not part of the semantic target;
    # only the typed role target is stable across retries.
    if gap_type == "candidate_count_shortfall":
        payload.pop("current_count", None)
    typed_target = payload
    return content_digest({"confirmed_requirement_digest": confirmed_requirement_digest, "hotel_selection_content_hash": hotel_selection_content_hash, "gap_type": gap_type, "typed_target": typed_target})


def invocation_request_digest(request: CandidateResearchInitialRequest | CandidateResearchSupplementalRequest) -> str:
    # Round-trip through strict JSON validation so callers cannot fingerprint a
    # mutated/forged model instance or a non-canonical nested value.
    encoded = canonical_json(request).encode("utf-8")
    fresh = TypeAdapter(type(request)).validate_json(encoded, strict=True)
    return content_digest(fresh)


def candidate_handle(admission_ordinal: int) -> str:
    if type(admission_ordinal) is not int or not 0 <= admission_ordinal <= 127:
        raise ValueError("invalid candidate ordinal")
    return f"c:{admission_ordinal}"


def evidence_handle(index: int) -> str:
    if type(index) is not int or not 0 <= index <= 47:
        raise ValueError("invalid evidence index")
    return f"e:{index}"


def objective_handle(objective_id: str) -> str:
    if type(objective_id) is not str or not objective_id.strip() or len(objective_id.strip()) > 128:
        raise ValueError("invalid objective id")
    return objective_id


def register_fingerprint(
    directory: MutableMapping[str, object], fingerprint: str, semantic_tree: object
) -> None:
    """Insert a fingerprint once and reject semantic collisions."""
    if re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is None:
        raise ValueError("invalid fingerprint")
    normalized = canonical_json(semantic_tree)
    previous = directory.get(fingerprint)
    if previous is not None and canonical_json(previous) != normalized:
        raise ValueError("fingerprint_collision")
    directory[fingerprint] = _jsonable(semantic_tree)


def objective_fingerprint(
    *,
    kind: ResearchObjectiveKind,
    gap_id: str | None,
    requirement_group_id: str | None,
    role: ResearchRole | None,
    interest: InterestTag | None,
    meal_intent: MealIntent | None,
    anchor: ResearchAnchorRef | None,
    radius_km: int | None,
    candidate_uids: tuple[str, ...],
    confirmed_requirement_digest: str,
    hotel_selection_content_hash: str,
    predecessor_candidate_set_content_hash: str | None,
    directory: MutableMapping[str, str],
) -> StableId:
    """Build a stable objective id from the complete frozen objective tree."""
    if not isinstance(directory, MutableMapping):
        raise ValueError("directory must be a mutable mapping")
    for digest in (confirmed_requirement_digest, hotel_selection_content_hash):
        if type(digest) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise ValueError("invalid objective content digest")
    if predecessor_candidate_set_content_hash is not None and (type(predecessor_candidate_set_content_hash) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", predecessor_candidate_set_content_hash) is None):
        raise ValueError("invalid objective content digest")
    if type(candidate_uids) is not tuple:
        raise ValueError("objective candidate uids must be a tuple")
    from .policy import ResearchObjective

    objective = ResearchObjective(
        objective_id="objective:pending", kind=kind, gap_id=gap_id,
        requirement_group_id=requirement_group_id, role=role, interest=interest,
        meal_intent=meal_intent, anchor=anchor, radius_km=radius_km,
        candidate_uids=candidate_uids,
    )
    tree = objective.model_dump(mode="python", exclude={"objective_id"})
    tree.update({
        "confirmed_requirement_digest": confirmed_requirement_digest,
        "hotel_selection_content_hash": hotel_selection_content_hash,
        "predecessor_candidate_set_content_hash": predecessor_candidate_set_content_hash,
    })
    return _short_id(f"objective:{objective.kind.value}", tree, directory=directory)


build_objective_id = objective_fingerprint


__all__ = (
    "canonical_json", "canonical_json_bytes", "content_digest", "requirement_item_id",
    "requirement_group_id", "search_action_fingerprint", "details_action_fingerprint",
    "action_fingerprint", "supplemental_gap_fingerprint", "invocation_request_digest",
    "candidate_handle", "evidence_handle", "objective_handle", "register_fingerprint",
    "objective_fingerprint", "build_objective_id",
)
