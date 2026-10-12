from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.candidate_research.service import CandidateResearchService
from app.candidate_research import CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY, CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY, CandidateResearchControlFailureCode, JudgmentPatch, NoUsefulActionProposed
from tests.support.candidate_research_fakes import FakeBudgetManager, FakeMapAdapter, FakeModelOperation, FakeRepository, FakeRuntime, search_no_result, structured
from tests.unit.candidate_research.test_service_initial import _action_batch_from_prompt, _request
from app.workflow_contracts import BudgetConsumeRequest


def test_empty_service_has_no_flights_or_run_context() -> None:
    class Runtime:
        def start_operation(self, role):
            raise AssertionError

    class Map:
        async def search_places(self, request):
            raise AssertionError

        async def get_place_details(self, request):
            raise AssertionError

    class Repository:
        async def put(self, request):
            raise AssertionError

        async def lookup(self, *, operation_id):
            raise AssertionError

    class Budget:
        async def consume(self, request):
            raise AssertionError

    service = CandidateResearchService(runtime=Runtime(), map_adapter=Map(), artifact_repository=Repository(), budget_manager=Budget())
    assert service._flights == {}
    assert service._runs == {}
    asyncio.run(service.close_run(UUID(int=1)))
    assert CandidateResearchControlFailureCode.INVALID_INPUT.value == "invalid_input"


@pytest.mark.asyncio
async def test_public_matching_partial_replay_reuses_action_place_and_judgment_without_provider_calls() -> None:
    run_id = UUID(int=2)
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    first_operation = FakeModelOperation()
    first_operation.responses.extend((lambda _count: structured(_action_batch_from_prompt(first_operation, 1)), structured(JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())), structured(NoUsefulActionProposed(reason_code="prior_no_progress"))))
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    budget = FakeBudgetManager(run_id)
    maps = FakeMapAdapter((search_no_result(),))
    first = CandidateResearchService(runtime=FakeRuntime(first_operation), map_adapter=maps, artifact_repository=repository, budget_manager=budget)
    first_result = await first.research_initial(request)
    assert first_result.outcome_type == "incomplete"
    consumed_before = dict(budget.consumed)
    repository.refs.pop(request.operation_plan.terminal_successor_operation_id)

    second_operation = FakeModelOperation()
    second = CandidateResearchService(runtime=FakeRuntime(second_operation), map_adapter=maps, artifact_repository=repository, budget_manager=budget)
    second_result = await second.research_initial(request)
    assert second_result.outcome_type == "incomplete"
    assert second_operation.call_count == 0
    assert maps.call_count == 1
    assert budget.consumed == consumed_before


@pytest.mark.asyncio
async def test_public_consumed_action_without_artifact_is_indeterminate_without_provider_or_map() -> None:
    run_id = UUID(int=3)
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    budget = FakeBudgetManager(run_id)
    await budget.consume(BudgetConsumeRequest(operation_id=request.operation_plan.iteration_slots[0].action_operation_id, key=CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY, amount=1))
    operation = FakeModelOperation()
    maps = FakeMapAdapter()
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=maps, artifact_repository=repository, budget_manager=budget)
    result = await service.research_initial(request)
    assert result.failure.code is CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE
    assert operation.call_count == 0
    assert maps.call_count == 0


@pytest.mark.asyncio
async def test_public_consumed_place_without_observation_artifact_is_indeterminate_before_map() -> None:
    run_id = UUID(int=4)
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    budget = FakeBudgetManager(run_id)
    await budget.consume(BudgetConsumeRequest(operation_id=request.operation_plan.iteration_slots[0].place_observation_operation_ids[0], key=CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY, amount=1))
    operation = FakeModelOperation()
    operation.responses.append(lambda _count: structured(_action_batch_from_prompt(operation, 1)))
    maps = FakeMapAdapter()
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=maps, artifact_repository=repository, budget_manager=budget)
    result = await service.research_initial(request)
    assert result.failure.code is CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE
    assert operation.call_count == 1
    assert maps.call_count == 0


@pytest.mark.asyncio
async def test_public_close_waits_for_active_run_and_is_idempotent() -> None:
    run_id = UUID(int=5)
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    operation = FakeModelOperation()
    operation.responses.extend((lambda _count: structured(_action_batch_from_prompt(operation, 1)), structured(NoUsefulActionProposed(reason_code="prior_no_progress"))))
    operation.block = True
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    runtime = FakeRuntime(operation)
    service = CandidateResearchService(runtime=runtime, map_adapter=FakeMapAdapter((search_no_result(),)), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    research_task = asyncio.create_task(service.research_initial(request))
    await operation.entered.wait()
    close_task = asyncio.create_task(service.close_run(run_id))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(close_task), timeout=0.01)
    assert operation.close_count == 0
    operation.release.set()
    await research_task
    await close_task
    await service.close_run(run_id)
    assert operation.close_count == 1
