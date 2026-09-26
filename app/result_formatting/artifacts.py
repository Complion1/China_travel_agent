"""Module 12 的严格持久模型、注册和无 I/O 写请求构造。"""

from __future__ import annotations

import json
import math
import warnings
from datetime import date, datetime, timedelta
from typing import Annotated, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from app.workflow_contracts import (
    ArtifactRef,
    ArtifactSchemaRegistration,
    ArtifactSchemaRegistry,
    ArtifactTooLargeError,
    ArtifactWriteRequest,
    OperationId,
    WorkflowPhase,
)

from .errors import ResultFormattingUsageError


RESULT_FORMATTING_SCHEMA_VERSION = "1.0"
RESULT_FORMATTING_TEMPLATE_VERSION = "zh-guide-v1"
MAX_RESULT_ARTIFACT_BYTES = 1_048_576
_SAFE_INTEGER_MAX = 2**53 - 1

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
RequirementText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
StableText = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
PlaceId = Annotated[int, Field(ge=1, le=131)]
SourceId = Annotated[int, Field(ge=1, le=1024)]
DayNumber = Annotated[int, Field(ge=1, le=60)]
NonnegativeInt = Annotated[int, Field(ge=0, le=_SAFE_INTEGER_MAX)]
PositiveInt = Annotated[int, Field(ge=1, le=_SAFE_INTEGER_MAX)]
BusinessHoursState = Literal["compatible", "unknown"]
BusinessSourceIds = Annotated[tuple[SourceId, ...], Field(min_length=0, max_length=8)]


def _check_scalar_tree(value: object) -> None:
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _check_scalar_tree(getattr(value, name))
    elif type(value) is str:
        value.encode("utf-8")
    elif type(value) is int:
        if not -_SAFE_INTEGER_MAX <= value <= _SAFE_INTEGER_MAX:
            raise ValueError("unsafe integer")
    elif type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite number")
    elif isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamp must be UTC")
    elif isinstance(value, (tuple, list)):
        for item in value:
            _check_scalar_tree(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_scalar_tree(key)
            _check_scalar_tree(item)


class _ResultArtifactModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    def __repr_args__(self):
        # Nested DTOs also contain travel privacy; never expose it through repr.
        return ()

    @model_validator(mode="after")
    def _valid_scalar_tree(self):
        _check_scalar_tree(self)
        return self


class ResultCoordinate(_ResultArtifactModel):
    coordinate_system: Literal["bd09ll"]
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


class ResultSource(_ResultArtifactModel):
    source_id: SourceId
    artifact_ref: ArtifactRef
    source: Literal["baidu_maps_mcp"]
    capability: Literal["place_search", "place_details", "geocoding", "route_matrix", "directions"]
    retrieved_at_utc: datetime
    verified: Literal[True]


class ResultPlace(_ResultArtifactModel):
    place_id: PlaceId
    kind: Literal["hotel", "candidate", "arrival_hub", "departure_hub"]
    uid: ShortText | None
    name: ShortText = Field(repr=False)
    address: ShortText = Field(repr=False)
    coordinate: ResultCoordinate
    identity_source_id: SourceId
    business_hours: Annotated[tuple[ShortText, ...], Field(min_length=0, max_length=16)] = Field(repr=False)
    business_hours_source_id: SourceId | None


class ResultInterval(_ResultArtifactModel):
    start_at_utc: datetime
    end_at_utc: datetime
    timezone: Literal["Asia/Shanghai"]

    @model_validator(mode="after")
    def _ordered_interval(self):
        if self.end_at_utc < self.start_at_utc:
            raise ValueError("interval is reversed")
        return self


def _check_business(state: str, requires_verification: bool, sources: tuple[int, ...]) -> None:
    if state == "unknown" and not requires_verification:
        raise ValueError("unknown business hours require verification")
    if len(set(sources)) != len(sources):
        raise ValueError("business source ids must be unique")


class ResultVisit(_ResultArtifactModel):
    visit_id: StableText
    place_id: PlaceId
    visit_kind: Literal["attraction", "lunch", "dinner", "attraction_lunch", "attraction_dinner"]
    interval: ResultInterval
    duration_source: Literal["planning_estimate"]
    business_hours_state: BusinessHoursState
    business_source_ids: BusinessSourceIds
    requires_verification: bool

    @model_validator(mode="after")
    def _business(self):
        _check_business(self.business_hours_state, self.requires_verification, self.business_source_ids)
        return self


class ResultRoute(_ResultArtifactModel):
    leg_id: StableText
    origin_place_id: PlaceId
    destination_place_id: PlaceId
    interval: ResultInterval
    transport_semantic: Literal["walking", "cycling", "public_transit", "ride_hailing", "private_car"]
    route_mode: Literal["walking", "riding", "transit", "driving"]
    distance_m: NonnegativeInt
    duration_s: NonnegativeInt
    scheduled_minutes: NonnegativeInt
    source_id: SourceId
    alternative_index: Annotated[int, Field(ge=0, le=2)]


class ResultBackup(_ResultArtifactModel):
    backup_kind: Literal["attraction", "lunch", "dinner"]
    core_visit_id: StableText
    place_id: PlaceId
    matrix_mode: Literal["walking", "riding", "driving"]
    distance_m: Annotated[int, Field(ge=0, le=3000)]
    source_id: SourceId
    business_hours_state: BusinessHoursState
    business_source_ids: BusinessSourceIds
    requires_verification: bool

    @model_validator(mode="after")
    def _business(self):
        _check_business(self.business_hours_state, self.requires_verification, self.business_source_ids)
        return self


class ResultTransfer(_ResultArtifactModel):
    transfer_kind: Literal["arrival", "departure"]
    hub_place_id: PlaceId
    hotel_place_id: PlaceId
    route: ResultRoute
    processing_minutes: Annotated[int, Field(ge=30, le=_SAFE_INTEGER_MAX)]
    approximate_extra_minutes: Literal[0, 30]


class ResultEndpoint(_ResultArtifactModel):
    hub_name: ShortText = Field(repr=False)
    hub_type: Literal["airport", "railway_station"]
    local_datetime: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")]
    is_approximate: bool

    @model_validator(mode="after")
    def _valid_local_datetime(self):
        datetime.strptime(self.local_datetime, "%Y-%m-%d %H:%M")
        return self


class ResultRequirement(_ResultArtifactModel):
    destination: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(repr=False)
    arrival: ResultEndpoint
    departure: ResultEndpoint
    sightseeing_start_date: date
    sightseeing_end_date: date
    sightseeing_days: DayNumber
    hotel_check_in_date: date
    hotel_check_out_date: date
    hotel_nights: PositiveInt
    adults_count: Annotated[int, Field(ge=0, le=100)]
    children_count: Annotated[int, Field(ge=0, le=100)]
    children_ages: Annotated[tuple[Annotated[int, Field(ge=0, le=17)], ...], Field(min_length=0, max_length=100)]
    seniors_count: Annotated[int, Field(ge=0, le=100)]
    total_people: Annotated[int, Field(ge=1, le=100)]
    room_count: PositiveInt
    budget_total_cny: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    budget_is_approximate: bool
    budget_scope: Literal["destination_only"]
    mobility_constraints: RequirementText | None = Field(repr=False)
    dietary_restrictions: RequirementText | None = Field(repr=False)
    room_type_requirements: RequirementText | None = Field(repr=False)
    hotel_preferences: RequirementText | None = Field(repr=False)
    transport_preferences: RequirementText | None = Field(repr=False)
    daily_time_preferences: RequirementText | None = Field(repr=False)
    free_text_notes: RequirementText | None = Field(repr=False)
    must_visit_places: Annotated[tuple[RequirementText, ...], Field(min_length=0, max_length=100)] = Field(repr=False)
    avoid_places: Annotated[tuple[RequirementText, ...], Field(min_length=0, max_length=100)] = Field(repr=False)


class ResultCore(_ResultArtifactModel):
    requirement: ResultRequirement = Field(repr=False)
    effective_pace: Literal["轻松", "标准", "紧凑"]
    places: Annotated[tuple[ResultPlace, ...], Field(min_length=3, max_length=131)] = Field(repr=False)
    sources: Annotated[tuple[ResultSource, ...], Field(min_length=1, max_length=1024)] = Field(repr=False)
    arrival_transfer: ResultTransfer
    departure_transfer: ResultTransfer
    unknown_business_visit_count: Annotated[int, Field(ge=0, le=360)]
    unknown_business_backup_count: Annotated[int, Field(ge=0, le=180)]
    weather_status: Literal["not_collected"]
    map_status: Literal["deferred"]
    cost_status: Literal["unverified"]
    scope_notice_code: Literal["m09_first_release"]

    @model_validator(mode="after")
    def _directories(self):
        if tuple(p.place_id for p in self.places) != tuple(range(1, len(self.places) + 1)):
            raise ValueError("place ids must be contiguous")
        if tuple(s.source_id for s in self.sources) != tuple(range(1, len(self.sources) + 1)):
            raise ValueError("source ids must be contiguous")
        if tuple(p.kind for p in self.places[:3]) != ("hotel", "arrival_hub", "departure_hub"):
            raise ValueError("place roles are out of order")
        if any(p.kind != "candidate" for p in self.places[3:]):
            raise ValueError("candidate directory contains another role")
        source_ids = {s.source_id for s in self.sources}
        if any(p.identity_source_id not in source_ids or (p.business_hours_source_id is not None and p.business_hours_source_id not in source_ids) for p in self.places):
            raise ValueError("place source outside directory")
        if len({s.artifact_ref.artifact_id for s in self.sources}) != len(self.sources):
            raise ValueError("source refs must be unique")
        return self


class ResultDayData(_ResultArtifactModel):
    day_no: DayNumber
    date: date
    day_kind: Literal["full", "partial", "transit_only"]
    theme: Annotated[str, StringConstraints(min_length=1, max_length=40)] = Field(repr=False)
    open_intervals: Annotated[tuple[ResultInterval, ...], Field(min_length=0, max_length=16)]
    visits: Annotated[tuple[ResultVisit, ...], Field(min_length=0, max_length=6)]
    route_legs: Annotated[tuple[ResultRoute, ...], Field(min_length=0, max_length=7)]
    backups: Annotated[tuple[ResultBackup, ...], Field(min_length=0, max_length=3)]


class ResultDayEntry(_ResultArtifactModel):
    day_no: DayNumber
    date: date
    ref: ArtifactRef


def _check_itinerary_ref(ref: ArtifactRef, run_id: UUID) -> None:
    if (ref.operation_id.run_id != run_id or ref.operation_id.phase is not WorkflowPhase.ITINERARY_PLANNING
            or ref.artifact_type != "itinerary_planning.itinerary" or ref.schema_version != "1.0"):
        raise ValueError("source itinerary reference mismatch")


def _check_markdown(value: str) -> None:
    if not value or value.startswith("\n") or value.endswith("\n") or "\r" in value:
        raise ValueError("invalid markdown fragment")


class ResultDayArtifact(_ResultArtifactModel):
    schema_version: Literal["1.0"]
    template_version: Literal["zh-guide-v1"]
    run_id: UUID
    source_itinerary_ref: ArtifactRef
    input_digest: Digest
    bundle_digest: Digest
    data: ResultDayData = Field(repr=False)
    markdown: str = Field(repr=False)

    @model_validator(mode="after")
    def _artifact(self):
        _check_itinerary_ref(self.source_itinerary_ref, self.run_id)
        _check_markdown(self.markdown)
        return self


class ResultIndexArtifact(_ResultArtifactModel):
    schema_version: Literal["1.0"]
    template_version: Literal["zh-guide-v1"]
    run_id: UUID
    source_itinerary_ref: ArtifactRef
    input_digest: Digest
    bundle_digest: Digest
    core: ResultCore = Field(repr=False)
    days: Annotated[tuple[ResultDayEntry, ...], Field(min_length=1, max_length=60)] = Field(repr=False)
    markdown_header: str = Field(repr=False)
    markdown_footer: str = Field(repr=False)

    @model_validator(mode="after")
    def _artifact(self):
        _check_itinerary_ref(self.source_itinerary_ref, self.run_id)
        _check_markdown(self.markdown_header)
        _check_markdown(self.markdown_footer)
        if len(self.days) != self.core.requirement.sightseeing_days:
            raise ValueError("index day count mismatch")
        for index, day in enumerate(self.days):
            if day.day_no != index + 1 or day.date != self.core.requirement.sightseeing_start_date + timedelta(days=index):
                raise ValueError("index days must preserve consecutive dates")
            ref = day.ref
            if (ref.operation_id.run_id != self.run_id or ref.operation_id.phase is not WorkflowPhase.OUTPUT_FORMATTING
                    or ref.artifact_type != "result_formatting.day" or ref.schema_version != "1.0"):
                raise ValueError("index day reference mismatch")
        if len({day.ref.artifact_id for day in self.days}) != len(self.days) or len({day.ref.operation_id for day in self.days}) != len(self.days):
            raise ValueError("index day references must be unique")
        if any(source.artifact_ref.operation_id.run_id != self.run_id for source in self.core.sources):
            raise ValueError("index source run mismatch")
        return self


RESULT_FORMATTING_ARTIFACT_REGISTRATIONS = (
    ArtifactSchemaRegistration(artifact_type="result_formatting.day", schema_version="1.0", model=ResultDayArtifact),
    ArtifactSchemaRegistration(artifact_type="result_formatting.index", schema_version="1.0", model=ResultIndexArtifact),
)

_TModel = TypeVar("_TModel", bound=BaseModel)


def _same_python_tree(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_python_tree(left[k], right[k]) for k in left)
    if isinstance(left, (tuple, list)):
        return len(left) == len(right) and all(_same_python_tree(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def fresh_model(value: object, expected_type: type[_TModel]) -> _TModel:
    """私有协作 API：先复验 Python tree，再返回独立 JSON round-trip。"""
    try:
        if type(value) is not expected_type:
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            original = value.model_dump(mode="python", exclude_none=False, by_alias=False, exclude_computed_fields=True)
            first = TypeAdapter(expected_type).validate_python(original, strict=True)
            checked = first.model_dump(mode="python", exclude_none=False, by_alias=False, exclude_computed_fields=True)
            if not _same_python_tree(original, checked):
                raise ValueError()
            encoded = json.dumps(first.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            return TypeAdapter(expected_type).validate_json(encoded, strict=True)
    except Exception:
        raise ResultFormattingUsageError() from None


def _prepare_request(operation_id: OperationId, artifact: ResultDayArtifact | ResultIndexArtifact) -> ArtifactWriteRequest:
    artifact_type = "result_formatting.day" if type(artifact) is ResultDayArtifact else "result_formatting.index"
    request = ArtifactWriteRequest(operation_id=operation_id, artifact_type=artifact_type, schema_version="1.0", content=artifact.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True))
    prepared = ArtifactSchemaRegistry(RESULT_FORMATTING_ARTIFACT_REGISTRATIONS).prepare(request)
    if prepared.size_bytes > MAX_RESULT_ARTIFACT_BYTES:
        raise ArtifactTooLargeError() from None
    return request


def build_result_artifact_write_request(*, operation_id: OperationId, artifact: ResultDayArtifact | ResultIndexArtifact) -> ArtifactWriteRequest:
    try:
        if type(artifact) not in (ResultDayArtifact, ResultIndexArtifact):
            raise ResultFormattingUsageError()
        fresh_artifact = fresh_model(artifact, type(artifact))
        fresh_operation = fresh_model(operation_id, OperationId)
        if fresh_operation.run_id != fresh_artifact.run_id or fresh_operation.phase is not WorkflowPhase.OUTPUT_FORMATTING:
            raise ResultFormattingUsageError()
        if isinstance(fresh_artifact, ResultIndexArtifact) and any(day.ref.operation_id == fresh_operation for day in fresh_artifact.days):
            raise ResultFormattingUsageError()
        return _prepare_request(fresh_operation, fresh_artifact)
    except ArtifactTooLargeError:
        raise
    except Exception:
        raise ResultFormattingUsageError() from None


def validate_artifact_size(artifact: ResultDayArtifact | ResultIndexArtifact) -> None:
    """私有协作 API：服务返回结果前的只读、全规范容量预检。"""
    try:
        if type(artifact) not in (ResultDayArtifact, ResultIndexArtifact):
            raise ResultFormattingUsageError()
        fresh = fresh_model(artifact, type(artifact))
        operation = OperationId(run_id=fresh.run_id, phase=WorkflowPhase.OUTPUT_FORMATTING, logical_step="result_formatting_validate")
        _prepare_request(operation, fresh)
    except ArtifactTooLargeError:
        raise
    except Exception:
        raise ResultFormattingUsageError() from None
