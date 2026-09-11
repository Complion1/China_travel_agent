"""09-3 的六工具合同、批次 Gate 和 typed dispatch。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field as dataclass_field
import inspect
import json
import math
from typing import Annotated, Callable, Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.baidu_map import (
    BaiduCoordinate,
    GeocodeAmbiguous,
    GeocodeCandidate,
    GeocodeNoResult,
    GeocodeSuccess,
    MapProvenance,
    RouteMatrixCellNoResult,
    RouteMatrixCellSuccess,
)
from app.llm_runtime import ToolContract, ToolResult, ValidatedToolCall
from app.workflow_contracts import ExecutionBudgetKey, OperationId, WorkflowPhase

from .map_execution import (
    DirectionsBatchResult,
    DirectionsLegSpec,
    DirectionsObservation,
    _DirectionsAdmission,
    GeocodeObservation,
    LocationBinding,
    MapExecutor,
    MatrixObservation,
)
from .models import (
    CalculateRouteMatrixArgs,
    DirectionLegIntent,
    GetCandidateEvidenceArgs,
    GeocodeHubArgs,
    PlanDirectionsArgs,
    PlanningTool,
    RequestCandidateSupplementArgs,
    SubmitItineraryArgs,
)
from .prompting import HandleDirectory, invoke_reader


_TOOL_DESCRIPTIONS = {
    PlanningTool.GET_CANDIDATE_EVIDENCE.value: "读取当前候选 handle 的已存在安全证据切片。",
    PlanningTool.GEOCODE_HUB.value: "解析已确认抵达或离开枢纽的 typed 地址。",
    PlanningTool.CALCULATE_ROUTE_MATRIX.value: "对已知地点按原序取得一个日期的完整 N×N 路线矩阵。",
    PlanningTool.PLAN_DIRECTIONS.value: "为相邻地点腿按模型顺序取得静态路线摘要、原始 leg_id 和 directions_handle。",
    PlanningTool.SUBMIT_ITINERARY.value: "提交完整的 typed 行程 proposal；每条路线必须原样复用同一 Directions Observation 的 leg_id 与 directions_handle。",
    PlanningTool.REQUEST_CANDIDATE_SUPPLEMENT.value: "提交严格的候选供给缺口并终止本次规划调用。",
}


def planning_tool_contracts() -> tuple[ToolContract[object], ...]:
    return (
        ToolContract(PlanningTool.GET_CANDIDATE_EVIDENCE.value, _TOOL_DESCRIPTIONS[PlanningTool.GET_CANDIDATE_EVIDENCE.value], GetCandidateEvidenceArgs),
        ToolContract(PlanningTool.GEOCODE_HUB.value, _TOOL_DESCRIPTIONS[PlanningTool.GEOCODE_HUB.value], GeocodeHubArgs),
        ToolContract(PlanningTool.CALCULATE_ROUTE_MATRIX.value, _TOOL_DESCRIPTIONS[PlanningTool.CALCULATE_ROUTE_MATRIX.value], CalculateRouteMatrixArgs),
        ToolContract(PlanningTool.PLAN_DIRECTIONS.value, _TOOL_DESCRIPTIONS[PlanningTool.PLAN_DIRECTIONS.value], PlanDirectionsArgs),
        ToolContract(PlanningTool.SUBMIT_ITINERARY.value, _TOOL_DESCRIPTIONS[PlanningTool.SUBMIT_ITINERARY.value], SubmitItineraryArgs),
        ToolContract(PlanningTool.REQUEST_CANDIDATE_SUPPLEMENT.value, _TOOL_DESCRIPTIONS[PlanningTool.REQUEST_CANDIDATE_SUPPLEMENT.value], RequestCandidateSupplementArgs),
    )


PLANNING_TOOL_CONTRACTS = planning_tool_contracts()
PLANNING_TOOL_NAMES = tuple(item.name for item in PLANNING_TOOL_CONTRACTS)
_ARGS_BY_TOOL = {item.name: item.args_schema for item in PLANNING_TOOL_CONTRACTS}
_TERMINAL_TOOLS = {
    PlanningTool.SUBMIT_ITINERARY.value,
    PlanningTool.REQUEST_CANDIDATE_SUPPLEMENT.value,
}


_SafeHandle = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[^\s\x00-\x1f\x7f]+$",
    ),
]
_SafeStatus = Literal["confirmed", "no_match", "ambiguous", "unresolved"]


class _CandidateRoleFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    role: Literal["attraction", "restaurant"]
    verdict: Literal["supported", "contradicted", "unresolved"]


class _CandidateInterestFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    interest: Literal["人文", "自然", "美食", "打卡"]
    verdict: _SafeStatus


class _CandidateMealFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    meal: Literal["lunch", "dinner"]
    verdict: _SafeStatus


class _CandidateRequirementFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    requirement_kind: Literal["must_visit", "avoid"]
    requirement_group_id: _SafeHandle
    verdict: _SafeStatus


class _BusinessHoursFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    evidence_handle: _SafeHandle
    business_hours: tuple[Annotated[str, Field(max_length=4096)], ...] = Field(max_length=16)


class _CandidateEvidenceCandidate(BaseModel):
    """候选证据的安全切片；不承载名称、地址、原始 provider 或自由文本。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    candidate_handle: _SafeHandle
    state: Literal["eligible", "withheld", "excluded", "conflicted"]
    role_facts: tuple[_CandidateRoleFact, ...] = Field(default=(), max_length=2)
    interest_facts: tuple[_CandidateInterestFact, ...] = Field(default=(), max_length=4)
    meal_facts: tuple[_CandidateMealFact, ...] = Field(default=(), max_length=2)
    requirement_facts: tuple[_CandidateRequirementFact, ...] = Field(default=(), max_length=32)
    details_status: Literal["not_requested", "success", "no_result"]
    evidence_handles: tuple[_SafeHandle, ...] = Field(default=(), max_length=48)
    business_hours_facts: tuple[_BusinessHoursFact, ...] = Field(default=(), max_length=1)


class _CandidateEvidenceObservation(BaseModel):
    """供 Module 09 读取的私有、冻结、严格候选证据 DTO。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["success", "no_result", "failure"]
    candidates: tuple[_CandidateEvidenceCandidate, ...] = Field(default=(), max_length=16)
    evidence_handles: tuple[_SafeHandle, ...] = Field(default=(), max_length=48)
    reason_code: Literal["not_found", "not_issued", "upstream_unavailable"] | None = None

    @model_validator(mode="after")
    def _validate_status_shape(self) -> "_CandidateEvidenceObservation":
        if self.status == "success" and (not self.candidates or self.reason_code is not None):
            raise ValueError("successful evidence must contain candidates")
        if self.status == "no_result" and (self.candidates or self.reason_code is not None):
            raise ValueError("no-result evidence must not contain candidates")
        if self.status == "failure" and (self.candidates or self.reason_code is None):
            raise ValueError("failed evidence must contain a safe reason code")
        return self


@dataclass(frozen=True, slots=True)
class ToolObservation:
    call_id: str
    tool_name: str
    payload: object = dataclass_field(repr=False)


@dataclass(frozen=True, slots=True)
class ToolDispatchResult:
    observations: tuple[ToolObservation, ...]
    terminal: bool
    cache_hits: int


@dataclass(frozen=True, slots=True)
class _DispatchPlan:
    calls: tuple[ValidatedToolCall, ...]
    evidence_handles: dict[str, tuple[str, ...]]
    matrix_bindings: dict[str, tuple[LocationBinding, ...]]
    direction_specs: dict[str, tuple[DirectionsLegSpec, ...]]
    direction_admissions: dict[str, _DirectionsAdmission]


class ToolBatchError(ValueError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _handle_names(value: object) -> tuple[str, ...]:
    names: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_handle") and type(item) is str:
                names.append(item)
            elif key.endswith("_handles") and isinstance(item, (tuple, list)):
                names.extend(item)
            else:
                names.extend(_handle_names(item))
    elif isinstance(value, (tuple, list)):
        for item in value:
            names.extend(_handle_names(item))
    return tuple(names)


def _validate_model_call(call: ValidatedToolCall) -> None:
    schema = _ARGS_BY_TOOL.get(call.tool_name)
    if schema is None:
        raise ToolBatchError("unknown_tool")
    if type(call.arguments) is not schema:
        raise ToolBatchError("tool_arguments_invalid")


def validate_tool_batch(
    calls: Sequence[ValidatedToolCall],
    *,
    sightseeing_days: int,
    directory: HandleDirectory,
) -> tuple[ValidatedToolCall, ...]:
    if not 1 <= sightseeing_days <= 60:
        raise ToolBatchError("action_batch_invalid")
    if not calls or len(calls) > sightseeing_days + 11:
        raise ToolBatchError("action_batch_invalid")
    if len({call.call_id for call in calls}) != len(calls):
        raise ToolBatchError("action_batch_invalid")
    for call in calls:
        _validate_model_call(call)
    terminal_count = sum(call.tool_name in _TERMINAL_TOOLS for call in calls)
    if terminal_count and (terminal_count != 1 or len(calls) != 1):
        raise ToolBatchError("action_batch_invalid")
    counts = {name: sum(call.tool_name == name for call in calls) for name in PLANNING_TOOL_NAMES}
    if counts[PlanningTool.GET_CANDIDATE_EVIDENCE.value] > 8:
        raise ToolBatchError("action_batch_invalid")
    evidence_handles: list[str] = []
    for call in calls:
        if call.tool_name == PlanningTool.GET_CANDIDATE_EVIDENCE.value:
            args = call.arguments
            assert isinstance(args, GetCandidateEvidenceArgs)
            evidence_handles.extend(args.candidate_handles)
            for handle in args.candidate_handles:
                try:
                    directory.require(handle, kind="candidate")
                except ValueError:
                    raise ToolBatchError("handle_invalid") from None
        elif call.tool_name == PlanningTool.GEOCODE_HUB.value:
            try:
                directory.require(f"hub-query-{call.arguments.endpoint_kind}", kind="hub_query")
            except ValueError:
                # A dispatcher may use confirmed endpoint queries without putting
                # query objects into the location directory; it validates this later.
                pass
        elif call.tool_name == PlanningTool.CALCULATE_ROUTE_MATRIX.value:
            args = call.arguments
            assert isinstance(args, CalculateRouteMatrixArgs)
            for handle in args.location_handles:
                try:
                    directory.require_location(handle)
                except ValueError:
                    raise ToolBatchError("handle_invalid") from None
        elif call.tool_name == PlanningTool.PLAN_DIRECTIONS.value:
            args = call.arguments
            assert isinstance(args, PlanDirectionsArgs)
            for leg in args.legs:
                for handle in (leg.origin_handle, leg.destination_handle):
                    try:
                        directory.require_location(handle)
                    except ValueError:
                        raise ToolBatchError("handle_invalid") from None
        elif call.tool_name in _TERMINAL_TOOLS:
            handles = _handle_names(call.arguments.model_dump(mode="python", exclude_none=False))
            for handle in handles:
                try:
                    directory.require(handle)
                except ValueError:
                    raise ToolBatchError("handle_invalid") from None
    if len(evidence_handles) != len(set(evidence_handles)):
        raise ToolBatchError("action_batch_invalid")
    if counts[PlanningTool.GEOCODE_HUB.value] > 2:
        raise ToolBatchError("action_batch_invalid")
    geocode_kinds = [call.arguments.endpoint_kind for call in calls if call.tool_name == PlanningTool.GEOCODE_HUB.value]
    if len(set(geocode_kinds)) != len(geocode_kinds):
        raise ToolBatchError("action_batch_invalid")
    if counts[PlanningTool.CALCULATE_ROUTE_MATRIX.value] > sightseeing_days:
        raise ToolBatchError("action_batch_invalid")
    matrix_dates = [call.arguments.date for call in calls if call.tool_name == PlanningTool.CALCULATE_ROUTE_MATRIX.value]
    if len(set(matrix_dates)) != len(matrix_dates):
        raise ToolBatchError("action_batch_invalid")
    if counts[PlanningTool.PLAN_DIRECTIONS.value] > 1:
        raise ToolBatchError("action_batch_invalid")
    return tuple(calls)


class ToolDispatcher:
    __slots__ = ("_directory", "_map", "_evidence_reader", "_operation_ids")

    def __init__(
        self,
        *,
        directory: HandleDirectory,
        map_executor: MapExecutor | None = None,
        evidence_reader: Callable[[tuple[str, ...]], _CandidateEvidenceObservation] | None = None,
        operation_ids: Sequence[object] = (),
    ) -> None:
        self._directory = directory
        self._map = map_executor
        self._evidence_reader = evidence_reader
        self._operation_ids = tuple(operation_ids)

    def validate(self, calls: Sequence[ValidatedToolCall], *, sightseeing_days: int) -> tuple[ValidatedToolCall, ...]:
        return validate_tool_batch(calls, sightseeing_days=sightseeing_days, directory=self._directory)

    async def _preflight(
        self,
        calls: Sequence[ValidatedToolCall],
        *,
        sightseeing_days: int,
        direction_budget_key: object | None,
    ) -> _DispatchPlan:
        valid = self.validate(calls, sightseeing_days=sightseeing_days)
        map_tools = {
            PlanningTool.GEOCODE_HUB.value,
            PlanningTool.CALCULATE_ROUTE_MATRIX.value,
            PlanningTool.PLAN_DIRECTIONS.value,
        }
        if any(call.tool_name in map_tools for call in valid) and self._map is None:
            raise ToolBatchError("map_executor_missing")

        evidence_handles: dict[str, tuple[str, ...]] = {}
        matrix_bindings: dict[str, tuple[LocationBinding, ...]] = {}
        direction_specs: dict[str, tuple[DirectionsLegSpec, ...]] = {}
        for call in valid:
            if call.tool_name == PlanningTool.GET_CANDIDATE_EVIDENCE.value:
                if self._evidence_reader is None:
                    raise ToolBatchError("evidence_reader_missing")
                evidence_handles[call.call_id] = tuple(call.arguments.candidate_handles)
                continue

            if call.tool_name == PlanningTool.GEOCODE_HUB.value:
                try:
                    self._map.require_hub_query(call.arguments.endpoint_kind)
                except ValueError:
                    raise ToolBatchError("hub_query_missing") from None
                continue

            if call.tool_name == PlanningTool.CALCULATE_ROUTE_MATRIX.value:
                args = call.arguments
                try:
                    matrix_bindings[call.call_id] = tuple(
                        self._location_binding(handle) for handle in args.location_handles
                    )
                except (TypeError, ValueError):
                    raise ToolBatchError("handle_invalid") from None
                continue

            if call.tool_name == PlanningTool.PLAN_DIRECTIONS.value:
                if direction_budget_key is None:
                    raise ToolBatchError("budget_key_missing")
                if (
                    type(direction_budget_key) is not ExecutionBudgetKey
                    or direction_budget_key.name != "planning.direction_call"
                    or direction_budget_key.phase is not WorkflowPhase.ITINERARY_PLANNING
                ):
                    raise ToolBatchError("budget_key_invalid")
                args = call.arguments
                if len(self._operation_ids) < len(args.legs):
                    raise ToolBatchError("operation_plan_invalid")
                operations = tuple(self._operation_ids[: len(args.legs)])
                if (
                    any(type(operation) is not OperationId for operation in operations)
                    or len(set(operations)) != len(operations)
                    or (
                        operations
                        and len({operation.run_id for operation in operations}) != 1
                    )
                    or any(
                        operation.phase is not WorkflowPhase.ITINERARY_PLANNING
                        for operation in operations
                    )
                ):
                    raise ToolBatchError("operation_plan_invalid")
                try:
                    direction_specs[call.call_id] = tuple(
                        DirectionsLegSpec(
                            leg_id=leg.leg_id,
                            origin=self._location_binding(leg.origin_handle),
                            destination=self._location_binding(leg.destination_handle),
                            transport_semantic=leg.transport_semantic,
                            route_mode=leg.route_mode,
                            alternatives_limit=leg.alternatives_limit,
                            operation_id=operation,
                        )
                        for leg, operation in zip(args.legs, operations, strict=True)
                    )
                except (TypeError, ValueError):
                    raise ToolBatchError("operation_plan_invalid") from None

        direction_admissions: dict[str, _DirectionsAdmission] = {}
        for call_id, specs in direction_specs.items():
            try:
                direction_admissions[call_id] = await self._map._admit_directions(
                    specs,
                    direction_budget_key=direction_budget_key,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                raise ToolBatchError("budget_snapshot_failed") from None

        return _DispatchPlan(valid, evidence_handles, matrix_bindings, direction_specs, direction_admissions)

    def _location_binding(self, handle: str) -> LocationBinding:
        entry = self._directory.require_location(handle)
        return LocationBinding(
            handle=handle,
            location=entry.value,
            coordinate=entry.coordinate,
            label=entry.label,
        )

    async def dispatch(
        self,
        calls: Sequence[ValidatedToolCall],
        *,
        sightseeing_days: int,
        iteration_index: int = 0,
        direction_budget_key: object | None = None,
    ) -> ToolDispatchResult:
        plan = await self._preflight(
            calls,
            sightseeing_days=sightseeing_days,
            direction_budget_key=direction_budget_key,
        )
        refused = next(
            (
                call
                for call in plan.calls
                if call.tool_name == PlanningTool.PLAN_DIRECTIONS.value
                and plan.direction_admissions[call.call_id].status == "not_admitted_budget_exhausted"
            ),
            None,
        )
        if refused is not None:
            admission = plan.direction_admissions[refused.call_id]
            payload = DirectionsBatchResult(
                admission.status,
                admission.cached_observations(),
                admission.cache_hits,
                0,
                self._map.peak_concurrency,
            )
            return ToolDispatchResult(
                (ToolObservation(refused.call_id, refused.tool_name, payload),),
                False,
                admission.cache_hits,
            )
        observations: list[ToolObservation] = []
        cache_hits = 0
        for ordinal, call in enumerate(plan.calls):
            if call.tool_name == PlanningTool.GET_CANDIDATE_EVIDENCE.value:
                handles = plan.evidence_handles[call.call_id]
                payload = await invoke_reader(self._evidence_reader, handles)
                observations.append(ToolObservation(call.call_id, call.tool_name, _fresh_candidate_evidence(payload)))
            elif call.tool_name == PlanningTool.GEOCODE_HUB.value:
                payload = await self._map.geocode_hub(endpoint_kind=call.arguments.endpoint_kind, action_ordinal=ordinal)
                cache_hits += int(payload.cache_hit)
                observations.append(ToolObservation(call.call_id, call.tool_name, payload))
            elif call.tool_name == PlanningTool.CALCULATE_ROUTE_MATRIX.value:
                args = call.arguments
                bindings = plan.matrix_bindings[call.call_id]
                payload = await self._map.calculate_route_matrix(matrix_date=args.date, mode=args.mode, locations=bindings, iteration_index=iteration_index, action_ordinal=ordinal)
                cache_hits += int(payload.cache_hit)
                observations.append(ToolObservation(call.call_id, call.tool_name, payload))
            elif call.tool_name == PlanningTool.PLAN_DIRECTIONS.value:
                specs = plan.direction_specs[call.call_id]
                payload = await self._map._execute_admitted_directions(
                    specs,
                    direction_budget_key=direction_budget_key,
                    max_concurrency=3,
                    admission=plan.direction_admissions[call.call_id],
                )
                cache_hits += payload.cache_hits
                observations.append(ToolObservation(call.call_id, call.tool_name, payload))
            else:
                observations.append(ToolObservation(call.call_id, call.tool_name, call.arguments))
        return ToolDispatchResult(tuple(observations), any(call.tool_name in _TERMINAL_TOOLS for call in plan.calls), cache_hits)


def tool_results(result: ToolDispatchResult, *, directory: HandleDirectory | None = None) -> tuple[ToolResult, ...]:
    projected: list[ToolResult] = []
    for observation in result.observations:
        try:
            content = _encode_tool_observation(observation)
            if directory is not None:
                data = json.loads(content)
                payload = observation.payload
                kinds = {"geocode_hub": "hub", "calculate_route_matrix": "matrix"}
                kind = kinds.get(observation.tool_name)
                issued = []
                for handle, entry_kind, _ in directory.snapshot():
                    if entry_kind != kind:
                        continue
                    value = directory.require(handle).value
                    if (kind == "hub" and getattr(value, "endpoint_kind", None) == getattr(payload, "endpoint_kind", None)) or (kind == "matrix" and getattr(value, "request_fingerprint", None) == getattr(payload, "request_fingerprint", None)):
                        issued.append(handle)
                data["observation"]["issued_handles"] = issued
                content = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if len(content.encode("utf-8")) > _MAX_TOOL_RESULT_BYTES:
                    raise ToolBatchError("tool_observation_too_large")
        except ToolBatchError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            raise ToolBatchError("tool_observation_invalid") from None
        projected.append(ToolResult(call_id=observation.call_id, content=content))
    return tuple(projected)


_MAX_TOOL_RESULT_BYTES = 64 * 1024
_SAFE_TERMINAL_KEYS = frozenset({"status", "code", "reason", "retryable", "possible_duplicate"})


def _safe_scalar(value: object) -> object:
    if value is None or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ToolBatchError("tool_observation_invalid")
        return value
    if type(value) is str:
        if len(value) > 4096:
            raise ToolBatchError("tool_observation_invalid")
        return value
    raise ToolBatchError("tool_observation_invalid")


def _safe_value(value: object, *, allowed_keys: frozenset[str], depth: int = 0) -> object:
    if depth > 4:
        raise ToolBatchError("tool_observation_invalid")
    if type(value) is dict:
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or key not in allowed_keys:
                continue
            if type(item) is dict or type(item) is list or type(item) is tuple:
                result[key] = _safe_value(item, allowed_keys=allowed_keys, depth=depth + 1)
            else:
                result[key] = _safe_scalar(item)
        return result
    if type(value) is list or type(value) is tuple:
        if len(value) > 128:
            raise ToolBatchError("tool_observation_invalid")
        return [
            _safe_value(item, allowed_keys=allowed_keys, depth=depth + 1)
            if type(item) is dict or type(item) is list or type(item) is tuple
            else _safe_scalar(item)
            for item in value
        ]
    return _safe_scalar(value)


def _coordinate(coordinate: object) -> dict[str, object]:
    if type(coordinate) is not BaiduCoordinate:
        raise ToolBatchError("tool_observation_invalid")
    return {"latitude": coordinate.latitude, "longitude": coordinate.longitude}


def _provenance(provenance: object | None) -> dict[str, object] | None:
    if provenance is None:
        return None
    if type(provenance) is not MapProvenance:
        raise ToolBatchError("tool_observation_invalid")
    return {
        "capability": provenance.capability.value,
        "verified": provenance.verified,
    }


def _project_evidence(payload: object) -> dict[str, object]:
    value = _fresh_candidate_evidence(payload)
    try:
        data = json.loads(value.model_dump_json(exclude_none=False))
    except (TypeError, ValueError):
        raise ToolBatchError("tool_observation_invalid") from None
    return {"status": value.status, "data": data}


def _fresh_candidate_evidence(payload: object) -> _CandidateEvidenceObservation:
    if type(payload) is not _CandidateEvidenceObservation:
        raise ToolBatchError("tool_observation_invalid")
    nested_types = (
        (_CandidateEvidenceCandidate, payload.candidates),
    )
    if any(type(item) is not expected for expected, items in nested_types for item in items):
        raise ToolBatchError("tool_observation_invalid")
    for candidate in payload.candidates:
        nested = (
            (_CandidateRoleFact, candidate.role_facts),
            (_CandidateInterestFact, candidate.interest_facts),
            (_CandidateMealFact, candidate.meal_facts),
            (_CandidateRequirementFact, candidate.requirement_facts),
            (_BusinessHoursFact, candidate.business_hours_facts),
        )
        if any(type(item) is not expected for expected, items in nested for item in items):
            raise ToolBatchError("tool_observation_invalid")
    try:
        return _CandidateEvidenceObservation.model_validate_json(
            payload.model_dump_json(exclude_none=False),
            strict=True,
        )
    except (AttributeError, TypeError, ValueError):
        raise ToolBatchError("tool_observation_invalid") from None


def _project_geocode(payload: object) -> dict[str, object]:
    if type(payload) is not GeocodeObservation:
        raise ToolBatchError("tool_observation_invalid")
    result: dict[str, object] = {
        "status": payload.status,
        "endpoint_kind": payload.endpoint_kind,
        "request_fingerprint": payload.request_fingerprint,
        "cache_hit": payload.cache_hit,
        "provenance": _provenance(payload.provenance),
    }
    if payload.status == "success":
        if type(payload.result) is not GeocodeCandidate:
            raise ToolBatchError("tool_observation_invalid")
        result["coordinate"] = _coordinate(payload.result.coordinate)
        result["confidence"] = payload.result.confidence
    elif payload.status == "ambiguous":
        if any(type(item) is not GeocodeCandidate for item in payload.candidates):
            raise ToolBatchError("tool_observation_invalid")
        result["candidates"] = [
            {"coordinate": _coordinate(item.coordinate), "confidence": item.confidence}
            for item in payload.candidates
        ]
    elif payload.status == "failure":
        result["error_code"] = payload.error_code.value if payload.error_code is not None else None
    elif payload.status != "no_result":
        raise ToolBatchError("tool_observation_invalid")
    return result


def _project_matrix(payload: object) -> dict[str, object]:
    if type(payload) is not MatrixObservation:
        raise ToolBatchError("tool_observation_invalid")
    cells: list[dict[str, object]] = []
    for cell in payload.cells:
        if type(cell) is RouteMatrixCellSuccess:
            cells.append({
                "status": "success",
                "origin_index": cell.origin_index,
                "destination_index": cell.destination_index,
                "distance_m": cell.distance_m,
                "duration_s": cell.duration_s,
            })
        elif type(cell) is RouteMatrixCellNoResult:
            cells.append({
                "status": "no_result",
                "origin_index": cell.origin_index,
                "destination_index": cell.destination_index,
            })
        else:
            raise ToolBatchError("tool_observation_invalid")
    return {
        "status": payload.status,
        "date": payload.matrix_date.isoformat(),
        "mode": payload.mode.value,
        "locations": [item.handle for item in payload.ordered_locations],
        "cells": cells,
        "request_fingerprint": payload.request_fingerprint,
        "cache_hit": payload.cache_hit,
        "provenance": _provenance(payload.provenance),
        "error_code": payload.error_code.value if payload.error_code is not None else None,
    }


def _project_directions_observation(payload: DirectionsObservation) -> dict[str, object]:
    if type(payload) is not DirectionsObservation:
        raise ToolBatchError("tool_observation_invalid")
    return {
        "leg_id": payload.leg_id,
        "leg_ordinal": payload.leg_ordinal,
        "leg_fingerprint": payload.leg_fingerprint,
        "origin_handle": payload.origin.handle,
        "destination_handle": payload.destination.handle,
        "transport_semantic": payload.transport_semantic.value,
        "route_mode": payload.route_mode.value,
        "alternatives_limit": payload.alternatives_limit,
        "status": payload.status,
        "alternatives": [
            {
                "alternative_index": item.alternative_index,
                "distance_m": item.distance_m,
                "duration_s": item.duration_s,
                "step_count": item.step_count,
            }
            for item in payload.alternatives
        ],
        "provenance": _provenance(payload.provenance),
        "error_code": payload.error_code.value if payload.error_code is not None else None,
        "directions_handle": payload.directions_handle,
        "cache_hit": payload.cache_hit,
    }


def _project_directions(payload: object) -> dict[str, object]:
    if type(payload) is not DirectionsBatchResult:
        raise ToolBatchError("tool_observation_invalid")
    return {
        "status": payload.status,
        "observations": [_project_directions_observation(item) for item in payload.observations],
        "cache_hits": payload.cache_hits,
        "reservations_applied": payload.reservations_applied,
        "peak_concurrency": payload.peak_concurrency,
    }


def _project_terminal(tool_name: str, payload: object) -> dict[str, object]:
    if type(payload) is dict:
        value = _safe_value(payload, allowed_keys=_SAFE_TERMINAL_KEYS)
        if type(value) is not dict:
            raise ToolBatchError("tool_observation_invalid")
        return {"status": "terminal", "tool_name": tool_name, "data": value}
    return {"status": "terminal", "tool_name": tool_name}


def _encode_tool_observation(observation: ToolObservation) -> str:
    if type(observation.call_id) is not str or type(observation.tool_name) is not str:
        raise ToolBatchError("tool_observation_invalid")
    if observation.tool_name == PlanningTool.GET_CANDIDATE_EVIDENCE.value:
        data = _project_evidence(observation.payload)
    elif observation.tool_name == PlanningTool.GEOCODE_HUB.value:
        data = _project_geocode(observation.payload)
    elif observation.tool_name == PlanningTool.CALCULATE_ROUTE_MATRIX.value:
        data = _project_matrix(observation.payload)
    elif observation.tool_name == PlanningTool.PLAN_DIRECTIONS.value:
        data = _project_directions(observation.payload)
    elif observation.tool_name in _TERMINAL_TOOLS:
        data = _project_terminal(observation.tool_name, observation.payload)
    else:
        raise ToolBatchError("tool_observation_invalid")
    content = json.dumps(
        {"schema_version": "1.0", "tool_name": observation.tool_name, "observation": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(content.encode("utf-8")) > _MAX_TOOL_RESULT_BYTES:
        raise ToolBatchError("tool_observation_too_large")
    return content


__all__ = ()
