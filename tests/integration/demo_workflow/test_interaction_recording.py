"""G-DEMO: production M01–M13, PostgreSQL and the real SDK over offline HTTP."""
import json
from dataclasses import replace
from datetime import date
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

import app.llm_runtime.provider as provider
from app.artifact_persistence import create_artifact_persistence_runtime, load_artifact_persistence_settings
from app.candidate_research import CandidateResearchService
from app.demo_workflow import (create_demo_workflow_definition, DemoStartCommand, DemoRequirementCommand,
    DemoHotelCommand, DEMO_ARTIFACT_REGISTRATIONS)
from app.demo_workflow.artifacts import DemoControlArtifact
from app.durable_execution import create_durable_execution_runtime, DurableExecutionPaused, DurableExecutionTerminal
from app.itinerary_planning import ItineraryPlanningService
from app.llm_runtime import DeepSeekRuntime, PromptEnvelope, ToolResult
from app.llm_runtime.settings import DeepSeekSettings
from app.requirement_extraction import DeepSeekRequirementExtractor
from app.requirement_subgraph import RequirementConfirmationResume
from app.requirements import RequirementDomainService, RequirementConfirmation
from app.hotel_selection import HotelSelectCommand, HOTEL_ARTIFACT_REGISTRATIONS
from app.candidate_research import CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
from app.itinerary_planning import ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS
from app.result_formatting import RESULT_FORMATTING_ARTIFACT_REGISTRATIONS
from app.workflow_contracts import ArtifactSchemaRegistry, DeliveryStatus
from scripts.export_interactions import summarize, write_export
from tests.support.demo_workflow_fakes import full_dependencies
from tests.unit.requirements.fakes import InMemoryEvidenceSink, FixedClock
from tests.unit.llm_runtime.fakes import RecordingSleeper
from tests.unit.llm_runtime.test_raw_interaction_capture import response


@pytest.mark.asyncio
@pytest.mark.parametrize('supplement,days,repair', [(False,1,False), (True,1,False), (False,3,False), (False,1,True)])
async def test_production_root_sdk_capture_postgres_export(durable_execution_settings, monkeypatch, tmp_path, supplement, days, repair):
    deps, turn, boundaries = await full_dependencies()
    patch = boundaries.extractor._responses[0]
    if days == 3:
        patch, turn = multiday_boundaries(boundaries, patch, turn)
    boundaries.planning_model.supplement = supplement
    extracted = False
    rejected_proposal = None
    fixture_errors = []
    original_sdk = provider.ChatDeepSeek

    async def handler(request):
        nonlocal extracted, rejected_proposal
        wire = json.loads(request.content)
        messages = wire['messages']
        if 'tools' in wire:
            tool_messages = [item for item in messages if item['role'] == 'tool']
            if rejected_proposal is not None:
                feedback = json.loads(next(item['content'] for item in tool_messages
                    if item['tool_call_id'] in boundaries.planning_model._pending_ids))
                assert feedback['tool_result']['reason'] == 'proposal_structure_or_reference_invalid'
                output = boundaries.planning_model.batch(('submit_itinerary', rejected_proposal))
                rejected_proposal = None
            elif boundaries.planning_model.call_count == 0:
                output = await boundaries.planning_model.ainvoke_tools(PromptEnvelope(
                    system_text=messages[0]['content'], user_text=messages[-1]['content']), ())
            else:
                pending = boundaries.planning_model._pending_ids
                feedback = tuple(ToolResult(call_id=item['tool_call_id'], content=item['content'])
                    for item in tool_messages if item['tool_call_id'] in pending)
                output = await boundaries.planning_model.acontinue_tools(feedback)
                if repair and output.calls[0].tool_name == 'submit_itinerary':
                    rejected_proposal = output.calls[0].arguments
                    day = rejected_proposal.draft.days[0]
                    first = day.visits[0]
                    invalid = rejected_proposal.model_copy(update={'draft': rejected_proposal.draft.model_copy(update={
                        'days': (day.model_copy(update={'visits': (first.model_copy(update={
                            'start_at': first.start_at.replace(hour=9)}), *day.visits[1:])}),)})})
                    output = output.model_copy(update={'calls': (output.calls[0].model_copy(update={'arguments': invalid}),)})
            calls = output.calls
            boundaries.planning_model._pending_ids = {item.call_id for item in calls}
            tools = [dict(id=item.call_id, type='function', function=dict(name=item.tool_name,
                arguments=item.arguments.model_dump_json())) for item in calls]
            raw = response(content=None, finish='tool_calls', tools=tools)
        elif not extracted:
            extracted = True
            raw = response(content=patch.model_dump_json())
        else:
            output = await boundaries.research_model.ainvoke_structured(PromptEnvelope(
                system_text=messages[0]['content'], user_text=messages[-1]['content']), object)
            tree = output.value.model_dump(mode='json')
            if tree.get('outcome_type') in {'proposed_actions', 'no_useful_action'}:
                tree = {'output': tree}
            raw = response(content=json.dumps(tree, ensure_ascii=False))
        return httpx.Response(200, content=raw, headers={'content-type': 'application/json'})

    async def checked_handler(request):
        try:
            return await handler(request)
        except Exception as error:
            fixture_errors.append((type(error).__name__, str(error)))
            raise

    def sdk_factory(**kwargs):
        model = original_sdk(**kwargs)
        model.root_async_client._client._transport = httpx.MockTransport(checked_handler)
        model.root_async_client._client._mounts = {}
        return model
    monkeypatch.setattr(provider, 'ChatDeepSeek', sdk_factory)
    llm = DeepSeekRuntime(provider.LangChainDeepSeekAdapter(DeepSeekSettings(api_key=SecretStr('OFFLINE_API_SENTINEL'))), RecordingSleeper())
    artifacts = await create_artifact_persistence_runtime(settings=load_artifact_persistence_settings(),
        registry=ArtifactSchemaRegistry(HOTEL_ARTIFACT_REGISTRATIONS + CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
            + ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS + RESULT_FORMATTING_ARTIFACT_REGISTRATIONS + DEMO_ARTIFACT_REGISTRATIONS))
    deps = replace(deps, domain_service=RequirementDomainService(extractor=DeepSeekRequirementExtractor(
        runtime=llm, invocation_sink=deps.diagnostics), evidence_sink=InMemoryEvidenceSink(), clock=FixedClock(date(2026,8,20))),
        research_factory=lambda cap: CandidateResearchService(runtime=llm, map_adapter=boundaries.research_map,
            artifact_repository=cap.artifact_repository, budget_manager=cap.execution_budget_manager),
        planning_factory=lambda cap: ItineraryPlanningService(runtime=llm, map_adapter=boundaries.planning_map,
            artifact_repository=cap.artifact_repository, budget_manager=cap.execution_budget_manager),
        interaction_recorder=artifacts.interaction_recorder)
    definition = create_demo_workflow_definition(dependencies=deps)
    class Sink:
        async def store(self, **kwargs):
            pass
    runtime = None
    run_id, trace_id = uuid4(), uuid4()
    try:
        runtime = await create_durable_execution_runtime(settings=durable_execution_settings,
            artifact_repository=artifacts.repository, contract=definition.contract, command_codec=definition.command_codec,
            budget_plan_provider=definition.budget_plan_provider, graph_factory=definition.graph_factory,
            pause_credential_sink=Sink(), cancellation_state_factory=definition.cancellation_state_factory)
        diagnostic = deps.diagnostics
        diagnostic.start_run(run_id=run_id, trace_id=trace_id)
        with diagnostic.operation(run_id=run_id, trace_id=trace_id, turn_id=turn.turn_id, kind='initial'):
            paused = await runtime.start(run_id=run_id, trace_id=trace_id, command=DemoStartCommand(schema_version='1.0',kind='start',turn=turn))
        assert isinstance(paused, DurableExecutionPaused)
        assert not any(item.record_kind == 'run_summary' for item in await artifacts.interaction_recorder.read_run(run_id))
        with diagnostic.operation(run_id=run_id, trace_id=trace_id, turn_id=None, kind='resume'):
            paused = await runtime.resume(run_id=run_id, pause_token=paused.pause.pause_token,
                command=DemoRequirementCommand(schema_version='1.0',kind='requirement',map_requested=False,
                    resume=RequirementConfirmationResume(action=RequirementConfirmation(decision='accept', revision_text=None))))
        assert isinstance(paused, DurableExecutionPaused)
        ref = next(item for item in paused.state.artifact_refs if item.artifact_type == 'demo.control')
        control = (await artifacts.repository.get(ref, expected_model=DemoControlArtifact)).content
        with diagnostic.operation(run_id=run_id, trace_id=trace_id, turn_id=None, kind='resume'):
            result = await runtime.resume(run_id=run_id, pause_token=paused.pause.pause_token,
                command=DemoHotelCommand(schema_version='1.0',kind='hotel',candidate_set_ref=control.hotel_candidate_ref,
                    action=HotelSelectCommand(selected_uid='h1')))
        assert isinstance(result, DurableExecutionTerminal)
        assert not fixture_errors, fixture_errors
        assert result.state.delivery_status is (DeliveryStatus.DEGRADED if days == 3 else DeliveryStatus.DEMO_READY), result.state.terminal_code
        index_ref = next(item for item in result.state.artifact_refs if item.artifact_type == 'result_formatting.index')
        from app.result_formatting import ResultIndexArtifact
        assert len((await artifacts.repository.get(index_ref, expected_model=ResultIndexArtifact)).content.days) == days
        await diagnostic.finish(state=result.state, budget_snapshots=await runtime.get_terminal_budget_snapshots(run_id=run_id))
        records = await artifacts.interaction_recorder.read_run(run_id)
        assert {'extract','research_action','research_judgment','planning','run'} <= {item.purpose for item in records}
        assert summarize(records)['records_complete']
        assert all('OFFLINE_API_SENTINEL' not in item.model_dump_json() for item in records)
        assert any(item.record_kind == 'tool_result' and item.payload.tool_name == 'submit_itinerary' for item in records)
        if supplement:
            assert any(item.record_kind == 'tool_result' and item.payload.tool_name == 'request_candidate_supplement' for item in records)
        if repair:
            submits = [item for item in records if item.record_kind == 'tool_result' and item.payload.tool_name == 'submit_itinerary']
            assert [item.payload.outcome for item in submits] == ['rejected', 'success']
        if days == 3:
            from app.result_formatting import ResultFormattingService, ResultReadRequest, ResultFormattingLoaded
            loaded = await ResultFormattingService(artifact_repository=artifacts.repository).load(
                ResultReadRequest(schema_version='2.0', state=result.state, index_ref=index_ref))
            assert isinstance(loaded, ResultFormattingLoaded)
            assert '地铁1号线' in loaded.markdown and '换乘地铁2号线' in loaded.markdown
        write_export(run_id, tmp_path / str(run_id), records)
        await artifacts.close()
        artifacts = await create_artifact_persistence_runtime(settings=load_artifact_persistence_settings(), registry=ArtifactSchemaRegistry(()))
        assert await artifacts.interaction_recorder.read_run(run_id) == records
    finally:
        if runtime is not None:
            await runtime.close()
        await definition.aclose()
        await artifacts.close()
        await llm.aclose()


def multiday_boundaries(boundaries, patch, turn):
    """Larger typed supplier proposals; every service and business Gate stays real."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from app.requirements.extraction_contract import ATTR_TO_PATH, StrictRequirementPatchDTO
    from app.candidate_research.models import (RoleJudgmentUpdate, RoleVerdict, RoleReasonCode, ResearchRole,
        EvidenceHandle, MealJudgmentUpdate, MealIntent, MatchVerdict, MealReasonCode, JudgmentPatch)
    from tests.support.candidate_research_fakes import structured
    from tests.unit.candidate_research.test_service_initial import _multi_place_search, _matching_details
    from app.itinerary_planning.models import (PlanDirectionsArgs, DirectionLegIntent, TransportSemantic,
        ProposedScheduledVisit, VisitKind, BusinessHoursState, ProposedDayPlan, PlanningDayKind, TimeInterval)
    from app.baidu_map import TravelMode
    payload = patch.model_dump(mode='json')
    payload.update(sightseeing_end_date='2026-09-05', sightseeing_days=3, departure_datetime='2026-09-06 18:00')
    spans = {key: f'{key}: {json.dumps(payload[key], ensure_ascii=False)}' for key in ATTR_TO_PATH if payload[key] is not None}
    payload['evidence'] = [dict(field_path=ATTR_TO_PATH[key], text=text) for key,text in spans.items()]
    patch = StrictRequirementPatchDTO.model_validate_json(json.dumps(payload), strict=True)
    turn = turn.model_copy(update={'user_message': '\n'.join(spans.values())})
    research = boundaries.research_model
    old_patch = research.responses[1].value
    roles = tuple(RoleJudgmentUpdate(candidate_handle=f'c:{i}', role=ResearchRole.ATTRACTION if i<18 else ResearchRole.RESTAURANT,
        verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS,
        evidence_handles=(EvidenceHandle(handle_id='e:0' if i<18 else 'e:1'),)) for i in range(30))
    meals = tuple(MealJudgmentUpdate(candidate_handle=f'c:{18+i}', meal_intent=MealIntent.LUNCH if i%2==0 else MealIntent.DINNER,
        verdict=MatchVerdict.CONFIRMED, reason_code=MealReasonCode.TARGETED_MEAL_SEARCH,
        evidence_handles=(EvidenceHandle(handle_id='e:1'),)) for i in range(12))
    research.responses[1] = structured(old_patch.model_copy(update={'role_updates':roles,'meal_updates':meals}))
    research.responses[4:] = [research.responses[2], structured(JudgmentPatch(role_updates=(),interest_updates=(),meal_updates=(),requirement_group_updates=()))]
    maps = boundaries.research_map
    maps.responses[:2] = [_multi_place_search(tuple(f'a{i}' for i in range(18)), category='museum'),
        _multi_place_search(tuple(f'r{i}' for i in range(12)), category='restaurant')]
    maps.responses[8:] = [_matching_details(f'a{i}',category='museum') for i in range(18)]
    model = boundaries.planning_model
    model.known_hours = False
    from tests.support.itinerary_planning_fakes import directions_success
    def transit_route(request):
        result = directions_success(duration_s=600)
        alternative = result.alternatives[0]
        template = alternative.steps[0]
        return result.model_copy(update={'alternatives': (alternative.model_copy(update={'steps': (
            template.model_copy(update={'instruction': '从A站乘坐地铁1号线至B站', 'duration_s':300}),
            template.model_copy(update={'instruction': '在B站换乘地铁2号线至C站', 'duration_s':300}),)}),)})
    boundaries.planning_map.directions_factory = transit_route
    original_continue = model.acontinue_tools
    async def continuation(results, **kwargs):
        if model.stage == 1:
            model.feedback.append([json.loads(item.content) for item in results])
            model.stage = 2
            pairs = [('hub-location-arrival','hotel-handle')]
            for day in range(3):
                handles = ('hotel-handle',f'candidate-handle-{day:04d}',f'candidate-handle-{18+day*2:04d}',
                    f'candidate-handle-{19+day*2:04d}','hotel-handle')
                pairs.extend(zip(handles, handles[1:]))
            pairs.append(('hotel-handle','hub-location-departure'))
            model.endpoints = tuple(pairs)
            return model.batch(('plan_directions', PlanDirectionsArgs(legs=tuple(DirectionLegIntent(leg_id=f'leg{i}',
                origin_handle=a,destination_handle=b,transport_semantic=TransportSemantic.PUBLIC_TRANSIT,route_mode=TravelMode.TRANSIT,
                alternatives_limit=1) for i,(a,b) in enumerate(pairs)))))
        return await original_continue(results, **kwargs)
    model.acontinue_tools = continuation
    original_proposal = model.proposal
    def proposal(feedback):
        base = original_proposal(feedback)
        found = {item['leg_id']: item['directions_handle'] for entry in feedback
            for item in entry['tool_result']['observation']['observations']}
        def at(day,hour,minute=0):
            return datetime(2026,9,day,hour,minute,tzinfo=ZoneInfo('Asia/Shanghai'))
        route_template = base.draft.arrival_transfer.route_leg
        routes, day_plans = [], []
        times = [(at(2,18,30),at(2,19))]
        for day in range(3,6):
            times.extend([(at(day,9,30),at(day,10)),(at(day,11),at(day,11,30)),(at(day,13),at(day,13,30)),(at(day,18,30),at(day,19))])
        times.append((at(6,14,30),at(6,15)))
        for i,((a,b),(start,end)) in enumerate(zip(model.endpoints,times,strict=True)):
            routes.append(route_template.model_copy(update={'leg_id':f'leg{i}','origin_handle':a,'destination_handle':b,
                'directions_handle':found[f'leg{i}'],'start_at':start,'end_at':end,
                'transport_semantic':TransportSemantic.PUBLIC_TRANSIT,'route_mode':TravelMode.TRANSIT}))
        for offset in range(3):
            day = offset+3
            visits = tuple(ProposedScheduledVisit(visit_id=f'd{day}v{i}',candidate_handle=f'candidate-handle-{ordinal:04d}',visit_kind=kind,
                start_at=start,end_at=end,duration_source='planning_estimate',must_group_ids=(),time_sensitive_anchor_ids=(),
                business_hours_state=BusinessHoursState.UNKNOWN,business_evidence_handles=()) for i,(ordinal,kind,start,end) in enumerate([
                (offset,VisitKind.ATTRACTION,at(day,10),at(day,11)),(18+offset*2,VisitKind.LUNCH,at(day,11,30),at(day,12,30)),
                (19+offset*2,VisitKind.DINNER,at(day,17,30),at(day,18,30))]))
            day_plans.append(ProposedDayPlan(date=date(2026,9,day),day_kind=PlanningDayKind.FULL,theme=f'第{offset+1}天',
                open_intervals=(TimeInterval(start_at=at(day,9),end_at=at(day,21)),),visits=visits,route_legs=tuple(routes[1+offset*4:5+offset*4]),backups=()))
        return base.model_copy(update={'draft':base.draft.model_copy(update={'days':tuple(day_plans),
            'arrival_transfer':base.draft.arrival_transfer.model_copy(update={'route_leg':routes[0]}),
            'departure_transfer':base.draft.departure_transfer.model_copy(update={'route_leg':routes[-1]})})})
    model.proposal = proposal
    return patch, turn
