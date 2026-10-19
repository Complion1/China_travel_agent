"""通过生产模型装配检查实际请求，HTTP响应使用离线测试数据。"""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.baidu_map import BaiduCoordinate, GeocodeCandidate, GeocodeSuccess, MapCapability
from app.itinerary_planning.prompt_loader import load_planning_prompt
from app.llm_runtime import DeepSeekRuntime
import app.llm_runtime.provider as provider
from app.llm_runtime.settings import DeepSeekSettings
from app.workflow_contracts import OperationId, WorkflowPhase
from tests.support.itinerary_planning_fakes import (
    FakeMapAdapter, PlanningServiceWithTrustedSyntheticResearch,
    make_planning_budget_manager, map_provenance,
)
from tests.unit.itinerary_planning.test_service_initial import _fixture
from tests.unit.llm_runtime.fakes import RecordingSleeper
from tests.unit.llm_runtime.test_raw_interaction_capture import response


@pytest.mark.asyncio
async def test_public_planning_sends_merge_rules_and_current_schema_to_the_sdk(monkeypatch):
    wire_requests = []

    def handler(request):
        wire_requests.append(json.loads(request.content))
        if len(wire_requests) == 1:
            calls = [dict(id=f"hub-{endpoint}", type="function", function=dict(
                name="geocode_hub", arguments=json.dumps({"endpoint_kind": endpoint})))
                for endpoint in ("arrival", "departure")]
            body = response(content=None, finish="tool_calls", tools=calls)
        else:
            assert len(wire_requests) == 2
            body = response(content="结束")
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    original = provider.ChatDeepSeek

    def factory(**kwargs):
        model = original(**kwargs)
        client = model.root_async_client._client
        client._transport = httpx.MockTransport(handler)
        client._mounts = {}
        return model

    monkeypatch.setattr(provider, "ChatDeepSeek", factory)
    runtime = DeepSeekRuntime(provider.LangChainDeepSeekAdapter(
        DeepSeekSettings(api_key=SecretStr("offline-route-test"))), RecordingSleeper())
    _, request, _, _, repository = _fixture()
    plan = request.operation_plan
    request = request.model_copy(update={"operation_plan": plan.model_copy(update={
        "direction_operation_ids": tuple(OperationId(run_id=plan.run_id,
            phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"route_wire_{i}") for i in range(2))})})
    maps = FakeMapAdapter(geocode_factory=lambda _: GeocodeSuccess(result=GeocodeCandidate(
        formatted_address="合成测试枢纽", coordinate=BaiduCoordinate(latitude=31.2, longitude=121.5),
        confidence=0.98), provenance=map_provenance(MapCapability.GEOCODING)))
    service = PlanningServiceWithTrustedSyntheticResearch(runtime=runtime, map_adapter=maps,
        artifact_repository=repository, budget_manager=make_planning_budget_manager(plan.run_id))
    try:
        result = await service.plan_initial(request)
        assert result.outcome_type == "incomplete"
        assert result.budgets.model_invocations_consumed == len(wire_requests) == 2
        assert len(maps.geocode_calls) == 2
        assert not maps.directions_calls
        prompt = load_planning_prompt()
        for payload in wire_requests:
            system = next(message["content"] for message in payload["messages"] if message["role"] == "system")
            assert system == prompt.text
            assert "每轮最多输出一个 plan_directions 调用" in system
            assert "多天路线，统一放入同一个调用的 legs" in system
            assert "抵离接驳与日内路线分阶段查询" in system
            assert "根据所有日期的 daily_windows 形成整份初步安排" in system
            assert "第一次日内 plan_directions 必须携带 daily_outline" in system
        route = next(tool["function"] for tool in wire_requests[1]["tools"]
            if tool["function"]["name"] == "plan_directions")
        assert "每轮最多一个调用" in route["description"]
        assert "多天路线合并到同一个 legs" in route["description"]
        assert "先查询抵离接驳，再查询日内路线" in route["description"]
        assert route["strict"] is True
        assert set(route["parameters"]["required"]) == {"daily_outline", "legs", "visit_intents"}
        actions = [entry.content for entry in repository.put_requests
            if entry.artifact_type == "itinerary_planning.model_action"]
        assert len(actions) == 2
        assert all(entry["schema_version"] == "11.0" and entry["prompt_version"] == "planning-v12"
            and entry["prompt_hash"] == prompt.sha256 for entry in actions)
    finally:
        await service.aclose()
        await runtime.aclose()
