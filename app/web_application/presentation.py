"""Small safe projections from verified public DTOs; no workflow decisions."""
from app.demo_workflow import DemoControlArtifact
from app.hotel_selection import HotelDiscoveryArtifact, HotelCandidatesReadyRecord
from enum import Enum
from app.requirements import NeedClarification, NeedConfirmation, RequirementInvalidated, RequirementFieldPath, FieldStatus
from app.workflow_contracts import TravelSessionState, WorkflowPhase
from .errors import WebApplicationDependencyError
from .models import WebInteractionCard, WebHotelChoice, WebFact, WebClarificationOption, WebClarificationQuestion, fresh


# UI descriptions of M01's public required fields and reported issues.
_REQUIRED_QUESTIONS = (
    ("destination", "目的城市", "text", None, None),
    ("time_window.sightseeing_start_date", "正式游玩开始日期", "date", None, None),
    ("time_window.sightseeing_end_date", "正式游玩结束日期", "date", None, None),
    ("time_window.sightseeing_days", "正式游玩天数", "number", 1, 60),
    ("time_window.arrival.hub_name", "到达机场/车站", "text", None, None),
    ("time_window.arrival.hub_type", "到达枢纽类型", "single", None, None),
    ("time_window.arrival.local_datetime", "到达日期与时间", "datetime", None, None),
    ("time_window.departure.hub_name", "离开机场/车站", "text", None, None),
    ("time_window.departure.hub_type", "离开枢纽类型", "single", None, None),
    ("time_window.departure.local_datetime", "离开日期与时间", "datetime", None, None),
    ("party.adults_count", "成人数量", "number", 1, 100),
    ("party.children_count", "儿童数量", "number", 0, 100),
    ("party.seniors_count", "老人数量", "number", 0, 100),
    ("rooms.room_count", "房间数量", "number", 1, 100),
    ("rooms.room_type_requirements", "房型要求", "single", None, None),
    ("constraints.mobility_constraints", "行动限制", "single", None, None),
    ("constraints.dietary_restrictions", "饮食禁忌", "single", None, None),
)
_STYLE_QUESTIONS = (
    ("preferences.interests", "兴趣偏好", "multiple", None, None),
    ("preferences.pace", "行程节奏", "single", None, None),
)
_OPTIONS = {
    "time_window.arrival.hub_type": (("airport", "机场"), ("railway_station", "火车站或高铁站")),
    "time_window.departure.hub_type": (("airport", "机场"), ("railway_station", "火车站或高铁站")),
    "preferences.interests": (("culture", "人文"), ("nature", "自然"), ("food", "美食"), ("checkin", "打卡"), ("agent_decides", "由 Agent 决定")),
    "preferences.pace": (("relaxed", "轻松"), ("normal", "标准"), ("intense", "紧凑"), ("agent_decides", "由 Agent 决定")),
    "rooms.room_type_requirements": (("double", "大床房"), ("twin", "双床房"), ("family", "家庭房"), ("no_preference", "无特殊要求"), ("other", "其他")),
    "constraints.mobility_constraints": (("none", "无"), ("other", "有，需要说明")),
    "constraints.dietary_restrictions": (("none", "无"), ("other", "有，需要说明")),
}


def _field_value(requirement, path):
    value = requirement
    for component in path.split("."):
        value = getattr(value, component)
    return value


def _clarification_details(outcome):
    requirement = outcome.requirement
    issue_fields = {issue.field_path.value for issue in outcome.issues}
    issue_fields.update(path.value for path, metadata in outcome.metadata.metadata.items()
        if metadata.issues or metadata.status in {FieldStatus.CONFLICT, FieldStatus.INVALID})
    if "preferences.agent_decides_interests" in issue_fields:
        issue_fields.add("preferences.interests")
    if "preferences.agent_decides_pace" in issue_fields:
        issue_fields.add("preferences.pace")
    questions, recorded = [], []
    for field, label, kind, minimum, maximum in (*_REQUIRED_QUESTIONS, *_STYLE_QUESTIONS):
        if requirement.destination and field in {"time_window.arrival.hub_name", "time_window.departure.hub_name"}:
            role = "到达" if field == "time_window.arrival.hub_name" else "离开"
            label = f"{role}{requirement.destination}的机场/车站"
        value = _field_value(requirement, field)
        missing = value is None or isinstance(value, str) and not value.strip()
        if field == "preferences.interests" and value == []:
            missing = True
        if field == "preferences.interests" and requirement.preferences.agent_decides_interests:
            missing = False
        if field == "preferences.pace" and requirement.preferences.agent_decides_pace:
            missing = False
        if not missing and field not in issue_fields:
            continue
        questions.append(WebClarificationQuestion(schema_version="1.0", field=RequirementFieldPath(field),
            label=label, kind=kind, minimum=minimum, maximum=maximum,
            options=tuple(WebClarificationOption(schema_version="1.0", value=code, label=text) for code, text in _OPTIONS.get(field, ()))))
        if field in issue_fields and (
                field == "preferences.interests" and not value and requirement.preferences.agent_decides_interests or
                field == "preferences.pace" and value is None and requirement.preferences.agent_decides_pace):
            value = "由 Agent 决定"
        if field in issue_fields and value is not None:
            text = "、".join(item.value if isinstance(item, Enum) else str(item) for item in value) if isinstance(value, list) else str(value.value if isinstance(value, Enum) else value)
            if text:
                recorded.append(WebFact(schema_version="1.0", label=f"目前记录：{label}", value=text))
    count = requirement.party.children_count
    if count is not None and count > 0 and (not requirement.party.children_ages or "party.children_ages" in issue_fields):
        questions.append(WebClarificationQuestion(schema_version="1.0", field=RequirementFieldPath.CHILDREN_AGES,
            label="儿童年龄", kind="ages", minimum=0, maximum=17, item_count=count))
    if "party.children_ages" in issue_fields and requirement.party.children_ages:
        recorded.append(WebFact(schema_version="1.0", label="目前记录：儿童年龄",
            value="、".join(str(age) for age in requirement.party.children_ages)))
    return tuple(questions), tuple(recorded)


def _paragraphs(value: str) -> tuple[str, ...]:
    if type(value) is not str or not value:
        raise WebApplicationDependencyError()
    return tuple(value[index:index + 8000] for index in range(0, len(value), 8000))


async def hotel_candidates(*, state, repository):
    state = fresh(state, TravelSessionState)
    controls = [ref for ref in state.artifact_refs if ref.artifact_type == "demo.control"]
    if not controls:
        raise WebApplicationDependencyError()
    latest = max(controls, key=lambda ref: ref.operation_id.logical_step)
    control = (await repository.get(latest, expected_model=DemoControlArtifact)).content
    control = fresh(control, DemoControlArtifact)
    ref = control.hotel_candidate_ref
    if ref is None or ref.operation_id.run_id != state.run_id:
        raise WebApplicationDependencyError()
    candidates = fresh((await repository.get(ref, expected_model=HotelDiscoveryArtifact)).content, HotelDiscoveryArtifact)
    if type(candidates.result) is not HotelCandidatesReadyRecord:
        raise WebApplicationDependencyError()
    return ref, candidates.result.candidates


async def interaction_card(*, state, repository) -> WebInteractionCard:
    state = fresh(state, TravelSessionState)
    outcome = state.requirement_outcome
    if state.phase is WorkflowPhase.REQUIREMENTS:
        if type(outcome) is NeedClarification:
            questions, recorded = _clarification_details(outcome)
            return WebInteractionCard(schema_version="1.0", kind="clarification", title="补充旅行需求",
                paragraphs=_paragraphs(outcome.message), facts=recorded, choices=(), questions=questions)
        if type(outcome) in {NeedConfirmation, RequirementInvalidated}:
            return WebInteractionCard(schema_version="1.0", kind="confirmation", title="确认旅行需求",
                paragraphs=_paragraphs(outcome.summary), facts=(), choices=())
    if state.phase is WorkflowPhase.HOTEL_SELECTION:
        _, candidates = await hotel_candidates(state=state, repository=repository)
        return WebInteractionCard(schema_version="1.0", kind="hotel", title="选择酒店或全部放弃",
            paragraphs=("以下候选来自已验证的地图事实，价格和空房情况需另行核实。",), facts=(),
            choices=tuple(WebHotelChoice(schema_version="1.0", ordinal=index + 1, name=item.name,
                address=item.address, facts=(WebFact(schema_version="1.0", label="类别", value=item.category),))
                for index, item in enumerate(candidates)))
    raise WebApplicationDependencyError()
