import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.baidu_map import (
    BaiduCoordinate,
    BaiduMapTimeoutError,
    DirectionsNoResult,
    GeocodeCandidate,
    GeocodeAmbiguous,
    GeocodeNoResult,
    GeocodeSuccess,
    MapCapability,
    MapProvenance,
    MatrixTravelMode,
    RouteMatrixNoResult,
    TravelMode,
)
from app.itinerary_planning.map_execution import (
    DirectionsBatchResult,
    DirectionsLegSpec,
    HubEndpointQuery,
    LocationBinding,
    MapExecutionError,
    MapExecutionCache,
    MapExecutor,
    _SharedMapConcurrency,
    _DirectionsAdmission,
    direction_matches,
    selected_direction_summary,
)
from app.itinerary_planning.models import CandidateLocationRef, TransportSemantic
from app.workflow_contracts import ExecutionBudgetKey, ExecutionBudgetScope, OperationId, WorkflowPhase
from tests.support.itinerary_planning_fakes import (
    FakeMapAdapter,
    directions_success,
    make_budget_manager,
    matrix_success,
    map_provenance,
)


def _setup(limit: int = 8):
    run_id = uuid4()
    key = ExecutionBudgetKey(scope=ExecutionBudgetScope.PHASE, name="planning.direction_call", phase=WorkflowPhase.ITINERARY_PLANNING)
    adapter = FakeMapAdapter()
    budget = make_budget_manager(run_id, key, limit)
    executor = MapExecutor(
        adapter=adapter, budget_manager=budget,
        context_digest="sha256:" + "2" * 64,
        cache=MapExecutionCache(),
    )
    return run_id, key, adapter, budget, executor


def _binding(index: int) -> LocationBinding:
    return LocationBinding(
        handle=f"h-{index}",
        location=CandidateLocationRef(admission_ordinal=index, uid=f"candidate-{index}"),
        coordinate=BaiduCoordinate(latitude=31.2 + index / 100, longitude=121.5 + index / 100),
    )


def _spec(run_id, index: int) -> DirectionsLegSpec:
    return DirectionsLegSpec(
        leg_id=f"leg-{index}", origin=_binding(index), destination=_binding(index + 1),
        transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
        alternatives_limit=3, operation_id=OperationId(
            run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"direction_{index}"
        ),
    )


class _BarrierMapAdapter:
    """让测试在三槽闸门处同步，不用时间等待来推断并发。"""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.first_wave = asyncio.Event()
        self.release = asyncio.Event()
        self.geocode_calls: list[object] = []
        self.matrix_calls: list[object] = []
        self.directions_calls: list[object] = []

    async def _hold(self, outcome: object) -> object:
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active == 3:
            self.first_wave.set()
        try:
            await self.release.wait()
            return outcome
        finally:
            self.active -= 1

    async def geocode(self, request: object) -> object:
        self.geocode_calls.append(request)
        return await self._hold(GeocodeSuccess(
            result=GeocodeCandidate(
                formatted_address="上海市虹桥机场",
                coordinate=BaiduCoordinate(latitude=31.1979, longitude=121.3363),
                confidence=0.99,
            ),
            provenance=MapProvenance(
                capability=MapCapability.GEOCODING,
                retrieved_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
                verified=True,
            ),
        ))

    async def calculate_route_matrix(self, request: object) -> object:
        self.matrix_calls.append(request)
        return await self._hold(matrix_success(len(request.origins)))

    async def plan_directions(self, request: object) -> object:
        self.directions_calls.append(request)
        return await self._hold(directions_success())


@pytest.mark.asyncio
async def test_matrix_is_ordered_square_for_ten_points_and_never_spends_direction_budget() -> None:
    run_id, key, adapter, budget, executor = _setup()
    locations = tuple(_binding(index) for index in range(10))
    observation = await executor.calculate_route_matrix(
        matrix_date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING,
        locations=locations,
    )
    assert observation.status == "success"
    assert len(observation.cells) == 100
    assert len(adapter.matrix_calls) == 1
    assert (await budget.get_snapshot(key=key)).consumed == 0


@pytest.mark.asyncio
async def test_directions_wave_is_three_ordered_and_strips_route_steps() -> None:
    run_id, key, adapter, budget, executor = _setup(8)
    result = await executor.plan_directions(tuple(_spec(run_id, index) for index in range(5)), direction_budget_key=key)
    assert result.status == "completed"
    assert [item.leg_id for item in result.observations] == [f"leg-{i}" for i in range(5)]
    assert result.peak_concurrency <= 3
    assert adapter.peak <= 3
    assert all(not hasattr(summary, "steps") for item in result.observations for summary in item.alternatives)
    assert (await budget.get_snapshot(key=key)).consumed == 5


@pytest.mark.asyncio
async def test_direction_budget_precheck_is_all_or_none_and_cache_hit_is_free() -> None:
    run_id, key, adapter, budget, executor = _setup(2)
    specs = tuple(_spec(run_id, index) for index in range(3))
    rejected = await executor.plan_directions(specs, direction_budget_key=key)
    assert rejected.status == "not_admitted_budget_exhausted"
    assert rejected.reservations_applied == 0
    assert not adapter.directions_calls
    first = await executor.plan_directions(specs[:1], direction_budget_key=key)
    assert first.status == "completed"
    calls = len(adapter.directions_calls)
    replay = await executor.plan_directions((
        DirectionsLegSpec(
            leg_id="replay-leg", origin=specs[0].origin, destination=specs[0].destination,
            transport_semantic=specs[0].transport_semantic, route_mode=specs[0].route_mode,
            alternatives_limit=specs[0].alternatives_limit,
            operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="direction_replay"),
        ),
    ), direction_budget_key=key)
    assert replay.cache_hits == 1
    assert len(adapter.directions_calls) == calls
    assert (await budget.get_snapshot(key=key)).consumed == 1


@pytest.mark.asyncio
async def test_cancellation_waits_for_started_tasks_and_does_not_issue_later_wave() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    run_id = uuid4()
    key = ExecutionBudgetKey(scope=ExecutionBudgetScope.PHASE, name="planning.direction_call", phase=WorkflowPhase.ITINERARY_PLANNING)
    adapter = FakeMapAdapter(started=started, release=release)
    budget = make_budget_manager(run_id, key, 8)
    executor = MapExecutor(adapter=adapter, budget_manager=budget, context_digest="sha256:" + "2" * 64)
    task = asyncio.create_task(executor.plan_directions(tuple(_spec(run_id, index) for index in range(5)), direction_budget_key=key))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.active == 0
    assert len(adapter.directions_calls) <= 3


@pytest.mark.asyncio
async def test_shared_map_limit_covers_two_batches_on_one_executor() -> None:
    run_id = uuid4()
    key = ExecutionBudgetKey(
        scope=ExecutionBudgetScope.PHASE,
        name="planning.direction_call",
        phase=WorkflowPhase.ITINERARY_PLANNING,
    )
    adapter = _BarrierMapAdapter()
    budget = make_budget_manager(run_id, key, 6)
    executor = MapExecutor(
        adapter=adapter, budget_manager=budget, context_digest="sha256:" + "7" * 64
    )
    first = asyncio.create_task(
        executor.plan_directions(tuple(_spec(run_id, index) for index in range(3)), direction_budget_key=key)
    )
    second = asyncio.create_task(
        executor.plan_directions(tuple(_spec(run_id, index) for index in range(3, 6)), direction_budget_key=key)
    )
    await adapter.first_wave.wait()
    adapter.release.set()
    results = await asyncio.gather(first, second)
    assert all(item.status == "completed" for item in results)
    assert len(adapter.directions_calls) == 6
    assert adapter.peak <= 3
    assert all(item.peak_concurrency <= 3 for item in results)


@pytest.mark.asyncio
async def test_shared_map_limit_covers_mixed_capabilities_and_executors() -> None:
    adapter = _BarrierMapAdapter()
    direction_key_a = ExecutionBudgetKey(
        scope=ExecutionBudgetScope.PHASE,
        name="planning.direction_call",
        phase=WorkflowPhase.ITINERARY_PLANNING,
    )
    direction_key_b = direction_key_a
    run_geo, run_matrix, run_a, run_b = (uuid4() for _ in range(4))
    geocode_executor = MapExecutor(
        adapter=adapter,
        budget_manager=make_budget_manager(run_geo, direction_key_a, 1),
        context_digest="sha256:" + "8" * 64,
        hub_queries=(HubEndpointQuery(
            endpoint_kind="arrival", address="上海虹桥机场", destination="上海",
            text_digest="sha256:" + "9" * 64,
        ),),
    )
    matrix_executor = MapExecutor(
        adapter=adapter,
        budget_manager=make_budget_manager(run_matrix, direction_key_a, 1),
        context_digest="sha256:" + "a" * 64,
    )
    direction_executor_a = MapExecutor(
        adapter=adapter,
        budget_manager=make_budget_manager(run_a, direction_key_a, 1),
        context_digest="sha256:" + "b" * 64,
    )
    direction_executor_b = MapExecutor(
        adapter=adapter,
        budget_manager=make_budget_manager(run_b, direction_key_b, 1),
        context_digest="sha256:" + "c" * 64,
    )
    tasks = (
        asyncio.create_task(geocode_executor.geocode_hub(endpoint_kind="arrival")),
        asyncio.create_task(matrix_executor.calculate_route_matrix(
            matrix_date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING, locations=(_binding(0),)
        )),
        asyncio.create_task(direction_executor_a.plan_directions((_spec(run_a, 0),), direction_budget_key=direction_key_a)),
        asyncio.create_task(direction_executor_b.plan_directions((_spec(run_b, 1),), direction_budget_key=direction_key_b)),
    )
    await adapter.first_wave.wait()
    adapter.release.set()
    await asyncio.gather(*tasks)
    assert len(adapter.geocode_calls) == 1
    assert len(adapter.matrix_calls) == 1
    assert len(adapter.directions_calls) == 2
    assert adapter.peak <= 3
    assert adapter.active == 0


@pytest.mark.asyncio
async def test_cancelled_waiting_batch_releases_shared_slot_and_no_later_wave() -> None:
    run_id = uuid4()
    key = ExecutionBudgetKey(
        scope=ExecutionBudgetScope.PHASE,
        name="planning.direction_call",
        phase=WorkflowPhase.ITINERARY_PLANNING,
    )
    adapter = _BarrierMapAdapter()
    budget = make_budget_manager(run_id, key, 8)
    executor = MapExecutor(
        adapter=adapter, budget_manager=budget, context_digest="sha256:" + "d" * 64
    )
    task = asyncio.create_task(
        executor.plan_directions(tuple(_spec(run_id, index) for index in range(5)), direction_budget_key=key)
    )
    await adapter.first_wave.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.active == 0
    assert len(adapter.directions_calls) == 3
    adapter.release.set()
    replay = await executor.plan_directions((_spec(run_id, 5),), direction_budget_key=key)
    assert replay.status == "completed"
    assert adapter.active == 0


def _hub_query(endpoint_kind: str = "arrival") -> HubEndpointQuery:
    return HubEndpointQuery(
        endpoint_kind=endpoint_kind,
        address="上海虹桥机场",
        destination="上海",
        text_digest="sha256:" + "e" * 64,
    )


@pytest.mark.asyncio
async def test_geocode_projects_all_public_outcomes_and_cache_rules() -> None:
    success_adapter = FakeMapAdapter(geocode_factory=lambda _: GeocodeSuccess(
        result=GeocodeCandidate(
            formatted_address="上海虹桥机场",
            coordinate=BaiduCoordinate(latitude=31.2, longitude=121.5),
            confidence=0.99,
        ),
        provenance=map_provenance(MapCapability.GEOCODING),
    ))
    _, key, _, _, _ = _setup()
    executor = MapExecutor(
        adapter=success_adapter,
        budget_manager=make_budget_manager(uuid4(), key, 1),
        context_digest="sha256:" + "3" * 64,
        hub_queries=(_hub_query(),),
    )
    first = await executor.geocode_hub(endpoint_kind="arrival", operation_id=None)
    replay = await executor.geocode_hub(endpoint_kind="arrival", operation_id=None)
    assert first.status == replay.status == "success"
    assert replay.cache_hit is True
    assert len(success_adapter.geocode_calls) == 1

    ambiguous = GeocodeAmbiguous(
        candidates=(
            GeocodeCandidate(formatted_address="候选一", coordinate=BaiduCoordinate(latitude=31.2, longitude=121.5)),
            GeocodeCandidate(formatted_address="候选二", coordinate=BaiduCoordinate(latitude=31.3, longitude=121.6)),
        ),
        provenance=map_provenance(MapCapability.GEOCODING),
    )
    no_result = GeocodeNoResult(provenance=map_provenance(MapCapability.GEOCODING))
    for outcome, expected in ((ambiguous, "ambiguous"), (no_result, "no_result")):
        adapter = FakeMapAdapter(geocode_factory=lambda _, outcome=outcome: outcome)
        executor = MapExecutor(
            adapter=adapter, budget_manager=make_budget_manager(uuid4(), key, 1),
            context_digest="sha256:" + "4" * 64, hub_queries=(_hub_query(),),
        )
        assert (await executor.geocode_hub(endpoint_kind="arrival")).status == expected

    def raise_timeout(_: object) -> object:
        raise BaiduMapTimeoutError(capability=MapCapability.GEOCODING)

    error_adapter = FakeMapAdapter(geocode_factory=raise_timeout)
    executor = MapExecutor(
        adapter=error_adapter, budget_manager=make_budget_manager(uuid4(), key, 1),
        context_digest="sha256:" + "5" * 64, hub_queries=(_hub_query(),),
    )
    error = await executor.geocode_hub(endpoint_kind="arrival")
    assert error.status == "failure" and error.error_code.value == "timeout_error"

    unknown = MapExecutor(
        adapter=FakeMapAdapter(geocode_factory=lambda _: object()),
        budget_manager=make_budget_manager(uuid4(), key, 1),
        context_digest="sha256:" + "6" * 64, hub_queries=(_hub_query(),),
    )
    with pytest.raises(MapExecutionError):
        await unknown.geocode_hub(endpoint_kind="arrival")
    with pytest.raises(ValueError):
        unknown.require_hub_query("departure")
    with pytest.raises(ValueError):
        await unknown.geocode_hub(endpoint_kind="departure", query=_hub_query("arrival"))


@pytest.mark.asyncio
async def test_matrix_projects_no_result_errors_and_enforces_request_identity() -> None:
    run_id, key, adapter, budget, executor = _setup()
    locations = (_binding(0), _binding(1))
    first = await executor.calculate_route_matrix(
        matrix_date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING, locations=locations,
    )
    replay = await executor.calculate_route_matrix(
        matrix_date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING, locations=locations,
        iteration_index=1,
    )
    assert first.status == "success" and replay.cache_hit is True
    assert len(adapter.matrix_calls) == 1
    assert (await budget.get_snapshot(key=key)).consumed == 0

    no_result = RouteMatrixNoResult(provenance=map_provenance(MapCapability.ROUTE_MATRIX))
    for outcome in (no_result,):
        fresh = MapExecutor(
            adapter=FakeMapAdapter(matrix_factory=lambda _, outcome=outcome: outcome),
            budget_manager=make_budget_manager(uuid4(), key, 1),
            context_digest="sha256:" + "9" * 64,
        )
        result = await fresh.calculate_route_matrix(
            matrix_date=date(2026, 9, 4), mode=MatrixTravelMode.WALKING, locations=(_binding(0),),
        )
        assert result.status == "no_result"

    def raise_timeout(_: object) -> object:
        raise BaiduMapTimeoutError(capability=MapCapability.ROUTE_MATRIX)

    error_executor = MapExecutor(
        adapter=FakeMapAdapter(matrix_factory=raise_timeout),
        budget_manager=make_budget_manager(uuid4(), key, 1),
        context_digest="sha256:" + "a" * 64,
    )
    error = await error_executor.calculate_route_matrix(
        matrix_date=date(2026, 9, 5), mode=MatrixTravelMode.RIDING, locations=(_binding(0),),
    )
    assert error.status == "failure"
    with pytest.raises(ValueError):
        await executor.calculate_route_matrix(
            matrix_date=date(2026, 9, 3), mode=TravelMode.TRANSIT, locations=locations,
            iteration_index=2,
        )
    with pytest.raises(ValueError):
        await executor.calculate_route_matrix(
            matrix_date=date(2026, 9, 6), mode=MatrixTravelMode.DRIVING,
            locations=(_binding(0), _binding(0)), iteration_index=2,
        )
    with pytest.raises(ValueError):
        await executor.calculate_route_matrix(
            matrix_date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING,
            locations=locations, iteration_index=0,
        )
    executor.set_context_after_user_correction("sha256:" + "b" * 64)
    assert len(executor.cache) == 0
    with pytest.raises(ValueError):
        executor.set_context_after_user_correction("not-a-digest")


@pytest.mark.asyncio
async def test_direction_no_result_failure_unknown_and_selection_matching() -> None:
    run_id, key, _, _, _ = _setup()
    no_result_executor = MapExecutor(
        adapter=FakeMapAdapter(directions_factory=lambda _: DirectionsNoResult(provenance=map_provenance(MapCapability.DIRECTIONS))),
        budget_manager=make_budget_manager(run_id, key, 1), context_digest="sha256:" + "c" * 64,
    )
    no_result = await no_result_executor.plan_directions((_spec(run_id, 0),), direction_budget_key=key)
    assert no_result.observations[0].status == "no_result"

    def raise_timeout(_: object) -> object:
        raise BaiduMapTimeoutError(capability=MapCapability.DIRECTIONS)

    error_run = uuid4()
    error_executor = MapExecutor(
        adapter=FakeMapAdapter(directions_factory=raise_timeout),
        budget_manager=make_budget_manager(error_run, key, 1), context_digest="sha256:" + "d" * 64,
    )
    error = await error_executor.plan_directions((_spec(error_run, 0),), direction_budget_key=key)
    assert error.observations[0].status == "failure"

    unknown_run = uuid4()
    unknown_executor = MapExecutor(
        adapter=FakeMapAdapter(directions_factory=lambda _: object()),
        budget_manager=make_budget_manager(unknown_run, key, 1), context_digest="sha256:" + "f" * 64,
    )
    with pytest.raises(MapExecutionError):
        await unknown_executor.plan_directions((_spec(unknown_run, 0),), direction_budget_key=key)

    success_executor = MapExecutor(
        adapter=FakeMapAdapter(), budget_manager=make_budget_manager(run_id, key, 1),
        context_digest="sha256:" + "1" * 64,
    )
    result = await success_executor.plan_directions((_spec(run_id, 1),), direction_budget_key=key)
    observation = result.observations[0]
    assert direction_matches(observation, origin=observation.origin, destination=observation.destination, route_mode=observation.route_mode, alternative_index=0)
    assert selected_direction_summary(observation, 0).distance_m > 0
    for changed in (
        observation.__class__(**{**observation.__dict__, "status": "failure"}) if hasattr(observation, "__dict__") else None,
    ):
        assert changed is None or not direction_matches(changed, origin=observation.origin, destination=observation.destination, route_mode=observation.route_mode, alternative_index=0)
    assert not direction_matches(observation, origin=_binding(8), destination=observation.destination, route_mode=observation.route_mode, alternative_index=0)
    assert not direction_matches(observation, origin=observation.origin, destination=observation.destination, route_mode=TravelMode.DRIVING, alternative_index=0)
    assert not direction_matches(observation, origin=observation.origin, destination=observation.destination, route_mode=observation.route_mode, alternative_index=99)
    with pytest.raises(ValueError):
        selected_direction_summary(observation, 99)


def test_direction_admission_and_cache_reject_invalid_internal_shapes() -> None:
    run_id, key, _, _, executor = _setup()
    spec = _spec(run_id, 0)
    with pytest.raises(ValueError):
        executor._validate_direction_batch((), direction_budget_key=key, max_concurrency=3)
    with pytest.raises(ValueError):
        executor._validate_direction_batch((spec,), direction_budget_key=key, max_concurrency=2)
    duplicate_leg = DirectionsLegSpec(**{**spec.__dict__, "leg_id": "leg-0"}) if hasattr(spec, "__dict__") else None
    assert duplicate_leg is None or duplicate_leg == spec
    with pytest.raises(ValueError):
        executor._validate_direction_batch((spec, _spec(run_id, 0)), direction_budget_key=key, max_concurrency=3)
    wrong_key = ExecutionBudgetKey(scope=ExecutionBudgetScope.PHASE, name="wrong.key", phase=WorkflowPhase.ITINERARY_PLANNING)
    with pytest.raises(ValueError):
        executor._validate_direction_batch((spec,), direction_budget_key=wrong_key, max_concurrency=3)
    other_run = _spec(uuid4(), 1)
    with pytest.raises(ValueError):
        executor._validate_direction_batch((spec, other_run), direction_budget_key=key, max_concurrency=3)
    executor.cache.put("directions", "sha256:" + "1" * 64, object(), context_digest=executor._context_digest)
    executor.cache.put("directions", "sha256:" + "2" * 64, type("Failure", (), {"status": "failure"})(), context_digest=executor._context_digest)
    assert len(executor.cache) == 1
    assert executor.cache.get("directions", "sha256:" + "1" * 64, context_digest="sha256:" + "0" * 64) is None


@pytest.mark.asyncio
async def test_shared_concurrency_gate_fails_closed_when_internal_semaphore_is_missing() -> None:
    gate = _SharedMapConcurrency()
    gate._active = 1
    gate._loop = asyncio.get_running_loop()
    with pytest.raises(RuntimeError):
        await gate.acquire()
    with pytest.raises(RuntimeError):
        gate.release()
