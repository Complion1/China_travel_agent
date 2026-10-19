"""计算每日可用时间和质量提醒，不替模型选择或安排活动。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.requirements import ConfirmedTravelRequirement, TravelPace

from .models import BoundaryDayQualityIssue, ItineraryDraft, VisitKind
from .policy import (
    MEAL_START_WINDOWS, SHANGHAI, boundary_processing_minutes, ceil_to_minute,
    default_open_interval, route_scheduled_minutes,
)


IDLE_THRESHOLDS = {TravelPace.RELAXED: 240, TravelPace.NORMAL: 180, TravelPace.INTENSE: 120}


def _endpoint_at(endpoint) -> datetime:
    return datetime.fromisoformat(endpoint.local_datetime).replace(tzinfo=SHANGHAI)


def _floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _processing(endpoint, kind: str) -> int:
    return boundary_processing_minutes(endpoint.hub_type, kind, is_approximate=endpoint.is_approximate) + (
        30 if endpoint.is_approximate else 0
    )


@dataclass(frozen=True)
class BoundaryDayWindow:
    date: date
    start_at: datetime
    end_at: datetime
    affected_by: tuple[str, ...]
    route_pending: bool
    arrival_processing_minutes: int
    departure_processing_minutes: int
    arrival_route: tuple[int, int] | None
    departure_route: tuple[int, int] | None

    def projection(self) -> dict:
        default = default_open_interval(self.date)
        return dict(date=self.date.isoformat(), affected_by=self.affected_by,
            default_start_at=default.start_at.isoformat(), default_end_at=default.end_at.isoformat(),
            start_at=self.start_at.isoformat(), end_at=self.end_at.isoformat(),
            available_minutes=max(0, int((self.end_at - self.start_at).total_seconds() // 60)),
            status="route_pending" if self.route_pending else "verified_route_window",
            arrival_processing_minutes=self.arrival_processing_minutes,
            departure_processing_minutes=self.departure_processing_minutes,
            arrival_route_selection=self.arrival_route, departure_route_selection=self.departure_route)


def boundary_day_windows(confirmed: ConfirmedTravelRequirement, *, draft: ItineraryDraft | None = None,
                         observations: tuple = ()) -> tuple[BoundaryDayWindow, ...]:
    """未选择路线时给有来源的最大可用窗口；提交后只采用所选路线事实。"""
    window = confirmed.requirement.time_window
    arrival, departure = window.arrival, window.departure
    arrival_at, departure_at = _endpoint_at(arrival), _endpoint_at(departure)
    arrival_buffer, departure_buffer = _processing(arrival, "arrival"), _processing(departure, "departure")

    def route(kind: str):
        if draft is not None:
            leg = getattr(draft, kind + "_transfer").route_leg
            return route_scheduled_minutes(leg.duration_s), (leg.directions_observation_index, leg.alternative_index)
        options = []
        for index, observation in enumerate(observations):
            if observation.status != "success":
                continue
            origin, destination = observation.origin.location, observation.destination.location
            hub, hotel = (origin, destination) if kind == "arrival" else (destination, origin)
            if hub.ref_type != "hub" or hub.endpoint_kind != kind or hotel.ref_type != "hotel":
                continue
            for alternative_index, alternative in enumerate(observation.alternatives):
                options.append((route_scheduled_minutes(alternative.duration_s), (index, alternative_index)))
        return min(options) if options else (None, None)

    arrival_minutes, arrival_ref = route("arrival")
    departure_minutes, departure_ref = route("departure")
    earliest = ceil_to_minute(arrival_at + timedelta(minutes=arrival_buffer + (arrival_minutes or 0)))
    latest = _floor_minute(departure_at - timedelta(minutes=departure_buffer + (departure_minutes or 0)))
    arrival_end = max(earliest.date(), arrival_at.date(),
                      draft.arrival_transfer.route_leg.interval.end_at.date() if draft else earliest.date())
    departure_start = min(latest.date(), departure_at.date(),
                          draft.departure_transfer.route_leg.interval.start_at.date() if draft else latest.date())
    first_date = date.fromisoformat(window.sightseeing_start_date)
    result = []
    for offset in range(window.sightseeing_days):
        day = first_date + timedelta(days=offset)
        default = default_open_interval(day)
        affected = tuple(kind for kind, touched in (
            ("arrival", arrival_at.date() <= day <= arrival_end),
            ("departure", departure_start <= day <= departure_at.date()),
        ) if touched)
        result.append(BoundaryDayWindow(day, max(default.start_at, earliest), min(default.end_at, latest),
            affected, arrival_minutes is None or departure_minutes is None, arrival_buffer, departure_buffer,
            arrival_ref, departure_ref))
    return tuple(result)


def validate_break_windows(confirmed: ConfirmedTravelRequirement, draft: ItineraryDraft) -> None:
    """休息只能在已实际到达酒店且尚未返站的日间时段内。"""
    windows = {item.date: item for item in boundary_day_windows(confirmed, draft=draft)}
    for day in draft.days:
        window = windows[day.date]
        lower = max(window.start_at, draft.arrival_transfer.route_leg.interval.end_at)
        upper = min(window.end_at, draft.departure_transfer.route_leg.interval.start_at)
        for item in day.planned_breaks:
            if item.start_at < lower or item.end_at > upper or item.start_at.date() != day.date:
                raise ValueError("planned_break_outside_available_window")


def _gaps(start: datetime, end: datetime, spans: list[tuple[datetime, datetime]]) -> tuple[tuple[datetime, datetime], ...]:
    cursor, result = start, []
    for left, right in sorted(spans):
        left, right = max(start, left), min(end, right)
        if left >= right:
            continue
        if cursor < left:
            result.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < end:
        result.append((cursor, end))
    return tuple(result)


def assess_daily_quality(confirmed: ConfirmedTravelRequirement, draft: ItineraryDraft) -> ItineraryDraft:
    """从全部日期实际安排派生提醒，不信任提交者已有的提醒。"""
    windows = {item.date: item for item in boundary_day_windows(confirmed, draft=draft)}
    days = []
    all_occupied = [(item.interval.start_at, item.interval.end_at)
        for day in draft.days for item in (*day.visits, *day.route_legs)]
    all_occupied.extend((transfer.route_leg.interval.start_at, transfer.route_leg.interval.end_at)
        for transfer in (draft.arrival_transfer, draft.departure_transfer))
    all_occupied.extend((item.start_at, item.end_at) for day in draft.days for item in day.planned_breaks)

    for day in draft.days:
        issues = assess_day_quality(day, windows[day.date], draft.effective_policy.effective_pace, all_occupied)
        days.append(day.model_copy(update={"quality_issues": issues}))
    return draft.model_copy(update={"days": tuple(days)})


def assess_day_quality(day, window, pace, occupied=None) -> tuple[BoundaryDayQualityIssue, ...]:
    """初步安排和最终草案共用同一质量口径。"""
    threshold = IDLE_THRESHOLDS[pace]
    problems = []
    all_occupied = occupied if occupied is not None else [
        (item.interval.start_at, item.interval.end_at) for item in day.visits]
    if occupied is None:
        all_occupied.extend((item.start_at, item.end_at) for item in day.planned_breaks)

    def add(rule, start, end, minutes=None):
        problems.append(BoundaryDayQualityIssue(date=day.date, rule=rule, start_at=start, end_at=end,
            minutes=minutes if minutes is not None else int((end - start).total_seconds() // 60)))

    if window.start_at < window.end_at:
        own = [(max(start, window.start_at), min(end, window.end_at)) for start, end in all_occupied
               if start < window.end_at and end > window.start_at]
        afternoon_end = datetime.combine(day.date, time(17, 30), tzinfo=SHANGHAI)
        horizon = min(window.end_at, max([afternoon_end, *(end for _, end in own)]))
        gaps = _gaps(window.start_at, horizon, own)
        for start, end in gaps:
            if (end - start).total_seconds() >= threshold * 60:
                add("unexplained_gap", start, end)
        total_idle = sum(int((end - start).total_seconds() // 60) for start, end in gaps)
        if total_idle >= 2 * threshold and not any(item.rule == "unexplained_gap" for item in problems):
            add("unexplained_gap", gaps[0][0], gaps[-1][1], total_idle)
        breaks = day.planned_breaks
        total_break = sum(int((item.end_at - item.start_at).total_seconds() // 60) for item in breaks)
        if total_break >= threshold:
            add("excessive_break", min(item.start_at for item in breaks), max(item.end_at for item in breaks), total_break)
        meal_kinds = {
            VisitKind.LUNCH: {VisitKind.LUNCH, VisitKind.ATTRACTION_LUNCH},
            VisitKind.DINNER: {VisitKind.DINNER, VisitKind.ATTRACTION_DINNER},
        }
        for kind, matching in meal_kinds.items():
            if any(item.visit_kind in matching for item in day.visits):
                continue
            first, last = MEAL_START_WINDOWS[kind]
            start = max(window.start_at, datetime.combine(day.date, first, tzinfo=SHANGHAI))
            latest_start = min(window.end_at - timedelta(minutes=60), datetime.combine(day.date, last, tzinfo=SHANGHAI))
            if start <= latest_start:
                add("missing_" + kind.value, start, start + timedelta(minutes=60))
    return tuple(sorted(set(problems), key=lambda item: (item.rule, item.start_at, item.end_at, item.minutes)))


assess_boundary_quality = assess_daily_quality


def quality_feedback(draft: ItineraryDraft) -> tuple[dict, ...]:
    return tuple(dict(target=f"{day.date.isoformat()}:daily:{item.rule}",
        field=f"draft.days[{index}]", **item.model_dump(mode="json"))
        for index, day in enumerate(draft.days) for item in day.quality_issues)


def unplanned_day_issues(days, windows, pace) -> tuple[dict, ...]:
    """足够长的可用日期必须有真实主访问，标签和休息不能替代。"""
    from .admission import _issue
    by_date = {item.date: item for item in days}
    return tuple(_issue(target=f"{window.date}:main:day:required", rule="unplanned_usable_day",
        field=f"days[{index}].visits", actual=0, expected="main_visit_in_usable_day")
        for index, window in enumerate(windows)
        if window.date in by_date and not by_date[window.date].visits
        and (window.end_at - window.start_at).total_seconds() >= IDLE_THRESHOLDS[pace or TravelPace.RELAXED] * 60)


def outline_window_issues(days, windows, pace) -> tuple[dict, ...]:
    from collections import Counter
    from .admission import _issue
    from .policy import VISIT_DURATION_BOUNDS
    issues = list(unplanned_day_issues(days, windows, pace))
    expected = tuple(item.date for item in windows)
    if tuple(day.date for day in days) != expected:
        issues.append(_issue(target="itinerary:main:dates:required", rule="outline_dates_incomplete",
            field="daily_outline", expected=[item.isoformat() for item in expected]))
    by_date = {item.date: item for item in windows}
    for index, day in enumerate(days):
        if day.date not in by_date:
            continue
        window = by_date[day.date]
        path = f"daily_outline[{index}]"
        spans = [(visit.interval.start_at, visit.interval.end_at) for visit in day.visits]
        spans.extend((rest.start_at, rest.end_at) for rest in day.planned_breaks)
        if any(start < window.start_at or end > window.end_at for start, end in spans):
            issues.append(_issue(target=f"{day.date}:main:time:window", rule="outside_daily_window",
                field=path, expected="verified_daily_window"))
        counts = Counter()
        for visit_index, visit in enumerate(day.visits):
            purpose = visit.visit_kind.value
            counts["attraction"] += purpose.startswith("attraction")
            for meal in ("lunch", "dinner"):
                counts[meal] += purpose in (meal, "attraction_" + meal)
            minutes = (visit.interval.end_at - visit.interval.start_at).total_seconds() / 60
            low, high = VISIT_DURATION_BOUNDS[visit.visit_kind]
            if not low <= minutes <= high:
                issues.append(_issue(target=f"{day.date}:main:{purpose}:duration", rule="visit_duration",
                    field=f"{path}.visits[{visit_index}].interval", expected=f"{low}..{high}"))
            for meal, kind in (("lunch", VisitKind.LUNCH), ("dinner", VisitKind.DINNER)):
                if purpose not in (meal, "attraction_" + meal):
                    continue
                earliest, latest = MEAL_START_WINDOWS[kind]
                if not earliest <= visit.interval.start_at.timetz().replace(tzinfo=None) <= latest:
                    issues.append(_issue(target=f"{day.date}:main:{meal}:time", rule="meal_start_window",
                        field=f"{path}.visits[{visit_index}].interval", expected="meal_start_window"))
        maximum = {TravelPace.RELAXED: 2, TravelPace.NORMAL: 3, TravelPace.INTENSE: 4}[pace or TravelPace.INTENSE]
        if counts["attraction"] > maximum or counts["lunch"] > 1 or counts["dinner"] > 1:
            issues.append(_issue(target=f"{day.date}:main:counts:maximum", rule="daily_count_exceeded",
                field=path + ".visits", expected="pace_maximum_and_single_meals"))
    return tuple(issues)
