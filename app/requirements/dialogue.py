from __future__ import annotations

from app.requirements.models import (
    HubType,
    InterestTag,
    RequirementIssue,
    TravelPace,
    TravelRequirement,
)

CLARIFICATION_TEMPLATE = "请补充以下信息：\n\n{items}\n\n请将上述信息一次性提供。"

FIELD_LABELS: dict[str, str] = {
    "destination": "目的城市",
    "time_window.sightseeing_start_date": "正式游玩开始日期",
    "time_window.sightseeing_end_date": "正式游玩结束日期",
    "time_window.sightseeing_days": "正式游玩天数",
    "time_window.arrival.hub_name": "到达机场/车站",
    "time_window.arrival.hub_type": "到达枢纽类型（机场或火车站/高铁站）",
    "time_window.arrival.local_datetime": "到达日期与时间",
    "time_window.departure.hub_name": "离开机场/车站",
    "time_window.departure.hub_type": "离开枢纽类型（机场或火车站/高铁站）",
    "time_window.departure.local_datetime": "离开日期与时间",
    "budget.total_cny": "目的地内总预算（人民币）",
    "party.adults_count": "成人数量",
    "party.children_count": "儿童数量",
    "party.children_ages": "儿童年龄",
    "party.seniors_count": "老人数量",
    "rooms.room_count": "房间数量",
    "rooms.room_type_requirements": "房型要求",
    "constraints.mobility_constraints": "行动限制（如无可填“无”）",
    "constraints.dietary_restrictions": "饮食禁忌（如无可填“无”）",
}

INTEREST_LABELS = {
    InterestTag.CULTURE: "人文",
    InterestTag.NATURE: "自然",
    InterestTag.FOOD: "美食",
    InterestTag.CHECKIN: "打卡",
}
PACE_LABELS = {
    TravelPace.RELAXED: "轻松",
    TravelPace.NORMAL: "标准",
    TravelPace.INTENSE: "紧凑",
}
HUB_TYPE_LABELS = {
    HubType.AIRPORT: "机场",
    HubType.RAILWAY_STATION: "火车站/高铁站",
}


def render_clarification(
    missing_fields: list[str],
    conflicts: list[RequirementIssue],
    needs_interests: bool,
    needs_pace: bool,
) -> str:
    parts: list[str] = []
    if missing_fields:
        parts.append("**缺失信息：**")
        for field_path in missing_fields:
            parts.append(f"- {FIELD_LABELS.get(field_path, field_path)}")
    if conflicts:
        parts.append("**需要确认：**")
        for conflict in conflicts:
            parts.append(f"- {conflict.message}")
    if needs_interests:
        parts.append("**兴趣偏好（可多选）：** 人文、自然、美食、打卡\n如无特别偏好，可填写“由 Agent 决定”")
    if needs_pace:
        parts.append("**行程节奏：** 轻松 / 标准 / 紧凑\n如无特别偏好，可填写“由 Agent 决定”")
    return CLARIFICATION_TEMPLATE.format(items="\n".join(parts))


def _optional(value: object | None) -> str:
    return "未填写" if value is None or value == "" else str(value)


def _list_or_none(values: list[object]) -> str:
    return "、".join(str(value) for value in values) if values else "无"


def render_confirmation_summary(req: TravelRequirement, *, collection_version="1.0") -> str:
    """Render every business field in a fixed, deterministic order."""
    time_window = req.time_window
    arrival = time_window.arrival
    departure = time_window.departure
    party = req.party
    preferences = req.preferences

    if preferences.interests:
        interests = "、".join(INTEREST_LABELS[item] for item in preferences.interests)
    elif preferences.agent_decides_interests:
        interests = "由 Agent 决定"
    else:
        interests = "未填写"

    if preferences.pace is not None:
        pace = PACE_LABELS[preferences.pace]
    elif preferences.agent_decides_pace:
        pace = "由 Agent 决定"
    else:
        pace = "未填写"

    lines = [
        "== 请确认以下旅行需求 ==",
        f"目的城市：{_optional(req.destination)}",
        f"抵达枢纽：{_optional(arrival.hub_name)}",
        f"抵达枢纽类型：{HUB_TYPE_LABELS.get(arrival.hub_type, '未填写')}",
        f"抵达时间：{_optional(arrival.local_datetime)}",
        f"抵达时间约数：{'是' if arrival.is_approximate else '否'}",
        f"正式游玩开始日期：{_optional(time_window.sightseeing_start_date)}",
        f"正式游玩结束日期：{_optional(time_window.sightseeing_end_date)}",
        f"正式游玩天数：{_optional(time_window.sightseeing_days)}",
        f"酒店入住日期：{_optional(time_window.hotel_check_in_date)}",
        f"酒店退房日期：{_optional(time_window.hotel_check_out_date)}",
        f"住宿晚数：{_optional(time_window.hotel_nights)}",
        f"离开枢纽：{_optional(departure.hub_name)}",
        f"离开枢纽类型：{HUB_TYPE_LABELS.get(departure.hub_type, '未填写')}",
        f"离开时间：{_optional(departure.local_datetime)}",
        f"离开时间约数：{'是' if departure.is_approximate else '否'}",
        f"成人数量：{_optional(party.adults_count)}",
        f"儿童数量：{_optional(party.children_count)}",
        f"儿童年龄：{_list_or_none(party.children_ages)}",
        f"老人数量：{_optional(party.seniors_count)}",
        f"总人数：{_optional(party.total_people)}",
        f"房间数量：{_optional(req.rooms.room_count)}",
        f"房型要求：{_optional(req.rooms.room_type_requirements)}",
        f"行动限制：{_optional(req.constraints.mobility_constraints)}",
        f"饮食禁忌：{_optional(req.constraints.dietary_restrictions)}",
        f"兴趣：{interests}",
        f"节奏：{pace}",
        f"交通偏好：{_optional(preferences.transport_preferences)}",
        f"酒店偏好：{_optional(preferences.hotel_preferences)}",
        f"必去地点：{_list_or_none(preferences.must_visit_places)}",
        f"避开地点：{_list_or_none(preferences.avoid_places)}",
        f"每日作息偏好：{_optional(preferences.daily_time_preferences)}",
        f"补充说明：{_optional(preferences.free_text_notes)}",
        f"派生同行场景：{_list_or_none(req.inferred_scenarios)}",
        "尚存假设：无",
        "请回复“确认”以继续，或补充修改信息。",
    ]
    # Preserve summaries (and their digests) for pre-deferral stored states.
    # New business inputs no longer populate these legacy fields.
    if req.budget.total_cny is not None:
        index = lines.index(f"成人数量：{_optional(party.adults_count)}")
        lines[index:index] = [
            f"目的地内总预算：{req.budget.total_cny:g} 元",
            f"预算约数：{'是' if req.budget.is_approximate else '否'}",
        ]
    if collection_version == "1.1":
        deferred_labels = {
            "成人数量", "儿童数量", "儿童年龄", "老人数量", "总人数",
            "房间数量", "房型要求", "行动限制", "饮食禁忌", "派生同行场景",
            "目的地内总预算", "预算约数",
        }
        lines = [line for line in lines if line.split("：", 1)[0] not in deferred_labels]
    return "\n".join(lines)
