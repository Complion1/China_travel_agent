import pytest

from app.workflow_contracts import TravelSessionState
from app.demo_workflow.artifacts import DemoControlArtifact
from tests.support.demo_workflow_fakes import full_runtime, start_to_pause
from tests.support.demo_workflow_worker import invoke_worker


@pytest.mark.asyncio
@pytest.mark.parametrize("hotel", [False, True])
async def test_real_process_reads_pause_and_resumes_native_hotel(durable_execution_settings, hotel):
    async with full_runtime(durable_execution_settings) as context:
        paused = await start_to_pause(context,hotel=hotel)
        await context.runtime.close()
        response = await invoke_worker(dict(run_id=paused.state.run_id,token=paused.pause.pause_token,
            mode="hotel_resume" if hotel else "read_pause"))
        state = TravelSessionState.model_validate_json(response["state"],strict=True)
        assert all(response[key] == 0 for key in ("extract_calls","hotel_calls","research_calls","planning_calls"))
        if hotel:
            assert set(paused.state.artifact_refs).issubset(state.artifact_refs)
            ref = max((r for r in state.artifact_refs if r.artifact_type == "demo.control"),key=lambda r:r.operation_id.logical_step)
            control = (await context.artifacts.repository.get(ref,expected_model=DemoControlArtifact)).content
            assert control.hotel_invalid_count == 1
            assert state.requirement_outcome == paused.state.requirement_outcome
        else:
            assert state == paused.state


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["research", "planning"])
async def test_private_context_loss_blocks_cross_process_rerun(durable_execution_settings,monkeypatch,phase):
    from app.candidate_research import CandidateResearchService, CandidateResearchArtifact
    from app.itinerary_planning import ItineraryPlanningService, ItineraryPlanningArtifact
    from app.durable_execution import DurableExecutionStateError
    from tests.support.demo_workflow_fakes import select_hotel
    service, method = (CandidateResearchService,"research_initial") if phase == "research" else (ItineraryPlanningService,"plan_initial")
    original = getattr(service,method)
    saved = []
    async def interrupted(self,request):
        result = await original(self,request)
        saved.append(result.candidate_set_ref if phase == "research" else result.itinerary_ref)
        raise DurableExecutionStateError()
    async with full_runtime(durable_execution_settings) as context:
        paused = await start_to_pause(context,hotel=True)
        with monkeypatch.context() as patch:
            patch.setattr(service,method,interrupted)
            with pytest.raises(DurableExecutionStateError):
                await select_hotel(context,paused)
        assert len(saved) == 1
        artifact = await context.artifacts.repository.get(saved[0],expected_model=CandidateResearchArtifact if phase == "research" else ItineraryPlanningArtifact)
        assert artifact.ref == saved[0]
        assert context.providers.research_model.call_count == 4
        assert context.providers.planning_model.call_count == (3 if phase == "planning" else 0)
        await context.runtime.close()
        result = await invoke_worker(dict(run_id=paused.state.run_id,mode="recover_blocked"))
        assert all(result[key] == 0 for key in ("extract_calls","hotel_calls","research_calls","planning_calls"))


@pytest.mark.asyncio
async def test_no_draft_diagnostic_is_readable_in_new_process(durable_execution_settings):
    from tests.support.demo_workflow_fakes import select_hotel
    from app.durable_execution import DurableExecutionTerminal
    async with full_runtime(durable_execution_settings) as context:
        context.providers.planning_model.stop_without_draft = True
        paused = await start_to_pause(context,hotel=True)
        result = await select_hotel(context,paused)
        assert type(result) is DurableExecutionTerminal
        assert result.state.terminal_code == "planning_incomplete"
        assert context.providers.planning_model.call_count == 1
        await context.runtime.close()
        response = await invoke_worker(dict(run_id=paused.state.run_id,mode="read_diagnostic"))
        assert TravelSessionState.model_validate_json(response["state"],strict=True) == result.state
        assert all(response[key] == 0 for key in ("extract_calls","hotel_calls","research_calls","planning_calls"))
