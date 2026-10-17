"""Explicit projections; provider responses and model reasoning never enter this module."""
from __future__ import annotations

from datetime import date
from enum import Enum
import json
import re

from app.workflow_contracts import ArtifactWriteRequest, ArtifactRef, ArtifactSchemaRegistration, TravelSessionState
from app.requirements import ConfirmedTravelRequirement
from .models import PublicFact, PublicTrip, PublicRecordBody, RECORD_CATALOG


_REDACTIONS = (
    re.compile(r"(?i)\b(?:sk-|ak-|bearer\s+)[a-z0-9_\-]{8,}"),
    re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\[^\\\s]+\\)[^\s,，;；]+"),
    re.compile(r"(?<![\w/])/(?!/)[^\s,，;；。！？、（）()【】\[\]<>\"']+"),
    re.compile(r"(?i)(?:api[_ -]?key|token|secret|password)\s*[:=]\s*[^\s,，;；]+"),
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_REJECTION_REASONS = {
    "arrival_processing_overlap": "到达枢纽的处理时间与接驳行程重叠",
    "departure_processing_overlap": "离开枢纽的预留时间与接驳行程重叠",
    "boundary_transfers_overlap": "到达和离开接驳的时间发生重叠",
    "day_route_boundary_overlap": "每日行程与抵离接驳的时间发生重叠",
    "route_end_time_mismatch": "路线结束时间与已查询的交通时长不一致",
    "route_evidence_mismatch": "路线与已有地图证据不一致",
    "route_leg_binding_mismatch": "路线起终点与已有查询不一致",
    "default_daily_window": "活动超出当前支持的每日时间范围",
    "business_hours_unverified": "营业时间尚未取得可用核验依据",
    "candidate_not_eligible": "选用地点尚未通过候选准入校验",
    "candidate_role_not_allowed": "选用地点不支持当前活动用途",
    "must_visit_unscheduled": "必去地点尚未完整安排",
    "backup_options_invalid": "备选地点未通过可用性校验",
    "empty_day_without_transfer": "该日既无活动也无抵离接驳",
}
_REJECTION_FIELDS = {
    "draft": "行程草案", "effective_policy": "规划规则", "day_kind": "当日类型",
    "draft.arrival_transfer": "到达接驳", "draft.departure_transfer": "离开接驳",
    "draft.arrival_transfer.route_leg.start_at": "到达接驳开始时间",
    "draft.departure_transfer.route_leg.start_at": "离开接驳开始时间",
    "draft.days.route_legs": "每日交通", "draft.days.route_legs.end_at": "每日交通结束时间",
    "draft.days.visits": "每日活动", "draft.days.visits.interval": "每日活动时段",
    "draft.days.backups": "每日备选地点", "visits.candidate_handle": "活动地点",
}
_STATES = {"success":"查询成功", "search_success":"查询成功", "details_success":"详情已核验",
    "no_result":"没有结果", "search_no_result":"没有结果", "details_no_result":"没有结果",
    "failure":"查询失败", "failed":"处理失败", "map_failure":"查询失败", "not_issued":"未执行",
    "unverified_result":"结果未核验", "ambiguous":"枢纽存在多个匹配", "ready":"候选已准备",
    "insufficient":"候选不足", "selected":"用户已选择", "declined":"用户未选择",
    "accepted":"动作已接受", "policy_rejected":"动作未通过规则", "controller_rejected":"动作未通过校验",
    "llm_failure":"模型调用失败", "in_progress":"处理中", "completed":"阶段已完成",
    "incomplete":"仍有未解决项", "awaiting_candidate_supplement":"等待定向补研", "terminal":"动作已结束",
    "preparation":"准备规划", "transport":"核验交通", "submit_repair":"提交或修复",
    "format_repair":"修正输出格式", "supplement_request":"请求补研", "tool_calls":"工具调用计划",
    "final_text":"模型结束本轮", "search_places":"拟查询地点", "get_place_details":"拟查地点详情",
    "city":"城市范围", "nearby":"周边范围", "eligible":"可用于候选", "excluded":"已排除",
    "conflicted":"存在冲突", "withheld":"暂缓准入", "pending":"待判断", "arrival":"到达枢纽", "departure":"离开枢纽",
    "driving":"驾车", "walking":"步行", "transit":"公共交通", "riding":"骑行",
    "public_transit":"公共交通", "cycling":"骑行", "ride_hailing":"网约车", "private_car":"私家车"}


def clean(value, *, secrets: tuple[str, ...], limit=512) -> str:
    if isinstance(value, Enum):
        value = value.value
    text = str(value)
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, "[已隐藏]")
    text = _CONTROL.sub(" ", text)
    for pattern in _REDACTIONS:
        text = pattern.sub("[已隐藏]", text)
    return text.strip()[:limit] or "未提供"


def _word(value):
    value = value.value if isinstance(value, Enum) else value
    return _STATES.get(value, "已保存，具体状态未展示")


def _rejection(reason: str, field: str | None):
    yield "校验反馈", _REJECTION_REASONS.get(reason, "校验未接受，请等待后续处理")
    label = _REJECTION_FIELDS.get(re.sub(r"\[[0-9]+\]", "", field or ""))
    if label is not None:
        yield "涉及字段", label


def _body(kind, facts, *, secrets, status="observed", trip=None):
    phase, title = RECORD_CATALOG[kind]
    entries = list(facts)
    if len(entries) > 24:
        entries = entries[:23] + [("展示范围", "仅展示前若干项，全部事实以最终攻略为准")]
    return PublicRecordBody(kind=kind, phase=phase, title=title, status=status, trip=trip,
        facts=tuple(PublicFact(label=label, value=clean(value, secrets=secrets)) for label, value in entries))


def _places(places):
    for place in places:
        yield "地点", place.name
        yield "地址", place.address
        coordinate = getattr(place, "coordinate", None)
        if coordinate is not None:
            yield "百度坐标", f"{coordinate.latitude}, {coordinate.longitude}"


def _provenance(value, *, planning=False):
    if value is not None:
        yield "来源", "百度地图"
        yield "获取时间（UTC）", getattr(value, "retrieved_at_utc" if planning else "retrieved_at").isoformat()
        yield "地图核验", "已核验" if value.verified else "未核验"


def project_artifact(*, request: ArtifactWriteRequest, ref: ArtifactRef,
        registrations: tuple[ArtifactSchemaRegistration, ...], secrets: tuple[str, ...]) -> PublicRecordBody | None:
    catalog = {
        "hotel.place_search":"hotel_search", "hotel.candidate_set":"hotel_candidates", "hotel.selection":"hotel_selection",
        "candidate_research.model_action":"research_action", "candidate_research.place_observation":"place_observation",
        "candidate_research.model_judgment":"research_judgment", "candidate_research.candidate_set":"research_state",
        "itinerary_planning.model_action":"planning_action", "itinerary_planning.hub_geocode_observation":"hub_observation",
        "itinerary_planning.route_matrix_observation":"route_matrix", "itinerary_planning.directions_observation":"directions",
        "itinerary_planning.itinerary":"planning_state", "result_formatting.day":"result_day", "result_formatting.index":"result_index"}
    kind = catalog.get(request.artifact_type)
    if kind is None:
        return None
    if (ref.operation_id != request.operation_id or ref.artifact_type != request.artifact_type
            or ref.schema_version != request.schema_version):
        raise ValueError("artifact identity mismatch")
    registration = next(item for item in registrations if
        (item.artifact_type, item.schema_version) == (request.artifact_type, request.schema_version))
    value = registration.model.model_validate_json(json.dumps(request.content, ensure_ascii=False), strict=True)
    if hasattr(value, "run_id") and value.run_id != request.operation_id.run_id:
        raise ValueError("artifact run mismatch")
    facts, status = [], "observed"
    if kind == "hotel_search":
        facts = [("查询结果", _word(value.evidence.outcome_type)), ("来源", "百度地图"),
            ("获取时间（UTC）", value.retrieved_at.isoformat()), ("地图核验", "已核验" if value.verified else "未核验")]
        places = getattr(value.evidence, "places", ())
        facts += [("返回地点数", len(places)), *_places(places)]
        status = "failed" if value.evidence.outcome_type == "failed" else status
    elif kind == "hotel_candidates":
        facts = [("候选状态", _word(value.result.result_type)), ("酒店数量", len(value.result.candidates)),
            ("核验范围", "仅核验地点身份，不代表有房或可订价格"), *_places(value.result.candidates)]
    elif kind == "hotel_selection":
        status = "accepted" if value.decision.decision_type == "selected" else "rejected"
        facts = [("选择状态", _word(value.decision.decision_type))]
        if status == "accepted":
            facts += [("酒店", value.decision.selected_candidate.name), ("地址", value.decision.selected_candidate.address)]
    elif kind in {"research_action", "research_judgment"}:
        result = value.result.final if value.result.record_type == "recovered" else value.result
        status = ("failed" if result.record_type == "llm_failure" else "rejected" if
            result.record_type in {"policy_rejected", "controller_rejected"} else "proposed" if kind == "research_action" else "accepted")
        facts = [("处置", _word(result.record_type))]
        if kind == "research_action" and result.record_type == "accepted":
            for action in getattr(result.output, "actions", ()) or ():
                facts.append(("拟执行", _word(action.action_type)))
                if action.action_type == "search_places":
                    facts += [("拟查询词", action.query), ("查询范围", _word(action.area_mode))]
        elif kind == "research_judgment":
            facts.append(("判断性质", "模型对候选用途的判断，不是地图事实"))
            if result.record_type == "accepted":
                for field, label in (("role_updates","用途判断数量"), ("interest_updates","兴趣判断数量"),
                        ("meal_updates","餐次判断数量"), ("requirement_group_updates","约束判断数量")):
                    facts.append((label, len(getattr(result.patch, field))))
    elif kind == "place_observation":
        result = value.result
        facts = [("查询结果", _word(result.record_type))]
        places = result.places if result.record_type == "search_success" else (result.details,) if result.record_type == "details_success" else ()
        facts += [("返回地点数", len(places)), *_provenance(getattr(result, "provenance", None)), *_places(places)]
        status = "failed" if result.record_type == "map_failure" else status
    elif kind == "research_state":
        result = value.record
        facts = [("研究状态", _word(result.record_type))]
        state = getattr(result, "state", None)
        if state is not None:
            counts = state.counts
            facts += [("景点候选 / 目标", f"{counts.attraction_count} / {counts.attraction_target}"),
                ("餐厅候选 / 目标", f"{counts.restaurant_count} / {counts.restaurant_target}"),
                ("可用候选", counts.unique_eligible_count), ("待解决缺口", len(state.pending_gaps))]
            for candidate in state.candidates:
                facts.append(("候选用途状态", f"{candidate.core.name} · {_word(candidate.state)}"))
        status = "failed" if result.record_type == "failed" else status
    elif kind == "planning_action":
        facts = [("当前阶段", _word(value.request_stage)), ("调用类别", _word(value.budget_category)),
            ("动作性质", _word(value.action_kind)), ("处置", _word(value.disposition))]
        facts += [("拟调用工具", clean(item.tool, secrets=secrets)) for item in value.call_summaries]
        status = "failed" if value.disposition == "failed" else "rejected" if value.disposition in {
            "policy_rejected", "controller_rejected"} else "proposed" if value.disposition == "accepted" and value.action_kind == "tool_calls" else status
    elif kind in {"hub_observation", "route_matrix", "directions"}:
        facts = [("查询结果", _word(value.observation_status)), *_provenance(value.provenance, planning=True)]
        status = "failed" if value.observation_status == "failure" else status
        if kind == "hub_observation":
            facts.append(("枢纽用途", _word(value.endpoint_kind)))
            if value.success_candidate is not None:
                candidate = value.success_candidate
                facts += [("匹配地址", candidate.formatted_address),
                    ("百度坐标", f"{candidate.coordinate.latitude}, {candidate.coordinate.longitude}")]
        elif kind == "route_matrix":
            facts += [("行程日期", value.date.isoformat()), ("交通方式", _word(value.mode)),
                ("返回路段", len(value.cells)), ("核验成功路段", sum(item.status == "success" for item in value.cells))]
        else:
            facts += [("交通方式", _word(value.transport_semantic)), ("路线模式", _word(value.route_mode))]
            for alternative in value.alternatives:
                facts += [("路线时长", f"{alternative.duration_s} 秒"), ("路线距离", f"{alternative.distance_m} 米")]
    elif kind == "planning_state":
        record = value.record
        state = getattr(record, "state", None)
        draft = getattr(record, "draft", None) if state is None else state.draft
        facts = [("提交状态", _word(record.record_type)), ("草案天数", len(draft.days) if draft is not None else 0),
            ("待解决缺口", len(getattr(record, "gaps", ())))]
        status = "accepted" if record.record_type == "completed" else "failed" if record.record_type == "failed" else status
        if state is not None:
            facts.append(("当前阶段", _word(state.current_stage)))
            if state.last_rejection is not None:
                status = "rejected"
                facts.extend(_rejection(state.last_rejection.reason, state.last_rejection.field))
    elif kind == "result_day":
        facts = [("第几天", value.data.day_no), ("旅行日期", value.data.date.isoformat()), ("主题", value.data.theme),
            ("活动数量", len(value.data.visits)), ("路线数量", len(value.data.route_legs))]
    elif kind == "result_index":
        facts = [("已保存日块", len(value.days)), ("交付状态", "以网页完整攻略加载校验为准")]
    body = _body(kind, facts, status=status, secrets=secrets)
    if body.phase is not request.operation_id.phase:
        raise ValueError("artifact phase mismatch")
    return body


def project_requirement(*, state: TravelSessionState, confirmed: bool, secrets: tuple[str, ...]) -> PublicRecordBody | None:
    state = TravelSessionState.model_validate_json(state.model_dump_json(), strict=True)
    outcome = state.requirement_outcome
    if type(confirmed) is not bool or confirmed != (type(outcome) is ConfirmedTravelRequirement):
        raise ValueError("verified confirmation required")
    requirement = getattr(outcome, "requirement", None)
    if requirement is None:
        return None
    time = requirement.time_window
    facts = [("需求状态", "已确认" if confirmed else "已提取，待确认")]
    trip = None
    if requirement.destination is not None:
        facts.append(("目的城市", requirement.destination))
    for value, label in ((time.sightseeing_start_date,"游玩开始日期"), (time.sightseeing_end_date,"游玩结束日期"),
            (time.sightseeing_days,"游玩天数"), (time.arrival.hub_name,"到达枢纽"),
            (time.arrival.local_datetime,"到达时间"), (time.departure.hub_name,"离开枢纽"),
            (time.departure.local_datetime,"离开时间")):
        if value is not None:
            facts.append((label, value))
    if requirement.preferences.interests:
        facts.append(("兴趣", "、".join(item.value for item in requirement.preferences.interests)))
    if requirement.preferences.pace is not None:
        facts.append(("节奏", requirement.preferences.pace.value))
    if requirement.destination and time.sightseeing_start_date and time.sightseeing_end_date and time.sightseeing_days:
        try:
            trip = PublicTrip(city=clean(requirement.destination, secrets=secrets, limit=128),
                start_date=date.fromisoformat(time.sightseeing_start_date), end_date=date.fromisoformat(time.sightseeing_end_date),
                days=time.sightseeing_days)
        except ValueError:
            pass
    return _body("requirement", facts, status="accepted" if confirmed else "proposed", secrets=secrets, trip=trip)
