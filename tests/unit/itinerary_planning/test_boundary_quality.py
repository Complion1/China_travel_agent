"""抵离日质量：实际窗口、反绕过和完整生产提交入口。"""

import asyncio
from datetime import date, datetime, timedelta
import hashlib
import json
from unittest.mock import patch as mock_patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.itinerary_planning import ItineraryPlanningArtifact, ItineraryPlanningCompleted, ItineraryPlanningFailed
from app.itinerary_planning.artifacts import _from_draft_snapshot, _to_draft_snapshot, BoundaryItineraryDraftSnapshot
from app.itinerary_planning.models import (
    DayPlan, PlannedBreak, PlanningDayKind, ProposedItineraryPatch, SubmitItineraryArgs, TimeInterval,
)
from app.itinerary_planning.policy import SHANGHAI
from app.itinerary_planning.quality import assess_boundary_quality, boundary_day_windows, validate_break_windows
from app.llm_runtime import ToolCallsReady, ToolTurnCompleted, ValidatedToolCall
from app.requirements import TravelPace, HubType
from tests.contract.itinerary_planning.test_artifact_registration import _directories, _draft
from tests.unit.itinerary_planning.test_service_initial import _confirmed, _metadata


def at(value):
    return datetime.fromisoformat("2026-09-03T" + value).replace(tzinfo=SHANGHAI)


def quality_case(arrival="09:00", departure="20:00", *, approximate=False, airport=False):
    confirmed = _confirmed()
    window = confirmed.requirement.time_window
    window.arrival.local_datetime = "2026-09-03 " + arrival
    window.departure.local_datetime = "2026-09-03 " + departure
    for endpoint in (window.arrival, window.departure):
        endpoint.is_approximate = approximate
        endpoint.hub_type = HubType.AIRPORT if airport else HubType.RAILWAY_STATION
    draft = _from_draft_snapshot(_draft(_directories()))
    arrival_leg = draft.arrival_transfer.route_leg.model_copy(update={
        "interval": draft.arrival_transfer.route_leg.interval.model_copy(update={"start_at": at("09:30"), "end_at": at("10:00")}),
        "duration_s": 1800, "scheduled_minutes": 30,
    })
    departure_leg = draft.departure_transfer.route_leg.model_copy(update={
        "interval": draft.departure_transfer.route_leg.interval.model_copy(update={"start_at": at("18:15"), "end_at": at("18:45")}),
        "duration_s": 1800, "scheduled_minutes": 30,
    })
    draft = draft.model_copy(update={
        "arrival_transfer": draft.arrival_transfer.model_copy(update={"route_leg": arrival_leg}),
        "departure_transfer": draft.departure_transfer.model_copy(update={"route_leg": departure_leg}),
    })
    return confirmed, draft


@pytest.mark.parametrize("airport,approximate,start,end", (
    (False, False, "10:00", "18:30"), (False, True, "10:30", "18:00"),
    (True, False, "10:30", "17:30"), (True, True, "11:00", "17:00"),
))
def test_windows_use_fixed_buffers_verified_duration_and_not_arbitrary_departure(airport, approximate, start, end):
    confirmed, draft = quality_case(approximate=approximate, airport=airport)
    result, = boundary_day_windows(confirmed, draft=draft)
    assert (result.start_at, result.end_at) == (at(start), at(end))
    assert result.affected_by == ("arrival", "departure")
    assert not result.route_pending
    if not airport and not approximate:
        assert result.end_at > draft.departure_transfer.route_leg.interval.start_at
    pending, = boundary_day_windows(confirmed)
    assert pending.route_pending and pending.arrival_route is None
    assert pending.projection()["status"] == "route_pending"


@pytest.mark.parametrize("arrival,departure,expected", (("23:00", "23:59", 0), ("18:00", "20:00", 0), ("09:00", "20:00", 2)))
def test_short_windows_do_not_force_missing_meals(arrival, departure, expected):
    confirmed, draft = quality_case(arrival, departure)
    result = assess_boundary_quality(confirmed, draft)
    assert sum(item.rule.startswith("missing_") for item in result.days[0].quality_issues) == expected


def test_transit_only_does_not_hide_an_unused_boundary_day_and_snapshot_round_trips():
    confirmed, draft = quality_case()
    result = assess_boundary_quality(confirmed, draft)
    assert result.days[0].day_kind is PlanningDayKind.TRANSIT_ONLY
    assert {item.rule for item in result.days[0].quality_issues} == {"unexplained_gap", "missing_lunch", "missing_dinner"}
    saved = _to_draft_snapshot(result)
    assert isinstance(saved, BoundaryItineraryDraftSnapshot)
    assert _from_draft_snapshot(type(saved).model_validate_json(saved.model_dump_json(), strict=True)) == result


@pytest.mark.parametrize("start,end", (("09:50", "10:10"), ("18:10", "18:20")))
def test_breaks_cannot_use_theoretical_window_outside_actual_hotel_availability(start, end):
    confirmed, draft = quality_case()
    rest = PlannedBreak(break_id="rest", kind="rest", start_at=at(start), end_at=at(end), reason="抵离日休息")
    day = draft.days[0].model_copy(update={"planned_breaks": (rest,)})
    with pytest.raises(ValueError, match="planned_break"):
        validate_break_windows(confirmed, draft.model_copy(update={"days": (day,)}))


@pytest.mark.parametrize("reason", (
    "<script>", "https://example.com", "休息;执行命令", "# 标题", "休息\n换行",
    "**rest**", "_rest_", "$(Get-Item .)", "休息\x7f", "休息\x85", "休息\\转义",
))
def test_break_reason_is_bounded_plain_text(reason):
    with pytest.raises(ValidationError):
        PlannedBreak(break_id="rest", kind="rest", start_at=at("14:00"), end_at=at("15:00"), reason=reason)


def test_cumulative_long_break_is_not_a_solution_to_empty_day():
    confirmed, draft = quality_case()
    rest = PlannedBreak(break_id="rest", kind="free_time", start_at=at("10:00"), end_at=at("17:00"), reason="自由活动")
    result = assess_boundary_quality(confirmed, draft.model_copy(update={
        "days": (draft.days[0].model_copy(update={"planned_breaks": (rest,)}),),
    }))
    assert any(item.rule == "excessive_break" and item.minutes == 420 for item in result.days[0].quality_issues)


def test_cross_midnight_transfers_mark_every_affected_date_without_filling_empty_windows():
    confirmed, draft = quality_case()
    window = confirmed.requirement.time_window
    window.arrival.local_datetime = "2026-09-03 23:45"
    window.departure.local_datetime = "2026-09-05 00:15"
    window.sightseeing_end_date, window.sightseeing_days = "2026-09-05", 3
    arrival = draft.arrival_transfer.route_leg.model_copy(update={
        "interval": draft.arrival_transfer.route_leg.interval.model_copy(update={
            "start_at": at("00:15") + timedelta(days=1), "end_at": at("00:45") + timedelta(days=1)})})
    departure = draft.departure_transfer.route_leg.model_copy(update={
        "interval": draft.departure_transfer.route_leg.interval.model_copy(update={
            "start_at": at("22:45") + timedelta(days=1), "end_at": at("23:15") + timedelta(days=1)})})
    draft = draft.model_copy(update={"days": tuple(draft.days[0].model_copy(update={
        "date": date(2026, 9, 3) + timedelta(days=offset)}) for offset in range(3)),
        "arrival_transfer": draft.arrival_transfer.model_copy(update={"route_leg": arrival}),
        "departure_transfer": draft.departure_transfer.model_copy(update={"route_leg": departure})})
    windows = boundary_day_windows(confirmed, draft=draft)
    assert [item.affected_by for item in windows] == [("arrival",), ("arrival", "departure"), ("departure",)]
    assert [item.projection()["available_minutes"] for item in windows] == [0, 870, 0]
    result = assess_boundary_quality(confirmed, draft)
    assert not result.days[0].quality_issues and not result.days[2].quality_issues
    assert any(item.rule == "unexplained_gap" for item in result.days[1].quality_issues)


def test_second_precision_never_expands_earliest_or_latest_boundary():
    confirmed, draft = quality_case(arrival="09:00:01", departure="20:00:59")
    window, = boundary_day_windows(confirmed, draft=draft)
    assert (window.start_at, window.end_at) == (at("10:01"), at("18:30"))


@pytest.mark.parametrize("pace,threshold", ((TravelPace.RELAXED, 240), (TravelPace.NORMAL, 180), (TravelPace.INTENSE, 120)))
@pytest.mark.parametrize("offset", (-1, 0))
def test_idle_threshold_is_inclusive_for_each_pace(pace, threshold, offset):
    confirmed, draft = quality_case()
    end = at("10:00") + timedelta(minutes=threshold + offset)
    confirmed.requirement.time_window.departure.local_datetime = (end + timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M")
    draft = draft.model_copy(update={"effective_policy": draft.effective_policy.model_copy(update={"effective_pace": pace})})
    result = assess_boundary_quality(confirmed, draft)
    assert any(item.rule == "unexplained_gap" for item in result.days[0].quality_issues) is (offset == 0)


@pytest.mark.parametrize("arrival,departure,rule,expected", (
    ("12:30", "16:00", "missing_lunch", True), ("12:31", "16:00", "missing_lunch", False),
    ("12:30", "15:59", "missing_lunch", False), ("18:30", "22:00", "missing_dinner", True),
    ("18:31", "22:00", "missing_dinner", False),
))
def test_meal_start_edge_requires_a_full_minimum_meal(arrival, departure, rule, expected):
    confirmed, draft = quality_case(arrival, departure)
    result = assess_boundary_quality(confirmed, draft)
    assert any(item.rule == rule for item in result.days[0].quality_issues) is expected


def test_combined_visits_count_as_existing_lunch_and_dinner():
    from app.itinerary_planning.artifacts import _from_day_snapshot
    from app.itinerary_planning.models import VisitKind
    from tests.contract.itinerary_planning.test_artifact_registration import _non_transit_day
    confirmed, draft = quality_case()
    day = _from_day_snapshot(_non_transit_day(2))
    visits = tuple(visit.model_copy(update={"visit_kind": kind, "interval": TimeInterval(start_at=at(start), end_at=at(end))})
        for visit, kind, start, end in zip(day.visits, (VisitKind.ATTRACTION_LUNCH, VisitKind.ATTRACTION_DINNER),
            ("12:00", "17:30"), ("13:30", "19:00"), strict=True))
    result = assess_boundary_quality(confirmed, draft.model_copy(update={"days": (day.model_copy(update={"visits": visits}),)}))
    assert not any(item.rule.startswith("missing_") for item in result.days[0].quality_issues)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", (
    "patch", "unresolved", "stop", "invalid_patch", "short_split", "technical", "cancel",
    "hard_loop", "no_capacity", "budget_stop", "supplement_resume", "supplement_stop", "multiday_patch",
))
async def test_public_boundary_quality(case):
    from tests.unit.itinerary_planning.test_service_initial import test_service_completed_requires_main_plan_and_valid_optional_backups
    await test_service_completed_requires_main_plan_and_valid_optional_backups("boundary_quality_" + case)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", (
    "budget_exhausted", "queries_exhausted", "slots_exhausted", "invalid_handle", "other_plan_invalid",
    "partial_slots", "no_quality_baseline", "artifact_failure", "budget_read_failure",
))
async def test_public_quality_direction_capacity(case):
    from tests.unit.itinerary_planning.test_service_initial import test_service_completed_requires_main_plan_and_valid_optional_backups
    await test_service_completed_requires_main_plan_and_valid_optional_backups("boundary_quality_direction_capacity_" + case)



def add_second_day_routes(request, operation, proposal, day):
    from app.baidu_map import BaiduCoordinate, TravelMode
    from app.itinerary_planning.models import (CandidateLocationRef, HotelLocationRef, DirectionLegIntent,
        PlanDirectionsArgs, ProposedRouteLeg, ProposedScheduledVisit, TransportSemantic, VisitKind, BusinessHoursState)
    from app.itinerary_planning.fingerprints import (
        fingerprint_planning_context, fingerprint_directions, fingerprint_directions_handle, _canonical_hash)
    from app.itinerary_planning.service import _confirmed_digest
    from app.workflow_contracts import OperationId, WorkflowPhase
    from tests.support.itinerary_planning_fakes import query_intents
    target_date = day.date + timedelta(days=1)
    context = fingerprint_planning_context(request.operation_plan.run_id, _confirmed_digest(request.confirmed),
        request.hotel_selection_ref, _canonical_hash("杭州东站"), _canonical_hash("杭州东站"))
    operations = tuple(OperationId(run_id=request.operation_plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING,
        logical_step=f"quality_second_day_{index}") for index in range(2))
    request = request.model_copy(update={"operation_plan": request.operation_plan.model_copy(update={
        "direction_operation_ids": (*request.operation_plan.direction_operation_ids, *operations)})})
    candidate, hotel = CandidateLocationRef(admission_ordinal=3, uid="candidate-4"), HotelLocationRef()
    places = (("hotel-handle", hotel, BaiduCoordinate(latitude=30.25, longitude=120.16)),
              ("candidate-handle-0003", candidate, BaiduCoordinate(latitude=30.243, longitude=120.153)))
    query = operation.responses[2].calls[0]
    legs, routes = [], []
    for index, (left, right) in enumerate((places, places[::-1])):
        leg_id = f"quality-second-{index}"
        legs.append(DirectionLegIntent(leg_id=leg_id, origin_handle=left[0], destination_handle=right[0],
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1))
        fingerprint = fingerprint_directions(context, left[1], left[2], right[1], right[2], TravelMode.WALKING, 1)
        handle = fingerprint_directions_handle(operations[index], len(query.arguments.legs) + index, fingerprint)
        routes.append(ProposedRouteLeg(leg_id=leg_id, origin_handle=left[0], destination_handle=right[0],
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, directions_handle=handle,
            start_at=at("09:30" if index == 0 else "11:00") + timedelta(days=1), end_at=None, alternative_index=0))
    args = query.arguments.model_copy(update={"legs": (*query.arguments.legs, *legs),
        "visit_intents": (*query.arguments.visit_intents, *query_intents(target_date, ("candidate-handle-0003",)))})
    operation.responses[2] = operation.responses[2].model_copy(update={"calls": (query.model_copy(update={"arguments": args}),)})
    second = day.model_copy(update={"date": target_date, "planned_breaks": (), "visits": (
        ProposedScheduledVisit(visit_id="quality-second-visit", candidate_handle="candidate-handle-0003",
            visit_kind=VisitKind.ATTRACTION, start_at=at("10:00") + timedelta(days=1), end_at=at("11:00") + timedelta(days=1),
            duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(),
            business_hours_state=BusinessHoursState.UNKNOWN, business_evidence_handles=()),),
        "route_legs": tuple(routes), "backups": (),
        "open_intervals": (TimeInterval(start_at=at("07:30") + timedelta(days=1), end_at=at("19:00") + timedelta(days=1)),)})
    return request, second


async def exercise_public_quality(case, service, request, operation, repository, submit):
    """重用已有真实候选／地图 fixture，质量循环完全经过公共入口。"""
    proposal = submit.draft
    arrival = proposal.arrival_transfer.route_leg.model_copy(update={"start_at": at("08:30"), "end_at": at("08:40")})
    departure = proposal.departure_transfer.route_leg.model_copy(update={"start_at": at("18:45"), "end_at": at("18:55")})
    multiday = case.endswith("multiday_patch")
    if multiday:
        departure = departure.model_copy(update={"start_at": departure.start_at + timedelta(days=1), "end_at": departure.end_at + timedelta(days=1)})
    day = proposal.days[0].model_copy(update={"day_kind": PlanningDayKind.PARTIAL, "backups": (), "planned_breaks": (),
        "open_intervals": (TimeInterval(start_at=at("09:00"), end_at=at("19:00")),)})
    if case.endswith("short_split"):
        day = day.model_copy(update={"planned_breaks": (PlannedBreak(break_id="tiny", kind="rest",
            start_at=at("15:00"), end_at=at("15:01"), reason="短暂休息"),)})
    other_days = ()
    if multiday:
        request, second_day = add_second_day_routes(request, operation, proposal, day)
        other_days = (second_day,)
    proposal = proposal.model_copy(update={"days": (day, *other_days),
        "arrival_transfer": proposal.arrival_transfer.model_copy(update={"route_leg": arrival}),
        "departure_transfer": proposal.departure_transfer.model_copy(update={"route_leg": departure})})
    submit = submit.model_copy(update={"draft": proposal,
        "effective_policy": submit.effective_policy.model_copy(update={"effective_pace": TravelPace.NORMAL})})
    if case.startswith("boundary_quality_direction_capacity_"):
        return await exercise_direction_capacity(case.removeprefix("boundary_quality_direction_capacity_"),
            service, request, operation, repository, submit)

    def response(args, call_id, tool_name="submit_itinerary"):
        return ToolCallsReady(calls=(ValidatedToolCall(call_id=call_id, tool_name=tool_name, arguments=args),), metadata=_metadata())

    def hard_error_sequence():
        changes = (("09:35", "10:35"), ("12:25", "13:25"), ("17:35", "18:35"))
        results = []
        for fixed in range(4):
            bad_visits = tuple(item if index < fixed else item.model_copy(update={"start_at": at(start), "end_at": at(end)})
                for index, (item, (start, end)) in enumerate(zip(day.visits, changes, strict=True)))
            bad_arrival = arrival.model_copy(update={"start_at": at("08:20"), "end_at": at("08:30")})
            draft = proposal.model_copy(update={"days": (day.model_copy(update={"visits": bad_visits}),),
                "arrival_transfer": proposal.arrival_transfer.model_copy(update={"route_leg": bad_arrival})})
            results.append(response(submit.model_copy(update={"draft": draft}), f"hard-{fixed}"))
        return results

    operation.responses[3:] = [response(submit, "quality-first")]
    if case.endswith("no_capacity"):
        operation.responses[3:] = [*hard_error_sequence(), response(submit, "last-slot-valid")]
    elif case.endswith("budget_stop"):
        operation.responses.extend(hard_error_sequence())
    elif case.endswith("technical"):
        operation.responses.append(RuntimeError("synthetic provider failure"))
    elif case.endswith("cancel"):
        operation.responses.append(asyncio.CancelledError())
    elif case.endswith(("supplement_resume", "supplement_stop")):
        from app.candidate_research import ResearchRole
        from app.itinerary_planning.models import ProposedCandidateCountTarget, ProposedSupplementGap, RequestCandidateSupplementArgs
        supplement = RequestCandidateSupplementArgs(effective_policy=submit.effective_policy, draft=proposal,
            proposed_gaps=(ProposedSupplementGap(source_gap_type="candidate_supply", date=None,
                target=ProposedCandidateCountTarget(role=ResearchRole.ATTRACTION, current_count=6, target_count=7),
                evidence_handles=("evidence-handle-0001",)),))
        operation.responses.append(response(supplement, "quality-supplement", "request_candidate_supplement"))
    elif case.endswith("stop"):
        operation.responses.append(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    else:
        repaired = day
        if case.endswith("patch") and not case.endswith("invalid_patch"):
            repaired = day.model_copy(update={"planned_breaks": (PlannedBreak(break_id="afternoon", kind="rest",
                start_at=at("14:00"), end_at=at("15:00"), reason="午后休息一小时"),)})
        elif case.endswith(("invalid_patch", "hard_loop")):
            repaired = day.model_copy(update={"planned_breaks": (PlannedBreak(break_id="overlap", kind="rest",
                start_at=at("10:30"), end_at=at("11:30"), reason="休息"),)})
        patch = SubmitItineraryArgs(effective_policy=submit.effective_policy,
            patch=ProposedItineraryPatch(days=(repaired,)))
        operation.responses.append(response(patch, "quality-patch"))
        if case.endswith("hard_loop"):
            operation.responses.append(response(patch, "quality-patch-repeat"))
        if case.endswith("invalid_patch"):
            operation.responses.append(ToolTurnCompleted(final_text="结束", metadata=_metadata()))

    if case.endswith("cancel"):
        with pytest.raises(asyncio.CancelledError):
            await service.plan_initial(request)
        return
    validations = []
    first_quality_days = []
    async def record_validation(identity, **values):
        validations.append(values)
        if values.get("code") == "daily_plan_quality_review":
            first_quality_days.extend(service._runs[request.operation_plan.run_id].draft.days)
    from app.llm_runtime.interaction_recording import InteractionCallIdentity
    with mock_patch.object(InteractionCallIdentity, "record_validation", record_validation):
        result = await service.plan_initial(request)
        if case.endswith(("supplement_resume", "supplement_stop")):
            from app.candidate_research import CandidateResearchV4Artifact as CandidateResearchArtifact, ResearchMode
            from app.itinerary_planning import ItineraryPlanningCandidateSupplementRequested, ItineraryPlanningResumeRequest
            from tests.unit.itinerary_planning.test_service_initial import _plan
            from tests.unit.itinerary_planning.test_service_resume import _supplemental_plan
            from app.itinerary_planning.service import _supplement_request, _supplement_request_digest
            assert isinstance(result, ItineraryPlanningCandidateSupplementRequested), result.model_dump(mode="json")
            awaiting = (await repository.get(result.planning_ref, expected_model=ItineraryPlanningArtifact)).content.record.state
            assert awaiting.quality_review_used and awaiting.draft.days[0].quality_issues
            run = service._runs[request.operation_plan.run_id]
            baseline = run.draft
            route_calls = len(service._map_adapter.directions_calls)
            matrix_calls = len(service._map_adapter.matrix_calls)
            candidate = (await repository.get(request.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
            # 补研未找到额外候选，合法基线及其质量标记仍须保留。
            actual_plan = _supplemental_plan(run.run_id)
            predecessor = (await repository.get(result.planning_ref, expected_model=ItineraryPlanningArtifact)).content
            successor = candidate.model_copy(update={"mode": ResearchMode.SUPPLEMENTAL,
                "predecessor_ref": request.candidate_set_ref,
                "invocation_request_digest": _supplement_request_digest(_supplement_request(predecessor, actual_plan))})
            successor = CandidateResearchArtifact.model_validate_json(successor.model_dump_json(), strict=True)
            from tests.support.itinerary_planning_fakes import FakeRepository
            _, encoded = FakeRepository._encoded(successor)
            successor_ref = request.candidate_set_ref.model_copy(update={"artifact_id": uuid4(),
                "operation_id": actual_plan.terminal_successor_operation_id,
                "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)})
            repository.seed(successor_ref, successor)
            resume_plan = _plan(run.run_id, suffix="quality_resume")
            resume_plan = resume_plan.model_copy(update={"invocation_slots": resume_plan.invocation_slots[run.invocation_count:]})
            resume = ItineraryPlanningResumeRequest(predecessor_planning_ref=result.planning_ref,
                supplemented_candidate_set_ref=successor_ref, supplemental_operation_plan=actual_plan,
                operation_plan=resume_plan)
            if case.endswith("supplement_stop"):
                operation.responses.append(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
            else:
                repaired = day.model_copy(update={"planned_breaks": (PlannedBreak(break_id="afternoon", kind="rest",
                    start_at=at("14:00"), end_at=at("15:00"), reason="午后休息一小时"),)})
                operation.responses.append(response(SubmitItineraryArgs(effective_policy=submit.effective_policy,
                    patch=ProposedItineraryPatch(days=(repaired,))), "quality-resumed-patch"))
            result = await service.plan_resume(resume)
            assert isinstance(result, ItineraryPlanningCompleted), getattr(result, "failure", result)
            assert run.quality_review_used and baseline.days[0].quality_issues
            assert run.draft.candidate_set_ref == successor_ref
            assert len(service._map_adapter.directions_calls) == route_calls
            assert len(service._map_adapter.matrix_calls) == matrix_calls
            assert result.budgets.model_invocations_consumed == awaiting.budgets.model_invocations_consumed + 1
    if case.endswith("technical"):
        assert isinstance(result, ItineraryPlanningFailed)
        assert result.failure.code.value == "operation_result_indeterminate"
        return
    assert isinstance(result, ItineraryPlanningCompleted), result.model_dump(mode="json")
    saved = (await repository.get(result.itinerary_ref, expected_model=ItineraryPlanningArtifact)).content
    run = service._runs[request.operation_plan.run_id]
    no_capacity = case.endswith("no_capacity")
    assert saved.schema_version == "8.0" and run.quality_review_used is not no_capacity
    assert len(saved.record.draft.days[0].visits) == 3
    issues = saved.record.draft.days[0].quality_issues
    repaired_quality = (case.endswith("patch") and not case.endswith("invalid_patch")) or case.endswith("supplement_resume")
    assert bool(issues) is not repaired_quality
    if multiday:
        assert service._runs[request.operation_plan.run_id].draft.days[1] == first_quality_days[1]
        assert any(item.rule == "unexplained_gap" and item.minutes >= 300 for item in saved.record.draft.days[1].quality_issues)
        assert saved.record.draft.days[0].planned_breaks
    feedbacks = [json.loads(results[0].content)["tool_result"] for results in operation.continuations]
    quality = [item for item in feedbacks if item.get("status") == "quality_review_requested"]
    assert len(quality) == (0 if no_capacity else 1)
    if not no_capacity:
        assert quality[0]["code"] == "daily_plan_quality_review"
        assert {item["field"] for item in quality[0]["issues"]} == ({"draft.days[0]", "draft.days[1]"} if multiday else {"draft.days[0]"})
        assert any(item.get("code") == "daily_plan_quality_review" and item["passed"] for item in validations)
    assert not any(item.get("code") == "daily_plan_quality_review" and not item["passed"] for item in validations)
    if case.endswith("short_split"):
        assert any(item["rule"] == "unexplained_gap" and item["minutes"] >= 360 for item in quality[0]["issues"])
    assert result.budgets.direction_calls_consumed == len(service._map_adapter.directions_calls) == (8 if multiday else 6)
    assert len(service._map_adapter.matrix_calls) == 1
    assert operation.call_count == result.budgets.model_invocations_consumed <= 8
    if case.endswith(("no_capacity", "budget_stop")):
        assert result.budgets.submit_repair_used == 5
    state_writes = [item for item in repository.put_requests if item.artifact_type == "itinerary_planning.itinerary"
        and item.content["record"]["record_type"] == "in_progress"]
    assert any(item.content["record"]["state"]["quality_review_used"] for item in state_writes) is not no_capacity
    if case.endswith(("supplement_resume", "supplement_stop")):
        assert await service.plan_resume(resume) == result
    else:
        assert await service.plan_initial(request) == result


async def exercise_direction_capacity(case, service, request, operation, repository, submit):
    """从公共入口耗尽真实槽位／账本，再验证质量基线的交付边界。"""
    from app.itinerary_planning.models import (
        DirectionLegIntent, PlanDirectionsArgs, ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
    )
    from app.workflow_contracts import OperationId, WorkflowPhase
    from tests.support.itinerary_planning_fakes import query_intents

    directions = operation.responses[2].calls[0]
    full_budget = case in {"budget_exhausted", "artifact_failure", "budget_read_failure"}
    extra_slots = 4 if full_budget or case == "queries_exhausted" else 1 if case == "partial_slots" else 0
    if extra_slots:
        plan = request.operation_plan
        extra_ids = tuple(OperationId(run_id=plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING,
            logical_step=f"capacity_extra_{index}") for index in range(extra_slots))
        request = request.model_copy(update={"operation_plan": plan.model_copy(update={
            "direction_operation_ids": (*plan.direction_operation_ids, *extra_ids)})})
    if full_budget:
        extra_legs = tuple(DirectionLegIntent(leg_id=f"capacity-extra-{index}",
            origin_handle=origin, destination_handle=destination,
            transport_semantic=directions.arguments.legs[0].transport_semantic,
            route_mode=directions.arguments.legs[0].route_mode, alternatives_limit=1)
            for index, (origin, destination) in enumerate((
                ("hotel-handle", "candidate-handle-0003"), ("candidate-handle-0003", "hotel-handle"),
                ("hotel-handle", "candidate-handle-0004"), ("candidate-handle-0004", "hotel-handle"))))
        directions = directions.model_copy(update={"arguments": PlanDirectionsArgs(
            legs=(*directions.arguments.legs, *extra_legs), daily_outline=directions.arguments.daily_outline,
            visit_intents=query_intents(submit.draft.days[0].date, tuple(f"candidate-handle-{index:04d}" for index in range(5))))})
        operation.responses[2] = operation.responses[2].model_copy(update={"calls": (directions,)})

    repair_legs = tuple(DirectionLegIntent(leg_id=f"capacity-repair-{index}", origin_handle="hotel-handle",
        destination_handle=handle, transport_semantic=directions.arguments.legs[0].transport_semantic,
        route_mode=directions.arguments.legs[0].route_mode, alternatives_limit=1)
        for index, handle in enumerate(("candidate-handle-0005", "candidate-handle-0004") if case == "partial_slots" else ("candidate-handle-0005",)))
    if case == "invalid_handle":
        repair_legs = (repair_legs[0].model_copy(update={"destination_handle": "candidate-handle-9999"}),)
    repair = ValidatedToolCall(call_id="capacity-repair", tool_name="plan_directions", arguments=PlanDirectionsArgs(
        legs=repair_legs, visit_intents=query_intents(submit.draft.days[0].date,
            ("candidate-handle-0005", "candidate-handle-0004") if case == "partial_slots" else ("candidate-handle-0005",))))
    calls = (repair,)
    if case == "other_plan_invalid":
        calls = (operation.responses[1].calls[0].model_copy(update={"call_id": "matrix-without-slot"}), repair)
    first_submit = submit
    if case == "no_quality_baseline":
        first_submit = submit.model_copy(update={"draft": submit.draft.model_copy(update={
            "arrival_transfer": submit.draft.arrival_transfer.model_copy(update={"route_leg":
                submit.draft.arrival_transfer.route_leg.model_copy(update={"start_at": at("08:20"), "end_at": at("08:30")})})})})
    operation.responses[3:] = [
        ToolCallsReady(calls=(ValidatedToolCall(call_id="capacity-first", tool_name="submit_itinerary", arguments=first_submit),), metadata=_metadata()),
        ToolCallsReady(calls=calls, metadata=_metadata()),
    ]

    if case == "partial_slots":
        operation.responses.append(ToolCallsReady(calls=(ValidatedToolCall(call_id="capacity-use-saved",
            tool_name="submit_itinerary", arguments=submit),), metadata=_metadata()))

    validations, tool_results, baseline_days = [], [], []
    fail_reads = False
    original_budget = service._budget
    if case == "budget_read_failure":
        class BudgetWithReadFailure:
            async def consume(self, request):
                return await original_budget.consume(request)

            async def get_snapshot(self, *, key):
                if fail_reads and key == ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY:
                    raise RuntimeError("synthetic ledger read failure")
                return await original_budget.get_snapshot(key=key)

        service._budget = BudgetWithReadFailure()

    async def record_validation(identity, **values):
        nonlocal fail_reads
        validations.append(values)
        if values.get("code") == "daily_plan_quality_review":
            baseline_days.extend(service._runs[request.operation_plan.run_id].draft.days)
            fail_reads = case == "budget_read_failure"
            if case == "queries_exhausted":
                from app.workflow_contracts import BudgetConsumeRequest
                for index in range(4):
                    await original_budget.consume(BudgetConsumeRequest(operation_id=OperationId(
                        run_id=request.operation_plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING,
                        logical_step=f"separately_reserved_{index}"), key=ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY, amount=1))
        if values.get("code") == "direction_capacity_exhausted" and case == "artifact_failure":
            repository.fail_put_for.add("itinerary_planning.itinerary")

    async def record_tool_result(identity, **values):
        tool_results.append(values)

    from app.llm_runtime.interaction_recording import InteractionCallIdentity
    with mock_patch.object(InteractionCallIdentity, "record_validation", record_validation), mock_patch.object(
        InteractionCallIdentity, "record_tool_result", record_tool_result):
        result = await service.plan_initial(request)

    run = service._runs[request.operation_plan.run_id]
    expected_calls = 10 if full_budget else 6
    assert len(service._map_adapter.directions_calls) == expected_calls
    assert len(service._map_adapter.matrix_calls) == 1
    assert operation.call_count == result.budgets.model_invocations_consumed == (
        4 if case == "budget_read_failure" else 6 if case == "partial_slots" else 5)
    assert result.budgets.direction_call_limit == 10
    assert run.quality_review_used is (case != "no_quality_baseline")
    if case in {"budget_exhausted", "queries_exhausted", "slots_exhausted", "partial_slots"}:
        assert isinstance(result, ItineraryPlanningCompleted), result.model_dump(mode="json")
        saved = (await repository.get(result.itinerary_ref, expected_model=ItineraryPlanningArtifact)).content
        assert saved.record.draft.days[0].quality_issues
        assert _from_draft_snapshot(saved.record.draft).days == tuple(baseline_days)
        assert result.budgets.direction_calls_consumed == (10 if case == "queries_exhausted" else expected_calls)
        assert result.budgets.budget_snapshot_complete
        assert result.budgets.submit_repair_used == (3 if case == "partial_slots" else 2)
        assert len([item for item in validations if item.get("code") == "daily_plan_quality_review"]) == 1
        assert any(item.get("code") == "direction_capacity_exhausted" and not item["passed"] for item in validations)
        refused = [item for item in tool_results if item.get("provider_tool_call_id") == "capacity-repair"]
        assert len(refused) == 1 and refused[0]["outcome"] == "rejected"
        capacity = json.loads(refused[0]["feedback"])
        assert capacity["reason"] == "direction_capacity_exhausted"
        assert capacity["remaining_direction_queries"] == (0 if full_budget or case == "queries_exhausted" else 4)
        assert capacity["remaining_direction_slots"] == (4 if case == "queries_exhausted" else 1 if case == "partial_slots" else 0)
    elif case == "no_quality_baseline":
        assert result.outcome_type == "incomplete" and result.itinerary_ref is None
        assert result.stop_reasons[0].value == "direction_budget_exhausted"
    else:
        assert isinstance(result, ItineraryPlanningFailed), result.model_dump(mode="json")
        expected_failure = {"artifact_failure": "artifact_error", "budget_read_failure": "budget_error"}.get(case, "action_batch_invalid")
        assert result.failure.code.value == expected_failure
    replay = await service.plan_initial(request)
    if case == "artifact_failure":
        assert replay.outcome_type == "failed" and replay.failure.code.value == "operation_result_indeterminate"
    else:
        assert replay == result
    assert len(service._map_adapter.directions_calls) == expected_calls
