"""Judgment patch 的句柄可见性校验与确定性合并。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json

from pydantic import TypeAdapter

from app.workflow_contracts import ArtifactRef

from .models import (
    EvidenceHandle, JudgmentPatch, RequirementGroupJudgmentUpdate,
    RoleJudgmentUpdate, InterestJudgmentUpdate, MealJudgmentUpdate, RequirementVerdictException,
)
from .fingerprints import candidate_handle, evidence_handle


class JudgmentEvidenceMismatch(ValueError):
    """Known evidence attached to the wrong visible candidate is correctable."""


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

    # Unknown references anywhere in the patch are fatal, even when an earlier
    # entry has a correctable ownership error. Classify the whole batch first.
    updates = (*fresh.role_updates, *fresh.interest_updates, *fresh.meal_updates)
    for update in updates:
        if update.candidate_handle not in candidates or any(item.handle_id not in evidence for item in update.evidence_handles):
            raise ValueError("unknown judgment reference")
    for group in fresh.requirement_group_updates:
        handles = (*group.default_evidence_handles, *(h for item in group.exceptions for h in item.evidence_handles))
        if (not set(group.evaluated_candidate_handles).issubset(candidates)
                or any(item.candidate_handle not in candidates for item in group.exceptions)
                or any(item.handle_id not in evidence for item in handles)):
            raise ValueError("unknown judgment reference")

    def require_owned(candidate: str, handles: Sequence[EvidenceHandle]) -> None:
        if any(item.handle_id not in evidence for item in handles):
            raise ValueError("unknown evidence handle")
        if any(item.handle_id not in owned_evidence[candidate] for item in handles):
            raise JudgmentEvidenceMismatch("evidence handle does not belong to candidate")

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


def merge_requirement_update(current: RequirementGroupJudgmentUpdate,
                             incoming: RequirementGroupJudgmentUpdate) -> RequirementGroupJudgmentUpdate:
    """Defaults are scoped to each update's evaluated set, never the union."""
    if (current.requirement_group_id, current.requirement_kind) != (
            incoming.requirement_group_id, incoming.requirement_kind):
        raise ValueError("requirement group identity mismatch")
    values = {}
    for group in (current, incoming):
        exceptions = {item.candidate_handle: item for item in group.exceptions}
        for handle in group.evaluated_candidate_handles:
            item = exceptions.get(handle)
            values[handle] = (item.verdict, item.reason_code, item.evidence_handles) if item else (
                group.default_verdict, group.default_reason_code, group.default_evidence_handles)
    if not values:
        return current
    handles = tuple(sorted(values, key=lambda value: int(value[2:])))
    default = values[handles[0]]
    return RequirementGroupJudgmentUpdate(requirement_group_id=current.requirement_group_id,
        requirement_kind=current.requirement_kind, evaluated_candidate_handles=handles,
        default_verdict=default[0], default_reason_code=default[1], default_evidence_handles=default[2],
        exceptions=tuple(RequirementVerdictException(candidate_handle=handle,
            verdict=values[handle][0], reason_code=values[handle][1], evidence_handles=values[handle][2])
            for handle in handles if values[handle] != default))


def merge_requirement_group_state(current, incoming):
    """Persisted and public patch merging share the same scoped reducer."""
    from .artifacts import ArtifactRequirementGroupState, ArtifactRequirementVerdictOverrideState
    if current.requirement_item_ids != incoming.requirement_item_ids:
        raise ValueError("requirement items cannot change during research")
    def public(group):
        return RequirementGroupJudgmentUpdate(requirement_group_id=group.group_id,
            requirement_kind=group.requirement_kind,
            evaluated_candidate_handles=tuple(candidate_handle(index) for index in group.evaluated_candidate_ordinals),
            default_verdict=group.default_verdict, default_reason_code=group.default_reason_code,
            default_evidence_handles=tuple(EvidenceHandle(handle_id=evidence_handle(index)) for index in group.default_evidence_indexes),
            exceptions=tuple(RequirementVerdictException(candidate_handle=candidate_handle(item.candidate_ordinal),
                verdict=item.verdict, reason_code=item.reason_code,
                evidence_handles=tuple(EvidenceHandle(handle_id=evidence_handle(index)) for index in item.evidence_indexes))
                for item in group.exceptions))
    merged = merge_requirement_update(public(current), public(incoming))
    return ArtifactRequirementGroupState(group_id=current.group_id, requirement_kind=current.requirement_kind,
        requirement_item_ids=current.requirement_item_ids,
        evaluated_candidate_ordinals=tuple(int(handle[2:]) for handle in merged.evaluated_candidate_handles),
        default_verdict=merged.default_verdict, default_reason_code=merged.default_reason_code,
        default_evidence_indexes=tuple(int(handle.handle_id[2:]) for handle in merged.default_evidence_handles),
        exceptions=tuple(ArtifactRequirementVerdictOverrideState(candidate_ordinal=int(item.candidate_handle[2:]),
            verdict=item.verdict, reason_code=item.reason_code,
            evidence_indexes=tuple(int(handle.handle_id[2:]) for handle in item.evidence_handles)) for item in merged.exceptions))


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
    groups = {item.requirement_group_id: item for item in base.requirement_group_updates}
    for item in patch.requirement_group_updates:
        groups[item.requirement_group_id] = merge_requirement_update(groups[item.requirement_group_id], item) if item.requirement_group_id in groups else item
    merged = JudgmentPatch(
        role_updates=role,
        interest_updates=interest,
        meal_updates=meal,
        requirement_group_updates=tuple(groups.values()),
    )
    return _fresh_patch(merged)


def apply_judgment_patch(current: JudgmentPatch, incoming: JudgmentPatch) -> JudgmentPatch:
    return merge_judgment_patch(current, incoming)


__all__ = (
    "validate_judgment_patch", "evidence_handle_indexes", "evidence_ref_directory",
    "merge_judgment_patch", "apply_judgment_patch",
)
