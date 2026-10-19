"""zh-guide-v1：仅对已关联的结果 DTO 渲染确定性中文 Markdown。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .artifacts import ResultCore, ResultDayData, ResultInterval, ResultPlace, ResultRoute, ResultTransitRoute, ResultVisit, ResultPlannedBreak
from .errors import ResultFormattingFailureCode as Code
from .projection import ProjectionFailure


_BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
_MARKDOWN_MARKS = frozenset("\\`*_{}[]()#+-.!|:/")
_DAY_KINDS = {"full": "完整游览日", "partial": "部分游览日", "transit_only": "纯交通日"}
_VISIT_KINDS = {
    "attraction": "景点", "lunch": "午餐", "dinner": "晚餐",
    "attraction_lunch": "景点兼午餐", "attraction_dinner": "景点兼晚餐",
}
_TRANSPORT = {
    "walking": "步行", "cycling": "骑行", "public_transit": "公共交通",
    "ride_hailing": "网约车（驾车路线估算，叫车可用性及费用未核验）", "private_car": "自驾",
}
_MATRIX_MODES = {"walking": "步行", "riding": "骑行", "driving": "驾车"}
_CAPABILITIES = {
    "place_search": "地点搜索", "place_details": "地点详情", "geocoding": "地理编码",
    "route_matrix": "路线矩阵", "directions": "路线规划",
}
_SCOPE_NOTICE = "本次完成的是首版支持范围内的行程规划；文本中的作息、活动时间或时长、交通限制等额外要求未纳入本次规划验证。"


def _escape(value: str) -> str:
    """逐字符处理数据；转义生成的标记不会再次参与转义。"""
    parts: list[str] = []
    previous_cr = False
    for character in value:
        if character == "\n" and previous_cr:
            previous_cr = False
            continue
        previous_cr = character == "\r"
        if character in "\r\n":
            parts.append("↵")
        elif character == "\t":
            parts.append("⇥")
        else:
            codepoint = ord(character)
            if (codepoint < 32 or 0x7F <= codepoint <= 0x9F or 0x202A <= codepoint <= 0x202E
                    or 0x2066 <= codepoint <= 0x2069 or 0xD800 <= codepoint <= 0xDFFF):
                raise ProjectionFailure(Code.UNSAFE_TEXT) from None
            if character in "<>&":
                parts.append({"<": "&lt;", ">": "&gt;", "&": "&amp;"}[character])
            elif character in _MARKDOWN_MARKS:
                parts.append("\\" + character)
            else:
                parts.append(character)
    return "".join(parts)


def _decimal(value: float | Decimal) -> str:
    result = format(Decimal(str(value)), "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _duration(seconds: int | Decimal) -> str:
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    if remainder == 0:
        return f"{minutes} 分钟"
    return f"{minutes} 分 {_decimal(Decimal(remainder))} 秒"


def _local_time(value: datetime, *, seconds: bool = False) -> str:
    local = value.astimezone(_BEIJING)
    # Always show calendar dates, including intervals that cross midnight.
    text = local.strftime("%Y-%m-%d %H:%M")
    if seconds or local.second or local.microsecond:
        text += local.strftime(":%S")
        if local.microsecond:
            text += "." + f"{local.microsecond:06d}".rstrip("0")
    return text


def _interval(value: ResultInterval) -> str:
    return f"{_local_time(value.start_at_utc)}—{_local_time(value.end_at_utc)}（北京时间）"


def _sources(source_ids: tuple[int, ...]) -> str:
    return "".join(f"〔来源 {source_id}〕" for source_id in source_ids)


def _place_map(core: ResultCore) -> dict[int, ResultPlace]:
    return {place.place_id: place for place in core.places}


def _route_line(route: ResultRoute, places: dict[int, ResultPlace]) -> str:
    origin = _escape(places[route.origin_place_id].name)
    destination = _escape(places[route.destination_place_id].name)
    summary = (f"- {_interval(route.interval)}｜交通：{origin} → {destination}；"
            f"{_TRANSPORT[route.transport_semantic]}；{route.distance_m} 米；"
            f"{_duration(route.duration_s)}（百度获取时路线估算）〔来源 {route.source_id}〕；"
            f"日程预留 {route.scheduled_minutes} 分钟；费用未核验。")
    if not isinstance(route, ResultTransitRoute):
        return summary
    steps = "\n".join(f"  {index}. {_escape(instruction)}" for index, instruction in enumerate(route.transit_instructions, 1))
    return summary + "\n\n  乘车顺序（查询时路线说明，班次与实时运营未核验）：\n\n" + steps


def _business(place: ResultPlace, state: str, business_source_ids: tuple[int, ...]) -> str:
    notice = "营业时间未核验，请出行前确认" if state == "unknown" else "规划时营业证据相容，实时营业仍请确认"
    text = notice + _sources(business_source_ids) + "。"
    if place.business_hours:
        text += "营业文本：" + "；".join(_escape(item) for item in place.business_hours)
        if place.business_hours_source_id is not None:
            text += f"〔来源 {place.business_hours_source_id}〕"
        text += "。"
    return text


def _visit_line(visit: ResultVisit, places: dict[int, ResultPlace]) -> str:
    place = places[visit.place_id]
    elapsed = visit.interval.end_at_utc - visit.interval.start_at_utc
    seconds = Decimal(elapsed.days * 86400 + elapsed.seconds) + Decimal(elapsed.microseconds) / 1_000_000
    return (f"- {_interval(visit.interval)}｜{_VISIT_KINDS[visit.visit_kind]}：{_escape(place.name)}；"
            f"地点身份已核验〔来源 {place.identity_source_id}〕；地址：{_escape(place.address)}；"
            f"活动时长 {_duration(seconds)}（规划估算）。"
            + _business(place, visit.business_hours_state, visit.business_source_ids))


def _hub_line(core: ResultCore, *, arrival: bool) -> str:
    endpoint = core.requirement.arrival if arrival else core.requirement.departure
    transfer = core.arrival_transfer if arrival else core.departure_transfer
    place = _place_map(core)[transfer.hub_place_id]
    label = "抵达" if arrival else "返程"
    approximate = "约 " if endpoint.is_approximate else ""
    return (f"- 已确认{label}：{approximate}{endpoint.local_datetime}（北京时间）；枢纽：{_escape(endpoint.hub_name)}"
            f"（名称由用户确认，位置由百度地理编码〔来源 {place.identity_source_id}〕）；"
            f"地址：{_escape(place.address)}。")


def _transfer_allowance(core: ResultCore, *, arrival: bool) -> str:
    transfer = core.arrival_transfer if arrival else core.departure_transfer
    label = "抵达" if arrival else "返程"
    return (f"- {label}处理预留：{transfer.processing_minutes} 分钟；"
            f"近似时刻额外预留：{transfer.approximate_extra_minutes} 分钟"
            "（均为规划预留，并非机场/车站官方政策）。")


def render_header(core: ResultCore, *, template_version="zh-guide-v1") -> str:
    requirement = core.requirement
    hotel = core.places[0]
    ages = "、".join(f"{age} 岁" for age in requirement.children_ages) or "未记录"
    lines = [
        f"# {_escape(requirement.destination)}旅行攻略",
        "",
        f"- 游览日期：{requirement.sightseeing_start_date.isoformat()}—{requirement.sightseeing_end_date.isoformat()}；共 {requirement.sightseeing_days} 天。",
        f"- 同行人员：共 {requirement.total_people} 人；成人 {requirement.adults_count} 人、儿童 {requirement.children_count} 人、老人 {requirement.seniors_count} 人；儿童年龄：{ages}。",
        f"- 住宿日期：{requirement.hotel_check_in_date.isoformat()} 入住，{requirement.hotel_check_out_date.isoformat()} 离店；{requirement.hotel_nights} 晚，{requirement.room_count} 间房。",
        f"- 行程节奏：{core.effective_pace}。",
        "",
        "## 已确认酒店",
        "",
        f"- {_escape(hotel.name)}；地址：{_escape(hotel.address)}；地点身份已核验〔来源 {hotel.identity_source_id}〕。",
        "- 酒店仅核验地点身份；实时房态、可订房型、取消政策和价格未核验。行李寄存需自行向酒店确认。",
        "",
        "## 抵达枢纽到酒店",
        "",
        _hub_line(core, arrival=True),
        _route_line(core.arrival_transfer.route, _place_map(core)),
        _transfer_allowance(core, arrival=True),
    ]
    # Existing numeric artifacts retain their exact historical rendering.
    # Active collection is deferred, so new runs reach this with a null amount.
    if requirement.budget_total_cny is not None:
        budget = ("约 " if requirement.budget_is_approximate else "") + _decimal(requirement.budget_total_cny)
        lines.insert(5, f"- 用户总预算：{budget} 元；范围为目的地内住宿、当地交通、餐饮、景点和其他当地消费，不含往返目的地的大交通。")
    if template_version in {"zh-guide-v2-active-inputs", "zh-guide-v3-boundary-quality", "zh-guide-v4-daily-quality"}:
        lines = [line for line in lines if not line.startswith(("- 同行人员：", "- 用户总预算："))]
        lines[3] = (f"- 住宿日期：{requirement.hotel_check_in_date.isoformat()} 入住，"
                    f"{requirement.hotel_check_out_date.isoformat()} 离店；{requirement.hotel_nights} 晚。")
    return "\n".join(lines)


def render_day(core: ResultCore, day: ResultDayData) -> str:
    places = _place_map(core)
    lines = [f"## 第 {day.day_no} 天｜{day.date.isoformat()}｜{_escape(day.theme)}", "",
             f"- 日类型：{_DAY_KINDS[day.day_kind]}。", "- 天气未获取。"]
    if day.open_intervals:
        lines.append("- 可用游览时段：" + "；".join(_interval(interval) for interval in day.open_intervals) + "。")
    if day.day_kind == "transit_only":
        lines.append("- 本日未安排游览。")
    timeline = [(route.interval.start_at_utc, index * 2, route) for index, route in enumerate(day.route_legs)]
    timeline.extend((visit.interval.start_at_utc, index * 2 + 1, visit) for index, visit in enumerate(day.visits))
    timeline.extend((item.interval.start_at_utc, 100 + index, item)
                    for index, item in enumerate(getattr(day, "planned_breaks", ())))
    if timeline:
        lines.extend(["", "### 主行程", ""])
        for _, _, item in sorted(timeline, key=lambda entry: (entry[0], entry[1])):
            if isinstance(item, ResultPlannedBreak):
                label = "休息" if item.kind == "rest" else "自由活动"
                lines.append(f"- {_interval(item.interval)}｜{label}（规划安排）：{_escape(item.reason)}。")
            else:
                lines.append(_route_line(item, places) if isinstance(item, ResultRoute) else _visit_line(item, places))
    issues = getattr(day, "quality_issues", ())
    if issues:
        lines.extend(["", "### 尚待完善的安排", ""])
        for item in issues:
            if item.rule == "unexplained_gap":
                message = f"{_interval(item.interval)} 内仍有 {item.minutes} 分钟未说明的空档，建议补充安排。"
            elif item.rule == "excessive_break":
                message = f"本日休息或自由活动累计 {item.minutes} 分钟，安排可能偏少，建议按体力和兴趣复核。"
            else:
                meal = "午餐" if item.rule == "missing_lunch" else "晚餐"
                message = f"可用时间覆盖{meal}时段，当前未安排{meal}；请补充并核验营业及路线。"
            lines.append("- " + message)
    if day.backups:
        lines.extend(["", "### 附近备选", "", "以下备选未纳入主路线；距离为对应模式的路网距离。", ""])
        visits = {visit.visit_id: visit for visit in day.visits}
        for backup in day.backups:
            place = places[backup.place_id]
            main = places[visits[backup.core_visit_id].place_id]
            lines.append(
                f"- {_VISIT_KINDS[backup.backup_kind]}备选：{_escape(place.name)}（对应主访问：{_escape(main.name)}）；"
                f"地点身份已核验〔来源 {place.identity_source_id}〕；地址：{_escape(place.address)}；"
                f"从主访问到备选 {backup.distance_m} 米（{_MATRIX_MODES[backup.matrix_mode]}路网距离〔来源 {backup.source_id}〕）。"
                + _business(place, backup.business_hours_state, backup.business_source_ids)
            )
    return "\n".join(lines)


def render_footer(core: ResultCore, *, template_version="zh-guide-v1") -> str:
    requirement = core.requirement
    lines = ["## 酒店到返程枢纽", "", _hub_line(core, arrival=False),
             _route_line(core.departure_transfer.route, _place_map(core)),
             _transfer_allowance(core, arrival=False), "", "## 费用核验状态", ""]
    lines.extend(f"- {category}：未核验。" for category in ("住宿", "当地交通", "餐饮", "景点", "其他当地消费"))
    if getattr(core, "quality_issue_count", 0):
        subject = "每日安排" if template_version == "zh-guide-v4-daily-quality" else "抵离日"
        lines.extend(["", "## 规划安排提醒", "",
            f"- {subject}仍有 {core.quality_issue_count} 项安排需要完善，详情见对应日期；通过时间和路线硬校验不表示这些问题已经解决。"])
    legacy = template_version == "zh-guide-v1"
    if legacy:
        lines.extend(["", "## 关键限制（已记录，本版未逐项验证）", ""])
        restrictions = (("行动限制", requirement.mobility_constraints), ("饮食禁忌", requirement.dietary_restrictions))
        if all(value is None for _, value in restrictions):
            lines.append("- 未记录行动限制或饮食禁忌文本；本版未验证适配性。")
        else:
            lines.extend(f"- {label}：{_escape(value)}。" for label, value in restrictions if value is not None)
    supplements = (("酒店偏好", requirement.hotel_preferences),
                   ("交通偏好", requirement.transport_preferences), ("作息", requirement.daily_time_preferences),
                   ("补充备注", requirement.free_text_notes))
    if legacy:
        supplements = (("房型", requirement.room_type_requirements), *supplements)
    if any(value is not None for _, value in supplements) or requirement.must_visit_places or requirement.avoid_places:
        lines.extend(["", "## 用户补充要求（已记录，本版未逐项验证）", ""])
        lines.extend(f"- {label}：{_escape(value)}。" for label, value in supplements if value is not None)
        lines.extend(f"- 用户必去要求：{_escape(value)}。" for value in requirement.must_visit_places)
        lines.extend(f"- 用户避开要求：{_escape(value)}。" for value in requirement.avoid_places)
    lines.extend(["", "## 本版规划范围", "", _SCOPE_NOTICE, "", "## 来源清单", ""])
    for source in sorted(core.sources, key=lambda item: item.source_id):
        lines.append(f"- 〔来源 {source.source_id}〕百度地图｜{_CAPABILITIES[source.capability]}｜"
                     f"获取时间：{_local_time(source.retrieved_at_utc, seconds=True)}（北京时间）｜供应商返回已核验。")
    lines.extend(["", "## 出行前复核", "",
                  "- 来源为查询时快照，不是当前实时保证；地点身份、路线估算、营业文本和备选路网距离的核验范围以正文标签为准。",
                  "- 请出行前确认实际营业、预约要求、实时库存、过敏原、无障碍条件和临时政策；本版没有这些事项的完整可信证据。",
                  "- 天气未获取；各类费用未核验。"])
    return "\n".join(lines)
