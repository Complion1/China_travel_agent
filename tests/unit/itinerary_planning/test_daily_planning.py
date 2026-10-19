"""每日统一规划：前置完整安排、局部替换和长空日交付边界。"""
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.itinerary_planning.models import (
    DailyPlanOutline, PlannedBreak, PurposeVisitIntent, TimeInterval, VisitKind, PlanDirectionsArgs,
    ProposedItineraryPatch, SubmitItineraryArgs, PlanningDayKind,
)
from app.itinerary_planning.quality import (
    assess_day_quality, boundary_day_windows, unplanned_day_issues,
)
from app.requirements import TravelPace
from app.llm_runtime import ToolCallsReady, ToolTurnCompleted, ValidatedToolCall
from tests.unit.itinerary_planning.test_boundary_quality import at, quality_case
from tests.unit.itinerary_planning.test_service_initial import _metadata


def visit(handle, kind, start, end, *, day=date(2026, 9, 3)):
    shift = timedelta(days=(day - date(2026, 9, 3)).days)
    return PurposeVisitIntent(date=day, candidate_handle=handle, visit_kind=kind, is_backup=False,
        interval=TimeInterval(start_at=at(start) + shift, end_at=at(end) + shift))


def test_equal_windows_apply_equal_quality_rules_regardless_of_boundary_label():
    confirmed, draft = quality_case()
    window, = boundary_day_windows(confirmed, draft=draft)
    outline = DailyPlanOutline(date=window.date, visits=(visit("candidate-handle-0000", VisitKind.ATTRACTION, "10:00", "11:00"),))
    for pace in TravelPace:
        assert assess_day_quality(outline, window, pace) == assess_day_quality(
            outline, replace(window, affected_by=()), pace)


def test_reasonable_evening_finish_does_not_have_to_fill_the_window_until_22():
    confirmed, draft = quality_case()
    window, = boundary_day_windows(confirmed, draft=draft)
    window = replace(window, start_at=at("07:30"), end_at=at("22:00"))
    outline = DailyPlanOutline(date=window.date, visits=(
        visit("candidate-handle-0000", VisitKind.ATTRACTION, "10:00", "12:00"),
        visit("candidate-handle-0001", VisitKind.LUNCH, "12:00", "13:15"),
        visit("candidate-handle-0002", VisitKind.ATTRACTION, "14:00", "16:00"),
        visit("candidate-handle-0003", VisitKind.DINNER, "17:30", "18:30"),
    ))
    assert (window.end_at - outline.visits[-1].interval.end_at).total_seconds() == 210 * 60
    assert not assess_day_quality(outline, window, TravelPace.NORMAL)


def test_long_empty_day_is_not_excused_by_breaks_but_short_empty_window_is_legal():
    confirmed, draft = quality_case()
    window, = boundary_day_windows(confirmed, draft=draft)
    empty = DailyPlanOutline(date=window.date, visits=(), planned_breaks=(
        PlannedBreak(break_id="whole-day", kind="free_time", start_at=at("10:00"), end_at=at("18:00"), reason="自由活动"),))
    assert unplanned_day_issues((empty,), (window,), TravelPace.NORMAL)[0]["rule"] == "unplanned_usable_day"
    short = replace(window, end_at=window.start_at + timedelta(minutes=90))
    assert not unplanned_day_issues((empty,), (short,), TravelPace.NORMAL)


def test_no_candidate_does_not_force_research_when_even_the_maximum_window_is_empty():
    from app.itinerary_planning.admission import supply_shortages
    confirmed, _ = quality_case(arrival="23:00", departure="23:59")
    assert supply_shortages((), confirmed, ()) == ()


@pytest.mark.parametrize("legacy_version", ("6.0", "7.0"))
def test_current_resume_request_rejects_legacy_nonterminal_sources(legacy_version):
    from app.itinerary_planning import ItineraryPlanningResumeRequest
    from app.workflow_contracts import WorkflowPhase
    from tests.unit.itinerary_planning.test_service_initial import _fixture, _ref
    from tests.unit.itinerary_planning.test_service_resume import _supplemental_plan
    _, request, *_ = _fixture()
    legacy = _ref(request.operation_plan.run_id, "itinerary_planning.itinerary",
        WorkflowPhase.ITINERARY_PLANNING, "legacy-nonterminal", "a").model_copy(update={"schema_version": legacy_version})
    with pytest.raises(ValidationError):
        ItineraryPlanningResumeRequest(predecessor_planning_ref=legacy,
            supplemented_candidate_set_ref=request.candidate_set_ref,
            supplemental_operation_plan=_supplemental_plan(request.operation_plan.run_id),
            operation_plan=request.operation_plan)


@pytest.mark.parametrize("kind", (PlanningDayKind.FULL, PlanningDayKind.PARTIAL))
def test_every_day_obeys_the_same_intracity_transport_limit(kind):
    from tests.unit.itinerary_planning.test_models import _day_with_visits
    from app.itinerary_planning.policy import validate_route_capacity
    day = _day_with_visits(2)
    day = day.model_copy(update={"day_kind": kind, "route_legs": tuple(
        leg.model_copy(update={"duration_s": 4000}) for leg in day.route_legs)})
    with pytest.raises(ValueError, match="daily route total"):
        validate_route_capacity(day, TravelPace.NORMAL)


@pytest.mark.parametrize("change", ({"is_backup": True}, {"interval": None}))
def test_outline_requires_dated_main_visit_intervals(change):
    item = visit("candidate-handle-0000", VisitKind.ATTRACTION, "10:00", "11:00").model_copy(update=change)
    with pytest.raises(ValidationError):
        DailyPlanOutline(date=item.date, visits=(item,))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", (
    "missing_last", "empty_last", "break_only_last", "duplicate_last", "missing_must", "boundary_pending", "rejected_preserves",
    "partial_update", "final_empty_last", "final_missing_last", "merged_multiday",
))
async def test_public_daily_planning(case):
    from tests.unit.itinerary_planning.test_service_initial import test_service_completed_requires_main_plan_and_valid_optional_backups
    await test_service_completed_requires_main_plan_and_valid_optional_backups("daily_unified_" + case)


async def exercise_daily_planning(case, service, request, operation, repository, submit):
    from app.itinerary_planning import ItineraryPlanningArtifact
    from app.itinerary_planning.models import DirectionLegIntent, TransportSemantic
    from app.baidu_map import TravelMode
    from app.workflow_contracts import OperationId, WorkflowPhase
    import json

    case = case.removeprefix("daily_unified_")
    daily = operation.responses[2].calls[0]
    initial = daily.arguments.daily_outline
    assert len(initial) == 2
    last_date = initial[1].date
    terminal_text = ToolTurnCompleted(final_text="结束", metadata=_metadata())
    expected_directions = 2
    expected_rule = None

    def response(call):
        return ToolCallsReady(calls=(call,), metadata=_metadata())

    if case == "merged_multiday":
        template = daily.arguments.legs[0]
        merged_legs = tuple(template.model_copy(update={
            "leg_id": f"merged-day-{index}-{side}",
            "origin_handle": "hotel-handle" if side == "out" else day.visits[0].candidate_handle,
            "destination_handle": day.visits[0].candidate_handle if side == "out" else "hotel-handle",
        }) for index, day in enumerate(initial) for side in ("out", "back"))
        args = PlanDirectionsArgs(daily_outline=initial, legs=merged_legs,
            visit_intents=tuple(day.visits[0] for day in initial))
        operation.responses[2] = response(daily.model_copy(update={"arguments": args}))
        operation.responses[3:] = [terminal_text]
        expected_directions = 6
    elif case == "boundary_pending":
        operation.responses[1] = operation.responses[1].model_copy(update={"calls": operation.responses[1].calls[:1]})
        operation.responses[3:] = [terminal_text]
        expected_directions = 0
        expected_rule = "boundary_routes_required"
    elif case in {"missing_last", "empty_last", "break_only_last", "duplicate_last"}:
        if case == "missing_last":
            replacement = initial[:1]
            expected_rule = "outline_dates_incomplete"
        elif case == "duplicate_last":
            replacement = (initial[0], DailyPlanOutline(date=last_date, visits=(
                visit("candidate-handle-0000", VisitKind.ATTRACTION, "10:00", "11:00", day=last_date),)))
            expected_rule = "candidate_duplicate"
        else:
            breaks = ()
            if case == "break_only_last":
                breaks = (PlannedBreak(break_id="all-afternoon", kind="free_time",
                    start_at=at("10:00") + timedelta(days=1), end_at=at("17:00") + timedelta(days=1), reason="自由活动"),)
            replacement = (initial[0], initial[1].model_copy(update={"visits": (), "planned_breaks": breaks}))
            expected_rule = "unplanned_usable_day"
        operation.responses[2] = response(daily.model_copy(update={"arguments": daily.arguments.model_copy(update={"daily_outline": replacement})}))
        operation.responses[3:] = [terminal_text]
    elif case == "missing_must":
        outline = tuple(day.model_copy(update={"visits": tuple(
            item.model_copy(update={"must_group_ids": ()}) for item in day.visits)}) for day in initial)
        operation.responses[2] = response(daily.model_copy(update={"arguments": daily.arguments.model_copy(update={"daily_outline": outline})}))
        operation.responses[3:] = [terminal_text]
        expected_rule = "must_visit_unscheduled"
    elif case in {"rejected_preserves", "partial_update"}:
        expected_directions = 6
        if case == "rejected_preserves":
            patch = initial[1].model_copy(update={"visits": ()})
            request_call = daily.model_copy(update={"call_id": "bad-outline-patch",
                "arguments": daily.arguments.model_copy(update={"daily_outline": (patch,)})})
            operation.responses[3:] = [response(request_call), terminal_text]
            expected_rule = "unplanned_usable_day"
        else:
            replacement = DailyPlanOutline(date=last_date, visits=(
                visit("candidate-handle-0003", VisitKind.ATTRACTION, "11:00", "12:00", day=last_date),))
            plan = request.operation_plan
            spare = OperationId(run_id=plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="outline_local_query")
            request = request.model_copy(update={"operation_plan": plan.model_copy(update={"direction_operation_ids": (*plan.direction_operation_ids, spare)})})
            args = PlanDirectionsArgs(daily_outline=(replacement,), visit_intents=replacement.visits, legs=(
                DirectionLegIntent(leg_id="outline-local-route", origin_handle="hotel-handle", destination_handle="candidate-handle-0003",
                    transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1),))
            operation.responses[3:] = [response(ValidatedToolCall(call_id="outline-local", tool_name="plan_directions", arguments=args)), terminal_text]
            expected_directions = 7
    else:
        expected_directions = 6
        if case == "final_empty_last":
            empty = submit.draft.days[0].model_copy(update={"date": last_date, "day_kind": PlanningDayKind.TRANSIT_ONLY,
                "open_intervals": (), "visits": (), "route_legs": (), "backups": (), "planned_breaks": ()})
            proposal = submit.draft.model_copy(update={"days": (*submit.draft.days, empty)})
            args = SubmitItineraryArgs(effective_policy=submit.effective_policy, draft=proposal)
            operation.responses[3:] = [response(ValidatedToolCall(call_id="final-empty", tool_name="submit_itinerary", arguments=args)), terminal_text]
            expected_rule = "unplanned_usable_day"
        else:
            operation.responses[4:] = [terminal_text]

    result = await service.plan_initial(request)
    assert result.outcome_type == "incomplete", result.outcome_type
    assert len(service._map_adapter.directions_calls) == result.budgets.direction_calls_consumed == expected_directions
    if case == "merged_multiday":
        from app.itinerary_planning.prompt_loader import load_planning_prompt
        assert result.budgets.model_invocations_consumed == operation.call_count == 4
        states = [item.content["record"]["state"] for item in repository.put_requests
            if item.artifact_type == "itinerary_planning.itinerary" and item.content["record"]["record_type"] == "in_progress"]
        stored_outline = states[-1]["daily_outline"]
        assert [day["date"] for day in stored_outline] == [day.date.isoformat() for day in initial]
        for stored, expected in zip(stored_outline, initial, strict=True):
            assert len(stored["visits"]) == len(expected.visits)
            for actual, intent in zip(stored["visits"], expected.visits, strict=True):
                assert actual["candidate_handle"] == intent.candidate_handle
                assert actual["visit_kind"] == intent.visit_kind.value
                assert datetime.fromisoformat(actual["interval"]["start_at_utc"]) == intent.interval.start_at
                assert datetime.fromisoformat(actual["interval"]["end_at_utc"]) == intent.interval.end_at
            assert stored["planned_breaks"] == []
        actions = [item.content for item in repository.put_requests if item.artifact_type == "itinerary_planning.model_action"]
        route_action = actions[2]
        assert route_action["disposition"] == "accepted"
        assert len(route_action["call_summaries"]) == 1
        assert route_action["call_summaries"][0]["tool"] == "plan_directions"
        assert all(item["prompt_version"] == "planning-v12" and item["prompt_hash"] == load_planning_prompt().sha256 for item in actions)
        observations = [item.content for item in repository.put_requests if item.artifact_type == "itinerary_planning.directions_observation"]
        feedback = json.loads(operation.continuations[-1][0].content)["tool_result"]
        assert [item["leg_id"] for item in feedback["observation"]["observations"]] == [leg.leg_id for leg in merged_legs]
        assert [item["leg_ordinal"] for item in observations[-4:]] == [0, 1, 2, 3]
        assert len(observations) == 6
        assert await service.plan_initial(request) == result
        assert len(service._map_adapter.directions_calls) == 6
        assert operation.call_count == 4
        return
    run = service._runs[request.operation_plan.run_id]
    if expected_rule:
        feedback = json.loads(operation.continuations[-1][0].content)["tool_result"]
        assert feedback.get("reason") == expected_rule or any(item["rule"] == expected_rule for item in feedback.get("issues", ()))
        if case == "duplicate_last":
            assert feedback["issues"][0]["field"] == "daily_outline[1].visits[0].candidate_handle"
        if case == "missing_must":
            assert feedback["issues"][0]["field"] == "daily_outline"
    if case in {"missing_last", "empty_last", "break_only_last", "duplicate_last", "missing_must", "boundary_pending"}:
        assert run.daily_outline == ()
    elif case == "partial_update":
        assert run.daily_outline == (initial[0], replacement)
        assert any(route["existing_directions"] for route in service._required_daily_routes(run))
    else:
        assert run.daily_outline == initial
    saved = [item for item in repository.put_requests if item.artifact_type == "itinerary_planning.itinerary"]
    assert saved[-1].content["record"]["record_type"] == "incomplete"
    assert all(item.schema_version == "8.0" for item in saved)
    assert await service.plan_initial(request) == result
    assert len(service._map_adapter.directions_calls) == expected_directions
