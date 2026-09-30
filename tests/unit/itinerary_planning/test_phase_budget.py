"""GPHASE/GREPAIR: public-loop outcomes and actual allowed tool contracts."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.baidu_map import MatrixTravelMode, TravelMode
from app.itinerary_planning import ItineraryPlanningIncomplete, PlanningStopReason
from app.itinerary_planning.models import (
    GetCandidateEvidenceArgs, GeocodeHubArgs, CalculateRouteMatrixArgs,
    ProposedRouteLeg, TransportSemantic, ProposedBoundaryTransfer, ProposedDayPlan,
    PlanningDayKind, ProposedItineraryDraft, ProposedEffectivePlanningPolicy, SubmitItineraryArgs,
)
from app.llm_runtime import ToolCallsReady, ToolTurnCompleted, ValidatedToolCall
from app.requirements import TravelPace
from app.workflow_contracts import OperationId, WorkflowPhase
from tests.unit.itinerary_planning.test_service_initial import _fixture, _metadata


def batch(name, arguments, call_id='phase'):
    return ToolCallsReady(calls=(ValidatedToolCall(call_id=call_id, tool_name=name, arguments=arguments),), metadata=_metadata())


def hubs():
    return ToolCallsReady(calls=tuple(ValidatedToolCall(call_id=endpoint, tool_name='geocode_hub', arguments=GeocodeHubArgs(endpoint_kind=endpoint))
        for endpoint in ('arrival', 'departure')), metadata=_metadata())


def invalid_submit(*, shift=0, theme='主题'):
    start = datetime(2026,9,3,9,tzinfo=ZoneInfo('Asia/Shanghai')) + timedelta(minutes=shift)
    route = ProposedRouteLeg(leg_id='bad-route', origin_handle='hotel-handle', destination_handle='hotel-handle',
        start_at=start, end_at=start, transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
        directions_handle='hotel-handle', alternative_index=0)
    return SubmitItineraryArgs(effective_policy=ProposedEffectivePlanningPolicy(effective_pace=TravelPace.NORMAL,
        daily_time_rules=(), time_sensitive_must=(), transport_rules=()), draft=ProposedItineraryDraft(
        arrival_transfer=ProposedBoundaryTransfer(transfer_kind='arrival', hub_handle='hotel-handle', hotel_handle='hotel-handle', route_leg=route),
        days=(ProposedDayPlan(date=date(2026,9,3), day_kind=PlanningDayKind.TRANSIT_ONLY, theme=theme, open_intervals=(), visits=(), route_legs=(), backups=()),),
        departure_transfer=ProposedBoundaryTransfer(transfer_kind='departure', hub_handle='hotel-handle', hotel_handle='hotel-handle', route_leg=route.model_copy(update={'leg_id':'bad-departure'})), upstream_gap_ids=()))


@pytest.mark.asyncio
async def test_preparation_stops_at_two_without_inventing_map_no_result():
    evidence = batch('get_candidate_evidence', GetCandidateEvidenceArgs(candidate_handles=('candidate-handle-0000',)))
    service, request, _, operation, _ = _fixture(evidence, evidence)
    result = await service.plan_initial(request)
    assert isinstance(result, ItineraryPlanningIncomplete)
    assert result.stop_reasons == (PlanningStopReason.ITERATION_LIMIT_REACHED,)
    assert result.budgets.preparation_used == result.budgets.model_invocations_consumed == 2
    assert result.budgets.transport_used == result.budgets.submit_repair_used == 0
    assert not service._map_adapter.geocode_calls
    assert all(gap.gap_type != 'hub_resolution' for gap in result.gaps)
    assert operation.call_count == 2


@pytest.mark.asyncio
async def test_ready_hubs_leave_preparation_immediately():
    service, request, _, operation, _ = _fixture(hubs(), ToolTurnCompleted(final_text='未提交',metadata=_metadata()))
    result = await service.plan_initial(request)
    assert result.budgets.preparation_used == result.budgets.transport_used == 1
    first = {tool.name for tool in operation.tool_contracts[0]}
    second = {tool.name for tool in operation.tool_contracts[1]}
    assert 'geocode_hub' in first and 'plan_directions' not in first
    assert 'geocode_hub' not in second and 'plan_directions' in second and 'submit_itinerary' in second
    assert len(service._map_adapter.geocode_calls) == 2


@pytest.mark.asyncio
async def test_transport_five_requires_first_real_submit_only():
    query = [batch('calculate_route_matrix', CalculateRouteMatrixArgs(date=date(2026,9,3), mode=tuple(MatrixTravelMode)[i//2],
        location_handles=('hotel-handle','candidate-handle-0000') if i%2 else ('candidate-handle-0000','hotel-handle')), str(i)) for i in range(5)]
    service, request, _, operation, _ = _fixture(hubs(), *query, batch('submit_itinerary',invalid_submit()), ToolTurnCompleted(final_text='未提交',metadata=_metadata()))
    request = request.model_copy(update={'operation_plan': request.operation_plan.model_copy(update={
        'matrix_operation_ids': tuple(OperationId(run_id=request.operation_plan.run_id,phase=WorkflowPhase.ITINERARY_PLANNING,logical_step=f'phase_matrix_{i}') for i in range(5))})})
    result = await service.plan_initial(request)
    assert result.budgets.transport_used == 5 and result.budgets.submit_repair_used == 2
    assert {tool.name for tool in operation.tool_contracts[6]} == {'submit_itinerary'}
    assert result.budgets.model_invocations_consumed == 8


@pytest.mark.asyncio
async def test_last_repair_actual_tools_only_submit_and_five_is_total():
    submits = [batch('submit_itinerary',invalid_submit(shift=i*30),str(i)) for i in range(5)]
    service, request, _, operation, _ = _fixture(*submits)
    result = await service.plan_initial(request)
    assert result.stop_reasons == (PlanningStopReason.ITERATION_LIMIT_REACHED,)
    assert result.budgets.submit_repair_used == 5 and result.budgets.preparation_used == 1
    assert result.budgets.model_invocations_consumed == operation.call_count == 6
    assert {tool.name for tool in operation.tool_contracts[-1]} == {'submit_itinerary'}


@pytest.mark.asyncio
async def test_cosmetic_second_rejection_stops_without_wasting_another_call():
    service, request, _, operation, _ = _fixture(batch('submit_itinerary',invalid_submit(theme='首版')),
        batch('submit_itinerary',invalid_submit(theme='仅改主题')))
    result = await service.plan_initial(request)
    assert result.stop_reasons == (PlanningStopReason.NO_PROGRESS,)
    assert result.budgets.submit_repair_used == 2
    assert operation.call_count == result.budgets.model_invocations_consumed == 3
