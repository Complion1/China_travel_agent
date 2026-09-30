from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.baidu_map import (
    BaiduCoordinate,
    BaiduMapErrorCode,
    MapCapability,
    MapProvenance,
    MatrixTravelMode,
    RouteMatrixCellSuccess,
    TravelMode,
)
from app.candidate_research import CandidateState, MealIntent, ResearchRole
from app.itinerary_planning import (
    ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
    ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY,
    CandidateLocationRef,
    BusinessHoursState,
    DayPlan,
    HotelLocationRef,
    LLMPlanningFailure,
    RouteLeg,
    RouteAlternativeSummary,
    RouteTimeInterval,
    PlanningDayKind,
    ScheduledVisit,
    TransportSemantic,
    TimeInterval,
    VisitKind,
)
from app.llm_runtime import ITINERARY_PLANNING_PROFILE, ModelRole
from app.itinerary_planning.models import (
    AreaCoverageTarget,
    AvoidResolutionTarget,
    CandidateCountTarget,
    CandidateDetailsTarget,
    MealCoverageTarget,
    MustResolutionTarget,
    ReplacementTarget,
)
from app.requirements import ConfirmedTravelRequirement, HubType, InterestTag, TravelPace
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase


@pytest.mark.asyncio
async def test_real_runtime_delivers_fifth_transport_result_to_submit_only_call() -> None:
    import json
    from app.llm_runtime import DeepSeekRuntime
    from app.llm_runtime.protocols import ProviderResponse
    from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper
    from tests.unit.itinerary_planning.test_service_initial import _fixture

    class Provider(ScriptedProviderAdapter):
        def __init__(self):
            super().__init__([])
            self.received_ids = []
            self.handles = ()
            self.context = None

        def response(self):
            index = self.call_count
            self.call_count += 1
            calls = tuple((call.call_id, call.tool_name, call.arguments.model_dump(mode="json")) for call in __import__("tests.unit.itinerary_planning.test_phase_budget", fromlist=["hubs"]).hubs().calls) if index == 0 else () if index == 6 else ((f"matrix-{index-1}", "calculate_route_matrix", {
                "date": self.context["sightseeing_start_date"],
                "mode": tuple(MatrixTravelMode)[(index-1) // 2].value,
                "location_handles": list(self.handles if index % 2 else self.handles[::-1]),
            }),)
            return ProviderResponse(content="结束" if not calls else None, tool_calls=calls,
                finish_reason="stop" if not calls else "tool_calls", model_name="deepseek-v4-flash",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                       "reasoning_tokens": 0, "prompt_cache_hit_tokens": 0,
                       "prompt_cache_miss_tokens": 1}, reasoning_content=None)

        async def ainvoke_tools(self, request):
            self.tool_requests.append(request)
            self.context = json.loads(request.user.split("BEGIN_TYPED_PLANNING_DATA\n")[1].split("\nEND_TYPED_PLANNING_DATA")[0])
            self.handles = tuple(item["handle"] for item in self.context["handles"] if item["kind"] in {"hotel", "candidate"})
            assert self.context["confirmed"]["arrival"]["local_datetime"]
            return self.response()

        async def ainvoke_tool_continuation(self, request):
            self.tool_continuations.append(request)
            results = [item for item in request.messages if item["role"] == "tool"]
            latest = results[-1]
            self.received_ids.append(latest["tool_call_id"])
            data = json.loads(latest["content"])
            assert data["tool_result"]["observation"]["issued_handles"]
            if self.call_count == 6:
                assert {tool["function"]["name"] for tool in request.tools} == {"submit_itinerary"}
            return self.response()

    service, request, _, _, repository = _fixture()
    from app.requirements import TravelTimeWindowArrival, HubType
    window = request.confirmed.requirement.time_window.model_copy(update={"arrival": TravelTimeWindowArrival(hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION, local_datetime="2026-09-03 08:00")})
    request = request.model_copy(update={"confirmed": request.confirmed.model_copy(update={"requirement": request.confirmed.requirement.model_copy(update={"time_window": window})})})
    provider = Provider()
    service._runtime = DeepSeekRuntime(provider=provider, sleeper=RecordingSleeper())
    request = request.model_copy(update={"operation_plan": request.operation_plan.model_copy(update={
        "matrix_operation_ids": tuple(OperationId(run_id=request.operation_plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"runtime_matrix_{i}") for i in range(6)),
    })})
    result = await service.plan_initial(request)
    assert result.outcome_type == "incomplete", (provider.call_count, getattr(getattr(getattr(result, "failure", None), "error", None), "code", None))
    assert result.budgets.model_invocations_consumed == 7
    assert provider.call_count == 7
    assert provider.received_ids == ["departure", *[f"matrix-{i}" for i in range(5)]]
    assert len(service._map_adapter.matrix_calls) == 5
    assert result.budgets.preparation_used == 1 and result.budgets.transport_used == 5 and result.budgets.submit_repair_used == 1


def test_required_upstream_public_contracts_are_consumable() -> None:
    assert ModelRole.ITINERARY_PLANNING.value == "itinerary_planning"
    assert ITINERARY_PLANNING_PROFILE.semantic_invocation_limit == 17
    assert ITINERARY_PLANNING_PROFILE.max_request_utf8_bytes == 2097152
    assert ITINERARY_PLANNING_PROFILE.max_output_tokens == 49152
    assert ITINERARY_PLANNING_PROFILE.read_timeout_seconds == 120
    assert ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY.name == "planning.model_invocation"
    assert ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY.name == "planning.direction_call"
    assert WorkflowPhase.ITINERARY_PLANNING.value == "itinerary_planning"
    assert TravelMode.TRANSIT.value == "transit"
    assert MatrixTravelMode.WALKING.value == "walking"
    assert BaiduCoordinate(latitude=31.2, longitude=121.5).coordinate_system.value == "bd09ll"
    assert HubType.AIRPORT.value == "airport"
    assert TravelPace.NORMAL.value == "标准"
    assert InterestTag.CULTURE.value == "人文"
    assert ResearchRole.ATTRACTION.value == "attraction"
    assert MealIntent.DINNER.value == "dinner"
    assert CandidateState.ELIGIBLE.value == "eligible"
    assert PlanningDayKind.FULL.value == "full"
    assert TransportSemantic.PRIVATE_CAR.value == "private_car"


@pytest.mark.asyncio
@pytest.mark.parametrize("update_old_details", (False, True))
async def test_real_candidate_research_incomplete_unchanged_uid_resumes_exact_request(update_old_details: bool) -> None:
    import json
    import app.candidate_research as research
    from tests.support import candidate_research_fakes as upstream
    from tests.unit.candidate_research.test_service_initial import _request, _first_objective_action, _action_batch_from_prompt
    from tests.unit.candidate_research.test_service_supplemental import _slots
    from tests.unit.itinerary_planning.test_service_initial import _fixture, _plan, _metadata
    from tests.unit.itinerary_planning.test_service_resume import _supplement_call
    from app.itinerary_planning import ItineraryPlanningResumeRequest
    from app.llm_runtime import ToolCallsReady, ToolTurnCompleted

    call = _supplement_call()
    if update_old_details:
        from app.itinerary_planning.models import ProposedCandidateDetailsTarget
        gap = call.arguments.proposed_gaps[0].model_copy(update={"target": ProposedCandidateDetailsTarget(candidate_handles=("candidate-handle-0000",))})
        call = call.model_copy(update={"arguments": call.arguments.model_copy(update={"proposed_gaps": (gap,)})})
    planner, planning_request, planning_runtime, _, repository = _fixture(
        ToolCallsReady(calls=(call,), metadata=_metadata()),
        ToolTurnCompleted(final_text="继续", metadata=_metadata()),
    )
    run_id = planning_request.operation_plan.run_id
    initial, _, _, _ = _request(run_id)
    initial = initial.model_copy(update={"confirmed": planning_request.confirmed, "hotel_selection_ref": planning_request.hotel_selection_ref})
    operation = upstream.FakeModelOperation()
    operation.responses.extend((
        lambda _: upstream.structured(research.ProposedActionBatch(actions=(_first_objective_action(operation),))),
        upstream.structured(research.JudgmentPatch(
            role_updates=(research.RoleJudgmentUpdate(candidate_handle="c:0", role=research.ResearchRole.ATTRACTION,
                verdict=research.RoleVerdict.SUPPORTED, reason_code=research.RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS,
                evidence_handles=(research.EvidenceHandle(handle_id="e:0"),)),),
            interest_updates=(), meal_updates=(), requirement_group_updates=())),
        lambda _: upstream.structured(research.ProposedActionBatch(actions=(_action_batch_from_prompt(operation, 2).actions[1].model_copy(update={"ordinal": 0}),))),
        upstream.structured(research.NoUsefulActionProposed(reason_code="prior_no_progress")),
    ))
    map_adapter = upstream.FakeMapAdapter((upstream.search_success("actual-uid"),))
    research_service = research.CandidateResearchService(runtime=upstream.FakeRuntime(operation),
        map_adapter=map_adapter,
        artifact_repository=repository, budget_manager=upstream.FakeBudgetManager(run_id))
    researched = await research_service.research_initial(initial)
    assert researched.outcome_type == "incomplete", getattr(getattr(researched, "failure", None), "code", None)
    planning_request = planning_request.model_copy(update={"candidate_set_ref": researched.candidate_set_ref})
    awaiting = await planner.plan_initial(planning_request)
    assert awaiting.outcome_type == "candidate_supplement_requested", awaiting
    gap = awaiting.gaps[0]
    predecessor = (await repository.get(researched.candidate_set_ref, expected_model=research.CandidateResearchArtifact)).content
    remaining = predecessor.record.state.budgets.iteration_limit - predecessor.record.state.budgets.iterations_consumed
    supplemental_plan = research.CandidateResearchOperationPlan(run_id=run_id,
        iteration_slots=_slots(run_id, count=remaining, prefix="actual_supplement", place_width=1),
        terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="actual_supplement_terminal"))
    actual_gap = research.CandidateDetailsMissingGap(gap_id=gap.gap_id, evidence_refs=gap.evidence_refs, candidate_uids=gap.target.candidate_uids) if update_old_details else research.CandidateCountShortfallGap(gap_id=gap.gap_id, evidence_refs=gap.evidence_refs,
        role=gap.target.role, current_count=gap.target.current_count, target_count=gap.target.target_count)
    supplemental = research.CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=researched.candidate_set_ref,
        gaps=(actual_gap,),
        operation_plan=supplemental_plan)
    if update_old_details:
        operation.responses.extend((
            lambda _: upstream.structured(research.ProposedActionBatch(actions=(research.ProposedDetailsAction(ordinal=0,
                objective_id=json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"], uid="actual-uid"),))),
            upstream.structured(research.JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())),
            lambda _: upstream.structured(research.ProposedActionBatch(actions=(_action_batch_from_prompt(operation, 2).actions[1].model_copy(update={"ordinal": 0}),))),
        ))
        map_adapter.responses.append(upstream.details_success("actual-uid"))
    operation.responses.append(upstream.structured(research.NoUsefulActionProposed(reason_code="prior_no_progress")))
    supplemented = await research_service.research_supplemental(supplemental)
    assert supplemented.outcome_type in {"completed", "incomplete"}, getattr(getattr(supplemented, "failure", None), "code", None)
    successor = (await repository.get(supplemented.candidate_set_ref, expected_model=research.CandidateResearchArtifact)).content
    assert tuple(item.core.uid for item in successor.record.state.candidates) == ("actual-uid",)
    if update_old_details:
        assert successor.record.state.candidates[0].details.status == "success"
        assert successor.predecessor_ref == researched.candidate_set_ref
    resume = ItineraryPlanningResumeRequest(predecessor_planning_ref=awaiting.planning_ref,
        supplemented_candidate_set_ref=supplemented.candidate_set_ref,
        operation_plan=_plan(run_id, suffix="actual_resume", slots=tuple(range(awaiting.budgets.model_invocations_consumed,17))), supplemental_operation_plan=supplemental_plan)
    def changed_id(name):
        return OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=name)
    different_terminal = supplemental_plan.model_copy(update={"terminal_successor_operation_id": changed_id("different_research_terminal")})
    different_slot = supplemental_plan.iteration_slots[0].model_copy(update={"action_operation_id": changed_id("different_research_action")})
    different_nonterminal = supplemental_plan.model_copy(update={"iteration_slots": (different_slot, *supplemental_plan.iteration_slots[1:])})
    for changed in (different_terminal, different_nonterminal):
        writes = len(repository.put_requests)
        calls = planning_runtime.operation.call_count
        rejected = await planner.plan_resume(resume.model_copy(update={"supplemental_operation_plan": changed}))
        assert rejected.failure.code.value == "request_mismatch"
        assert len(repository.put_requests) == writes
        assert planning_runtime.operation.call_count == calls
    result = await planner.plan_resume(resume)
    assert result.outcome_type == "incomplete", result
    assert planning_runtime.start_count == 1
    reads = len(repository.get_calls)
    assert await planner.plan_resume(resume) == result
    assert repository.get_calls[reads:] == [repository.refs[resume.operation_plan.terminal_successor_operation_id]]


@pytest.mark.asyncio
async def test_real_runtime_uses_new_geocode_handle_for_directions() -> None:
    import json
    from app.baidu_map import GeocodeSuccess, GeocodeCandidate
    from app.llm_runtime import DeepSeekRuntime
    from app.llm_runtime.protocols import ProviderResponse
    from app.requirements import TravelTimeWindowArrival
    from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper
    from tests.unit.itinerary_planning.test_service_initial import _fixture
    from tests.support.itinerary_planning_fakes import FakeMapAdapter, map_provenance

    class Provider(ScriptedProviderAdapter):
        def __init__(self):
            super().__init__([])
            self.hotel = None
            self.hub = None

        def response(self, calls):
            self.call_count += 1
            return ProviderResponse(content=None if calls else "结束", tool_calls=calls,
                finish_reason="tool_calls" if calls else "stop", model_name="deepseek-v4-flash",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                    "reasoning_tokens": 0, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1}, reasoning_content=None)

        async def ainvoke_tools(self, request):
            context = json.loads(request.user.split("BEGIN_TYPED_PLANNING_DATA\n")[1].split("\nEND_TYPED_PLANNING_DATA")[0])
            self.hotel = next(item["handle"] for item in context["handles"] if item["kind"] == "hotel")
            assert context["confirmed"]["arrival"]["hub_name"] == "杭州东站"
            return self.response((("locate", "geocode_hub", {"endpoint_kind": "arrival"}), ("locate_departure", "geocode_hub", {"endpoint_kind": "departure"})))

        async def ainvoke_tool_continuation(self, request):
            latest = [item for item in request.messages if item["role"] == "tool"][-1]
            data = json.loads(latest["content"])
            assert "planning_context" not in data
            state = data["planning_state"]
            if self.call_count == 1:
                assert latest["tool_call_id"] == "locate_departure" and state["model_invocations_consumed"] == 1
                assert state["candidates"] == []
                self.hub = json.loads([item for item in request.messages if item["role"] == "tool"][-2]["content"])["tool_result"]["observation"]["issued_handles"][0]
                return self.response((("route", "plan_directions", {"legs": [{"leg_id": "arrival", "origin_handle": self.hub,
                    "destination_handle": self.hotel, "transport_semantic": "walking", "route_mode": "walking", "alternatives_limit": 1}]}),))
            assert latest["tool_call_id"] == "route" and state["model_invocations_consumed"] == 2
            assert data["tool_result"]["observation"]["observations"][0]["directions_handle"]
            return self.response(())

    service, request, _, _, _ = _fixture()
    window = request.confirmed.requirement.time_window.model_copy(update={"arrival": TravelTimeWindowArrival(hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION, local_datetime="2026-09-03 08:00")})
    request = request.model_copy(update={"confirmed": request.confirmed.model_copy(update={"requirement": request.confirmed.requirement.model_copy(update={"time_window": window})})})
    def op(step):
        return OperationId(run_id=request.operation_plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=step)
    request = request.model_copy(update={"operation_plan": request.operation_plan.model_copy(update={"hub_geocode_operation_ids": (op("real_hub"), op("real_hub_departure")), "direction_operation_ids": (op("real_route"),)})})
    service._map_adapter = FakeMapAdapter(geocode_factory=lambda _: GeocodeSuccess(result=GeocodeCandidate(formatted_address="杭州东站", coordinate=BaiduCoordinate(latitude=30.29, longitude=120.21), confidence=0.98), provenance=map_provenance(MapCapability.GEOCODING)))
    provider = Provider()
    service._runtime = DeepSeekRuntime(provider=provider, sleeper=RecordingSleeper())
    result = await service.plan_initial(request)
    assert result.outcome_type == "incomplete", result
    assert provider.call_count == 3
    assert len(service._map_adapter.geocode_calls) == 2 and len(service._map_adapter.directions_calls) == 1


def test_public_and_upstream_values_use_fresh_explicit_artifact_snapshots() -> None:
    import importlib
    from app.llm_runtime import LLMRuntimeError, LLMErrorCode

    local = importlib.import_module("app.itinerary_planning.artifacts")
    candidate = CandidateLocationRef(admission_ordinal=2, uid="candidate-2")
    candidate_snapshot = local._to_location_snapshot(candidate)
    candidate_copy = local._from_location_snapshot(candidate_snapshot)
    assert candidate_copy == candidate
    assert candidate_copy is not candidate

    coordinate = BaiduCoordinate(latitude=31.2, longitude=121.5)
    coordinate_snapshot = local._to_coordinate_snapshot(coordinate)
    assert local._from_coordinate_snapshot(coordinate_snapshot) == coordinate
    provenance = MapProvenance(capability=MapCapability.GEOCODING, retrieved_at=datetime(2026, 9, 3, tzinfo=timezone.utc), verified=True)
    provenance_snapshot = local._to_provenance_snapshot(provenance)
    assert local._from_provenance_snapshot(provenance_snapshot) == provenance

    cell = RouteMatrixCellSuccess(origin_index=0, destination_index=0, distance_m=1200, duration_s=600)
    cell_snapshot = local._to_matrix_cell_snapshot(cell)
    assert local._from_matrix_cell_snapshot(cell_snapshot) == cell
    alternative = RouteAlternativeSummary(alternative_index=0, distance_m=1200, duration_s=600, step_count=2)
    alternative_snapshot = local._to_route_alternative_snapshot(alternative)
    assert local._from_route_alternative_snapshot(alternative_snapshot) == alternative

    error = LLMRuntimeError(
        code=LLMErrorCode.TIMEOUT, role=ModelRole.ITINERARY_PLANNING, retryable=True, possible_duplicate=False,
        provider_status_code=None, retry_after_seconds=None, actual_bytes=None, max_bytes=None,
    )
    failure = LLMPlanningFailure(error=error, invocation_index=0, retryable=True, possible_duplicate=False)
    failure_snapshot = local._to_failure_snapshot(failure)
    assert local._from_failure_snapshot(failure_snapshot) == failure


def _public_day(visit_count: int) -> DayPlan:
    shanghai = ZoneInfo("Asia/Shanghai")
    candidates = tuple(CandidateLocationRef(admission_ordinal=index, uid=f"candidate-{index + 1}") for index in range(visit_count))
    visit_windows = ((10, 11), (12, 13))
    visits = tuple(
        ScheduledVisit(
            visit_id=f"visit-{index + 1}", location=candidate, visit_kind=VisitKind.ATTRACTION,
            interval=TimeInterval(
                start_at=datetime(2026, 9, 3, visit_windows[index][0], tzinfo=shanghai),
                end_at=datetime(2026, 9, 3, visit_windows[index][1], tzinfo=shanghai),
            ),
            duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(),
            business_hours_state=BusinessHoursState.COMPATIBLE, business_evidence_indexes=(), requires_verification=False,
        )
        for index, candidate in enumerate(candidates)
    )
    route_points = (9.5, 11, 13)
    hotel = HotelLocationRef()
    route_refs = (hotel, *candidates, hotel)
    route_legs = tuple(
        RouteLeg(
            leg_id=f"leg-{index + 1}", origin=route_refs[index], destination=route_refs[index + 1],
            interval=RouteTimeInterval(
                start_at=datetime(2026, 9, 3, int(route_points[index]), int((route_points[index] % 1) * 60), tzinfo=shanghai),
                end_at=datetime(2026, 9, 3, int(route_points[index]), int((route_points[index] % 1) * 60), tzinfo=shanghai),
            ),
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
            directions_observation_index=0, alternative_index=0, distance_m=0, duration_s=0, scheduled_minutes=0,
        )
        for index in range(visit_count + 1)
    )
    return DayPlan(
        date=datetime(2026, 9, 3, tzinfo=shanghai).date(), day_kind=PlanningDayKind.PARTIAL, theme="游览日",
        open_intervals=(TimeInterval(
            start_at=datetime(2026, 9, 3, 9, tzinfo=shanghai), end_at=datetime(2026, 9, 3, 14, tzinfo=shanghai),
        ),),
        visits=visits, route_legs=route_legs, backups=(),
    )


@pytest.mark.parametrize("visit_count", (1, 2))
def test_public_day_plan_round_trips_through_fresh_artifact_snapshot(visit_count: int) -> None:
    import importlib

    local = importlib.import_module("app.itinerary_planning.artifacts")
    source = _public_day(visit_count)
    snapshot = local._to_day_snapshot(source)
    restored = local._from_day_snapshot(snapshot)
    assert restored == source
    assert restored is not source
    assert restored.visits is not source.visits
    assert restored.route_legs is not source.route_legs


def _supplement_targets() -> tuple[object, ...]:
    candidate = CandidateLocationRef(admission_ordinal=2, uid="candidate-2")
    hotel = HotelLocationRef()
    return (
        CandidateCountTarget(role=ResearchRole.ATTRACTION, current_count=1, target_count=2),
        AreaCoverageTarget(role=ResearchRole.ATTRACTION, anchor=candidate, radius_km=3),
        MealCoverageTarget(meal_intent=MealIntent.DINNER, anchor=hotel, radius_km=1),
        MustResolutionTarget(requirement_group_id="must-1", requirement_item_ids=("must-item-1",)),
        AvoidResolutionTarget(
            requirement_group_id="avoid-1", requirement_item_ids=("avoid-item-1",), candidate_uids=("candidate-2",),
        ),
        CandidateDetailsTarget(candidate_uids=("candidate-2",)),
        ReplacementTarget(rejected_uid="rejected-1", role=ResearchRole.ATTRACTION, anchor=None, radius_km=None),
        ReplacementTarget(rejected_uid="rejected-2", role=ResearchRole.ATTRACTION, anchor=candidate, radius_km=5),
        ReplacementTarget(rejected_uid="rejected-3", role=ResearchRole.ATTRACTION, anchor=hotel, radius_km=1),
    )


@pytest.mark.parametrize("target", _supplement_targets(), ids=lambda value: value.target_type)
def test_all_candidate_supplement_target_branches_round_trip_fresh(target: object) -> None:
    import importlib

    local = importlib.import_module("app.itinerary_planning.artifacts")
    snapshot = local._to_target_snapshot(target)
    restored = local._from_target_snapshot(snapshot)
    assert restored == target
    assert restored is not target
    if hasattr(target, "anchor") and target.anchor is not None:
        assert restored.anchor is not target.anchor


def test_replacement_target_rejects_unpaired_anchor_and_radius() -> None:
    with pytest.raises(ValueError):
        ReplacementTarget(
            rejected_uid="rejected-1", role=ResearchRole.ATTRACTION,
            anchor=CandidateLocationRef(admission_ordinal=0, uid="candidate-1"), radius_km=None,
        )
    with pytest.raises(ValueError):
        ReplacementTarget(rejected_uid="rejected-2", role=ResearchRole.ATTRACTION, anchor=None, radius_km=1)
