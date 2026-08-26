"""Module 07 阶段 07-3/07-4 的确定性测试替身。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from pydantic import TypeAdapter

from app.baidu_map import (
    BaiduCoordinate,
    MapCapability,
    MapProvenance,
    PlaceSearchNoResult,
    PlaceSearchOutcome,
    PlaceSearchRequest,
    PlaceSearchSuccess,
    PlaceSummary,
)
from app.requirements import (
    ConfirmedTravelRequirement,
    FieldMetadataRegistry,
    TravelPreferences,
    TravelRequirement,
)
from app.workflow_contracts import (
    ArtifactConflictError,
    ArtifactLookupOutcome,
    ArtifactWriteRequest,
    ArtifactSchemaRegistry,
    DEMO_ARTIFACT_REPOSITORY_LIMITS,
    DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    ExecutionBudgetLimit,
    ExecutionBudgetManagerLimits,
    ExecutionBudgetPlan,
    InMemoryArtifactRepository,
    InMemoryExecutionBudgetManager,
    OperationId,
    ExecutionBudgetKey,
    ExecutionBudgetSnapshot,
    WorkflowPhase,
)
from app.hotel_selection import (
    HOTEL_ARTIFACT_REGISTRATIONS,
    HOTEL_PLACE_CALL_BUDGET_KEY,
    HotelCandidateOperationIds,
    HotelCandidateSelectionService,
)


class FixedClock:
    """返回固定 UTC 时间，避免测试依赖墙上时钟。"""

    def __init__(self, value: datetime | None = None) -> None:
        self.value = value or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value


class SequenceUUIDFactory:
    """为 ArtifactRepository 提供可重复的 UUID 序列。"""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def new(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


class SharedEventSink:
    """把 repository、budget、adapter 的事件合并到一条可断言的轨迹。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def record(self, source: str, detail: str) -> None:
        self.events.append((source, detail))


class ScriptedBaiduMapAdapter:
    """只允许 place-search；其余六个能力若被调用立即失败。"""

    def __init__(self, responses: Iterable[object], *, event_sink: SharedEventSink | None = None) -> None:
        self.responses = list(responses)
        self.event_sink = event_sink
        self.requests: list[PlaceSearchRequest] = []
        self.search_call_count = 0
        self.non_search_call_count = 0
        self.call_counts = {
            "geocode": 0,
            "reverse_geocode": 0,
            "get_place_details": 0,
            "calculate_route_matrix": 0,
            "plan_directions": 0,
            "get_weather": 0,
        }

    async def search_places(self, request: PlaceSearchRequest) -> PlaceSearchOutcome:
        fresh = TypeAdapter(PlaceSearchRequest).validate_json(
            request.model_dump_json(), strict=True
        )
        self.requests.append(fresh)
        self.search_call_count += 1
        if self.event_sink is not None:
            self.event_sink.record("adapter", f"search:{fresh.query}")
        if not self.responses:
            raise AssertionError("scripted adapter response exhausted")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response(fresh)
        return response

    async def geocode(self, request: object) -> object:
        self.non_search_call_count += 1
        self.call_counts["geocode"] += 1
        raise AssertionError("geocode must not be called by Module 07")

    async def reverse_geocode(self, request: object) -> object:
        self.non_search_call_count += 1
        self.call_counts["reverse_geocode"] += 1
        raise AssertionError("reverse_geocode must not be called by Module 07")

    async def get_place_details(self, request: object) -> object:
        self.non_search_call_count += 1
        self.call_counts["get_place_details"] += 1
        raise AssertionError("get_place_details must not be called by Module 07")

    async def calculate_route_matrix(self, request: object) -> object:
        self.non_search_call_count += 1
        self.call_counts["calculate_route_matrix"] += 1
        raise AssertionError("calculate_route_matrix must not be called by Module 07")

    async def plan_directions(self, request: object) -> object:
        self.non_search_call_count += 1
        self.call_counts["plan_directions"] += 1
        raise AssertionError("plan_directions must not be called by Module 07")

    async def get_weather(self, request: object) -> object:
        self.non_search_call_count += 1
        self.call_counts["get_weather"] += 1
        raise AssertionError("get_weather must not be called by Module 07")


class RecordingArtifactRepository:
    """记录 Module 07 的 lookup/get/put 顺序，可一次性阻断 candidate-set put。"""

    def __init__(
        self,
        delegate,
        *,
        suppress_candidate_set_puts: int = 0,
        put_failure: BaseException | None = None,
        put_failure_artifact_type: str | None = None,
        event_sink: SharedEventSink | None = None,
    ) -> None:
        self.delegate = delegate
        self.events: list[tuple[str, str]] = []
        self.suppress_candidate_set_puts = suppress_candidate_set_puts
        self.put_failure = put_failure
        self.put_failure_artifact_type = put_failure_artifact_type
        self.event_sink = event_sink

    async def put(self, request: ArtifactWriteRequest):
        self.events.append(("put", request.artifact_type))
        if self.event_sink is not None:
            self.event_sink.record("repository", f"put:{request.artifact_type}")
        if (
            self.put_failure is not None
            and request.artifact_type == self.put_failure_artifact_type
        ):
            failure = self.put_failure
            self.put_failure = None
            raise failure
        if request.artifact_type == "hotel.candidate_set" and self.suppress_candidate_set_puts:
            self.suppress_candidate_set_puts -= 1
            raise ArtifactConflictError()
        return await self.delegate.put(request)

    async def lookup(self, *, operation_id: OperationId) -> ArtifactLookupOutcome:
        self.events.append(("lookup", operation_id.logical_step))
        if self.event_sink is not None:
            self.event_sink.record("repository", f"lookup:{operation_id.logical_step}")
        return await self.delegate.lookup(operation_id=operation_id)

    async def get(self, ref, *, expected_model):
        self.events.append(("get", ref.artifact_type))
        if self.event_sink is not None:
            self.event_sink.record("repository", f"get:{ref.artifact_type}")
        return await self.delegate.get(ref, expected_model=expected_model)


class RecordingBudgetManager:
    """预算端口的记录 wrapper；不复制预算状态机。"""

    def __init__(self, delegate, *, event_sink: SharedEventSink | None = None) -> None:
        self.delegate = delegate
        self.events: list[tuple[str, str]] = []
        self.event_sink = event_sink

    async def consume(self, request):
        self.events.append(("consume", request.operation_id.logical_step))
        if self.event_sink is not None:
            self.event_sink.record("budget", f"consume:{request.operation_id.logical_step}")
        return await self.delegate.consume(request)

    async def get_snapshot(self, *, key: ExecutionBudgetKey) -> ExecutionBudgetSnapshot:
        return await self.delegate.get_snapshot(key=key)

    async def get_all_snapshots(self):
        return await self.delegate.get_all_snapshots()


def confirmed_requirement(*, hotel_preference: str | None = None) -> ConfirmedTravelRequirement:
    return ConfirmedTravelRequirement(
        outcome_type="confirmed",
        schema_version="1.0",
        requirement=TravelRequirement(
            destination="杭州",
            preferences=TravelPreferences(hotel_preferences=hotel_preference),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(),
        confirmation_summary_digest="sha256:confirmed-requirement",
    )


def operation_ids(run_id: UUID) -> HotelCandidateOperationIds:
    phase = WorkflowPhase.HOTEL_SELECTION
    return HotelCandidateOperationIds(
        search_operation_ids=tuple(
            OperationId(run_id=run_id, phase=phase, logical_step=f"hotel_search_{index}")
            for index in range(1, 4)
        ),
        candidate_set_operation_id=OperationId(
            run_id=run_id, phase=phase, logical_step="hotel_candidate_set"
        ),
    )


def selection_operation(run_id: UUID, suffix: str = "one") -> OperationId:
    return OperationId(
        run_id=run_id,
        phase=WorkflowPhase.HOTEL_SELECTION,
        logical_step=f"hotel_selection_{suffix}",
    )


def place(uid: str, *, category: str | None = "酒店", index: int = 0) -> PlaceSummary:
    return PlaceSummary(
        uid=uid,
        name=f"测试酒店 {uid}",
        address=f"杭州测试路 {index + 1} 号",
        coordinate=BaiduCoordinate(latitude=30.20 + index / 1000, longitude=120.15 + index / 1000),
        category=category,
    )


def map_success(*places: PlaceSummary, verified: bool = True) -> PlaceSearchSuccess:
    return PlaceSearchSuccess(
        places=tuple(places),
        provenance=MapProvenance(
            capability=MapCapability.PLACE_SEARCH,
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            verified=verified,
        ),
    )


def map_no_result(*, verified: bool = True) -> PlaceSearchNoResult:
    return PlaceSearchNoResult(
        provenance=MapProvenance(
            capability=MapCapability.PLACE_SEARCH,
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            verified=verified,
        )
    )


make_no_result = map_no_result


def make_service(
    responses: Iterable[object],
    *,
    run_id: UUID | None = None,
    budget_limit: int = 3,
    event_sink: SharedEventSink | None = None,
) -> tuple[HotelCandidateSelectionService, ScriptedBaiduMapAdapter, InMemoryArtifactRepository, InMemoryExecutionBudgetManager, FixedClock]:
    run_id = run_id or UUID("00000000-0000-0000-0000-000000000007")
    clock = FixedClock()
    adapter = ScriptedBaiduMapAdapter(responses, event_sink=event_sink)
    registry = ArtifactSchemaRegistry(HOTEL_ARTIFACT_REGISTRATIONS)
    repository = InMemoryArtifactRepository(
        registry=registry,
        utc_clock=clock,
        uuid_factory=SequenceUUIDFactory(),
        limits=DEMO_ARTIFACT_REPOSITORY_LIMITS,
    )
    plan = ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=run_id,
        limits=(ExecutionBudgetLimit(key=HOTEL_PLACE_CALL_BUDGET_KEY, limit=budget_limit),),
    )
    budget = InMemoryExecutionBudgetManager(
        plan=plan,
        limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    )
    return HotelCandidateSelectionService(adapter, repository, budget, clock), adapter, repository, budget, clock


__all__ = (
    "FixedClock",
    "SequenceUUIDFactory",
    "SharedEventSink",
    "ScriptedBaiduMapAdapter",
    "confirmed_requirement",
    "operation_ids",
    "selection_operation",
    "place",
    "map_success",
    "map_no_result",
    "make_no_result",
    "make_service",
)
