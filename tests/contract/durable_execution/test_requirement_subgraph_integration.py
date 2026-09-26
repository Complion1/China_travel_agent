from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app.durable_execution.checkpointer import FencedCheckpointSaver, create_fenced_checkpoint_saver
from app.durable_execution.models import DEMO_DURABLE_EXECUTION_LIMITS
from app.durable_execution.repository import DurableExecutionRepository
from app.durable_execution.settings import load_durable_execution_settings
from app.requirement_subgraph import (
    RequirementConfirmationResume,
    RequirementSubgraphCoordinatorLimits,
    create_requirement_subgraph_embedded_driver,
)
from app.requirements import RequirementConfirmation
from app.workflow_contracts import (
    DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    InMemoryExecutionBudgetManager,
    TravelSessionState,
    WorkflowPhase,
)
from tests.requirement_subgraph_fakes import (
    ScriptedRequirementDomainService,
    make_confirmed_transition,
    make_initial_state,
    make_need_confirmation_transition,
    make_turn,
)
from tests.support.durable_execution_fakes import make_contract


def _start_plan(run_id: UUID) -> ExecutionBudgetPlan:
    return ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=run_id,
        limits=(
            ExecutionBudgetLimit(
                key=ExecutionBudgetKey(
                    scope=ExecutionBudgetScope.PHASE,
                    name="requirements.clarification_round",
                    phase=WorkflowPhase.REQUIREMENTS,
                ),
                limit=6,
            ),
        ),
    )


def _manager_factory(run_id: UUID) -> InMemoryExecutionBudgetManager:
    return InMemoryExecutionBudgetManager(
        plan=_start_plan(run_id),
        limits=DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    )


async def _pool(settings) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        conninfo="",
        kwargs={
            "host": settings.host,
            "port": settings.port,
            "dbname": settings.database,
            "user": settings.user,
            "password": settings.password.get_secret_value(),
            "autocommit": False,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=durable_execution,pg_catalog -c timezone=UTC",
        },
        min_size=1,
        max_size=4,
        open=False,
    )
    await pool.open(wait=True)
    return pool


def _parent_graph(driver, saver):
    builder = StateGraph(TravelSessionState)
    builder.add_node("requirements", driver.graph)
    builder.add_edge(START, "requirements")
    builder.add_edge("requirements", END)
    return builder.compile(checkpointer=saver)


def _assert_snapshot_task_bindings(snapshot, run_id: UUID) -> None:
    for task in snapshot.tasks:
        task_state = task.state
        assert str(UUID(str(task.id))) == str(task.id).lower()
        if task_state is None:
            # LangGraph 1.2.7 may expose a scheduled inner task without a
            # public child StateSnapshot; its canonical task identity is
            # still observable, but there is no namespace config to compare.
            continue
        task_config = task_state.config["configurable"]
        namespace = str(task_config["checkpoint_ns"])
        task_uuid = namespace.rsplit("|", 1)[-1].split(":", 1)[1]
        assert str(UUID(task_uuid)) == task_uuid.lower()
        assert str(task.id) == task_uuid.lower()
        assert task_config["thread_id"] == str(run_id)
        _assert_snapshot_task_bindings(task_state, run_id)


async def _paused_child_state(parent, config, run_id: UUID):
    snapshot = await parent.aget_state(config, subgraphs=True)
    assert snapshot.next == ("requirements",)
    assert len(snapshot.tasks) == 1
    task = snapshot.tasks[0]
    assert task.interrupts
    _assert_snapshot_task_bindings(snapshot, run_id)
    values = task.state.values
    if type(values) is dict:
        values = TypeAdapter(TravelSessionState).validate_python(values, strict=True)
    assert type(values) is TravelSessionState
    return values, task.interrupts[0].value


async def _read_persisted_rows(settings, run_id: UUID):
    connection = await AsyncConnection.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.user,
        password=settings.password.get_secret_value(),
        autocommit=False,
        prepare_threshold=0,
        row_factory=dict_row,
        options="-c search_path=durable_execution,pg_catalog -c timezone=UTC",
    )
    try:
        async with connection.transaction():
            cursor = await connection.execute(
                """
                SELECT checkpoint_ns, checkpoint_id, parent_checkpoint_id, metadata
                FROM durable_execution.checkpoints
                WHERE thread_id = %s
                ORDER BY checkpoint_id
                """,
                (str(run_id),),
            )
            checkpoints = tuple(await cursor.fetchall())
            counts: dict[str, int] = {}
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                count_cursor = await connection.execute(
                    f"SELECT count(*) AS n FROM durable_execution.{table} WHERE thread_id = %s",
                    (str(run_id),),
                )
                counts[table] = int((await count_cursor.fetchone())["n"])
        return checkpoints, counts
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def durable_context():
    settings = load_durable_execution_settings()
    pool = await _pool(settings)
    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    await repository.initialize_capacity()
    run_id = uuid4()
    await repository.create_run(
        run_id=run_id,
        trace_id=uuid4(),
        contract=make_contract(),
        start_plan=_start_plan(run_id),
    )
    owner = uuid4()
    token = await repository.acquire_lease(run_id=run_id, owner=owner)
    saver = await create_fenced_checkpoint_saver(
        settings=settings,
        repository=repository,
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        registered_node_names=(
            "requirements",
            "requirements/process_initial_turn",
            "requirements/await_clarification",
            "requirements/await_confirmation",
        ),
    )
    try:
        yield settings, run_id, saver
    finally:
        await saver.aclose()
        await pool.close()


@pytest.mark.asyncio
async def test_public_module05_driver_persists_child_pause_and_resume(durable_context, monkeypatch) -> None:
    settings, run_id, saver = durable_context
    actual_runtime_configs: list[Mapping[str, object]] = []
    original_checked_config = FencedCheckpointSaver._checked_config

    def record_actual_runtime(self, config, *, allow_actual_namespace=False):
        if not allow_actual_namespace and isinstance(config, Mapping):
            configurable = config.get("configurable")
            if isinstance(configurable, Mapping) and configurable.get("checkpoint_ns"):
                assert "__pregel_runtime" in configurable
                actual_runtime_configs.append(config)
        return original_checked_config(
            self,
            config,
            allow_actual_namespace=allow_actual_namespace,
        )

    monkeypatch.setattr(FencedCheckpointSaver, "_checked_config", record_actual_runtime)
    initial = make_initial_state(run_id=run_id)
    first_driver = create_requirement_subgraph_embedded_driver(
        domain_service=ScriptedRequirementDomainService(
            process_results=[make_need_confirmation_transition(initial)],
        ),
        execution_budget_manager_factory=_manager_factory,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    first_parent = _parent_graph(first_driver, saver)
    assert first_parent.checkpointer is saver
    config = {"configurable": {"thread_id": str(run_id)}}

    async with first_driver.start_operation(
        state=initial,
        turn=make_turn(initial),
    ) as entered:
        await first_parent.ainvoke(entered, config=config)
        first_snapshot = await first_parent.aget_state(config, subgraphs=True)
        _assert_snapshot_task_bindings(first_snapshot, run_id)
        await saver._confirm_graph_state(first_snapshot)
        paused_state, interrupt_value = await _paused_child_state(first_parent, config, run_id)
        paused = first_driver.resolve_result(
            state=paused_state,
            interrupt_value=interrupt_value,
        )

    assert paused.result_type == "paused"
    assert paused.state.run_id == run_id
    checkpoints, counts = await _read_persisted_rows(settings, run_id)
    assert counts["checkpoints"] >= 2
    assert counts["checkpoint_blobs"] >= 2
    assert counts["checkpoint_writes"] >= 1
    child_rows = [row for row in checkpoints if row["checkpoint_ns"]]
    assert child_rows
    child_namespace = str(child_rows[0]["checkpoint_ns"])
    node_name, task_text = child_namespace.split(":", 1)
    assert node_name == "requirements"
    assert str(UUID(task_text)) == task_text.lower()
    child_metadata = child_rows[0]["metadata"]
    assert set(child_metadata) == {"source", "step", "parents"}
    assert set(child_metadata["parents"]) == {""}
    root_ids = {row["checkpoint_id"] for row in checkpoints if not row["checkpoint_ns"]}
    assert child_metadata["parents"][""] in root_ids
    assert actual_runtime_configs
    for actual_config in actual_runtime_configs:
        actual_configurable = actual_config["configurable"]
        namespace = str(actual_configurable["checkpoint_ns"])
        final_task_uuid = namespace.rsplit("|", 1)[-1].split(":", 1)[1]
        runtime = actual_configurable["__pregel_runtime"]
        execution_info = runtime.execution_info
        assert execution_info.checkpoint_ns == namespace
        assert str(UUID(execution_info.task_id)) == final_task_uuid.lower()
        assert execution_info.thread_id == str(run_id)
        if "__pregel_task_id" in actual_configurable:
            assert str(UUID(actual_configurable["__pregel_task_id"])) == final_task_uuid.lower()

    second_driver = create_requirement_subgraph_embedded_driver(
        domain_service=ScriptedRequirementDomainService(
            confirm_results=[make_confirmed_transition(initial)],
        ),
        execution_budget_manager_factory=_manager_factory,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=2),
    )
    second_parent = _parent_graph(second_driver, saver)
    resume = RequirementConfirmationResume(
        action=RequirementConfirmation(decision="accept", revision_text=None),
    )
    async with second_driver.resume_accepted_operation(
        state=paused.state,
        durable_pause=paused.durable_pause,
        resume=resume,
    ) as command:
        final_value = await second_parent.ainvoke(command, config=config)
        final_snapshot = await second_parent.aget_state(config, subgraphs=True)
        _assert_snapshot_task_bindings(final_snapshot, run_id)
        await saver._confirm_graph_state(final_snapshot)
        final_state = (
            TypeAdapter(TravelSessionState).validate_python(final_value, strict=True)
            if type(final_value) is dict
            else final_value
        )
        assert type(final_state) is TravelSessionState
        final_result = second_driver.resolve_result(
            state=final_state,
            interrupt_value=None,
        )
    assert final_result.result_type == "confirmed"
    resumed_checkpoints, resumed_counts = await _read_persisted_rows(settings, run_id)
    assert len(resumed_checkpoints) > len(checkpoints)
    assert resumed_counts["checkpoints"] > counts["checkpoints"]
