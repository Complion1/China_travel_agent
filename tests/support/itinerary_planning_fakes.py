"""Module 09 阶段性 fake：只覆盖公共 Module 06/04 合同。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Callable, Iterable
from uuid import UUID, uuid4

from pydantic import BaseModel, TypeAdapter

from app.baidu_map import (
    BaiduCoordinate,
    DirectionsSuccess,
    MapCapability,
    MapProvenance,
    MatrixTravelMode,
    RouteAlternative,
    RouteEndpoint,
    RouteMatrixSuccess,
    RouteStep,
)
from app.itinerary_planning import ItineraryPlanningService
from app.itinerary_planning.models import (
    ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
    ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY,
)
from app.workflow_contracts import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactReadResult,
    ArtifactRef,
    ArtifactWriteRequest,
    BudgetConsumeRequest,
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetManagerLimits,
    InMemoryExecutionBudgetManager,
    OperationId,
    WorkflowPhase,
)


def map_provenance(capability: MapCapability) -> MapProvenance:
    return MapProvenance(
        capability=capability,
        retrieved_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        verified=True,
    )


def query_intents(day, handles):
    """Explicit attraction intents for existing route-only fixtures; never production inference."""
    from app.itinerary_planning.models import PurposeVisitIntent, VisitKind
    return tuple(PurposeVisitIntent(date=day, candidate_handle=handle, visit_kind=VisitKind.ATTRACTION,
        is_backup=False) for handle in dict.fromkeys(handles) if handle.startswith("candidate-handle-"))


def directions_success(
    *,
    distance_m: int = 1200,
    duration_s: int = 600,
    alternatives: int = 1,
) -> DirectionsSuccess:
    route_steps = tuple(
        RouteStep(
            instruction="内部路线指令不得进入 Module 09 安全投影",
            distance_m=distance_m,
            duration_s=duration_s,
            start=BaiduCoordinate(latitude=31.20, longitude=121.50),
            end=BaiduCoordinate(latitude=31.21, longitude=121.51),
        )
        for _ in range(1)
    )
    return DirectionsSuccess(
        alternatives=tuple(
            RouteAlternative(
                distance_m=distance_m + index,
                duration_s=duration_s + index,
                steps=route_steps,
            )
            for index in range(alternatives)
        ),
        provenance=map_provenance(MapCapability.DIRECTIONS),
    )


def matrix_success(size: int = 1) -> RouteMatrixSuccess:
    from app.baidu_map import RouteMatrixCellSuccess

    return RouteMatrixSuccess(
        origin_count=size,
        destination_count=size,
        cells=tuple(
            RouteMatrixCellSuccess(
                origin_index=origin,
                destination_index=destination,
                distance_m=100 * (origin + destination + 1),
                duration_s=60,
            )
            for origin in range(size)
            for destination in range(size)
        ),
        provenance=map_provenance(MapCapability.ROUTE_MATRIX),
    )


class FakeMapAdapter:
    def __init__(
        self,
        *,
        directions_factory: Callable[[object], object] | None = None,
        matrix_factory: Callable[[object], object] | None = None,
        geocode_factory: Callable[[object], object] | None = None,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.directions_factory = directions_factory or (lambda _request: directions_success())
        self.matrix_factory = matrix_factory or (lambda request: matrix_success(len(request.origins)))
        self.geocode_factory = geocode_factory
        self.started = started
        self.release = release
        self.geocode_calls: list[object] = []
        self.matrix_calls: list[object] = []
        self.directions_calls: list[object] = []
        self.active = 0
        self.peak = 0

    async def geocode(self, request: object) -> object:
        self.geocode_calls.append(request)
        if self.geocode_factory is None:
            raise AssertionError("geocode factory is not configured")
        return await self._call(self.geocode_factory, request)

    async def calculate_route_matrix(self, request: object) -> object:
        self.matrix_calls.append(request)
        return await self._call(self.matrix_factory, request)

    async def plan_directions(self, request: object) -> object:
        self.directions_calls.append(request)
        return await self._call(self.directions_factory, request)

    async def _call(self, factory: Callable[[object], object], request: object) -> object:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.started is not None:
                self.started.set()
            if self.release is not None:
                await self.release.wait()
            result = factory(request)
            if hasattr(result, "__await__"):
                return await result
            return result
        finally:
            self.active -= 1


class FakeModelOperation:
    def __init__(self, responses: Iterable[BaseModel | BaseException] = ()) -> None:
        self.responses = list(responses)
        self.prompts: list[object] = []
        self.tool_contracts: list[tuple[object, ...]] = []
        self.continuations: list[tuple[object, ...]] = []
        self.closed = False
        self.close_count = 0
        self.call_count = 0
        self.pending_call_ids: tuple[str, ...] = ()
        self.block = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke_tools(self, prompt: object, contracts: tuple[object, ...], *, admission_callback=None, request_stage=None, allow_argument_repair=False) -> BaseModel:
        self.prompts.append(prompt)
        self.tool_contracts.append(contracts)
        if admission_callback is not None:
            from app.llm_runtime import InteractionCallIdentity
            await admission_callback(InteractionCallIdentity(call_id=uuid4(), logical_call_index=self.call_count))
        return await self._next()

    async def arepair_tools(self, *, tools=None, admission_callback=None, request_stage=None) -> BaseModel:
        self.tool_contracts.append(tools)
        if admission_callback is not None:
            from app.llm_runtime import InteractionCallIdentity
            await admission_callback(InteractionCallIdentity(call_id=uuid4(), logical_call_index=self.call_count))
        return await self._next()

    async def acontinue_tools(self, results: tuple[object, ...], *, tools=None, admission_callback=None, request_stage=None) -> BaseModel:
        if tuple(item.call_id for item in results) != self.pending_call_ids or not results:
            raise ValueError("continuation must resolve every pending tool call")
        self.continuations.append(results)
        self.tool_contracts.append(tools)
        if admission_callback is not None:
            from app.llm_runtime import InteractionCallIdentity
            await admission_callback(InteractionCallIdentity(call_id=uuid4(), logical_call_index=self.call_count))
        return await self._next()

    async def _next(self) -> BaseModel:
        self.call_count += 1
        self.entered.set()
        if self.block:
            await self.release.wait()
        if self.closed:
            raise RuntimeError("operation closed")
        if not self.responses:
            raise RuntimeError("fake response exhausted")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        self.pending_call_ids = tuple(item.call_id for item in getattr(value, "calls", ()))
        return value

    def close(self) -> None:
        self.close_count += 1
        self.closed = True


class FakeRuntime:
    def __init__(self, operation: FakeModelOperation | None = None) -> None:
        self.operation = operation or FakeModelOperation()
        self.roles: list[object] = []
        self.start_count = 0

    def start_operation(self, role: object) -> FakeModelOperation:
        self.start_count += 1
        self.roles.append(role)
        return self.operation


class FakeRepository:
    """可播种上游 Artifact、并对 Module 09 写入执行最小幂等存储的 fake。"""

    def __init__(self) -> None:
        self.refs: dict[OperationId, ArtifactRef] = {}
        self.contents: dict[UUID, dict[str, object]] = {}
        self.put_requests: list[ArtifactWriteRequest] = []
        self.lookup_calls: list[OperationId] = []
        self.get_calls: list[ArtifactRef] = []
        self.fail_put_for: set[str] = set()

    @staticmethod
    def _encoded(value: BaseModel | dict[str, object]) -> tuple[dict[str, object], bytes]:
        tree = value.model_dump(mode="json", exclude_none=False) if isinstance(value, BaseModel) else value
        encoded = json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return tree, encoded

    def seed(self, ref: ArtifactRef, content: BaseModel) -> None:
        tree, encoded = self._encoded(content)
        if ref.content_hash != "sha256:" + hashlib.sha256(encoded).hexdigest():
            raise ValueError("seed ref hash mismatch")
        self.refs[ref.operation_id] = ref
        self.contents[ref.artifact_id] = tree

    async def lookup(self, *, operation_id: OperationId) -> object:
        self.lookup_calls.append(operation_id)
        ref = self.refs.get(operation_id)
        return ArtifactAbsent(operation_id=operation_id) if ref is None else ArtifactFound(ref=ref)

    async def get(self, ref: ArtifactRef, *, expected_model: type[BaseModel]) -> ArtifactReadResult[BaseModel]:
        self.get_calls.append(ref)
        tree = self.contents.get(ref.artifact_id)
        if tree is None:
            raise RuntimeError("artifact missing")
        _, encoded = self._encoded(tree)
        content = TypeAdapter(expected_model).validate_json(encoded, strict=True)
        return ArtifactReadResult(ref=ref, content=content)

    async def put(self, request: ArtifactWriteRequest) -> ArtifactRef:
        self.put_requests.append(request)
        if request.artifact_type in self.fail_put_for:
            self.fail_put_for.remove(request.artifact_type)
            raise RuntimeError("write failed")
        existing = self.refs.get(request.operation_id)
        tree, encoded = self._encoded(request.content)
        digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if existing is not None:
            if existing.content_hash != digest:
                raise RuntimeError("artifact conflict")
            return existing
        ref = ArtifactRef(
            artifact_id=uuid4(), artifact_type=request.artifact_type,
            schema_version=request.schema_version, content_hash=digest,
            operation_id=request.operation_id, created_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            size_bytes=len(encoded),
        )
        self.refs[request.operation_id] = ref
        self.contents[ref.artifact_id] = tree
        return ref


def make_planning_budget_manager(run_id: UUID, *, iteration_limit: int = 19, direction_limit: int | None = None) -> InMemoryExecutionBudgetManager:
    direction_limit = 8 * 1 + 2 if direction_limit is None else direction_limit
    plan = ExecutionBudgetPlan(
        schema_version="1.0", run_id=run_id,
        limits=(
            ExecutionBudgetLimit(key=ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY, limit=iteration_limit),
            ExecutionBudgetLimit(key=ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY, limit=direction_limit),
        ),
    )
    return InMemoryExecutionBudgetManager(plan=plan, limits=ExecutionBudgetManagerLimits(max_operation_records=4096))


def make_budget_manager(run_id: UUID, direction_key: ExecutionBudgetKey, limit: int) -> InMemoryExecutionBudgetManager:
    plan = ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=run_id,
        limits=(ExecutionBudgetLimit(key=direction_key, limit=limit),),
    )
    return InMemoryExecutionBudgetManager(
        plan=plan,
        limits=ExecutionBudgetManagerLimits(max_operation_records=4096),
    )



def remap_fixture_handles(value, replacements):
    """调整拆分批次后的已知证据句柄，保留其余测试参数。"""
    from pydantic import BaseModel
    if isinstance(value, BaseModel):
        return value.model_copy(update={name: remap_fixture_handles(getattr(value, name), replacements)
            for name in type(value).model_fields})
    if isinstance(value, tuple):
        return tuple(remap_fixture_handles(item, replacements) for item in value)
    if isinstance(value, list):
        return [remap_fixture_handles(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: remap_fixture_handles(item, replacements) for key, item in value.items()}
    return replacements.get(value, value) if isinstance(value, str) else value


def split_boundary_fixture(request, direction_call, context, route_specs, direction_ids):
    """先在前一查询批次取得接驳，再查询带全日期安排的日内路线。"""
    from datetime import date, datetime, time, timedelta
    from app.itinerary_planning.models import DailyPlanOutline, PurposeVisitIntent, TimeInterval, VisitKind
    from app.itinerary_planning.fingerprints import fingerprint_directions, fingerprint_directions_handle
    from app.itinerary_planning.policy import SHANGHAI, boundary_processing_minutes
    args = direction_call.arguments
    boundary = tuple(i for i, leg in enumerate(args.legs)
        if "candidate-handle-" not in leg.origin_handle and "candidate-handle-" not in leg.destination_handle)
    daily = tuple(i for i in range(len(args.legs)) if i not in boundary)
    order = (*boundary, *daily)
    operations = tuple(direction_ids[i] for i in order)
    replacements = {}
    for indexes in (boundary, daily):
        for ordinal, index in enumerate(indexes):
            _, origin, origin_coordinate, destination, destination_coordinate = route_specs[index]
            leg = args.legs[index]
            fingerprint = fingerprint_directions(context, origin, origin_coordinate, destination,
                destination_coordinate, leg.route_mode, leg.alternatives_limit)
            replacements[fingerprint_directions_handle(direction_ids[index], index, fingerprint)] = (
                fingerprint_directions_handle(direction_ids[index], ordinal, fingerprint))
    window = request.confirmed.requirement.time_window
    arrival, departure = window.arrival, window.departure
    start = datetime.fromisoformat(arrival.local_datetime).replace(tzinfo=SHANGHAI) + timedelta(minutes=10 +
        boundary_processing_minutes(arrival.hub_type, "arrival", is_approximate=arrival.is_approximate) + (30 if arrival.is_approximate else 0))
    end = datetime.fromisoformat(departure.local_datetime).replace(tzinfo=SHANGHAI) - timedelta(minutes=10 +
        boundary_processing_minutes(departure.hub_type, "departure", is_approximate=departure.is_approximate) + (30 if departure.is_approximate else 0))
    days = []
    for offset in range(window.sightseeing_days):
        day = date.fromisoformat(window.sightseeing_start_date) + timedelta(days=offset)
        left = max(datetime.combine(day, time(10), SHANGHAI), start)
        right = min(datetime.combine(day, time(22), SHANGHAI), end)
        visits = ()
        if left + timedelta(hours=1) <= right:
            visits = (PurposeVisitIntent(date=day, candidate_handle=f"candidate-handle-{0 if offset == 0 else offset+2:04d}",
                visit_kind=VisitKind.ATTRACTION, is_backup=False,
                interval=TimeInterval(start_at=left, end_at=left + timedelta(hours=1))),)
        days.append(DailyPlanOutline(date=day, visits=visits))
    request = request.model_copy(update={"operation_plan": request.operation_plan.model_copy(update={
        "direction_operation_ids": operations})})
    boundary_call = direction_call.model_copy(update={"call_id": direction_call.call_id + "_boundaries",
        "arguments": args.model_copy(update={"legs": tuple(args.legs[i] for i in boundary),
            "visit_intents": (), "daily_outline": ()})})
    daily_call = direction_call.model_copy(update={"arguments": args.model_copy(update={
        "legs": tuple(args.legs[i] for i in daily), "daily_outline": tuple(days)})})
    return request, boundary_call, daily_call, replacements


__all__ = (
    "FakeMapAdapter", "FakeModelOperation", "FakeRepository", "FakeRuntime",
    "directions_success", "matrix_success", "make_budget_manager", "make_planning_budget_manager",
    "map_provenance",
)

class PlanningServiceWithTrustedSyntheticResearch(ItineraryPlanningService):
    """规划规则单元测试使用已信任的合成 M08 来源；真实来源另由公共入口回归核验。"""

    async def _validate_candidate_sources(self, artifact):
        return None