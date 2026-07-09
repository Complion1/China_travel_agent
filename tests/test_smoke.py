import os

from app.agent.guardrail import TravelGuardrail
from app.agent.router import DeepSeekRouterProvider, Intent, LLMRouter, RouterProviderError
from app.agent.schemas import Coordinates, PlaceCandidate, PlaceRole, RouteCandidate, TravelPlanState
from app.config import _load_env_file, get_env
from app.nlp.extractor import RequirementExtractor


def test_config_loads_project_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("BAIDU_MAP_AUTH_TOKEN", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("BAIDU_MAP_AUTH_TOKEN=test-token\n", encoding="utf-8")

    _load_env_file(env_path)

    assert os.getenv("BAIDU_MAP_AUTH_TOKEN") == "test-token"
    assert get_env("BAIDU_MAP_AUTH_TOKEN") == "test-token"


class FakeDeepSeekClient:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = []

    def chat_json(self, messages, **kwargs):  # noqa: ANN001
        self.calls.append((messages, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def test_llm_router_returns_route_decision():
    client = FakeDeepSeekClient(
        {
            "intent": "travel_plan",
            "skill_name": "travel_plan",
            "confidence": 0.93,
            "reason": "The user wants a multi-day travel itinerary.",
        }
    )
    router = LLMRouter(provider=DeepSeekRouterProvider(client=client))

    decision = router.route("帮我规划杭州3天自然美食路线")

    assert decision.intent == Intent.TRAVEL_PLAN
    assert decision.skill_name == "travel_plan"
    assert decision.confidence == 0.93
    assert decision.source == "deepseek"
    assert decision.raw["intent"] == "travel_plan"
    messages, kwargs = client.calls[0]
    assert "intent" in messages[1]["content"]
    assert kwargs["temperature"] == 0


def test_llm_router_rejects_inconsistent_skill():
    client = FakeDeepSeekClient(
        {
            "intent": "route_query",
            "skill_name": "travel_plan",
            "confidence": 0.9,
            "reason": "bad route",
        }
    )
    router = LLMRouter(provider=DeepSeekRouterProvider(client=client))

    try:
        router.route("从杭州东站到西湖怎么走")
    except RouterProviderError:
        return
    raise AssertionError("expected RouterProviderError")


def test_llm_router_propagates_provider_error():
    router = LLMRouter(provider=DeepSeekRouterProvider(client=FakeDeepSeekClient(error=RuntimeError("timeout"))))

    try:
        router.route("帮我规划杭州3天旅游攻略")
    except RuntimeError as exc:
        assert str(exc) == "timeout"
        return
    raise AssertionError("expected RuntimeError")


def test_extractor_basic_fields():
    req = RequirementExtractor().extract("我想去杭州玩3天，喜欢自然风景和美食，高铁到杭州东站，轻松一点")
    assert req.city == "杭州"
    assert req.days == 3
    assert "nature" in req.styles
    assert "food" in req.styles
    assert req.arrival_place == "杭州东站"
    assert req.pace == "relaxed"


def test_guardrail_requires_city_and_days():
    state = TravelPlanState(user_input="demo")
    state.requirement.city = "杭州"
    state.requirement.days = 3
    checked = TravelGuardrail().check_requirements(state)
    assert checked.missing_fields == []


def test_travel_plan_state_serializes_nested_schemas():
    state = TravelPlanState(user_input="demo")
    state.hotel_candidates.append(
        PlaceCandidate(
            name="湖滨酒店",
            role=PlaceRole.HOTEL.value,
            address="杭州市上城区",
            location=Coordinates(lat=30.251234, lng=120.165678),
        )
    )
    state.routes.append(
        RouteCandidate(
            name="arrival_to_hotel",
            origin="杭州东站",
            destination="湖滨酒店",
            summary="地铁约30分钟",
            resource_key="demo",
        )
    )

    payload = state.to_dict()

    assert payload["hotel_candidates"][0]["name"] == "湖滨酒店"
    assert payload["hotel_candidates"][0]["location"]["lat"] == 30.251234
    assert payload["routes"][0]["resource_key"] == "demo"
    assert payload["validation_report"]["ok"] is True
