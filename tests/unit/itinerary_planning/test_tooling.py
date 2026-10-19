import asyncio
import json
from datetime import date
from uuid import uuid4

import pytest

from app.baidu_map import (
    BaiduCoordinate,
    GeocodeCandidate,
    MapCapability,
    MatrixTravelMode,
    MapProvenance,
    RouteMatrixCellNoResult,
    RouteMatrixCellSuccess,
    TravelMode,
)
from app.itinerary_planning.map_execution import (
    DirectionsBatchResult,
    DirectionsObservation,
    GeocodeObservation,
    HubEndpointQuery,
    LocationBinding,
    MapExecutor,
    MatrixObservation,
)
from app.itinerary_planning.models import (
    CalculateRouteMatrixArgs,
    CandidateLocationRef,
    DirectionLegIntent,
    GetCandidateEvidenceArgs,
    GeocodeHubArgs,
    PlanDirectionsArgs,
    PlanningTool,
    TransportSemantic,
)
from app.itinerary_planning.prompting import HandleDirectory
from app.itinerary_planning.tooling import (
    PLANNING_TOOL_CONTRACTS,
    ToolBatchError,
    ToolDispatchResult,
    ToolDispatcher,
    ToolObservation,
    _CandidateEvidenceCandidate,
    _CandidateEvidenceObservation,
    tool_results,
    validate_tool_batch,
)
from app.workflow_contracts import (
    ExecutionBudgetKey,
    ExecutionBudgetScope,
    OperationId,
    WorkflowPhase,
)
from tests.support.itinerary_planning_fakes import FakeMapAdapter, make_budget_manager


def _directory() -> HandleDirectory:
    directory = HandleDirectory()
    directory.add_location(
        CandidateLocationRef(admission_ordinal=0, uid="candidate-0"),
        BaiduCoordinate(latitude=31.2, longitude=121.5),
        handle="candidate-handle",
    )
    directory.add_location(
        CandidateLocationRef(admission_ordinal=1, uid="candidate-1"),
        BaiduCoordinate(latitude=31.21, longitude=121.51),
        handle="candidate-handle-1",
    )
    return directory


def _call(name: str, arguments: object, call_id: str = "call-1") -> object:
    from app.llm_runtime import ValidatedToolCall

    return ValidatedToolCall(call_id=call_id, tool_name=name, arguments=arguments)


def test_six_tool_contracts_are_exact_and_near_name_is_rejected_before_map_call() -> None:
    assert tuple(item.name for item in PLANNING_TOOL_CONTRACTS) == tuple(item.value for item in PlanningTool)
    directory = _directory()
    call = _call("plan_direction", GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",)))
    with pytest.raises(ToolBatchError) as error:
        validate_tool_batch((call,), sightseeing_days=1, directory=directory)
    assert error.value.code == "unknown_tool"


def test_terminal_tool_cannot_mix_with_normal_tool_or_expired_handle() -> None:
    directory = _directory()
    normal = _call(
        PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",)),
    )
    with pytest.raises(ToolBatchError):
        validate_tool_batch((normal, _call(PlanningTool.SUBMIT_ITINERARY.value, GeocodeHubArgs(endpoint_kind="arrival"), "call-2")), sightseeing_days=1, directory=directory)
    stale = _call(
        PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        GetCandidateEvidenceArgs(candidate_handles=("expired",)),
    )
    with pytest.raises(ToolBatchError) as error:
        validate_tool_batch((stale,), sightseeing_days=1, directory=directory)
    assert error.value.code == "handle_invalid"


@pytest.mark.asyncio
async def test_directions_dispatch_uses_only_location_handles() -> None:
    directory = _directory()
    run_id = uuid4()
    op = OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="direction_0")
    key = ExecutionBudgetKey(scope=ExecutionBudgetScope.PHASE, name="planning.direction_call", phase=WorkflowPhase.ITINERARY_PLANNING)
    budget = make_budget_manager(run_id, key, 2)
    executor = MapExecutor(
        adapter=FakeMapAdapter(), budget_manager=budget,
        context_digest="sha256:" + "1" * 64,
    )
    dispatcher = ToolDispatcher(directory=directory, map_executor=executor, operation_ids=(op,))
    args = PlanDirectionsArgs(legs=(DirectionLegIntent(
        leg_id="leg-0", origin_handle="candidate-handle", destination_handle="candidate-handle",
        transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1,
    ),))
    result = await dispatcher.dispatch((_call(PlanningTool.PLAN_DIRECTIONS.value, args),), sightseeing_days=1, direction_budget_key=key)
    assert result.observations[0].tool_name == PlanningTool.PLAN_DIRECTIONS.value


def _matrix_args() -> CalculateRouteMatrixArgs:
    return CalculateRouteMatrixArgs(
        date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING, location_handles=("candidate-handle",)
    )


def _direction_args() -> PlanDirectionsArgs:
    return PlanDirectionsArgs(legs=(DirectionLegIntent(
        leg_id="leg-0", origin_handle="candidate-handle", destination_handle="candidate-handle",
        transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
        alternatives_limit=1,
    ),))


def _two_direction_args() -> PlanDirectionsArgs:
    return PlanDirectionsArgs(legs=(
        DirectionLegIntent(
            leg_id="leg-0", origin_handle="candidate-handle", destination_handle="candidate-handle-1",
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
            alternatives_limit=1,
        ),
        DirectionLegIntent(
            leg_id="leg-1", origin_handle="candidate-handle-1", destination_handle="candidate-handle",
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
            alternatives_limit=1,
        ),
    ))


def _cached_and_miss_direction_args() -> PlanDirectionsArgs:
    return PlanDirectionsArgs(legs=(
        DirectionLegIntent(
            leg_id="leg-0", origin_handle="candidate-handle", destination_handle="candidate-handle",
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
            alternatives_limit=1,
        ),
        DirectionLegIntent(
            leg_id="leg-1", origin_handle="candidate-handle", destination_handle="candidate-handle-1",
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
            alternatives_limit=1,
        ),
    ))


def _direction_key() -> ExecutionBudgetKey:
    return ExecutionBudgetKey(
        scope=ExecutionBudgetScope.PHASE,
        name="planning.direction_call",
        phase=WorkflowPhase.ITINERARY_PLANNING,
    )


def _evidence_payload(handle: str = "candidate-handle") -> _CandidateEvidenceObservation:
    return _CandidateEvidenceObservation(
        status="success",
        candidates=(
            _CandidateEvidenceCandidate(
                candidate_handle=handle,
                state="eligible",
                details_status="not_requested",
            ),
        ),
    )


def _executor_for_dispatch(adapter: FakeMapAdapter, run_id, key: ExecutionBudgetKey, *, hub_queries=()):
    return MapExecutor(
        adapter=adapter,
        budget_manager=make_budget_manager(run_id, key, 2),
        context_digest="sha256:" + "3" * 64,
        hub_queries=hub_queries,
    )


def test_tool_result_rejects_untyped_evidence_before_projection() -> None:
    observation = ToolObservation(
        call_id="evidence-call",
        tool_name=PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        payload={
            "status": "success",
            "candidate": "safe-candidate",
            "evidence": {"candidate": "nested-safe"},
            "raw_provider_payload": "CANARY",
        },
    )
    result = ToolDispatchResult((observation,), terminal=False, cache_hits=0)
    with pytest.raises(ToolBatchError) as error:
        tool_results(result)
    assert error.value.code == "tool_observation_invalid"
    assert "CANARY" not in repr(observation)
    assert "CANARY" not in repr(result)


def test_tool_result_accepts_only_strict_candidate_evidence_dto() -> None:
    observation = ToolObservation(
        call_id="evidence-call",
        tool_name=PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        payload=_evidence_payload(),
    )
    projected = tool_results(ToolDispatchResult((observation,), terminal=False, cache_hits=0))
    wire = json.loads(projected[0].content)
    assert wire["tool_name"] == PlanningTool.GET_CANDIDATE_EVIDENCE.value
    assert wire["observation"]["data"]["candidates"][0]["candidate_handle"] == "candidate-handle"
    assert "provider_payload" not in projected[0].content


@pytest.mark.asyncio
async def test_dispatch_budget_admission_blocks_evidence_matrix_and_directions() -> None:
    directory = _directory()
    run_id = uuid4()
    key = _direction_key()
    adapter = FakeMapAdapter()
    budget = make_budget_manager(run_id, key, 1)
    executor = MapExecutor(adapter=adapter, budget_manager=budget, context_digest="sha256:" + "5" * 64)
    effects = {"reader": 0}

    def reader(_handles):
        effects["reader"] += 1
        return {"status": "success"}

    operations = tuple(
        OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"direction_{index}")
        for index in range(2)
    )
    dispatcher = ToolDispatcher(
        directory=directory, map_executor=executor, evidence_reader=reader, operation_ids=operations
    )
    calls = (
        _call(PlanningTool.GET_CANDIDATE_EVIDENCE.value, GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",)), "evidence"),
        _call(PlanningTool.CALCULATE_ROUTE_MATRIX.value, _matrix_args(), "matrix"),
        _call(PlanningTool.PLAN_DIRECTIONS.value, _two_direction_args(), "directions"),
    )
    result = await dispatcher.dispatch(calls, sightseeing_days=1, direction_budget_key=key)
    direction = next(item for item in result.observations if item.call_id == "directions")
    assert direction.payload.status == "not_admitted_budget_exhausted"
    assert effects["reader"] == 0
    assert not adapter.matrix_calls
    assert not adapter.directions_calls
    assert len(executor.cache) == 0
    assert not executor._matrix_seen
    assert (await budget.get_snapshot(key=key)).consumed == 0
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]


@pytest.mark.asyncio
async def test_dispatch_geocode_and_over_quota_directions_have_zero_effects() -> None:
    from tests.support.itinerary_planning_fakes import map_provenance
    from app.baidu_map import GeocodeNoResult, MapCapability

    directory = _directory()
    run_id = uuid4()
    key = _direction_key()
    adapter = FakeMapAdapter(geocode_factory=lambda _request: GeocodeNoResult(provenance=map_provenance(MapCapability.GEOCODING)))
    budget = make_budget_manager(run_id, key, 1)
    executor = MapExecutor(
        adapter=adapter,
        budget_manager=budget,
        context_digest="sha256:" + "6" * 64,
        hub_queries=(HubEndpointQuery(
            endpoint_kind="arrival", address="上海虹桥机场", destination="上海", text_digest="sha256:" + "7" * 64,
        ),),
    )
    operations = tuple(
        OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"direction_{index}")
        for index in range(2)
    )
    dispatcher = ToolDispatcher(directory=directory, map_executor=executor, operation_ids=operations)
    calls = (
        _call(PlanningTool.GEOCODE_HUB.value, GeocodeHubArgs(endpoint_kind="arrival"), "geocode"),
        _call(PlanningTool.PLAN_DIRECTIONS.value, _two_direction_args(), "directions"),
    )
    result = await dispatcher.dispatch(calls, sightseeing_days=1, direction_budget_key=key)
    direction = next(item for item in result.observations if item.call_id == "directions")
    assert direction.payload.status == "not_admitted_budget_exhausted"
    assert not adapter.geocode_calls
    assert not adapter.directions_calls
    assert (await budget.get_snapshot(key=key)).consumed == 0


class _SnapshotFailureBudget:
    def __init__(self) -> None:
        self.consume_calls = 0

    async def get_snapshot(self, *, key):
        raise RuntimeError("provider budget snapshot failure")

    async def consume(self, request):
        self.consume_calls += 1
        raise AssertionError("consume must not run")


@pytest.mark.asyncio
async def test_dispatch_budget_snapshot_failure_has_zero_effects() -> None:
    directory = _directory()
    run_id = uuid4()
    key = _direction_key()
    adapter = FakeMapAdapter()
    budget = _SnapshotFailureBudget()
    executor = MapExecutor(adapter=adapter, budget_manager=budget, context_digest="sha256:" + "8" * 64)
    effects = {"reader": 0}

    def reader(_handles):
        effects["reader"] += 1
        return {"status": "success"}

    operations = tuple(
        OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"direction_{index}")
        for index in range(2)
    )
    dispatcher = ToolDispatcher(
        directory=directory, map_executor=executor, evidence_reader=reader, operation_ids=operations
    )
    calls = (
        _call(PlanningTool.GET_CANDIDATE_EVIDENCE.value, GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",)), "evidence"),
        _call(PlanningTool.CALCULATE_ROUTE_MATRIX.value, _matrix_args(), "matrix"),
        _call(PlanningTool.PLAN_DIRECTIONS.value, _two_direction_args(), "directions"),
    )
    with pytest.raises(ToolBatchError) as error:
        await dispatcher.dispatch(calls, sightseeing_days=1, direction_budget_key=key)
    assert error.value.code == "budget_snapshot_failed"
    assert effects["reader"] == 0
    assert not adapter.matrix_calls
    assert not adapter.directions_calls
    assert budget.consume_calls == 0


@pytest.mark.asyncio
async def test_dispatch_exact_direction_quota_admits_all_cache_misses() -> None:
    directory = _directory()
    run_id = uuid4()
    key = _direction_key()
    adapter = FakeMapAdapter()
    budget = make_budget_manager(run_id, key, 2)
    executor = MapExecutor(adapter=adapter, budget_manager=budget, context_digest="sha256:" + "9" * 64)
    operations = tuple(
        OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"direction_{index}")
        for index in range(2)
    )
    dispatcher = ToolDispatcher(directory=directory, map_executor=executor, operation_ids=operations)
    result = await dispatcher.dispatch(
        (_call(PlanningTool.PLAN_DIRECTIONS.value, _two_direction_args(), "directions"),),
        sightseeing_days=1,
        direction_budget_key=key,
    )
    payload = result.observations[0].payload
    assert payload.status == "completed"
    assert payload.reservations_applied == 2
    assert len(adapter.directions_calls) == 2
    assert (await budget.get_snapshot(key=key)).consumed == 2


@pytest.mark.asyncio
async def test_dispatch_admission_counts_only_cache_misses() -> None:
    directory = _directory()
    run_id = uuid4()
    key = _direction_key()
    adapter = FakeMapAdapter()
    budget = make_budget_manager(run_id, key, 2)
    executor = MapExecutor(adapter=adapter, budget_manager=budget, context_digest="sha256:" + "a" * 64)
    first_operation = OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="direction_first")
    first = ToolDispatcher(directory=directory, map_executor=executor, operation_ids=(first_operation,))
    await first.dispatch(
        (_call(PlanningTool.PLAN_DIRECTIONS.value, _direction_args(), "first"),),
        sightseeing_days=1,
        direction_budget_key=key,
    )
    operations = tuple(
        OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"direction_replay_{index}")
        for index in range(2)
    )
    second = ToolDispatcher(directory=directory, map_executor=executor, operation_ids=operations)
    result = await second.dispatch(
        (_call(PlanningTool.PLAN_DIRECTIONS.value, _cached_and_miss_direction_args(), "second"),),
        sightseeing_days=1,
        direction_budget_key=key,
    )
    payload = result.observations[0].payload
    assert payload.status == "completed"
    assert payload.cache_hits == 1
    assert payload.reservations_applied == 1
    assert len(adapter.directions_calls) == 2
    assert (await budget.get_snapshot(key=key)).consumed == 2


@pytest.mark.asyncio
async def test_dispatch_preflight_missing_budget_has_zero_reader_map_and_budget_effects() -> None:
    directory = _directory()
    run_id = uuid4()
    key = _direction_key()
    adapter = FakeMapAdapter()
    executor = _executor_for_dispatch(adapter, run_id, key)
    effects = {"reader": 0}

    async def reader(_handles):
        effects["reader"] += 1
        return {"status": "success"}

    operation = OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="direction_0")
    dispatcher = ToolDispatcher(
        directory=directory, map_executor=executor, evidence_reader=reader, operation_ids=(operation,)
    )
    calls = (
        _call(PlanningTool.GET_CANDIDATE_EVIDENCE.value, GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",)), "evidence"),
        _call(PlanningTool.CALCULATE_ROUTE_MATRIX.value, _matrix_args(), "matrix"),
        _call(PlanningTool.PLAN_DIRECTIONS.value, _direction_args(), "directions"),
    )
    with pytest.raises(ToolBatchError) as error:
        await dispatcher.dispatch(calls, sightseeing_days=1)
    assert error.value.code == "budget_key_missing"
    assert effects["reader"] == 0
    assert not adapter.matrix_calls
    assert not adapter.directions_calls


@pytest.mark.asyncio
async def test_dispatch_preflight_insufficient_operation_plan_has_zero_effects() -> None:
    directory = _directory()
    run_id = uuid4()
    key = _direction_key()
    adapter = FakeMapAdapter()
    executor = _executor_for_dispatch(adapter, run_id, key)
    effects = {"reader": 0}

    def reader(_handles):
        effects["reader"] += 1
        return {"status": "success"}

    dispatcher = ToolDispatcher(directory=directory, map_executor=executor, evidence_reader=reader)
    calls = (
        _call(PlanningTool.GET_CANDIDATE_EVIDENCE.value, GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",)), "evidence"),
        _call(PlanningTool.CALCULATE_ROUTE_MATRIX.value, _matrix_args(), "matrix"),
        _call(PlanningTool.PLAN_DIRECTIONS.value, _direction_args(), "directions"),
    )
    with pytest.raises(ToolBatchError) as error:
        await dispatcher.dispatch(calls, sightseeing_days=1, direction_budget_key=key)
    assert error.value.code == "operation_plan_invalid"
    assert effects["reader"] == 0
    assert not adapter.matrix_calls
    assert not adapter.directions_calls


@pytest.mark.asyncio
async def test_dispatch_preflight_missing_hub_query_has_zero_prior_effects() -> None:
    directory = _directory()
    run_id = uuid4()
    key = _direction_key()
    adapter = FakeMapAdapter(geocode_factory=lambda _request: None)
    executor = _executor_for_dispatch(adapter, run_id, key)
    effects = {"reader": 0}

    def reader(_handles):
        effects["reader"] += 1
        return {"status": "success"}

    dispatcher = ToolDispatcher(directory=directory, map_executor=executor, evidence_reader=reader)
    calls = (
        _call(PlanningTool.GEOCODE_HUB.value, GeocodeHubArgs(endpoint_kind="arrival"), "geocode"),
        _call(PlanningTool.GET_CANDIDATE_EVIDENCE.value, GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",)), "evidence"),
    )
    with pytest.raises(ToolBatchError) as error:
        await dispatcher.dispatch(calls, sightseeing_days=1)
    assert error.value.code == "hub_query_missing"
    assert effects["reader"] == 0
    assert not adapter.geocode_calls


@pytest.mark.asyncio
async def test_dispatch_keeps_legal_mixed_observations_in_original_order() -> None:
    directory = _directory()
    run_id = uuid4()
    key = _direction_key()
    adapter = FakeMapAdapter()
    budget = make_budget_manager(run_id, key, 1)
    executor = MapExecutor(adapter=adapter, budget_manager=budget, context_digest="sha256:" + "4" * 64)
    operation = OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="direction_0")
    dispatcher = ToolDispatcher(
        directory=directory,
        map_executor=executor,
        evidence_reader=lambda _handles: _evidence_payload(),
        operation_ids=(operation,),
    )
    calls = (
        _call(PlanningTool.GET_CANDIDATE_EVIDENCE.value, GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",)), "evidence"),
        _call(PlanningTool.CALCULATE_ROUTE_MATRIX.value, _matrix_args(), "matrix"),
        _call(PlanningTool.PLAN_DIRECTIONS.value, _direction_args(), "directions"),
    )
    result = await dispatcher.dispatch(calls, sightseeing_days=1, direction_budget_key=key)
    assert [item.call_id for item in result.observations] == ["evidence", "matrix", "directions"]
    projected = tool_results(result)
    assert [item.call_id for item in projected] == ["evidence", "matrix", "directions"]
    assert all(json.loads(item.content)["tool_name"] == observation.tool_name for item, observation in zip(projected, result.observations, strict=True))


def test_validate_tool_batch_covers_global_limits_and_duplicate_guards() -> None:
    directory = _directory()
    valid_evidence = lambda index: _call(
        PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",)),
        f"evidence-{index}",
    )
    with pytest.raises(ToolBatchError):
        validate_tool_batch((valid_evidence(0),), sightseeing_days=0, directory=directory)
    with pytest.raises(ToolBatchError):
        validate_tool_batch((), sightseeing_days=1, directory=directory)
    with pytest.raises(ToolBatchError):
        validate_tool_batch((valid_evidence(0), valid_evidence(0)), sightseeing_days=1, directory=directory)
    with pytest.raises(ToolBatchError):
        validate_tool_batch(tuple(valid_evidence(i) for i in range(9)), sightseeing_days=1, directory=directory)
    with pytest.raises(ToolBatchError):
        validate_tool_batch((valid_evidence(0), valid_evidence(1)), sightseeing_days=1, directory=directory)

    geocode = lambda index, endpoint="arrival": _call(
        PlanningTool.GEOCODE_HUB.value, GeocodeHubArgs(endpoint_kind=endpoint), f"geo-{index}"
    )
    with pytest.raises(ToolBatchError):
        validate_tool_batch((geocode(0), geocode(1), geocode(2)), sightseeing_days=1, directory=directory)
    with pytest.raises(ToolBatchError):
        validate_tool_batch((geocode(0), geocode(1, "departure"), geocode(2, "arrival")), sightseeing_days=1, directory=directory)

    matrix = lambda index, matrix_date=date(2026, 9, 3): _call(
        PlanningTool.CALCULATE_ROUTE_MATRIX.value,
        CalculateRouteMatrixArgs(date=matrix_date, mode=MatrixTravelMode.DRIVING, location_handles=("candidate-handle",)),
        f"matrix-{index}",
    )
    with pytest.raises(ToolBatchError):
        validate_tool_batch((matrix(0), matrix(1, date(2026, 9, 4))), sightseeing_days=1, directory=directory)
    with pytest.raises(ToolBatchError):
        validate_tool_batch((matrix(0), matrix(1)), sightseeing_days=2, directory=directory)

    direction = lambda index: _call(PlanningTool.PLAN_DIRECTIONS.value, _direction_args(), f"direction-{index}")
    with pytest.raises(ToolBatchError):
        validate_tool_batch((direction(0), direction(1)), sightseeing_days=1, directory=directory)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", (2, 3))
async def test_multiple_route_calls_are_rejected_before_any_map_request(count):
    directory = _directory()
    adapter = FakeMapAdapter()
    run_id = uuid4()
    key = _direction_key()
    operations = tuple(OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING,
        logical_step=f"rejected_route_{index}") for index in range(count))
    dispatcher = ToolDispatcher(directory=directory,
        map_executor=_executor_for_dispatch(adapter, run_id, key), operation_ids=operations)
    calls = tuple(_call(PlanningTool.PLAN_DIRECTIONS.value, _direction_args(), f"route-{index}")
        for index in range(count))
    with pytest.raises(ToolBatchError) as error:
        await dispatcher.dispatch(calls, sightseeing_days=1, direction_budget_key=key)
    assert error.value.code == "action_batch_invalid"
    assert not adapter.directions_calls
    assert not adapter.geocode_calls
    assert not adapter.matrix_calls


@pytest.mark.asyncio
async def test_dispatch_preflight_and_projection_reject_or_sanitize_untrusted_shapes() -> None:
    directory = _directory()
    with pytest.raises(ToolBatchError) as error:
        await ToolDispatcher(directory=directory).dispatch(
            (_call(PlanningTool.GEOCODE_HUB.value, GeocodeHubArgs(endpoint_kind="arrival")),),
            sightseeing_days=1,
        )
    assert error.value.code == "map_executor_missing"

    run_id = uuid4()
    key = _direction_key()
    executor = _executor_for_dispatch(FakeMapAdapter(), run_id, key)
    with pytest.raises(ToolBatchError) as error:
        await ToolDispatcher(directory=directory, map_executor=executor).dispatch(
            (_call(PlanningTool.GET_CANDIDATE_EVIDENCE.value, GetCandidateEvidenceArgs(candidate_handles=("candidate-handle",))),),
            sightseeing_days=1,
        )
    assert error.value.code == "evidence_reader_missing"

    operation = OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="direction_0")
    dispatcher = ToolDispatcher(directory=directory, map_executor=executor, operation_ids=(operation,))
    with pytest.raises(ToolBatchError) as error:
        await dispatcher.dispatch(
            (_call(PlanningTool.PLAN_DIRECTIONS.value, _direction_args()),),
            sightseeing_days=1, direction_budget_key="invalid",
        )
    assert error.value.code == "budget_key_invalid"
    invalid_operations = ToolDispatcher(directory=directory, map_executor=executor, operation_ids=("invalid",))
    with pytest.raises(ToolBatchError) as error:
        await invalid_operations.dispatch(
            (_call(PlanningTool.PLAN_DIRECTIONS.value, _direction_args()),),
            sightseeing_days=1, direction_budget_key=key,
        )
    assert error.value.code == "operation_plan_invalid"

    from tests.support.itinerary_planning_fakes import map_provenance
    fp = "sha256:" + "b" * 64
    binding = LocationBinding("candidate-handle", CandidateLocationRef(admission_ordinal=0, uid="candidate-0"), BaiduCoordinate(latitude=31.2, longitude=121.5))
    candidate = GeocodeCandidate(formatted_address="上海虹桥机场", coordinate=binding.coordinate, confidence=0.9)
    provenance = map_provenance(MapCapability.GEOCODING)
    geocode_payloads = (
        GeocodeObservation("arrival", fp, "success", candidate, (), provenance, None, None, 0),
        GeocodeObservation("arrival", fp, "ambiguous", None, (candidate, candidate), provenance, None, None, 0),
        GeocodeObservation("arrival", fp, "failure", None, (), None, None, None, 0),
        GeocodeObservation("arrival", fp, "no_result", None, (), provenance, None, None, 0),
    )
    for payload in geocode_payloads:
        encoded = tool_results(ToolDispatchResult((ToolObservation("geo", PlanningTool.GEOCODE_HUB.value, payload),), False, 0))
        assert json.loads(encoded[0].content)["observation"]["status"] in {"success", "ambiguous", "failure", "no_result"}

    matrix_payload = MatrixObservation(
        date(2026, 9, 3), MatrixTravelMode.DRIVING, fp, (binding,), "success",
        (RouteMatrixCellSuccess(origin_index=0, destination_index=0, distance_m=1, duration_s=1), RouteMatrixCellNoResult(origin_index=0, destination_index=1)),
        map_provenance(MapCapability.ROUTE_MATRIX), None, None, 0,
    )
    encoded = tool_results(ToolDispatchResult((ToolObservation("matrix", PlanningTool.CALCULATE_ROUTE_MATRIX.value, matrix_payload),), False, 0))
    assert "origin_index" in encoded[0].content
    terminal = tool_results(ToolDispatchResult((ToolObservation("submit", PlanningTool.SUBMIT_ITINERARY.value, {"status": "ok", "secret": "removed"}),), True, 0))
    assert "secret" not in terminal[0].content and '"status":"ok"' in terminal[0].content
    with pytest.raises(ToolBatchError):
        tool_results(ToolDispatchResult((ToolObservation("bad", "unknown", object()),), False, 0))
    with pytest.raises(ToolBatchError):
        tool_results(ToolDispatchResult((ToolObservation("bad", PlanningTool.GEOCODE_HUB.value, GeocodeObservation("arrival", fp, "unexpected", None, (), None, None, None, 0)),), False, 0))
