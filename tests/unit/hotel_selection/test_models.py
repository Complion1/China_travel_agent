"""Module 07 公共 DTO 的严格字段和判别 union 合同。"""

from __future__ import annotations

import json
from typing import get_origin
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from app.hotel_selection import (
    HotelCandidate,
    HotelCandidateDiscoveryFailed,
    HotelCandidateDiscoveryFailureCode,
    HotelCandidateDiscoveryOutcome,
    HotelCandidateOperationIds,
    HotelCandidatesInsufficient,
    HotelCandidatesReady,
    HotelCoordinate,
    HotelMapFailureCode,
    HotelPlaceEvidence,
    HotelSelectCommand,
    HotelDeclineCommand,
    HotelSelectionCommand,
    HotelSelectionOutcome,
    HotelSelectionRequest,
    HotelSelectionRejected,
    HotelSelectionRejectionCode,
    HotelSelected,
    HotelSelectionDeclined,
)
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")


def operation(step: str) -> OperationId:
    return OperationId(run_id=RUN_ID, phase=WorkflowPhase.HOTEL_SELECTION, logical_step=step)


def ref(step: str, artifact_type: str = "hotel.candidate_set") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=UUID("87654321-4321-8765-4321-876543218765"),
        artifact_type=artifact_type,
        schema_version="1.0",
        content_hash="sha256:" + "0" * 64,
        operation_id=operation(step),
        created_at=__import__("datetime").datetime(2026, 8, 25, tzinfo=__import__("datetime").timezone.utc),
        size_bytes=1,
    )


def coordinate(lat: float = 30.25, lon: float = 120.15) -> HotelCoordinate:
    return HotelCoordinate(
        schema_version="1.0",
        coordinate_system="bd09ll",
        latitude=lat,
        longitude=lon,
    )


def candidate(uid: str = "uid-1") -> HotelCandidate:
    return HotelCandidate(
        schema_version="1.0",
        uid=uid,
        name=f"酒店-{uid}",
        address="虚构地址",
        coordinate=coordinate(),
        category="酒店",
    )


def test_models_are_strict_frozen_forbidden_and_ordered() -> None:
    models = (HotelCoordinate, HotelPlaceEvidence, HotelCandidate, HotelCandidateOperationIds)
    expected = {"strict": True, "frozen": True, "extra": "forbid"}
    for model in models:
        assert dict(model.model_config) == expected
    assert tuple(HotelCoordinate.model_fields) == (
        "schema_version",
        "coordinate_system",
        "latitude",
        "longitude",
    )
    assert tuple(HotelCandidate.model_fields) == (
        "schema_version",
        "uid",
        "name",
        "address",
        "coordinate",
        "category",
    )
    with pytest.raises(ValidationError):
        HotelCoordinate(
            schema_version="1.0",
            coordinate_system="bd09ll",
            latitude="30.25",
            longitude=120.15,
        )
    with pytest.raises(ValidationError):
        HotelCoordinate(
            schema_version="1.0",
            coordinate_system="bd09ll",
            latitude=91,
            longitude=120.15,
        )
    with pytest.raises(ValidationError):
        HotelCandidate(**candidate().model_dump(), extra="forbidden")
    value = candidate()
    with pytest.raises(ValidationError):
        value.uid = "changed"


def test_dto_field_matrix_covers_order_required_nullable_and_approved_defaults() -> None:
    matrix = (
        (HotelCoordinate, ("schema_version", "coordinate_system", "latitude", "longitude"), ("schema_version", "coordinate_system", "latitude", "longitude"), {}),
        (HotelPlaceEvidence, ("schema_version", "uid", "name", "address", "coordinate", "category"), ("schema_version", "uid", "name", "address", "coordinate", "category"), {}),
        (HotelCandidate, ("schema_version", "uid", "name", "address", "coordinate", "category"), ("schema_version", "uid", "name", "address", "coordinate", "category"), {}),
        (HotelCandidateOperationIds, ("schema_version", "search_operation_ids", "candidate_set_operation_id"), ("search_operation_ids", "candidate_set_operation_id"), {"schema_version": "1.0"}),
        (HotelSelectCommand, ("schema_version", "action", "selected_uid"), ("selected_uid",), {"schema_version": "1.0", "action": "select"}),
        (HotelDeclineCommand, ("schema_version", "action"), (), {"schema_version": "1.0", "action": "decline"}),
        (HotelSelectionRequest, ("schema_version", "selection_operation_id", "candidate_set_ref", "command"), ("selection_operation_id", "candidate_set_ref", "command"), {"schema_version": "1.0"}),
        (HotelCandidatesReady, ("schema_version", "outcome_type", "candidate_set_ref", "candidates", "disclaimer_code"), ("candidate_set_ref", "candidates"), {"schema_version": "1.0", "outcome_type": "ready", "disclaimer_code": "map_identity_only"}),
        (HotelCandidatesInsufficient, ("schema_version", "outcome_type", "candidate_set_ref", "qualified_candidates", "searches_attempted"), ("candidate_set_ref", "qualified_candidates", "searches_attempted"), {"schema_version": "1.0", "outcome_type": "insufficient"}),
        (HotelCandidateDiscoveryFailed, ("schema_version", "outcome_type", "reason", "failed_search_ordinal", "qualified_count", "failure_search_ref"), ("reason", "failed_search_ordinal", "qualified_count", "failure_search_ref"), {"schema_version": "1.0", "outcome_type": "failed"}),
        (HotelSelected, ("schema_version", "outcome_type", "selection_ref", "selected_candidate"), ("selection_ref", "selected_candidate"), {"schema_version": "1.0", "outcome_type": "selected"}),
        (HotelSelectionDeclined, ("schema_version", "outcome_type", "selection_ref"), ("selection_ref",), {"schema_version": "1.0", "outcome_type": "declined"}),
        (HotelSelectionRejected, ("schema_version", "outcome_type", "reason"), ("reason",), {"schema_version": "1.0", "outcome_type": "rejected"}),
    )
    for model, fields, required, defaults in matrix:
        assert tuple(model.model_fields) == fields
        assert tuple(name for name, field in model.model_fields.items() if field.is_required()) == required
        assert {
            name: field.default
            for name, field in model.model_fields.items()
            if not field.is_required()
        } == defaults
        assert dict(model.model_config) == {"strict": True, "frozen": True, "extra": "forbid"}


def test_enums_have_exact_values_and_operation_ids_are_distinct() -> None:
    assert {item.name: item.value for item in HotelMapFailureCode} == {
        "REQUEST_ERROR": "request_error",
        "CONFIGURATION_ERROR": "configuration_error",
        "AUTHENTICATION_ERROR": "authentication_error",
        "RATE_LIMIT_ERROR": "rate_limit_error",
        "TIMEOUT_ERROR": "timeout_error",
        "TRANSPORT_ERROR": "transport_error",
        "SESSION_ERROR": "session_error",
        "TOOL_UNAVAILABLE": "tool_unavailable",
        "TOOL_SCHEMA_ERROR": "tool_schema_error",
        "PROTOCOL_ERROR": "protocol_error",
        "RESPONSE_ERROR": "response_error",
        "CONCURRENCY_ERROR": "concurrency_error",
        "UNVERIFIED_RESULT": "unverified_result",
    }
    assert {item.name: item.value for item in HotelCandidateDiscoveryFailureCode} == {
        "BUDGET_EXHAUSTED": "budget_exhausted",
        "MAP_CALL_FAILED": "map_call_failed",
    }
    assert {item.name: item.value for item in HotelSelectionRejectionCode} == {
        "CANDIDATE_UID_NOT_FOUND": "candidate_uid_not_found",
        "CANDIDATE_SET_NOT_READY": "candidate_set_not_ready",
        "STALE_CANDIDATE_SET": "stale_candidate_set",
        "DECISION_CONFLICT": "decision_conflict",
    }
    ids = HotelCandidateOperationIds(
        search_operation_ids=(operation("search_one"), operation("search_two"), operation("search_three")),
        candidate_set_operation_id=operation("candidate_set"),
    )
    assert ids.schema_version == "1.0"
    with pytest.raises(ValidationError):
        HotelCandidateOperationIds(
            search_operation_ids=(operation("same"), operation("same"), operation("third")),
            candidate_set_operation_id=operation("candidate_set"),
        )


def test_discovery_and_selection_unions_round_trip_strict_json() -> None:
    candidates = tuple(candidate(f"uid-{index}") for index in range(3))
    ready = HotelCandidatesReady(candidate_set_ref=ref("candidate_set"), candidates=candidates)
    restored = TypeAdapter(HotelCandidateDiscoveryOutcome).validate_json(
        ready.model_dump_json(), strict=True
    )
    assert type(restored) is HotelCandidatesReady
    insufficient = HotelCandidatesInsufficient(
        candidate_set_ref=ref("candidate_set"),
        qualified_candidates=(candidate(),),
        searches_attempted=3,
    )
    assert type(TypeAdapter(HotelCandidateDiscoveryOutcome).validate_json(insufficient.model_dump_json(), strict=True)) is HotelCandidatesInsufficient
    failed = HotelCandidateDiscoveryFailed(
        reason=HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED,
        failed_search_ordinal=3,
        qualified_count=1,
        failure_search_ref=None,
    )
    assert TypeAdapter(HotelCandidateDiscoveryOutcome).validate_json(failed.model_dump_json(), strict=True) == failed
    selected = HotelSelected(selection_ref=ref("selection", "hotel.selection"), selected_candidate=candidate())
    declined = HotelSelectionDeclined(selection_ref=ref("selection", "hotel.selection"))
    rejected = HotelSelectionRejected(reason=HotelSelectionRejectionCode.CANDIDATE_UID_NOT_FOUND)
    for value in (selected, declined, rejected):
        assert type(TypeAdapter(HotelSelectionOutcome).validate_json(value.model_dump_json(), strict=True)) is type(value)
    assert TypeAdapter(HotelSelectionCommand).validate_python({"action": "select", "selected_uid": "uid-1"}, strict=True) == HotelSelectCommand(selected_uid="uid-1")
    assert TypeAdapter(HotelSelectionCommand).validate_python({"action": "decline"}, strict=True) == HotelDeclineCommand()


def test_nullable_fields_are_required_and_cross_constraints_are_exact() -> None:
    evidence = HotelPlaceEvidence(
        schema_version="1.0",
        uid="uid-1",
        name="酒店",
        address="地址",
        coordinate=coordinate(),
        category=None,
    )
    assert evidence.category is None
    with pytest.raises(ValidationError):
        HotelPlaceEvidence(
            schema_version="1.0",
            uid="uid-1",
            name="酒店",
            address="地址",
            coordinate=coordinate(),
        )

    candidate_payload = candidate().model_dump()
    with pytest.raises(ValidationError):
        HotelCandidate(**{**candidate_payload, "category": None})
    with pytest.raises(ValidationError):
        HotelCandidate(**{key: value for key, value in candidate_payload.items() if key != "category"})

    budget = HotelCandidateDiscoveryFailed(
        reason=HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED,
        failed_search_ordinal=1,
        qualified_count=0,
        failure_search_ref=None,
    )
    assert budget.failure_search_ref is None
    with pytest.raises(ValidationError):
        HotelCandidateDiscoveryFailed(
            reason=HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED,
            failed_search_ordinal=1,
            qualified_count=0,
        )
    map_failed = HotelCandidateDiscoveryFailed(
        reason=HotelCandidateDiscoveryFailureCode.MAP_CALL_FAILED,
        failed_search_ordinal=2,
        qualified_count=1,
        failure_search_ref=ref("search_two", "hotel.place_search"),
    )
    assert map_failed.failure_search_ref is not None
    with pytest.raises(ValidationError):
        HotelCandidateDiscoveryFailed(
            reason=HotelCandidateDiscoveryFailureCode.MAP_CALL_FAILED,
            failed_search_ordinal=2,
            qualified_count=1,
            failure_search_ref=None,
        )


def test_tuple_fields_reject_strict_python_lists_and_enforce_documented_lengths() -> None:
    tuple_fields = (
        (HotelCandidateOperationIds, "search_operation_ids"),
        (HotelCandidatesReady, "candidates"),
        (HotelCandidatesInsufficient, "qualified_candidates"),
    )
    for model, field_name in tuple_fields:
        assert get_origin(model.model_fields[field_name].annotation) is tuple

    operations = (operation("one"), operation("two"), operation("three"))
    with pytest.raises(ValidationError):
        HotelCandidateOperationIds(
            search_operation_ids=list(operations),
            candidate_set_operation_id=operation("candidate_set"),
        )
    with pytest.raises(ValidationError):
        HotelCandidateOperationIds(
            search_operation_ids=operations[:2],
            candidate_set_operation_id=operation("candidate_set"),
        )
    with pytest.raises(ValidationError):
        HotelCandidateOperationIds(
            search_operation_ids=(*operations, operation("four")),
            candidate_set_operation_id=operation("candidate_set"),
        )

    candidates = tuple(candidate(f"uid-{index}") for index in range(5))
    with pytest.raises(ValidationError):
        HotelCandidatesReady(candidate_set_ref=ref("candidate_set"), candidates=list(candidates[:3]))
    with pytest.raises(ValidationError):
        HotelCandidatesReady(candidate_set_ref=ref("candidate_set"), candidates=candidates[:2])
    assert HotelCandidatesReady(candidate_set_ref=ref("candidate_set"), candidates=candidates).candidates == candidates
    with pytest.raises(ValidationError):
        HotelCandidatesInsufficient(
            candidate_set_ref=ref("candidate_set"),
            qualified_candidates=list(candidates[:1]),
            searches_attempted=3,
        )
    with pytest.raises(ValidationError):
        HotelCandidatesInsufficient(
            candidate_set_ref=ref("candidate_set"),
            qualified_candidates=candidates[:3],
            searches_attempted=3,
        )


def test_public_union_matrix_round_trips_every_branch_and_rejects_unknown_discriminators() -> None:
    candidate_values = tuple(candidate(f"uid-{index}") for index in range(3))
    discovery_values = (
        HotelCandidatesReady(candidate_set_ref=ref("candidate_set"), candidates=candidate_values),
        HotelCandidatesInsufficient(
            candidate_set_ref=ref("candidate_set"),
            qualified_candidates=(candidate_values[0],),
            searches_attempted=3,
        ),
        HotelCandidateDiscoveryFailed(
            reason=HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED,
            failed_search_ordinal=3,
            qualified_count=1,
            failure_search_ref=None,
        ),
    )
    outcome_values = (
        HotelSelected(selection_ref=ref("selection", "hotel.selection"), selected_candidate=candidate_values[0]),
        HotelSelectionDeclined(selection_ref=ref("selection", "hotel.selection")),
        HotelSelectionRejected(reason=HotelSelectionRejectionCode.CANDIDATE_UID_NOT_FOUND),
    )
    union_cases = (
        (HotelSelectionCommand, (HotelSelectCommand(selected_uid="uid-1"), HotelDeclineCommand()), "action"),
        (HotelCandidateDiscoveryOutcome, discovery_values, "outcome_type"),
        (HotelSelectionOutcome, outcome_values, "outcome_type"),
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


def test_failure_ref_and_coordinate_boundaries_fail_closed() -> None:
    with pytest.raises(ValidationError):
        HotelCandidateDiscoveryFailed(
            reason=HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED,
            failed_search_ordinal=1,
            qualified_count=0,
            failure_search_ref=ref("search_one", "hotel.place_search"),
        )
    with pytest.raises(ValidationError):
        HotelCoordinate(schema_version="1.0", coordinate_system="bd09ll", latitude=True, longitude=120.0)
    with pytest.raises(ValidationError):
        HotelCandidate(**{**candidate().model_dump(), "category": "餐饮"})


def test_operation_and_outcome_cross_bindings_fail_closed() -> None:
    with pytest.raises(ValidationError):
        HotelCandidateOperationIds(
            search_operation_ids=(
                OperationId(run_id=RUN_ID, phase=WorkflowPhase.REQUIREMENTS, logical_step="search_1"),
                operation("search_2"),
                operation("search_3"),
            ),
            candidate_set_operation_id=operation("candidate_set"),
        )
    with pytest.raises(ValidationError):
        HotelCandidateOperationIds(
            search_operation_ids=(operation("same"), operation("same"), operation("search_3")),
            candidate_set_operation_id=operation("candidate_set"),
        )
    other_run = UUID("12345678-1234-5678-1234-567812345679")
    with pytest.raises(ValidationError):
        HotelCandidateOperationIds(
            search_operation_ids=(operation("search_1"), operation("search_2"), operation("search_3")),
            candidate_set_operation_id=OperationId(
                run_id=other_run, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="candidate_set"
            ),
        )
    with pytest.raises(ValidationError):
        HotelSelectionRequest(
            selection_operation_id=OperationId(
                run_id=RUN_ID, phase=WorkflowPhase.REQUIREMENTS, logical_step="selection"
            ),
            candidate_set_ref=ref("candidate_set"),
            command=HotelDeclineCommand(),
        )
    duplicate_candidates = (candidate("same"), candidate("same"), candidate("same"))
    with pytest.raises(ValidationError):
        HotelCandidatesReady(candidate_set_ref=ref("candidate_set"), candidates=duplicate_candidates)
    with pytest.raises(ValidationError):
        HotelCandidatesInsufficient(
            candidate_set_ref=ref("candidate_set"),
            qualified_candidates=(candidate("same"), candidate("same")),
            searches_attempted=3,
        )
    with pytest.raises(ValidationError):
        HotelCandidateDiscoveryFailed(
            reason=HotelCandidateDiscoveryFailureCode.MAP_CALL_FAILED,
            failed_search_ordinal=1,
            qualified_count=0,
            failure_search_ref=ref("selection", "hotel.selection"),
        )
