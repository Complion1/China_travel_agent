"""Module 09 的确定性业务 Gate。

这些函数只验证模型 proposal 已经表达的选择；它们不替模型选择地点、主题、
顺序、交通偏好或路线方案。
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from datetime import date, datetime, time, timedelta
from enum import Enum
from math import ceil
from typing import Iterable
from zoneinfo import ZoneInfo

from app.baidu_map import MatrixTravelMode, TravelMode
from app.candidate_research import CandidateRecord, CandidateState, MatchVerdict, ResearchRole, RoleVerdict
from app.requirements import ConfirmedTravelRequirement, FieldStatus, HubType, RequirementFieldPath, TravelPace
from pydantic import BaseModel

from .models import (
    BackupOption,
    BusinessHoursState,
    CandidateLocationRef,
    DayPlan,
    EffectivePlanningPolicy,
    ProposedEffectivePlanningPolicy,
    PlanningDayKind,
    PlanningStopReason,
    RouteLeg,
    ScheduledVisit,
    TimeSensitiveMustAnchor,
    TransportSemantic,
    VisitKind,
)


class _PolicyBusinessRejected(ValueError):
    """结构合法但不能由当前上游证据授权的策略提案。"""

    __slots__ = ()


class _PolicyEvidenceInsufficient(_PolicyBusinessRejected):
    """候选证据不足以授权时间敏感活动锚点。"""

    __slots__ = ()


class _PolicyLockedMismatch(_PolicyBusinessRejected):
    """后续策略提案试图改变已锁定的有效策略。"""

    __slots__ = ()


SHANGHAI = ZoneInfo("Asia/Shanghai")
ARRIVAL_BUFFER_MINUTES = {HubType.AIRPORT: 60, HubType.RAILWAY_STATION: 30}
DEPARTURE_BUFFER_MINUTES = {HubType.AIRPORT: 120, HubType.RAILWAY_STATION: 60}
FULL_DAY_TRANSPORT_LIMIT_SECONDS = {
    TravelPace.RELAXED: 120 * 60,
    TravelPace.NORMAL: 180 * 60,
    TravelPace.INTENSE: 240 * 60,
}
SINGLE_LEG_LIMIT_SECONDS = {
    TravelPace.RELAXED: 60 * 60,
    TravelPace.NORMAL: 90 * 60,
    TravelPace.INTENSE: 120 * 60,
}


def ceil_to_grid(value: datetime) -> datetime:
    """把下一可排活动起点向上吸附到上海时区的半小时网格。"""
    if value.tzinfo is None or value.utcoffset() != timedelta(hours=8):
        raise ValueError("grid value must be Asia/Shanghai aware")
    value = value.replace(second=0, microsecond=0)
    extra = (30 - value.minute % 30) % 30
    return value + timedelta(minutes=extra)


def default_open_interval(day: date):
    from .models import TimeInterval
    return TimeInterval(
        start_at=datetime.combine(day, time(9, 0), tzinfo=SHANGHAI),
        end_at=datetime.combine(day, time(21, 0), tzinfo=SHANGHAI),
    )


def boundary_processing_minutes(
    hub_type: HubType,
    transfer_kind: str,
    *,
    is_approximate: bool,
    user_minutes: int | None = None,
) -> int:
    if transfer_kind not in {"arrival", "departure"}:
        raise ValueError("invalid boundary direction")
    base = (ARRIVAL_BUFFER_MINUTES if transfer_kind == "arrival" else DEPARTURE_BUFFER_MINUTES)[hub_type]
    minimum = base + (30 if is_approximate else 0)
    if user_minutes is not None and user_minutes < minimum:
        raise ValueError("user boundary margin cannot reduce the fixed safety margin")
    # Keep the fixed processing buffer and the approximate-time allowance in
    # separate public fields.  The latter is applied by the schedule gate.
    return base if user_minutes is None else user_minutes


def route_scheduled_minutes(duration_s: int) -> int:
    if type(duration_s) is not int or duration_s < 0:
        raise ValueError("duration must be a non-negative integer")
    return 0 if duration_s == 0 else ceil(duration_s / 1800) * 30


def route_mode_for_semantic(semantic: TransportSemantic) -> TravelMode:
    return {
        TransportSemantic.WALKING: TravelMode.WALKING,
        TransportSemantic.CYCLING: TravelMode.RIDING,
        TransportSemantic.PUBLIC_TRANSIT: TravelMode.TRANSIT,
        TransportSemantic.RIDE_HAILING: TravelMode.DRIVING,
        TransportSemantic.PRIVATE_CAR: TravelMode.DRIVING,
    }[semantic]


def attraction_bounds(pace: TravelPace, day_kind: PlanningDayKind) -> tuple[int, int]:
    if day_kind is PlanningDayKind.TRANSIT_ONLY:
        return (0, 0)
    bounds = {
        TravelPace.RELAXED: (1, 2),
        TravelPace.NORMAL: (3, 3),
        TravelPace.INTENSE: (4, 4),
    }[pace]
    return bounds if day_kind is PlanningDayKind.FULL else (0, bounds[1])


def visit_role_counts(visits: Iterable[ScheduledVisit]) -> tuple[int, int, int]:
    values = tuple(visits)
    attractions = sum(item.visit_kind in {VisitKind.ATTRACTION, VisitKind.ATTRACTION_LUNCH, VisitKind.ATTRACTION_DINNER} for item in values)
    lunches = sum(item.visit_kind in {VisitKind.LUNCH, VisitKind.ATTRACTION_LUNCH} for item in values)
    dinners = sum(item.visit_kind in {VisitKind.DINNER, VisitKind.ATTRACTION_DINNER} for item in values)
    return attractions, lunches, dinners


def validate_density(day: DayPlan, pace: TravelPace) -> None:
    attractions, _, _ = visit_role_counts(day.visits)
    minimum, maximum = attraction_bounds(pace, day.day_kind)
    if not minimum <= attractions <= maximum:
        raise ValueError("day attraction density violates the locked pace")


def validate_meal_slots(
    day: DayPlan,
    *,
    time_sensitive_must: Iterable[TimeSensitiveMustAnchor] = (),
) -> None:
    user_anchors = {
        anchor.anchor_id: anchor
        for anchor in time_sensitive_must
        if anchor.source == "user_provided"
    }
    _, lunches, dinners = visit_role_counts(day.visits)
    if day.day_kind is PlanningDayKind.FULL and (lunches != 1 or dinners != 1):
        raise ValueError("full day requires exactly one lunch and one dinner")
    if day.day_kind is PlanningDayKind.PARTIAL and (lunches > 1 or dinners > 1):
        raise ValueError("partial day has at most one meal of each kind")
    if day.day_kind is PlanningDayKind.TRANSIT_ONLY and (lunches or dinners):
        raise ValueError("transit-only day cannot contain meals")
    for visit in day.visits:
        if visit.visit_kind not in {
            VisitKind.LUNCH,
            VisitKind.ATTRACTION_LUNCH,
            VisitKind.DINNER,
            VisitKind.ATTRACTION_DINNER,
        }:
            continue
        explicit_meal_anchors = tuple(
            user_anchors[anchor_id]
            for anchor_id in visit.time_sensitive_anchor_ids
            if anchor_id in user_anchors
        )
        if len(explicit_meal_anchors) > 1:
            raise ValueError("meal cannot have multiple explicit time anchors")
        if explicit_meal_anchors:
            if not visit.interval.start_at <= explicit_meal_anchors[0].event_at < visit.interval.end_at:
                raise ValueError("meal must cover its explicit user time")
            continue
        if visit.visit_kind in {VisitKind.LUNCH, VisitKind.ATTRACTION_LUNCH}:
            if not time(11, 30) <= visit.interval.start_at.timetz().replace(tzinfo=None) <= time(13, 30):
                raise ValueError("lunch must start in the 11:30-13:30 window")
        if visit.visit_kind in {VisitKind.DINNER, VisitKind.ATTRACTION_DINNER}:
            if not time(17, 30) <= visit.interval.start_at.timetz().replace(tzinfo=None) <= time(19, 30):
                raise ValueError("dinner must start in the 17:30-19:30 window")


_MEAL_TIME_PATTERN = re.compile(
    r"(?<!\d)(?P<hour>[01]?\d|2[0-3])(?:[:：](?P<minute>[0-5]\d)|点(?:(?P<minute_cn>[0-5]?\d)分?)?)(?!\d)"
)
_MEAL_DATE_PATTERN = re.compile(r"(?<!\d)(?P<month>1[0-2]|0?[1-9])月(?P<day>[12]?\d|3[01])日?")
_MEAL_NEGATIVE_WORDS = ("不要", "别", "不安排", "不能", "不想", "避免", "无需", "不用", "拒绝")
_MEAL_POSITIVE_WORDS = ("必须", "预约", "预订", "安排", "固定", "指定", "定在", "开始", "有", "午餐", "午饭", "晚餐", "晚饭")
_MEAL_CLAUSE_SPLIT_PATTERN = re.compile(r"(?:并且|而且|同时|以及|且|[,，、])")
_MEAL_KINDS = {
    "lunch": ("午餐", "午饭"),
    "dinner": ("晚餐", "晚饭"),
}
_MEAL_GENERIC_SOURCE_PATTERN = re.compile(
    r"(?:允许活动到|活动到|不晚于|不得晚于|最晚|最迟|当天|当日|本日|中午|下午|晚上|"
    r"午餐|午饭|晚餐|晚饭|必须|预约|预订|安排|固定|指定|定在|开始|有|不要|别|不安排|"
    r"不能|不想|避免|无需|不用|拒绝|尽量|最好|优先|希望|想|请|要|在|于|吃饭|用餐|吃|"
    r"时间|时刻|到|点|分|或|和|且|以及|同时|并且|而且|的|了|吧|[0-9０-９]+|[：:，,；;\s])"
)
_TIME_SENSITIVE_ACTIVITY_WORDS = (
    "活动", "参观", "游览", "景点", "预约", "预订", "安排", "固定", "指定", "参加", "开始",
)


def _meal_source_segments(source: str) -> tuple[tuple[str, tuple[int, int] | None], ...]:
    """Split one confirmed source into date-scoped meal requirement segments.

    A semicolon/newline continues the current date context so a leading date
    can scope the following requirement clause. Sentence-ending punctuation
    resets that context; an explicit date in a segment always replaces it.
    """
    current_date: tuple[int, int] | None = None
    segments: list[tuple[str, tuple[int, int] | None]] = []
    for part in re.split(r"([；;\n。！？!?])", source):
        if part in {"；", ";", "\n", "。", "！", "？", "!", "?"}:
            if part in {"。", "！", "？", "!", "?"}:
                current_date = None
            continue
        text = part.strip()
        if not text:
            continue
        dates = tuple(
            (int(match.group("month")), int(match.group("day")))
            for match in _MEAL_DATE_PATTERN.finditer(text)
        )
        if len(set(dates)) > 1:
            segments.append((text, None))
            current_date = None
            continue
        if dates:
            current_date = dates[0]
        segments.append((text, current_date))
    return tuple(segments)


def _meal_candidate_mentions(text: str, candidate_names: Iterable[str]) -> frozenset[str]:
    names = tuple(sorted({name for name in candidate_names if name}, key=len, reverse=True))
    if not names:
        return frozenset()
    pattern = re.compile("|".join(re.escape(name) for name in names))
    return frozenset(match.group(0) for match in pattern.finditer(text))


def _meal_has_unresolved_location_scope(text: str, candidate_names: Iterable[str]) -> bool:
    """Reject an apparent location scope that is not in the admitted set.

    This is deliberately a conservative lexical check, not a place-name parser:
    after removing admitted candidate names, dates, times and generic scheduling
    words, any remaining text in a meal requirement cannot be treated as
    unscoped authorization.
    """
    remainder = _MEAL_DATE_PATTERN.sub("", text)
    names = tuple(sorted({name for name in candidate_names if name}, key=len, reverse=True))
    for name in names:
        remainder = remainder.replace(name, "")
    remainder = _MEAL_TIME_PATTERN.sub("", remainder)
    remainder = _MEAL_GENERIC_SOURCE_PATTERN.sub("", remainder)
    return bool(remainder.strip())


def _meal_kinds(text: str) -> frozenset[str]:
    return frozenset(kind for kind, words in _MEAL_KINDS.items() if any(word in text for word in words))


def _meal_source_requirements(segment: str) -> tuple[tuple[frozenset[str], tuple[int, int], bool], ...]:
    """Extract all locally scoped meal/time requirements from one segment."""
    active_kinds: frozenset[str] = frozenset()
    requirements: list[tuple[frozenset[str], tuple[int, int], bool]] = []
    for clause in _MEAL_CLAUSE_SPLIT_PATTERN.split(segment):
        text = clause.strip()
        if not text:
            continue
        local_kinds = _meal_kinds(text)
        if local_kinds:
            active_kinds = local_kinds
        if not active_kinds or not any(word in text for word in _MEAL_POSITIVE_WORDS + _MEAL_NEGATIVE_WORDS):
            continue
        for match in _MEAL_TIME_PATTERN.finditer(text):
            minute_text = match.group("minute") or match.group("minute_cn")
            source_time = (int(match.group("hour")), int(minute_text) if minute_text is not None else 0)
            is_negative = (
                any(word in text[:match.start()][-24:] for word in _MEAL_NEGATIVE_WORDS)
                or any(word in text[match.end():][:16] for word in _MEAL_NEGATIVE_WORDS)
            )
            requirements.append((active_kinds, source_time, is_negative))
    return tuple(requirements)


def validate_user_meal_anchor_source(
    confirmed: ConfirmedTravelRequirement,
    anchor: TimeSensitiveMustAnchor,
    *,
    visit_kind: VisitKind,
    candidate_name: str,
    candidate_names: Iterable[str] | None = None,
) -> None:
    """Require a meal anchor to match an explicit confirmed user requirement.

    The proposal's ``source`` is only a model claim.  A meal override is
    accepted here only when the confirmed requirement text contains the same
    local date, exact time, meal kind and, when explicitly scoped, the named
    candidate.
    This deliberately does not infer an authorization from the anchor label,
    a time string alone, or a locked policy.
    """
    if type(confirmed) is not ConfirmedTravelRequirement or anchor.source != "user_provided":
        raise ValueError("meal anchor source is not user-confirmed")
    if visit_kind in {VisitKind.LUNCH, VisitKind.ATTRACTION_LUNCH}:
        target_kind = "lunch"
    elif visit_kind in {VisitKind.DINNER, VisitKind.ATTRACTION_DINNER}:
        target_kind = "dinner"
    else:
        raise ValueError("user meal anchor must attach to a meal visit")
    if not candidate_name:
        raise ValueError("meal anchor candidate is missing")

    preferences = confirmed.requirement.preferences
    sources: list[str] = []
    for field_path, value in (
        (RequirementFieldPath.DAILY_TIME_PREFERENCES, preferences.daily_time_preferences),
        (RequirementFieldPath.FREE_TEXT_NOTES, preferences.free_text_notes),
        (RequirementFieldPath.MUST_VISIT_PLACES, "\n".join(preferences.must_visit_places) if preferences.must_visit_places else None),
    ):
        metadata = confirmed.metadata_without_raw_evidence.metadata.get(field_path)
        if value is not None and (metadata is None or metadata.status is FieldStatus.CONFIRMED):
            sources.append(value)
    if not sources:
        raise ValueError("meal anchor has no confirmed user source")

    candidate_names_supplied = candidate_names is not None
    all_candidate_names = tuple(candidate_names) if candidate_names_supplied else (candidate_name,)
    if candidate_name not in all_candidate_names or all_candidate_names.count(candidate_name) != 1:
        raise ValueError("meal anchor candidate is not uniquely admitted")
    positive_match = False
    negative_match = False
    applicable_positive_times: set[tuple[int, int]] = set()
    unresolved_scope_match = False
    target_time = (anchor.event_at.hour, anchor.event_at.minute)
    for source in sources:
        for segment, segment_date in _meal_source_segments(source):
            if segment_date != (anchor.date.month, anchor.date.day):
                continue
            requirements = _meal_source_requirements(segment)
            has_target_requirement = any(target_kind in meal_kinds for meal_kinds, _, _ in requirements)
            candidate_mentions = _meal_candidate_mentions(segment, all_candidate_names)
            unresolved_location_scope = (
                candidate_names_supplied
                and has_target_requirement
                and _meal_has_unresolved_location_scope(segment, all_candidate_names)
            )
            if unresolved_location_scope:
                unresolved_scope_match = True
                candidate_scope_matches = False
            elif candidate_mentions == {candidate_name}:
                candidate_scope_matches = True
            elif not candidate_mentions and candidate_names_supplied:
                candidate_scope_matches = True
            else:
                candidate_scope_matches = False
            if not candidate_scope_matches:
                continue
            for meal_kinds, source_time, is_negative in requirements:
                if target_kind not in meal_kinds:
                    continue
                if not is_negative:
                    applicable_positive_times.add(source_time)
                if source_time == target_time:
                    negative_match |= is_negative
                    positive_match |= not is_negative
    if unresolved_scope_match or len(applicable_positive_times) > 1 or not positive_match or negative_match:
        raise ValueError("meal anchor is not authorized by the confirmed user requirement")


def validate_user_time_sensitive_anchor_source(
    confirmed: ConfirmedTravelRequirement,
    anchor: TimeSensitiveMustAnchor,
    *,
    visit_kind: VisitKind,
    candidate_name: str,
    candidate_names: Iterable[str] | None = None,
) -> None:
    """Require every user activity anchor to match confirmed local evidence.

    Meal anchors retain the stricter meal-specific parser above. For other
    visits, the confirmed text must bind one admitted candidate, the exact
    local date, and the exact event time in an activity clause. A time string
    or the proposal's anchor label alone is never authorization.
    """
    if visit_kind in {
        VisitKind.LUNCH,
        VisitKind.ATTRACTION_LUNCH,
        VisitKind.DINNER,
        VisitKind.ATTRACTION_DINNER,
    }:
        validate_user_meal_anchor_source(
            confirmed,
            anchor,
            visit_kind=visit_kind,
            candidate_name=candidate_name,
            candidate_names=candidate_names,
        )
        return
    if type(confirmed) is not ConfirmedTravelRequirement or anchor.source != "user_provided":
        raise ValueError("time-sensitive anchor source is not user-confirmed")
    if visit_kind is not VisitKind.ATTRACTION:
        raise ValueError("user time-sensitive anchor must attach to an attraction visit")
    if not candidate_name:
        raise ValueError("time-sensitive anchor candidate is missing")

    preferences = confirmed.requirement.preferences
    sources: list[str] = []
    for field_path, value in (
        (RequirementFieldPath.DAILY_TIME_PREFERENCES, preferences.daily_time_preferences),
        (RequirementFieldPath.FREE_TEXT_NOTES, preferences.free_text_notes),
        (RequirementFieldPath.MUST_VISIT_PLACES, "\n".join(preferences.must_visit_places) if preferences.must_visit_places else None),
    ):
        metadata = confirmed.metadata_without_raw_evidence.metadata.get(field_path)
        if value is not None and (metadata is None or metadata.status is FieldStatus.CONFIRMED):
            sources.append(value)
    if not sources:
        raise ValueError("time-sensitive anchor has no confirmed user source")

    all_candidate_names = tuple(candidate_names) if candidate_names is not None else (candidate_name,)
    if candidate_name not in all_candidate_names or all_candidate_names.count(candidate_name) != 1:
        raise ValueError("time-sensitive anchor candidate is not uniquely admitted")
    target_time = (anchor.event_at.hour, anchor.event_at.minute)
    positive_times: set[tuple[int, int]] = set()
    negative_times: set[tuple[int, int]] = set()
    for source in sources:
        for segment, segment_date in _meal_source_segments(source):
            if segment_date != (anchor.date.month, anchor.date.day):
                continue
            for clause in _MEAL_CLAUSE_SPLIT_PATTERN.split(segment):
                text = clause.strip()
                if not text or not any(word in text for word in _TIME_SENSITIVE_ACTIVITY_WORDS):
                    continue
                if _meal_candidate_mentions(text, all_candidate_names) != {candidate_name}:
                    continue
                for match in _MEAL_TIME_PATTERN.finditer(text):
                    minute_text = match.group("minute") or match.group("minute_cn")
                    source_time = (int(match.group("hour")), int(minute_text) if minute_text is not None else 0)
                    is_negative = (
                        any(word in text[:match.start()][-24:] for word in _MEAL_NEGATIVE_WORDS)
                        or any(word in text[match.end():][:16] for word in _MEAL_NEGATIVE_WORDS)
                    )
                    (negative_times if is_negative else positive_times).add(source_time)
    if len(positive_times) != 1 or target_time not in positive_times or target_time in negative_times:
        raise ValueError("time-sensitive anchor is not authorized by the confirmed user requirement")


def confirmed_visit_duration(name: str, names: tuple[str, ...], sources: tuple[str, ...]) -> int | None:
    """Resolve an unambiguous, POI-bound duration; unsupported claims fail closed.

    This recognises duration clauses, not arbitrary planning instructions. A
    negated quantity is never evidence for an override. Commas retain the POI
    subject; a new named POI or sentence ends that scope. Distinct affirmative
    quantities require clarification rather than guessing which one wins.
    """
    number = r"(?:[0-9]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百]+)"
    quantity = rf"(?:半(?:个)?小时|{number}\s*(?:个)?小时(?:半|(?:又)?\s*{number}\s*分钟)?|{number}\s*分钟)"
    duration = re.compile(quantity)
    subjects = re.compile("|".join(re.escape(value) for value in sorted(set(names), key=len, reverse=True)))
    prefix = re.compile(r"(?:(?:我|我们|想|希望|请|要|只|就|仅|在|这里|改为|而是|改成|是|游览|停留|参观|用餐|安排|游玩|玩|逛|待|呆|花|时间|时长|为|：|:|\s))*")
    negatives = ("不要", "别", "不想", "不愿", "不是", "不用", "不安排")

    def value_of(token: str) -> Decimal:
        if token[0].isascii():
            return Decimal(token)
        digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
        digits.update({"〇": 0, "两": 2})
        total = current = 0
        for char in token:
            if char in digits:
                current = digits[char]
            else:
                total += (current or 1) * (10 if char == "十" else 100)
                current = 0
        return Decimal(total + current)

    def minutes_of(text: str) -> int:
        text = re.sub(r"\s+|个|又", "", text)
        if text == "半小时":
            return 30
        if "小时" in text:
            hours, rest = text.split("小时")
            result = value_of(hours) * 60
            if rest:
                result += 30 if rest == "半" else value_of(rest.removesuffix("分钟"))
        else:
            result = value_of(text.removesuffix("分钟"))
        if result <= 0 or result != int(result):
            raise ValueError("unsupported confirmed visit duration")
        return int(result)

    positive: set[int] = set()
    negative: set[int] = set()
    for source in sources:
        subject = None
        for clause in re.split(r"([。；;\n！？!?])|[，,]", source):
            if not clause:
                continue
            if clause in "。；;\n！？!?":
                subject = None
                continue
            mentions = tuple(subjects.finditer(clause))
            if mentions:
                # Multiple subjects in one clause are not an exact POI binding.
                if len(mentions) != 1:
                    if any(match.group() == name for match in mentions) and duration.search(clause):
                        raise ValueError("ambiguous confirmed visit duration subject")
                    subject = None
                    continue
                subject = mentions[0].group()
                clause = clause[:mentions[0].start()] + clause[mentions[0].end():]
            if subject != name:
                continue
            matches = tuple(duration.finditer(clause))
            if not matches:
                continue
            if len(matches) != 1:
                raise ValueError("ambiguous confirmed visit duration")
            match = matches[0]
            before, after = clause[:match.start()].strip(), clause[match.end():].strip()
            is_negative = any(word in before for word in negatives)
            for word in negatives:
                before = before.replace(word, "")
            if prefix.fullmatch(before) is None or after not in {"", "即可", "就好", "就够了"}:
                raise ValueError("unsupported confirmed visit duration expression")
            (negative if is_negative else positive).add(minutes_of(match.group()))
    if len(positive) > 1 or positive & negative:
        raise ValueError("conflicting confirmed visit durations")
    return next(iter(positive)) if positive else None


def validate_visit_duration(visit: ScheduledVisit, *, user_duration_minutes: int | None = None) -> None:
    """Validate the deterministic stay-duration bounds from the planning contract."""
    # Retain the confirmed quantity for comparison; only its scheduled
    # occupancy is rounded up. A model marker alone is not user evidence.
    minutes = int((visit.interval.end_at - visit.interval.start_at).total_seconds() // 60)
    if visit.duration_source == "user_explicit" or user_duration_minutes is not None:
        if (
            type(user_duration_minutes) is not int or user_duration_minutes <= 0
            or visit.duration_source != "user_explicit"
            or route_scheduled_minutes(user_duration_minutes * 60) != minutes
        ):
            raise ValueError("user duration requires matching confirmed evidence")
        return
    bounds = {
        VisitKind.ATTRACTION: (60, 240),
        VisitKind.LUNCH: (60, 120),
        VisitKind.DINNER: (60, 120),
        VisitKind.ATTRACTION_LUNCH: (90, 240),
        VisitKind.ATTRACTION_DINNER: (90, 240),
    }[visit.visit_kind]
    if minutes % 30 != 0 or not bounds[0] <= minutes <= bounds[1]:
        raise ValueError("visit duration violates the planning bounds or grid")


def validate_route_capacity(
    day: DayPlan,
    pace: TravelPace,
    *,
    explicit_day_limit_seconds: int | None = None,
) -> None:
    for leg in day.route_legs:
        validate_route_leg_capacity(leg, pace)
    if day.day_kind is PlanningDayKind.FULL:
        total = sum(leg.duration_s for leg in day.route_legs)
        limit = FULL_DAY_TRANSPORT_LIMIT_SECONDS[pace]
        if explicit_day_limit_seconds is not None:
            if explicit_day_limit_seconds < 1:
                raise ValueError("explicit transport limit must be positive")
            limit = min(limit, explicit_day_limit_seconds)
        if total > limit:
            raise ValueError("full-day route total exceeds pace limit")


def validate_route_leg_capacity(leg: RouteLeg, pace: TravelPace) -> None:
    if leg.duration_s > SINGLE_LEG_LIMIT_SECONDS[pace]:
        raise ValueError("single route leg exceeds pace limit")


def validate_business_state(item: ScheduledVisit | BackupOption) -> None:
    if item.business_hours_state is BusinessHoursState.CONFLICTS:
        raise ValueError("conflicting business hours cannot be scheduled")
    if item.business_hours_state is BusinessHoursState.UNKNOWN and not item.requires_verification:
        raise ValueError("unknown business hours require verification")


def validate_backup_options(day: DayPlan) -> None:
    required: list[str] = []
    attractions, lunches, dinners = visit_role_counts(day.visits)
    if attractions:
        required.append("attraction")
    if lunches:
        required.append("lunch")
    if dinners:
        required.append("dinner")
    kinds = [item.backup_kind for item in day.backups]
    if tuple(kinds) != tuple(required):
        raise ValueError("backup slots must follow the day's actual role order")
    if len(set(item.candidate.uid for item in day.backups)) != len(day.backups):
        raise ValueError("backup candidate uid must be unique")
    for item in day.backups:
        validate_business_state(item)
        if item.distance_m > 3000:
            raise ValueError("backup matrix distance must be within 3000m")


def validate_day_policy(
    day: DayPlan,
    pace: TravelPace,
    *,
    explicit_day_limit_seconds: int | None = None,
) -> None:
    validate_density(day, pace)
    validate_meal_slots(day)
    validate_route_capacity(day, pace, explicit_day_limit_seconds=explicit_day_limit_seconds)
    validate_backup_options(day)
    for item in day.visits:
        validate_business_state(item)


def validate_time_sensitive_anchor(
    anchor: TimeSensitiveMustAnchor,
    visits: Iterable[ScheduledVisit],
) -> None:
    matching = tuple(item for item in visits if anchor.anchor_id in item.time_sensitive_anchor_ids)
    if len(matching) != 1 or not (matching[0].interval.start_at <= anchor.event_at < matching[0].interval.end_at):
        raise ValueError("time-sensitive anchor must be covered by exactly one scheduled visit")


def candidate_is_eligible(record: object, role: ResearchRole | None = None) -> bool:
    if type(record) is not CandidateRecord:
        return False
    try:
        record = CandidateRecord.model_validate_json(
            record.model_dump_json(exclude_none=False), strict=True
        )
    except (TypeError, ValueError):
        return False
    if record.state is not CandidateState.ELIGIBLE:
        return False
    if role is None:
        return True
    return any(item.role is role and item.verdict is RoleVerdict.SUPPORTED for item in record.roles)


def candidate_uid_is_allowed(record: object, role: ResearchRole | None = None) -> bool:
    if not candidate_is_eligible(record, role):
        return False
    # Confirmed avoid is a hard exclusion; unresolved avoid cannot be promoted.
    for item in record.avoid_matches:
        if item.verdict in {MatchVerdict.CONFIRMED, MatchVerdict.UNRESOLVED}:
            return False
    return True


def validate_unique_main_and_backup_uids(
    main_refs: Iterable[CandidateLocationRef], backup_refs: Iterable[CandidateLocationRef]
) -> None:
    main = tuple(item.uid for item in main_refs)
    backups = tuple(item.uid for item in backup_refs)
    if len(set(main)) != len(main) or len(set(backups)) != len(backups):
        raise ValueError("main and backup candidate uids must each be unique")
    if set(main) & set(backups):
        raise ValueError("backup candidate uid cannot appear in main itinerary")


def order_stop_reasons(reasons: Iterable[PlanningStopReason]) -> tuple[PlanningStopReason, ...]:
    order = {item: index for index, item in enumerate(PlanningStopReason)}
    unique = set(reasons)
    return tuple(sorted(unique, key=order.__getitem__))


def validate_effective_policy(policy: EffectivePlanningPolicy) -> None:
    if policy.locked is not True:
        raise ValueError("effective planning policy must be locked")
    rule_ids = tuple(item.rule_id for item in policy.daily_time_rules)
    anchor_ids = tuple(item.anchor_id for item in policy.time_sensitive_must)
    transport_ids = tuple(item.rule_id for item in policy.transport_rules)
    if len(set(rule_ids)) != len(rule_ids) or len(set(anchor_ids)) != len(anchor_ids) or len(set(transport_ids)) != len(transport_ids):
        raise ValueError("policy ids must be unique")


def _policy_source_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


_POLICY_SOURCE_ALIASES = {
    "daily_time_preferences": RequirementFieldPath.DAILY_TIME_PREFERENCES.value,
    RequirementFieldPath.DAILY_TIME_PREFERENCES.value: RequirementFieldPath.DAILY_TIME_PREFERENCES.value,
    "transport_preferences": RequirementFieldPath.TRANSPORT_PREFERENCES.value,
    RequirementFieldPath.TRANSPORT_PREFERENCES.value: RequirementFieldPath.TRANSPORT_PREFERENCES.value,
}


def _policy_json_value(value: object) -> object:
    if value is None or type(value) is bool or type(value) is int or type(value) is str:
        return value
    if type(value) is float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("policy source contains a non-finite number")
        return value
    if isinstance(value, Enum):
        return _policy_json_value(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _policy_json_value(value.model_dump(mode="python", exclude_none=False, by_alias=False, exclude_computed_fields=True))
    if type(value) is tuple or type(value) is list:
        return [_policy_json_value(item) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("policy source mapping keys must be text")
        return {key: _policy_json_value(item) for key, item in value.items()}
    raise ValueError("unsupported policy source value")


def _policy_source_binding(
    confirmed: ConfirmedTravelRequirement,
    source_field: str,
    *,
    allowed: frozenset[str],
) -> tuple[str, str]:
    canonical_field = _POLICY_SOURCE_ALIASES.get(source_field)
    if canonical_field is None or canonical_field not in allowed:
        raise ValueError("policy source field is not allowed for this rule")
    value: object = confirmed.requirement
    for part in canonical_field.split("."):
        value = getattr(value, part)
    if value is None:
        raise _PolicyBusinessRejected("policy source field is not confirmed")
    metadata = confirmed.metadata_without_raw_evidence.metadata.get(
        RequirementFieldPath(canonical_field)
    )
    metadata_projection = None
    if metadata is not None:
        metadata_projection = metadata.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
    return canonical_field, _policy_source_digest(
        {
            "schema_version": confirmed.schema_version,
            "field_path": canonical_field,
            "value": _policy_json_value(value),
            "metadata": metadata_projection,
        }
    )


def _effective_policy_from_proposal(
    proposal: ProposedEffectivePlanningPolicy,
    *,
    confirmed: ConfirmedTravelRequirement,
    source_handle_indexes: dict[str, int] | None = None,
    locked_policy: EffectivePlanningPolicy | None = None,
    reject_unsupported_verified_evidence: bool = True,
) -> EffectivePlanningPolicy:
    """Convert a validated model proposal to the locked public policy.

    The proposal supplies choices; this function only performs deterministic
    conversion and hard consistency checks. In particular, free-form requirement
    text is never parsed here.
    """

    if type(proposal) is not ProposedEffectivePlanningPolicy or type(confirmed) is not ConfirmedTravelRequirement:
        raise ValueError("policy inputs must be the confirmed and proposal DTOs")
    try:
        proposal = ProposedEffectivePlanningPolicy.model_validate_json(
            proposal.model_dump_json(exclude_none=False), strict=True
        )
        confirmed = ConfirmedTravelRequirement.model_validate_json(
            confirmed.model_dump_json(exclude_none=False), strict=True
        )
    except (TypeError, ValueError):
        raise ValueError("policy inputs failed fresh validation") from None
    preferences = confirmed.requirement.preferences
    configured_pace = preferences.pace
    agent_decides = preferences.agent_decides_pace
    if not agent_decides and configured_pace is not None and proposal.effective_pace is not configured_pace:
        raise _PolicyBusinessRejected("model pace conflicts with confirmed pace")
    existing_locked: EffectivePlanningPolicy | None = None
    if locked_policy is not None:
        if type(locked_policy) is not EffectivePlanningPolicy:
            raise ValueError("locked policy must use the public policy DTO")
        try:
            existing_locked = EffectivePlanningPolicy.model_validate_json(
                locked_policy.model_dump_json(exclude_none=False), strict=True
            )
            validate_effective_policy(existing_locked)
        except (TypeError, ValueError):
            raise ValueError("locked policy failed fresh validation") from None

    # MODULE09-STRUCTURED-DEMO-REPAIR-01 / D §8.2-§8.5: the first
    # structured-demo version does not interpret free-form time, duration,
    # meal, transport, mobility, or note text as planning policy.  Preserve
    # the typed proposal boundary, but reject any deferred rule tuple instead
    # of converting it into a durable authorization.
    if proposal.daily_time_rules or proposal.time_sensitive_must or proposal.transport_rules:
        raise _PolicyBusinessRejected("structured_demo_deferred_policy_rule_rejected")

    result = EffectivePlanningPolicy(
        effective_pace=proposal.effective_pace,
        daily_time_rules=(),
        time_sensitive_must=(),
        transport_rules=(),
        locked=True,
    )
    validate_effective_policy(result)
    if existing_locked is not None:
        if result != existing_locked:
            raise _PolicyLockedMismatch("locked effective policy cannot be modified")
        return existing_locked
    return result


def effective_policy_from_proposal(
    proposal: ProposedEffectivePlanningPolicy,
    *,
    confirmed: ConfirmedTravelRequirement,
    source_handle_indexes: dict[str, int] | None = None,
    locked_policy: EffectivePlanningPolicy | None = None,
) -> EffectivePlanningPolicy:
    return _effective_policy_from_proposal(
        proposal,
        confirmed=confirmed,
        source_handle_indexes=source_handle_indexes,
        locked_policy=locked_policy,
    )


def lock_effective_pace(
    proposal: ProposedEffectivePlanningPolicy,
    *,
    confirmed: ConfirmedTravelRequirement,
    locked_policy: EffectivePlanningPolicy | None = None,
) -> EffectivePlanningPolicy:
    """Lock only the selected pace when a deferred rule proposal is rejected."""
    if type(proposal) is not ProposedEffectivePlanningPolicy:
        raise ValueError("policy proposal must use the public proposal DTO")
    stripped = proposal.model_copy(update={
        "daily_time_rules": (),
        "time_sensitive_must": (),
        "transport_rules": (),
    })
    return _effective_policy_from_proposal(
        stripped,
        confirmed=confirmed,
        locked_policy=locked_policy,
    )


build_effective_policy = effective_policy_from_proposal
lock_effective_policy = effective_policy_from_proposal


__all__ = ()
