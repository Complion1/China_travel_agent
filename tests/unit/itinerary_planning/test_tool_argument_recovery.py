"""M09 §26: independent semantic repairs, accounting and original gates."""
import json
from types import SimpleNamespace

import pytest

from app.llm_runtime import DeepSeekRuntime, ToolTurnRejected, LLMErrorCode
from app.llm_runtime.protocols import ProviderResponse
from app.itinerary_planning import ItineraryPlanningFailed, ItineraryPlanningIncomplete
from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper
from tests.unit.itinerary_planning.test_service_initial import _fixture
from tests.unit.llm_runtime.test_tool_argument_recovery import reply, USAGE


def good_hubs():
    return reply(("arrival", "geocode_hub", '{"endpoint_kind":"arrival"}'),
        ("departure", "geocode_hub", '{"endpoint_kind":"departure"}'))


def stop():
    return ProviderResponse("fixture", (), "stop", "deepseek-v4-flash", USAGE, None)


async def run_script(*responses):
    service, request, _, _, repository = _fixture()
    provider = ScriptedProviderAdapter(list(responses))
    service._runtime = DeepSeekRuntime(provider, RecordingSleeper())
    result = await service.plan_initial(request)
    return service, request, provider, repository, result


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_count", [1, 2])
async def test_bad_then_good_executes_once_and_accounts_each_call(invalid_count):
    invalid = [reply((f"bad-{i}", "geocode_hub", '{}' if i==0 else '{"endpoint_kind":"other"}')) for i in range(invalid_count)]
    service, _, provider, repository, result = await run_script(*invalid, good_hubs(), stop())
    assert isinstance(result, ItineraryPlanningIncomplete)
    assert result.budgets.format_repair_used == invalid_count
    assert result.budgets.preparation_used == 1 and result.budgets.transport_used == 1
    assert result.budgets.model_invocations_consumed == provider.call_count == invalid_count + 2
    assert result.budgets.known_provider_attempts == provider.call_count
    assert result.budgets.known_usage.total_tokens == 15 * provider.call_count
    assert result.budgets.usage_complete and result.budgets.provider_attempts_complete
    assert len(service._map_adapter.geocode_calls) == 2
    actions = [r for r in repository.put_requests if r.artifact_type=="itinerary_planning.model_action"]
    assert [r.content["budget_category"] for r in actions] == ["preparation"] + ["format_repair"]*invalid_count + ["transport"]
    for action in actions[:invalid_count]:
        assert action.content["action_kind"] == "llm_failure"
        assert not action.content["call_summaries"] and action.content["invocation_metadata"] is None
    assert all(r.schema_version=="6.0" for r in actions)


@pytest.mark.asyncio
async def test_three_errors_stop_even_when_ids_fields_and_rules_change():
    service, _, provider, _, result = await run_script(
        reply(("one", "geocode_hub", '{}')),
        reply(("two", "geocode_hub", '{"endpoint_kind":3}')),
        reply(("three", "get_candidate_evidence", '{"unknown":"PRIVATE_INPUT"}')),
        good_hubs())
    assert isinstance(result, ItineraryPlanningFailed) and result.failure.error.code is LLMErrorCode.TOOL_ARGUMENTS_INVALID
    assert not result.retryable and provider.call_count == 3
    assert result.budgets.preparation_used == 1 and result.budgets.format_repair_used == 2
    assert result.budgets.model_invocations_consumed == 3 and result.budgets.known_provider_attempts == 3
    assert not service._map_adapter.geocode_calls
    assert "PRIVATE_INPUT" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_success_clears_episode_but_does_not_replenish_run_reserve():
    service, _, provider, _, result = await run_script(
        reply(("a", "geocode_hub", '{}')), reply(("b", "geocode_hub", '{')),
        good_hubs(), reply(("new-episode", "plan_directions", '{}')), good_hubs())
    assert isinstance(result, ItineraryPlanningFailed) and provider.call_count == 4
    assert result.budgets.preparation_used == 1 and result.budgets.transport_used == 1
    assert result.budgets.format_repair_used == 2 and result.budgets.model_invocations_consumed == 4
    assert len(service._map_adapter.geocode_calls) == 2


@pytest.mark.asyncio
async def test_mixed_valid_and_invalid_batch_has_zero_execution_before_repair():
    service, _, provider, _, result = await run_script(
        reply(("valid", "geocode_hub", '{"endpoint_kind":"arrival"}'), ("bad", "geocode_hub", '{}')),
        good_hubs(), stop())
    assert len(service._map_adapter.geocode_calls) == 2
    original = provider.tool_continuations[0].messages
    feedback = [m for m in original if m["role"]=="tool"]
    assert [m["tool_call_id"] for m in feedback] == ["valid", "bad"]
    assert all(json.loads(m["content"])["status"]=="not_executed" for m in feedback)
    assert result.budgets.model_invocations_consumed == 3


def test_pending_at_normal_stage_limit_keeps_exact_tools_and_allows_reserve():
    service, *_ = _fixture()
    run = SimpleNamespace(current_stage="submit_repair", preparation_used=2, transport_used=5,
        submit_repair_used=5, supplement_request_used=5, invocation_count=17, format_repair_used=0,
        pending_argument_rejection=object(), pending_argument_tools=("previous-exact-contract",))
    assert service._advance_stage(run) is None
    assert service._effective_tools(run) == run.pending_argument_tools
    run.pending_argument_rejection = None
    assert service._advance_stage(run).value == "iteration_limit_reached"


@pytest.mark.asyncio
@pytest.mark.parametrize("already_exhausted", [False, True])
async def test_public_controller_normal_limit_and_last_call_repair(already_exhausted):
    from app.hotel_selection import HotelSelectionArtifact
    from app.candidate_research import CandidateResearchArtifact
    from app.workflow_contracts import BudgetConsumeRequest
    from app.itinerary_planning.models import ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY
    from tests.unit.itinerary_planning.test_phase_budget import invalid_submit
    service, request, _, _, repository = _fixture()
    provider = ScriptedProviderAdapter([reply(("last-normal", "submit_itinerary", '{}')),
        reply(("fixed", "submit_itinerary", invalid_submit().model_dump_json()))])
    service._runtime = DeepSeekRuntime(provider, RecordingSleeper())
    hotel = (await repository.get(request.hotel_selection_ref, expected_model=HotelSelectionArtifact)).content
    candidate = (await repository.get(request.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    candidates, refs, groups = service._candidate_snapshot(candidate, confirmed=request.confirmed)
    run = service._make_run(request_kind="initial", request_digest="sha256:"+"a"*64, confirmed=request.confirmed,
        request=request, hotel=hotel, candidates=candidates, evidence_refs=refs, requirement_group_states=groups)
    # A proven prior prefix is seeded into the same ledger and operation.
    prefix = 17 if already_exhausted else 16
    for slot in request.operation_plan.invocation_slots[:prefix]:
        await service._budget.consume(BudgetConsumeRequest(operation_id=slot.action_operation_id,
            key=ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY, amount=1))
    run.current_stage = "submit_repair"
    run.preparation_used, run.transport_used, run.supplement_request_used = 2, 5, 5
    run.submit_repair_used = prefix - 12
    run.invocation_count = run.provider_attempts = prefix
    run.operation._semantic_invocations_used = prefix
    result = await service._drive(run, first=True)
    assert isinstance(result, ItineraryPlanningIncomplete)
    assert result.budgets.model_invocations_consumed == (17 if already_exhausted else 18)
    assert result.budgets.format_repair_used == (0 if already_exhausted else 1)
    assert provider.call_count == (0 if already_exhausted else 2)
    assert result.budgets.budget_snapshot_complete
    if not already_exhausted:
        assert {t["function"]["name"] for t in provider.tool_continuations[0].tools} == {"submit_itinerary"}
    run.operation.close()


@pytest.mark.asyncio
async def test_format_success_still_reaches_original_business_gate():
    from tests.unit.itinerary_planning.test_phase_budget import invalid_submit
    draft = invalid_submit().model_dump(mode="json")
    del draft["draft"]["arrival_transfer"]["transfer_kind"]
    del draft["draft"]["departure_transfer"]["transfer_kind"]
    valid_schema = invalid_submit().model_dump_json()
    service, _, provider, _, result = await run_script(good_hubs(),
        reply(("missing-kinds", "submit_itinerary", json.dumps(draft))),
        reply(("fixed-kinds", "submit_itinerary", valid_schema)), stop())
    assert isinstance(result, ItineraryPlanningIncomplete)
    assert result.budgets.format_repair_used == 1 and provider.call_count == 4
    assert result.outcome_type != "completed"
    assert len(service._map_adapter.geocode_calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("before_supplement", [1, 2])
async def test_resume_shares_format_reserve_and_never_restarts_operation(before_supplement):
    from app.candidate_research import CandidateResearchArtifact
    from app.itinerary_planning import ItineraryPlanningCandidateSupplementRequested, ItineraryPlanningResumeRequest
    from tests.unit.itinerary_planning.test_service_initial import _plan
    from tests.unit.itinerary_planning.test_service_resume import (
        _supplement_call, _seed_supplemented_candidate, _supplemental_plan,
    )
    invalid = [reply((f"bad-evidence-{i}", "get_candidate_evidence", '{}')) for i in range(before_supplement)]
    evidence = reply(("evidence", "get_candidate_evidence", '{"candidate_handles":["candidate-handle-0000"]}'))
    supplement = _supplement_call()
    service, request, provider, repository, awaiting = await run_script(
        *invalid, evidence, reply((supplement.call_id, supplement.tool_name, supplement.arguments.model_dump_json())),
        reply(("after-resume", "geocode_hub", '{}')), good_hubs(), stop())
    assert isinstance(awaiting, ItineraryPlanningCandidateSupplementRequested)
    assert awaiting.budgets.format_repair_used == before_supplement
    operation_before = service._runs[request.operation_plan.run_id].operation
    candidate = (await repository.get(request.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    supplied = _seed_supplemented_candidate(repository, candidate, request.operation_plan.run_id, request.candidate_set_ref, awaiting)
    resume = ItineraryPlanningResumeRequest(predecessor_planning_ref=awaiting.planning_ref,
        supplemented_candidate_set_ref=supplied, supplemental_operation_plan=_supplemental_plan(request.operation_plan.run_id),
        operation_plan=_plan(request.operation_plan.run_id, suffix="format_resume",
            slots=tuple(range(awaiting.budgets.model_invocations_consumed, 19))))
    result = await service.plan_resume(resume)
    calls_before_replay = provider.call_count
    assert await service.plan_resume(resume) == result
    assert provider.call_count == calls_before_replay
    assert len(provider.tool_requests) == 1  # The original private operation survives the pause.
    assert result.budgets.format_repair_used == 2 and result.budgets.supplement_request_used == 1
    assert result.budgets.semantic_invocations_consumed == operation_before._semantic_invocations_used
    assert result.budgets.model_invocations_consumed == provider.call_count
    assert type(result) is (ItineraryPlanningIncomplete if before_supplement == 1 else ItineraryPlanningFailed)
    actions = [r.content for r in repository.put_requests if r.artifact_type == "itinerary_planning.model_action"]
    assert sum(a["budget_category"] == "format_repair" for a in actions) == 2
    assert len(service._map_adapter.geocode_calls) == (2 if before_supplement == 1 else 0)


@pytest.mark.asyncio
async def test_network_retry_during_format_repair_consumes_one_semantic_slot():
    from app.llm_runtime.protocols import ProviderFailure
    _, _, provider, _, result = await run_script(reply(("bad", "geocode_hub", '{}')),
        ProviderFailure("rate_limited", True, 429, 0), good_hubs(), stop())
    assert isinstance(result, ItineraryPlanningIncomplete)
    assert result.budgets.format_repair_used == 1 and result.budgets.model_invocations_consumed == 3
    assert provider.call_count == result.budgets.known_provider_attempts == 4
    assert result.budgets.provider_attempts_complete and result.budgets.usage_complete
    assert result.budgets.known_usage.total_tokens == 45  # Only the three verified responses carry usage.
