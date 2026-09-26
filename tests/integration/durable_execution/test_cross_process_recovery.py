from __future__ import annotations

import asyncio
import multiprocessing
from uuid import uuid4

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph

from app.durable_execution import (
    DEMO_DURABLE_EXECUTION_LIMITS,
    create_durable_execution_runtime,
)
from app.durable_execution.models import NodeRecoveryClass
from app.workflow_contracts import DeliveryStatus, TravelSessionState
from tests.support.durable_execution_fakes import (
    FakeArtifactRepository,
    FakeBudgetPlanProvider,
    FakeCommandCodec,
    make_recovery_contract,
)
from tests.support.durable_execution_worker import make_demo_ready_state, run_recovery_worker


@pytest.mark.asyncio
async def test_expired_lease_has_one_cross_process_recovery_winner(durable_execution_settings) -> None:
    finish_entered = asyncio.Event()
    finish_release = asyncio.Event()

    def build(_capabilities):
        graph = StateGraph(TravelSessionState)
        graph.add_node("safe", lambda state: state)

        async def finish(state: TravelSessionState) -> TravelSessionState:
            finish_entered.set()
            await finish_release.wait()
            return make_demo_ready_state(state)

        graph.add_node("finish", finish)
        graph.add_edge(START, "safe")
        graph.add_edge("safe", "finish")
        graph.add_edge("finish", END)
        return graph

    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
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
    run_id, trace_id = uuid4(), uuid4()
    processes = []
    release = None
    result_queue = None
    try:
        start_task = asyncio.create_task(
            runtime.start(run_id=run_id, trace_id=trace_id, command="跨进程恢复")
        )
        await finish_entered.wait()
        start_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start_task
        old_fence = int((await runtime._repository.get_lease_state(run_id=run_id))["fencing_token"])
        await runtime.close()
        runtime = None

        async with await psycopg.AsyncConnection.connect(
            host=durable_execution_settings.host,
            port=durable_execution_settings.port,
            dbname=durable_execution_settings.database,
            user=durable_execution_settings.user,
            password=durable_execution_settings.password.get_secret_value(),
            autocommit=True,
            prepare_threshold=0,
        ) as connection:
            await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET lease_owner = %s,
                    expires_at = clock_timestamp() - interval '1 second',
                    renewed_at = clock_timestamp()
                WHERE run_id = %s
                """,
                (uuid4(), run_id),
            )

        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        graph_started = context.Event()
        release = context.Event()
        effect_counter = context.Value("i", 0)
        result_queue = context.Queue()
        for _ in range(2):
            process = context.Process(
                target=run_recovery_worker,
                args=(run_id, barrier, graph_started, release, effect_counter, result_queue),
            )
            process.start()
            processes.append(process)

        assert await asyncio.to_thread(graph_started.wait, 30)
        first_result = await asyncio.to_thread(result_queue.get, True, 30)
        assert first_result == ("busy", None)

        async with await psycopg.AsyncConnection.connect(
            host=durable_execution_settings.host,
            port=durable_execution_settings.port,
            dbname=durable_execution_settings.database,
            user=durable_execution_settings.user,
            password=durable_execution_settings.password.get_secret_value(),
            autocommit=True,
            prepare_threshold=0,
        ) as connection:
            pre_release_lease = await (await connection.execute(
                """
                SELECT fencing_token,
                       lease_owner IS NOT NULL AND expires_at > clock_timestamp()
                FROM durable_execution.run_leases
                WHERE run_id = %s
                """,
                (run_id,),
            )).fetchone()
        assert pre_release_lease is not None
        assert int(pre_release_lease[0]) == old_fence + 1
        assert pre_release_lease[1] is True
        with effect_counter.get_lock():
            assert effect_counter.value == 0

        release.set()
        second_result = await asyncio.to_thread(result_queue.get, True, 30)
        assert second_result == ("terminal", DeliveryStatus.DEMO_READY.value)
        for process in processes:
            await asyncio.to_thread(process.join, 30)
            assert process.exitcode == 0
        assert effect_counter.value == 1

        async with await psycopg.AsyncConnection.connect(
            host=durable_execution_settings.host,
            port=durable_execution_settings.port,
            dbname=durable_execution_settings.database,
            user=durable_execution_settings.user,
            password=durable_execution_settings.password.get_secret_value(),
            autocommit=True,
            prepare_threshold=0,
        ) as connection:
            row = await (await connection.execute(
                "SELECT fencing_token FROM durable_execution.run_leases WHERE run_id = %s",
                (run_id,),
            )).fetchone()
        assert int(row[0]) == old_fence + 1
    finally:
        if release is not None:
            release.set()
        for process in processes:
            if process.is_alive():
                await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
        if result_queue is not None:
            result_queue.close()
        if runtime is not None:
            await runtime.close()
