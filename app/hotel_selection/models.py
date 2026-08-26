"""Module 07 公共 DTO、枚举和确定性预算键。"""

from __future__ import annotations

from enum import Enum
import re
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.workflow_contracts import (
    ArtifactRef,
    ExecutionBudgetKey,
    ExecutionBudgetScope,
    OperationId,
    WorkflowPhase,
)


HOTEL_SELECTION_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class HotelMapFailureCode(str, Enum):
    REQUEST_ERROR = "request_error"
    CONFIGURATION_ERROR = "configuration_error"
    AUTHENTICATION_ERROR = "authentication_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    TIMEOUT_ERROR = "timeout_error"
    TRANSPORT_ERROR = "transport_error"
    SESSION_ERROR = "session_error"
    TOOL_UNAVAILABLE = "tool_unavailable"
    TOOL_SCHEMA_ERROR = "tool_schema_error"
    PROTOCOL_ERROR = "protocol_error"
    RESPONSE_ERROR = "response_error"
    CONCURRENCY_ERROR = "concurrency_error"
    UNVERIFIED_RESULT = "unverified_result"


class HotelCandidateDiscoveryFailureCode(str, Enum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    MAP_CALL_FAILED = "map_call_failed"


class HotelSelectionRejectionCode(str, Enum):
    CANDIDATE_UID_NOT_FOUND = "candidate_uid_not_found"
    CANDIDATE_SET_NOT_READY = "candidate_set_not_ready"
    STALE_CANDIDATE_SET = "stale_candidate_set"
    DECISION_CONFLICT = "decision_conflict"


class _HotelModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


_Text512: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
_TextUnbounded: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_Latitude: TypeAlias = Annotated[
    float,
    Field(ge=-90, le=90, allow_inf_nan=False),
]
_Longitude: TypeAlias = Annotated[
    float,
    Field(ge=-180, le=180, allow_inf_nan=False),
]


# Category qualification is intentionally defined once here so the DTO
# invariant and the query projection use identical, exact semantics.
_CATEGORY_SPLIT_RE = re.compile(r"[;；,，/|>]+")
_HOTEL_CATEGORY_TOKENS = frozenset(
    {
        "住宿服务",
        "酒店",
        "星级酒店",
        "快捷酒店",
        "经济型酒店",
        "宾馆酒店",
        "宾馆",
        "旅馆",
        "客栈",
        "民宿",
        "度假村",
        "公寓式酒店",
        "青年旅舍",
    }
)


def _qualify_hotel_category(category: str | None) -> bool:
    if category is None or type(category) is not str:
        return False
    tokens = (token.strip() for token in _CATEGORY_SPLIT_RE.split(category))
    return any(token in _HOTEL_CATEGORY_TOKENS for token in tokens if token)


class HotelCoordinate(_HotelModel):
    schema_version: Literal["1.0"]
    coordinate_system: Literal["bd09ll"]
    latitude: _Latitude
    longitude: _Longitude


class HotelPlaceEvidence(_HotelModel):
    schema_version: Literal["1.0"]
    uid: _Text512
    name: _Text512
    address: _Text512
    coordinate: HotelCoordinate
    category: _Text512 | None


class HotelCandidate(_HotelModel):
    schema_version: Literal["1.0"]
    uid: _Text512
    name: _Text512
    address: _Text512
    coordinate: HotelCoordinate
    category: _Text512

    @model_validator(mode="after")
    def _validate_category(self) -> "HotelCandidate":
        if not _qualify_hotel_category(self.category):
            raise ValueError("candidate category is not hotel-qualified")
        return self


HOTEL_PLACE_CALL_BUDGET_KEY = ExecutionBudgetKey(
    scope=ExecutionBudgetScope.PHASE,
    name="hotel.place_call",
    phase=WorkflowPhase.HOTEL_SELECTION,
)


class HotelCandidateOperationIds(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    search_operation_ids: Annotated[
        tuple[OperationId, ...], Field(min_length=3, max_length=3)
    ]
    candidate_set_operation_id: OperationId

    @model_validator(mode="after")
    def _validate_operation_ids(self) -> "HotelCandidateOperationIds":
        all_ids = (*self.search_operation_ids, self.candidate_set_operation_id)
        if any(operation.phase is not WorkflowPhase.HOTEL_SELECTION for operation in all_ids):
            raise ValueError("invalid hotel selection phase")
        if len({
            (operation.run_id, operation.phase, operation.logical_step)
            for operation in all_ids
        }) != 4:
            raise ValueError("operation ids must be distinct")
        runs = {operation.run_id for operation in all_ids}
        if len(runs) != 1:
            raise ValueError("operation ids must share a run")
        return self


class HotelSelectCommand(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    action: Literal["select"] = "select"
    selected_uid: _Text512


class HotelDeclineCommand(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    action: Literal["decline"] = "decline"


HotelSelectionCommand: TypeAlias = Annotated[
    HotelSelectCommand | HotelDeclineCommand,
    Field(discriminator="action"),
]


class HotelSelectionRequest(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    selection_operation_id: OperationId
    candidate_set_ref: ArtifactRef
    command: HotelSelectionCommand

    @model_validator(mode="after")
    def _validate_selection_operation(self) -> "HotelSelectionRequest":
        if self.selection_operation_id.phase is not WorkflowPhase.HOTEL_SELECTION:
            raise ValueError("invalid hotel selection phase")
        return self


def _validate_candidate_uids(candidates: tuple[HotelCandidate, ...]) -> None:
    uids = tuple(candidate.uid for candidate in candidates)
    if len(set(uids)) != len(uids):
        raise ValueError("candidate UIDs must be unique")


class HotelCandidatesReady(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    outcome_type: Literal["ready"] = "ready"
    candidate_set_ref: ArtifactRef
    candidates: Annotated[tuple[HotelCandidate, ...], Field(min_length=3, max_length=5)]
    disclaimer_code: Literal["map_identity_only"] = "map_identity_only"

    @model_validator(mode="after")
    def _validate_candidates(self) -> "HotelCandidatesReady":
        _validate_candidate_uids(self.candidates)
        return self


class HotelCandidatesInsufficient(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    outcome_type: Literal["insufficient"] = "insufficient"
    candidate_set_ref: ArtifactRef
    qualified_candidates: Annotated[
        tuple[HotelCandidate, ...], Field(min_length=0, max_length=2)
    ]
    searches_attempted: Literal[3]

    @model_validator(mode="after")
    def _validate_candidates(self) -> "HotelCandidatesInsufficient":
        _validate_candidate_uids(self.qualified_candidates)
        return self


class HotelCandidateDiscoveryFailed(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    outcome_type: Literal["failed"] = "failed"
    reason: HotelCandidateDiscoveryFailureCode
    failed_search_ordinal: Annotated[int, Field(ge=1, le=3)]
    qualified_count: Annotated[int, Field(ge=0, le=5)]
    failure_search_ref: ArtifactRef | None

    @model_validator(mode="after")
    def _validate_failure_ref(self) -> "HotelCandidateDiscoveryFailed":
        is_budget = self.reason is HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED
        if is_budget and self.failure_search_ref is not None:
            raise ValueError("budget failure cannot carry a search ref")
        if not is_budget:
            ref = self.failure_search_ref
            if ref is None:
                raise ValueError("map failure requires a search ref")
            if ref.artifact_type != "hotel.place_search" or ref.schema_version != "1.0":
                raise ValueError("map failure ref must point to place search")
        return self


HotelCandidateDiscoveryOutcome: TypeAlias = Annotated[
    HotelCandidatesReady | HotelCandidatesInsufficient | HotelCandidateDiscoveryFailed,
    Field(discriminator="outcome_type"),
]


class HotelSelected(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    outcome_type: Literal["selected"] = "selected"
    selection_ref: ArtifactRef
    selected_candidate: HotelCandidate


class HotelSelectionDeclined(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    outcome_type: Literal["declined"] = "declined"
    selection_ref: ArtifactRef


class HotelSelectionRejected(_HotelModel):
    schema_version: Literal["1.0"] = HOTEL_SELECTION_SCHEMA_VERSION
    outcome_type: Literal["rejected"] = "rejected"
    reason: HotelSelectionRejectionCode


HotelSelectionOutcome: TypeAlias = Annotated[
    HotelSelected | HotelSelectionDeclined | HotelSelectionRejected,
    Field(discriminator="outcome_type"),
]


__all__ = (
    "HOTEL_PLACE_CALL_BUDGET_KEY",
    "HOTEL_SELECTION_SCHEMA_VERSION",
    "HotelCandidate",
    "HotelCandidateDiscoveryFailed",
    "HotelCandidateDiscoveryFailureCode",
    "HotelCandidateDiscoveryOutcome",
    "HotelCandidateOperationIds",
    "HotelCandidatesInsufficient",
    "HotelCandidatesReady",
    "HotelCoordinate",
    "HotelMapFailureCode",
    "HotelPlaceEvidence",
    "HotelSelectionDeclined",
    "HotelSelected",
    "HotelSelectionOutcome",
    "HotelSelectionRejected",
    "HotelSelectionRejectionCode",
    "HotelSelectionRequest",
    "HotelSelectionCommand",
    "HotelSelectCommand",
    "HotelDeclineCommand",
)
