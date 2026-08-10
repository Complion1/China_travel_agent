from __future__ import annotations

from app.requirements.extraction_contract import (
    ATTR_TO_PATH,
    EvidenceVerified,
    ExtractionRepairFeedback,
    ExtractionRepairRequired,
    RequirementRepairCode,
    StrictFieldEvidenceDTO,
    StrictRequirementPatchDTO,
)
from app.requirements.models import (
    RequirementFieldPath,
    VerifiedFieldEvidence,
)


def enumerate_non_null_leaves(
    dto: StrictRequirementPatchDTO,
) -> list[tuple[RequirementFieldPath, object]]:
    leaves: list[tuple[RequirementFieldPath, object]] = []
    for attr, fp in ATTR_TO_PATH.items():
        val = getattr(dto, attr, None)
        if val is None:
            continue
        leaves.append((RequirementFieldPath(fp), val))
    return leaves


def group_evidence_by_field(
    evidence: list[StrictFieldEvidenceDTO],
) -> dict[RequirementFieldPath, list[StrictFieldEvidenceDTO]]:
    grouped: dict[RequirementFieldPath, list[StrictFieldEvidenceDTO]] = {}
    for e in evidence:
        grouped.setdefault(e.field_path, []).append(e)
    return grouped


def _repair(
    code: RequirementRepairCode,
    paths: set[RequirementFieldPath],
) -> ExtractionRepairRequired:
    return ExtractionRepairRequired(
        feedback=ExtractionRepairFeedback(
            code=code,
            field_paths=tuple(paths),
        )
    )


def verify_evidence(
    dto: StrictRequirementPatchDTO,
    user_message: str,
) -> EvidenceVerified | ExtractionRepairRequired:
    leaf_paths = {path for path, _value in enumerate_non_null_leaves(dto)}
    grouped = group_evidence_by_field(dto.evidence)

    missing = {path for path in leaf_paths if len(grouped.get(path, ())) == 0}
    if missing:
        return _repair("evidence_missing", missing)

    duplicate = {path for path in leaf_paths if len(grouped.get(path, ())) > 1}
    if duplicate:
        return _repair("evidence_duplicate", duplicate)

    not_substring = {
        path
        for path in leaf_paths
        if grouped[path][0].text not in user_message
    }
    if not_substring:
        return _repair("evidence_not_substring", not_substring)

    orphan = set(grouped) - leaf_paths
    if orphan:
        return _repair("orphan_evidence", orphan)

    verified = tuple(
        VerifiedFieldEvidence(field_path=path, text=grouped[path][0].text)
        for path in sorted(leaf_paths, key=lambda item: item.value)
    )
    return EvidenceVerified(evidence=verified)
