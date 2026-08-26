"""Module 07 三类 Artifact 的字段、不变量与注册顺序。"""

from datetime import datetime, timezone
from typing import get_origin
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.hotel_selection import (
    HOTEL_ARTIFACT_REGISTRATIONS,
    HotelCandidate,
    HotelCandidatesInsufficientRecord,
    HotelCandidatesReadyRecord,
    HotelDeclinedDecision,
    HotelDiscoveryArtifact,
    HotelMapFailureCode,
    HotelPlaceEvidence,
    HotelPlaceSearchArtifact,
    HotelSearchFailedEvidence,
    HotelSearchNoResultEvidence,
    HotelSearchSuccessEvidence,
    HotelSelectedDecision,
    HotelSelectionArtifact,
)
from app.hotel_selection.artifacts import _artifact_write_request
from app.hotel_selection.errors import HotelInputError
from app.hotel_selection.models import HotelCoordinate
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 8, 25, tzinfo=timezone.utc)


def op(step: str) -> OperationId:
    return OperationId(run_id=RUN_ID, phase=WorkflowPhase.HOTEL_SELECTION, logical_step=step)


def ref(step: str, artifact_type: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=UUID("87654321-4321-8765-4321-876543218765"),
        artifact_type=artifact_type,
        schema_version="1.0",
        content_hash="sha256:" + "1" * 64,
        operation_id=op(step),
        created_at=UTC,
        size_bytes=1,
    )


def place(uid: str = "uid-1") -> HotelPlaceEvidence:
    return HotelPlaceEvidence(
        schema_version="1.0",
        uid=uid,
        name=f"酒店-{uid}",
        address="虚构地址",
        coordinate=HotelCoordinate(
            schema_version="1.0", coordinate_system="bd09ll", latitude=30.2, longitude=120.1
        ),
        category="酒店",
    )


def candidate(uid: str = "uid-1") -> HotelCandidate:
    evidence = place(uid)
    return HotelCandidate(**evidence.model_dump())


def discovery_refs() -> tuple[ArtifactRef, ...]:
    return tuple(ref(f"search_{index}", "hotel.place_search") for index in range(1, 4))


def test_search_evidence_and_provenance_contract() -> None:
    success = HotelSearchSuccessEvidence(
        schema_version="1.0", outcome_type="success", places=(place(),)
    )
    no_result = HotelSearchNoResultEvidence(schema_version="1.0", outcome_type="no_result")
    failed = HotelSearchFailedEvidence(
        schema_version="1.0",
        outcome_type="failed",
        failure_code=HotelMapFailureCode.TIMEOUT_ERROR,
    )
    for evidence, verified in ((success, True), (no_result, True), (failed, False)):
        artifact = HotelPlaceSearchArtifact(
            schema_version="1.0",
            search_ordinal=1,
            request_hash="sha256:" + "2" * 64,
            source="baidu_maps_mcp",
            verified=verified,
            retrieved_at=UTC,
            evidence=evidence,
        )
        assert artifact.evidence == evidence
    with pytest.raises(ValidationError):
        HotelPlaceSearchArtifact(
            schema_version="1.0",
            search_ordinal=1,
            request_hash="sha256:" + "2" * 64,
            source="baidu_maps_mcp",
            verified=False,
            retrieved_at=UTC,
            evidence=success,
        )
    with pytest.raises(ValidationError):
        HotelPlaceSearchArtifact(
            schema_version="1.0",
            search_ordinal=1,
            request_hash="sha256:" + "2" * 64,
            source="baidu_maps_mcp",
            verified=False,
            retrieved_at=datetime(2026, 8, 25),
            evidence=failed,
        )


def test_artifact_schema_matrix_is_ordered_strict_and_all_fields_required() -> None:
    matrix = (
        (HotelSearchSuccessEvidence, ("schema_version", "outcome_type", "places")),
        (HotelSearchNoResultEvidence, ("schema_version", "outcome_type")),
        (HotelSearchFailedEvidence, ("schema_version", "outcome_type", "failure_code")),
        (HotelPlaceSearchArtifact, ("schema_version", "search_ordinal", "request_hash", "source", "verified", "retrieved_at", "evidence")),
        (HotelCandidatesReadyRecord, ("schema_version", "result_type", "candidates")),
        (HotelCandidatesInsufficientRecord, ("schema_version", "result_type", "candidates")),
        (HotelDiscoveryArtifact, ("schema_version", "confirmation_summary_digest", "query_plan_hash", "search_operation_ids", "search_refs", "result")),
        (HotelSelectedDecision, ("schema_version", "decision_type", "selected_candidate")),
        (HotelDeclinedDecision, ("schema_version", "decision_type")),
        (HotelSelectionArtifact, ("schema_version", "candidate_set_ref", "decision")),
    )
    for model, fields in matrix:
        assert tuple(model.model_fields) == fields
        assert all(field.is_required() for field in model.model_fields.values())
        assert dict(model.model_config) == {"strict": True, "frozen": True, "extra": "forbid"}


def test_artifact_tuple_fields_are_declared_ordered_and_strict_with_length_bounds() -> None:
    tuple_fields = (
        (HotelSearchSuccessEvidence, "places"),
        (HotelCandidatesReadyRecord, "candidates"),
        (HotelCandidatesInsufficientRecord, "candidates"),
        (HotelDiscoveryArtifact, "search_operation_ids"),
        (HotelDiscoveryArtifact, "search_refs"),
    )
    for model, field_name in tuple_fields:
        assert get_origin(model.model_fields[field_name].annotation) is tuple

    places = tuple(place(f"place-{index}") for index in range(10))
    assert len(HotelSearchSuccessEvidence(schema_version="1.0", outcome_type="success", places=places).places) == 10
    with pytest.raises(ValidationError):
        HotelSearchSuccessEvidence(schema_version="1.0", outcome_type="success", places=[place()])
    with pytest.raises(ValidationError):
        HotelSearchSuccessEvidence(schema_version="1.0", outcome_type="success", places=())
    with pytest.raises(ValidationError):
        HotelSearchSuccessEvidence(
            schema_version="1.0",
            outcome_type="success",
            places=tuple(place(f"place-{index}") for index in range(11)),
        )

    candidates = tuple(candidate(f"uid-{index}") for index in range(5))
    assert len(HotelCandidatesReadyRecord(schema_version="1.0", result_type="ready", candidates=candidates).candidates) == 5
    with pytest.raises(ValidationError):
        HotelCandidatesReadyRecord(
            schema_version="1.0", result_type="ready", candidates=list(candidates[:3])
        )
    with pytest.raises(ValidationError):
        HotelCandidatesReadyRecord(
            schema_version="1.0", result_type="ready", candidates=candidates[:2]
        )
    with pytest.raises(ValidationError):
        HotelCandidatesReadyRecord(
            schema_version="1.0", result_type="ready", candidates=(*candidates, candidate("uid-5"))
        )
    assert HotelCandidatesInsufficientRecord(
        schema_version="1.0", result_type="insufficient", candidates=()
    ).candidates == ()
    with pytest.raises(ValidationError):
        HotelCandidatesInsufficientRecord(
            schema_version="1.0", result_type="insufficient", candidates=list(candidates[:1])
        )
    with pytest.raises(ValidationError):
        HotelCandidatesInsufficientRecord(
            schema_version="1.0", result_type="insufficient", candidates=candidates[:3]
        )

    operations = tuple(op(f"search_{index}") for index in range(1, 4))
    refs = discovery_refs()
    ready = HotelCandidatesReadyRecord(
        schema_version="1.0", result_type="ready", candidates=candidates[:3]
    )
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=list(operations),
            search_refs=refs[:1],
            result=ready,
        )
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=operations[:2],
            search_refs=refs[:1],
            result=ready,
        )
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=operations,
            search_refs=list(refs[:1]),
            result=ready,
        )
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=operations,
            search_refs=(),
            result=ready,
        )
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=operations,
            search_refs=(*refs, ref("search_4", "hotel.place_search")),
            result=ready,
        )


def test_candidate_set_and_selection_artifacts_bind_refs_and_uids() -> None:
    operations = tuple(op(f"search_{index}") for index in range(1, 4))
    refs = discovery_refs()
    ready = HotelDiscoveryArtifact(
        schema_version="1.0",
        confirmation_summary_digest="digest",
        query_plan_hash="sha256:" + "3" * 64,
        search_operation_ids=operations,
        search_refs=refs[:1],
        result=HotelCandidatesReadyRecord(
            schema_version="1.0",
            result_type="ready",
            candidates=tuple(candidate(f"uid-{index}") for index in range(3)),
        ),
    )
    assert ready.result.result_type == "ready"
    insufficient = HotelDiscoveryArtifact(
        schema_version="1.0",
        confirmation_summary_digest="digest",
        query_plan_hash="sha256:" + "3" * 64,
        search_operation_ids=operations,
        search_refs=refs,
        result=HotelCandidatesInsufficientRecord(
            schema_version="1.0", result_type="insufficient", candidates=()
        ),
    )
    assert insufficient.result.result_type == "insufficient"
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=operations,
            search_refs=(refs[1],),
            result=ready.result,
        )
    selected = HotelSelectionArtifact(
        schema_version="1.0",
        candidate_set_ref=ref("candidate_set", "hotel.candidate_set"),
        decision=HotelSelectedDecision(
            schema_version="1.0", decision_type="selected", selected_candidate=candidate()
        ),
    )
    declined = HotelSelectionArtifact(
        schema_version="1.0",
        candidate_set_ref=ref("candidate_set", "hotel.candidate_set"),
        decision=HotelDeclinedDecision(schema_version="1.0", decision_type="declined"),
    )
    assert selected.decision.decision_type == "selected"
    assert declined.decision.decision_type == "declined"


def test_registration_order_and_single_write_helper_use_json_content() -> None:
    assert tuple((item.artifact_type, item.schema_version) for item in HOTEL_ARTIFACT_REGISTRATIONS) == (
        ("hotel.place_search", "1.0"),
        ("hotel.candidate_set", "1.0"),
        ("hotel.selection", "1.0"),
    )
    artifact = HotelPlaceSearchArtifact(
        schema_version="1.0",
        search_ordinal=1,
        request_hash="sha256:" + "2" * 64,
        source="baidu_maps_mcp",
        verified=False,
        retrieved_at=UTC,
        evidence=HotelSearchFailedEvidence(
            schema_version="1.0", outcome_type="failed", failure_code=HotelMapFailureCode.RESPONSE_ERROR
        ),
    )
    request = _artifact_write_request(
        operation_id=op("search_1"), artifact_type="hotel.place_search", artifact=artifact
    )
    assert type(request.content) is dict
    assert request.content["retrieved_at"].endswith("Z")
    assert request.content["evidence"]["failure_code"] == "response_error"


def test_artifact_binding_validators_cover_duplicate_and_wrong_reference_shapes() -> None:
    operations = tuple(op(f"search_{index}") for index in range(1, 4))
    refs = discovery_refs()
    ready = HotelCandidatesReadyRecord(
        schema_version="1.0",
        result_type="ready",
        candidates=tuple(candidate(f"uid-{index}") for index in range(3)),
    )
    with pytest.raises(ValidationError):
        HotelCandidatesReadyRecord(
            schema_version="1.0", result_type="ready", candidates=(candidate("same"),) * 3
        )
    with pytest.raises(ValidationError):
        HotelCandidatesInsufficientRecord(
            schema_version="1.0", result_type="insufficient", candidates=(candidate("same"),) * 2
        )
    for bad_operations in (
        (
            OperationId(run_id=RUN_ID, phase=WorkflowPhase.REQUIREMENTS, logical_step="search_1"),
            operations[1],
            operations[2],
        ),
        (operations[0], operations[0], operations[2]),
        (
            operations[0],
            operations[1],
            OperationId(
                run_id=UUID("12345678-1234-5678-1234-567812345679"),
                phase=WorkflowPhase.HOTEL_SELECTION,
                logical_step="search_3",
            ),
        ),
    ):
        with pytest.raises(ValidationError):
            HotelDiscoveryArtifact(
                schema_version="1.0",
                confirmation_summary_digest="digest",
                query_plan_hash="sha256:" + "3" * 64,
                search_operation_ids=bad_operations,
                search_refs=refs[:1],
                result=ready,
            )
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=operations,
            search_refs=(refs[0], refs[0]),
            result=ready,
        )
    insufficient = HotelCandidatesInsufficientRecord.model_construct(
        schema_version="1.0", result_type="insufficient", candidates=()
    )
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=operations,
            search_refs=refs[:1],
            result=insufficient,
        )
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=operations,
            search_refs=(ref("bad", "hotel.selection"),),
            result=ready,
        )
    duplicate_result = HotelCandidatesReadyRecord.model_construct(
        schema_version="1.0", result_type="ready", candidates=(candidate("same"),) * 3
    )
    forged_artifact = HotelDiscoveryArtifact.model_construct(
        schema_version="1.0",
        confirmation_summary_digest="digest",
        query_plan_hash="sha256:" + "3" * 64,
        search_operation_ids=operations,
        search_refs=refs[:1],
        result=duplicate_result,
    )
    with pytest.raises(ValueError):
        forged_artifact._validate_search_bindings()
    with pytest.raises(ValidationError):
        HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest="digest",
            query_plan_hash="sha256:" + "3" * 64,
            search_operation_ids=operations,
            search_refs=refs[:1],
            result=duplicate_result,
        )
    with pytest.raises(ValidationError):
        HotelSelectionArtifact(
            schema_version="1.0",
            candidate_set_ref=ref("wrong", "hotel.place_search"),
            decision=HotelDeclinedDecision(schema_version="1.0", decision_type="declined"),
        )

    class NotAnObject:
        def model_dump(self, **_: object) -> object:
            return []

    with pytest.raises(HotelInputError):
        _artifact_write_request(
            operation_id=op("search_1"),
            artifact_type="hotel.place_search",
            artifact=NotAnObject(),
        )
