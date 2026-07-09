from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent.formatter import TravelFormatter
from app.agent.graph import TravelPlanningGraph
from app.agent.guardrail import TravelGuardrail
from app.agent.schemas import PlaceRole, RouteCandidate, ToolCallRecord, TravelPlanState, to_plain
from app.nlp.extractor import RequirementExtractor
from app.tools.baidu_map_tool import BaiduMapTool, BaiduMapToolError, compact_place_results

if TYPE_CHECKING:
    from app.agent.trace import TraceRecorder


@dataclass(frozen=True)
class SkillResult:
    answer: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)


class BaseSkill:
    name = "base"

    def run(self, text: str, trace: "TraceRecorder | None" = None) -> SkillResult:
        raise NotImplementedError


class TravelPlanSkill(BaseSkill):
    name = "travel_plan"

    def __init__(self, map_tool: BaiduMapTool | None = None):
        self.map_tool = map_tool or BaiduMapTool()
        self.graph = TravelPlanningGraph(
            extractor=RequirementExtractor(),
            guardrail=TravelGuardrail(),
            planner_node=self._planner_node,
            formatter=TravelFormatter(),
        )

    def run(self, text: str, trace: "TraceRecorder | None" = None) -> SkillResult:
        if trace is not None:
            trace.thought("Run travel planning graph.", skill=self.name)
        state = self.graph.run(text)
        if trace is not None:
            trace.observation(
                "Travel planning graph completed.",
                requirement=state.requirement.__dict__,
                missing_fields=state.missing_fields,
                tool_calls=[record.__dict__ for record in state.tool_calls],
            )
        return SkillResult(answer=state.final_answer or "", data=state.to_dict())

    def _planner_node(self, state: TravelPlanState) -> TravelPlanState:
        req = state.requirement
        city = req.city or ""
        days = req.days or 1
        styles = req.styles or ["classic"]

        state.weather = self._safe_tool(
            state,
            "baidu_weather",
            {"region": city},
            lambda: self.map_tool.weather(region=city),
        )

        hotel_query = f"{city}适合游客住宿的酒店区域或酒店，交通方便，中等预算"
        hotel_payload = self._safe_tool(
            state,
            "baidu_place",
            {"user_raw_request": hotel_query, "region": city},
            lambda: self.map_tool.place(hotel_query, city),
        )
        if hotel_payload:
            state.hotel_candidates = compact_place_results(hotel_payload, limit=5, role=PlaceRole.HOTEL.value)

        attraction_query = f"{city}{days}天旅游，风格：{','.join(styles)}，推荐景点"
        attraction_payload = self._safe_tool(
            state,
            "baidu_place",
            {"user_raw_request": attraction_query, "region": city},
            lambda: self.map_tool.place(attraction_query, city),
        )
        if attraction_payload:
            state.attractions = compact_place_results(
                attraction_payload,
                limit=max(6, days * 3),
                role=PlaceRole.ATTRACTION.value,
            )

        restaurant_query = f"{city}本地特色餐厅和美食街，适合游客"
        restaurant_payload = self._safe_tool(
            state,
            "baidu_place",
            {"user_raw_request": restaurant_query, "region": city},
            lambda: self.map_tool.place(restaurant_query, city),
        )
        if restaurant_payload:
            state.restaurants = compact_place_results(
                restaurant_payload,
                limit=max(5, days * 2),
                role=PlaceRole.RESTAURANT.value,
            )

        if req.arrival_place and state.hotel_candidates:
            hotel_name = state.hotel_candidates[0].name or "推荐住宿区域"
            direction_query = f"从{req.arrival_place}到{hotel_name}，公共交通或打车怎么走"
            route_payload = self._safe_tool(
                state,
                "baidu_direction",
                {"user_raw_request": direction_query, "location": "30.000000,120.000000"},
                lambda: self.map_tool.direction(direction_query, location="30.000000,120.000000"),
            )
            if route_payload:
                state.routes.append(
                    RouteCandidate(
                        name="arrival_to_hotel",
                        origin=req.arrival_place,
                        destination=hotel_name,
                        summary=route_payload.get("message") or "route returned",
                        resource_key=route_payload.get("resource_key"),
                        raw=route_payload,
                    )
                )

        return state

    def _safe_tool(self, state: TravelPlanState, tool: str, tool_input: dict[str, Any], call):
        try:
            payload = call()
        except (BaiduMapToolError, ValueError) as exc:
            state.tool_calls.append(ToolCallRecord(tool=tool, tool_input=to_plain(tool_input), ok=False, error=str(exc)))
            return None

        state.tool_calls.append(
            ToolCallRecord(
                tool=tool,
                tool_input=to_plain(tool_input),
                ok=True,
                output_summary=_summarize_payload(payload),
                raw_output=payload,
            )
        )
        return payload


class EchoSkill(BaseSkill):
    name = "chat"

    def run(self, text: str, trace: "TraceRecorder | None" = None) -> SkillResult:
        return SkillResult(answer=f"暂时只支持旅行规划任务。收到：{text}")


def _summarize_payload(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    keys = ",".join(payload.keys())
    results = payload.get("results") or []
    count = len(results) if isinstance(results, list) else 0
    return f"status={status}; keys={keys}; results={count}"
