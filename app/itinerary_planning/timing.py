"""既有提交校验的时间问题定位；不修改或生成排程。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.requirements import HubType, TravelPace
from app.requirements.models import TravelTimeWindow

from .models import PlanningDayKind, RouteLeg
from .policy import (
    FULL_DAY_TRANSPORT_LIMIT_SECONDS, MEAL_START_WINDOWS, SHANGHAI,
    SINGLE_LEG_LIMIT_SECONDS, VISIT_DURATION_BOUNDS, boundary_processing_minutes,
    default_open_interval,
)


def time_issue(rule: str, field: str, actual: object, expected: object, *, day: date):
    """目标由日期和结构字段决定，不采用模型填写的标识。"""
    return dict(
        target=f"{day.isoformat()}:main:time:{rule}:{field}", rule=rule, field=field,
        candidate_handle=None, actual=actual, expected=expected, evidence_handles=[],
        allowed_actions=["patch_draft", "query_route"],
    )


def _span(interval):
    return [interval.start_at.isoformat(), interval.end_at.isoformat()]


def endpoint_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def boundary_time_issues(
    *, window: TravelTimeWindow, arrival: RouteLeg, departure: RouteLeg,
    days: tuple[tuple[date, tuple[RouteLeg, ...]], ...], pace: TravelPace,
):
    """汇总已绑定路线证据的抵离余量和跨日衔接问题。"""
    issues = []
    endpoints = (("arrival", window.arrival, arrival), ("departure", window.departure, departure))
    for kind, endpoint, route in endpoints:
        field = f"draft.{kind}_transfer.route_leg.start_at"
        endpoint_at = endpoint_datetime(endpoint.local_datetime)
        processing = boundary_processing_minutes(
            endpoint.hub_type or HubType.RAILWAY_STATION, kind, is_approximate=endpoint.is_approximate,
        ) + (30 if endpoint.is_approximate else 0)
        if endpoint_at is not None:
            if kind == "arrival":
                earliest = endpoint_at + timedelta(minutes=processing)
                if route.interval.start_at < earliest:
                    issues.append(time_issue("arrival_processing_overlap", field,
                        route.interval.start_at.isoformat(), dict(not_before=earliest.isoformat()), day=endpoint_at.date()))
            else:
                latest = endpoint_at - timedelta(minutes=processing)
                if route.interval.end_at > latest:
                    latest_start = latest - timedelta(minutes=route.scheduled_minutes)
                    issues.append(time_issue("departure_processing_overlap", field,
                        route.interval.start_at.isoformat(), dict(not_after=latest_start.isoformat()), day=endpoint_at.date()))
        if route.duration_s > SINGLE_LEG_LIMIT_SECONDS[pace]:
            issues.append(time_issue("single_leg_capacity", f"draft.{kind}_transfer.route_leg",
                route.duration_s, dict(at_most=SINGLE_LEG_LIMIT_SECONDS[pace]), day=route.interval.start_at.date()))

    arrival_end = arrival.interval.end_at
    departure_start = departure.interval.start_at
    if arrival_end > departure_start:
        issues.append(time_issue("boundary_transfers_overlap", "draft.arrival_transfer.route_leg.end_at",
            arrival_end.isoformat(), dict(not_after=departure_start.isoformat()), day=arrival_end.date()))
    previous_return = arrival_end
    for index, (day, routes) in enumerate(days):
        if not routes:
            continue
        if routes[0].interval.start_at < previous_return:
            issues.append(time_issue("day_route_boundary_overlap", f"draft.days[{index}].route_legs[0].start_at",
                routes[0].interval.start_at.isoformat(), dict(not_before=previous_return.isoformat()), day=day))
        if routes[-1].interval.end_at > departure_start:
            latest_start = departure_start - timedelta(minutes=routes[-1].scheduled_minutes)
            issues.append(time_issue("day_route_boundary_overlap", f"draft.days[{index}].route_legs[{len(routes)-1}].start_at",
                routes[-1].interval.start_at.isoformat(), dict(not_after=latest_start.isoformat()), day=day))
        previous_return = routes[-1].interval.end_at
    return tuple(issues)


def day_time_issues(day, *, field: str, visits, routes, pace):
    """在组装DayPlan前汇总能独立确定的时间冲突。"""
    issues = []

    def add(rule, path, actual, expected):
        issues.append(time_issue(rule, f"{field}.{path}", actual, expected, day=day.date))

    default = default_open_interval(day.date)
    groups = (("open_intervals", day.open_intervals), ("visits", visits), ("route_legs", routes))
    for name, values in groups:
        for index, item in enumerate(values):
            interval = item if name == "open_intervals" else item.interval
            path = f"{name}[{index}]"
            if interval.start_at < default.start_at or interval.end_at > default.end_at:
                add("default_daily_window", path, _span(interval), _span(default))
            if name == "open_intervals" or (name == "route_legs" and interval.start_at == interval.end_at):
                continue
            if not any(window.start_at <= interval.start_at and interval.end_at <= window.end_at
                       for window in day.open_intervals):
                add("outside_open_intervals", path, _span(interval), [_span(value) for value in day.open_intervals])

    for index, visit in enumerate(visits):
        minutes = int((visit.interval.end_at - visit.interval.start_at).total_seconds() // 60)
        lower, upper = VISIT_DURATION_BOUNDS[visit.visit_kind]
        if visit.duration_source == "planning_estimate" and not lower <= minutes <= upper:
            add("visit_duration_out_of_bounds", f"visits[{index}].end_at", minutes, [lower, upper])
        window = MEAL_START_WINDOWS.get(visit.visit_kind)
        if window is not None:
            lower, upper = (datetime.combine(day.date, value, tzinfo=SHANGHAI) for value in window)
            if not lower <= visit.interval.start_at <= upper:
                add("meal_start_window", f"visits[{index}].start_at",
                    visit.interval.start_at.isoformat(), [lower.isoformat(), upper.isoformat()])
    for index, (left, right) in enumerate(zip(visits, visits[1:])):
        if left.interval.end_at > right.interval.start_at:
            add("visit_overlap", f"visits[{index + 1}].start_at",
                right.interval.start_at.isoformat(), dict(not_before=left.interval.end_at.isoformat()))
    if len(routes) == len(visits) + 1 and visits:
        for index, route in enumerate(routes):
            if index and route.interval.start_at < visits[index - 1].interval.end_at:
                add("route_visit_overlap", f"route_legs[{index}].start_at",
                    route.interval.start_at.isoformat(), dict(not_before=visits[index - 1].interval.end_at.isoformat()))
            if index < len(visits) and route.interval.end_at > visits[index].interval.start_at:
                add("route_visit_overlap", f"visits[{index}].start_at",
                    visits[index].interval.start_at.isoformat(), dict(not_before=route.interval.end_at.isoformat()))
    for index, route in enumerate(routes):
        if route.duration_s > SINGLE_LEG_LIMIT_SECONDS[pace]:
            add("single_leg_capacity", f"route_legs[{index}]", route.duration_s,
                dict(at_most=SINGLE_LEG_LIMIT_SECONDS[pace]))
    total = sum(route.duration_s for route in routes)
    if day.day_kind is PlanningDayKind.FULL and total > FULL_DAY_TRANSPORT_LIMIT_SECONDS[pace]:
        add("full_day_transport_capacity", "route_legs", total, dict(at_most=FULL_DAY_TRANSPORT_LIMIT_SECONDS[pace]))
    return tuple(issues)
