from datetime import datetime, timezone
from uuid import uuid4

import hashlib
import pytest

from app.itinerary_planning.fingerprints import (
    artifact_ref_payload,
    canonical_json_bytes,
    fingerprint_geocode,
    fingerprint_planning_context,
)
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase


ZERO = "sha256:" + "0" * 64


def test_optional_backups_do_not_replay_the_required_backup_scope(monkeypatch):
    import app.itinerary_planning.fingerprints as fingerprints
    from tests.unit.itinerary_planning.test_service_initial import _fixture
    _, request, *_ = _fixture()
    current = fingerprints.fingerprint_request_initial(request)
    monkeypatch.setattr(fingerprints, "INITIAL_CONTEXT_FINGERPRINT_VERSION", "planning-fp-v3")
    assert fingerprints.fingerprint_request_initial(request) != current


def test_initial_fingerprint_accepts_valid_enum_keyed_requirement_metadata():
    from app.requirements import RequirementFieldPath
    from app.requirements.models import FieldMetadata
    from app.itinerary_planning.fingerprints import _model_hash, fingerprint_request_initial
    from tests.unit.itinerary_planning.test_service_initial import _fixture

    _, request, *_ = _fixture()
    metadata = request.confirmed.metadata_without_raw_evidence.metadata
    metadata[RequirementFieldPath.DESTINATION] = FieldMetadata(source_turn_id=uuid4())
    metadata[RequirementFieldPath.ARRIVAL_HUB_NAME] = FieldMetadata(source_turn_id=uuid4())
    before = request.model_dump_json()
    expected = "sha256:" + hashlib.sha256(canonical_json_bytes(
        request.confirmed.model_dump(mode="json", exclude_none=False))).hexdigest()
    assert _model_hash(request.confirmed) == expected
    first = fingerprint_request_initial(request)
    assert request.model_dump_json() == before
    request.confirmed.metadata_without_raw_evidence.metadata = dict(reversed(tuple(metadata.items())))
    assert fingerprint_request_initial(request) == first
    metadata[RequirementFieldPath.DESTINATION].source_turn_id = uuid4()
    assert fingerprint_request_initial(request) != first


def test_enum_key_encoding_does_not_coerce_invalid_metadata_or_change_utc_canonicalization():
    from datetime import timedelta
    from pydantic import BaseModel, ConfigDict
    from app.requirements.models import FieldMetadata
    from app.itinerary_planning.fingerprints import _model_hash
    from tests.unit.itinerary_planning.test_service_initial import _fixture

    _, request, *_ = _fixture()
    request.confirmed.metadata_without_raw_evidence.metadata["destination"] = FieldMetadata()
    with pytest.raises(TypeError):
        _model_hash(request.confirmed)
    ref = _ref(5)
    assert _model_hash(ref) == "sha256:" + hashlib.sha256(canonical_json_bytes(ref.model_dump(mode="python"))).hexdigest()
    class TimestampPayload(BaseModel):
        model_config = ConfigDict(strict=True)
        at: datetime
    utc = TimestampPayload(at=ref.created_at)
    shifted = TimestampPayload(at=ref.created_at.astimezone(timezone(timedelta(hours=8))))
    assert _model_hash(utc) == _model_hash(shifted)


@pytest.mark.asyncio
async def test_public_initial_accepts_nonempty_requirement_metadata():
    from app.requirements import RequirementFieldPath
    from app.requirements.models import FieldMetadata
    from tests.unit.itinerary_planning.test_service_initial import _fixture

    service, request, runtime, _, _ = _fixture()
    request.confirmed.metadata_without_raw_evidence.metadata[RequirementFieldPath.DESTINATION] = FieldMetadata(source_turn_id=uuid4())
    try:
        result = await service.plan_initial(request)
        assert runtime.start_count == 1
        assert result.outcome_type == "failed"  # Scripted model intentionally has no response.
    finally:
        await service.aclose()


def test_resume_fingerprint_uses_local_v5_domain_and_both_full_plans():
    import hashlib
    from app.itinerary_planning import ItineraryPlanningResumeRequest
    from app.itinerary_planning.fingerprints import fingerprint_request_resume, fingerprint_request_initial
    from tests.unit.itinerary_planning.test_service_initial import _fixture, _ref as planning_ref
    from tests.unit.itinerary_planning.test_service_resume import _supplemental_plan
    _, initial, *_ = _fixture()
    initial_digest = fingerprint_request_initial(initial)
    run_id = initial.operation_plan.run_id
    request = ItineraryPlanningResumeRequest(predecessor_planning_ref=planning_ref(run_id, "itinerary_planning.itinerary", WorkflowPhase.ITINERARY_PLANNING, "awaiting"),
        supplemented_candidate_set_ref=initial.candidate_set_ref, operation_plan=initial.operation_plan, supplemental_operation_plan=_supplemental_plan(run_id))
    first = fingerprint_request_resume(request)
    def model_hash(value):
        return "sha256:" + hashlib.sha256(canonical_json_bytes(value.model_dump(mode="json", exclude_none=False))).hexdigest()
    expected = {"fingerprint_version": "planning-resume-fp-v6", "kind": "request_resume", "fields": {
            "capability_scope": "module09-phase-budget-v1",
            "schema_version": "3.0", "run_id": run_id,
        "predecessor_planning_ref": artifact_ref_payload(request.predecessor_planning_ref),
        "supplemented_candidate_set_ref": artifact_ref_payload(request.supplemented_candidate_set_ref),
        "operation_plan": model_hash(request.operation_plan),
        "supplemental_operation_plan": model_hash(request.supplemental_operation_plan),
    }}
    assert first == "sha256:" + hashlib.sha256(canonical_json_bytes(expected)).hexdigest()
    changed_plan = request.supplemental_operation_plan.model_copy(update={"terminal_successor_operation_id": OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="another_terminal")})
    assert fingerprint_request_resume(request.model_copy(update={"supplemental_operation_plan": changed_plan})) != first
    assert fingerprint_request_initial(initial) == initial_digest
    assert request.schema_version == "3.0"


def _ref(size: int) -> ArtifactRef:
    run = uuid4()
    return ArtifactRef(
        artifact_id=uuid4(),
        artifact_type="hotel.selection",
        schema_version="1.0",
        content_hash=ZERO,
        operation_id=OperationId(run_id=run, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="selection"),
        created_at=datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc),
        size_bytes=size,
    )


def test_canonical_json_is_key_order_stable_and_ref_uses_all_fields() -> None:
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    payload = artifact_ref_payload(_ref(10))
    assert set(payload) == {"artifact_id", "artifact_type", "schema_version", "content_hash", "operation_id", "created_at", "size_bytes"}


def test_size_bytes_changes_context_dependent_fingerprint() -> None:
    first = _ref(10)
    second = first.model_copy(update={"size_bytes": 11})
    run = uuid4()
    one = fingerprint_planning_context(run, ZERO, first, ZERO, ZERO)
    two = fingerprint_planning_context(run, ZERO, second, ZERO, ZERO)
    assert one != two
    assert fingerprint_geocode(ZERO, "上海", "arrival", ZERO) == fingerprint_geocode(ZERO, "上海", "arrival", ZERO)
