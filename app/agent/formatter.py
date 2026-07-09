from __future__ import annotations

from typing import Any

from app.agent.schemas import PlaceCandidate, RouteCandidate, TravelPlanState


class TravelFormatter:
    def format(self, state: TravelPlanState) -> str:
        if state.clarify_question:
            return state.clarify_question

        req = state.requirement
        city = req.city or "目的地"
        days = req.days or 1
        styles = "、".join(req.styles or ["classic"])

        lines = [
            f"# {city}{days}天一条龙攻略草案",
            "",
            "## 已识别需求",
            f"- 城市：{city}",
            f"- 天数：{days}",
            f"- 风格：{styles}",
            f"- 到达点：{req.arrival_place or '未提供'}",
            f"- 节奏：{req.pace}",
            f"- 交通偏好：{req.transport_preference or '公共交通+打车'}",
            "",
            "## 住宿候选",
        ]
        lines.extend(_format_items(state.hotel_candidates, "暂无住宿候选。"))
        lines.extend(["", "## 景点候选"])
        lines.extend(_format_items(state.attractions, "暂无景点候选。"))
        lines.extend(["", "## 餐饮候选"])
        lines.extend(_format_items(state.restaurants, "暂无餐饮候选。"))

        if state.routes:
            lines.extend(["", "## 已校验路线"])
            for route in state.routes:
                lines.append(f"- {_route_name(route)}: {_route_summary(route)}")
                resource_key = _route_resource_key(route)
                if resource_key:
                    lines.append(f"  - resource_key: {resource_key}")

        lines.extend(["", "## 每日骨架"])
        for day in range(1, int(days) + 1):
            lines.append(f"### Day {day}")
            if day == 1:
                lines.append("- 到达机场/火车站 -> 酒店/住宿区域")
            lines.append("- 酒店 -> 上午景点 -> 午餐 -> 下午景点 -> 晚餐/夜游 -> 酒店")
            if day == int(days):
                lines.append("- 若当天返程，预留返程缓冲时间")

        warnings = state.validation_report.warnings
        if warnings:
            lines.extend(["", "## 校验提示"])
            lines.extend(f"- {item}" for item in warnings)

        return "\n".join(lines)


def _format_items(items: list[PlaceCandidate | dict[str, Any]], fallback: str) -> list[str]:
    if not items:
        return [f"- {fallback}"]
    lines = []
    for item in items[:8]:
        if isinstance(item, PlaceCandidate):
            name = item.name or "未命名地点"
            address = item.address or "地址未返回"
        else:
            name = item.get("name") or "未命名地点"
            address = item.get("address") or "地址未返回"
        lines.append(f"- {name}（{address}）")
    return lines


def _route_name(route: RouteCandidate | dict[str, Any]) -> str:
    if isinstance(route, RouteCandidate):
        return route.name
    return str(route.get("name") or "route")


def _route_summary(route: RouteCandidate | dict[str, Any]) -> str:
    if isinstance(route, RouteCandidate):
        return route.summary or "route returned"
    return str(route.get("summary") or "route returned")


def _route_resource_key(route: RouteCandidate | dict[str, Any]) -> str | None:
    if isinstance(route, RouteCandidate):
        return route.resource_key
    return route.get("resource_key")
