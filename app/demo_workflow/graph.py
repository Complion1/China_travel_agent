"""Production graph composition over existing module capabilities."""
from uuid import UUID

from langchain_core.runnables import RunnableLambda

from app.requirement_subgraph import create_requirement_subgraph_embedded_driver

from .errors import DemoWorkflowUsageError
from .errors import DemoWorkflowConfigurationError


def requirement_node(dependencies, caps):
    driver = create_requirement_subgraph_embedded_driver(
        domain_service=dependencies.domain_service,
        execution_budget_manager_factory=caps.execution_budget_manager_factory(node_name="requirements"),
        limits=caps.requirement_subgraph_limits,
    )
    caps.register_requirement_subgraph(node_name="requirements", driver=driver)

    def accepted(state):
        # M11 has already verified and entered the accepted operation scope.
        dependencies.diagnostics._resume_accepted(state)
        return state

    return RunnableLambda(accepted) | driver.graph


class Services:
    def __init__(self):
        self.instances = {}

    async def release_run(self, run_id):
        if type(run_id) is not UUID:
            raise DemoWorkflowUsageError()
        for key in tuple(self.instances):
            if key[0] != run_id:
                continue
            if key[1] in {"research", "planning"}:
                try:
                    await self.instances[key].close_run(run_id)
                except Exception:
                    raise DemoWorkflowUsageError() from None
            del self.instances[key]

    async def aclose(self):
        for run_id in tuple(dict.fromkeys(key[0] for key in self.instances)):
            await self.release_run(run_id)


def create_demo_workflow_definition(*, dependencies):
    from langgraph.graph import START, StateGraph
    from app.durable_execution import DurableExecutionContract, DurableExecutionNodeRecoveryRule, NodeRecoveryClass
    from app.workflow_contracts import WorkflowPhase, TravelSessionState
    from .models import DemoWorkflowDependencies, DemoWorkflowDefinition
    from .commands import DemoCommandCodec, canonical, digest
    from .plans import DemoBudgetPlanProvider, BUDGETED_PHASES, research_plan, planning_plan
    from .nodes import Nodes
    from .publication import day_node, index_node
    from .terminal import make_cancelled_state

    if type(dependencies) is not DemoWorkflowDependencies:
        raise DemoWorkflowConfigurationError()
    dependencies.diagnostics._bind_interactions(dependencies.interaction_recorder)
    codec = DemoCommandCodec(max_payload_bytes=dependencies.durable_limits.max_payload_bytes)
    replay, unsafe, journal = NodeRecoveryClass.REPLAY_SAFE, NodeRecoveryClass.NON_RECOVERABLE, NodeRecoveryClass.JOURNAL_GUARDED
    req, hotel, research, planning, output = BUDGETED_PHASES
    specs = {
        "requirements": (req, unsafe), "after_requirements": (req, replay),
        "prepare_hotel": (hotel, replay), "hotel_discover": (hotel, unsafe), "hotel_wait": (hotel, replay), "hotel_select": (hotel, unsafe),
        "prepare_research": (research, replay), "research": (research, unsafe),
        "prepare_planning": (planning, replay), "planning": (planning, unsafe),
        "prepare_output": (output, replay), "publish_index": (output, journal), "terminal": (output, replay),
        **{f"requirements/{name}": (req, unsafe) for name in ("process_initial_turn", "await_clarification", "await_confirmation")},
        **{f"publish_day_{i}": (output, journal) for i in range(1, 61)},
    }
    rules = tuple(DurableExecutionNodeRecoveryRule(node_name=name, phase=phase, recovery_class=recovery)
        for name, (phase, recovery) in sorted(specs.items()))
    payload = dict(schema_version="1.0", contract_id="demo_workflow.web.v3",
        command_codec=codec.descriptor.model_dump(mode="json"), budgeted_phases=[phase.value for phase in BUDGETED_PHASES],
        node_rules=[rule.model_dump(mode="json") for rule in rules])
    contract = DurableExecutionContract.model_validate_json(canonical(payload | {"contract_digest": digest(canonical(payload))}), strict=True)
    # Structural bounds only. Provider content and actual database capacity remain runtime checks.
    limits = dependencies.durable_limits
    bound_id = UUID(int=1)
    plans = [research_plan(bound_id), *(planning_plan(bound_id, days) for days in (1, 60))]
    if max([len(canonical(payload)), *(len(canonical(plan.model_dump(mode="json"))) for plan in plans)]) > limits.max_payload_bytes:
        raise DemoWorkflowConfigurationError()
    if limits.max_managed_operations_per_run < 625 or dependencies.artifact_limits.max_artifacts < 61:
        raise DemoWorkflowConfigurationError()
    services = Services()
    def build(caps):
        nodes = Nodes(dependencies, caps, services.instances)
        graph = StateGraph(TravelSessionState)
        graph.add_node("requirements", requirement_node(dependencies, caps))
        for name in ("after_requirements", "hotel_discover", "hotel_wait", "hotel_select", "research", "planning", "terminal"):
            graph.add_node(name, getattr(nodes, name))
        for phase, destination in ((hotel, "hotel_discover"), (research, "research"), (planning, "planning"), (output, "publish_day_1")):
            name = {hotel: "prepare_hotel", research: "prepare_research", planning: "prepare_planning", output: "prepare_output"}[phase]
            graph.add_node(name, nodes.prepare(phase, destination))
        for day in range(1, 61):
            graph.add_node(f"publish_day_{day}", day_node(nodes, day))
        graph.add_node("publish_index", index_node(nodes))
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "after_requirements")
        return graph
    return DemoWorkflowDefinition(contract=contract, command_codec=codec, budget_plan_provider=DemoBudgetPlanProvider(),
        graph_factory=build, cancellation_state_factory=make_cancelled_state,
        release_run=services.release_run, aclose=services.aclose, durable_limits=dependencies.durable_limits, artifact_limits=dependencies.artifact_limits)
