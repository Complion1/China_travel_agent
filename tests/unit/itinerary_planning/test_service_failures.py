"""09-5 失败语义与终止写入异常的回归。"""

from __future__ import annotations

from datetime import date
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.baidu_map import (
    BaiduCoordinate,
    GeocodeAmbiguous,
    GeocodeCandidate,
    GeocodeSuccess,
    MapCapability,
    MatrixTravelMode,
    TravelMode,
)
from app.candidate_research import MealIntent, ResearchRole
from app.itinerary_planning import ItineraryPlanningIncomplete, PlanningDayKind, PlanningFailureCode, PlanningTool
from app.itinerary_planning.map_execution import (
    DirectionsBatchResult,
    DirectionsObservation,
    GeocodeObservation,
    LocationBinding,
    MatrixObservation,
)
from app.itinerary_planning.models import (
    CandidateLocationRef,
    GetCandidateEvidenceArgs,
    ITINERARY_PLANNING_ITERATION_BUDGET_KEY,
    TransportSemantic,
)
from app.itinerary_planning.service import _fresh
from app.itinerary_planning.tooling import ToolDispatchResult, ToolObservation
from app.llm_runtime import (
    LLMErrorCode,
    LLMInvocationFailure,
    LLMRuntimeError,
    ModelRole,
    ToolCallsReady,
    ToolTurnCompleted,
    ValidatedToolCall,
)
from app.requirements import HubType, TravelTimeWindowArrival, TravelTimeWindowDeparture
from app.workflow_contracts import BudgetExceeded, OperationId, WorkflowPhase

from tests.unit.itinerary_planning.test_service_initial import _fixture, _metadata
from tests.support.itinerary_planning_fakes import FakeMapAdapter, make_planning_budget_manager, map_provenance


class _BrokenLookupRepository:
    async def lookup(self, *, operation_id):
        raise RuntimeError("lookup unavailable")

    async def get(self, ref, *, expected_model):
        raise AssertionError("get must not be called")

    async def put(self, request):
        raise AssertionError("put must not be called")


class _UnknownBudgetResult:
    def __init__(self, delegate, result):
        self.delegate = delegate
        self.result = result

    async def consume(self, request):
        return self.result

    async def get_snapshot(self, *, key):
        return await self.delegate.get_snapshot(key=key)


@pytest.mark.asyncio
async def test_model_operation_exception_is_indeterminate_without_fake_retry() -> None:
    service, request, _, operation, repository = _fixture(RuntimeError("provider closed"))

    result = await service.plan_initial(request)

    assert result.failure.code is PlanningFailureCode.OPERATION_RESULT_INDETERMINATE
    assert result.failure.possible_duplicate is True
    assert operation.call_count == 1
    assert repository.put_requests == []
    again = await service.plan_initial(request)
    assert again.failure.code is PlanningFailureCode.OPERATION_RESULT_INDETERMINATE
    assert operation.call_count == 1
    assert service._runtime.start_count == 1


@pytest.mark.asyncio
async def test_unknown_tool_is_model_contract_failure() -> None:
    call = ValidatedToolCall(
        call_id="unknown_0",
        tool_name="not_a_planning_tool",
        arguments=GetCandidateEvidenceArgs(
            candidate_handles=("candidate-handle-0000",),
        ),
    )
    service, request, _, _, _ = _fixture(
        ToolCallsReady(calls=(call,), metadata=_metadata()),
    )

    result = await service.plan_initial(request)

    assert result.failure.code is PlanningFailureCode.MODEL_CONTRACT_INVALID


@pytest.mark.asyncio
async def test_model_action_write_failure_is_retryable_artifact_error() -> None:
    service, request, _, _, repository = _fixture(
        ToolTurnCompleted(final_text="需要澄清", metadata=_metadata()),
    )
    repository.fail_put_for.add("itinerary_planning.model_action")

    result = await service.plan_initial(request)

    assert result.failure.code is PlanningFailureCode.ARTIFACT_ERROR
    assert result.failure.retryable is True
    assert result.failure.possible_duplicate is False


@pytest.mark.asyncio
async def test_terminal_write_failure_is_artifact_error_then_indeterminate_without_retry() -> None:
    service, request, _, operation, repository = _fixture(
        ToolTurnCompleted(final_text="需要澄清", metadata=_metadata()),
    )
    repository.fail_put_for.add("itinerary_planning.itinerary")

    result = await service.plan_initial(request)

    assert result.failure.code is PlanningFailureCode.ARTIFACT_ERROR
    assert result.failure.retryable is False
    assert result.failure.possible_duplicate is True
    assert any(
        item.artifact_type == "itinerary_planning.itinerary"
        for item in repository.put_requests
    )
    retry = await service.plan_initial(request)
    assert retry.failure.code is PlanningFailureCode.OPERATION_RESULT_INDETERMINATE
    assert retry.failure.possible_duplicate is True
    assert operation.call_count == 1
    assert len(repository.put_requests) == 2


def test_hub_and_map_failure_projection_is_typed_and_ordered() -> None:
    service, request, *_ = _fixture(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    fp = "sha256:" + "1" * 64
    coordinate = BaiduCoordinate(latitude=30.2, longitude=120.1)
    candidate = GeocodeCandidate(formatted_address="杭州东站", coordinate=coordinate, confidence=0.9)
    success = GeocodeObservation("arrival", fp, "success", candidate, (), map_provenance(MapCapability.GEOCODING), None, None, 0)
    ambiguous = GeocodeObservation("arrival", fp, "ambiguous", None, (candidate, candidate), map_provenance(MapCapability.GEOCODING), None, None, 0)
    failure = GeocodeObservation("arrival", fp, "failure", None, (), None, __import__("app.baidu_map", fromlist=["BaiduMapErrorCode"]).BaiduMapErrorCode.TIMEOUT_ERROR, None, 0)
    assert service._hub_snapshot(success)[0] is not None
    assert len(service._hub_snapshot(ambiguous)[1]) == 2
    assert service._hub_snapshot(failure)[3] is not None

    binding = LocationBinding("candidate-handle-0000", CandidateLocationRef(admission_ordinal=0, uid="candidate-1"), coordinate)
    operation_id = OperationId(run_id=request.operation_plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="direction_failure")
    matrix = MatrixObservation(date(2026, 9, 3), MatrixTravelMode.DRIVING, fp, (binding,), "failure", (), None, __import__("app.baidu_map", fromlist=["BaiduMapErrorCode"]).BaiduMapErrorCode.RATE_LIMIT_ERROR, None, 1)
    directions = DirectionsObservation("leg-0", 0, fp, binding, binding, TransportSemantic.WALKING, TravelMode.WALKING, 1, "failure", (), None, __import__("app.baidu_map", fromlist=["BaiduMapErrorCode"]).BaiduMapErrorCode.TIMEOUT_ERROR, operation_id, fp)
    assert service._map_failure_from_dispatch(ToolDispatchResult((ToolObservation("matrix", "calculate_route_matrix", matrix),), False, 0)).capability == "route_matrix"
    assert service._map_failure_from_dispatch(ToolDispatchResult((ToolObservation("directions", "plan_directions", DirectionsBatchResult("completed", (directions,), 0, 0, 0)),), False, 0)).capability == "directions"
    assert service._map_failure_from_dispatch(ToolDispatchResult((), False, 0)) is None


def test_constructor_and_fresh_outcome_fail_closed_at_configuration_boundary() -> None:
    with pytest.raises(Exception) as error:
        __import__("app.itinerary_planning", fromlist=["ItineraryPlanningService"]).ItineraryPlanningService(
            runtime=object(), map_adapter=object(), artifact_repository=object(), budget_manager=object(),
        )
    assert error.value.__class__.__name__ == "ItineraryPlanningConfigurationError"
    service, request, *_ = _fixture(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    assert service._fresh_tool_outcome(object()) is not None
    failure = LLMInvocationFailure(error=LLMRuntimeError(
        code=LLMErrorCode.TIMEOUT,
        role=ModelRole.ITINERARY_PLANNING,
        retryable=True,
        possible_duplicate=False,
    ))
    assert isinstance(service._fresh_tool_outcome(failure), LLMInvocationFailure)
    with pytest.raises(ValueError):
        _fresh(request, type(request.operation_plan))


@pytest.mark.asyncio
async def test_terminal_replay_mismatch_lookup_failure_and_budget_outcomes_are_explicit() -> None:
    service, request, _, _, _ = _fixture(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    first = await service.plan_initial(request)
    changed = request.model_copy(update={
        "confirmed": request.confirmed.model_copy(update={
            "requirement": request.confirmed.requirement.model_copy(update={"destination": "上海"}),
        }),
    })
    mismatch = await service.plan_initial(changed)
    assert mismatch.failure.code.value == "request_mismatch"
    await service.close_run(request.operation_plan.run_id)
    await service.close_run(request.operation_plan.run_id)
    assert first is not None

    broken_service, broken_request, *_ = _fixture(ToolTurnCompleted(final_text="不会执行", metadata=_metadata()))
    broken_service._repository = _BrokenLookupRepository()
    broken = await broken_service.plan_initial(broken_request)
    assert broken.failure.code.value == "artifact_error"

    service, request, *_ = _fixture(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    await service.plan_initial(request)
    run = service._runs[request.operation_plan.run_id]
    delegate = service._budget
    proxy = _UnknownBudgetResult(delegate, object())
    service._budget = proxy
    with pytest.raises(__import__("app.itinerary_planning.service", fromlist=["_Indeterminate"])._Indeterminate):
        await service._consume_iteration(run)
    snapshot = await delegate.get_snapshot(key=ITINERARY_PLANNING_ITERATION_BUDGET_KEY)
    proxy.result = BudgetExceeded(
        disposition="rejected",
        operation_id=run.plan.iteration_slots[0].planning_state_operation_id,
        key=ITINERARY_PLANNING_ITERATION_BUDGET_KEY,
        amount=1,
        snapshot=snapshot,
    )
    assert await service._consume_iteration(run) is False


@pytest.mark.asyncio
async def test_direction_budget_refusal_is_a_typed_terminal_stop() -> None:
    models = __import__("app.itinerary_planning.models", fromlist=["DirectionLegIntent", "PlanDirectionsArgs"])
    first_args = models.PlanDirectionsArgs(legs=(models.DirectionLegIntent(
        leg_id="leg-0", origin_handle="candidate-handle-0000", destination_handle="hotel-handle",
        transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1,
    ),))
    second_args = models.PlanDirectionsArgs(legs=(
        models.DirectionLegIntent(
            leg_id="leg-1", origin_handle="hotel-handle", destination_handle="candidate-handle-0000",
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1,
        ),
        models.DirectionLegIntent(
            leg_id="leg-2", origin_handle="candidate-handle-0000", destination_handle="hotel-handle",
            transport_semantic=TransportSemantic.RIDE_HAILING, route_mode=TravelMode.DRIVING, alternatives_limit=1,
        ),
    ))
    service, request, _, _, _ = _fixture(
        ToolCallsReady(calls=(ValidatedToolCall(call_id="direction_first", tool_name=PlanningTool.PLAN_DIRECTIONS.value, arguments=first_args),), metadata=_metadata()),
        ToolCallsReady(calls=(ValidatedToolCall(call_id="direction_second", tool_name=PlanningTool.PLAN_DIRECTIONS.value, arguments=second_args),), metadata=_metadata()),
    )
    service._budget = make_planning_budget_manager(request.operation_plan.run_id, direction_limit=2)
    base = request.operation_plan
    direction_ids = tuple(OperationId(run_id=base.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"budget_direction_{index}") for index in range(3))
    request = request.model_copy(update={"operation_plan": base.model_copy(update={"direction_operation_ids": direction_ids})})

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete)
    assert result.stop_reasons == (__import__("app.itinerary_planning.models", fromlist=["PlanningStopReason"]).PlanningStopReason.DIRECTION_BUDGET_EXHAUSTED,)
