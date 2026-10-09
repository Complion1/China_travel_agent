from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Optional

from app.requirements.models import (
    NormalizationError,
    RequirementFieldPath,
    RequirementIssue,
    TravelRequirement,
    TravelTimeWindow,
)


_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_CHINESE_DATE = r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]"


def _parse_cn_integer(text: str) -> Optional[int]:
    if text.isdigit():
        return int(text)
    if text in _CN_DIGITS:
        return _CN_DIGITS[text]
    if text == "十":
        return 10
    if "十" in text:
        left, right = text.split("十", 1)
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def normalize_relative_date(raw: str, session_date: date) -> tuple[str, bool]:
    text = raw.strip()
    offsets = {"今天": 0, "明天": 1, "后天": 2}
    if text in offsets:
        return (session_date + timedelta(days=offsets[text])).isoformat(), True

    match = re.fullmatch(_CHINESE_DATE, text)
    if match:
        try:
            normalized = date(int(match.group(1)) if match.group(1) else session_date.year,
                              int(match.group(2)), int(match.group(3)))
        except ValueError as exc:
            raise NormalizationError(f"invalid_date: {text}") from exc
        return normalized.isoformat(), False

    try:
        return date.fromisoformat(text).isoformat(), False
    except ValueError as exc:
        raise NormalizationError(f"ambiguous_or_invalid_date: {text}") from exc


def normalize_local_datetime(raw: str, session_date: date) -> tuple[str, bool, bool]:
    """Parse one explicit local date/time, preserving relative/approximate flags.

    Calendar and clock validation stay strict. Unsupported or contradictory
    day-period wording is a clarification, never an inferred time of day.
    """
    number = r"[0-9零一二两三四五六七八九十]+"
    matched = re.fullmatch(
        rf"(?P<date>今天|明天|后天|\d{{4}}-\d{{2}}-\d{{2}}|{_CHINESE_DATE})\s*"
        rf"(?P<period>上午|下午|晚上|中午|凌晨|早上|傍晚)?\s*"
        rf"(?P<hour>{number})\s*(?:(?P<colon>[:：])\s*(?P<minute>\d{{2}})|"
        rf"点(?:(?P<fraction>半|一刻|三刻)|\s*(?P<cn_minute>{number})\s*分?)?)"
        r"\s*(?P<approximate>左右)?", raw.strip(),
    )
    if matched is None:
        raise NormalizationError("ambiguous_or_invalid_datetime")
    normalized_date, relative = normalize_relative_date(matched.group("date"), session_date)
    hour = _parse_cn_integer(matched.group("hour"))
    minute = _parse_cn_integer(matched.group("minute") or matched.group("cn_minute") or "0")
    fractions = {"半": 30, "一刻": 15, "三刻": 45}
    if matched.group("fraction"):
        minute = fractions[matched.group("fraction")]
    if hour is None or minute is None or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise NormalizationError("invalid_clock_time")
    period = matched.group("period")
    if period in {"下午", "晚上", "傍晚"} and 1 <= hour <= 11:
        hour += 12
    elif period == "中午" and hour in {1, 2}:
        hour += 12
    allowed = {"上午": range(1, 12), "早上": range(5, 12), "凌晨": range(0, 6),
               "中午": range(11, 15), "下午": range(12, 19), "傍晚": range(17, 20),
               "晚上": range(18, 24)}
    if period is not None and hour not in allowed[period]:
        raise NormalizationError("conflicting_day_period")
    if period is None and matched.group("colon") is None and 1 <= hour <= 12:
        raise NormalizationError("ambiguous_day_period")
    return f"{normalized_date} {hour:02d}:{minute:02d}", relative, matched.group("approximate") is not None


def normalize_budget(raw: str) -> tuple[float, bool]:
    text = raw.strip().replace(",", "").replace("，", "")
    approximate = "左右" in text or "约" in text or "大约" in text
    number_text = re.sub(r"(?:左右|大约|约)", "", text).strip()
    match = re.fullmatch(r"([\d]+(?:\.\d+)?|[零一二两三四五六七八九十]+)\s*([千万]?)", number_text)
    if not match:
        raise NormalizationError(f"invalid_budget: {raw}")
    token = match.group(1)
    base = float(token) if re.fullmatch(r"\d+(?:\.\d+)?", token) else _parse_cn_integer(token)
    if base is None:
        raise NormalizationError(f"invalid_budget: {raw}")
    multiplier = {"": 1, "千": 1_000, "万": 10_000}[match.group(2)]
    value = float(base * multiplier)
    if not math.isfinite(value) or value <= 0:
        raise NormalizationError(f"invalid_budget: {raw}")
    return value, approximate


def infer_hub_type(name: str) -> Optional[str]:
    normalized = name.strip()
    if not normalized:
        return None
    if re.fullmatch(r".+机场(?:\s*[Tt]?\d+航站楼)?", normalized):
        return "airport"
    if (normalized.endswith("站") and normalized not in {"站", "车站", "火车站", "高铁站", "铁路站", "铁路车站"}
            and not any(marker in normalized for marker in ("汽车", "客运", "公交", "地铁"))):
        return "railway_station"
    return None


def _issue(code: str, field_path: str, message: str) -> RequirementIssue:
    return RequirementIssue(
        code=code,
        field_path=RequirementFieldPath(field_path),
        message=message,
    )


def validate_requirement_hubs(req: TravelRequirement) -> tuple[RequirementIssue, ...]:
    """Validate endpoint completeness without changing historical DTOs or guessing geography."""
    issues: list[RequirementIssue] = []
    for role, label in (("arrival", "到达"), ("departure", "离开")):
        hub = getattr(req.time_window, role)
        prefix = f"time_window.{role}"
        inferred = infer_hub_type(hub.hub_name) if hub.hub_name else None
        if not hub.hub_name or not hub.hub_name.strip():
            issues.append(_issue("hub_name_missing", f"{prefix}.hub_name", f"请填写{label}目的城市的具体机场或火车站名称"))
        elif inferred is None:
            issues.append(_issue("hub_name_not_specific", f"{prefix}.hub_name", f"请填写{label}目的城市的具体机场或火车站全名，不能只填写城市名"))
        if hub.hub_type is None:
            issues.append(_issue("hub_type_missing", f"{prefix}.hub_type", f"请选择{label}枢纽类型：机场或火车站/高铁站"))
        elif inferred is not None and inferred != hub.hub_type.value:
            for field in ("hub_name", "hub_type"):
                issues.append(_issue("hub_type_mismatch", f"{prefix}.{field}", f"{label}枢纽名称与类型不一致，请核对具体名称及类型"))
        if not hub.local_datetime or not hub.local_datetime.strip():
            issues.append(_issue("hub_time_missing", f"{prefix}.local_datetime", f"请填写{label}目的城市的日期与时间"))
    return tuple(issues)


def _parse_date(value: Optional[str], field_path: str, issues: list[RequirementIssue]) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        issues.append(_issue("invalid_date", field_path, "日期格式必须为 YYYY-MM-DD"))
        return None


def _parse_datetime(
    value: Optional[str], field_path: str, issues: list[RequirementIssue]
) -> Optional[datetime]:
    if value is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        issues.append(_issue("invalid_datetime", field_path, "日期时间格式必须为 YYYY-MM-DD HH:MM"))
        return None
    return parsed


def validate_time_window(tw: TravelTimeWindow) -> tuple[RequirementIssue, ...]:
    issues: list[RequirementIssue] = []
    start = _parse_date(tw.sightseeing_start_date, "time_window.sightseeing_start_date", issues)
    end = _parse_date(tw.sightseeing_end_date, "time_window.sightseeing_end_date", issues)
    check_in = _parse_date(tw.hotel_check_in_date, "time_window.hotel_check_in_date", issues)
    check_out = _parse_date(tw.hotel_check_out_date, "time_window.hotel_check_out_date", issues)
    arrival = _parse_datetime(tw.arrival.local_datetime, "time_window.arrival.local_datetime", issues)
    departure = _parse_datetime(tw.departure.local_datetime, "time_window.departure.local_datetime", issues)

    if start is not None and end is not None:
        if end < start:
            issues.append(_issue("invalid_date_order", "time_window.sightseeing_end_date", "游玩结束日期不能早于开始日期"))
        else:
            computed_days = (end - start).days + 1
            if tw.sightseeing_days is not None and tw.sightseeing_days != computed_days:
                issues.append(_issue(
                    "days_mismatch",
                    "time_window.sightseeing_days",
                    f"游玩天数{tw.sightseeing_days}与日期范围{computed_days}天不符",
                ))
    if tw.sightseeing_days is not None and tw.sightseeing_days <= 0:
        issues.append(_issue("value_out_of_range", "time_window.sightseeing_days", "游玩天数必须大于0"))

    if arrival is not None and start is not None and arrival.date() > start:
        issues.append(_issue("arrival_after_sightseeing_start", "time_window.arrival.local_datetime", "抵达时间不能晚于游玩开始日"))
    if departure is not None and end is not None and departure.date() < end:
        issues.append(_issue("departure_before_sightseeing_end", "time_window.departure.local_datetime", "离开时间不能早于游玩结束日"))

    if check_in is not None and check_out is not None:
        if check_out < check_in:
            issues.append(_issue("invalid_hotel_date_order", "time_window.hotel_check_out_date", "退房日期不能早于入住日期"))
        else:
            computed_nights = (check_out - check_in).days
            if tw.hotel_nights is not None and tw.hotel_nights != computed_nights:
                issues.append(_issue("hotel_nights_mismatch", "time_window.hotel_nights", "住宿晚数与入住退房日期不一致"))
    if tw.hotel_nights is not None and tw.hotel_nights < 0:
        issues.append(_issue("value_out_of_range", "time_window.hotel_nights", "住宿晚数不能小于0"))
    return tuple(issues)


def validate_party(
    adults: Optional[int],
    children: Optional[int],
    children_ages: list[int],
    seniors: Optional[int],
) -> tuple[RequirementIssue, ...]:
    issues: list[RequirementIssue] = []
    counts = {
        "party.adults_count": adults,
        "party.children_count": children,
        "party.seniors_count": seniors,
    }
    for field_path, value in counts.items():
        if value is not None and not 0 <= value <= 100:
            issues.append(_issue("value_out_of_range", field_path, "人数必须在0到100之间"))

    if adults is not None and adults < 1:
        issues.append(
            _issue("adults_required", "party.adults_count", "成人数量必须至少为1")
        )

    total = sum(value or 0 for value in counts.values())
    if all(value is not None for value in counts.values()):
        if total <= 0:
            issues.append(
                _issue(
                    "no_people",
                    RequirementFieldPath.TOTAL_PEOPLE.value,
                    "总人数必须大于0",
                )
            )
        elif total > 100:
            issues.append(_issue("value_out_of_range", "party.total_people", "总人数不能超过100"))

    for age in children_ages:
        if not 0 <= age <= 17:
            issues.append(_issue("value_out_of_range", "party.children_ages", "儿童年龄必须在0到17岁之间"))
            break
    if children is not None:
        if children == 0 and children_ages:
            issues.append(_issue("children_age_mismatch", "party.children_ages", "儿童人数为0时年龄必须为空"))
        elif children > 0 and len(children_ages) != children:
            issues.append(_issue("children_age_mismatch", "party.children_ages", f"儿童人数{children}与年龄数量{len(children_ages)}不一致"))
    if len(children_ages) > 100:
        issues.append(_issue("value_out_of_range", "party.children_ages", "儿童年龄数量不能超过100"))
    return tuple(issues)


def validate_budget(total_cny: Optional[float]) -> tuple[RequirementIssue, ...]:
    if total_cny is None:
        return ()
    if not math.isfinite(total_cny) or total_cny <= 0:
        return (_issue("invalid_budget", "budget.total_cny", "预算必须是大于0的有限数值"),)
    return ()


def validate_rooms(room_count: Optional[int], total_people: int) -> tuple[RequirementIssue, ...]:
    if room_count is None:
        return ()
    issues: list[RequirementIssue] = []
    if not 1 <= room_count <= 100:
        issues.append(_issue("value_out_of_range", "rooms.room_count", "房间数必须在1到100之间"))
    elif total_people > room_count * 4:
        issues.append(_issue("rooms_too_few", "rooms.room_count", f"{room_count}间房可能无法容纳{total_people}人"))
    return tuple(issues)


def validate_requirement_bounds(req: TravelRequirement) -> tuple[RequirementIssue, ...]:
    issues: list[RequirementIssue] = []
    string_limits: tuple[tuple[str, Optional[str], int], ...] = (
        ("destination", req.destination, 64),
        ("time_window.arrival.hub_name", req.time_window.arrival.hub_name, 128),
        ("time_window.departure.hub_name", req.time_window.departure.hub_name, 128),
        ("rooms.room_type_requirements", req.rooms.room_type_requirements, 500),
        ("constraints.mobility_constraints", req.constraints.mobility_constraints, 500),
        ("constraints.dietary_restrictions", req.constraints.dietary_restrictions, 500),
        ("preferences.transport_preferences", req.preferences.transport_preferences, 500),
        ("preferences.hotel_preferences", req.preferences.hotel_preferences, 500),
        ("preferences.daily_time_preferences", req.preferences.daily_time_preferences, 500),
        ("preferences.free_text_notes", req.preferences.free_text_notes, 500),
    )
    for field_path, value, limit in string_limits:
        if value is not None and not 1 <= len(value) <= limit:
            issues.append(_issue("value_out_of_range", field_path, f"文本长度必须在1到{limit}之间"))
    for field_path, values in (
        ("preferences.must_visit_places", req.preferences.must_visit_places),
        ("preferences.avoid_places", req.preferences.avoid_places),
    ):
        if len(values) > 100:
            issues.append(_issue("value_out_of_range", field_path, "列表项不能超过100个"))
        if any(not 1 <= len(value) <= 500 for value in values):
            issues.append(_issue("value_out_of_range", field_path, "每项文本长度必须在1到500之间"))
    return tuple(issues)
