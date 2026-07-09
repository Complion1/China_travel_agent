from __future__ import annotations

from app.agent.schemas import TravelPlanState, ValidationReport, ValidationSeverity


class TravelGuardrail:
    required_fields = ("city", "days")

    def check_requirements(self, state: TravelPlanState) -> TravelPlanState:
        req = state.requirement
        missing = [field for field in self.required_fields if not getattr(req, field)]
        state.missing_fields = missing
        if missing:
            label_map = {"city": "目的地城市", "days": "旅游天数"}
            state.clarify_question = "还需要补充：" + "、".join(label_map.get(item, item) for item in missing)
        return state

    def validate_plan(self, state: TravelPlanState) -> TravelPlanState:
        report = ValidationReport(ok=not state.missing_fields)
        if state.missing_fields:
            report.add(
                ValidationSeverity.ERROR.value,
                "missing_required_fields",
                "缺少关键字段，未进入完整规划。",
                field="requirement",
            )
        if not state.attractions:
            report.add(
                ValidationSeverity.WARNING.value,
                "missing_attractions",
                "暂未拿到景点候选，可能是工具调用失败或 token 未配置。",
                field="attractions",
            )
        if not state.restaurants:
            report.add(
                ValidationSeverity.WARNING.value,
                "missing_restaurants",
                "暂未拿到餐饮候选，后续需要补齐。",
                field="restaurants",
            )
        if state.requirement.arrival_place and not state.routes:
            report.add(
                ValidationSeverity.WARNING.value,
                "missing_arrival_route",
                "用户提供了到达点，但尚未完成到达点到酒店/住宿区域的路线校验。",
                field="routes",
            )
        state.validation_report = report
        return state
