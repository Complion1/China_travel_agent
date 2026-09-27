from __future__ import annotations

import re
from datetime import date, datetime

from app.requirements.models import (
    HubType,
    InterestTag,
    ManualTemplateSubmission,
    RequirementPatch,
    TravelBudget,
    TravelConstraints,
    TravelParty,
    TravelPreferences,
    TravelRequirement,
    TravelRooms,
    TravelTimeWindow,
    TravelTimeWindowArrival,
    TravelTimeWindowDeparture,
    TravelPace,
)

MANUAL_TEMPLATE_VERSION = "1"
MANUAL_TEMPLATE_KEYS = [
    "目的城市",
    "正式游玩日期",
    "正式游玩天数",
    "到达机场/车站、日期、时间",
    "离开机场/车站、日期、时间",
    "成人/儿童/老人",
    "儿童年龄",
    "房间及房型",
    "行动限制",
    "饮食禁忌",
    "兴趣",
    "节奏",
]

_ALL_KEYS = ["模板版本", *MANUAL_TEMPLATE_KEYS]
_LINE_RE = re.compile(r"^(?P<key>[^：:]+)(?:：|:)(?P<value>.*)$")
_DATE_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+至\s+(\d{4}-\d{2}-\d{2})$")
_HUB_RE = re.compile(
    r"^(?P<hub>[^，,]+)[，,](?P<datetime>\d{4}-\d{2}-\d{2} \d{2}:\d{2})"
    r"(?:[，,](?P<approximate>大约))?$"
)
_ROOM_RE = re.compile(r"^(?P<count>\d+)\s*间[，,](?P<room_type>.+)$")

_INTEREST_TO_LABEL = {
    InterestTag.CULTURE: "人文",
    InterestTag.NATURE: "自然",
    InterestTag.FOOD: "美食",
    InterestTag.CHECKIN: "打卡",
}
_LABEL_TO_INTEREST = {label: value for value, label in _INTEREST_TO_LABEL.items()}
_PACE_TO_LABEL = {
    TravelPace.RELAXED: "轻松",
    TravelPace.NORMAL: "标准",
    TravelPace.INTENSE: "紧凑",
}
_LABEL_TO_PACE = {label: value for value, label in _PACE_TO_LABEL.items()}


def render_manual_template() -> str:
    """Render the fixed empty v1 template in canonical order."""
    return "\n".join([f"模板版本：{MANUAL_TEMPLATE_VERSION}", *(f"{key}：" for key in MANUAL_TEMPLATE_KEYS)])


def _interest_text(requirement: TravelRequirement) -> str:
    preferences = requirement.preferences
    if preferences.agent_decides_interests:
        if preferences.interests:
            raise ValueError("interests 与 agent_decides_interests 不能同时设置")
        return "由 Agent 决定"
    if not preferences.interests:
        return ""
    return "、".join(_INTEREST_TO_LABEL[item] for item in preferences.interests)


def _pace_text(requirement: TravelRequirement) -> str:
    preferences = requirement.preferences
    if preferences.agent_decides_pace:
        if preferences.pace is not None:
            raise ValueError("pace 与 agent_decides_pace 不能同时设置")
        return "由 Agent 决定"
    return _PACE_TO_LABEL[preferences.pace] if preferences.pace is not None else ""


def _hub_text(
    endpoint: TravelTimeWindowArrival | TravelTimeWindowDeparture,
) -> str:
    if endpoint.hub_name is None and endpoint.local_datetime is None:
        return ""
    base = f"{endpoint.hub_name or ''}，{endpoint.local_datetime or ''}"
    return f"{base}，大约" if endpoint.is_approximate else base


def serialize_manual_template(requirement: TravelRequirement) -> ManualTemplateSubmission:
    """Serialize every field represented by the fixed v1 template."""
    time_window = requirement.time_window
    party = requirement.party
    rooms = requirement.rooms
    values = {
        "目的城市": requirement.destination or "",
        "正式游玩日期": (
            f"{time_window.sightseeing_start_date} 至 {time_window.sightseeing_end_date}"
            if time_window.sightseeing_start_date and time_window.sightseeing_end_date
            else ""
        ),
        "正式游玩天数": str(time_window.sightseeing_days or ""),
        "到达机场/车站、日期、时间": _hub_text(time_window.arrival),
        "离开机场/车站、日期、时间": _hub_text(time_window.departure),
        "成人/儿童/老人": "/".join(
            "" if value is None else str(value)
            for value in (party.adults_count, party.children_count, party.seniors_count)
        ),
        "儿童年龄": "、".join(str(age) for age in party.children_ages),
        "房间及房型": (
            f"{rooms.room_count}间，{rooms.room_type_requirements}"
            if rooms.room_count is not None and rooms.room_type_requirements is not None
            else ""
        ),
        "行动限制": requirement.constraints.mobility_constraints or "",
        "饮食禁忌": requirement.constraints.dietary_restrictions or "",
        "兴趣": _interest_text(requirement),
        "节奏": _pace_text(requirement),
    }
    content = "\n".join(
        [
            f"模板版本：{MANUAL_TEMPLATE_VERSION}",
            *(f"{key}：{values[key]}" for key in MANUAL_TEMPLATE_KEYS),
        ]
    )
    return ManualTemplateSubmission(template_version=MANUAL_TEMPLATE_VERSION, content=content)


class ManualTemplateParseError(ValueError):
    def __init__(self, field_errors: dict[str, str]):
        self.field_errors = field_errors
        super().__init__(f"Template parse errors: {field_errors}")


def _add_error(errors: dict[str, str], key: str, message: str) -> None:
    errors[key] = f"{errors[key]}; {message}" if key in errors else message


def _validate_iso_date(raw: str, key: str, errors: dict[str, str]) -> None:
    try:
        date.fromisoformat(raw)
    except ValueError:
        _add_error(errors, key, f"无效日期：{raw}")


def _parse_hub(
    raw: str,
    key: str,
    errors: dict[str, str],
) -> TravelTimeWindowArrival | TravelTimeWindowDeparture | None:
    match = _HUB_RE.fullmatch(raw)
    if match is None:
        _add_error(errors, key, "格式应为 <枢纽>，<YYYY-MM-DD HH:MM>[，大约]")
        return None
    try:
        datetime.strptime(match.group("datetime"), "%Y-%m-%d %H:%M")
    except ValueError:
        _add_error(errors, key, "日期时间无效")
        return None
    hub_name = match.group("hub").strip()
    hub_type = (
        HubType.AIRPORT
        if hub_name.endswith("机场")
        else HubType.RAILWAY_STATION
        if hub_name.endswith("站")
        else None
    )
    values = {
        "hub_name": hub_name,
        "hub_type": hub_type,
        "local_datetime": match.group("datetime"),
        "is_approximate": match.group("approximate") is not None,
    }
    endpoint_type = TravelTimeWindowArrival if key.startswith("到达") else TravelTimeWindowDeparture
    return endpoint_type(**values)


def parse_manual_template(submission: ManualTemplateSubmission) -> RequirementPatch:
    """Parse a fixed v1 template into a presence-aware domain patch."""
    errors: dict[str, str] = {}
    if submission.template_version != MANUAL_TEMPLATE_VERSION:
        _add_error(errors, "模板版本", f"只支持版本 {MANUAL_TEMPLATE_VERSION}")

    lines = submission.content.splitlines()
    parsed: dict[str, str] = {}
    encountered: list[str] = []
    seen: dict[str, int] = {}

    for index, line in enumerate(lines, start=1):
        match = _LINE_RE.fullmatch(line)
        if match is None:
            _add_error(errors, f"第{index}行", "额外尾随文本或缺少冒号")
            continue
        key = match.group("key")
        value = match.group("value").strip()
        if key == "总预算":
            # Accept the legacy optional line without collecting an amount.
            # Duplicates remain an error, as for every other template key.
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                _add_error(errors, key, "重复键")
            continue
        if key not in _ALL_KEYS:
            _add_error(errors, f"第{index}行", f"未知键：{key}")
            continue
        encountered.append(key)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            _add_error(errors, key, "重复键")
            continue
        parsed[key] = value

    for key in _ALL_KEYS:
        if seen.get(key, 0) == 0:
            _add_error(errors, key, "缺少键")
    if encountered != _ALL_KEYS:
        _add_error(errors, "_order", "全部键必须按固定顺序且恰好出现一次")

    if parsed.get("模板版本") != MANUAL_TEMPLATE_VERSION:
        _add_error(errors, "模板版本", f"内容版本必须精确为 {MANUAL_TEMPLATE_VERSION}")

    required_non_empty = set(MANUAL_TEMPLATE_KEYS) - {"儿童年龄"}
    for key in required_non_empty:
        if key in parsed and not parsed[key]:
            _add_error(errors, key, "值不能为空")

    destination = parsed.get("目的城市") or None

    start_date: str | None = None
    end_date: str | None = None
    date_text = parsed.get("正式游玩日期", "")
    if date_text:
        match = _DATE_RANGE_RE.fullmatch(date_text)
        if match is None:
            _add_error(errors, "正式游玩日期", "格式应为 YYYY-MM-DD 至 YYYY-MM-DD")
        else:
            start_date, end_date = match.groups()
            _validate_iso_date(start_date, "正式游玩日期", errors)
            _validate_iso_date(end_date, "正式游玩日期", errors)

    sightseeing_days: int | None = None
    days_text = parsed.get("正式游玩天数", "")
    if days_text:
        try:
            sightseeing_days = int(days_text)
            if sightseeing_days <= 0:
                raise ValueError
        except ValueError:
            _add_error(errors, "正式游玩天数", "必须为正整数")

    arrival = _parse_hub(parsed["到达机场/车站、日期、时间"], "到达机场/车站、日期、时间", errors) if parsed.get("到达机场/车站、日期、时间") else None
    departure = _parse_hub(parsed["离开机场/车站、日期、时间"], "离开机场/车站、日期、时间", errors) if parsed.get("离开机场/车站、日期、时间") else None

    adults: int | None = None
    children: int | None = None
    seniors: int | None = None
    party_text = parsed.get("成人/儿童/老人", "")
    if party_text:
        parts = party_text.split("/")
        try:
            if len(parts) != 3 or any(not part.isdigit() for part in parts):
                raise ValueError
            adults, children, seniors = (int(part) for part in parts)
        except ValueError:
            _add_error(errors, "成人/儿童/老人", "格式应为 非负整数/非负整数/非负整数")

    child_ages: list[int] = []
    ages_text = parsed.get("儿童年龄", "")
    if ages_text:
        try:
            raw_ages = re.split(r"[、,，]", ages_text)
            if any(not item.strip().isdigit() for item in raw_ages):
                raise ValueError
            child_ages = [int(item.strip()) for item in raw_ages]
        except ValueError:
            _add_error(errors, "儿童年龄", "必须为中文顿号分隔的非负整数")
    if children is not None:
        if children == 0 and child_ages:
            _add_error(errors, "儿童年龄", "儿童人数为 0 时必须为空")
        elif children > 0 and len(child_ages) != children:
            _add_error(errors, "儿童年龄", f"年龄数量{len(child_ages)}与儿童人数{children}不一致")

    room_count: int | None = None
    room_type: str | None = None
    room_text = parsed.get("房间及房型", "")
    if room_text:
        match = _ROOM_RE.fullmatch(room_text)
        if match is None or int(match.group("count")) <= 0 or not match.group("room_type").strip():
            _add_error(errors, "房间及房型", "格式应为 <正整数>间，<非空房型>")
        else:
            room_count = int(match.group("count"))
            room_type = match.group("room_type").strip()

    interests: list[InterestTag] | None = None
    agent_decides_interests = False
    interests_text = parsed.get("兴趣", "")
    if interests_text == "由 Agent 决定":
        agent_decides_interests = True
    elif interests_text:
        labels = [item.strip() for item in re.split(r"[、,，]", interests_text)]
        unknown = [label for label in labels if label not in _LABEL_TO_INTEREST]
        if unknown or len(set(labels)) != len(labels):
            _add_error(errors, "兴趣", "只允许人文、自然、美食、打卡且不得重复")
        else:
            interests = [_LABEL_TO_INTEREST[label] for label in labels]

    pace: TravelPace | None = None
    agent_decides_pace = False
    pace_text = parsed.get("节奏", "")
    if pace_text == "由 Agent 决定":
        agent_decides_pace = True
    elif pace_text:
        pace = _LABEL_TO_PACE.get(pace_text)
        if pace is None:
            _add_error(errors, "节奏", "只允许轻松、标准、紧凑或由 Agent 决定")

    if errors:
        raise ManualTemplateParseError(errors)

    time_window = TravelTimeWindow(
        arrival=arrival,
        departure=departure,
        sightseeing_start_date=start_date,
        sightseeing_end_date=end_date,
        sightseeing_days=sightseeing_days,
    )
    party = TravelParty(
        adults_count=adults,
        children_count=children,
        children_ages=child_ages,
        seniors_count=seniors,
    )
    preferences = TravelPreferences(
        interests=interests,
        pace=pace,
        agent_decides_interests=agent_decides_interests,
        agent_decides_pace=agent_decides_pace,
    )
    return RequirementPatch(
        destination=destination,
        time_window=time_window,
        budget=TravelBudget(),
        party=party,
        rooms=TravelRooms(room_count=room_count, room_type_requirements=room_type),
        constraints=TravelConstraints(
            mobility_constraints=parsed["行动限制"],
            dietary_restrictions=parsed["饮食禁忌"],
        ),
        preferences=preferences,
    )
