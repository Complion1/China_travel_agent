"""分钟排程、持久时间校验和可用于纠错的具体反馈。"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.itinerary_planning import ItineraryPlanningUsageError, RouteTimeInterval, TimeInterval, VisitKind
from app.itinerary_planning.artifacts import _from_route_snapshot, _to_route_snapshot
from app.itinerary_planning.policy import VISIT_DURATION_BOUNDS, ceil_to_minute, route_scheduled_minutes, validate_visit_duration
from app.itinerary_planning.timing import day_time_issues
from app.requirements import TravelPace
from tests.unit.itinerary_planning.test_models import _candidate, _day_with_visits, _leg

SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize("minute", range(60))
def test_every_minute_is_legal_and_round_trips(minute):
    start = datetime(2026, 9, 3, 10, minute, tzinfo=SHANGHAI)
    interval = TimeInterval(start_at=start, end_at=start + timedelta(minutes=75))
    assert TimeInterval.model_validate_json(interval.model_dump_json(), strict=True) == interval


@pytest.mark.parametrize("seconds,expected", [
    (0, 0), (1, 10), (59, 10), (60, 10), (61, 10), (599, 10),
    (600, 10), (601, 20), (1801, 40), (2580, 50), (3000, 50), (3001, 60),
])
def test_route_duration_and_snapshot_use_the_ten_minute_ceiling(seconds, expected):
    start = datetime(2026, 9, 3, 23, 47, tzinfo=SHANGHAI)
    assert route_scheduled_minutes(seconds) == expected
    leg = _leg({"ref_type": "hotel", "hotel_selection_ref_index": 0}, _candidate(),
        start, start + timedelta(minutes=expected), seconds=seconds)
    snapshot = _to_route_snapshot(leg)
    assert snapshot.schema_version == "3.0" and snapshot.interval.schema_version == "2.0"
    assert snapshot.scheduled_minutes == expected
    assert _from_route_snapshot(snapshot) == leg
    raw = snapshot.model_dump(mode="json")
    raw["schema_version"] = "1.0"
    with pytest.raises(ValueError):
        type(snapshot).model_validate_json(__import__("json").dumps(raw), strict=True)


@pytest.mark.parametrize("model", [TimeInterval, RouteTimeInterval])
@pytest.mark.parametrize("extra", [dict(second=1), dict(microsecond=1)])
def test_subminute_times_are_rejected(model, extra):
    start = datetime(2026, 9, 3, 10, 37, tzinfo=SHANGHAI).replace(**extra)
    with pytest.raises(ValueError):
        model(start_at=start, end_at=start + timedelta(minutes=75))


def test_earliest_start_rounds_up_without_losing_seconds_or_midnight():
    value = datetime(2026, 9, 3, 23, 59, 1, tzinfo=SHANGHAI)
    assert ceil_to_minute(value) == datetime(2026, 9, 4, 0, 0, tzinfo=SHANGHAI)
    assert ceil_to_minute(value.replace(second=0)) == value.replace(second=0)


def test_non_grid_overlap_has_an_exact_field_and_boundary():
    day = _day_with_visits(2)
    visits = list(day.visits)
    visits[1] = visits[1].model_copy(update={"interval": TimeInterval(
        start_at=datetime(2026, 9, 3, 11, 45, tzinfo=SHANGHAI),
        end_at=datetime(2026, 9, 3, 13, 0, tzinfo=SHANGHAI))})
    routes = list(day.route_legs)
    start = datetime(2026, 9, 3, 11, 35, tzinfo=SHANGHAI)
    routes[1] = _leg(visits[0].location, visits[1].location, start,
        start + timedelta(minutes=20), seconds=1020)
    issues = day_time_issues(day, field="draft.days[0]", visits=visits, routes=routes, pace=TravelPace.RELAXED)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["rule"] == "route_visit_overlap"
    assert issue["field"] == "draft.days[0].visits[1].start_at"
    assert issue["actual"] == "2026-09-03T11:45:00+08:00"
    assert issue["expected"] == {"not_before": "2026-09-03T11:55:00+08:00"}
    assert visits[1].visit_id not in issue["target"]
    visits[1] = visits[1].model_copy(update={"interval": TimeInterval(
        start_at=datetime(2026, 9, 3, 11, 55, tzinfo=SHANGHAI),
        end_at=datetime(2026, 9, 3, 13, 0, tzinfo=SHANGHAI))})
    assert not day_time_issues(day, field="draft.days[0]", visits=visits, routes=routes, pace=TravelPace.RELAXED)


@pytest.mark.parametrize("kind", list(VisitKind))
@pytest.mark.parametrize("boundary", ["below", "minimum", "after_minimum", "before_maximum", "maximum", "above"])
def test_all_visit_kinds_keep_minute_duration_bounds(kind, boundary):
    from tests.unit.itinerary_planning.test_models import _visit

    lower, upper = VISIT_DURATION_BOUNDS[kind]
    minutes = {"below": lower - 1, "minimum": lower, "after_minimum": lower + 1,
        "before_maximum": upper - 1, "maximum": upper, "above": upper + 1}[boundary]
    start = datetime(2026, 9, 3, 10, 7, tzinfo=SHANGHAI)
    visit = _visit().model_copy(update={"visit_kind": kind, "interval": TimeInterval(
        start_at=start, end_at=start + timedelta(minutes=minutes))})
    if boundary in {"below", "above"}:
        with pytest.raises(ValueError):
            validate_visit_duration(visit)
    else:
        validate_visit_duration(visit)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["request", "plan", "slot", "all", "mixed"])
async def test_old_or_mixed_requests_are_rejected_before_any_side_effect(level):
    from tests.unit.itinerary_planning.test_service_initial import _fixture

    service, request, runtime, operation, repository = _fixture()
    before = await service._budget.get_all_snapshots()
    plan = request.operation_plan
    if level in {"slot", "all", "mixed"}:
        slot = plan.invocation_slots[0].model_copy(update={"schema_version": "3.0"})
        plan = plan.model_copy(update={"invocation_slots": (slot, *plan.invocation_slots[1:])})
    if level in {"plan", "all", "mixed"}:
        plan = plan.model_copy(update={"schema_version": "4.0"})
    request = request.model_copy(update={"operation_plan": plan})
    if level in {"request", "all"}:
        request = request.model_copy(update={"schema_version": "4.0"})
    with pytest.raises(ItineraryPlanningUsageError):
        await service.plan_initial(request)
    assert runtime.start_count == operation.call_count == 0
    assert not service._map_adapter.geocode_calls
    assert not service._map_adapter.matrix_calls
    assert not service._map_adapter.directions_calls
    assert not repository.lookup_calls and not repository.put_requests
    assert await service._budget.get_all_snapshots() == before
    await service.aclose()


def test_current_failure_accepts_action_v11_and_rejects_v10():
    from app.itinerary_planning import ControlPlanningFailure, ItineraryPlanningFailed, PlanningFailureCode
    from tests.contract.itinerary_planning.test_artifact_registration import _directories

    ref = _directories().model_action_refs[0].model_copy(update={"schema_version": "11.0"})
    failure = ControlPlanningFailure(code=PlanningFailureCode.MODEL_CONTRACT_INVALID, retryable=False, possible_duplicate=False)
    result = ItineraryPlanningFailed(failure=failure, last_consistent_itinerary_ref=None,
        failure_artifact_ref=ref, budgets=None, retryable=False, possible_duplicate=False)
    assert result.failure_artifact_ref.schema_version == "11.0"
    with pytest.raises(ValueError):
        ItineraryPlanningFailed(failure=failure, last_consistent_itinerary_ref=None,
            failure_artifact_ref=ref.model_copy(update={"schema_version": "8.0"}), budgets=None,
            retryable=False, possible_duplicate=False)


def test_old_minute_reservation_and_old_route_version_are_rejected():
    start = datetime(2026, 9, 3, 10, 7, tzinfo=SHANGHAI)
    leg = _leg({"ref_type": "hotel", "hotel_selection_ref_index": 0}, _candidate(),
        start, start + timedelta(minutes=50), seconds=2580)
    raw = leg.model_dump(mode="python")
    with pytest.raises(ValueError):
        type(leg).model_validate({**raw, "schema_version": "2.0"}, strict=True)
    with pytest.raises(ValueError):
        type(leg).model_validate({**raw, "scheduled_minutes": 43,
            "interval": RouteTimeInterval(start_at=start, end_at=start + timedelta(minutes=43))}, strict=True)


def test_confirmed_activity_duration_does_not_use_transport_reservation():
    from tests.unit.itinerary_planning.test_models import _visit

    start = datetime(2026, 9, 3, 10, 7, tzinfo=SHANGHAI)
    visit = _visit().model_copy(update={"duration_source": "user_explicit",
        "interval": TimeInterval(start_at=start, end_at=start + timedelta(minutes=75))})
    validate_visit_duration(visit, user_duration_minutes=75)
    with pytest.raises(ValueError):
        validate_visit_duration(visit, user_duration_minutes=80)
