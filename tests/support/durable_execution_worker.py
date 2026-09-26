"""跨进程恢复测试的独立 runtime worker。"""

from __future__ import annotations

import asyncio
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from app.durable_execution import DEMO_DURABLE_EXECUTION_LIMITS, create_durable_execution_runtime
from app.durable_execution.errors import DurableExecutionRunBusyError
from app.durable_execution.models import NodeRecoveryClass
from app.durable_execution.repository import DurableExecutionRepository
from app.durable_execution.settings import load_durable_execution_settings
from app.workflow_contracts import DeliveryStatus, TravelSessionState
from tests.requirement_subgraph_fakes import make_confirmed_transition
from tests.support.durable_execution_fakes import (
    FakeArtifactRepository,
    FakeBudgetPlanProvider,
    FakeCommandCodec,
    make_recovery_contract,
)


def make_demo_ready_state(state: TravelSessionState) -> TravelSessionState:
    transition = make_confirmed_transition(state)
    return TravelSessionState.model_validate(
        {
            "schema_version": state.schema_version,
            "run_id": state.run_id,
            "trace_id": state.trace_id,
            "phase": state.phase,
            "delivery_status": DeliveryStatus.DEMO_READY,
            "terminal_code": "workflow_completed",
            "terminal_outcome": None,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
            "artifact_refs": state.artifact_refs,
        },
        strict=True,
    )


def run_recovery_worker(run_id, barrier, graph_started, graph_release, effect_counter, result_queue) -> None:
    asyncio.run(_run_recovery_worker(
        run_id=run_id,
        barrier=barrier,
        graph_started=graph_started,
        graph_release=graph_release,
        effect_counter=effect_counter,
        result_queue=result_queue,
    ))


async def _run_recovery_worker(*, run_id, barrier, graph_started, graph_release, effect_counter, result_queue) -> None:
    original = DurableExecutionRepository.get_lease_state

    async def synchronized_lease_read(self, *, run_id):
        result = await original(self, run_id=run_id)
        barrier.wait(timeout=30)
        return result

    DurableExecutionRepository.get_lease_state = synchronized_lease_read
    runtime = None
    try:
        def build(_capabilities):
            graph = StateGraph(TravelSessionState)

            graph.add_node("safe", lambda state: state)

            async def finish(state: TravelSessionState) -> TravelSessionState:
                graph_started.set()
                if not graph_release.wait(30):
                    raise TimeoutError("recovery graph release event timed out")
                with effect_counter.get_lock():
                    effect_counter.value += 1
                return make_demo_ready_state(state)

            graph.add_node("finish", finish)
            graph.add_edge(START, "safe")
            graph.add_edge("safe", "finish")
            graph.add_edge("finish", END)
            return graph

        runtime = await create_durable_execution_runtime(
            settings=load_durable_execution_settings(),
            artifact_repository=FakeArtifactRepository(),
            contract=make_recovery_contract(nodes=(
                ("safe", NodeRecoveryClass.REPLAY_SAFE),
                ("finish", NodeRecoveryClass.REPLAY_SAFE),
            )),
            command_codec=FakeCommandCodec(),
            budget_plan_provider=FakeBudgetPlanProvider(),
            graph_factory=build,
            limits=DEMO_DURABLE_EXECUTION_LIMITS,
        )
        terminal = await runtime.recover(run_id=UUID(str(run_id)))
        result_queue.put(("terminal", terminal.state.delivery_status.value))
    except DurableExecutionRunBusyError:
        result_queue.put(("busy", None))
    except BaseException as error:
        result_queue.put(("error", type(error).__name__))
        raise
    finally:
        if runtime is not None:
            await runtime.close()
        DurableExecutionRepository.get_lease_state = original
