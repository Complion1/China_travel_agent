"""DLG-T06: fixed manual-template serializer/parser contract."""

import pytest

from app.requirements.manual_template import (
    MANUAL_TEMPLATE_KEYS,
    ManualTemplateParseError,
    parse_manual_template,
    render_manual_template,
    serialize_manual_template,
)
from app.requirements.models import (
    HubType,
    InterestTag,
    ManualTemplateSubmission,
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


def full_requirement() -> TravelRequirement:
    return TravelRequirement(
        destination="杭州",
        time_window=TravelTimeWindow(
            arrival=TravelTimeWindowArrival(
                hub_name="杭州东站",
                hub_type=HubType.RAILWAY_STATION,
                local_datetime="2025-06-01 09:00",
                is_approximate=True,
            ),
            departure=TravelTimeWindowDeparture(
                hub_name="杭州萧山机场",
                hub_type=HubType.AIRPORT,
                local_datetime="2025-06-03 18:00",
                is_approximate=False,
            ),
            sightseeing_start_date="2025-06-01",
            sightseeing_end_date="2025-06-03",
            sightseeing_days=3,
        ),
        budget=TravelBudget(total_cny=5000.0),
        party=TravelParty(
            adults_count=2,
            children_count=1,
            children_ages=[5],
            seniors_count=0,
        ),
        rooms=TravelRooms(room_count=1, room_type_requirements="标准间"),
        constraints=TravelConstraints(
            mobility_constraints="无",
            dietary_restrictions="不吃花生",
        ),
        preferences=TravelPreferences(
            interests=[InterestTag.NATURE, InterestTag.FOOD],
            pace=TravelPace.NORMAL,
        ),
    )


def materialize(patch) -> TravelRequirement:
    return TravelRequirement(
        destination=patch.destination,
        time_window=patch.time_window,
        budget=patch.budget,
        party=patch.party,
        rooms=patch.rooms,
        constraints=patch.constraints,
        preferences=patch.preferences,
    )


def test_serialize_parse_round_trip_all_template_business_fields():
    requirement = full_requirement()

    submission = serialize_manual_template(requirement)
    parsed = parse_manual_template(submission)

    assert materialize(parsed) == requirement.model_copy(update={"budget": TravelBudget()})
    assert parsed.model_fields_set >= {
        "destination",
        "time_window",
        "budget",
        "party",
        "rooms",
        "constraints",
        "preferences",
    }
    assert parsed.party is not None
    assert "children_ages" in parsed.party.model_fields_set


def test_serializer_uses_exact_version_key_order_and_full_width_punctuation():
    submission = serialize_manual_template(full_requirement())
    lines = submission.content.splitlines()

    assert submission.template_version == "1"
    assert lines[0] == "模板版本：1"
    assert [line.partition("：")[0] for line in lines[1:]] == MANUAL_TEMPLATE_KEYS
    assert len(lines) == len(MANUAL_TEMPLATE_KEYS) + 1
    assert all("：" in line for line in lines)
    assert "杭州东站，2025-06-01 09:00，大约" in submission.content
    assert "兴趣：自然、美食" in submission.content
    assert "节奏：标准" in submission.content


def test_empty_template_has_no_internal_blank_line_and_fixed_order():
    lines = render_manual_template().splitlines()
    assert lines[0] == "模板版本：1"
    assert [line.partition("：")[0] for line in lines[1:]] == MANUAL_TEMPLATE_KEYS
    assert "" not in lines


def test_parser_accepts_half_width_colons():
    submission = serialize_manual_template(full_requirement())
    half_width = ManualTemplateSubmission(
        template_version="1",
        content=submission.content.replace("：", ":"),
    )
    assert materialize(parse_manual_template(half_width)) == full_requirement().model_copy(update={"budget": TravelBudget()})


def test_parser_requires_exact_key_text_without_surrounding_spaces():
    submission = serialize_manual_template(full_requirement())
    content = submission.content.replace("模板版本：1", "模板版本 ：1")
    with pytest.raises(ManualTemplateParseError) as exc_info:
        parse_manual_template(ManualTemplateSubmission(template_version="1", content=content))
    assert any("未知键" in message for message in exc_info.value.field_errors.values())


def test_parser_preserves_explicit_no_children_presence():
    requirement = full_requirement()
    requirement.party.children_count = 0
    requirement.party.children_ages = []

    patch = parse_manual_template(serialize_manual_template(requirement))

    assert patch.party is not None
    assert patch.party.children_count == 0
    assert patch.party.children_ages == []
    assert "children_ages" in patch.party.model_fields_set


def test_parser_supports_agent_decides_flags():
    requirement = full_requirement()
    requirement.preferences.interests = None
    requirement.preferences.pace = None
    requirement.preferences.agent_decides_interests = True
    requirement.preferences.agent_decides_pace = True

    patch = parse_manual_template(serialize_manual_template(requirement))

    assert patch.preferences is not None
    assert patch.preferences.agent_decides_interests is True
    assert patch.preferences.agent_decides_pace is True
    assert patch.preferences.interests is None
    assert patch.preferences.pace is None


def test_parser_accumulates_version_duplicate_missing_order_unknown_and_trailing_errors():
    lines = serialize_manual_template(full_requirement()).content.splitlines()
    broken_lines = [
        "模板版本：3",
        lines[2],
        lines[1],
        lines[1],
        *lines[3:-1],
        "未知字段：值",
        "额外尾随文本",
    ]
    submission = ManualTemplateSubmission(
        template_version="3",
        content="\n".join(broken_lines),
    )

    with pytest.raises(ManualTemplateParseError) as exc_info:
        parse_manual_template(submission)

    errors = exc_info.value.field_errors
    assert "模板版本" in errors
    assert "目的城市" in errors and "重复键" in errors["目的城市"]
    assert "节奏" in errors and "缺少键" in errors["节奏"]
    assert "_order" in errors
    assert any("未知键" in message for message in errors.values())
    assert any("额外尾随文本" in message for message in errors.values())


def test_parser_accumulates_independent_value_errors():
    content = serialize_manual_template(full_requirement()).content
    replacements = {
        "正式游玩日期：2025-06-01 至 2025-06-03": "正式游玩日期：2025-02-30 至 2025-06-03",
        "正式游玩天数：3": "正式游玩天数：0",
        "成人/儿童/老人：2/1/0": "成人/儿童/老人：2/一/0",
        "儿童年龄：5": "儿童年龄：五",
        "房间及房型：1间，标准间": "房间及房型：0间，",
        "兴趣：自然、美食": "兴趣：自然、未知",
        "节奏：标准": "节奏：飞快",
    }
    for original, replacement in replacements.items():
        content = content.replace(original, replacement)

    with pytest.raises(ManualTemplateParseError) as exc_info:
        parse_manual_template(ManualTemplateSubmission(template_version="1", content=content))

    assert {
        "正式游玩日期",
        "正式游玩天数",
        "成人/儿童/老人",
        "儿童年龄",
        "房间及房型",
        "兴趣",
        "节奏",
    } <= exc_info.value.field_errors.keys()


def test_serializer_rejects_conflicting_interest_and_agent_decides_values():
    requirement = full_requirement()
    requirement.preferences.agent_decides_interests = True
    with pytest.raises(ValueError, match="不能同时设置"):
        serialize_manual_template(requirement)


def test_serializer_emits_empty_values_for_an_incomplete_requirement():
    submission = serialize_manual_template(TravelRequirement())
    assert "总预算" not in submission.content
    assert "到达机场/车站、日期、时间：\n" in f"{submission.content}\n"
    assert "兴趣：\n" in f"{submission.content}\n"
    assert "节奏：" == submission.content.splitlines()[-1]


def test_serializer_rejects_conflicting_pace_and_agent_decides_values():
    requirement = full_requirement()
    requirement.preferences.agent_decides_pace = True
    with pytest.raises(ValueError, match="不能同时设置"):
        serialize_manual_template(requirement)


def test_parser_reports_all_empty_required_values():
    with pytest.raises(ManualTemplateParseError) as exc_info:
        parse_manual_template(
            ManualTemplateSubmission(template_version="1", content=render_manual_template())
        )
    assert set(MANUAL_TEMPLATE_KEYS) - {"儿童年龄"} <= exc_info.value.field_errors.keys()


@pytest.mark.parametrize(
    ("original", "replacement", "error_key"),
    [
        (
            "正式游玩日期：2025-06-01 至 2025-06-03",
            "正式游玩日期：2025/06/01 到 2025/06/03",
            "正式游玩日期",
        ),
        (
            "到达机场/车站、日期、时间：杭州东站，2025-06-01 09:00，大约",
            "到达机场/车站、日期、时间：杭州东站 2025-06-01 09:00",
            "到达机场/车站、日期、时间",
        ),
        (
            "离开机场/车站、日期、时间：杭州萧山机场，2025-06-03 18:00",
            "离开机场/车站、日期、时间：杭州萧山机场，2025-02-30 18:00",
            "离开机场/车站、日期、时间",
        ),
    ],
)
def test_parser_rejects_exact_date_and_hub_format_errors(original, replacement, error_key):
    content = serialize_manual_template(full_requirement()).content.replace(original, replacement)
    with pytest.raises(ManualTemplateParseError) as exc_info:
        parse_manual_template(ManualTemplateSubmission(template_version="1", content=content))
    assert error_key in exc_info.value.field_errors


@pytest.mark.parametrize(
    ("party", "ages", "message"),
    [
        ("2/0/0", "5", "儿童人数为 0 时必须为空"),
        ("2/2/0", "5", "年龄数量1与儿童人数2不一致"),
    ],
)
def test_parser_rejects_children_count_age_semantic_errors(party, ages, message):
    content = serialize_manual_template(full_requirement()).content
    content = content.replace("成人/儿童/老人：2/1/0", f"成人/儿童/老人：{party}")
    content = content.replace("儿童年龄：5", f"儿童年龄：{ages}")
    with pytest.raises(ManualTemplateParseError) as exc_info:
        parse_manual_template(ManualTemplateSubmission(template_version="1", content=content))
    assert message in exc_info.value.field_errors["儿童年龄"]


def test_parser_ignores_legacy_budget_but_rejects_duplicate_interest_labels():
    content = serialize_manual_template(full_requirement()).content
    content += "\n总预算：0"
    content = content.replace("兴趣：自然、美食", "兴趣：自然、自然")
    with pytest.raises(ManualTemplateParseError) as exc_info:
        parse_manual_template(ManualTemplateSubmission(template_version="1", content=content))
    assert "总预算" not in exc_info.value.field_errors
    assert "兴趣" in exc_info.value.field_errors


@pytest.mark.parametrize("amount", ["", "5000", "零元", "任意延期文本"])
def test_legacy_optional_budget_line_never_produces_an_amount(amount):
    content = serialize_manual_template(full_requirement()).content + "\n总预算：" + amount
    parsed = parse_manual_template(ManualTemplateSubmission(template_version="1", content=content))
    assert parsed.budget.total_cny is None
    assert not parsed.budget.is_approximate
