from __future__ import annotations

from uuid import uuid4

import pytest
from tests.unit.candidate_research.test_qualification import legal_judgment
from pydantic import BaseModel

from app.candidate_research import CandidateResearchControlFailureCode
from app.candidate_research.service import CandidateResearchService
from app.candidate_research import JudgmentPatch, MealIntent, NoUsefulActionProposed, ProposedActionBatch, ProposedDetailsAction, ProposedSearchAction, SearchAreaMode, RoleJudgmentUpdate, ResearchRole, RoleVerdict, RoleReasonCode, EvidenceHandle
from app.baidu_map import BaiduMapRateLimitError, BaiduMapSessionError, BaiduMapTimeoutError, BaiduMapTransportError, MapCapability
from app.llm_runtime import LLMErrorCode, LLMInvocationFailure, LLMRuntimeError, ModelRole
from tests.support.candidate_research_fakes import FakeBudgetManager, FakeMapAdapter, FakeModelOperation, FakeRepository, FakeRuntime, details_no_result, search_no_result, search_success, structured
from tests.unit.candidate_research.test_service_initial import _request


def test_control_failure_flags_are_conservative() -> None:
    outcome = CandidateResearchService._zero_failed(CandidateResearchControlFailureCode.OPERATION_RESULT_INDETERMINATE)
    assert outcome.retryable is False
    assert outcome.possible_duplicate is True


def test_persistence_failure_flags_are_conservative() -> None:
    outcome = CandidateResearchService._zero_failed(CandidateResearchControlFailureCode.PERSISTENCE_ERROR)
    assert outcome.retryable is False
    assert outcome.possible_duplicate is True


@pytest.mark.parametrize("copy_interest", (False, True))
@pytest.mark.asyncio
async def test_search_query_wording_does_not_replace_required_objective_interest(copy_interest) -> None:
    from tests.unit.candidate_research.test_service_initial import _action_batch_from_prompt

    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=8)
    operation = FakeModelOperation()

    def action_response(_count):
        batch = _action_batch_from_prompt(operation, 8)
        actions = tuple(
            action.model_copy(update={
                "query": f"杭州{action.interest.value}景点",
                "interest": action.interest if copy_interest else None,
            }) if action.interest is not None else action
            for action in batch.actions
        )
        return structured(batch.model_copy(update={"actions": actions}))

    operation.responses.append(action_response)
    if not copy_interest:
        operation.responses.extend([structured(NoUsefulActionProposed(reason_code="prior_no_progress"))] * 2)
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    maps = FakeMapAdapter(tuple(BaiduMapTimeoutError(capability=MapCapability.PLACE_SEARCH) for _ in range(3)))
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=maps,
        artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)

    if copy_interest:
        assert result.failure.failure_type == "map_failure"
        assert len(maps.search_requests) == 3
    else:
        assert result.outcome_type == "incomplete"
        assert "correction_limit_reached" in {item.value for item in result.stop_reasons}
        assert not maps.search_requests


@pytest.mark.asyncio
async def test_public_judgment_rejection_persists_the_actual_proposed_patch() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    operation = FakeModelOperation()
    def action_response(_count):
        import json
        objective_id = json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"]
        return structured(ProposedActionBatch(actions=(ProposedSearchAction(ordinal=0, objective_id=objective_id, query="museum", area_mode=SearchAreaMode.CITY),)))
    operation.responses.extend((action_response, structured(JudgmentPatch(role_updates=(RoleJudgmentUpdate(candidate_handle="c:9", role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_handles=(EvidenceHandle(handle_id="e:0"),)),), interest_updates=(), meal_updates=(), requirement_group_updates=()))))
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter((search_success(),)), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.failure.code is CandidateResearchControlFailureCode.JUDGMENT_PATCH_INVALID
    judgment_id = request.operation_plan.iteration_slots[0].judgment_operation_id
    payload = repository.contents[repository.refs[judgment_id].artifact_id]
    assert payload["result"]["proposed_patch"]["role_updates"][0]["candidate_handle"] == "c:9"


@pytest.mark.asyncio
async def test_public_unknown_runtime_result_is_dependency_contract_failure() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    operation = FakeModelOperation((RuntimeError("unexpected"),))
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter(), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.failure.code is CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR


@pytest.mark.asyncio
async def test_public_semantic_failure_costs_zero_for_schema_unsupported_input_too_large_and_quota() -> None:
    for code in (LLMErrorCode.SCHEMA_UNSUPPORTED, LLMErrorCode.INPUT_TOO_LARGE, LLMErrorCode.QUOTA_EXHAUSTED):
        run_id = uuid4()
        request, hotel_ref, hotel_artifact, _ = _request(run_id)
        error = LLMRuntimeError(code=code, role=ModelRole.CANDIDATE_RESEARCH, retryable=True, possible_duplicate=True)
        operation = FakeModelOperation((LLMInvocationFailure(error=error),))
        repository = FakeRepository()
        repository.seed(hotel_ref, hotel_artifact)
        service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter(), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
        result = await service.research_initial(request)
        assert result.budgets.semantic_invocations_consumed == 0, result.failure.code


@pytest.mark.asyncio
async def test_public_action_success_plus_judgment_timeout_costs_two_semantic_invocations() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    operation = FakeModelOperation()

    def action_response(_count):
        import json
        objective_id = json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"]
        return structured(ProposedActionBatch(actions=(ProposedSearchAction(ordinal=0, objective_id=objective_id, query="museum", area_mode=SearchAreaMode.CITY),)))

    error = LLMRuntimeError(code=LLMErrorCode.TIMEOUT, role=ModelRole.CANDIDATE_RESEARCH, retryable=True, possible_duplicate=True)
    operation.responses.extend((action_response, LLMInvocationFailure(error=error)))
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter((search_success(),)), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.failure.invocation_kind.value == "judgment"
    assert result.budgets.semantic_invocations_consumed == 2


@pytest.mark.asyncio
async def test_public_search_no_result_records_attempt_and_still_invokes_judgment() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    operation = FakeModelOperation()

    def action_response(_count):
        import json
        objective_id = json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"]
        return structured(ProposedActionBatch(actions=(ProposedSearchAction(ordinal=0, objective_id=objective_id, query="museum", area_mode=SearchAreaMode.CITY),)))

    operation.responses.extend(
        (
            action_response,
            lambda _: legal_judgment(operation),
            structured(NoUsefulActionProposed(reason_code="prior_no_progress")),
            structured(NoUsefulActionProposed(reason_code="prior_no_progress")),
        )
    )
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    map_adapter = FakeMapAdapter((search_no_result(),))
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=map_adapter, artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete"
    assert map_adapter.call_count == 1
    assert operation.call_count == 3
    observations = [item for item in repository.put_requests if item.artifact_type == "candidate_research.place_observation"]
    assert observations and observations[0].content["result"]["record_type"] == "search_no_result"


@pytest.mark.asyncio
async def test_public_unknown_map_result_is_dependency_contract_failure() -> None:
    class UnknownMapResult(BaseModel):
        marker: str = "unknown"

    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    operation = FakeModelOperation()

    def action_response(_count):
        import json
        objective_id = json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"]
        return structured(ProposedActionBatch(actions=(ProposedSearchAction(ordinal=0, objective_id=objective_id, query="museum", area_mode=SearchAreaMode.CITY),)))

    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    operation.responses.append(action_response)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter((UnknownMapResult(),)), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.failure.code is CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR


@pytest.mark.parametrize(
    ("error_type", "retryable", "possible_duplicate"),
    [
        (BaiduMapRateLimitError, True, False),
        (BaiduMapTimeoutError, True, True),
        (BaiduMapTransportError, True, True),
        (BaiduMapSessionError, True, False),
    ],
)
@pytest.mark.asyncio
async def test_public_typed_map_failures_preserve_retry_and_duplicate_mapping(
    error_type, retryable, possible_duplicate,
) -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    operation = FakeModelOperation()

    def action_response(_count):
        import json
        objective_id = json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"]
        return structured(ProposedActionBatch(actions=(ProposedSearchAction(
            ordinal=0, objective_id=objective_id, query="museum", area_mode=SearchAreaMode.CITY,
        ),)))

    operation.responses.append(action_response)
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    map_adapter = FakeMapAdapter((error_type(capability=MapCapability.PLACE_SEARCH),))
    service = CandidateResearchService(
        runtime=FakeRuntime(operation), map_adapter=map_adapter,
        artifact_repository=repository, budget_manager=FakeBudgetManager(run_id),
    )
    result = await service.research_initial(request)
    assert result.failure.failure_type == "map_failure"
    assert result.retryable is retryable
    assert result.possible_duplicate is possible_duplicate


@pytest.mark.asyncio
async def test_public_unknown_repository_lookup_is_dependency_contract_failure() -> None:
    class UnknownRepository(FakeRepository):
        async def lookup(self, *, operation_id):
            return object()

    run_id = uuid4()
    request, _, _, _ = _request(run_id)
    service = CandidateResearchService(runtime=FakeRuntime(), map_adapter=FakeMapAdapter(), artifact_repository=UnknownRepository(), budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.failure.code is CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR


@pytest.mark.asyncio
async def test_public_unknown_budget_snapshot_is_dependency_contract_failure() -> None:
    class UnknownBudget(FakeBudgetManager):
        async def get_snapshot(self, *, key):
            return object()

    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    operation = FakeModelOperation()

    def action_response(_count):
        import json
        objective_id = json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"]
        return structured(ProposedActionBatch(actions=(ProposedSearchAction(ordinal=0, objective_id=objective_id, query="museum", area_mode=SearchAreaMode.CITY),)))

    operation.responses.append(action_response)
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter(), artifact_repository=repository, budget_manager=UnknownBudget(run_id))
    result = await service.research_initial(request)
    assert result.failure.code is CandidateResearchControlFailureCode.DEPENDENCY_CONTRACT_ERROR


@pytest.mark.asyncio
async def test_public_details_no_result_is_persisted_without_retrying_same_uid() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=2)
    operation = FakeModelOperation()

    def initial_actions(_count):
        import json
        objectives = json.loads(operation.prompts[-1].user_text)["objectives"][:2]
        return structured(ProposedActionBatch(actions=(
            ProposedSearchAction(ordinal=0, objective_id=objectives[0]["objective_id"], query="city", area_mode=SearchAreaMode.CITY),
            ProposedSearchAction(ordinal=1, objective_id=objectives[1]["objective_id"], query="nearby", area_mode=SearchAreaMode.NEARBY, anchor=__import__("app.candidate_research", fromlist=["HotelAnchorRef"]).HotelAnchorRef(), radius_km=3),
        )))

    role_updates = tuple(
        RoleJudgmentUpdate(candidate_handle=f"c:{ordinal}", role=role, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS, evidence_handles=(EvidenceHandle(handle_id=f"e:{ordinal}"),))
        for ordinal in (0, 1)
        for role in (ResearchRole.ATTRACTION, ResearchRole.RESTAURANT)
    )

    def details_action(_count):
        import json
        prompt_objectives = json.loads(operation.prompts[-1].user_text)["objectives"]
        details = next(item for item in prompt_objectives if item["kind"] == "details_maturity")
        lunch = next(item for item in prompt_objectives if item["kind"] == "meal_lunch")
        return structured(ProposedActionBatch(actions=(
            ProposedSearchAction(ordinal=0, objective_id=lunch["objective_id"], query="lunch", area_mode=SearchAreaMode.CITY, meal_intent=MealIntent.LUNCH),
            ProposedDetailsAction(ordinal=1, objective_id=details["objective_id"], uid=details["candidate_uids"][0]),
        )))

    operation.responses.extend((initial_actions, lambda _: legal_judgment(operation, JudgmentPatch(role_updates=role_updates, interest_updates=(), meal_updates=(), requirement_group_updates=())), details_action, lambda _: legal_judgment(operation), structured(NoUsefulActionProposed(reason_code="prior_no_progress"))))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel_artifact)
    map_adapter = FakeMapAdapter((search_success("place-1"), search_success("place-2"), search_no_result(), details_no_result()))
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=map_adapter, artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete"
    assert map_adapter.call_count == 4
    assert len([item for item in map_adapter.details_requests if item.uid == "place-1"]) == 1
