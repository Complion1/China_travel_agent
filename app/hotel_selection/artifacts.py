"""Module 07 Artifact DTO、注册描述符和统一写请求构造器。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, StringConstraints, model_validator

from app.workflow_contracts import (
    ArtifactRef,
    ArtifactSchemaRegistration,
    ArtifactWriteRequest,
    ContentHash,
    OperationId,
    WorkflowPhase,
)

from .models import (
    HotelCandidate,
    HotelMapFailureCode,
    HotelPlaceEvidence,
    HOTEL_SELECTION_SCHEMA_VERSION,
    _HotelModel,
)


class HotelSearchSuccessEvidence(_HotelModel):
    schema_version: Literal["1.0"]
    outcome_type: Literal["success"]
    places: Annotated[tuple[HotelPlaceEvidence, ...], Field(min_length=1, max_length=10)]


class HotelSearchNoResultEvidence(_HotelModel):
    schema_version: Literal["1.0"]
    outcome_type: Literal["no_result"]


class HotelSearchFailedEvidence(_HotelModel):
    schema_version: Literal["1.0"]
    outcome_type: Literal["failed"]
    failure_code: HotelMapFailureCode


HotelSearchEvidence: TypeAlias = Annotated[
    HotelSearchSuccessEvidence
    | HotelSearchNoResultEvidence
    | HotelSearchFailedEvidence,
    Field(discriminator="outcome_type"),
]


class HotelPlaceSearchArtifact(_HotelModel):
    schema_version: Literal["1.0"]
    search_ordinal: Annotated[int, Field(ge=1, le=3)]
    request_hash: ContentHash
    source: Literal["baidu_maps_mcp"]
    verified: bool
    retrieved_at: datetime
    evidence: HotelSearchEvidence

    @model_validator(mode="after")
    def _validate_provenance_and_evidence(self) -> "HotelPlaceSearchArtifact":
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() != timedelta(0):
            raise ValueError("retrieved_at must be UTC datetime")
        is_failed = type(self.evidence) is HotelSearchFailedEvidence
        if self.verified is not (not is_failed):
            raise ValueError("verified does not match evidence")
        return self


class HotelCandidatesReadyRecord(_HotelModel):
    schema_version: Literal["1.0"]
    result_type: Literal["ready"]
    candidates: Annotated[tuple[HotelCandidate, ...], Field(min_length=3, max_length=5)]

    @model_validator(mode="after")
    def _validate_candidate_uids(self) -> "HotelCandidatesReadyRecord":
        uids = tuple(candidate.uid for candidate in self.candidates)
        if len(set(uids)) != len(uids):
            raise ValueError("candidate UIDs must be unique")
        return self


class HotelCandidatesInsufficientRecord(_HotelModel):
    schema_version: Literal["1.0"]
    result_type: Literal["insufficient"]
    candidates: Annotated[tuple[HotelCandidate, ...], Field(min_length=0, max_length=2)]

    @model_validator(mode="after")
    def _validate_candidate_uids(self) -> "HotelCandidatesInsufficientRecord":
        uids = tuple(candidate.uid for candidate in self.candidates)
        if len(set(uids)) != len(uids):
            raise ValueError("candidate UIDs must be unique")
        return self


HotelDiscoveryResult: TypeAlias = Annotated[
    HotelCandidatesReadyRecord | HotelCandidatesInsufficientRecord,
    Field(discriminator="result_type"),
]


class HotelDiscoveryArtifact(_HotelModel):
    schema_version: Literal["1.0"]
    confirmation_summary_digest: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1)
    ]
    query_plan_hash: ContentHash
    search_operation_ids: Annotated[
        tuple[OperationId, ...], Field(min_length=3, max_length=3)
    ]
    search_refs: Annotated[tuple[ArtifactRef, ...], Field(min_length=1, max_length=3)]
    result: HotelDiscoveryResult

    @model_validator(mode="after")
    def _validate_search_bindings(self) -> "HotelDiscoveryArtifact":
        operations = self.search_operation_ids
        if any(operation.phase is not WorkflowPhase.HOTEL_SELECTION for operation in operations):
            raise ValueError("search operations must be hotel selection operations")
        if len({
            (operation.run_id, operation.phase, operation.logical_step)
            for operation in operations
        }) != len(operations):
            raise ValueError("search operations must be distinct")
        if len({operation.run_id for operation in operations}) != 1:
            raise ValueError("search operations must share a run")
        if len(set(self.search_refs)) != len(self.search_refs):
            raise ValueError("search refs must be unique")
        if type(self.result) is HotelCandidatesInsufficientRecord and len(self.search_refs) != 3:
            raise ValueError("insufficient result requires three search refs")
        for index, ref in enumerate(self.search_refs):
            if ref.artifact_type != "hotel.place_search" or ref.schema_version != "1.0":
                raise ValueError("search refs must point to place search artifacts")
            if ref.operation_id != operations[index]:
                raise ValueError("search ref operation mismatch")
        candidates = self.result.candidates
        uids = tuple(candidate.uid for candidate in candidates)
        if len(set(uids)) != len(uids):
            raise ValueError("candidate UIDs must be unique")
        return self


class HotelSelectedDecision(_HotelModel):
    schema_version: Literal["1.0"]
    decision_type: Literal["selected"]
    selected_candidate: HotelCandidate


class HotelDeclinedDecision(_HotelModel):
    schema_version: Literal["1.0"]
    decision_type: Literal["declined"]


HotelSelectionDecision: TypeAlias = Annotated[
    HotelSelectedDecision | HotelDeclinedDecision,
    Field(discriminator="decision_type"),
]


class HotelSelectionArtifact(_HotelModel):
    schema_version: Literal["1.0"]
    candidate_set_ref: ArtifactRef
    decision: HotelSelectionDecision

    @model_validator(mode="after")
    def _validate_candidate_set_ref(self) -> "HotelSelectionArtifact":
        if (
            self.candidate_set_ref.artifact_type != "hotel.candidate_set"
            or self.candidate_set_ref.schema_version != "1.0"
        ):
            raise ValueError("selection must reference a candidate set")
        return self


def _artifact_write_request(
    *,
    operation_id: OperationId,
    artifact_type: Literal[
        "hotel.place_search",
        "hotel.candidate_set",
        "hotel.selection",
    ],
    artifact: HotelPlaceSearchArtifact | HotelDiscoveryArtifact | HotelSelectionArtifact,
) -> ArtifactWriteRequest:
    content = artifact.model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    if type(content) is not dict:
        from .errors import HotelInputError

        raise HotelInputError()
    return ArtifactWriteRequest(
        operation_id=operation_id,
        artifact_type=artifact_type,
        schema_version=HOTEL_SELECTION_SCHEMA_VERSION,
        content=content,
    )


HOTEL_ARTIFACT_REGISTRATIONS: tuple[ArtifactSchemaRegistration, ...] = (
    ArtifactSchemaRegistration(
        artifact_type="hotel.place_search",
        schema_version="1.0",
        model=HotelPlaceSearchArtifact,
    ),
    ArtifactSchemaRegistration(
        artifact_type="hotel.candidate_set",
        schema_version="1.0",
        model=HotelDiscoveryArtifact,
    ),
    ArtifactSchemaRegistration(
        artifact_type="hotel.selection",
        schema_version="1.0",
        model=HotelSelectionArtifact,
    ),
)


__all__ = (
    "HOTEL_ARTIFACT_REGISTRATIONS",
    "HotelCandidatesInsufficientRecord",
    "HotelCandidatesReadyRecord",
    "HotelDeclinedDecision",
    "HotelDiscoveryArtifact",
    "HotelDiscoveryResult",
    "HotelPlaceSearchArtifact",
    "HotelSearchEvidence",
    "HotelSearchFailedEvidence",
    "HotelSearchNoResultEvidence",
    "HotelSearchSuccessEvidence",
    "HotelSelectedDecision",
    "HotelSelectionArtifact",
    "HotelSelectionDecision",
)
