"""Typed deterministic boundaries used only by M14 tests."""
from datetime import datetime, timezone
import json
from pathlib import Path
from app.web_application import WebApplicationSettings

class FixedClock:
    def __init__(self, value=None):
        self.value = value or datetime(2026, 10, 5, 3, tzinfo=timezone.utc)
    def now(self):
        return self.value

def web_settings(**changes):
    return WebApplicationSettings(**(dict(public_origin="http://127.0.0.1:8080",
        pause_key_file=Path("/tmp/test-pause-key.json"), data_volume_path=Path("/tmp")) | changes))


class OfflineProvider:
    """Provider boundary only: real M02 decoding/budgeting and real M03 extraction remain."""
    def __init__(self, payload, full_text, maps):
        import asyncio
        self.payload, self.full_text, self.maps = payload, full_text, maps
        self.calls = 0
        self.closed = False
        self.block = False
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.repair = False
        self.fail = False
        self.fail_extraction = False
        self.reset()

    def reset(self):
        from tests.support.demo_workflow_fakes import research_boundaries, PlanningModel
        self.research, research_map = research_boundaries()
        self.planning = PlanningModel()
        self.maps.research = research_map

    @staticmethod
    def response(content=None, tool_calls=(), finish="stop"):
        from app.llm_runtime.protocols import ProviderResponse
        return ProviderResponse(content=content, tool_calls=tool_calls, finish_reason=finish,
            model_name="deepseek-v4-flash", usage=dict(prompt_tokens=1, completion_tokens=1,
                total_tokens=2, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1), reasoning_content=None,
            raw_tool_calls=tuple((call_id, name, json.dumps(arguments, ensure_ascii=False))
                                 for call_id, name, arguments in tool_calls))

    def form_payload(self, message):
        """Decode this test's explicit form answers; never fill absent answers."""
        from app.requirements import ATTR_TO_PATH
        import re
        labels = {
            "目的城市":"destination", "正式游玩开始日期":"sightseeing_start_date",
            "正式游玩结束日期":"sightseeing_end_date", "正式游玩天数":"sightseeing_days",
            "到达机场/车站":"arrival_hub_name", "到达日期与时间":"arrival_datetime",
            "离开机场/车站":"departure_hub_name", "离开日期与时间":"departure_datetime",
            "到达枢纽类型":"arrival_hub_type", "离开枢纽类型":"departure_hub_type",
            "成人数量":"adults_count", "儿童数量":"children_count", "儿童年龄":"children_ages",
            "老人数量":"seniors_count", "房间数量":"room_count", "房型要求":"room_type_requirements",
            "行动限制":"mobility_constraints", "饮食禁忌":"dietary_restrictions",
            "兴趣偏好":"interests", "行程节奏":"pace",
        }
        numbers = {"sightseeing_days", "adults_count", "children_count", "seniors_count", "room_count"}
        payload = {name:None for name in self.payload if name not in {"evidence", "is_correction"}}
        evidence = []
        for line in message.splitlines():
            label, separator, text = line.partition("：")
            if re.fullmatch(r"到达.+的机场/车站", label):
                label = "到达机场/车站"
            elif re.fullmatch(r"离开.+的机场/车站", label):
                label = "离开机场/车站"
            if not separator or label not in labels or not text:
                continue
            attribute = labels[label]
            if attribute in {"interests", "pace"} and text == "由 Agent 决定":
                attribute = "agent_decides_"+attribute
                value = True
            elif attribute in numbers:
                value = int(text)
            elif attribute == "children_ages":
                value = [int(item) for item in text.split("、")]
            elif attribute == "interests":
                value = text.split("、")
            elif attribute in {"arrival_hub_type", "departure_hub_type"}:
                value = {"机场":"airport", "火车站或高铁站":"railway_station"}[text]
            else:
                value = text
            payload[attribute] = value
            evidence.append({"field_path":ATTR_TO_PATH[attribute], "text":line})
        return payload | {"is_correction":False, "evidence":evidence}

    async def ainvoke_json(self, request):
        import json
        from app.llm_runtime import ModelRole, PromptEnvelope
        self.calls += 1
        self.entered.set()
        if self.block:
            await self.release.wait()
        if (self.fail and request.profile.role is ModelRole.CANDIDATE_RESEARCH) or (
                self.fail_extraction and request.profile.role is ModelRole.REQUIREMENT_EXTRACTION):
            from app.llm_runtime.protocols import ProviderFailure
            return ProviderFailure(code="authentication_failed", retryable=False, provider_status_code=401, retry_after_seconds=None)
        if request.profile.role is ModelRole.REQUIREMENT_EXTRACTION:
            data = json.loads(request.user)
            if data["intent"] == "initial":
                self.reset()
            payload = dict(self.payload)
            if data["user_message"].startswith("补充旅行需求：\n"):
                payload = self.form_payload(data["user_message"])
            elif len(data["user_message"]) < 100:
                payload = {name: None for name in payload if name not in {"evidence", "is_correction"}}
                payload.update(destination="杭州", evidence=[{"field_path":"destination", "text":"杭州"}], is_correction=False)
            payload["is_correction"] = data["intent"] in {"correction", "confirmation_revision"}
            if self.repair and "repair_feedback" not in data:
                payload["evidence"] = [{"field_path":"destination", "text":"不在用户原文的证据"}]
            return self.response(json.dumps(payload, ensure_ascii=False))
        outcome = await self.research.ainvoke_structured(PromptEnvelope(system_text=request.system, user_text=request.user), object)
        value=json.loads(outcome.value.model_dump_json())
        if request.json_schema and "output" in request.json_schema.get("properties",{}):
            value={"output":value}
        return self.response(json.dumps(value,ensure_ascii=False))

    async def ainvoke_tools(self, request):
        from app.llm_runtime import PromptEnvelope
        self.calls += 1
        return self.tool_response(await self.planning.ainvoke_tools(PromptEnvelope(system_text=request.system, user_text=request.user), ()))

    def tool_response(self, outcome):
        import json
        from app.llm_runtime import ToolCallsReady
        if type(outcome) is ToolCallsReady:
            return self.response(tool_calls=tuple((call.call_id, call.tool_name, json.loads(call.arguments.model_dump_json())) for call in outcome.calls), finish="tool_calls")
        return self.response(outcome.final_text)

    async def ainvoke_tool_continuation(self, request):
        from app.llm_runtime import ToolResult
        self.calls += 1
        last_assistant = max(i for i, item in enumerate(request.messages) if item["role"] == "assistant")
        results = tuple(ToolResult(call_id=item["tool_call_id"], content=item["content"])
            for item in request.messages[last_assistant + 1:] if item["role"] == "tool")
        return self.tool_response(await self.planning.acontinue_tools(results))

    async def aclose(self):
        self.closed = True

    def close(self):
        self.closed = True


class OfflineMaps:
    def __init__(self):
        from app.baidu_map import GeocodeSuccess, GeocodeCandidate, BaiduCoordinate, MapCapability
        from tests.support.itinerary_planning_fakes import FakeMapAdapter, map_provenance
        self.planning = FakeMapAdapter(geocode_factory=lambda request: GeocodeSuccess(result=GeocodeCandidate(
            formatted_address="杭州交通枢纽", coordinate=BaiduCoordinate(latitude=30.29, longitude=120.21), confidence=0.98),
            provenance=map_provenance(MapCapability.GEOCODING)))
        self.research = None
        self.calls = 0
        self.closed = False
        self.fail_session = False

    async def search_places(self, request):
        self.calls += 1
        if self.fail_session:
            from app.baidu_map import BaiduMapSessionError
            raise BaiduMapSessionError()
        if request.query in {"酒店", "宾馆", "住宿"} or request.query.endswith(" 酒店"):
            from tests.support.hotel_selection_fakes import map_success, place
            return map_success(place("h1"), place("h2", index=1), place("h3", index=2))
        return await self.research.search_places(request)

    async def get_place_details(self, request):
        self.calls += 1
        return await self.research.get_place_details(request)

    async def geocode(self, request):
        self.calls += 1
        return await self.planning.geocode(request)

    async def calculate_route_matrix(self, request):
        self.calls += 1
        return await self.planning.calculate_route_matrix(request)

    async def plan_directions(self, request):
        self.calls += 1
        return await self.planning.plan_directions(request)

    async def reverse_geocode(self, request):
        raise AssertionError("unexpected reverse geocode")

    async def get_weather(self, request):
        raise AssertionError("unexpected weather")


async def offline_boundaries():
    import json
    from app.requirements import ATTR_TO_PATH, StrictRequirementPatchDTO
    from tests.unit.result_formatting.fakes import make_confirmed
    transition = await make_confirmed(budget_total_cny=None)
    tree = transition.outcome.requirement.model_dump(mode="json")
    tree["preferences"].update(pace="轻松", transport_preferences=None, daily_time_preferences=None,
        free_text_notes=None, hotel_preferences=None)
    payload, evidence, spans = {}, [], []
    for attribute, path in ATTR_TO_PATH.items():
        value = tree
        for part in path.split("."):
            value = value[part]
        payload[attribute] = value
        if value is not None:
            span = f"{attribute}: {json.dumps(value, ensure_ascii=False)}"
            spans.append(span)
            evidence.append(dict(field_path=path, text=span))
    payload.update(is_correction=False, evidence=evidence)
    StrictRequirementPatchDTO.model_validate_json(json.dumps(payload, ensure_ascii=False), strict=True)
    maps = OfflineMaps()
    provider = OfflineProvider(payload, "\n".join(spans), maps)
    return provider, maps


def offline_factory(*, monkeypatch, settings, pg_settings, provider, maps, disk_probe=None, clock=None):
    """Test-only factory swaps model/map/clock/disk boundaries in the actual production root."""
    from contextlib import asynccontextmanager
    from dataclasses import replace
    from types import SimpleNamespace
    from app.web_application import assembly
    from app.web_application import WebApplicationService
    environment = {"POSTGRES_HOST":pg_settings.host, "POSTGRES_PORT":str(pg_settings.port),
        "POSTGRES_DB":pg_settings.database, "POSTGRES_USER":pg_settings.user,
        "POSTGRES_PASSWORD":pg_settings.password.get_secret_value(),
        "DEEPSEEK_API_KEY":"m14-offline-provider-boundary-key", "BAIDU_MAPS_API_KEY":"m14-offline-map-boundary-key"}
    @asynccontextmanager
    async def map_factory(**kwargs):
        try:
            yield SimpleNamespace(adapter=maps, map_launch=maps)
        finally:
            maps.closed = True
    @asynccontextmanager
    async def factory():
        with monkeypatch.context() as context:
            context.setattr(assembly, "LangChainDeepSeekAdapter", lambda settings: provider)
            context.setattr(assembly, "create_baidu_map_runtime", map_factory)
            context.setattr(assembly, "SystemUtcClock", lambda: clock or FixedClock(datetime(2026,8,20,tzinfo=timezone.utc)))
            context.setattr(assembly, "WebApplicationService", lambda *, settings, dependencies:
                WebApplicationService(settings=settings, dependencies=replace(dependencies,
                    disk_probe=disk_probe or (lambda:20 * 1024**3))))
            async with assembly.production_service(settings=settings, environ=environment) as service:
                yield service
    return factory


async def wait_for_view(service, owner, run_id, *, activity, timeout=25):
    import asyncio
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        view = await service.get_run(owner=owner, run_id=run_id)
        if view.run.activity == activity:
            return view
        await asyncio.sleep(0.05)
    raise AssertionError(("expected activity not reached", activity, view.run.activity, view.run.lifecycle, view.run.terminal_code))


async def wait_for_summary(service, owner, run_id, *, status="saved", timeout=12):
    import asyncio
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        view = await service.read_summary(owner=owner, run_id=run_id)
        if view.status == status:
            return view
        await asyncio.sleep(0.05)
    raise AssertionError("expected summary status not reached")
