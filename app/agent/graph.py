from __future__ import annotations

from typing import Callable

from app.agent.formatter import TravelFormatter
from app.agent.guardrail import TravelGuardrail
from app.agent.schemas import TravelPlanState
from app.nlp.extractor import RequirementExtractor


class TravelPlanningGraph:
    """Explicit graph facade.

    It uses LangGraph when installed. Otherwise it falls back to the same
    deterministic node order, which keeps the architecture testable before
    dependencies are installed.
    """

    def __init__(
        self,
        extractor: RequirementExtractor,
        guardrail: TravelGuardrail,
        planner_node: Callable[[TravelPlanState], TravelPlanState],
        formatter: TravelFormatter,
    ):
        self.extractor = extractor
        self.guardrail = guardrail
        self.planner_node = planner_node
        self.formatter = formatter
        self._compiled = self._try_build_langgraph()

    def run(self, text: str) -> TravelPlanState:
        if self._compiled is not None:
            raw = self._compiled.invoke({"state": TravelPlanState(user_input=text)})
            return raw["state"]
        return self._run_fallback(text)

    def _run_fallback(self, text: str) -> TravelPlanState:
        state = TravelPlanState(user_input=text)
        state.requirement = self.extractor.extract(text)
        state = self.guardrail.check_requirements(state)
        if not state.missing_fields:
            state = self.planner_node(state)
        state = self.guardrail.validate_plan(state)
        state.final_answer = self.formatter.format(state)
        return state

    def _try_build_langgraph(self):
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError:
            return None

        graph = StateGraph(dict)

        def extract_node(raw: dict) -> dict:
            state: TravelPlanState = raw["state"]
            state.requirement = self.extractor.extract(state.user_input)
            return {"state": state}

        def guardrail_node(raw: dict) -> dict:
            return {"state": self.guardrail.check_requirements(raw["state"])}

        def planner_node(raw: dict) -> dict:
            state: TravelPlanState = raw["state"]
            if not state.missing_fields:
                state = self.planner_node(state)
            return {"state": state}

        def validator_node(raw: dict) -> dict:
            return {"state": self.guardrail.validate_plan(raw["state"])}

        def formatter_node(raw: dict) -> dict:
            state: TravelPlanState = raw["state"]
            state.final_answer = self.formatter.format(state)
            return {"state": state}

        graph.add_node("extractor", extract_node)
        graph.add_node("guardrail", guardrail_node)
        graph.add_node("planner", planner_node)
        graph.add_node("validator", validator_node)
        graph.add_node("formatter", formatter_node)
        graph.add_edge(START, "extractor")
        graph.add_edge("extractor", "guardrail")
        graph.add_edge("guardrail", "planner")
        graph.add_edge("planner", "validator")
        graph.add_edge("validator", "formatter")
        graph.add_edge("formatter", END)
        return graph.compile()
