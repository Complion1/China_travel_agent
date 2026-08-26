"""Module 07 strict JSON fresh-copy 和 ArtifactWriteRequest 序列化。"""

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from app.hotel_selection import (
    HotelCandidate,
    HotelCandidateDiscoveryFailed,
    HotelCandidateDiscoveryFailureCode,
    HotelCandidatesReadyRecord,
    HotelCandidatesInsufficientRecord,
    HotelDiscoveryArtifact,
    HotelPlaceSearchArtifact,
    HotelPlaceEvidence,
    HotelSearchFailedEvidence,
    HotelSearchNoResultEvidence,
    HotelSearchSuccessEvidence,
    HotelMapFailureCode,
    HotelSelectionArtifact,
    HotelSelectedDecision,
    HotelDeclinedDecision,
)
from app.hotel_selection.artifacts import (
    HotelDiscoveryResult,
    HotelSearchEvidence,
    HotelSelectionDecision,
    _artifact_write_request,
)
from app.hotel_selection.models import HotelCoordinate
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 8, 25, tzinfo=timezone.utc)


def op(step: str) -> OperationId:
    return OperationId(run_id=RUN_ID, phase=WorkflowPhase.HOTEL_SELECTION, logical_step=step)


def ref(step: str, kind: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=UUID("87654321-4321-8765-4321-876543218765"),
        artifact_type=kind,
        schema_version="1.0",
        content_hash="sha256:" + "0" * 64,
        operation_id=op(step),
        created_at=UTC,
        size_bytes=1,
    )


def candidate(uid: str) -> HotelCandidate:
    return HotelCandidate(
        schema_version="1.0",
        uid=uid,
        name="测试酒店",
        address="测试地址",
        coordinate=HotelCoordinate(
            schema_version="1.0", coordinate_system="bd09ll", latitude=30.2, longitude=120.1
        ),
        category="酒店",
    )


def test_unions_round_trip_to_independent_objects() -> None:
    evidence = HotelSearchFailedEvidence(
        schema_version="1.0", outcome_type="failed", failure_code=HotelMapFailureCode.TIMEOUT_ERROR
    )
    restored_evidence = TypeAdapter(HotelSearchEvidence).validate_json(
        evidence.model_dump_json(), strict=True
    )
    assert type(restored_evidence) is HotelSearchFailedEvidence
    assert restored_evidence is not evidence
    result = HotelCandidatesReadyRecord(
        schema_version="1.0", result_type="ready", candidates=tuple(candidate(f"u{n}") for n in range(3))
    )
    assert type(TypeAdapter(HotelDiscoveryResult).validate_json(result.model_dump_json(), strict=True)) is HotelCandidatesReadyRecord
    decision = HotelDeclinedDecision(schema_version="1.0", decision_type="declined")
    assert type(TypeAdapter(HotelSelectionDecision).validate_json(decision.model_dump_json(), strict=True)) is HotelDeclinedDecision


def test_all_closed_artifact_unions_round_trip_every_branch_and_reject_unknown_discriminators() -> None:
    search_values = (
        HotelSearchSuccessEvidence(schema_version="1.0", outcome_type="success", places=(HotelPlaceEvidence(
            schema_version="1.0",
            uid="uid-1",
            name="酒店",
            address="地址",
            coordinate=HotelCoordinate(
                schema_version="1.0", coordinate_system="bd09ll", latitude=30.2, longitude=120.1
            ),
            category=None,
        ),)),
        HotelSearchNoResultEvidence(schema_version="1.0", outcome_type="no_result"),
        HotelSearchFailedEvidence(
            schema_version="1.0", outcome_type="failed", failure_code=HotelMapFailureCode.TIMEOUT_ERROR
        ),
    )
    discovery_values = (
        HotelCandidatesReadyRecord(
            schema_version="1.0",
            result_type="ready",
            candidates=tuple(candidate(f"u{n}") for n in range(3)),
        ),
        HotelCandidatesInsufficientRecord(
            schema_version="1.0", result_type="insufficient", candidates=()
        ),
    )
    decision_values = (
        HotelSelectedDecision(
            schema_version="1.0", decision_type="selected", selected_candidate=candidate("selected")
        ),
        HotelDeclinedDecision(schema_version="1.0", decision_type="declined"),
    )
    union_cases = (
        (HotelSearchEvidence, search_values, "outcome_type"),
        (HotelDiscoveryResult, discovery_values, "result_type"),
        (HotelSelectionDecision, decision_values, "decision_type"),
    )
    for union_type, values, discriminator in union_cases:
        for value in values:
            restored = TypeAdapter(union_type).validate_json(value.model_dump_json(), strict=True)
            assert type(restored) is type(value)
            assert restored is not value
            assert restored.model_dump() == value.model_dump()
            assert getattr(restored, discriminator) == getattr(value, discriminator)
        with pytest.raises(ValidationError):
            TypeAdapter(union_type).validate_json(
                json.dumps({discriminator: "unknown"}), strict=True
            )


def test_all_artifacts_produce_json_safe_write_content() -> None:
    search = HotelPlaceSearchArtifact(
        schema_version="1.0",
        search_ordinal=1,
        request_hash="sha256:" + "1" * 64,
        source="baidu_maps_mcp",
        verified=False,
        retrieved_at=UTC,
        evidence=HotelSearchFailedEvidence(
            schema_version="1.0", outcome_type="failed", failure_code=HotelMapFailureCode.RESPONSE_ERROR
        ),
    )
    discovery = HotelDiscoveryArtifact(
        schema_version="1.0",
        confirmation_summary_digest="digest",
        query_plan_hash="sha256:" + "2" * 64,
        search_operation_ids=(op("s1"), op("s2"), op("s3")),
        search_refs=(ref("s1", "hotel.place_search"),),
        result=HotelCandidatesReadyRecord(
            schema_version="1.0", result_type="ready", candidates=tuple(candidate(f"u{n}") for n in range(3))
        ),
    )
    selection = HotelSelectionArtifact(
        schema_version="1.0",
        candidate_set_ref=ref("candidate_set", "hotel.candidate_set"),
        decision=HotelDeclinedDecision(schema_version="1.0", decision_type="declined"),
    )
    for operation, kind, artifact in (
        (op("s1"), "hotel.place_search", search),
        (op("candidate_set"), "hotel.candidate_set", discovery),
        (op("selection"), "hotel.selection", selection),
    ):
        request = _artifact_write_request(operation_id=operation, artifact_type=kind, artifact=artifact)
        assert type(request.content) is dict
        assert all(not isinstance(value, (datetime, UUID)) for value in request.content.values())


def test_unknown_discriminators_and_extra_fields_fail_strictly() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(HotelSearchEvidence).validate_python(
            {"schema_version": "1.0", "outcome_type": "unknown"}, strict=True
        )
    with pytest.raises(ValidationError):
        HotelPlaceSearchArtifact(
            schema_version="1.0",
            search_ordinal=1,
            request_hash="sha256:" + "1" * 64,
            source="baidu_maps_mcp",
            verified=False,
            retrieved_at=UTC,
            evidence=HotelSearchFailedEvidence(
                schema_version="1.0", outcome_type="failed", failure_code=HotelMapFailureCode.RESPONSE_ERROR
            ),
            extra="forbidden",
        )


def test_required_nullable_fields_and_nested_candidate_category_fail_closed() -> None:
    with pytest.raises(ValidationError):
        HotelPlaceEvidence(
            schema_version="1.0",
            uid="uid-1",
            name="酒店",
            address="地址",
            coordinate=HotelCoordinate(
                schema_version="1.0", coordinate_system="bd09ll", latitude=30.2, longitude=120.1
            ),
        )
    with pytest.raises(ValidationError):
        HotelCandidateDiscoveryFailed(
            reason=HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED,
            failed_search_ordinal=1,
            qualified_count=0,
        )

    discovery = HotelDiscoveryArtifact(
        schema_version="1.0",
        confirmation_summary_digest="digest",
        query_plan_hash="sha256:" + "2" * 64,
        search_operation_ids=(op("s1"), op("s2"), op("s3")),
        search_refs=(ref("s1", "hotel.place_search"),),
        result=HotelCandidatesReadyRecord(
            schema_version="1.0",
            result_type="ready",
            candidates=tuple(candidate(f"u{n}") for n in range(3)),
        ),
    )
    payload = discovery.model_dump(mode="json")
    payload["result"]["candidates"][0]["category"] = "餐饮"
    with pytest.raises(ValidationError):
        TypeAdapter(HotelDiscoveryArtifact).validate_json(
            json.dumps(payload, ensure_ascii=False), strict=True
        )
