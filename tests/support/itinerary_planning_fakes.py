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
from app.itinerary_planning.models import (
    ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
    ITINERARY_PLANNING_ITERATION_BUDGET_KEY,
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

    async def ainvoke_tools(self, prompt: object, contracts: tuple[object, ...]) -> BaseModel:
        self.prompts.append(prompt)
        self.tool_contracts.append(contracts)
        return await self._next()

    async def acontinue_tools(self, results: tuple[object, ...]) -> BaseModel:
        if tuple(item.call_id for item in results) != self.pending_call_ids or not results:
            raise ValueError("continuation must resolve every pending tool call")
        self.continuations.append(results)
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


def make_planning_budget_manager(run_id: UUID, *, iteration_limit: int = 6, direction_limit: int | None = None) -> InMemoryExecutionBudgetManager:
    direction_limit = 8 * 1 + 2 if direction_limit is None else direction_limit
    plan = ExecutionBudgetPlan(
        schema_version="1.0", run_id=run_id,
        limits=(
            ExecutionBudgetLimit(key=ITINERARY_PLANNING_ITERATION_BUDGET_KEY, limit=iteration_limit),
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


__all__ = (
    "FakeMapAdapter", "FakeModelOperation", "FakeRepository", "FakeRuntime",
    "directions_success", "matrix_success", "make_budget_manager", "make_planning_budget_manager",
    "map_provenance",
)
