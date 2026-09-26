from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from langgraph.graph import END, START, StateGraph
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from app.durable_execution import DEMO_DURABLE_EXECUTION_LIMITS, DurableExecutionRunBlockedError, create_durable_execution_runtime
from app.durable_execution.errors import DurableExecutionStateError, DurableExecutionUsageError
from app.durable_execution.models import DurableExecutionEncodedCommand, NodeRecoveryClass
from app.durable_execution.repository import DurableExecutionRepository, _BlockFacts
from app.durable_execution.settings import DurableExecutionSettings
from app.workflow_contracts import (
    BudgetConsumeRequest,
    ExecutionBudgetKey,
    ExecutionBudgetLimit,
    ExecutionBudgetPlan,
    ExecutionBudgetScope,
    OperationId,
    OperationResultIndeterminateError,
    WorkflowPhase,
    DeliveryStatus,
    TravelSessionState,
)
from app.workflow_contracts.artifacts.models import ArtifactAbsent, ArtifactFound, ArtifactRef, ArtifactWriteRequest
from app.requirement_subgraph import RequirementConfirmationResume, create_requirement_subgraph_embedded_driver
from app.requirements import NeedConfirmation, RequirementConfirmation, RequirementDomainTransition
from tests.requirement_subgraph_fakes import ScriptedRequirementDomainService, fresh
from tests.support.durable_execution_fakes import (
    FakeArtifactRepository,
    FakeBudgetPlanProvider,
    FakeCommandCodec,
    make_contract,
    make_recovery_contract,
    make_requirement_recovery_contract,
)


async def _expire_lease(runtime, run_id: UUID) -> None:
    async with runtime._repository._pool.connection() as connection:
        async with connection.transaction():
            result = await connection.execute(
                """
                UPDATE durable_execution.run_leases
                SET lease_owner = COALESCE(lease_owner, %s),
                    expires_at = clock_timestamp() - interval '1 second',
                    renewed_at = clock_timestamp()
                WHERE run_id = %s
                """,
                (uuid4(), run_id),
            )
            assert result.rowcount == 1


async def _blocked_recovery_evidence(runtime, run_id: UUID) -> dict[str, object]:
    repository = runtime._repository
    async with repository._pool.connection() as connection:
        capacity = await (await connection.execute(
            "SELECT active_run_count, stored_run_count FROM durable_execution.capacity_state WHERE singleton_id = 1"
        )).fetchone()
        journal = await (await connection.execute(
            """SELECT operation_phase, operation_logical_step, transition_ordinal,
                      journal_state, reason_code
               FROM durable_execution.operation_journal WHERE run_id = %s
               ORDER BY operation_phase, operation_logical_step, transition_ordinal""",
            (run_id,),
        )).fetchall()
    lease = await repository.get_lease_state(run_id=run_id)
    return {
        "run": await repository.get_run_row(run_id=run_id),
        "pending": await repository.get_pending_command(run_id=run_id),
        "lease": {key: lease[key] for key in ("lease_owner", "fencing_token", "expires_at", "renewed_at")},
        "capacity": capacity,
        "journal": journal,
    }


class _SideArtifactRepository:
    def __init__(self) -> None:
        self.refs: dict[object, ArtifactRef] = {}
        self.lookup_calls = []
        self.put_calls = []

    async def lookup(self, *, operation_id):
        self.lookup_calls.append(operation_id)
        ref = self.refs.get(operation_id)
        if ref is None:
            return ArtifactAbsent(operation_id=operation_id)
        return ArtifactFound(ref=ref)

    async def put(self, request):
        self.put_calls.append(request)
        ref = ArtifactRef(
            artifact_id=uuid4(),
            artifact_type=request.artifact_type,
            schema_version=request.schema_version,
            content_hash="sha256:" + "a" * 64,
            operation_id=request.operation_id,
            created_at=datetime.now(UTC),
            size_bytes=2,
        )
        self.refs[request.operation_id] = ref
        return ref

    async def get(self, ref, *, expected_model):
        raise AssertionError("the non-recoverable recovery path must not read Artifact content")


class _ConfirmationCommandCodec(FakeCommandCodec):
    def requirement_resume(self, *, run_id, pause_generation, node_name, decoded_command):
        return RequirementConfirmationResume(
            action=RequirementConfirmation(decision="accept", revision_text=None)
        )


class _BlockingConfirmationService(ScriptedRequirementDomainService):
    def __init__(self) -> None:
        super().__init__()
        self.confirm_started = asyncio.Event()
        self.confirm_release = asyncio.Event()

    async def process_turn(self, input):
        self.process_calls.append(fresh(type(input), input))
        state = input.current.model_copy(update={"confirmation_status": "awaiting_confirmation"})
        return RequirementDomainTransition(
            state=state,
            outcome=NeedConfirmation(
                requirement=state.requirement,
                metadata=state.metadata,
                summary="请确认行程需求",
            ),
        )

    async def confirm(self, state, action):
        self.confirm_calls.append((fresh(type(state), state), fresh(type(action), action)))
        self.confirm_started.set()
        await self.confirm_release.wait()
        raise AssertionError("the cancelled service call must not return")


def _requirement_graph_factory(service):
    def build(capabilities):
        driver = create_requirement_subgraph_embedded_driver(
            domain_service=service,
            execution_budget_manager_factory=capabilities.execution_budget_manager_factory(
                node_name="requirements"
            ),
            limits=capabilities.requirement_subgraph_limits,
        )
        capabilities.register_requirement_subgraph(node_name="requirements", driver=driver)
        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(TravelSessionState)

        def finish(state: TravelSessionState) -> TravelSessionState:
            return state.model_copy(update={
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph.add_node("requirements", driver.graph)
        graph.add_node("finish", finish)
        graph.add_edge(START, "requirements")
        graph.add_edge("requirements", "finish")
        graph.add_edge("finish", END)
        return graph

    return build


async def _pool(settings: DurableExecutionSettings) -> AsyncConnectionPool:
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
                    name="requirements.failure",
                    phase=WorkflowPhase.REQUIREMENTS,
                ),
                limit=1,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_pending_command_fsm_uses_cas_and_clear_preserves_only_digest(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    await repository.initialize_capacity()
    try:
        run_id = uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        owner = uuid4()
        token = await repository.acquire_lease(run_id=run_id, owner=owner)
        command_bytes = b'{"kind":"initial"}'
        import hashlib

        encoded = DurableExecutionEncodedCommand(
            command_kind="initial",
            type_id="requirements.initial",
            type_version="1.0",
            canonical_bytes=command_bytes,
            digest="sha256:" + hashlib.sha256(command_bytes).hexdigest(),
            size_bytes=len(command_bytes),
        )
        await repository.create_pending_command(
            run_id=run_id,
            pause_generation=0,
            command_kind="initial",
            node_name="__start__",
            owner=owner,
            fencing_token=token,
            encoded_command=encoded,
            status="accepted",
        )
        await repository.transition_pending_command(
            run_id=run_id,
            pause_generation=0,
            expected_status="accepted",
            new_status="dispatch_started",
            owner=owner,
            fencing_token=token,
        )
        await repository.transition_pending_command(
            run_id=run_id,
            pause_generation=0,
            expected_status="dispatch_started",
            new_status="checkpointed",
            owner=owner,
            fencing_token=token,
        )
        await repository.transition_pending_command(
            run_id=run_id,
            pause_generation=0,
            expected_status="checkpointed",
            new_status="cleared",
            owner=owner,
            fencing_token=token,
        )
        with pytest.raises(DurableExecutionStateError):
            await repository.transition_pending_command(
                run_id=run_id,
                pause_generation=0,
                expected_status="cleared",
                new_status="accepted",
                owner=owner,
                fencing_token=token,
            )
        async with pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT status, command_bytes, command_digest, cleared_at
                    FROM durable_execution.pending_commands
                    WHERE run_id = %s AND pause_generation = 0
                    """,
                    (run_id,),
                )
                row = await cursor.fetchone()
                assert row["status"] == "cleared"
                assert row["command_bytes"] is None
                assert row["command_digest"] == encoded.digest
                assert row["cleared_at"] is not None
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_blocked_reason_is_closed_and_invalid_reason_is_rejected(
    durable_execution_settings: DurableExecutionSettings,
) -> None:
    pool = await _pool(durable_execution_settings)
    repository = DurableExecutionRepository(
        coordination_pool=pool,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    await repository.initialize_capacity()
    try:
        run_id = uuid4()
        await repository.create_run(
            run_id=run_id,
            trace_id=uuid4(),
            contract=make_contract(),
            start_plan=_plan(run_id),
        )
        with pytest.raises(DurableExecutionUsageError):
            await repository.block_run(run_id=run_id, reason_code="free_text")
        with pytest.raises(DurableExecutionUsageError):
            await repository.block_run(run_id=run_id, reason_code="state_invalid")
        await repository.block_run(run_id=run_id, facts=_BlockFacts())
        assert (await repository.get_run_row(run_id=run_id))["lifecycle"] == "blocked"
        await repository.block_run(run_id=run_id, facts=_BlockFacts())
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_module05_nonrecoverable_confirmation_node_is_never_called_twice(
    durable_execution_settings,
) -> None:
    service = _BlockingConfirmationService()
    artifacts = FakeArtifactRepository()
    runtime = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=artifacts,
        contract=make_requirement_recovery_contract(),
        command_codec=_ConfirmationCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_requirement_graph_factory(service),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    run_id, trace_id = uuid4(), uuid4()
    paused = await runtime.start(run_id=run_id, trace_id=trace_id, command="开始需求确认")
    assert paused.state.delivery_status is DeliveryStatus.AWAITING_USER
    await runtime.close()

    resuming = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=artifacts,
        contract=make_requirement_recovery_contract(),
        command_codec=_ConfirmationCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_requirement_graph_factory(service),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    resume_task = asyncio.create_task(resuming.resume(
        run_id=run_id,
        pause_token=paused.pause.pause_token,
        command="确认",
    ))
    try:
        await asyncio.wait_for(service.confirm_started.wait(), timeout=10.0)
        resume_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await resume_task
    finally:
        service.confirm_release.set()
        await resuming.close()

    recovering = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=artifacts,
        contract=make_requirement_recovery_contract(),
        command_codec=_ConfirmationCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=_requirement_graph_factory(service),
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        await _expire_lease(recovering, run_id)
        before = await _blocked_recovery_evidence(recovering, run_id)
        assert before["run"]["lifecycle"] == "open"
        assert before["pending"]["status"] == "dispatch_started"
        with pytest.raises(OperationResultIndeterminateError) as caught:
            await recovering.recover(run_id=run_id)
        assert type(caught.value) is OperationResultIndeterminateError
        after = await _blocked_recovery_evidence(recovering, run_id)
        assert after["run"]["lifecycle"] == "blocked"
        assert after["run"]["blocked_reason_code"] == "non_recoverable_dispatch"
        assert after["pending"]["status"] == "cleared"
        assert after["pending"]["command_bytes"] is None
        assert after["lease"]["fencing_token"] == before["lease"]["fencing_token"]
        side_effects = (len(service.process_calls), len(service.confirm_calls),
                        len(artifacts.lookup_calls), len(artifacts.put_calls))
        for call in (
            lambda: recovering.recover(run_id=run_id),
            lambda: recovering.get_state(run_id=run_id),
            lambda: recovering.resume(run_id=run_id, pause_token=paused.pause.pause_token, command="确认"),
        ):
            with pytest.raises(DurableExecutionRunBlockedError) as blocked:
                await call()
            assert type(blocked.value) is DurableExecutionRunBlockedError
        assert await _blocked_recovery_evidence(recovering, run_id) == after
        assert side_effects == (len(service.process_calls), len(service.confirm_calls),
                                len(artifacts.lookup_calls), len(artifacts.put_calls))
        assert len(service.process_calls) == 1
        assert len(service.confirm_calls) == 1
    finally:
        await recovering.close()


@pytest.mark.asyncio
async def test_candidate_research_service_node_is_not_replayed_after_dispatch_crash(
    durable_execution_settings,
) -> None:
    service_calls = 0
    service_entered = asyncio.Event()
    service_release = asyncio.Event()
    artifacts = _SideArtifactRepository()
    candidate_capabilities = []

    def build(capabilities):
        graph = StateGraph(TravelSessionState)
        candidate = capabilities.for_node(node_name="candidate_search")
        finish_capability = capabilities.for_node(node_name="finish")
        candidate_capabilities.append(candidate)

        async def candidate_search(state: TravelSessionState) -> TravelSessionState:
            nonlocal service_calls
            await candidate.ensure_phase_budget(state=state)
            with pytest.raises(DurableExecutionUsageError):
                _ = finish_capability.execution_budget_manager
            operation_id = OperationId(
                run_id=state.run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="candidate_search",
            )
            await candidate.execution_budget_manager.consume(BudgetConsumeRequest(
                operation_id=operation_id,
                key=ExecutionBudgetKey(
                    scope=ExecutionBudgetScope.PHASE,
                    name="requirements.clarification_round",
                    phase=WorkflowPhase.REQUIREMENTS,
                ),
                amount=1,
            ))
            service_calls += 1
            ref = await candidate.artifact_repository.put(ArtifactWriteRequest(
                operation_id=operation_id,
                artifact_type="test.candidate_search",
                schema_version="1.0",
                content={"candidate_count": 1},
            ))
            service_entered.set()
            await service_release.wait()
            return state.model_copy(update={"artifact_refs": (*state.artifact_refs, ref)})

        def finish(state: TravelSessionState) -> TravelSessionState:
            return state.model_copy(update={
                "delivery_status": DeliveryStatus.DEMO_READY,
                "terminal_code": "workflow_completed",
                "terminal_outcome": None,
            })

        graph.add_node("candidate_search", candidate_search)
        graph.add_node("finish", finish)
        graph.add_edge(START, "candidate_search")
        graph.add_edge("candidate_search", "finish")
        graph.add_edge("finish", END)
        return graph

    contract = make_recovery_contract(nodes=(
        ("candidate_search", NodeRecoveryClass.NON_RECOVERABLE),
        ("finish", NodeRecoveryClass.REPLAY_SAFE),
    ))
    first = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=artifacts,
        contract=contract,
        command_codec=FakeCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=build,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    run_id, trace_id = uuid4(), uuid4()
    task = asyncio.create_task(first.start(run_id=run_id, trace_id=trace_id, command="研究候选"))
    entered_wait = asyncio.create_task(service_entered.wait())
    try:
        done, _ = await asyncio.wait(
            (entered_wait, task),
            timeout=10.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            task.cancel()
            settled, _ = await asyncio.wait((task,), timeout=5.0)
            if task not in settled:
                raise TimeoutError("candidate start task did not settle after cancellation")
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise TimeoutError("candidate service was not entered before the diagnostic deadline")
        if task in done and not service_entered.is_set():
            await task
            raise AssertionError("candidate start completed before entering the service")
        await entered_wait
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        service_release.set()
        if not entered_wait.done():
            entered_wait.cancel()
            await asyncio.gather(entered_wait, return_exceptions=True)
        await first.close()
    assert len(candidate_capabilities) == 1
    with pytest.raises(DurableExecutionUsageError):
        _ = candidate_capabilities[0].execution_budget_manager

    second = await create_durable_execution_runtime(
        settings=durable_execution_settings,
        artifact_repository=artifacts,
        contract=contract,
        command_codec=FakeCommandCodec(),
        budget_plan_provider=FakeBudgetPlanProvider(),
        graph_factory=build,
        limits=DEMO_DURABLE_EXECUTION_LIMITS,
    )
    try:
        await _expire_lease(second, run_id)
        before = await _blocked_recovery_evidence(second, run_id)
        assert before["run"]["lifecycle"] == "open"
        assert before["pending"]["status"] == "dispatch_started"
        with pytest.raises(OperationResultIndeterminateError) as caught:
            await second.recover(run_id=run_id)
        assert type(caught.value) is OperationResultIndeterminateError
        after = await _blocked_recovery_evidence(second, run_id)
        assert after["run"]["lifecycle"] == "blocked"
        assert after["run"]["blocked_reason_code"] == "operation_indeterminate"
        assert after["pending"]["status"] == "cleared"
        assert after["pending"]["command_bytes"] is None
        assert after["lease"]["fencing_token"] == before["lease"]["fencing_token"]
        assert service_calls == 1
        assert len(artifacts.put_calls) == 1
        assert len(artifacts.lookup_calls) == 1
        evidence = await second._repository.get_recovery_operation_evidence(run_id=run_id)
        assert len(evidence) == 1
        assert evidence[0]["journal_state"] == "indeterminate"
        assert evidence[0]["artifact_id"] is None
        side_effects = (service_calls, len(candidate_capabilities),
                        len(artifacts.put_calls), len(artifacts.lookup_calls))
        for call in (
            lambda: second.recover(run_id=run_id),
            lambda: second.get_state(run_id=run_id),
            lambda: second.resume(run_id=run_id, pause_token="blocked-run", command="研究候选"),
        ):
            with pytest.raises(DurableExecutionRunBlockedError) as blocked:
                await call()
            assert type(blocked.value) is DurableExecutionRunBlockedError
        assert await _blocked_recovery_evidence(second, run_id) == after
        assert side_effects == (service_calls, len(candidate_capabilities),
                                len(artifacts.put_calls), len(artifacts.lookup_calls))
    finally:
        await second.close()
