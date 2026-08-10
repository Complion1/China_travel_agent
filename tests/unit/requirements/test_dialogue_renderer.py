"""DLG-T05: deterministic dialogue renderer tests."""

from app.requirements.dialogue import render_clarification, render_confirmation_summary
from app.requirements.models import (
    HubType,
    InterestTag,
    RequirementFieldPath,
    RequirementIssue,
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


SUMMARY_LABELS = [
    "目的城市",
    "抵达枢纽",
    "抵达枢纽类型",
    "抵达时间",
    "抵达时间约数",
    "正式游玩开始日期",
    "正式游玩结束日期",
    "正式游玩天数",
    "酒店入住日期",
    "酒店退房日期",
    "住宿晚数",
    "离开枢纽",
    "离开枢纽类型",
    "离开时间",
    "离开时间约数",
    "目的地内总预算",
    "预算约数",
    "成人数量",
    "儿童数量",
    "儿童年龄",
    "老人数量",
    "总人数",
    "房间数量",
    "房型要求",
    "行动限制",
    "饮食禁忌",
    "兴趣",
    "节奏",
    "交通偏好",
    "酒店偏好",
    "必去地点",
    "避开地点",
    "每日作息偏好",
    "补充说明",
    "派生同行场景",
    "尚存假设",
]


def complete_requirement() -> TravelRequirement:
    return TravelRequirement(
        destination="杭州",
        time_window=TravelTimeWindow(
            arrival=TravelTimeWindowArrival(
                hub_name="杭州东站",
                hub_type=HubType.RAILWAY_STATION,
                local_datetime="2025-06-01 09:00",
                is_approximate=True,
            ),
            sightseeing_start_date="2025-06-01",
            sightseeing_end_date="2025-06-03",
            sightseeing_days=3,
            hotel_check_in_date="2025-06-01",
            hotel_check_out_date="2025-06-04",
            hotel_nights=3,
            departure=TravelTimeWindowDeparture(
                hub_name="杭州萧山机场",
                hub_type=HubType.AIRPORT,
                local_datetime="2025-06-04 18:30",
                is_approximate=False,
            ),
        ),
        budget=TravelBudget(total_cny=5000.0, is_approximate=True),
        party=TravelParty(
            adults_count=2,
            children_count=1,
            children_ages=[5],
            seniors_count=1,
            total_people=4,
        ),
        rooms=TravelRooms(room_count=2, room_type_requirements="相邻双床房"),
        constraints=TravelConstraints(
            mobility_constraints="少走楼梯",
            dietary_restrictions="不吃花生",
        ),
        preferences=TravelPreferences(
            interests=[InterestTag.CULTURE, InterestTag.NATURE, InterestTag.FOOD],
            pace=TravelPace.NORMAL,
            transport_preferences="公共交通优先",
            hotel_preferences="地铁站附近",
            must_visit_places=["西湖", "良渚博物院"],
            avoid_places=["拥挤夜市"],
            daily_time_preferences="每天九点后出发",
            free_text_notes="需要午休",
        ),
        inferred_scenarios=["亲子", "带老人"],
    )


def test_clarification_renders_all_sections_in_fixed_order():
    conflict = RequirementIssue(
        code="budget_conflict",
        field_path=RequirementFieldPath.BUDGET_TOTAL_CNY,
        message="预算冲突",
    )
    result = render_clarification(
        ["destination", "party.children_ages"],
        [conflict],
        True,
        True,
    )

    markers = ["**缺失信息：**", "**需要确认：**", "**兴趣偏好", "**行程节奏"]
    assert [result.index(marker) for marker in markers] == sorted(result.index(marker) for marker in markers)
    assert "目的城市" in result
    assert "儿童年龄" in result
    assert "预算冲突" in result
    assert result.count("请将上述信息一次性提供") == 1


def test_clarification_with_no_sections_keeps_single_message_shape():
    result = render_clarification([], [], False, False)
    assert result == "请补充以下信息：\n\n\n\n请将上述信息一次性提供。"


def test_confirmation_summary_has_every_label_once_in_fixed_order():
    result = render_confirmation_summary(complete_requirement())
    body_lines = result.splitlines()[1:-1]
    labels = [line.partition("：")[0] for line in body_lines]

    assert labels == SUMMARY_LABELS
    assert all(result.count(f"{label}：") == 1 for label in SUMMARY_LABELS)


def test_confirmation_summary_uses_chinese_enum_labels_and_all_values():
    result = render_confirmation_summary(complete_requirement())

    expected_fragments = [
        "抵达枢纽类型：火车站/高铁站",
        "离开枢纽类型：机场",
        "兴趣：人文、自然、美食",
        "节奏：标准",
        "目的地内总预算：5000 元",
        "儿童年龄：5",
        "必去地点：西湖、良渚博物院",
        "派生同行场景：亲子、带老人",
        "尚存假设：无",
    ]
    assert all(fragment in result for fragment in expected_fragments)
    assert "InterestTag." not in result
    assert "TravelPace." not in result
    assert "HubType." not in result


def test_confirmation_summary_renders_fixed_empty_values():
    result = render_confirmation_summary(TravelRequirement())

    assert [line.partition("：")[0] for line in result.splitlines()[1:-1]] == SUMMARY_LABELS
    assert "目的城市：未填写" in result
    assert "儿童年龄：无" in result
    assert "必去地点：无" in result
    assert "兴趣：未填写" in result
    assert "节奏：未填写" in result
    assert "尚存假设：无" in result


def test_confirmation_summary_renders_agent_decides_instead_of_missing():
    requirement = TravelRequirement()
    requirement.preferences.agent_decides_interests = True
    requirement.preferences.agent_decides_pace = True

    result = render_confirmation_summary(requirement)

    assert "兴趣：由 Agent 决定" in result
    assert "节奏：由 Agent 决定" in result


def test_confirmation_summary_does_not_expose_internal_fields():
    result = render_confirmation_summary(complete_requirement()).lower()
    forbidden = [
        "evidence",
        "digest",
        "source_turn_id",
        "trace",
        "checkpoint",
        "provider_response",
        "stack trace",
    ]
    assert all(token not in result for token in forbidden)
