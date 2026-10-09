"""Module 09 的显式、版本化 fingerprint helpers。

所有 digest 都对显式 payload 做 Module 04 兼容的 canonical JSON；业务 tuple
顺序保留，JSON key 顺序由 canonicalizer 统一处理。
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.baidu_map import BaiduCoordinate, MatrixTravelMode, TravelMode
from app.workflow_contracts import ArtifactRef, OperationId

from .models import (
    ContentDigest,
    ItineraryPlanningInitialRequest,
    ItineraryPlanningOperationPlan,
    ItineraryPlanningResumeRequest,
    PlanningLocationRef,
    PlanningTool,
    TransportSemantic,
)


FINGERPRINT_VERSION = "planning-fp-v1"
INITIAL_CONTEXT_FINGERPRINT_VERSION = "planning-fp-v6"
RESUME_FINGERPRINT_VERSION = "planning-resume-fp-v7"
CAPABILITY_SCOPE = "module09-format-recovery-v1"


def _normal(value: object) -> object:
    if isinstance(value, BaseModel):
        return _normal(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return _normal(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fingerprint datetime must be aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if value is None or type(value) is bool or type(value) is int or type(value) is str:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("fingerprint float must be finite")
        return 0.0 if value == 0.0 else value
    if isinstance(value, (tuple, list)):
        return [_normal(item) for item in value]
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("fingerprint dictionary keys must be strings")
        return {key: _normal(item) for key, item in value.items()}
    raise TypeError(f"unsupported fingerprint value: {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _normal(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_bytes(payload: dict[str, object]) -> bytes:
    """Return the stable UTF-8 canonical representation used by Module 04."""
    return _canonical_bytes(payload)


def _digest(
    kind: str,
    fields: dict[str, object],
    *,
    fingerprint_version: str = FINGERPRINT_VERSION,
) -> ContentDigest:
    payload = {"fingerprint_version": fingerprint_version, "kind": kind, "fields": fields}
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _canonical_hash(value: object) -> ContentDigest:
    """Hash a value itself, without the planning fingerprint envelope."""
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def artifact_ref_payload(ref: ArtifactRef) -> dict[str, object]:
    """Preserve all seven Module 04 ArtifactRef fields in fingerprint payloads."""
    return {
        "artifact_id": ref.artifact_id,
        "artifact_type": ref.artifact_type,
        "schema_version": ref.schema_version,
        "content_hash": ref.content_hash,
        "operation_id": ref.operation_id,
        "created_at": ref.created_at,
        "size_bytes": ref.size_bytes,
    }


def operation_id_payload(operation_id: OperationId) -> dict[str, object]:
    return {
        "run_id": operation_id.run_id,
        "phase": operation_id.phase,
        "logical_step": operation_id.logical_step,
    }


def _model_json_keys(value: object) -> object:
    """Encode validated enum map keys while retaining canonical value types."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key = key.value if isinstance(key, Enum) else key
            if type(key) is not str or key in result:
                raise ValueError("model dictionary keys must encode uniquely as strings")
            result[key] = _model_json_keys(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_model_json_keys(item) for item in value]
    return value


def _model_hash(value: BaseModel) -> ContentDigest:
    if not isinstance(value, BaseModel):
        raise TypeError("invalid model")
    try:
        # The exact runtime class is the only available public type witness at
        # this generic helper boundary.  Validate its Python tree before
        # canonical hashing so model_construct/forged instances cannot wash
        # invalid values through JSON coercion.
        from pydantic import TypeAdapter

        tree = value.model_dump(
            mode="python", exclude_none=False, by_alias=False,
            exclude_computed_fields=True,
        )
        fresh = TypeAdapter(type(value)).validate_python(tree, strict=True)
        return _canonical_hash(_model_json_keys(fresh.model_dump(mode="python")))
    except Exception:
        raise TypeError("model failed fresh strict validation") from None


def fingerprint_request_initial(request: ItineraryPlanningInitialRequest) -> ContentDigest:
    return _digest(
        "request_initial",
        {
            "capability_scope": CAPABILITY_SCOPE,
            "schema_version": request.schema_version,
            "run_id": request.operation_plan.run_id,
            "confirmed": _model_hash(request.confirmed),
            "hotel_selection_ref": artifact_ref_payload(request.hotel_selection_ref),
            "candidate_set_ref": artifact_ref_payload(request.candidate_set_ref),
            "operation_plan": _model_hash(request.operation_plan),
        },
        fingerprint_version=INITIAL_CONTEXT_FINGERPRINT_VERSION,
    )


def fingerprint_request_resume(request: ItineraryPlanningResumeRequest) -> ContentDigest:
    return _canonical_hash({
        "fingerprint_version": RESUME_FINGERPRINT_VERSION,
        "kind": "request_resume",
        "fields": {
            "capability_scope": CAPABILITY_SCOPE,
            "schema_version": request.schema_version,
            "run_id": request.operation_plan.run_id,
            "predecessor_planning_ref": artifact_ref_payload(request.predecessor_planning_ref),
            "supplemented_candidate_set_ref": artifact_ref_payload(request.supplemented_candidate_set_ref),
            "operation_plan": _model_hash(request.operation_plan),
            "supplemental_operation_plan": _model_hash(request.supplemental_operation_plan),
        },
    })


def fingerprint_planning_context(
    run_id: UUID,
    confirmed_digest: ContentDigest,
    hotel_selection_ref: ArtifactRef,
    arrival_hub_digest: ContentDigest,
    departure_hub_digest: ContentDigest,
) -> ContentDigest:
    return _digest(
        "planning_context",
        {
            "capability_scope": CAPABILITY_SCOPE,
            "run_id": run_id,
            "confirmed": confirmed_digest,
            "hotel_selection_ref": artifact_ref_payload(hotel_selection_ref),
            "arrival_hub_digest": arrival_hub_digest,
            "departure_hub_digest": departure_hub_digest,
        },
        fingerprint_version=INITIAL_CONTEXT_FINGERPRINT_VERSION,
    )


def fingerprint_candidate_handle(
    planning_context_digest: ContentDigest, admission_ordinal: int, uid: str
) -> ContentDigest:
    return _digest(
        "candidate_handle",
        {
            "planning_context_digest": planning_context_digest,
            "admission_ordinal": admission_ordinal,
            "uid": uid,
        },
    )


def fingerprint_candidate_evidence(
    planning_context_digest: ContentDigest,
    candidate_handle: str,
    candidate_projection: BaseModel,
    evidence_refs: tuple[ArtifactRef, ...],
) -> ContentDigest:
    return _digest(
        "candidate_evidence",
        {
            "planning_context_digest": planning_context_digest,
            "candidate_handle": candidate_handle,
            "candidate_projection": _model_hash(candidate_projection),
            "evidence_content_hashes": tuple(ref.content_hash for ref in evidence_refs),
        },
    )


def fingerprint_geocode(
    planning_context_digest: ContentDigest,
    destination: str,
    endpoint_kind: str,
    confirmed_hub_text_digest: ContentDigest,
) -> ContentDigest:
    return _digest(
        "geocode",
        {
            "planning_context_digest": planning_context_digest,
            "destination": destination,
            "endpoint_kind": endpoint_kind,
            "confirmed_hub_text_digest": confirmed_hub_text_digest,
        },
    )


def location_identity(location: PlanningLocationRef, coordinate: BaiduCoordinate | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"location": location}
    if coordinate is not None:
        payload["coordinate"] = {
            "coordinate_system": coordinate.coordinate_system,
            "longitude": coordinate.longitude,
            "latitude": coordinate.latitude,
        }
    return payload


def fingerprint_matrix(
    planning_context_digest: ContentDigest,
    matrix_date: date,
    mode: MatrixTravelMode,
    locations: tuple[tuple[PlanningLocationRef, BaiduCoordinate], ...],
) -> ContentDigest:
    return _digest(
        "matrix",
        {
            "planning_context_digest": planning_context_digest,
            "date": matrix_date,
            "mode": mode,
            "locations": tuple(location_identity(ref, coordinate) for ref, coordinate in locations),
        },
    )


def fingerprint_directions(
    planning_context_digest: ContentDigest,
    origin: PlanningLocationRef,
    origin_coordinate: BaiduCoordinate,
    destination: PlanningLocationRef,
    destination_coordinate: BaiduCoordinate,
    route_mode: TravelMode,
    alternatives_limit: int,
) -> ContentDigest:
    return _digest(
        "directions",
        {
            "planning_context_digest": planning_context_digest,
            "origin": location_identity(origin, origin_coordinate),
            "destination": location_identity(destination, destination_coordinate),
            "route_mode": route_mode,
            "alternatives_limit": alternatives_limit,
        },
    )


def fingerprint_directions_handle(
    operation_id: OperationId, leg_ordinal: int, directions_request_fingerprint: ContentDigest
) -> ContentDigest:
    return _digest(
        "directions_handle",
        {
            "directions_operation_id": operation_id_payload(operation_id),
            "leg_ordinal": leg_ordinal,
            "directions_request_fingerprint": directions_request_fingerprint,
        },
    )


def fingerprint_gap(
    planning_context_digest: ContentDigest,
    gap_type: str,
    typed_target: BaseModel,
    evidence_refs: tuple[ArtifactRef, ...],
) -> ContentDigest:
    return _digest(
        "gap",
        {
            "planning_context_digest": planning_context_digest,
            "gap_type": gap_type,
            "typed_target": typed_target.model_dump(mode="python"),
            "evidence_content_hashes": tuple(ref.content_hash for ref in evidence_refs),
        },
    )


def fingerprint_submit(
    planning_context_digest: ContentDigest,
    candidate_set_ref: ArtifactRef,
    effective_policy: BaseModel,
    draft: BaseModel,
) -> ContentDigest:
    return _digest(
        "submit",
        {
            "planning_context_digest": planning_context_digest,
            "candidate_set_ref": artifact_ref_payload(candidate_set_ref),
            "effective_policy": effective_policy.model_dump(mode="python"),
            "draft": draft.model_dump(mode="python"),
        },
    )


def fingerprint_terminal_record(
    request_digest: ContentDigest,
    record_type: str,
    record: BaseModel,
    terminal_successor_operation_id: OperationId,
) -> ContentDigest:
    return _digest(
        "terminal_record",
        {
            "request_digest": request_digest,
            "record_type": record_type,
            "record": _model_hash(record),
            "terminal_successor_operation_id": operation_id_payload(terminal_successor_operation_id),
        },
    )


# Friendly aliases used by narrow callers; these remain module-private from the
# package facade and all share the same frozen field set above.
request_initial_fingerprint = fingerprint_request_initial
request_resume_fingerprint = fingerprint_request_resume
planning_context_fingerprint = fingerprint_planning_context
candidate_handle_fingerprint = fingerprint_candidate_handle
candidate_evidence_fingerprint = fingerprint_candidate_evidence
geocode_fingerprint = fingerprint_geocode
matrix_fingerprint = fingerprint_matrix
directions_fingerprint = fingerprint_directions
directions_handle_fingerprint = fingerprint_directions_handle
gap_fingerprint = fingerprint_gap
submit_fingerprint = fingerprint_submit
terminal_record_fingerprint = fingerprint_terminal_record


__all__ = ()
