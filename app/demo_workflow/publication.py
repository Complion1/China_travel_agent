"""One journal-protected result write per root node."""
from langgraph.graph import END

from app.durable_execution import DurableExecutionDependencyError, DurableExecutionStateError
from app.result_formatting import (
    ResultFormattingService, ResultFormattingRequest, ResultFormattingFailed,
    ResultFormattingPrepared, ResultIndexReady, ResultDayArtifact, ResultIndexArtifact,
    ResultFormattingFailureCode, build_result_artifact_write_request,
)
from app.workflow_contracts import (
    ArtifactFound, ArtifactAbsent, ArtifactTooLargeError, BudgetConsumed,
    BudgetConsumeRequest, OperationResultIndeterminateError, WorkflowPhase,
)
from .nodes import goto, with_refs
from .plans import days_for, operation, RESULT_WRITE_KEY
from .terminal import terminal_state


def formatting_failure(state, failure):
    code = failure.code
    if code is ResultFormattingFailureCode.UNSAFE_TEXT:
        return goto(terminal_state(state, "workflow_output_failed"), END)
    if code is ResultFormattingFailureCode.ARTIFACT_DEPENDENCY_ERROR:
        raise DurableExecutionDependencyError()
    if code is ResultFormattingFailureCode.RESULT_TOO_LARGE:
        raise ArtifactTooLargeError()
    raise DurableExecutionStateError()


async def persist(*, state, cap, artifact, step, diagnostics):
    request = build_result_artifact_write_request(
        operation_id=operation(state.run_id, WorkflowPhase.OUTPUT_FORMATTING, step), artifact=artifact)
    found = await cap.artifact_repository.lookup(operation_id=request.operation_id)
    if type(found) is ArtifactFound:
        saved = await cap.artifact_repository.get(found.ref, expected_model=type(artifact))
        if saved.content != artifact:
            raise DurableExecutionStateError()
        diagnostics._emit(state.run_id, "artifact.reused", state.phase, operation_id=request.operation_id)
        return found.ref
    if type(found) is not ArtifactAbsent:
        raise DurableExecutionStateError()
    consumed = await cap.execution_budget_manager.consume(BudgetConsumeRequest(
        operation_id=request.operation_id, key=RESULT_WRITE_KEY, amount=1))
    if type(consumed) is not BudgetConsumed:
        raise DurableExecutionStateError()
    if consumed.disposition != "applied":
        raise OperationResultIndeterminateError()
    return await cap.artifact_repository.put(request)


def day_node(nodes, number):
    async def publish(state):
        if not 1 <= number <= days_for(state):
            raise DurableExecutionStateError()
        cap = nodes.cap(f"publish_day_{number}")
        await cap.ensure_phase_budget(state=state)
        control, _ = await nodes.control(state, cap)
        prepared = await ResultFormattingService(artifact_repository=cap.read_only_artifact_repository).prepare(
            ResultFormattingRequest(schema_version="1.0", state=state, itinerary_ref=control.planning_ref))
        if type(prepared) is ResultFormattingFailed:
            return formatting_failure(state, prepared)
        if type(prepared) is not ResultFormattingPrepared or len(prepared.days) != days_for(state):
            raise DurableExecutionStateError()
        artifact = prepared.days[number - 1]
        if artifact.data.day_no != number:
            raise DurableExecutionStateError()
        ref = await persist(state=state, cap=cap, artifact=artifact, step=f"result_day_{number}", diagnostics=nodes.dependencies.diagnostics)
        return goto(with_refs(state, ref), f"publish_day_{number + 1}" if number < days_for(state) else "publish_index")
    return publish


def index_node(nodes):
    async def publish(state):
        cap = nodes.cap("publish_index")
        await cap.ensure_phase_budget(state=state)
        control, _ = await nodes.control(state, cap)
        by_step = {ref.operation_id.logical_step: ref for ref in state.artifact_refs if ref.artifact_type == "result_formatting.day"}
        try:
            day_refs = tuple(by_step[f"result_day_{day}"] for day in range(1, days_for(state) + 1))
        except KeyError:
            raise DurableExecutionStateError() from None
        result = await ResultFormattingService(artifact_repository=cap.read_only_artifact_repository).build_index(
            ResultFormattingRequest(schema_version="1.0", state=state, itinerary_ref=control.planning_ref), day_refs=day_refs)
        if type(result) is ResultFormattingFailed:
            return formatting_failure(state, result)
        if type(result) is not ResultIndexReady:
            raise DurableExecutionStateError()
        ref = await persist(state=state, cap=cap, artifact=result.index, step="result_index", diagnostics=nodes.dependencies.diagnostics)
        return goto(with_refs(state, ref), "terminal")
    return publish
