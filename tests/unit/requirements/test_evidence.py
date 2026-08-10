"""Evidence binding tests for the strict extraction DTO."""

from app.requirements.evidence import (
    enumerate_non_null_leaves,
    group_evidence_by_field,
    verify_evidence,
)
from app.requirements.extraction_contract import (
    EvidenceVerified,
    ExtractionRepairRequired,
    StrictFieldEvidenceDTO,
)
from app.requirements.models import RequirementFieldPath
from tests.unit.requirements.fakes import strict_dto


def evidence(path: RequirementFieldPath, text: str) -> StrictFieldEvidenceDTO:
    return StrictFieldEvidenceDTO(field_path=path, text=text)


def test_enumerate_non_null_leaves_finds_destination_and_skips_nulls():
    leaves = enumerate_non_null_leaves(strict_dto(destination="杭州"))
    assert leaves == [(RequirementFieldPath.DESTINATION, "杭州")]


def test_enumerate_non_null_leaves_keeps_false_and_empty_list_as_explicit_values():
    leaves = enumerate_non_null_leaves(
        strict_dto(arrival_is_approximate=False, children_ages=[])
    )
    assert (RequirementFieldPath.ARRIVAL_IS_APPROXIMATE, False) in leaves
    assert (RequirementFieldPath.CHILDREN_AGES, []) in leaves


def test_group_evidence_by_field_uses_canonical_path_enum():
    grouped = group_evidence_by_field(
        [evidence(RequirementFieldPath.DESTINATION, "杭州")]
    )
    assert list(grouped) == [RequirementFieldPath.DESTINATION]


def test_verify_evidence_passes_and_returns_typed_evidence():
    dto = strict_dto(
        destination="杭州",
        evidence=[evidence(RequirementFieldPath.DESTINATION, "杭州")],
    )
    result = verify_evidence(dto, "我想去杭州")
    assert isinstance(result, EvidenceVerified)
    assert result.evidence[0].field_path is RequirementFieldPath.DESTINATION
    assert result.evidence[0].text == "杭州"


def test_verify_evidence_distinguishes_missing_and_duplicate_evidence():
    missing = strict_dto(destination="杭州")
    missing_result = verify_evidence(missing, "我想去杭州")
    assert isinstance(missing_result, ExtractionRepairRequired)
    assert missing_result.feedback.code == "evidence_missing"
    assert missing_result.feedback.field_paths == (RequirementFieldPath.DESTINATION,)

    duplicate = strict_dto(
        destination="杭州",
        evidence=[
            evidence(RequirementFieldPath.DESTINATION, "杭州"),
            evidence(RequirementFieldPath.DESTINATION, "我想去杭州"),
        ],
    )
    duplicate_result = verify_evidence(duplicate, "我想去杭州")
    assert isinstance(duplicate_result, ExtractionRepairRequired)
    assert duplicate_result.feedback.code == "evidence_duplicate"


def test_verify_evidence_rejects_text_not_in_original_message():
    dto = strict_dto(
        destination="北京",
        evidence=[evidence(RequirementFieldPath.DESTINATION, "北京")],
    )
    result = verify_evidence(dto, "我想去杭州")
    assert isinstance(result, ExtractionRepairRequired)
    assert result.feedback.code == "evidence_not_substring"


def test_verify_evidence_rejects_orphan_evidence():
    dto = strict_dto(
        evidence=[evidence(RequirementFieldPath.DESTINATION, "杭州")]
    )
    result = verify_evidence(dto, "我想去杭州")
    assert isinstance(result, ExtractionRepairRequired)
    assert result.feedback.code == "orphan_evidence"


def test_explicit_empty_list_still_requires_evidence():
    dto = strict_dto(children_ages=[])
    result = verify_evidence(dto, "没有儿童")
    assert isinstance(result, ExtractionRepairRequired)
    assert result.feedback.code == "evidence_missing"

    dto = strict_dto(
        children_ages=[],
        evidence=[evidence(RequirementFieldPath.CHILDREN_AGES, "没有儿童")],
    )
    verified = verify_evidence(dto, "没有儿童")
    assert isinstance(verified, EvidenceVerified)
    assert verified.evidence[0].field_path is RequirementFieldPath.CHILDREN_AGES
