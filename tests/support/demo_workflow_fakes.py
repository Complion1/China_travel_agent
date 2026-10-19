"""Test-only node capabilities; integration tests use M11's real capabilities."""
from types import SimpleNamespace

from app.demo_workflow import DemoBudgetPlanProvider, DEMO_ARTIFACT_REGISTRATIONS
from app.demo_workflow.diagnostics import DemoDiagnostics
from app.demo_workflow.nodes import Nodes
from app.hotel_selection import HOTEL_ARTIFACT_REGISTRATIONS, HotelCandidateSelectionService
from app.candidate_research import CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
from app.itinerary_planning import ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS
from app.result_formatting import RESULT_FORMATTING_ARTIFACT_REGISTRATIONS
from app.workflow_contracts import (
    ArtifactSchemaRegistry, InMemoryArtifactRepository, InMemoryExecutionBudgetManager,
    DEMO_ARTIFACT_REPOSITORY_LIMITS, DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    NoOpSafeWorkflowEventSink, SystemUtcClock, SystemMonotonicClock, RandomUUIDFactory,
    WorkflowPhase, TravelSessionState,
)
from tests.requirement_subgraph_fakes import make_initial_state
from tests.unit.result_formatting.fakes import make_confirmed
from tests.support.hotel_selection_fakes import ScriptedBaiduMapAdapter, map_success, place, FixedClock


def diagnostics(sink=None):
    return DemoDiagnostics(sink=sink or NoOpSafeWorkflowEventSink(), clock=SystemUtcClock(),
        monotonic_clock=SystemMonotonicClock(), uuid_provider=RandomUUIDFactory())


async def hotel_nodes():
    initial = make_initial_state()
    transition = await make_confirmed()
    state = TravelSessionState.model_validate({**initial.model_dump(mode="python"),
        "phase": WorkflowPhase.HOTEL_SELECTION, "requirement_state": transition.state,
        "requirement_outcome": transition.outcome}, strict=True)
    repository = InMemoryArtifactRepository(registry=ArtifactSchemaRegistry(
        HOTEL_ARTIFACT_REGISTRATIONS + CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS + ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS + RESULT_FORMATTING_ARTIFACT_REGISTRATIONS + DEMO_ARTIFACT_REGISTRATIONS),
        limits=DEMO_ARTIFACT_REPOSITORY_LIMITS, utc_clock=SystemUtcClock(), uuid_factory=RandomUUIDFactory())
    budget = InMemoryExecutionBudgetManager(
        plan=DemoBudgetPlanProvider().plan_for_phase(run_id=state.run_id, phase=state.phase, state=state),
        limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS)
    adapter = ScriptedBaiduMapAdapter([map_success(place("hotel1"), place("hotel2", index=1), place("hotel3", index=2))])
    async def ensure(*, state):
        pass
    command = [None]
    cap = SimpleNamespace(artifact_repository=repository, read_only_artifact_repository=repository,
        execution_budget_manager=budget, ensure_phase_budget=ensure, current_command=lambda: command[0])
    caps = SimpleNamespace(for_node=lambda *, node_name: cap)
    deps = SimpleNamespace(hotel_factory=lambda cap: HotelCandidateSelectionService(adapter, cap.artifact_repository,
        cap.execution_budget_manager, FixedClock()), diagnostics=diagnostics())
    return Nodes(deps, caps, {}), state, command, adapter


async def full_dependencies():
    """Real domain/services; deterministic model and map boundaries only."""
    import json
    from datetime import date
    from app.demo_workflow import DemoWorkflowDependencies
    from app.durable_execution import DEMO_DURABLE_EXECUTION_LIMITS
    from app.requirements import RequirementDomainService, RequirementTurnInput, RequirementDomainState, RequirementIntent
    from app.requirements.extraction_contract import ATTR_TO_PATH, StrictRequirementPatchDTO
    from tests.unit.requirements.fakes import RecordingRequirementExtractor, InMemoryEvidenceSink, FixedClock as RequirementClock
    from app.candidate_research import CandidateResearchService
    from app.itinerary_planning import ItineraryPlanningService
    from tests.support.candidate_research_fakes import FakeRuntime as ResearchRuntime
    from tests.support.itinerary_planning_fakes import FakeRuntime as PlanningRuntime
    from app.baidu_map import GeocodeSuccess, GeocodeCandidate, BaiduCoordinate, MapCapability
    from tests.support.itinerary_planning_fakes import FakeMapAdapter, directions_success, map_provenance
    from uuid import uuid4

    confirmed = await make_confirmed(budget_total_cny=None)
    tree = confirmed.outcome.requirement.model_dump(mode="json")
    tree["preferences"].update(pace="轻松", transport_preferences=None, daily_time_preferences=None,
        free_text_notes=None, hotel_preferences=None)
    # Explicit, dated user facts, each backed by a verbatim evidence span.
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
    patch = StrictRequirementPatchDTO.model_validate_json(json.dumps(payload | {"is_correction": False, "evidence": evidence}), strict=True)
    extractor = RecordingRequirementExtractor([patch])
    domain = RequirementDomainService(extractor=extractor, evidence_sink=InMemoryEvidenceSink(), clock=RequirementClock(date(2026, 8, 20)))
    turn = RequirementTurnInput(turn_id=uuid4(), user_message="\n".join(spans), session_date=date(2026, 8, 20),
        intent=RequirementIntent.INITIAL, current=RequirementDomainState())
    hotel_map = ScriptedBaiduMapAdapter([map_success(place("h1"), place("h2", index=1), place("h3", index=2))])
    research_model, research_map = research_boundaries()
    planning_model = PlanningModel()
    planning_model.first_route_minutes = 50
    planning_map = FakeMapAdapter(geocode_factory=lambda request: GeocodeSuccess(result=GeocodeCandidate(
        formatted_address="杭州交通枢纽", coordinate=BaiduCoordinate(latitude=30.29, longitude=120.21), confidence=0.98),
        provenance=map_provenance(MapCapability.GEOCODING)))
    planning_map.directions_factory = lambda _: directions_success(
        duration_s=2580 if len(planning_map.directions_calls) == 2 else 600)
    diagnostic = diagnostics()
    deps = DemoWorkflowDependencies(domain_service=domain, diagnostics=diagnostic,
        hotel_factory=lambda cap: HotelCandidateSelectionService(hotel_map, cap.artifact_repository, cap.execution_budget_manager, FixedClock()),
        research_factory=lambda cap: CandidateResearchService(runtime=ResearchRuntime(research_model), map_adapter=research_map,
            artifact_repository=cap.artifact_repository, budget_manager=cap.execution_budget_manager),
        planning_factory=lambda cap: ItineraryPlanningService(runtime=PlanningRuntime(planning_model), map_adapter=planning_map,
            artifact_repository=cap.artifact_repository, budget_manager=cap.execution_budget_manager),
        durable_limits=DEMO_DURABLE_EXECUTION_LIMITS, artifact_limits=DEMO_ARTIFACT_REPOSITORY_LIMITS)
    return deps, turn, SimpleNamespace(extractor=extractor, hotel_map=hotel_map, research_model=research_model,
        research_map=research_map, planning_model=planning_model, planning_map=planning_map)


def research_boundaries():
    import json
    from app.candidate_research.models import (ProposedSearchAction, ProposedDetailsAction, ProposedActionBatch,
        SearchAreaMode, HotelAnchorRef, JudgmentPatch, RoleJudgmentUpdate, ResearchRole, RoleVerdict, RoleReasonCode,
        EvidenceHandle, InterestJudgmentUpdate, MatchVerdict, InterestReasonCode, MealJudgmentUpdate, MealIntent, MealReasonCode)
    from app.requirements import InterestTag
    from tests.support.candidate_research_fakes import FakeModelOperation, FakeMapAdapter, structured
    from tests.unit.candidate_research.test_service_initial import _multi_place_search, _matching_details
    from tests.unit.candidate_research.test_qualification import legal_judgment
    model = FakeModelOperation()
    def actions(_):
        objectives = json.loads(model.prompts[-1].user_text)["objectives"][:8]
        return structured(ProposedActionBatch(actions=tuple(ProposedSearchAction(ordinal=i,
            objective_id=o["objective_id"], query=f"{o['kind']}-{o.get('interest') or o.get('meal_intent') or i}",
            area_mode=SearchAreaMode.NEARBY if o["kind"] == "initial_hotel_restaurant_nearby" else SearchAreaMode.CITY,
            anchor=HotelAnchorRef() if o["kind"] == "initial_hotel_restaurant_nearby" else None,
            radius_km=3 if o["kind"] == "initial_hotel_restaurant_nearby" else None,
            interest=InterestTag(o["interest"]) if o.get("interest") else None,
            meal_intent=MealIntent(o["meal_intent"]) if o.get("meal_intent") else None) for i, o in enumerate(objectives))))
    patch = JudgmentPatch(role_updates=tuple(RoleJudgmentUpdate(candidate_handle=f"c:{i}",
        role=ResearchRole.ATTRACTION if i < 6 else ResearchRole.RESTAURANT, verdict=RoleVerdict.SUPPORTED,
        reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS, evidence_handles=(EvidenceHandle(handle_id=f"e:{0 if i < 6 else 1}"),)) for i in range(10)),
        interest_updates=tuple(InterestJudgmentUpdate(candidate_handle=f"c:{i}", interest=interest,
            verdict=MatchVerdict.CONFIRMED, reason_code=InterestReasonCode.TARGETED_SEARCH,
            evidence_handles=(EvidenceHandle(handle_id="e:0"),)) for i, interest in enumerate((InterestTag.CULTURE, InterestTag.NATURE))),
        meal_updates=tuple(MealJudgmentUpdate(candidate_handle=f"c:{6+i}", meal_intent=meal,
            verdict=MatchVerdict.CONFIRMED, reason_code=MealReasonCode.TARGETED_MEAL_SEARCH,
            evidence_handles=(EvidenceHandle(handle_id=f"e:{2+i%2}"),))
            for i, meal in enumerate((MealIntent.LUNCH, MealIntent.DINNER, MealIntent.LUNCH, MealIntent.DINNER))),
        requirement_group_updates=())
    def details(_):
        objective = next(o for o in json.loads(model.prompts[-1].user_text)["objectives"] if o["kind"] == "details_maturity")
        return structured(ProposedActionBatch(actions=tuple(ProposedDetailsAction(ordinal=i,
            objective_id=objective["objective_id"], uid=uid) for i, uid in enumerate(objective["candidate_uids"][:8]))))
    def supplement(_):
        objective = next(o for o in json.loads(model.prompts[-1].user_text)["objectives"] if o["kind"] == "supplemental_gap")
        return structured(ProposedActionBatch(actions=(ProposedDetailsAction(ordinal=0,objective_id=objective["objective_id"],uid="r2"),)))
    known_meals = {(item.candidate_handle, item.meal_intent) for item in patch.meal_updates}
    patch = patch.model_copy(update={"meal_updates": (*patch.meal_updates, *tuple(
        MealJudgmentUpdate(candidate_handle=f"c:{ordinal}", meal_intent=meal, verdict=MatchVerdict.UNRESOLVED,
            reason_code=MealReasonCode.EVIDENCE_INSUFFICIENT, evidence_handles=(EvidenceHandle(handle_id="e:1"),))
        for ordinal in range(6, 10) for meal in MealIntent if (f"c:{ordinal}", meal) not in known_meals))})
    model.base_patch = patch
    model.responses = [actions, lambda _: legal_judgment(model, patch), details, lambda _: legal_judgment(model)]
    model.responses.extend([supplement, lambda _: legal_judgment(model)])
    searches = [(("a0", "a1", "a2", "a3", "a4", "a5"), "museum"), (("r0", "r1", "r2", "r3"), "restaurant"),
        (("r0", "r2"), "restaurant"), (("r1", "r3"), "restaurant"), (("a0",), "museum"), (("a1",), "museum"), (("a2",), "museum"), (("r2",), "restaurant")]
    adapter = FakeMapAdapter([*(_multi_place_search(uids, category=kind) for uids, kind in searches),
        *(_matching_details(uid, category="museum" if uid.startswith("a") else "restaurant") for uid in ("a0", "a1", "a2", "a3", "a4", "a5", "r0", "r1", "r2"))])
    return model, adapter


class PlanningModel:
    """Scripted provider proposals; all handles come from real tool outputs."""
    def __init__(self):
        self.call_count = 0
        self.closed = False
        self.feedback = []
        self.known_hours = True
        self.supplement = False
        self.stage = 0
        self.stop_without_draft = False
        self.first_route_minutes = 10

    def close(self):
        self.closed = True

    async def ainvoke_tools(self, prompt, contracts, *, admission_callback=None, request_stage=None, allow_argument_repair=False):
        if admission_callback is not None:
            from app.llm_runtime import InteractionCallIdentity
            from uuid import uuid4
            await admission_callback(InteractionCallIdentity(call_id=uuid4(), logical_call_index=self.call_count))
        if self.stop_without_draft:
            from app.llm_runtime import ToolTurnCompleted
            from tests.unit.itinerary_planning.test_service_initial import _metadata
            self.call_count += 1
            return ToolTurnCompleted(final_text="候选暂不能排出完整路线",metadata=_metadata())
        if self.supplement:
            from app.itinerary_planning.models import RequestCandidateSupplementArgs, ProposedEffectivePlanningPolicy, ProposedSupplementGap, ProposedCandidateDetailsTarget
            from app.requirements import TravelPace
            return self.batch(("request_candidate_supplement", RequestCandidateSupplementArgs(
                effective_policy=ProposedEffectivePlanningPolicy(effective_pace=TravelPace.RELAXED,daily_time_rules=(),time_sensitive_must=(),transport_rules=()),
                draft=None,proposed_gaps=(ProposedSupplementGap(source_gap_type="candidate_supply",date=None,
                    target=ProposedCandidateDetailsTarget(candidate_handles=("candidate-handle-0008",)),evidence_handles=("evidence-handle-0001",)),))))
        return self.geocode()

    def geocode(self):
        from app.itinerary_planning.models import GeocodeHubArgs
        self.stage = 1
        return self.batch(("geocode_hub", GeocodeHubArgs(endpoint_kind="arrival")), ("geocode_hub", GeocodeHubArgs(endpoint_kind="departure")))

    def batch(self, *values):
        from app.llm_runtime import ToolCallsReady, ValidatedToolCall
        from tests.unit.itinerary_planning.test_service_initial import _metadata
        self.call_count += 1
        return ToolCallsReady(calls=tuple(ValidatedToolCall(call_id=f"demo_{self.call_count}_{i}", tool_name=name, arguments=args)
            for i, (name, args) in enumerate(values)), metadata=_metadata())

    async def acontinue_tools(self, results, *, tools=None, admission_callback=None, request_stage=None):
        if admission_callback is not None:
            from app.llm_runtime import InteractionCallIdentity
            from uuid import uuid4
            await admission_callback(InteractionCallIdentity(call_id=uuid4(), logical_call_index=self.call_count))
        import json
        from app.itinerary_planning.models import PlanDirectionsArgs, DirectionLegIntent, TransportSemantic, PurposeVisitIntent, VisitKind
        from app.baidu_map import TravelMode
        self.feedback.append([json.loads(r.content) for r in results])
        if self.stage == 0:
            return self.geocode()
        if self.stage in {1, 2}:
            from datetime import date, datetime
            from zoneinfo import ZoneInfo
            from app.itinerary_planning.models import DailyPlanOutline, TimeInterval, PlannedBreak
            handles = ("hub-location-arrival", "hotel-handle", "candidate-handle-0000", "candidate-handle-0006", "candidate-handle-0007", "hotel-handle", "hub-location-departure")
            self.endpoints = tuple(zip(handles, handles[1:]))
            boundary = self.stage == 1
            indexes = (0, 5) if boundary else (1, 2, 3, 4)
            self.stage += 1
            def at(hour, minute=0):
                return datetime(2026, 9, 3, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
            visits = tuple(PurposeVisitIntent(date=date(2026,9,3), candidate_handle=handle,
                visit_kind=kind, is_backup=False, interval=TimeInterval(start_at=start, end_at=end))
                for handle,kind,start,end in (("candidate-handle-0000",VisitKind.ATTRACTION,at(10,7),at(11,22)),
                    ("candidate-handle-0006",VisitKind.LUNCH,at(11,37),at(12,42)),
                    ("candidate-handle-0007",VisitKind.DINNER,at(17,43),at(18,48))))
            rests = (PlannedBreak(break_id="afternoon-rest",kind="rest",start_at=at(14),end_at=at(16),reason="午后休息"),)
            return self.batch(("plan_directions", PlanDirectionsArgs(
                daily_outline=() if boundary else (DailyPlanOutline(date=date(2026,9,3),visits=visits,planned_breaks=rests),),
                legs=tuple(DirectionLegIntent(leg_id=f"leg{i}", origin_handle=self.endpoints[i][0],
                    destination_handle=self.endpoints[i][1], transport_semantic=TransportSemantic.WALKING,
                    route_mode=TravelMode.WALKING, alternatives_limit=1) for i in indexes),
                visit_intents=() if boundary else visits)))
        if self.stage == 3:
            self.stage = 4
            return self.batch(("submit_itinerary", self.proposal(self.feedback)))
        from app.llm_runtime import ToolTurnCompleted
        from tests.unit.itinerary_planning.test_service_initial import _metadata
        self.call_count += 1
        return ToolTurnCompleted(final_text="完成", metadata=_metadata())

    def proposal(self, response):
        from datetime import date, datetime, timedelta
        from zoneinfo import ZoneInfo
        from app.itinerary_planning.models import (ProposedRouteLeg, ProposedScheduledVisit, VisitKind, BusinessHoursState,
            TransportSemantic, ProposedItineraryDraft, ProposedBoundaryTransfer, ProposedDayPlan, PlanningDayKind,
            TimeInterval, SubmitItineraryArgs, ProposedEffectivePlanningPolicy, PlannedBreak)
        from app.baidu_map import TravelMode
        from app.requirements import TravelPace
        def at(day, hour, minute=0):
            return datetime(2026, 9, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
        found = {}
        def walk(value):
            if isinstance(value, dict):
                if "leg_id" in value and "directions_handle" in value:
                    found[value["leg_id"]] = value["directions_handle"]
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(response)
        starts = (at(2,18,31), at(3,10,3) - timedelta(minutes=self.first_route_minutes),
            at(3,11,23), at(3,13,3), at(3,18,49), at(4,14,37))
        routes = tuple(ProposedRouteLeg(leg_id=f"leg{i}", origin_handle=a, destination_handle=b,
            start_at=start, end_at=None, transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
            directions_handle=found[f"leg{i}"], alternative_index=0) for i, ((a,b),start) in enumerate(zip(self.endpoints,starts,strict=True)))
        visits = tuple(ProposedScheduledVisit(visit_id=f"visit{i}", candidate_handle=f"candidate-handle-{ordinal:04d}",
            visit_kind=kind, start_at=start, end_at=end, duration_source="planning_estimate", must_group_ids=(),
            time_sensitive_anchor_ids=(), business_hours_state=BusinessHoursState.COMPATIBLE if self.known_hours else BusinessHoursState.UNKNOWN,
            business_evidence_handles=(f"evidence-handle-{(8,14,15)[i]:04d}",) if self.known_hours else ())
            for i, (ordinal,kind,start,end) in enumerate(((0,VisitKind.ATTRACTION,at(3,10,7),at(3,11,22)),
                (6,VisitKind.LUNCH,at(3,11,37),at(3,12,42)),(7,VisitKind.DINNER,at(3,17,43),at(3,18,48)))))
        return SubmitItineraryArgs(effective_policy=ProposedEffectivePlanningPolicy(effective_pace=TravelPace.RELAXED,
            daily_time_rules=(), time_sensitive_must=(), transport_rules=()), draft=ProposedItineraryDraft(
                arrival_transfer=ProposedBoundaryTransfer(transfer_kind="arrival", hub_handle="hub-location-arrival", hotel_handle="hotel-handle", route_leg=routes[0]),
                days=(ProposedDayPlan(date=date(2026,9,3),day_kind=PlanningDayKind.FULL,theme="杭州漫游",
                    open_intervals=(TimeInterval(start_at=at(3,9),end_at=at(3,21)),),visits=visits,route_legs=routes[1:5],backups=(),
                    planned_breaks=(PlannedBreak(break_id="afternoon-rest",kind="rest",start_at=at(3,14),end_at=at(3,16),reason="午后休息"),)),),
                departure_transfer=ProposedBoundaryTransfer(transfer_kind="departure", hub_handle="hub-location-departure", hotel_handle="hotel-handle", route_leg=routes[5]),upstream_gap_ids=()))


from contextlib import asynccontextmanager


@asynccontextmanager
async def full_runtime(settings):
    from app.demo_workflow import create_demo_workflow_definition
    from app.artifact_persistence import create_artifact_persistence_runtime, load_artifact_persistence_settings
    from app.durable_execution import create_durable_execution_runtime
    dependencies, turn, providers = await full_dependencies()
    definition = create_demo_workflow_definition(dependencies=dependencies)
    artifacts = await create_artifact_persistence_runtime(settings=load_artifact_persistence_settings(),
        registry=ArtifactSchemaRegistry(HOTEL_ARTIFACT_REGISTRATIONS + CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
            + ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS + RESULT_FORMATTING_ARTIFACT_REGISTRATIONS + DEMO_ARTIFACT_REGISTRATIONS))
    class Sink:
        def __init__(self):
            self.tokens = {}
        async def store(self, *, run_id, pause_generation, contract_digest, pause_token):
            self.tokens[(run_id, pause_generation)] = pause_token
    sink = Sink()
    runtime = None
    try:
        runtime = await create_durable_execution_runtime(settings=settings, artifact_repository=artifacts.repository,
            contract=definition.contract, command_codec=definition.command_codec, budget_plan_provider=definition.budget_plan_provider,
            graph_factory=definition.graph_factory, pause_credential_sink=sink, cancellation_state_factory=definition.cancellation_state_factory)
        yield SimpleNamespace(runtime=runtime, definition=definition, dependencies=dependencies, turn=turn,
            providers=providers, artifacts=artifacts, sink=sink)
    finally:
        if runtime is not None:
            await runtime.close()
        await definition.aclose()
        await artifacts.close()


async def start_to_pause(context, *, hotel=False):
    from uuid import uuid4
    from app.demo_workflow import DemoStartCommand, DemoRequirementCommand
    from app.requirements import RequirementConfirmation
    from app.requirement_subgraph import RequirementConfirmationResume
    from app.durable_execution import DurableExecutionPaused
    run_id, trace_id = uuid4(), uuid4()
    diagnostic = context.dependencies.diagnostics
    diagnostic.start_run(run_id=run_id, trace_id=trace_id)
    with diagnostic.operation(run_id=run_id, trace_id=trace_id, turn_id=context.turn.turn_id, kind="initial"):
        paused = await context.runtime.start(run_id=run_id, trace_id=trace_id,
            command=DemoStartCommand(schema_version="1.0",kind="start",turn=context.turn))
    assert type(paused) is DurableExecutionPaused
    if hotel:
        with diagnostic.operation(run_id=run_id, trace_id=trace_id, turn_id=None, kind="resume"):
            paused = await context.runtime.resume(run_id=run_id,pause_token=paused.pause.pause_token,
                command=DemoRequirementCommand(schema_version="1.0",kind="requirement",map_requested=False,
                    resume=RequirementConfirmationResume(action=RequirementConfirmation(decision="accept",revision_text=None))))
        assert type(paused) is DurableExecutionPaused
    return paused


async def select_hotel(context, paused):
    from app.demo_workflow import DemoHotelCommand
    from app.demo_workflow.artifacts import DemoControlArtifact
    from app.hotel_selection import HotelSelectCommand
    ref = max((r for r in paused.state.artifact_refs if r.artifact_type == "demo.control"),key=lambda r:r.operation_id.logical_step)
    control = (await context.artifacts.repository.get(ref,expected_model=DemoControlArtifact)).content
    with context.dependencies.diagnostics.operation(run_id=paused.state.run_id,trace_id=paused.state.trace_id,turn_id=None,kind="resume"):
        return await context.runtime.resume(run_id=paused.state.run_id,pause_token=paused.pause.pause_token,
            command=DemoHotelCommand(schema_version="1.0",kind="hotel",candidate_set_ref=control.hotel_candidate_ref,action=HotelSelectCommand(selected_uid="h1")))
