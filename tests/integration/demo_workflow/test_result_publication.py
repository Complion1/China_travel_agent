"""Real PostgreSQL writes around the day/index confirmation window."""
import pytest

from app.artifact_persistence import ArtifactPersistenceDependencyError
from app.durable_execution import DurableExecutionDependencyError, DurableExecutionStateError, DurableExecutionTerminal
from app.workflow_contracts import ArtifactFound, ArtifactAbsent, OperationResultIndeterminateError, WorkflowPhase, DeliveryStatus
from app.demo_workflow.plans import operation, RESULT_WRITE_KEY
from tests.support.demo_workflow_fakes import full_runtime, start_to_pause, select_hotel


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["result_day_1", "result_index"])
@pytest.mark.parametrize("committed", [False, True])
async def test_publication_failure_never_reexecutes_providers(durable_execution_settings, monkeypatch, step, committed):
    async with full_runtime(durable_execution_settings) as context:
        paused = await start_to_pause(context,hotel=True)
        repository = context.artifacts.repository
        original = type(repository).put
        seen = []
        async def uncertain_put(self, request):
            if request.operation_id.logical_step == step:
                seen.append(request.operation_id)
                if committed:
                    await original(self, request)
                raise ArtifactPersistenceDependencyError()
            return await original(self, request)
        with monkeypatch.context() as patch:
            patch.setattr(type(repository),"put",uncertain_put)
            with pytest.raises((DurableExecutionDependencyError,DurableExecutionStateError,OperationResultIndeterminateError,ArtifactPersistenceDependencyError)):
                await select_hotel(context,paused)
        assert len(seen) == 1
        run_id = paused.state.run_id
        target = await repository.lookup(operation_id=operation(run_id,WorkflowPhase.OUTPUT_FORMATTING,step))
        assert type(target) is (ArtifactFound if committed else ArtifactAbsent)
        if step == "result_index":
            day = await repository.lookup(operation_id=operation(run_id,WorkflowPhase.OUTPUT_FORMATTING,"result_day_1"))
            assert type(day) is ArtifactFound
        before = (context.providers.research_model.call_count,context.providers.planning_model.call_count,
            len(context.providers.planning_map.directions_calls))
        if committed:
            result = await context.runtime.recover(run_id=run_id)
            assert type(result) is DurableExecutionTerminal
            assert result.state.delivery_status is DeliveryStatus.DEMO_READY
            snapshots = await context.runtime.get_terminal_budget_snapshots(run_id=run_id)
            assert next(s for s in snapshots if s.key == RESULT_WRITE_KEY).consumed == 2
        else:
            # M11 classifies a journal-guarded local write without an artifact
            # as invalid journal evidence; it blocks before re-entering M13.
            with pytest.raises(DurableExecutionStateError):
                await context.runtime.recover(run_id=run_id)
            assert (await context.runtime.get_run(run_id=run_id)).activity == "blocked"
            assert type(await repository.lookup(operation_id=operation(run_id,WorkflowPhase.OUTPUT_FORMATTING,"result_index"))) is ArtifactAbsent
        assert before == (context.providers.research_model.call_count,context.providers.planning_model.call_count,
            len(context.providers.planning_map.directions_calls))
