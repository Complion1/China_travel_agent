"""Spawn entry point: exchange credentials only through an anonymous Pipe."""
import asyncio
import os


def worker(connection, request):
    try:
        connection.send(asyncio.run(execute(request)))
    except BaseException as error:
        connection.send({"error": type(error).__name__})
        raise
    finally:
        connection.close()


async def execute(request):
    from app.durable_execution import load_durable_execution_settings
    from app.demo_workflow import DemoHotelCommand
    from app.demo_workflow.artifacts import DemoControlArtifact
    from app.hotel_selection import HotelSelectCommand
    from app.result_formatting import ResultFormattingService, ResultReadRequest, ResultFormattingLoaded
    from tests.support.demo_workflow_fakes import full_runtime
    async with full_runtime(load_durable_execution_settings()) as context:
        if request["mode"] == "recover_blocked":
            from app.durable_execution import DurableExecutionRunBlockedError, DurableExecutionStateError
            from app.workflow_contracts import OperationResultIndeterminateError
            try:
                await context.runtime.recover(run_id=request["run_id"])
            except (DurableExecutionRunBlockedError,DurableExecutionStateError,OperationResultIndeterminateError):
                pass
            else:
                raise AssertionError("private context loss must not restart business work")
            assert (await context.runtime.get_run(run_id=request["run_id"])).activity == "blocked"
            return dict(pid=os.getpid(),extract_calls=len(context.providers.extractor.calls),
                hotel_calls=context.providers.hotel_map.search_call_count,research_calls=context.providers.research_model.call_count,
                planning_calls=context.providers.planning_model.call_count)
        state = await context.runtime.get_state(run_id=request["run_id"])
        if request["mode"] == "read_research":
            from app.candidate_research import (
                CandidateResearchArtifact, CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,
                validate_qualification_sources,
            )
            ref = max((r for r in state.artifact_refs if r.artifact_type == "demo.control"), key=lambda r: r.operation_id.logical_step)
            control = (await context.artifacts.repository.get(ref, expected_model=DemoControlArtifact)).content
            saved = (await context.artifacts.repository.get(control.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
            assert saved.record.record_type == "incomplete" and not saved.record.state.candidates
            assert saved.record.budgets.corrections_consumed == 2
            models = {(item.artifact_type, item.schema_version): item.model for item in CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS}
            sources = {}
            for source in (*saved.invocation_attempt_refs, *saved.record.state.model_action_refs,
                           *saved.record.state.model_judgment_refs, *saved.record.state.observation_refs):
                sources[source.artifact_id] = await context.artifacts.repository.get(source, expected_model=models[source.artifact_type, source.schema_version])
            validate_qualification_sources(saved, sources)
        if request["mode"] == "read_diagnostic":
            from app.itinerary_planning import ItineraryPlanningArtifact
            ref = max((r for r in state.artifact_refs if r.artifact_type == "demo.control"),key=lambda r:r.operation_id.logical_step)
            control = (await context.artifacts.repository.get(ref,expected_model=DemoControlArtifact)).content
            saved = (await context.artifacts.repository.get(control.planning_ref,expected_model=ItineraryPlanningArtifact)).content
            assert saved.record.record_type == "incomplete" and saved.record.draft is None
            assert saved.record.gaps and saved.record.stop_reasons
            assert not any(r.artifact_type.startswith("result_formatting.") for r in state.artifact_refs)
        if request["mode"] == "hotel_resume":
            ref = max((r for r in state.artifact_refs if r.artifact_type == "demo.control"),key=lambda r:r.operation_id.logical_step)
            control = (await context.artifacts.repository.get(ref,expected_model=DemoControlArtifact)).content
            result = await context.runtime.resume(run_id=state.run_id,pause_token=request["token"],
                command=DemoHotelCommand(schema_version="1.0",kind="hotel",candidate_set_ref=control.hotel_candidate_ref,
                    action=HotelSelectCommand(selected_uid="invalid")))
            state = result.state
        if request["mode"] == "read_result":
            index = next(r for r in state.artifact_refs if r.artifact_type == "result_formatting.index")
            loaded = await ResultFormattingService(artifact_repository=context.artifacts.repository).load(
                ResultReadRequest(schema_version="2.0",state=state,index_ref=index))
            assert type(loaded) is ResultFormattingLoaded
            budgets = await context.runtime.get_terminal_budget_snapshots(run_id=state.run_id)
            summary = await context.dependencies.diagnostics.finish(state=state,budget_snapshots=budgets)
            assert summary.summary is None and summary.unavailable_reason == "process_history_unavailable"
        return dict(pid=os.getpid(),state=state.model_dump_json(),extract_calls=len(context.providers.extractor.calls),
            hotel_calls=context.providers.hotel_map.search_call_count,research_calls=context.providers.research_model.call_count,
            planning_calls=context.providers.planning_model.call_count)


async def invoke_worker(request):
    import multiprocessing
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=worker,args=(child,request))
    process.start()
    child.close()
    try:
        if not await asyncio.to_thread(parent.poll, 45):
            raise AssertionError("worker timed out")
        result = parent.recv()
        await asyncio.to_thread(process.join, 5)
        assert process.exitcode == 0 and "error" not in result, result
        assert result["pid"] != os.getpid()
        return result
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)
        process.close()
