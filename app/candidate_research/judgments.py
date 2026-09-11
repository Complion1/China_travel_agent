"""Judgment patch 的句柄可见性校验与确定性合并。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json

from pydantic import TypeAdapter

from app.workflow_contracts import ArtifactRef

from .models import (
    EvidenceHandle, JudgmentPatch, RequirementGroupJudgmentUpdate,
    RoleJudgmentUpdate, InterestJudgmentUpdate, MealJudgmentUpdate,
)
from .fingerprints import candidate_handle, evidence_handle


def _fresh_patch(patch: JudgmentPatch) -> JudgmentPatch:
    payload = patch.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return TypeAdapter(JudgmentPatch).validate_json(encoded, strict=True)


def validate_judgment_patch(
    *,
    patch: JudgmentPatch,
    candidate_handles: Sequence[str],
    evidence_handles: Sequence[str],
    candidate_evidence_handles: Mapping[str, Sequence[str]],
) -> JudgmentPatch:
    fresh = _fresh_patch(patch)
    candidates = set(candidate_handles)
    evidence = set(evidence_handles)
    if len(candidates) != len(tuple(candidate_handles)) or len(evidence) != len(tuple(evidence_handles)):
        raise ValueError("visible handles must be unique")
    if any(candidate_handle(int(item[2:])) != item for item in candidates if item.startswith("c:") and item[2:].isdigit()) or any(not (item.startswith("c:") and item[2:].isdigit()) for item in candidates):
        raise ValueError("invalid candidate handle directory")
    if any(evidence_handle(int(item[2:])) != item for item in evidence if item.startswith("e:") and item[2:].isdigit()) or any(not (item.startswith("e:") and item[2:].isdigit()) for item in evidence):
        raise ValueError("invalid evidence handle directory")
    if set(candidate_evidence_handles) != candidates:
        raise ValueError("candidate evidence directory must cover visible candidates")
    owned_evidence: dict[str, set[str]] = {}
    for handle, values in candidate_evidence_handles.items():
        sequence = tuple(values)
        owned = set(sequence)
        if len(owned) != len(sequence) or not owned.issubset(evidence):
            raise ValueError("invalid candidate evidence directory")
        owned_evidence[handle] = owned

    def require_owned(candidate: str, handles: Sequence[EvidenceHandle]) -> None:
        if any(item.handle_id not in owned_evidence[candidate] for item in handles):
            raise ValueError("evidence handle does not belong to candidate")

    seen: set[str] = set()
    for update in (*fresh.role_updates, *fresh.interest_updates, *fresh.meal_updates):
        if update.candidate_handle not in candidates:
            raise ValueError("unknown candidate handle")
        require_owned(update.candidate_handle, update.evidence_handles)
        for item in update.evidence_handles:
            if item.handle_id not in evidence:
                raise ValueError("unknown evidence handle")
            seen.add(item.handle_id)
    for update in fresh.requirement_group_updates:
        if not set(update.evaluated_candidate_handles).issubset(candidates):
            raise ValueError("unknown evaluated candidate handle")
        if any(item.candidate_handle not in candidates for item in update.exceptions):
            raise ValueError("unknown exception candidate handle")
        exception_candidates = {item.candidate_handle for item in update.exceptions}
        for candidate in update.evaluated_candidate_handles:
            if candidate in exception_candidates:
                continue
            require_owned(candidate, update.default_evidence_handles)
        for exception in update.exceptions:
            require_owned(exception.candidate_handle, exception.evidence_handles)
        for item in (*update.default_evidence_handles, *(handle for exception in update.exceptions for handle in exception.evidence_handles)):
            if item.handle_id not in evidence:
                raise ValueError("unknown evidence handle")
            seen.add(item.handle_id)
    return fresh


def evidence_handle_indexes(handles: Sequence[EvidenceHandle], visible_handles: Sequence[str]) -> tuple[int, ...]:
    index = {handle: position for position, handle in enumerate(visible_handles)}
    result = tuple(index[item.handle_id] for item in handles if item.handle_id in index)
    if len(result) != len(handles):
        raise ValueError("unknown evidence handle")
    return result


def evidence_ref_directory(refs: Sequence[ArtifactRef]) -> Mapping[str, ArtifactRef]:
    return {f"e:{index}": ref for index, ref in enumerate(refs)}


def merge_judgment_patch(current: JudgmentPatch, incoming: JudgmentPatch) -> JudgmentPatch:
    base = _fresh_patch(current)
    patch = _fresh_patch(incoming)
    def merge_by_key(base_items, incoming_items, key):
        positions = {key(item): index for index, item in enumerate(base_items)}
        merged = list(base_items)
        for item in incoming_items:
            item_key = key(item)
            if item_key in positions:
                merged[positions[item_key]] = item
            else:
                positions[item_key] = len(merged)
                merged.append(item)
        return tuple(merged)

    role = merge_by_key(base.role_updates, patch.role_updates, lambda item: (item.candidate_handle, item.role))
    interest = merge_by_key(base.interest_updates, patch.interest_updates, lambda item: (item.candidate_handle, item.interest))
    meal = merge_by_key(base.meal_updates, patch.meal_updates, lambda item: (item.candidate_handle, item.meal_intent))
    groups = merge_by_key(base.requirement_group_updates, patch.requirement_group_updates, lambda item: item.requirement_group_id)
    merged = JudgmentPatch(
        role_updates=role,
        interest_updates=interest,
        meal_updates=meal,
        requirement_group_updates=groups,
    )
    return _fresh_patch(merged)


def apply_judgment_patch(current: JudgmentPatch, incoming: JudgmentPatch) -> JudgmentPatch:
    return merge_judgment_patch(current, incoming)


__all__ = (
    "validate_judgment_patch", "evidence_handle_indexes", "evidence_ref_directory",
    "merge_judgment_patch", "apply_judgment_patch",
)
