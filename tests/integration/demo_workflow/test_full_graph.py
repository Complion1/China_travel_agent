"""Stage T03/T04 graph; never exported as the production demo factory."""
from types import SimpleNamespace

import pytest
from langgraph.graph import START, StateGraph

from app.artifact_persistence import create_artifact_persistence_runtime, load_artifact_persistence_settings
from app.demo_workflow import DemoStartCommand, DemoRequirementCommand, DemoHotelCommand, DemoCommandCodec, DemoBudgetPlanProvider, DEMO_ARTIFACT_REGISTRATIONS
from app.demo_workflow.artifacts import DemoControlArtifact
from app.demo_workflow.commands import canonical, digest
from app.demo_workflow.graph import requirement_node
from app.demo_workflow.nodes import Nodes
from app.demo_workflow.terminal import make_cancelled_state
from app.durable_execution import (
    DurableExecutionContract, DurableExecutionNodeRecoveryRule, NodeRecoveryClass,
    DurableExecutionPaused, DurableExecutionTerminal, create_durable_execution_runtime,
)
from app.hotel_selection import HOTEL_ARTIFACT_REGISTRATIONS, HotelCandidateSelectionService, HotelDeclineCommand, HotelSelectCommand
from app.requirement_subgraph import RequirementConfirmationResume
from app.requirements import RequirementConfirmation, RequirementDomainTransition, NeedConfirmation
from app.workflow_contracts import ArtifactSchemaRegistry, TravelSessionState, WorkflowPhase, DeliveryStatus
from tests.requirement_subgraph_fakes import make_initial_state, make_turn, ScriptedRequirementDomainService
from tests.support.demo_workflow_fakes import diagnostics
from tests.support.hotel_selection_fakes import ScriptedBaiduMapAdapter, map_success, place, FixedClock
from tests.unit.result_formatting.fakes import make_confirmed


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_stage_real_pg_requirement_to_hotel_and_decline(durable_execution_settings, invalid):
    initial = make_initial_state()
    confirmed = await make_confirmed()
    waiting_state = confirmed.state.model_copy(update={"confirmation_status": "awaiting_confirmation"})
    waiting = RequirementDomainTransition(state=waiting_state, outcome=NeedConfirmation(
        requirement=waiting_state.requirement, metadata=waiting_state.metadata, summary="确认测试行程"))
    domain = ScriptedRequirementDomainService(process_results=(waiting,), confirm_results=(confirmed,))
    diagnostic = diagnostics()
    adapter = ScriptedBaiduMapAdapter([map_success(place("h1"), place("h2", index=1), place("h3", index=2))])
    deps = SimpleNamespace(domain_service=domain, diagnostics=diagnostic,
        hotel_factory=lambda cap: HotelCandidateSelectionService(adapter, cap.artifact_repository, cap.execution_budget_manager, FixedClock()))
    codec = DemoCommandCodec(max_payload_bytes=1048576)
    names = {
        "requirements": (WorkflowPhase.REQUIREMENTS, NodeRecoveryClass.NON_RECOVERABLE),
        **{f"requirements/{name}": (WorkflowPhase.REQUIREMENTS, NodeRecoveryClass.NON_RECOVERABLE)
           for name in ("process_initial_turn", "await_clarification", "await_confirmation")},
        "after_requirements": (WorkflowPhase.REQUIREMENTS, NodeRecoveryClass.REPLAY_SAFE),
        "prepare_hotel": (WorkflowPhase.HOTEL_SELECTION, NodeRecoveryClass.REPLAY_SAFE),
        "hotel_discover": (WorkflowPhase.HOTEL_SELECTION, NodeRecoveryClass.NON_RECOVERABLE),
        "hotel_wait": (WorkflowPhase.HOTEL_SELECTION, NodeRecoveryClass.REPLAY_SAFE),
        "hotel_select": (WorkflowPhase.HOTEL_SELECTION, NodeRecoveryClass.NON_RECOVERABLE),
        "terminal": (WorkflowPhase.OUTPUT_FORMATTING, NodeRecoveryClass.REPLAY_SAFE),
    }
    rules = tuple(DurableExecutionNodeRecoveryRule(node_name=name, phase=phase, recovery_class=recovery)
        for name, (phase, recovery) in sorted(names.items()))
    payload = dict(schema_version="1.0", contract_id="test.demo.hotel.v1", command_codec=codec.descriptor.model_dump(mode="json"),
        budgeted_phases=["requirements", "hotel_selection"], node_rules=[r.model_dump(mode="json") for r in rules])
    contract = DurableExecutionContract.model_validate_json(canonical(payload | {"contract_digest": digest(canonical(payload))}), strict=True)
    services = {}
    def build(caps):
        nodes = Nodes(deps, caps, services)
        graph = StateGraph(TravelSessionState)
        graph.add_node("requirements", requirement_node(deps, caps))
        graph.add_node("after_requirements", nodes.after_requirements)
        graph.add_node("prepare_hotel", nodes.prepare(WorkflowPhase.HOTEL_SELECTION, "hotel_discover"))
        for name in ("hotel_discover", "hotel_wait", "hotel_select", "terminal"):
            graph.add_node(name, getattr(nodes, name))
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "after_requirements")
        return graph
    artifacts = await create_artifact_persistence_runtime(settings=load_artifact_persistence_settings(),
        registry=ArtifactSchemaRegistry(HOTEL_ARTIFACT_REGISTRATIONS + DEMO_ARTIFACT_REGISTRATIONS))
    tokens = {}
    class Sink:
        async def store(self, *, run_id, pause_generation, contract_digest, pause_token):
            tokens[pause_generation] = pause_token
    runtime = None
    try:
        runtime = await create_durable_execution_runtime(settings=durable_execution_settings, artifact_repository=artifacts.repository,
            contract=contract, command_codec=codec, budget_plan_provider=DemoBudgetPlanProvider(), graph_factory=build,
            pause_credential_sink=Sink(), cancellation_state_factory=make_cancelled_state)
        diagnostic.start_run(run_id=initial.run_id, trace_id=initial.trace_id)
        turn = make_turn(initial)
        with diagnostic.operation(run_id=initial.run_id, trace_id=initial.trace_id, turn_id=turn.turn_id, kind="initial"):
            result = await runtime.start(run_id=initial.run_id, trace_id=initial.trace_id,
                command=DemoStartCommand(schema_version="1.0", kind="start", turn=turn))
        assert type(result) is DurableExecutionPaused
        with diagnostic.operation(run_id=initial.run_id, trace_id=initial.trace_id, turn_id=None, kind="resume"):
            result = await runtime.resume(run_id=initial.run_id, pause_token=result.pause.pause_token,
                command=DemoRequirementCommand(schema_version="1.0", kind="requirement", map_requested=False,
                    resume=RequirementConfirmationResume(action=RequirementConfirmation(decision="accept", revision_text=None))))
        assert type(result) is DurableExecutionPaused and result.state.phase is WorkflowPhase.HOTEL_SELECTION
        ref = next(ref for ref in result.state.artifact_refs if ref.artifact_type == "demo.control")
        control = (await artifacts.repository.get(ref, expected_model=DemoControlArtifact)).content
        rounds = 3 if invalid else 1
        for number in range(rounds):
            with diagnostic.operation(run_id=initial.run_id, trace_id=initial.trace_id, turn_id=None, kind="resume"):
                result = await runtime.resume(run_id=initial.run_id, pause_token=result.pause.pause_token,
                    command=DemoHotelCommand(schema_version="1.0", kind="hotel", candidate_set_ref=control.hotel_candidate_ref,
                        action=HotelSelectCommand(selected_uid="invalid") if invalid else HotelDeclineCommand()))
        assert type(result) is DurableExecutionTerminal
        assert result.state.delivery_status is (DeliveryStatus.FAILED if invalid else DeliveryStatus.CANCELLED)
        assert adapter.search_call_count == 1
        budgets = await runtime.get_terminal_budget_snapshots(run_id=initial.run_id)
        summary = await diagnostic.finish(state=result.state, budget_snapshots=budgets)
        assert next(c.value for c in summary.summary.counters if c.name == "workflow.resumed") == rounds + 1
        assert len(tokens) == rounds + 1
    finally:
        if runtime is not None:
            await runtime.close()
        await artifacts.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["model", "map"])
async def test_research_technical_failure_stops_before_planning(durable_execution_settings,failure):
    from tests.support.demo_workflow_fakes import full_runtime,start_to_pause,select_hotel
    from app.llm_runtime import LLMInvocationFailure, LLMRuntimeError, LLMErrorCode, ModelRole
    from app.baidu_map import BaiduMapTimeoutError, MapCapability
    async with full_runtime(durable_execution_settings) as context:
        if failure == "model":
            context.providers.research_model.responses[0] = LLMInvocationFailure(error=LLMRuntimeError(
                code=LLMErrorCode.TIMEOUT,role=ModelRole.CANDIDATE_RESEARCH,retryable=True,possible_duplicate=True))
        else:
            context.providers.research_map.responses[0] = BaiduMapTimeoutError(capability=MapCapability.PLACE_SEARCH)
        paused = await start_to_pause(context,hotel=True)
        result = await select_hotel(context,paused)
        assert type(result) is DurableExecutionTerminal
        assert result.state.terminal_code == ("workflow_provider_failed" if failure == "model" else "workflow_map_failed")
        assert result.state.delivery_status is DeliveryStatus.FAILED
        assert context.providers.research_model.call_count == 1
        assert context.providers.planning_model.call_count == 0
        assert not any(r.artifact_type.startswith("result_formatting.") for r in result.state.artifact_refs)


@pytest.mark.asyncio
@pytest.mark.parametrize("known_hours,map_requested,supplement", [(True,False,False),(False,False,False),(True,True,False),(True,False,True)])
async def test_production_graph_real_services_and_postgres(durable_execution_settings,known_hours,map_requested,supplement):
    from uuid import uuid4
    from app.demo_workflow import create_demo_workflow_definition
    from app.candidate_research import CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
    from app.itinerary_planning import ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS
    from app.result_formatting import RESULT_FORMATTING_ARTIFACT_REGISTRATIONS
    from tests.support.demo_workflow_fakes import full_dependencies
    deps, turn, providers = await full_dependencies()
    providers.planning_model.known_hours = known_hours
    providers.planning_model.supplement = supplement
    definition = create_demo_workflow_definition(dependencies=deps)
    artifacts = await create_artifact_persistence_runtime(settings=load_artifact_persistence_settings(),
        registry=ArtifactSchemaRegistry(HOTEL_ARTIFACT_REGISTRATIONS + CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
            + ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS + RESULT_FORMATTING_ARTIFACT_REGISTRATIONS + DEMO_ARTIFACT_REGISTRATIONS))
    tokens = {}
    class Sink:
        async def store(self, *, run_id, pause_generation, contract_digest, pause_token):
            tokens[pause_generation] = pause_token
    runtime = None
    run_id, trace_id = uuid4(), uuid4()
    try:
        runtime = await create_durable_execution_runtime(settings=durable_execution_settings, artifact_repository=artifacts.repository,
            contract=definition.contract, command_codec=definition.command_codec, budget_plan_provider=definition.budget_plan_provider,
            graph_factory=definition.graph_factory, pause_credential_sink=Sink(), cancellation_state_factory=definition.cancellation_state_factory)
        deps.diagnostics.start_run(run_id=run_id, trace_id=trace_id)
        with deps.diagnostics.operation(run_id=run_id, trace_id=trace_id, turn_id=turn.turn_id, kind="initial"):
            result = await runtime.start(run_id=run_id, trace_id=trace_id,
                command=DemoStartCommand(schema_version="1.0",kind="start",turn=turn))
        assert type(result) is DurableExecutionPaused, result
        with deps.diagnostics.operation(run_id=run_id, trace_id=trace_id, turn_id=None, kind="resume"):
            result = await runtime.resume(run_id=run_id,pause_token=result.pause.pause_token,
                command=DemoRequirementCommand(schema_version="1.0",kind="requirement",map_requested=map_requested,
                    resume=RequirementConfirmationResume(action=RequirementConfirmation(decision="accept",revision_text=None))))
        assert type(result) is DurableExecutionPaused and result.state.phase is WorkflowPhase.HOTEL_SELECTION, result
        control_ref = next(r for r in result.state.artifact_refs if r.artifact_type == "demo.control")
        control = (await artifacts.repository.get(control_ref,expected_model=DemoControlArtifact)).content
        with deps.diagnostics.operation(run_id=run_id, trace_id=trace_id, turn_id=None, kind="resume"):
            result = await runtime.resume(run_id=run_id,pause_token=result.pause.pause_token,
                command=DemoHotelCommand(schema_version="1.0",kind="hotel",candidate_set_ref=control.hotel_candidate_ref,
                    action=HotelSelectCommand(selected_uid="h1")))
        assert type(result) is DurableExecutionTerminal, result
        assert result.state.delivery_status in {DeliveryStatus.DEMO_READY, DeliveryStatus.DEGRADED}, (result.state.terminal_code, providers.research_model.call_count, providers.planning_model.feedback)
        assert result.state.delivery_status is (DeliveryStatus.DEMO_READY if known_hours and not map_requested else DeliveryStatus.DEGRADED)
        assert len([r for r in result.state.artifact_refs if r.artifact_type == "result_formatting.index"]) == 1
        assert len(providers.extractor.calls) == providers.hotel_map.search_call_count == 1
        assert providers.research_model.call_count > 0 and providers.planning_model.call_count > 0
        budgets = await runtime.get_terminal_budget_snapshots(run_id=run_id)
        summary = await deps.diagnostics.finish(state=result.state,budget_snapshots=budgets)
        assert summary.summary is not None
        await definition.release_run(run_id)
        assert providers.research_model.closed and providers.planning_model.closed
        await runtime.close()
        from tests.support.demo_workflow_worker import invoke_worker
        loaded = await invoke_worker(dict(run_id=run_id,mode="read_result"))
        assert TravelSessionState.model_validate_json(loaded["state"],strict=True) == result.state
        assert all(loaded[key] == 0 for key in ("extract_calls","hotel_calls","research_calls","planning_calls"))
    finally:
        await definition.aclose()
        if runtime is not None:
            await runtime.close()
        await artifacts.close()
