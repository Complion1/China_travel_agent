from datetime import date

import pytest

from app.baidu_map import MatrixTravelMode
from app.itinerary_planning.map_execution import DirectionsLegSpec, MapExecutionCache, MapExecutor
from app.workflow_contracts import ExecutionBudgetKey, ExecutionBudgetScope, OperationId, WorkflowPhase
from tests.support.itinerary_planning_fakes import make_budget_manager, FakeMapAdapter
from tests.unit.itinerary_planning.test_map_execution import _binding, _spec
from uuid import uuid4


@pytest.mark.asyncio
async def test_cross_date_matrix_batch_and_exact_direction_replay() -> None:
    run_id = uuid4()
    key = ExecutionBudgetKey(scope=ExecutionBudgetScope.PHASE, name="planning.direction_call", phase=WorkflowPhase.ITINERARY_PLANNING)
    adapter = FakeMapAdapter()
    budget = make_budget_manager(run_id, key, 8)
    executor = MapExecutor(adapter=adapter, budget_manager=budget, context_digest="sha256:" + "3" * 64, cache=MapExecutionCache())
    locations = tuple(_binding(index) for index in range(2))
    matrix = await executor.calculate_route_matrices((
        {"matrix_date": date(2026, 9, 3), "mode": MatrixTravelMode.WALKING, "locations": locations},
        {"matrix_date": date(2026, 9, 4), "mode": MatrixTravelMode.WALKING, "locations": locations},
    ))
    assert [item.matrix_date for item in matrix] == [date(2026, 9, 3), date(2026, 9, 4)]
    assert len(adapter.matrix_calls) == 2
    first = await executor.plan_directions((_spec(run_id, 0),), direction_budget_key=key)
    original = _spec(run_id, 0)
    second = await executor.plan_directions((DirectionsLegSpec(
        leg_id="replay", origin=original.origin, destination=original.destination,
        transport_semantic=original.transport_semantic, route_mode=original.route_mode,
        alternatives_limit=original.alternatives_limit,
        operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="replay"),
    ),), direction_budget_key=key)
    assert first.status == second.status == "completed"
    assert second.cache_hits == 1
    assert (await budget.get_snapshot(key=key)).consumed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state_case", ("intact", "corrupt", "foreign_digest"))
async def test_public_missing_terminal_audits_state_before_action_and_never_resends(state_case: str) -> None:
    from app.llm_runtime import ToolCallsReady, ToolTurnCompleted, ValidatedToolCall
    from app.itinerary_planning.models import GetCandidateEvidenceArgs
    from tests.unit.itinerary_planning.test_service_initial import _fixture, _metadata
    service, request, runtime, operation, repository = _fixture(
        ToolCallsReady(calls=(ValidatedToolCall(call_id="read", tool_name="get_candidate_evidence",
            arguments=GetCandidateEvidenceArgs(candidate_handles=("candidate-handle-0000",))),), metadata=_metadata()),
        ToolTurnCompleted(final_text="结束", metadata=_metadata()),
    )
    await service.plan_initial(request)
    terminal_ref = repository.refs.pop(request.operation_plan.terminal_successor_operation_id)
    repository.contents.pop(terminal_ref.artifact_id)
    state_ref = repository.refs[request.operation_plan.invocation_slots[0].planning_state_operation_id]
    if state_case == "corrupt":
        repository.contents[state_ref.artifact_id]["schema_version"] = "invalid"
    elif state_case == "foreign_digest":
        import hashlib
        repository.contents[state_ref.artifact_id]["request_digest"] = "sha256:" + "f" * 64
        _, encoded = repository._encoded(repository.contents[state_ref.artifact_id])
        state_ref = state_ref.model_copy(update={"content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)})
        repository.refs[state_ref.operation_id] = state_ref
    repository.lookup_calls.clear()
    repository.get_calls.clear()
    writes = len(repository.put_requests)
    result = await service.plan_initial(request)
    assert result.failure.code.value == ("request_mismatch" if state_case == "foreign_digest" else "operation_result_indeterminate")
    assert repository.lookup_calls[:2] == [request.operation_plan.terminal_successor_operation_id, state_ref.operation_id]
    assert repository.get_calls[0] == state_ref
    assert len(repository.put_requests) == writes
    assert runtime.start_count == 1 and operation.call_count == 2
    if state_case == "intact":
        assert repository.lookup_calls[1:20] == [slot.planning_state_operation_id for slot in request.operation_plan.invocation_slots]
        assert repository.lookup_calls[20:39] == [slot.action_operation_id for slot in request.operation_plan.invocation_slots]
