from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from app.llm import DeepSeekClient


class Intent(str, Enum):
    TRAVEL_PLAN = "travel_plan"
    ADJUST_PLAN = "adjust_plan"
    PLACE_QUERY = "place_query"
    ROUTE_QUERY = "route_query"
    CITY_RECOMMEND = "city_recommend"
    CHAT = "chat"


INTENT_TO_SKILL: dict[Intent, str] = {
    Intent.TRAVEL_PLAN: "travel_plan",
    Intent.ADJUST_PLAN: "adjust_plan",
    Intent.PLACE_QUERY: "place_query",
    Intent.ROUTE_QUERY: "route_query",
    Intent.CITY_RECOMMEND: "city_recommend",
    Intent.CHAT: "chat",
}


@dataclass(frozen=True)
class RouteDecision:
    intent: Intent
    skill_name: str
    confidence: float
    reason: str
    source: str = "deepseek"
    raw: dict[str, Any] = field(default_factory=dict)


class RouterProvider(Protocol):
    def classify(self, text: str) -> RouteDecision:
        ...


class RouterProviderError(ValueError):
    pass


class DeepSeekRouterProvider:
    def __init__(self, client: DeepSeekClient | None = None):
        self.client = client or DeepSeekClient()

    def classify(self, text: str) -> RouteDecision:
        raw = self.client.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the intent router for a China travel-planning agent. "
                        "Choose exactly one allowed intent and skill. "
                        "Return a valid JSON object only, with no markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Classify the user request.\n"
                        "Allowed options:\n"
                        "- travel_plan -> travel_plan: plan a full trip by city, days, style, arrival, hotel, places, food, and routes\n"
                        "- adjust_plan -> adjust_plan: revise, replace, or adjust an existing itinerary\n"
                        "- place_query -> place_query: query attractions, restaurants, hotels, or nearby places\n"
                        "- route_query -> route_query: ask how to travel between places or which transport to use\n"
                        "- city_recommend -> city_recommend: recommend destination cities when the user is unsure where to go\n"
                        "- chat -> chat: casual conversation or requests outside travel tool tasks\n\n"
                        "Return JSON fields: intent, skill_name, confidence, reason.\n"
                        "confidence must be a number from 0 to 1.\n\n"
                        f"User request: {text}"
                    ),
                },
            ],
            temperature=0,
            max_tokens=512,
        )
        intent = _parse_intent(raw.get("intent"))
        expected_skill = INTENT_TO_SKILL[intent]
        skill_name = str(raw.get("skill_name") or raw.get("skill") or expected_skill).strip()
        if skill_name != expected_skill:
            raise RouterProviderError(
                f"LLM returned inconsistent skill {skill_name!r} for intent {intent.value!r}"
            )

        return RouteDecision(
            intent=intent,
            skill_name=expected_skill,
            confidence=_parse_confidence(raw.get("confidence")),
            reason=str(raw.get("reason") or "DeepSeek router classified the request."),
            source="deepseek",
            raw=raw,
        )


class LLMRouter:
    """LLM-only router."""

    def __init__(self, provider: RouterProvider | None = None):
        self.provider = provider or DeepSeekRouterProvider()

    def route(self, text: str) -> RouteDecision:
        return self.provider.classify(text)


def _parse_intent(value: object) -> Intent:
    try:
        return Intent(str(value).strip().lower())
    except ValueError as exc:
        raise RouterProviderError(f"LLM returned unsupported intent: {value!r}") from exc


def _parse_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))
