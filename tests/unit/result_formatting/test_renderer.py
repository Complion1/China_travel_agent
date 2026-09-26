"""手写文案预期与安全场景，逐项对应 D §8。"""

from datetime import timedelta

import pytest

from app.result_formatting.artifacts import ResultInterval
from app.result_formatting.projection import ProjectionFailure, project_result
from app.result_formatting.renderer import render_day, render_footer, render_header
from tests.unit.result_formatting.fakes import make_scenario


def project(scenario):
    return project_result(scenario.request, scenario.itinerary, scenario.evidence)


@pytest.mark.asyncio
async def test_transit_day_exact_golden_contains_no_invented_activity():
    core, days = project(await make_scenario(transit_only=True))
    assert render_day(core, days[0]) == (
        "## 第 1 天｜2026-09-03｜抵离交通\n\n"
        "- 日类型：纯交通日。\n"
        "- 天气未获取。\n"
        "- 本日未安排游览。"
    )


@pytest.mark.asyncio
async def test_header_and_footer_cover_confirmed_fields_and_fixed_scope(scenario):
    core, _ = project(scenario)
    header, footer = render_header(core), render_footer(core)
    assert header.startswith(
        "# 杭州旅行攻略\n\n"
        "- 游览日期：2026-09-03—2026-09-03；共 1 天。\n"
        "- 同行人员：共 3 人；成人 2 人、儿童 1 人、老人 0 人；儿童年龄：8 岁。\n"
        "- 住宿日期：2026-09-02 入住，2026-09-04 离店；2 晚，1 间房。\n"
        "- 用户总预算：约 10000.5 元；范围为目的地内住宿、当地交通、餐饮、景点和其他当地消费，不含往返目的地的大交通。\n"
        "- 行程节奏：标准。"
    )
    assert "名称由用户确认，位置由百度地理编码〔来源 " in header
    assert "行李寄存需自行向酒店确认" in header
    assert "已确认返程：约 2026-09-04 18:00（北京时间）" in footer
    assert "返程处理预留：120 分钟；近似时刻额外预留：30 分钟" in footer
    assert "均为规划预留，并非机场/车站官方政策" in footer
    assert "\n".join(f"- {category}：未核验。" for category in ("住宿", "当地交通", "餐饮", "景点", "其他当地消费")) in footer
    assert "- 行动限制：无。\n- 饮食禁忌：不吃辣。" in footer
    assert (
        "## 用户补充要求（已记录，本版未逐项验证）\n\n"
        "- 房型：家庭房。\n- 酒店偏好：安静。\n- 交通偏好：少换乘。\n"
        "- 作息：晚一点出发。\n- 补充备注：带儿童同行。"
    ) in footer
    assert "本次完成的是首版支持范围内的行程规划；文本中的作息、活动时间或时长、交通限制等额外要求未纳入本次规划验证。" in footer
    assert "获取时间：2026-08-20 10:03:04（北京时间）｜供应商返回已核验" in footer
    assert "2026-08-21" not in footer
    assert "来源为查询时快照，不是当前实时保证" in footer
    assert "预约要求、实时库存、过敏原、无障碍条件和临时政策" in footer
    assert "## 地图" not in header + footer
    assert "预算合规" not in header + footer and "剩余预算" not in header + footer


@pytest.mark.asyncio
async def test_combined_meal_and_backups_keep_their_roles_and_directed_distances(scenario):
    core, days = project(scenario)
    text = render_day(core, days[0])
    main, backup = text.split("### 附近备选")
    assert main.count("｜景点兼午餐：") == 1
    assert main.count("｜午餐：") == 0
    assert main.count("｜晚餐：") == 1
    assert "1201 米；10 分 1 秒（百度获取时路线估算）〔来源 " in main
    assert "日程预留 30 分钟；费用未核验" in main
    assert "活动时长 120 分钟（规划估算）" in main
    assert "规划时营业证据相容，实时营业仍请确认〔来源 " in main
    assert "营业文本：09\\:00\\-21\\:00〔来源 " in main
    assert "营业时间未核验，请出行前确认" in main
    assert "未纳入主路线；距离为对应模式的路网距离" in backup
    assert "从主访问到备选 402 米（步行路网距离〔来源 " in backup
    assert "从主访问到备选 403 米（步行路网距离〔来源 " in backup
    assert "从主访问到备选 504 米（步行路网距离〔来源 " in backup
    assert "600 米" not in backup
    assert all(f"- {role}备选：" in backup for role in ("景点", "午餐", "晚餐"))


@pytest.mark.asyncio
async def test_multiple_day_fragments_are_stable_and_sources_have_continuous_ids():
    core, days = project(await make_scenario(days=3))
    first = (render_header(core), *(render_day(core, day) for day in days), render_footer(core))
    second = (render_header(core), *(render_day(core, day) for day in days), render_footer(core))
    assert first == second
    assert len(first) == 5
    for number, day in enumerate(days, 1):
        assert f"## 第 {number} 天｜2026-09-0{number + 2}｜" in first[number]
    for fragment in first:
        assert fragment and "\r" not in fragment
        assert not fragment.startswith("\n") and not fragment.endswith("\n")
    footer = first[-1]
    positions = [footer.index(f"- 〔来源 {source.source_id}〕百度地图") for source in core.sources]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_zero_time_route_order_uses_path_and_cross_midnight_has_dates(scenario):
    core, days = project(scenario)
    day = days[0]
    visit = day.visits[0]
    # A zero-time entering leg at the visit boundary must precede the visit;
    # the leaving leg follows it, preserving upstream path ordering.
    zero_interval = ResultInterval(start_at_utc=visit.interval.start_at_utc,
                                   end_at_utc=visit.interval.start_at_utc, timezone="Asia/Shanghai")
    entering = day.route_legs[0].model_copy(update={"interval": zero_interval, "duration_s": 0,
                                                 "scheduled_minutes": 0, "distance_m": 0})
    edited_day = day.model_copy(update={"route_legs": (entering, *day.route_legs[1:])})
    text = render_day(core, edited_day)
    entering_position = text.index("｜交通：")
    visit_position = text.index("｜景点兼午餐：")
    leaving_position = text.index("｜交通：", entering_position + 1)
    assert entering_position < visit_position < leaving_position
    assert "0 米；0 分钟（百度获取时路线估算）" in text
    assert "日程预留 0 分钟" in text

    crossing_core, crossing_days = project(await make_scenario(cross_midnight=True))
    crossing = render_day(crossing_core, crossing_days[0])
    assert "2026-09-04 00:00—2026-09-04 00:30（北京时间）" in crossing
    # Also exercise an interval spanning the date boundary without changing it.
    late_route = crossing_days[0].route_legs[-1]
    spanning = ResultInterval(start_at_utc=late_route.interval.start_at_utc - timedelta(minutes=15),
                              end_at_utc=late_route.interval.end_at_utc - timedelta(minutes=15), timezone="Asia/Shanghai")
    spanning_day = crossing_days[0].model_copy(update={"route_legs": (*crossing_days[0].route_legs[:-1], late_route.model_copy(update={"interval": spanning}))})
    assert "2026-09-03 23:45—2026-09-04 00:15（北京时间）" in render_day(crossing_core, spanning_day)


@pytest.mark.asyncio
async def test_supplement_text_is_escaped_ordered_and_never_interpreted(scenario):
    core, _ = project(scenario)
    requirement = core.requirement.model_copy(update={
        "room_type_requirements": None,
        "hotel_preferences": "<img src=x>&**安静**",
        "transport_preferences": "[去这里](https://example.com)\\路线",
        "daily_time_preferences": "# 凌晨出发\r\n- 绝不能迟到\t!",
        "free_text_notes": "`代码`_{花}(括)|+.",
        "must_visit_places": ("乙地", "甲地", "乙地"),
        "avoid_places": ("丙地", "丁地"),
    })
    text = render_footer(core.model_copy(update={"requirement": requirement}))
    assert "- 房型：" not in text
    assert "&lt;img src=x&gt;&amp;\\*\\*安静\\*\\*" in text
    assert "\\[去这里\\]\\(https\\:\\/\\/example\\.com\\)\\\\路线" in text
    assert "\\# 凌晨出发↵\\- 绝不能迟到⇥\\!" in text
    assert "\\`代码\\`\\_\\{花\\}\\(括\\)\\|\\+\\." in text
    assert "\n- 用户必去要求：乙地。\n- 用户必去要求：甲地。\n- 用户必去要求：乙地。" in text
    assert "\n- 用户避开要求：丙地。\n- 用户避开要求：丁地。" in text
    assert "\n# 凌晨" not in text and "<img" not in text and "](https" not in text
    assert "已记录，本版未逐项验证" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["\x00", "\x1b", "\x7f", "\x85", "\u202e", "\u2066", "\ud800"])
async def test_unsafe_text_has_a_fixed_failure_code_without_payload(scenario, unsafe):
    core, days = project(scenario)
    day = days[0].model_copy(update={"theme": "合成敏感正文" + unsafe})
    with pytest.raises(ProjectionFailure) as caught:
        render_day(core, day)
    assert caught.value.code == "unsafe_text"
    assert "合成敏感正文" not in str(caught.value)


@pytest.mark.asyncio
async def test_names_and_business_text_use_the_same_escaping(scenario):
    core, days = project(scenario)
    places = tuple(place.model_copy(update={"name": "**合成地点**", "address": "<合成地址>",
                                           "business_hours": ("# 任意营业文本\n[链接](x)",) if place.business_hours else ()})
                   for place in core.places)
    edited = core.model_copy(update={"places": places})
    text = render_header(edited) + render_day(edited, days[0])
    assert "\\*\\*合成地点\\*\\*" in text and "&lt;合成地址&gt;" in text
    assert "营业文本：\\# 任意营业文本↵\\[链接\\]\\(x\\)" in text
    assert "\n# 任意营业文本" not in text


@pytest.mark.asyncio
async def test_budget_plain_decimal_and_transport_semantics_remain_honest(scenario):
    core, days = project(scenario)
    requirement = core.requirement.model_copy(update={"budget_total_cny": 1e20, "budget_is_approximate": False})
    header = render_header(core.model_copy(update={"requirement": requirement}))
    assert "用户总预算：100000000000000000000 元" in header and "1e+20" not in header
    route = days[0].route_legs[0].model_copy(update={"transport_semantic": "ride_hailing", "route_mode": "driving"})
    day = days[0].model_copy(update={"route_legs": (route, *days[0].route_legs[1:])})
    assert "网约车（驾车路线估算，叫车可用性及费用未核验）" in render_day(core, day)
