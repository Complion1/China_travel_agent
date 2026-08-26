"""Artifact Registry 对 Module 07 可达模型图的合同验证。"""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.hotel_selection import (
    HOTEL_ARTIFACT_REGISTRATIONS,
    HotelCandidate,
    HotelCandidatesReadyRecord,
    HotelDeclinedDecision,
    HotelDiscoveryArtifact,
    HotelMapFailureCode,
    HotelPlaceSearchArtifact,
    HotelSearchFailedEvidence,
    HotelSelectionArtifact,
)
from app.hotel_selection.artifacts import _artifact_write_request
from app.hotel_selection.models import HotelCoordinate
from app.workflow_contracts import (
    ArtifactExpectedModelMismatchError,
    ArtifactSchemaRegistry,
    DEMO_ARTIFACT_REPOSITORY_LIMITS,
    InMemoryArtifactRepository,
    OperationId,
    UnsupportedArtifactTypeError,
    UnsupportedArtifactVersionError,
    WorkflowPhase,
)


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 8, 25, tzinfo=timezone.utc)


def op(step: str) -> OperationId:
    return OperationId(run_id=RUN_ID, phase=WorkflowPhase.HOTEL_SELECTION, logical_step=step)


def candidate(uid: str) -> HotelCandidate:
    return HotelCandidate(
        schema_version="1.0",
        uid=uid,
        name=f"酒店-{uid}",
        address="虚构地址",
        coordinate=HotelCoordinate(
            schema_version="1.0", coordinate_system="bd09ll", latitude=30.2, longitude=120.1
        ),
        category="酒店",
    )


def artifact_ref(step: str, artifact_type: str, artifact_id: UUID) -> object:
    from app.workflow_contracts import ArtifactRef

    return ArtifactRef(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        schema_version="1.0",
        content_hash="sha256:" + "0" * 64,
        operation_id=op(step),
        created_at=UTC,
        size_bytes=1,
    )


def artifact_cases() -> tuple[tuple[str, object, OperationId, type[object]], ...]:
    search = HotelPlaceSearchArtifact(
        schema_version="1.0",
        search_ordinal=1,
        request_hash="sha256:" + "1" * 64,
        source="baidu_maps_mcp",
        verified=False,
        retrieved_at=UTC,
        evidence=HotelSearchFailedEvidence(
            schema_version="1.0", outcome_type="failed", failure_code=HotelMapFailureCode.TIMEOUT_ERROR
        ),
    )
    search_operations = tuple(op(f"search_{index}") for index in range(1, 4))
    refs = tuple(
        artifact_ref(f"search_{index}", "hotel.place_search", UUID(int=index))
        for index in range(1, 4)
    )
    candidate_set = HotelDiscoveryArtifact(
        schema_version="1.0",
        confirmation_summary_digest="digest",
        query_plan_hash="sha256:" + "2" * 64,
        search_operation_ids=search_operations,
        search_refs=refs,
        result=HotelCandidatesReadyRecord(
            schema_version="1.0",
            result_type="ready",
            candidates=tuple(candidate(f"uid-{index}") for index in range(1, 4)),
        ),
    )
    selection = HotelSelectionArtifact(
        schema_version="1.0",
        candidate_set_ref=artifact_ref(
            "candidate_set", "hotel.candidate_set", UUID(int=10)
        ),
        decision=HotelDeclinedDecision(schema_version="1.0", decision_type="declined"),
    )
    return (
        ("hotel.place_search", search, op("search_1"), HotelPlaceSearchArtifact),
        ("hotel.candidate_set", candidate_set, op("candidate_set"), HotelDiscoveryArtifact),
        ("hotel.selection", selection, op("selection"), HotelSelectionArtifact),
    )


class FixedClock:
    def now(self) -> datetime:
        return UTC


class SequenceUUIDFactory:
    def __init__(self, value: UUID) -> None:
        self.value = value

    def new(self) -> UUID:
        return self.value


def test_module07_registrations_prepare_registry_without_global_singleton() -> None:
    registry = ArtifactSchemaRegistry(HOTEL_ARTIFACT_REGISTRATIONS)
    assert registry.resolve(
        artifact_type="hotel.place_search", schema_version="1.0"
    ).model.__name__ == "HotelPlaceSearchArtifact"
    assert registry.resolve(
        artifact_type="hotel.candidate_set", schema_version="1.0"
    ).model.__name__ == "HotelDiscoveryArtifact"
    assert registry.resolve(
        artifact_type="hotel.selection", schema_version="1.0"
    ).model.__name__ == "HotelSelectionArtifact"


def test_registration_order_is_the_frozen_three_item_order() -> None:
    assert tuple(item.artifact_type for item in HOTEL_ARTIFACT_REGISTRATIONS) == (
        "hotel.place_search",
        "hotel.candidate_set",
        "hotel.selection",
    )
    assert tuple(item.schema_version for item in HOTEL_ARTIFACT_REGISTRATIONS) == (
        "1.0",
        "1.0",
        "1.0",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact_type,artifact,operation_id,expected_model", artifact_cases())
async def test_each_registered_artifact_completes_prepare_put_get_round_trip(
    artifact_type: str,
    artifact: object,
    operation_id: OperationId,
    expected_model: type[object],
) -> None:
    registry = ArtifactSchemaRegistry(HOTEL_ARTIFACT_REGISTRATIONS)
    request = _artifact_write_request(
        operation_id=operation_id,
        artifact_type=artifact_type,
        artifact=artifact,
    )
    assert type(request.content) is dict
    prepared = registry.prepare(request)
    repository = InMemoryArtifactRepository(
        registry=registry,
        utc_clock=FixedClock(),
        uuid_factory=SequenceUUIDFactory(UUID(int=100)),
        limits=DEMO_ARTIFACT_REPOSITORY_LIMITS,
    )
    stored_ref = await repository.put(request)
    assert stored_ref.artifact_type == artifact_type
    assert stored_ref.schema_version == "1.0"
    assert stored_ref.content_hash == prepared.content_hash
    assert stored_ref.size_bytes == prepared.size_bytes
    read = await repository.get(stored_ref, expected_model=expected_model)
    assert read.content == artifact
    assert read.content is not artifact
    assert read.ref is not stored_ref

    wrong_model = HotelSelectionArtifact if expected_model is not HotelSelectionArtifact else HotelPlaceSearchArtifact
    with pytest.raises(ArtifactExpectedModelMismatchError):
        await repository.get(stored_ref, expected_model=wrong_model)


def test_unknown_artifact_type_and_version_fail_closed() -> None:
    registry = ArtifactSchemaRegistry(HOTEL_ARTIFACT_REGISTRATIONS)
    artifact_type, artifact, operation_id, _ = artifact_cases()[0]
    request = _artifact_write_request(
        operation_id=operation_id,
        artifact_type=artifact_type,
        artifact=artifact,
    )
    with pytest.raises(UnsupportedArtifactTypeError):
        registry.prepare(request.model_copy(update={"artifact_type": "hotel.unknown"}))
    with pytest.raises(UnsupportedArtifactVersionError):
        registry.prepare(request.model_copy(update={"schema_version": "2.0"}))
