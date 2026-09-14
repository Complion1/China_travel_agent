from __future__ import annotations

import inspect
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.candidate_research import (
    CandidateResearchCompleted,
    CandidateResearchInitialRequest,
    CandidateResearchOperationPlan,
    CandidateResearchService,
    EvidenceHandle,
    ProposedActionBatch,
    ProposedSearchAction,
    ProposedDetailsAction,
    JudgmentPatch,
    InterestJudgmentUpdate,
    InterestReasonCode,
    MealJudgmentUpdate,
    MealReasonCode,
    MatchVerdict,
    RoleJudgmentUpdate,
    RoleReasonCode,
    RoleVerdict,
    ResearchRole,
    ResearchObjectiveKind,
    RequirementGroupJudgmentUpdate,
    RequirementReasonCode,
    RequirementVerdictException,
    NoUsefulActionProposed,
    HotelAnchorRef,
    MealIntent,
    ResearchIterationOperationIds,
    SearchAreaMode,
)
from app.candidate_research.errors import CandidateResearchConfigurationError
from app.candidate_research.artifacts import (
    ArtifactCandidateCoreSnapshot,
    ArtifactCoordinateSnapshot,
    CandidateSetStateSnapshot,
    SearchCoverageRecord,
    SearchCoverageState,
)
from app.candidate_research.service import _filter_search_coverage
from app.requirements import ConfirmedTravelRequirement, FieldMetadataRegistry, InterestTag, TravelPreferences, TravelRequirement, TravelTimeWindow
from app.workflow_contracts import OperationId, WorkflowPhase
from app.baidu_map import BaiduCoordinate, BaiduMapTimeoutError, MapCapability, PlaceDetails, PlaceDetailsSuccess, PlaceSearchSuccess, PlaceSummary

from tests.support.candidate_research_fakes import FakeBudgetManager, FakeMapAdapter, FakeModelOperation, FakeRepository, FakeRuntime, hotel_selection, map_provenance, search_success, structured


class _Runtime:
    def start_operation(self, role):
        raise AssertionError("the runtime must not be touched by constructor validation")


class _Map:
    async def search_places(self, request):
        raise AssertionError

    async def get_place_details(self, request):
        raise AssertionError


class _Repository:
    async def put(self, request):
        raise AssertionError

    async def lookup(self, *, operation_id):
        raise AssertionError


class _Budget:
    async def consume(self, request):
        raise AssertionError


def test_constructor_is_keyword_only_and_public() -> None:
    signature = inspect.signature(CandidateResearchService)
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in signature.parameters.values())
    service = CandidateResearchService(runtime=_Runtime(), map_adapter=_Map(), artifact_repository=_Repository(), budget_manager=_Budget())
    assert isinstance(service, CandidateResearchService)


def test_constructor_rejects_missing_public_ports() -> None:
    with pytest.raises(CandidateResearchConfigurationError):
        CandidateResearchService(runtime=object(), map_adapter=_Map(), artifact_repository=_Repository(), budget_manager=_Budget())


def test_search_coverage_drops_candidates_that_later_cease_to_be_eligible() -> None:
    search = SearchCoverageState(
        records=(
            SearchCoverageRecord(
                objective_id="objective:history",
                objective_kind=ResearchObjectiveKind.INITIAL_CITY_ATTRACTION,
                action_fingerprint="sha256:" + "a" * 64,
                observation_index=0,
                role=ResearchRole.ATTRACTION,
                interest=None,
                meal_intent=None,
                eligible_candidate_ordinals=(0, 1, 2),
            ),
        ),
        initial_city_attraction_search_done=True,
        initial_hotel_restaurant_nearby_done=False,
    )

    filtered = _filter_search_coverage(search, (0, 2))

    assert filtered.records[0].eligible_candidate_ordinals == (0, 2)
    assert search.records[0].eligible_candidate_ordinals == (0, 1, 2)


def test_core_comparison_accepts_baidu_search_details_coordinate_precision_only() -> None:
    search = ArtifactCandidateCoreSnapshot(
        uid="uid-1",
        name="工联美食广场",
        address="延安路292号",
        coordinate=ArtifactCoordinateSnapshot(
            coordinate_system="bd09ll",
            latitude=30.26103757730365,
            longitude=120.17104720809715,
        ),
        provider_category="美食;其他",
    )
    details = ArtifactCandidateCoreSnapshot(
        **{
            **search.model_dump(mode="python"),
            "coordinate": {
                "coordinate_system": "bd09ll",
                "latitude": 30.261038,
                "longitude": 120.171047,
            },
        }
    )
    moved = ArtifactCandidateCoreSnapshot(
        **{
            **search.model_dump(mode="python"),
            "coordinate": {
                "coordinate_system": "bd09ll",
                "latitude": 30.261138,
                "longitude": 120.171047,
            },
        }
    )

    assert CandidateResearchService._core_equal(search, details)
    assert not CandidateResearchService._core_equal(search, moved)


def _request(run_id, *, place_width: int = 1, slot_count: int = 6):
    def op(name):
        return OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=name)
    slots = tuple(
        ResearchIterationOperationIds(
            invocation_slot_index=index, action_operation_id=op(f"action_{index}"), judgment_operation_id=op(f"judgment_{index}"),
            candidate_set_operation_id=op(f"candidate_{index}"),
            place_observation_operation_ids=tuple(op(f"observation_{index}_{ordinal}") for ordinal in range(place_width)),
        )
        for index in range(slot_count)
    )
    plan = CandidateResearchOperationPlan(run_id=run_id, iteration_slots=slots, terminal_successor_operation_id=op("terminal"))
    confirmed = ConfirmedTravelRequirement(
        schema_version="1.0",
        requirement=TravelRequirement(
            destination="杭州",
            time_window=TravelTimeWindow(sightseeing_days=1),
            preferences=TravelPreferences(interests=[InterestTag.CULTURE, InterestTag.NATURE]),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(), confirmation_summary_digest="confirmed",
    )
    hotel_ref, hotel_artifact = hotel_selection(run_id)
    return CandidateResearchInitialRequest(confirmed=confirmed, hotel_selection_ref=hotel_ref, operation_plan=plan), hotel_ref, hotel_artifact, slots[0]


def _action_batch_from_prompt(operation: FakeModelOperation, count: int) -> ProposedActionBatch:
    objectives = json.loads(operation.prompts[-1].user_text)["objectives"][:count]
    actions = []
    for ordinal, objective in enumerate(objectives):
        kind = objective["kind"]
        if kind == "initial_hotel_restaurant_nearby":
            actions.append(ProposedSearchAction(ordinal=ordinal, objective_id=objective["objective_id"], query=f"q-{ordinal}", area_mode=SearchAreaMode.NEARBY, anchor=HotelAnchorRef(), radius_km=3))
            continue
        interest = InterestTag(objective["interest"]) if objective.get("interest") is not None else None
        meal = MealIntent(objective["meal_intent"]) if objective.get("meal_intent") is not None else None
        actions.append(ProposedSearchAction(ordinal=ordinal, objective_id=objective["objective_id"], query=f"q-{ordinal}", area_mode=SearchAreaMode.CITY, interest=interest, meal_intent=meal))
    return ProposedActionBatch(actions=tuple(actions))


def _first_objective_action(operation: FakeModelOperation, *, ordinal: int = 0, query: str = "museum") -> ProposedSearchAction | ProposedDetailsAction:
    objective = json.loads(operation.prompts[-1].user_text)["objectives"][0]
    kind = objective["kind"]
    if kind == "details_maturity" and objective.get("candidate_uids"):
        return ProposedDetailsAction(ordinal=ordinal, objective_id=objective["objective_id"], uid=objective["candidate_uids"][0])
    if kind == "initial_hotel_restaurant_nearby":
        return ProposedSearchAction(ordinal=ordinal, objective_id=objective["objective_id"], query=query, area_mode=SearchAreaMode.NEARBY, anchor=HotelAnchorRef(), radius_km=3)
    return ProposedSearchAction(
        ordinal=ordinal,
        objective_id=objective["objective_id"],
        query=query,
        area_mode=SearchAreaMode.CITY,
        interest=InterestTag(objective["interest"]) if objective.get("interest") else None,
        meal_intent=MealIntent(objective["meal_intent"]) if objective.get("meal_intent") else None,
    )


@pytest.mark.asyncio
async def test_public_initial_rejects_invalid_action_and_persists_actual_batch() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id)
    operation = FakeModelOperation(responses=(structured(ProposedActionBatch(actions=(ProposedSearchAction(ordinal=0, objective_id="objective:not-current", query="museum", area_mode=SearchAreaMode.CITY),))),))
    runtime = FakeRuntime(operation)
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    service = CandidateResearchService(runtime=runtime, map_adapter=FakeMapAdapter(), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert operation.schemas[0].model_json_schema()["type"] == "object"
    assert set(operation.schemas[0].model_json_schema()["properties"]) == {"output"}
    assert result.failure.code.value == "action_batch_invalid"
    assert len(repository.put_requests) == 2
    action_payload = repository.contents[repository.refs[request.operation_plan.iteration_slots[0].action_operation_id].artifact_id]
    assert action_payload["result"]["output"]["actions"][0]["query"] == "museum"


@pytest.mark.asyncio
async def test_public_initial_stops_after_first_failed_wave_without_fourth_map_call() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=4)
    operation = FakeModelOperation()
    operation.responses.append(lambda _count: structured(_action_batch_from_prompt(operation, 4)))
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    map_adapter = FakeMapAdapter(
        (
            lambda call: BaiduMapTimeoutError(capability=MapCapability.PLACE_SEARCH) if call == 1 else search_success(f"place-{call}"),
            lambda call: search_success(f"place-{call}"),
            lambda call: search_success(f"place-{call}"),
        )
    )
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=map_adapter, artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.failure.failure_type == "map_failure"
    assert result.failure.code.value == "timeout_error"
    assert map_adapter.call_count == 3
    assert len(map_adapter.search_requests) + len(map_adapter.details_requests) == 3


@pytest.mark.asyncio
async def test_public_initial_executes_eight_actions_as_non_overlapping_three_three_two_waves() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=8)
    operation = FakeModelOperation()
    operation.responses.extend(
        (
            lambda _count: structured(_action_batch_from_prompt(operation, 8)),
            structured(JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())),
            structured(NoUsefulActionProposed(reason_code="prior_no_progress")),
            structured(NoUsefulActionProposed(reason_code="prior_no_progress")),
        )
    )
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    map_adapter = FakeMapAdapter(tuple(lambda call: search_success(f"place-{call}") for _ in range(8)))
    map_adapter.block = True
    map_adapter.release_after = 3
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=map_adapter, artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete"
    assert map_adapter.call_count == 8
    assert map_adapter.max_active_calls == 3
    assert len([item for item in repository.put_requests if item.artifact_type == "candidate_research.place_observation"]) == 8
    assert operation.call_count == 3


def _multi_place_search(uids: tuple[str, ...], *, category: str) -> PlaceSearchSuccess:
    return PlaceSearchSuccess(
        places=tuple(
            PlaceSummary(
                uid=uid,
                name=uid,
                address=f"{uid} Road",
                coordinate=BaiduCoordinate(
                    latitude=30.1 + (int(uid[1:]) if uid[1:].isdigit() else index) / 1000,
                    longitude=120.1 + (int(uid[1:]) if uid[1:].isdigit() else index) / 1000,
                ),
                category=category,
            )
            for index, uid in enumerate(uids)
        ),
        provenance=map_provenance(MapCapability.PLACE_SEARCH),
    )


def _matching_details(uid: str, *, category: str = "museum") -> PlaceDetailsSuccess:
    ordinal = int(uid[1:]) if uid[1:].isdigit() else 0
    return PlaceDetailsSuccess(
        details=PlaceDetails(
            uid=uid,
            name=uid,
            address=f"{uid} Road",
            coordinate=BaiduCoordinate(latitude=30.1 + ordinal / 1000, longitude=120.1 + ordinal / 1000),
            category=category,
            brand=None,
            telephone=None,
            rating=4.0,
            business_hours=("10:00-20:00",),
        ),
        provenance=map_provenance(MapCapability.PLACE_DETAILS),
    )


@pytest.mark.parametrize("extra_irrelevant_conflict", (False, True))
@pytest.mark.asyncio
async def test_public_initial_completes_full_candidate_loop_and_replays_terminal_without_providers(
    extra_irrelevant_conflict: bool,
) -> None:
    """Drive every initial objective once, then mature the derived eligible set."""
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=8)
    operation = FakeModelOperation()

    def initial_actions(_count: int):
        payload = json.loads(operation.prompts[-1].user_text)
        objectives = payload["objectives"][:8]
        actions = []
        for ordinal, objective in enumerate(objectives):
            if objective["kind"] == "initial_hotel_restaurant_nearby":
                actions.append(ProposedSearchAction(
                    ordinal=ordinal,
                    objective_id=objective["objective_id"],
                    query="nearby restaurant",
                    area_mode=SearchAreaMode.NEARBY,
                    anchor=HotelAnchorRef(),
                    radius_km=3,
                ))
            else:
                actions.append(ProposedSearchAction(
                    ordinal=ordinal,
                    objective_id=objective["objective_id"],
                    query=f"{objective['kind']}-{objective.get('interest') or objective.get('meal_intent') or ordinal}",
                    area_mode=SearchAreaMode.CITY,
                    interest=InterestTag(objective["interest"]) if objective.get("interest") else None,
                    meal_intent=MealIntent(objective["meal_intent"]) if objective.get("meal_intent") else None,
                ))
        return structured(ProposedActionBatch(actions=tuple(actions)))

    patch = JudgmentPatch(
        role_updates=tuple(
            RoleJudgmentUpdate(
                candidate_handle=f"c:{index}",
                role=ResearchRole.ATTRACTION if index < 6 else ResearchRole.RESTAURANT,
                verdict=RoleVerdict.SUPPORTED,
                reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS,
                evidence_handles=(EvidenceHandle(handle_id=f"e:{0 if index < 6 else 1}"),),
            )
            for index in range(10)
        ),
        interest_updates=(
            InterestJudgmentUpdate(candidate_handle="c:0", interest=InterestTag.CULTURE, verdict=MatchVerdict.CONFIRMED, reason_code=InterestReasonCode.TARGETED_SEARCH, evidence_handles=(EvidenceHandle(handle_id="e:0"),)),
            InterestJudgmentUpdate(candidate_handle="c:1", interest=InterestTag.NATURE, verdict=MatchVerdict.CONFIRMED, reason_code=InterestReasonCode.TARGETED_SEARCH, evidence_handles=(EvidenceHandle(handle_id="e:0"),)),
        ),
        meal_updates=(
            MealJudgmentUpdate(candidate_handle="c:6", meal_intent=MealIntent.LUNCH, verdict=MatchVerdict.CONFIRMED, reason_code=MealReasonCode.TARGETED_MEAL_SEARCH, evidence_handles=(EvidenceHandle(handle_id="e:1"),)),
            MealJudgmentUpdate(candidate_handle="c:7", meal_intent=MealIntent.DINNER, verdict=MatchVerdict.CONFIRMED, reason_code=MealReasonCode.TARGETED_MEAL_SEARCH, evidence_handles=(EvidenceHandle(handle_id="e:1"),)),
        ),
        requirement_group_updates=(),
    )

    def details_actions(_count: int):
        payload = json.loads(operation.prompts[-1].user_text)
        objective = next(item for item in payload["objectives"] if item["kind"] == "details_maturity")
        return structured(ProposedActionBatch(actions=tuple(
            ProposedDetailsAction(ordinal=index, objective_id=objective["objective_id"], uid=uid)
            for index, uid in enumerate(objective["candidate_uids"][:4])
        )))

    operation.responses.extend((initial_actions, structured(patch), details_actions, structured(JudgmentPatch(
        role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=(),
    ))))
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    extra_candidate_search = _multi_place_search(("a0", "x0"), category="museum")
    conflicting_extra_search = _multi_place_search(("x0",), category="museum")
    conflicting_extra_search = conflicting_extra_search.model_copy(
        update={
            "places": (
                conflicting_extra_search.places[0].model_copy(
                    update={"address": "x0 Conflicting Road"}
                ),
            )
        }
    )
    map_adapter = FakeMapAdapter((
        _multi_place_search(("a0", "a1", "a2", "a3", "a4", "a5"), category="museum"),
        _multi_place_search(("r0", "r1", "r2", "r3"), category="restaurant"),
        _multi_place_search(("r0",), category="restaurant"),
        _multi_place_search(("r1",), category="restaurant"),
        extra_candidate_search if extra_irrelevant_conflict else _multi_place_search(("a0",), category="museum"),
        _multi_place_search(("a1",), category="museum"),
        conflicting_extra_search if extra_irrelevant_conflict else _multi_place_search(("a2",), category="museum"),
        _multi_place_search(("r2",), category="restaurant"),
        _matching_details("a0"), _matching_details("a1"), _matching_details("a2"), _matching_details("a3"),
    ))
    budget = FakeBudgetManager(run_id)
    runtime = FakeRuntime(operation)
    service = CandidateResearchService(runtime=runtime, map_adapter=map_adapter, artifact_repository=repository, budget_manager=budget)

    result = await service.research_initial(request)

    assert isinstance(result, CandidateResearchCompleted), result.model_dump(mode="json")
    judgment_context = json.loads(operation.prompts[1].user_text)
    assert judgment_context["current_evidence_handles"] == [
        item["handle"] for item in judgment_context["evidence"]
    ]
    assert judgment_context["evidence"][0]["accepted_search"]["role"] == "attraction"
    assert any(
        item["accepted_search"] is not None
        and item["accepted_search"]["interest"] == "人文"
        for item in judgment_context["evidence"]
    )
    assert judgment_context["candidates"][0]["evidence_handles"]
    second_judgment_context = json.loads(operation.prompts[3].user_text)
    assert second_judgment_context["current_evidence_handles"] == [
        item["handle"] for item in second_judgment_context["evidence"]
    ]
    assert second_judgment_context["current_evidence_handles"] == [
        "e:8", "e:9", "e:10", "e:11"
    ]
    assert not set(second_judgment_context["current_evidence_handles"]) & set(
        judgment_context["current_evidence_handles"]
    )
    assert result.counts.attraction_count == 6
    assert result.counts.restaurant_count == 4
    assert result.counts.unique_eligible_count == 10
    assert result.counts.admitted_slots == 10 + int(extra_irrelevant_conflict)
    assert result.coverage.covered_interests == (InterestTag.CULTURE, InterestTag.NATURE)
    assert result.coverage.covered_meals == (MealIntent.LUNCH, MealIntent.DINNER)
    assert result.coverage.resolved_must_item_count == result.coverage.must_item_count == 0
    assert result.coverage.details_success_count == result.coverage.details_target == 4
    assert all(
        gap.gap_type != "candidate_details_missing"
        for gap in service._gaps(service._runs[run_id], request)
    )
    assert map_adapter.call_count == 12
    assert operation.call_count == 4
    assert {item.artifact_type for item in repository.put_requests} >= {
        "candidate_research.model_action",
        "candidate_research.model_judgment",
        "candidate_research.place_observation",
        "candidate_research.candidate_set",
    }
    assert all(item.operation_id.run_id == run_id for item in repository.put_requests)
    terminal_payload = repository.contents[result.candidate_set_ref.artifact_id]
    assert terminal_payload["record"]["record_type"] == "completed"
    assert sum(
        candidate["state"] == "conflicted"
        for candidate in terminal_payload["record"]["state"]["candidates"]
    ) == int(extra_irrelevant_conflict)

    provider_calls = (operation.call_count, map_adapter.call_count, budget.consume_count, len(repository.put_requests))
    replay = await service.research_initial(request)
    assert replay == result
    assert (operation.call_count, map_adapter.call_count, budget.consume_count, len(repository.put_requests)) == provider_calls
    await service.close_run(run_id)
    replay_after_close = await service.research_initial(request)
    assert replay_after_close == result
    assert (operation.call_count, map_adapter.call_count, budget.consume_count, len(repository.put_requests)) == provider_calls


@pytest.mark.parametrize("mutation", ("unknown_objective", "capacity", "wrong_area", "extra_interest"))
@pytest.mark.asyncio
async def test_public_initial_action_validation_rejects_each_structural_shape(mutation: str) -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=1)
    operation = FakeModelOperation()

    def response(_count: int):
        action = _first_objective_action(operation)
        if mutation == "unknown_objective":
            action = action.model_copy(update={"objective_id": "objective:not-current"})
        elif mutation == "capacity":
            action = ProposedActionBatch(actions=(action, action.model_copy(update={"ordinal": 1})))
            return structured(action)
        elif mutation == "wrong_area":
            action = action.model_copy(update={"area_mode": SearchAreaMode.NEARBY, "anchor": HotelAnchorRef(), "radius_km": 3}) if action.area_mode is SearchAreaMode.CITY else action.model_copy(update={"area_mode": SearchAreaMode.CITY, "anchor": None, "radius_km": None})
        elif mutation == "extra_interest":
            action = action.model_copy(update={"interest": InterestTag.CULTURE})
        return structured(ProposedActionBatch(actions=(action,)))

    operation.responses.append(response)
    repository = FakeRepository(); repository.seed(hotel_ref, hotel_artifact)
    maps = FakeMapAdapter()
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=maps, artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.failure.code.value == "action_batch_invalid"
    assert maps.call_count == 0


@pytest.mark.asyncio
async def test_public_initial_rejects_same_batch_duplicate_before_map_calls() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=2)
    operation = FakeModelOperation()

    def response(_count: int):
        first = _first_objective_action(operation)
        second = first.model_copy(update={"ordinal": 1})
        return structured(ProposedActionBatch(actions=(first, second)))

    operation.responses.append(response)
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    maps = FakeMapAdapter()
    service = CandidateResearchService(
        runtime=FakeRuntime(operation),
        map_adapter=maps,
        artifact_repository=repository,
        budget_manager=FakeBudgetManager(run_id),
    )

    result = await service.research_initial(request)

    assert result.failure.code.value == "action_batch_invalid"
    assert maps.call_count == 0


@pytest.mark.asyncio
async def test_public_initial_policy_rejection_persists_and_progresses_to_terminal() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=1)
    operation = FakeModelOperation()

    def ignored_action(_count: int):
        payload = json.loads(operation.prompts[-1].user_text)
        objective = payload["objectives"][1]
        if objective["kind"] == "initial_hotel_restaurant_nearby":
            action = ProposedSearchAction(ordinal=0, objective_id=objective["objective_id"], query="nearby", area_mode=SearchAreaMode.NEARBY, anchor=HotelAnchorRef(), radius_km=3)
        else:
            action = ProposedSearchAction(ordinal=0, objective_id=objective["objective_id"], query="ignored", area_mode=SearchAreaMode.CITY)
        return structured(ProposedActionBatch(actions=(action,)))

    operation.responses.extend((ignored_action, structured(NoUsefulActionProposed(reason_code="prior_no_progress"))))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel_artifact)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter(), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete"
    action_records = [item.content["result"] for item in repository.put_requests if item.artifact_type == "candidate_research.model_action"]
    assert action_records and action_records[0]["record_type"] == "policy_rejected"


@pytest.mark.asyncio
async def test_public_initial_unverified_map_result_is_terminal_control_failure() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=1)
    operation = FakeModelOperation()
    operation.responses.append(lambda _count: structured(ProposedActionBatch(actions=(_first_objective_action(operation),))))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel_artifact)
    maps = FakeMapAdapter((search_success("unverified", verified=False),))
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=maps, artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.failure.code.value == "unverified_result"
    assert result.failure_artifact_ref is not None
    assert maps.call_count == 1


@pytest.mark.parametrize("reason", ("prior_no_progress", "insufficient_remaining_budget"))
@pytest.mark.asyncio
async def test_public_initial_no_useful_action_is_checked_against_public_policy(reason: str) -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=1)
    responses = [structured(NoUsefulActionProposed(reason_code=reason))]
    if reason == "insufficient_remaining_budget":
        responses.append(structured(NoUsefulActionProposed(reason_code="prior_no_progress")))
    operation = FakeModelOperation(responses)
    repository = FakeRepository(); repository.seed(hotel_ref, hotel_artifact)
    budget = FakeBudgetManager(run_id, place_limit=0 if reason == "insufficient_remaining_budget" else 48)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter(), artifact_repository=repository, budget_manager=budget)
    result = await service.research_initial(request)
    if reason == "prior_no_progress":
        assert result.failure.code.value == "action_batch_invalid"
    else:
        assert result.outcome_type == "incomplete"


@pytest.mark.parametrize("budget_kind", ("iteration", "place"))
@pytest.mark.asyncio
async def test_public_initial_budget_exhaustion_stays_fail_closed(budget_kind: str) -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=1)
    operation = FakeModelOperation()
    operation.responses.append(lambda _count: structured(ProposedActionBatch(actions=(_first_objective_action(operation),))))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel_artifact)
    budget = FakeBudgetManager(run_id, iteration_limit=0 if budget_kind == "iteration" else 6, place_limit=0 if budget_kind == "place" else 48)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter(), artifact_repository=repository, budget_manager=budget)
    result = await service.research_initial(request)
    assert result.outcome_type in {"incomplete", "failed"}
    assert result.budgets.iterations_consumed == (0 if budget_kind == "iteration" else 1)


@pytest.mark.parametrize(("requirement_kind", "expected_state"), (("must_visit", "eligible"), ("avoid", "excluded")))
@pytest.mark.asyncio
async def test_public_initial_requirement_group_judgment_updates_derived_state(requirement_kind: str, expected_state: str) -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=1)
    preferences = TravelPreferences(interests=[InterestTag.CULTURE, InterestTag.NATURE], must_visit_places=["West Lake"] if requirement_kind == "must_visit" else [], avoid_places=["Night Market"] if requirement_kind == "avoid" else [])
    confirmed = ConfirmedTravelRequirement(schema_version="1.0", requirement=TravelRequirement(destination="杭州", time_window=TravelTimeWindow(sightseeing_days=1), preferences=preferences), metadata_without_raw_evidence=FieldMetadataRegistry(), confirmation_summary_digest="confirmed")
    request = CandidateResearchInitialRequest(confirmed=confirmed, hotel_selection_ref=hotel_ref, operation_plan=request.operation_plan)
    operation = FakeModelOperation()

    def action_response(_count: int):
        return structured(ProposedActionBatch(actions=(_first_objective_action(operation),)))

    def judgment_response(_count: int):
        group = json.loads(operation.prompts[-1].user_text)["requirements"][0]
        role = RoleJudgmentUpdate(candidate_handle="c:0", role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS, evidence_handles=(EvidenceHandle(handle_id="e:0"),))
        if requirement_kind == "must_visit":
            update = RequirementGroupJudgmentUpdate(requirement_group_id=group["requirement_group_id"], requirement_kind="must_visit", evaluated_candidate_handles=("c:0",), default_verdict=MatchVerdict.CONFIRMED, default_reason_code=RequirementReasonCode.EXACT_NAME_MATCH, default_evidence_handles=(EvidenceHandle(handle_id="e:0"),), exceptions=())
        else:
            update = RequirementGroupJudgmentUpdate(requirement_group_id=group["requirement_group_id"], requirement_kind="avoid", evaluated_candidate_handles=("c:0",), default_verdict=MatchVerdict.NO_MATCH, default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT, default_evidence_handles=(EvidenceHandle(handle_id="e:0"),), exceptions=(RequirementVerdictException(candidate_handle="c:0", verdict=MatchVerdict.CONFIRMED, reason_code=RequirementReasonCode.EXACT_NAME_MATCH, evidence_handles=(EvidenceHandle(handle_id="e:0"),)),))
        return structured(JudgmentPatch(role_updates=(role,), interest_updates=(), meal_updates=(), requirement_group_updates=(update,)))

    def ignored_action(_count: int):
        payload = json.loads(operation.prompts[-1].user_text)
        objective = payload["objectives"][1]
        if objective["kind"] == "initial_hotel_restaurant_nearby":
            action = ProposedSearchAction(ordinal=0, objective_id=objective["objective_id"], query="ignored-nearby", area_mode=SearchAreaMode.NEARBY, anchor=HotelAnchorRef(), radius_km=3)
        else:
            action = ProposedSearchAction(ordinal=0, objective_id=objective["objective_id"], query="ignored", area_mode=SearchAreaMode.CITY)
        return structured(ProposedActionBatch(actions=(action,)))

    operation.responses.extend((action_response, judgment_response, ignored_action, structured(NoUsefulActionProposed(reason_code="prior_no_progress"))))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel_artifact)
    runtime = FakeRuntime(operation)
    service = CandidateResearchService(runtime=runtime, map_adapter=FakeMapAdapter((search_success("requirement-place"),)), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    result = await service.research_initial(request)
    assert result.outcome_type in {"incomplete", "failed"}
    if result.outcome_type == "failed":
        assert result.failure.code.value == "action_batch_invalid"
        assert result.failure.code.value != "replay_mismatch"
        state_ref = result.last_consistent_candidate_set_ref
    else:
        state_ref = result.candidate_set_ref
    assert state_ref is not None
    first_state_ref = repository.refs[request.operation_plan.iteration_slots[0].candidate_set_operation_id]
    first_state = repository.contents[first_state_ref.artifact_id]["record"]["state"]
    terminal_state = repository.contents[state_ref.artifact_id]["record"]["state"]
    for state in (first_state, terminal_state):
        candidate = state["candidates"][0]
        groups = state["requirement_group_states"]
        assert candidate["state"] == expected_state
        assert groups and groups[0]["requirement_kind"] == requirement_kind
        if requirement_kind == "must_visit":
            assert state["coverage"]["must_item_count"] == 1
            assert state["coverage"]["resolved_must_item_count"] == 1
            assert groups[0]["default_verdict"] == "confirmed"
            assert candidate["must_matches"][0]["requirement_group_id"] == groups[0]["group_id"]
            assert candidate["must_matches"][0]["verdict"] == groups[0]["default_verdict"]
            assert candidate["must_matches"][0]["evidence_indexes"] == groups[0]["default_evidence_indexes"]
        else:
            assert state["coverage"]["must_item_count"] == 0
            assert state["coverage"]["resolved_must_item_count"] == 0
            assert candidate["avoid_matches"][0]["requirement_group_id"] == groups[0]["group_id"]
    if result.outcome_type == "incomplete":
        assert result.coverage.must_item_count == (1 if requirement_kind == "must_visit" else 0)
        assert result.coverage.resolved_must_item_count == (1 if requirement_kind == "must_visit" else 0)


@pytest.mark.asyncio
async def test_public_initial_multiple_must_groups_and_items_are_counted_once_per_resolved_group() -> None:
    run_id = uuid4()
    request, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=1)
    confirmed = ConfirmedTravelRequirement(
        schema_version="1.0",
        requirement=TravelRequirement(
            destination="杭州",
            time_window=TravelTimeWindow(sightseeing_days=1),
            preferences=TravelPreferences(
                interests=[InterestTag.CULTURE, InterestTag.NATURE],
                must_visit_places=["West Lake", "West Lake", "Lingyin Temple"],
            ),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(),
        confirmation_summary_digest="confirmed",
    )
    request = CandidateResearchInitialRequest(
        confirmed=confirmed,
        hotel_selection_ref=hotel_ref,
        operation_plan=request.operation_plan,
    )
    operation = FakeModelOperation()

    def action_response(_count: int):
        return structured(ProposedActionBatch(actions=(_first_objective_action(operation),)))

    def judgment_response(_count: int):
        groups = json.loads(operation.prompts[-1].user_text)["requirements"]
        resolved_group = next(group for group in groups if len(group["requirement_item_ids"]) == 2)
        unresolved_group = next(group for group in groups if len(group["requirement_item_ids"]) == 1)
        roles = tuple(
            RoleJudgmentUpdate(
                candidate_handle=f"c:{ordinal}",
                role=ResearchRole.ATTRACTION,
                verdict=RoleVerdict.SUPPORTED,
                reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS,
                evidence_handles=(EvidenceHandle(handle_id="e:0"),),
            )
            for ordinal in range(2)
        )
        updates = (
            RequirementGroupJudgmentUpdate(
                requirement_group_id=resolved_group["requirement_group_id"],
                requirement_kind="must_visit",
                evaluated_candidate_handles=("c:0", "c:1"),
                default_verdict=MatchVerdict.CONFIRMED,
                default_reason_code=RequirementReasonCode.EXACT_NAME_MATCH,
                default_evidence_handles=(EvidenceHandle(handle_id="e:0"),),
                exceptions=(),
            ),
            RequirementGroupJudgmentUpdate(
                requirement_group_id=unresolved_group["requirement_group_id"],
                requirement_kind="must_visit",
                evaluated_candidate_handles=("c:0", "c:1"),
                default_verdict=MatchVerdict.UNRESOLVED,
                default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT,
                default_evidence_handles=(EvidenceHandle(handle_id="e:0"),),
                exceptions=(),
            ),
        )
        return structured(
            JudgmentPatch(
                role_updates=roles,
                interest_updates=(),
                meal_updates=(),
                requirement_group_updates=updates,
            )
        )

    def ignored_action(_count: int):
        objective = json.loads(operation.prompts[-1].user_text)["objectives"][1]
        if objective["kind"] == "initial_hotel_restaurant_nearby":
            action = ProposedSearchAction(
                ordinal=0,
                objective_id=objective["objective_id"],
                query="ignored-nearby",
                area_mode=SearchAreaMode.NEARBY,
                anchor=HotelAnchorRef(),
                radius_km=3,
            )
        else:
            action = ProposedSearchAction(
                ordinal=0,
                objective_id=objective["objective_id"],
                query="ignored",
                area_mode=SearchAreaMode.CITY,
            )
        return structured(ProposedActionBatch(actions=(action,)))

    operation.responses.extend(
        (
            action_response,
            judgment_response,
            ignored_action,
            structured(NoUsefulActionProposed(reason_code="prior_no_progress")),
        )
    )
    search = PlaceSearchSuccess(
        places=(
            PlaceSummary(uid="west-lake-a", name="West Lake A", address="Road A", coordinate=BaiduCoordinate(latitude=30.1, longitude=120.1), category="museum"),
            PlaceSummary(uid="west-lake-b", name="West Lake B", address="Road B", coordinate=BaiduCoordinate(latitude=30.2, longitude=120.2), category="museum"),
        ),
        provenance=map_provenance(MapCapability.PLACE_SEARCH),
    )
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    service = CandidateResearchService(
        runtime=FakeRuntime(operation),
        map_adapter=FakeMapAdapter((search,)),
        artifact_repository=repository,
        budget_manager=FakeBudgetManager(run_id),
    )

    result = await service.research_initial(request)

    assert result.outcome_type == "incomplete"
    state = repository.contents[result.candidate_set_ref.artifact_id]["record"]["state"]
    assert state["coverage"]["must_item_count"] == result.coverage.must_item_count == 3
    assert state["coverage"]["resolved_must_item_count"] == result.coverage.resolved_must_item_count == 2
    assert [candidate["state"] for candidate in state["candidates"]] == ["eligible", "eligible"]
    resolved_group = next(group for group in state["requirement_group_states"] if len(group["requirement_item_ids"]) == 2)
    unresolved_group = next(group for group in state["requirement_group_states"] if len(group["requirement_item_ids"]) == 1)
    assert resolved_group["default_verdict"] == "confirmed"
    assert unresolved_group["default_verdict"] == "unresolved"
    assert all(
        next(match for match in candidate["must_matches"] if match["requirement_group_id"] == resolved_group["group_id"])["verdict"] == "confirmed"
        for candidate in state["candidates"]
    )
    validated = CandidateSetStateSnapshot.model_validate_json(
        json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        strict=True,
    )
    assert validated.coverage.resolved_must_item_count == 2
    for field, stale_value in (("must_item_count", 2), ("resolved_must_item_count", 3)):
        stale = json.loads(json.dumps(state))
        stale["coverage"][field] = stale_value
        with pytest.raises(ValidationError, match="must coverage summary mismatch"):
            CandidateSetStateSnapshot.model_validate_json(
                json.dumps(stale, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                strict=True,
            )
