"""M13 cancellation uses M11 durable intent and terminal budget snapshots."""
import pytest

from app.durable_execution import DurableExecutionCancellationPendingError, DurableRunListRequest
from app.workflow_contracts import DeliveryStatus
from tests.support.demo_workflow_fakes import full_runtime, start_to_pause


@pytest.mark.asyncio
@pytest.mark.parametrize("hotel", [False, True])
async def test_cancel_waiting_preserves_evidence_and_runtime(durable_execution_settings, hotel):
    async with full_runtime(durable_execution_settings) as context:
        paused = await start_to_pause(context, hotel=hotel)
        run_id = paused.state.run_id
        assert (await context.runtime.get_run(run_id=run_id)).activity == "awaiting_user"
        assert (await context.runtime.request_cancel(run_id=run_id)).status == "requested"
        assert (await context.runtime.request_cancel(run_id=run_id)).status == "requested"
        assert (await context.runtime.get_run(run_id=run_id)).activity == "cancelling"
        with pytest.raises(DurableExecutionCancellationPendingError):
            await context.runtime.get_state(run_id=run_id)
        result = await context.runtime.settle_cancel(run_id=run_id)
        assert result.status == "cancelled"
        assert result.state.delivery_status is DeliveryStatus.CANCELLED
        assert result.state.artifact_refs == paused.state.artifact_refs
        budgets = await context.runtime.get_terminal_budget_snapshots(run_id=run_id)
        assert all(s.run_id == run_id for s in budgets)
        summary = await context.dependencies.diagnostics.finish(state=result.state, budget_snapshots=budgets)
        assert summary.summary.budget_snapshots == budgets
        assert (await context.runtime.request_cancel(run_id=run_id)).status == "already_terminal"
        page = await context.runtime.list_runs(request=DurableRunListRequest(limit=100))
        item = next(i for i in page.items if i.run_id == run_id)
        assert item.activity == "terminal"
        assert "pause_token" not in item.model_dump_json()
        assert len(context.providers.extractor.calls) == 1
        assert context.providers.hotel_map.search_call_count == int(hotel)
        assert context.providers.research_model.call_count == context.providers.planning_model.call_count == 0
        await context.definition.release_run(run_id)
        await context.definition.release_run(run_id)
        # Release does not close the borrowed runtime or artifact repository.
        assert (await context.runtime.get_state(run_id=run_id)) == result.state
