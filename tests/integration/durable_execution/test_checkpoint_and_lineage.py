from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.runtime import ExecutionInfo, Runtime
from langgraph.types import Interrupt, PregelTask, StateSnapshot
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app.durable_execution.checkpointer import _safe_snapshot_digest, create_fenced_checkpoint_saver
from app.durable_execution.errors import DurableExecutionStateError, DurableExecutionUsageError
from app.durable_execution.models import DEMO_DURABLE_EXECUTION_LIMITS
from app.durable_execution.repository import DurableExecutionRepository
from app.durable_execution.repository import migration_advisory_key
from app.durable_execution.settings import DurableExecutionSettings
from app.workflow_contracts import (
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    WorkflowPhase,
)
from tests.requirement_subgraph_fakes import make_initial_state
from tests.support.durable_execution_fakes import make_contract


async def _coordination_pool(settings: DurableExecutionSettings) -> AsyncConnectionPool:
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


def _plan(run_id: UUID) -> ExecutionBudgetPlan:
    return ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=run_id,
        limits=(
            ExecutionBudgetLimit(
                key=ExecutionBudgetKey(
                    scope=ExecutionBudgetScope.PHASE,
                    name="requirements.checkpoint",
                    phase=WorkflowPhase.REQUIREMENTS,
                ),
                limit=2,
            ),
        ),
    )


def _checkpoint(value: object, *, run_id: UUID):
    version_by_marker = {
        "root": "1",
        "child": "2",
        "before": "1",
        "expired": "2",
        "stale": "3",
        "new": "4",
        "bad": "1",
        "bad_parent": "1",
        "grandchild": "3",
        "orphan": "4",
    }
    marker = next(iter(value)) if isinstance(value, dict) and value else "state"
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {
        "state": make_initial_state(run_id=run_id).model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
    }
    checkpoint["channel_versions"] = {"state": version_by_marker.get(marker, "1")}
    checkpoint["updated_channels"] = ["state"]
    return checkpoint


async def _counts(pool: AsyncConnectionPool) -> dict[str, int]:
    async with pool.connection() as connection:
        async with connection.transaction():
            result: dict[str, int] = {}
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                cursor = await connection.execute(
                    f"SELECT count(*) AS n FROM durable_execution.{table}"
                )
                row = await cursor.fetchone()
                result[table] = int(row["n"])
            return result


def _runtime_for_namespace(*, run_id: UUID, namespace: str) -> Runtime:
    return Runtime(
        execution_info=ExecutionInfo(
            checkpoint_id="runtime",
            checkpoint_ns=namespace,
            task_id=namespace.rsplit(":", 1)[1],
            thread_id=str(run_id),
            run_id=str(run_id),
        )
    )


async def _put_root_child_lineage(saver, *, run_id: UUID) -> dict[str, object]:
    root = await saver.aput(
        {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}},
        _checkpoint({"root": True}, run_id=run_id),
        {"source": "input", "step": -1, "parents": {}},
        {"state": "1"},
    )
    root_id = root["configurable"]["checkpoint_id"]
    child_namespace = "child:" + str(uuid4())
    child = await saver.aput(
        {
            "configurable": {
                "thread_id": str(run_id),
                "checkpoint_ns": child_namespace,
                "checkpoint_map": {"": root_id},
                "__pregel_runtime": _runtime_for_namespace(
                    run_id=run_id,
                    namespace=child_namespace,
                ),
            }
        },
        _checkpoint({"child": True}, run_id=run_id),
        {"source": "loop", "step": 0, "parents": {"": root_id}},
        {"state": "2"},
    )
    root_tuple = await saver.aget_tuple(root)
    child_tuple = await saver.aget_tuple(child)
    assert root_tuple is not None
    assert child_tuple is not None
    values = make_initial_state(run_id=run_id).model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    child_snapshot = StateSnapshot(
        values=values,
        next=(),
        config=child,
        metadata=child_tuple.metadata,
        created_at=None,
        parent_config=root,
        tasks=(),
        interrupts=(),
    )
    snapshot = StateSnapshot(
        values=values,
        next=(),
        config=root,
        metadata=root_tuple.metadata,
        created_at=None,
        parent_config=None,
        tasks=(
            PregelTask(
                id=child_namespace.rsplit(":", 1)[1],
                name="child",
                path=("child",),
                state=child_snapshot,
            ),
        ),
        interrupts=(),
    )
    return {
        "root": root,
        "root_id": root_id,
        "root_tuple": root_tuple,
        "child": child,
        "child_id": child["configurable"]["checkpoint_id"],
        "child_namespace": child_namespace,
        "child_tuple": child_tuple,
        "snapshot": snapshot,
        "values": values,
    }


@pytest_asyncio.fixture
async def checkpoint_context(durable_execution_settings: DurableExecutionSettings):
    coordination_pool = await _coordination_pool(durable_execution_settings)
    repository = DurableExecutionRepository(
        coordination_pool=coordination_pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    await repository.initialize_capacity()
    run_id = uuid4()
    await repository.create_run(
        run_id=run_id,
        trace_id=uuid4(),
        contract=make_contract(),
        start_plan=_plan(run_id),
    )
    owner = uuid4()
    token = await repository.acquire_lease(run_id=run_id, owner=owner)
    try:
        yield durable_execution_settings, coordination_pool, repository, run_id, owner, token
    finally:
        await coordination_pool.close()


@pytest.mark.asyncio
async def test_official_saver_writes_four_table_data_and_root_child_lineage(checkpoint_context) -> None:
    settings, pool, repository, run_id, owner, token = checkpoint_context
    saver = await create_fenced_checkpoint_saver(
        settings=settings,
        repository=repository,
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        registered_node_names=("child",),
    )
    try:
        root = await saver.aput(
            {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}},
            _checkpoint({"root": True}, run_id=run_id),
            {"source": "input", "step": -1, "parents": {}},
            {"state": "1"},
        )
        root_id = root["configurable"]["checkpoint_id"]
        await saver.aput_writes(
            {
                "configurable": {
                    "thread_id": str(run_id),
                    "checkpoint_ns": "",
                    "checkpoint_id": root_id,
                }
            },
            [("state", {"write": 1})],
            "task-root",
        )
        child_namespace = "child:" + str(uuid4())
        child_runtime = Runtime(
            execution_info=ExecutionInfo(
                checkpoint_id="runtime",
                checkpoint_ns=child_namespace,
                task_id=child_namespace.split(":", 1)[1],
                thread_id=str(run_id),
                run_id=str(run_id),
            )
        )
        child = await saver.aput(
            {
                "configurable": {
                    "thread_id": str(run_id),
                    "checkpoint_ns": child_namespace,
                    "checkpoint_map": {"": root_id},
                    "__pregel_runtime": child_runtime,
                }
            },
            _checkpoint({"child": True}, run_id=run_id),
            {"source": "loop", "step": 0, "parents": {"": root_id}},
            {"state": "2"},
        )
        child_tuple = await saver.aget_tuple(child)
        assert child_tuple is not None
        assert child_tuple.config["configurable"]["checkpoint_ns"] == child_namespace
        assert child_tuple.metadata["parents"][""] == root_id
        state_values = make_initial_state(run_id=run_id).model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        child_snapshot = StateSnapshot(
            values=state_values,
            next=(),
            config=child,
            metadata=child_tuple.metadata,
            created_at=None,
            parent_config=root,
            tasks=(),
            interrupts=(),
        )
        graph_snapshot = StateSnapshot(
            values=state_values,
            next=(),
            config=root,
            metadata={"source": "input", "step": -1, "parents": {}},
            created_at=None,
            parent_config=None,
            tasks=(PregelTask(id=child_namespace.split(":", 1)[1], name="child", path=("child",), state=child_snapshot),),
            interrupts=(),
        )
        root_tuple = await saver.aget_tuple(root)
        assert root_tuple is not None
        base_digest = _safe_snapshot_digest(
            graph_snapshot,
            root_tuple=root_tuple,
            descendant_tuples=(child_tuple,),
            registered_names={"child"},
            run_id=run_id,
        )
        assert base_digest != _safe_snapshot_digest(
            graph_snapshot._replace(next=("child",)),
            root_tuple=root_tuple,
            descendant_tuples=(child_tuple,),
            registered_names={"child"},
            run_id=run_id,
        )
        assert base_digest != _safe_snapshot_digest(
            graph_snapshot._replace(parent_config=root),
            root_tuple=root_tuple,
            descendant_tuples=(child_tuple,),
            registered_names={"child"},
            run_id=run_id,
        )
        interrupted_task = PregelTask(
            id=child_namespace.split(":", 1)[1],
            name="child",
            path=("child",),
            interrupts=(Interrupt(value={"kind": "pause"}, id="interrupt-1"),),
            state=child_snapshot,
        )
        assert base_digest != _safe_snapshot_digest(
            graph_snapshot._replace(tasks=(interrupted_task,)),
            root_tuple=root_tuple,
            descendant_tuples=(child_tuple,),
            registered_names={"child"},
            run_id=run_id,
        )
        # 设计闭集之外的官方字段、tuple metadata source/step、snapshot
        # created_at 和 PregelTask.result 都不能影响 durable pointer digest。
        for field, value in {
            "ts": "2099-01-01T00:00:00Z",
            "channel_versions": {"ignored": "999"},
            "versions_seen": {"ignored": {"seen": 1}},
            "pending_sends": [{"ignored": True}],
            "updated_channels": ["ignored"],
        }.items():
            ignored_checkpoint = dict(root_tuple.checkpoint)
            ignored_checkpoint[field] = value
            assert base_digest == _safe_snapshot_digest(
                graph_snapshot,
                root_tuple=root_tuple._replace(checkpoint=ignored_checkpoint),
                descendant_tuples=(child_tuple,),
                registered_names={"child"},
                run_id=run_id,
            )
        assert base_digest == _safe_snapshot_digest(
            graph_snapshot._replace(
                created_at="ignored-created-at",
                metadata={"source": "loop", "step": 999, "parents": {}},
            ),
            root_tuple=root_tuple._replace(
                metadata={"source": "loop", "step": 999, "parents": {}}
            ),
            descendant_tuples=(child_tuple._replace(metadata={
                "source": "input",
                "step": 999,
                "parents": {"": root_id},
            }),),
            registered_names={"child"},
            run_id=run_id,
        )
        ignored_result_task = PregelTask(
            id=child_namespace.split(":", 1)[1],
            name="child",
            path=("child",),
            result={"ignored": True},
            state=child_snapshot,
        )
        assert base_digest == _safe_snapshot_digest(
            graph_snapshot._replace(tasks=(ignored_result_task,)),
            root_tuple=root_tuple,
            descendant_tuples=(child_tuple,),
            registered_names={"child"},
            run_id=run_id,
        )
        with pytest.raises(DurableExecutionStateError):
            await saver._confirm_graph_state(
                graph_snapshot._replace(
                    tasks=(PregelTask(
                        id=str(uuid4()),
                        name="child",
                        path=("child",),
                        state=child_snapshot,
                    ),)
                )
            )
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT latest_root_checkpoint_id FROM durable_execution.runs WHERE run_id = %s",
                    (run_id,),
                )
                assert (await cursor.fetchone())["latest_root_checkpoint_id"] is None
        await saver._confirm_graph_state(graph_snapshot)
        lineage = await saver._read_bounded_lineage(
            {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}},
            ({"configurable": {"thread_id": "unrelated", "checkpoint_ns": "child:" + str(uuid4())}},),
            snapshot=graph_snapshot,
        )
        assert len(lineage) == 2

        counts = await _counts(pool)
        assert counts["checkpoints"] >= 2
        assert counts["checkpoint_blobs"] >= 2
        assert counts["checkpoint_writes"] >= 1
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT latest_root_checkpoint_ns, latest_root_checkpoint_id, latest_root_checkpoint_digest "
                    "FROM durable_execution.runs WHERE run_id = %s",
                    (run_id,),
                )
                pointer = await cursor.fetchone()
        assert pointer["latest_root_checkpoint_ns"] == ""
        assert pointer["latest_root_checkpoint_id"] == root_id
        assert pointer["latest_root_checkpoint_digest"].startswith("sha256:")
        with pytest.raises(DurableExecutionUsageError):
            await saver.adelete_thread(str(run_id))
    finally:
        await saver.aclose()


@pytest.mark.asyncio
async def test_nested_descendant_lineage_uses_actual_task_closure(checkpoint_context) -> None:
    settings, _pool, repository, run_id, owner, token = checkpoint_context
    saver = await create_fenced_checkpoint_saver(
        settings=settings,
        repository=repository,
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        registered_node_names=("child", "child/grandchild"),
    )
    try:
        lineage = await _put_root_child_lineage(saver, run_id=run_id)
        root = lineage["root"]
        child = lineage["child"]
        child_namespace = lineage["child_namespace"]
        grandchild_namespace = f"{child_namespace}|grandchild:{uuid4()}"
        checkpoint_map = {
            "": lineage["root_id"],
            child_namespace: lineage["child_id"],
        }
        grandchild = await saver.aput(
            {
                "configurable": {
                    "thread_id": str(run_id),
                    "checkpoint_ns": grandchild_namespace,
                    "checkpoint_map": checkpoint_map,
                    "__pregel_runtime": _runtime_for_namespace(
                        run_id=run_id,
                        namespace=grandchild_namespace,
                    ),
                }
            },
            _checkpoint({"grandchild": True}, run_id=run_id),
            {"source": "loop", "step": 1, "parents": checkpoint_map},
            {"state": "3"},
        )
        grandchild_tuple = await saver.aget_tuple(grandchild)
        assert grandchild_tuple is not None
        values = lineage["values"]
        grandchild_snapshot = StateSnapshot(
            values=values,
            next=(),
            config=grandchild,
            metadata=grandchild_tuple.metadata,
            created_at=None,
            parent_config=child,
            tasks=(),
            interrupts=(),
        )
        original_snapshot = lineage["snapshot"]
        original_child_snapshot = original_snapshot.tasks[0].state
        nested_child_snapshot = original_child_snapshot._replace(
            tasks=(
                PregelTask(
                    id=grandchild_namespace.rsplit(":", 1)[1],
                    name="grandchild",
                    path=("grandchild",),
                    state=grandchild_snapshot,
                ),
            )
        )
        nested_snapshot = original_snapshot._replace(
            tasks=(original_snapshot.tasks[0]._replace(state=nested_child_snapshot),)
        )
        await saver._confirm_graph_state(nested_snapshot)
        records = await saver._read_bounded_lineage(
            {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}},
            (),
            snapshot=nested_snapshot,
        )
        assert len(records) == 3
        assert (await repository.get_run_row(run_id=run_id))["latest_root_checkpoint_id"] == root["configurable"]["checkpoint_id"]
    finally:
        await saver.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    (
        "cross_thread",
        "orphan",
        "missing_root",
        "missing_child",
        "missing_parent",
        "corrupt_metadata",
        "corrupt_map",
    ),
)
async def test_lineage_corruption_matrix_fails_without_pointer_advance(
    checkpoint_context,
    case: str,
) -> None:
    settings, pool, repository, run_id, owner, token = checkpoint_context
    saver = await create_fenced_checkpoint_saver(
        settings=settings,
        repository=repository,
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        registered_node_names=("child", "other"),
    )
    try:
        lineage = await _put_root_child_lineage(saver, run_id=run_id)
        snapshot = lineage["snapshot"]
        root_id = lineage["root_id"]
        child_id = lineage["child_id"]
        child_namespace = lineage["child_namespace"]
        pointer_before = (await repository.get_run_row(run_id=run_id))["latest_root_checkpoint_id"]
        assert pointer_before is None

        if case == "corrupt_map":
            child_state = snapshot.tasks[0].state
            bad_child_config = {
                "configurable": {
                    **child_state.config["configurable"],
                    "checkpoint_map": {"": "missing-root-map"},
                }
            }
            snapshot = snapshot._replace(
                tasks=(snapshot.tasks[0]._replace(state=child_state._replace(config=bad_child_config)),)
            )
        else:
            async with pool.connection() as connection:
                async with connection.transaction():
                    if case == "cross_thread":
                        await connection.execute(
                            "UPDATE durable_execution.checkpoints SET thread_id = %s WHERE thread_id = %s AND checkpoint_ns = '' AND checkpoint_id = %s",
                            (str(uuid4()), str(run_id), root_id),
                        )
                    elif case == "missing_root":
                        await connection.execute(
                            "DELETE FROM durable_execution.checkpoints WHERE thread_id = %s AND checkpoint_ns = '' AND checkpoint_id = %s",
                            (str(run_id), root_id),
                        )
                    elif case == "missing_child":
                        await connection.execute(
                            "DELETE FROM durable_execution.checkpoints WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s",
                            (str(run_id), child_namespace, child_id),
                        )
                    elif case == "missing_parent":
                        await connection.execute(
                            "UPDATE durable_execution.checkpoints SET metadata = jsonb_set(metadata, '{parents}', '{}'::jsonb, true) WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s",
                            (str(run_id), child_namespace, child_id),
                        )
                    elif case == "corrupt_metadata":
                        await connection.execute(
                            "UPDATE durable_execution.checkpoints SET metadata = metadata || '{\"canary\":\"forbidden\"}'::jsonb WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s",
                            (str(run_id), child_namespace, child_id),
                        )
                    else:
                        other_namespace = "other:" + str(uuid4())
                        other = await saver.aput(
                            {
                                "configurable": {
                                    "thread_id": str(run_id),
                                    "checkpoint_ns": other_namespace,
                                    "checkpoint_map": {"": root_id},
                                    "__pregel_runtime": _runtime_for_namespace(
                                        run_id=run_id,
                                        namespace=other_namespace,
                                    ),
                                }
                            },
                            _checkpoint({"orphan": True}, run_id=run_id),
                            {"source": "loop", "step": 0, "parents": {"": root_id}},
                            {"state": "4"},
                        )
                        await connection.execute(
                            "UPDATE durable_execution.checkpoints SET metadata = jsonb_set(metadata, '{parents}', '{\"\":\"missing-root\"}'::jsonb, true) WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s",
                            (
                                str(run_id),
                                other_namespace,
                                other["configurable"]["checkpoint_id"],
                            ),
                        )

        with pytest.raises(DurableExecutionStateError):
            await saver._confirm_graph_state(snapshot)
        assert (await repository.get_run_row(run_id=run_id))["latest_root_checkpoint_id"] == pointer_before
    finally:
        await saver.aclose()


@pytest.mark.asyncio
async def test_old_writer_lock_precedes_takeover_without_timing_guess(checkpoint_context) -> None:
    _settings, pool, repository, run_id, owner, token = checkpoint_context
    new_owner = uuid4()
    key = migration_advisory_key(domain="run", payload=str(run_id).encode("ascii"))
    async with repository.fencing_transaction(
        run_id=run_id,
        owner=owner,
        fencing_token=token,
    ):
        async with pool.connection() as probe:
            async with probe.transaction():
                cursor = await probe.execute(
                    "SELECT pg_try_advisory_xact_lock(%s) AS acquired",
                    (key,),
                )
                row = await cursor.fetchone()
                assert row["acquired"] is False
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE durable_execution.run_leases SET expires_at = clock_timestamp() - interval '1 second' WHERE run_id = %s",
                (run_id,),
            )
    assert await repository.acquire_lease(run_id=run_id, owner=new_owner) == token + 1


@pytest.mark.asyncio
async def test_current_root_fork_is_rejected_without_pointer_mutation(checkpoint_context) -> None:
    settings, pool, repository, run_id, owner, token = checkpoint_context
    saver = await create_fenced_checkpoint_saver(
        settings=settings,
        repository=repository,
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        registered_node_names=("child",),
    )
    try:
        root = await saver.aput(
            {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}},
            _checkpoint({"root": True}, run_id=run_id),
            {"source": "input", "step": -1, "parents": {}},
            {"state": "1"},
        )
        root_id = root["configurable"]["checkpoint_id"]

        def child_runtime(namespace: str) -> Runtime:
            task_id = namespace.split(":", 1)[1]
            return Runtime(
                execution_info=ExecutionInfo(
                    checkpoint_id="runtime",
                    checkpoint_ns=namespace,
                    task_id=task_id,
                    thread_id=str(run_id),
                    run_id=str(run_id),
                )
            )

        expected_namespace = "child:" + str(uuid4())
        expected_child = await saver.aput(
            {
                "configurable": {
                    "thread_id": str(run_id),
                    "checkpoint_ns": expected_namespace,
                    "checkpoint_map": {"": root_id},
                    "__pregel_runtime": child_runtime(expected_namespace),
                }
            },
            _checkpoint({"child": True}, run_id=run_id),
            {"source": "loop", "step": 0, "parents": {"": root_id}},
            {"state": "2"},
        )
        fork_namespace = "child:" + str(uuid4())
        await saver.aput(
            {
                "configurable": {
                    "thread_id": str(run_id),
                    "checkpoint_ns": fork_namespace,
                    "checkpoint_map": {"": root_id},
                    "__pregel_runtime": child_runtime(fork_namespace),
                }
            },
            _checkpoint({"child": True}, run_id=run_id),
            {"source": "loop", "step": 0, "parents": {"": root_id}},
            {"state": "2"},
        )
        expected_tuple = await saver.aget_tuple(expected_child)
        assert expected_tuple is not None
        state_values = make_initial_state(run_id=run_id).model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        snapshot = StateSnapshot(
            values=state_values,
            next=(),
            config=root,
            metadata={"source": "input", "step": -1, "parents": {}},
            created_at=None,
            parent_config=None,
            tasks=(PregelTask(
                id=expected_namespace.split(":", 1)[1],
                name="child",
                path=("child",),
                state=StateSnapshot(
                    values=state_values,
                    next=(),
                    config=expected_child,
                    metadata=expected_tuple.metadata,
                    created_at=None,
                    parent_config=root,
                    tasks=(),
                    interrupts=(),
                ),
            ),),
            interrupts=(),
        )
        with pytest.raises(DurableExecutionStateError):
            await saver._confirm_graph_state(snapshot)
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    "SELECT latest_root_checkpoint_id FROM durable_execution.runs WHERE run_id = %s",
                    (run_id,),
                )
                assert (await cursor.fetchone())["latest_root_checkpoint_id"] is None
    finally:
        await saver.aclose()


@pytest.mark.asyncio
async def test_custom_low_lineage_bound_counts_root_history_and_descendants(checkpoint_context) -> None:
    settings, pool, repository, run_id, owner, token = checkpoint_context
    low_limits = DEMO_DURABLE_EXECUTION_LIMITS.model_copy(update={"max_lineage_checkpoints": 2})
    saver = await create_fenced_checkpoint_saver(
        settings=settings,
        repository=repository,
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        limits=low_limits,
        registered_node_names=("child", "other"),
    )
    try:
        root = await saver.aput(
            {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}},
            _checkpoint({"root": True}, run_id=run_id),
            {"source": "input", "step": -1, "parents": {}},
            {"state": "1"},
        )
        root_tuple = await saver.aget_tuple(root)
        assert root_tuple is not None
        child_namespace = "child:" + str(uuid4())
        child = await saver.aput(
            {
                "configurable": {
                    "thread_id": str(run_id),
                    "checkpoint_ns": child_namespace,
                    "checkpoint_map": {"": root["configurable"]["checkpoint_id"]},
                    "__pregel_runtime": Runtime(
                        execution_info=ExecutionInfo(
                            checkpoint_id="runtime",
                            checkpoint_ns=child_namespace,
                            task_id=child_namespace.split(":", 1)[1],
                            thread_id=str(run_id),
                            run_id=str(run_id),
                        )
                    ),
                }
            },
            _checkpoint({"child": True}, run_id=run_id),
            {"source": "loop", "step": 0, "parents": {"": root["configurable"]["checkpoint_id"]}},
            {"state": "2"},
        )
        child_tuple = await saver.aget_tuple(child)
        assert child_tuple is not None
        values = make_initial_state(run_id=run_id).model_dump(
            mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True
        )

        def snapshot(root_config, root_metadata, children):
            tasks = []
            for name, child_config, child_record in children:
                child_state = StateSnapshot(
                    values=values,
                    next=(),
                    config=child_config,
                    metadata=child_record.metadata,
                    created_at=None,
                    parent_config=root_config,
                    tasks=(),
                    interrupts=(),
                )
                tasks.append(
                    PregelTask(
                        id=child_config["configurable"]["checkpoint_ns"].rsplit(":", 1)[1],
                        name=name,
                        path=(name,),
                        state=child_state,
                    )
                )
            return StateSnapshot(
                values=values,
                next=(),
                config=root_config,
                metadata=root_metadata,
                created_at=None,
                parent_config=None,
                tasks=tuple(tasks),
                interrupts=(),
            )

        initial_snapshot = snapshot(root, root_tuple.metadata, (("child", child, child_tuple),))
        exact = await saver._read_bounded_lineage(
            {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}},
            (),
            snapshot=initial_snapshot,
        )
        assert len(exact) == 2
        await saver._confirm_graph_state(initial_snapshot)
        root_pointer_before = (await repository.get_run_row(run_id=run_id))["latest_root_checkpoint_id"]

        root2 = await saver.aput(
            {
                "configurable": {
                    "thread_id": str(run_id),
                    "checkpoint_ns": "",
                    "checkpoint_id": root["configurable"]["checkpoint_id"],
                }
            },
            _checkpoint({"root2": True}, run_id=run_id),
            {"source": "loop", "step": 0, "parents": {}},
            {"state": "3"},
        )
        root2_tuple = await saver.aget_tuple(root2)
        assert root2_tuple is not None
        child2_namespace = "child:" + str(uuid4())
        child2 = await saver.aput(
            {
                "configurable": {
                    "thread_id": str(run_id),
                    "checkpoint_ns": child2_namespace,
                    "checkpoint_map": {"": root2["configurable"]["checkpoint_id"]},
                    "__pregel_runtime": Runtime(
                        execution_info=ExecutionInfo(
                            checkpoint_id="runtime",
                            checkpoint_ns=child2_namespace,
                            task_id=child2_namespace.split(":", 1)[1],
                            thread_id=str(run_id),
                            run_id=str(run_id),
                        )
                    ),
                }
            },
            _checkpoint({"child2": True}, run_id=run_id),
            {"source": "loop", "step": 0, "parents": {"": root2["configurable"]["checkpoint_id"]}},
            {"state": "4"},
        )
        child2_tuple = await saver.aget_tuple(child2)
        assert child2_tuple is not None
        stale_snapshot = initial_snapshot
        with pytest.raises(DurableExecutionStateError):
            await saver._confirm_graph_state(stale_snapshot)
        with pytest.raises(DurableExecutionStateError):
            await saver._confirm_graph_state(
                snapshot(root2, root2_tuple.metadata, (("child", child2, child2_tuple),))
            )
        assert (await repository.get_run_row(run_id=run_id))["latest_root_checkpoint_id"] == root_pointer_before

        other_namespace = "other:" + str(uuid4())
        other = await saver.aput(
            {
                "configurable": {
                    "thread_id": str(run_id),
                    "checkpoint_ns": other_namespace,
                    "checkpoint_map": {"": root2["configurable"]["checkpoint_id"]},
                    "__pregel_runtime": Runtime(
                        execution_info=ExecutionInfo(
                            checkpoint_id="runtime",
                            checkpoint_ns=other_namespace,
                            task_id=other_namespace.split(":", 1)[1],
                            thread_id=str(run_id),
                            run_id=str(run_id),
                        )
                    ),
                }
            },
            _checkpoint({"other": True}, run_id=run_id),
            {"source": "loop", "step": 0, "parents": {"": root2["configurable"]["checkpoint_id"]}},
            {"state": "5"},
        )
        other_tuple = await saver.aget_tuple(other)
        assert other_tuple is not None
        with pytest.raises(DurableExecutionStateError):
            await saver._confirm_graph_state(
                snapshot(
                    root2,
                    root2_tuple.metadata,
                    (("child", child2, child2_tuple), ("other", other, other_tuple)),
                )
            )
        assert (await repository.get_run_row(run_id=run_id))["latest_root_checkpoint_id"] == root_pointer_before
    finally:
        await saver.aclose()


@pytest.mark.asyncio
async def test_expired_and_stale_fence_have_no_checkpoint_write(checkpoint_context) -> None:
    settings, pool, repository, run_id, owner, token = checkpoint_context
    old_saver = await create_fenced_checkpoint_saver(
        settings=settings,
        repository=repository,
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        root_config = {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}}
        await old_saver.aput(root_config, _checkpoint({"before": True}, run_id=run_id), {"source": "input", "step": -1, "parents": {}}, {"state": "1"})
        before = await _counts(pool)
        async with pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "UPDATE durable_execution.run_leases SET expires_at = clock_timestamp() - interval '1 second' WHERE run_id = %s",
                    (run_id,),
                )
        with pytest.raises(DurableExecutionStateError):
            await old_saver.aput(root_config, _checkpoint({"expired": True}, run_id=run_id), {"source": "loop", "step": 0, "parents": {}}, {"state": "2"})
        with pytest.raises(DurableExecutionStateError):
            await old_saver.aput_writes(
                {"configurable": {"thread_id": str(run_id), "checkpoint_ns": "", "checkpoint_id": "missing"}},
                [("state", {"write": 2})],
                "task-expired",
            )
        assert await _counts(pool) == before

        new_owner = uuid4()
        new_token = await repository.acquire_lease(run_id=run_id, owner=new_owner)
        new_saver = await create_fenced_checkpoint_saver(
            settings=settings,
            repository=repository,
            run_id=run_id,
            owner=new_owner,
            fencing_token=new_token,
            limits=DEMO_DURABLE_EXECUTION_LIMITS,
        )
        try:
            with pytest.raises(DurableExecutionStateError):
                await old_saver.aput(root_config, _checkpoint({"stale": True}, run_id=run_id), {"source": "loop", "step": 0, "parents": {}}, {"state": "3"})
            after_stale = await _counts(pool)
            assert after_stale == before
            await new_saver.aput(root_config, _checkpoint({"new": True}, run_id=run_id), {"source": "loop", "step": 0, "parents": {}}, {"state": "4"})
            assert (await _counts(pool))["checkpoints"] == before["checkpoints"] + 1
        finally:
            await new_saver.aclose()
    finally:
        await old_saver.aclose()


@pytest.mark.asyncio
async def test_metadata_and_namespace_canaries_fail_closed(checkpoint_context) -> None:
    settings, _pool, repository, run_id, owner, token = checkpoint_context
    saver = await create_fenced_checkpoint_saver(
        settings=settings,
        repository=repository,
        run_id=run_id,
        owner=owner,
        fencing_token=token,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
        registered_node_names=("child",),
    )
    try:
        config = {"configurable": {"thread_id": str(run_id), "checkpoint_ns": ""}}
        with pytest.raises(DurableExecutionStateError):
            await saver.aput(config, _checkpoint({"bad": True}, run_id=run_id), {"source": "loop", "step": 0, "canary": "no"}, {"state": "1"})
        with pytest.raises(DurableExecutionUsageError):
            await saver.aput(
                {"configurable": {"thread_id": str(run_id), "checkpoint_ns": "unregistered:" + str(uuid4())}},
                _checkpoint({"bad": True}, run_id=run_id),
                {"source": "loop", "step": 0, "parents": {}},
                {"state": "1"},
            )
        with pytest.raises(DurableExecutionUsageError):
            await saver.aput(
                {
                    "configurable": {
                        "thread_id": str(run_id),
                        "checkpoint_ns": "child:" + str(uuid4()),
                    }
                },
                _checkpoint({"canonical_without_runtime": True}, run_id=run_id),
                {"source": "loop", "step": 0, "parents": {}},
                {"state": "1"},
            )
        with pytest.raises(DurableExecutionStateError):
            bad_namespace = "child:" + str(uuid4())
            await saver.aput(
                {
                    "configurable": {
                        "thread_id": str(run_id),
                        "checkpoint_ns": bad_namespace,
                        "checkpoint_map": {},
                        "__pregel_runtime": Runtime(
                            execution_info=ExecutionInfo(
                                checkpoint_id="runtime",
                                checkpoint_ns=bad_namespace,
                                task_id=bad_namespace.split(":", 1)[1],
                                thread_id=str(run_id),
                                run_id=str(run_id),
                            )
                        ),
                    }
                },
                _checkpoint({"bad_parent": True}, run_id=run_id),
                {"source": "loop", "step": 0, "parents": {}},
                {"state": "1"},
            )
        identity_namespace = "child:" + str(uuid4())
        identity_task = identity_namespace.split(":", 1)[1]
        with pytest.raises(DurableExecutionUsageError):
            await saver.aput(
                {
                    "configurable": {
                        "thread_id": str(run_id),
                        "checkpoint_ns": identity_namespace,
                        "__pregel_runtime": Runtime(
                            execution_info=ExecutionInfo(
                                checkpoint_id="runtime",
                                checkpoint_ns=identity_namespace,
                                task_id=str(uuid4()),
                                thread_id=str(run_id),
                                run_id=str(run_id),
                            )
                        ),
                    }
                },
                _checkpoint({"bad_task": True}, run_id=run_id),
                {"source": "loop", "step": 0, "parents": {}},
                {"state": "1"},
            )
        with pytest.raises(DurableExecutionUsageError):
            await saver.aput(
                {
                    "configurable": {
                        "thread_id": str(run_id),
                        "checkpoint_ns": identity_namespace,
                        "__pregel_runtime": Runtime(
                            execution_info=ExecutionInfo(
                                checkpoint_id="runtime",
                                checkpoint_ns=identity_namespace,
                                task_id=identity_task,
                                thread_id=str(uuid4()),
                                run_id=str(run_id),
                            )
                        ),
                    }
                },
                _checkpoint({"bad_thread": True}, run_id=run_id),
                {"source": "loop", "step": 0, "parents": {}},
                {"state": "1"},
            )
        with pytest.raises(DurableExecutionUsageError):
            await saver.aput(
                {
                    "configurable": {
                        "thread_id": str(run_id),
                        "checkpoint_ns": identity_namespace,
                        "__pregel_runtime": Runtime(
                            execution_info=ExecutionInfo(
                                checkpoint_id="runtime",
                                checkpoint_ns="child:" + str(uuid4()),
                                task_id=identity_task,
                                thread_id=str(run_id),
                                run_id=str(run_id),
                            )
                        ),
                    }
                },
                _checkpoint({"bad_namespace": True}, run_id=run_id),
                {"source": "loop", "step": 0, "parents": {}},
                {"state": "1"},
            )
        with pytest.raises(DurableExecutionUsageError):
            await saver.aput(
                {
                    "configurable": {
                        "thread_id": str(run_id),
                        "checkpoint_ns": identity_namespace,
                        "__pregel_task_id": str(uuid4()),
                        "__pregel_runtime": Runtime(
                            execution_info=ExecutionInfo(
                                checkpoint_id="runtime",
                                checkpoint_ns=identity_namespace,
                                task_id=identity_task,
                                thread_id=str(run_id),
                                run_id=str(run_id),
                            )
                        ),
                    }
                },
                _checkpoint({"bad_public_task_id": True}, run_id=run_id),
                {"source": "loop", "step": 0, "parents": {}},
                {"state": "1"},
            )
    finally:
        await saver.aclose()
