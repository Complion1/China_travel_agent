"""Serial orchestration of existing domain ports, without a second validator."""
from dataclasses import replace

from langgraph.graph import END
from langgraph.types import Command, interrupt
from pydantic import TypeAdapter

from app.durable_execution import DurableExecutionStateError, DurableExecutionDependencyError
from app.workflow_contracts import (
    ArtifactFound, ArtifactWriteRequest, DeliveryStatus, WorkflowPhase,
    OperationResultIndeterminateError,
)
from app.requirements import ConfirmedTravelRequirement, RequirementExtractionFailed
from app.result_formatting import ResultIndexArtifact
from app.hotel_selection import (
    HotelCandidateDiscoveryOutcome, HotelSelectionOutcome, HotelSelectionRequest,
    HotelSelectionRejectionCode,
)
from app.candidate_research import (
    CandidateResearchArtifact, CandidateResearchInitialRequest, CandidateResearchSupplementalRequest,
    CandidateResearchOutcome, CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY, CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY,
    candidate_supply_status,
)
from app.itinerary_planning import (
    ItineraryPlanningArtifact, ItineraryPlanningInitialRequest, ItineraryPlanningResumeRequest,
    ItineraryPlanningOutcome, ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY, ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
)

from .artifacts import DemoControlArtifact
from .commands import canonical, digest
from .models import DemoHotelCommand, DemoRequirementCommand, fresh
from .plans import operation, hotel_plan, research_plan, planning_plan, days_for, planning_request_digest
from .supplement import restore_gaps, research_gaps
from .terminal import changed, terminal_state
from .errors import DemoWorkflowUsageError


def goto(state, node):
    return Command(update={name: getattr(state, name) for name in type(state).model_fields}, goto=node)


def with_refs(state, *refs):
    values = {ref.operation_id: ref for ref in state.artifact_refs}
    for ref in refs:
        if ref is None:
            continue
        if ref.operation_id.run_id != state.run_id or (ref.operation_id in values and values[ref.operation_id] != ref):
            raise DurableExecutionStateError()
        values[ref.operation_id] = ref
    order = {phase: i for i, phase in enumerate(WorkflowPhase)}
    return changed(state, artifact_refs=tuple(sorted(values.values(), key=lambda r: (order[r.operation_id.phase], r.operation_id.logical_step))))


def outcome(value, expected):
    adapter = TypeAdapter(expected)
    adapter.validate_python(value.model_dump(mode="python"), strict=True)
    return adapter.validate_json(value.model_dump_json(), strict=True)


def failure_code(failure):
    kind = failure.failure_type
    if kind in {"llm", "llm_failure"}:
        if failure.error.code.value == "quota_exhausted":
            return "workflow_budget_exhausted"
        if failure.error.code.value in {"configuration_error", "capability_unavailable", "schema_unsupported", "input_too_large"}:
            return "workflow_contract_failed"
        if failure.error.code.value in {"structured_output_invalid", "tool_arguments_invalid", "unknown_tool",
                "output_truncated", "malformed_provider_response"}:
            return "workflow_model_output_invalid"
        return "workflow_provider_failed"
    if kind in {"map", "map_failure"}:
        return "workflow_map_failed"
    code = failure.code.value
    if code == "operation_result_indeterminate":
        raise OperationResultIndeterminateError()
    if code in {"artifact_error", "persistence_error", "budget_error"}:
        raise DurableExecutionDependencyError()
    if code == "budget_exhausted":
        return "workflow_budget_exhausted"
    if code in {"input_invalid", "invalid_input"}:
        return "requirement_failed"
    research_codes = {
        "context_contract_error": "research_context_invalid",
        "action_correction_exhausted": "research_correction_exhausted",
        "judgment_patch_invalid": "research_judgment_invalid",
        "judgment_required_missing": "research_recovery_exhausted",
        "state_invariant_failed": "research_state_invalid",
        "replay_mismatch": "workflow_replay_mismatch",
    }
    return research_codes.get(code, "workflow_contract_failed")


class Nodes:
    def __init__(self, dependencies, caps, services):
        self.dependencies, self.caps, self.services = dependencies, caps, services

    def cap(self, name):
        return self.caps.for_node(node_name=name)

    def service(self, name, run_id):
        key = (run_id, name)
        if key not in self.services:
            factory = (self.dependencies.hotel_factory if name.startswith("hotel_") else
                self.dependencies.research_factory if name == "research" else self.dependencies.planning_factory)
            self.services[key] = factory(self.cap(name))
        return self.services[key]

    async def control(self, state, cap):
        refs = sorted((r for r in state.artifact_refs if r.artifact_type == "demo.control"), key=lambda r: r.operation_id.logical_step)
        previous = None
        current = None
        for number, ref in enumerate(refs):
            value = (await cap.read_only_artifact_repository.get(ref, expected_model=DemoControlArtifact)).content
            if value.schema_version != ref.schema_version or value.sequence != number or value.previous_ref != previous or ref.operation_id.logical_step != f"control_{number:02d}":
                raise DurableExecutionStateError()
            if current is not None and (
                value.map_requested != current.map_requested
                or value.hotel_invalid_count < current.hotel_invalid_count
                or value.hotel_invalid_count > current.hotel_invalid_count + 1
                or value.supplement_count < current.supplement_count
                or value.supplement_count > current.supplement_count + 1
                or value.handled_gap_fingerprints[:current.supplement_count] != current.handled_gap_fingerprints
            ):
                raise DurableExecutionStateError()
            previous, current = ref, value
        if current is None:
            raise DurableExecutionStateError()
        return current, previous

    async def receipt(self, state, cap, *, prior=None, prior_ref=None, **updates):
        if prior is None:
            command = cap.current_command()
            values = dict(schema_version="2.0", sequence=0, previous_ref=None, hotel_invalid_count=0,
                map_requested=type(command) is DemoRequirementCommand and command.map_requested,
                supplement_count=0, handled_gap_fingerprints=(), hotel_candidate_ref=None,
                hotel_selection_ref=None, candidate_set_ref=None, planning_ref=None,
                last_outcome="hotel_ready", terminal_code=None, supplemental_plan_call=None)
        else:
            values = {key: getattr(prior, key) for key in DemoControlArtifact.model_fields}
            values.update(sequence=prior.sequence + 1, previous_ref=prior_ref)
        control = DemoControlArtifact.model_validate(values | updates, strict=True)
        ref = await cap.artifact_repository.put(ArtifactWriteRequest(
            operation_id=operation(state.run_id, state.phase, f"control_{control.sequence:02d}"),
            artifact_type="demo.control", schema_version="2.0", content=control.model_dump(mode="json")))
        return with_refs(state, ref, control.hotel_candidate_ref, control.hotel_selection_ref, control.candidate_set_ref, control.planning_ref)

    async def after_requirements(self, state):
        if type(state.requirement_outcome) is ConfirmedTravelRequirement:
            return goto(state, "prepare_hotel")
        code = "requirement_failed"
        if state.terminal_code == "budget_exhausted":
            code = "workflow_budget_exhausted"
        if type(state.requirement_outcome) is RequirementExtractionFailed:
            code = "workflow_provider_failed"
        return goto(terminal_state(state, code), "terminal")

    def prepare(self, phase, next_node):
        def enter(state):
            return goto(changed(state, phase=phase, delivery_status=DeliveryStatus.IN_PROGRESS), next_node)
        return enter

    async def hotel_discover(self, state):
        cap = self.cap("hotel_discover")
        await cap.ensure_phase_budget(state=state)
        value = outcome(await self.service("hotel_discover", state.run_id).discover_candidates(
            state.requirement_outcome, hotel_plan(state.run_id)), HotelCandidateDiscoveryOutcome)
        if value.outcome_type == "ready":
            state = await self.receipt(state, cap, hotel_candidate_ref=value.candidate_set_ref)
            return goto(changed(state, delivery_status=DeliveryStatus.AWAITING_USER), "hotel_wait")
        if value.outcome_type == "insufficient":
            code, ref = "hotel_candidates_insufficient", value.candidate_set_ref
        else:
            code = "workflow_budget_exhausted" if value.reason.value == "budget_exhausted" else "workflow_map_failed"
            ref = None
            state = with_refs(state, value.failure_search_ref)
        state = await self.receipt(state, cap, hotel_candidate_ref=ref, last_outcome="failed", terminal_code=code)
        return goto(state, "terminal")

    async def hotel_wait(self, state):
        interrupt({"kind": "hotel", "schema_version": "1.0"})
        self.dependencies.diagnostics._resume_accepted(state)
        return goto(changed(state, delivery_status=DeliveryStatus.IN_PROGRESS), "hotel_select")

    async def hotel_select(self, state):
        cap = self.cap("hotel_select")
        control, ref = await self.control(state, cap)
        command = cap.current_command()
        if type(command) is not DemoHotelCommand:
            raise DurableExecutionStateError()
        value = outcome(await self.service("hotel_select", state.run_id).record_selection(
            HotelSelectionRequest(selection_operation_id=operation(state.run_id, state.phase,
                f"hotel_selection_{control.hotel_invalid_count}"), candidate_set_ref=command.candidate_set_ref,
                command=command.action), active_candidate_set_ref=control.hotel_candidate_ref), HotelSelectionOutcome)
        values = dict(prior=control, prior_ref=ref)
        if value.outcome_type == "selected":
            state = await self.receipt(state, cap, **values, hotel_selection_ref=value.selection_ref, last_outcome="hotel_selected")
            return goto(state, "prepare_research")
        if value.outcome_type == "declined":
            state = await self.receipt(state, cap, **values, hotel_selection_ref=value.selection_ref,
                last_outcome="hotel_declined", terminal_code="cancelled_by_user")
            return goto(state, "terminal")
        if value.reason is HotelSelectionRejectionCode.CANDIDATE_UID_NOT_FOUND:
            count = control.hotel_invalid_count + 1
            state = await self.receipt(state, cap, **values, hotel_invalid_count=count,
                last_outcome="failed" if count == 3 else "hotel_invalid",
                terminal_code="hotel_input_exhausted" if count == 3 else None)
            if count < 3:
                return goto(changed(state, delivery_status=DeliveryStatus.AWAITING_USER), "hotel_wait")
        else:
            state = await self.receipt(state, cap, **values, last_outcome="failed", terminal_code="hotel_selection_rejected")
        return goto(state, "terminal")

    async def supplemental_request(self, state, cap, control):
        planning = (await cap.read_only_artifact_repository.get(control.planning_ref, expected_model=ItineraryPlanningArtifact)).content
        if planning.schema_version != control.planning_ref.schema_version or planning.record.record_type != "awaiting_candidate_supplement":
            raise DurableExecutionStateError()
        predecessor = planning.directories.candidate_set_refs[-1]
        prior = (await cap.read_only_artifact_repository.get(predecessor, expected_model=CandidateResearchArtifact)).content
        budgets = prior.record.budgets
        plan = research_plan(state.run_id, control.supplement_count, 6 - budgets.iterations_consumed, 48 - budgets.place_calls_consumed)
        return CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=predecessor,
            gaps=research_gaps(restore_gaps(planning)), operation_plan=plan)

    async def research(self, state):
        cap = self.cap("research")
        await cap.ensure_phase_budget(state=state)
        control, ref = await self.control(state, cap)
        service = self.service("research", state.run_id)
        if control.supplement_count == 0:
            value = await service.research_initial(CandidateResearchInitialRequest(
                confirmed=state.requirement_outcome, hotel_selection_ref=control.hotel_selection_ref,
                operation_plan=research_plan(state.run_id)))
        else:
            request = await self.supplemental_request(state, cap, control)
            value = await service.research_supplemental(request)
        value = outcome(value, CandidateResearchOutcome)
        if value.outcome_type == "failed":
            state = with_refs(state, value.last_consistent_candidate_set_ref, value.failure_artifact_ref)
            state = await self.receipt(state, cap, prior=control, prior_ref=ref, last_outcome="failed", terminal_code=failure_code(value.failure))
            return goto(state, "terminal")
        saved = (await cap.read_only_artifact_repository.get(
            value.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
        if (saved.schema_version != "5.0" or saved.run_id != state.run_id
                or saved.record.record_type not in {"completed", "incomplete"}
                or saved.record.counts.model_dump(mode="python") != value.counts.model_dump(mode="python")):
            raise DurableExecutionStateError()
        code = candidate_supply_status(saved)
        usable = code is None
        state = await self.receipt(state, cap, prior=control, prior_ref=ref, candidate_set_ref=value.candidate_set_ref,
            last_outcome="research_ready" if usable else "failed", terminal_code=code,
            supplemental_plan_call=control.supplement_count or None)
        return goto(state, "prepare_planning" if usable else "terminal")

    async def planning(self, state):
        cap = self.cap("planning")
        await cap.ensure_phase_budget(state=state)
        control, ref = await self.control(state, cap)
        iterations = await cap.execution_budget_manager.get_snapshot(key=ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY)
        directions = await cap.execution_budget_manager.get_snapshot(key=ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY)
        plan = planning_plan(state.run_id, days_for(state), control.supplement_count, iterations.consumed, directions.remaining)
        service = self.service("planning", state.run_id)
        if control.supplement_count == 0:
            request = ItineraryPlanningInitialRequest(confirmed=state.requirement_outcome,
                hotel_selection_ref=control.hotel_selection_ref, candidate_set_ref=control.candidate_set_ref, operation_plan=plan)
            value = await service.plan_initial(request)
        else:
            supplemental = await self.supplemental_request(state, cap, control)
            request = ItineraryPlanningResumeRequest(predecessor_planning_ref=control.planning_ref,
                supplemented_candidate_set_ref=control.candidate_set_ref, operation_plan=plan,
                supplemental_operation_plan=supplemental.operation_plan)
            value = await service.plan_resume(request)
        value = outcome(value, ItineraryPlanningOutcome)
        values = dict(prior=control, prior_ref=ref)
        if value.outcome_type == "failed":
            state = with_refs(state, value.last_consistent_itinerary_ref, value.failure_artifact_ref)
            state = await self.receipt(state, cap, **values, last_outcome="failed", terminal_code=failure_code(value.failure))
            return goto(state, "terminal")
        if value.outcome_type == "candidate_supplement_requested":
            saved = (await cap.read_only_artifact_repository.get(value.planning_ref, expected_model=ItineraryPlanningArtifact)).content
            if saved.schema_version != value.planning_ref.schema_version or saved.record.record_type != "awaiting_candidate_supplement" or saved.record.gap_fingerprint != value.gap_fingerprint or restore_gaps(saved) != value.gaps or saved.directories.candidate_set_refs[-1] != value.candidate_set_ref:
                raise DurableExecutionStateError()
            if control.supplement_count >= 5 or value.gap_fingerprint in control.handled_gap_fingerprints:
                state = await self.receipt(state, cap, **values, planning_ref=value.planning_ref, last_outcome="failed", terminal_code="planning_incomplete")
                return goto(state, "terminal")
            research_gaps(value.gaps)
            state = await self.receipt(state, cap, **values, planning_ref=value.planning_ref,
                last_outcome="supplement_required", supplement_count=control.supplement_count + 1,
                handled_gap_fingerprints=(*control.handled_gap_fingerprints, value.gap_fingerprint))
            return goto(state, "prepare_research")
        planning_ref = value.itinerary_ref
        if planning_ref is None:
            found = await cap.read_only_artifact_repository.lookup(operation_id=plan.terminal_successor_operation_id)
            if type(found) is not ArtifactFound:
                raise DurableExecutionStateError()
            planning_ref = found.ref
            saved = (await cap.read_only_artifact_repository.get(planning_ref, expected_model=ItineraryPlanningArtifact)).content
            if (planning_ref.operation_id != plan.terminal_successor_operation_id
                or planning_ref.artifact_type != "itinerary_planning.itinerary" or planning_ref.schema_version != "8.0" or saved.schema_version != planning_ref.schema_version
                or saved.request_digest != planning_request_digest(request)
                or saved.record.record_type != "incomplete" or saved.record.stop_reasons != value.stop_reasons
                or restore_gaps(saved) != value.gaps or saved.directories.candidate_set_refs[-1] != control.candidate_set_ref
                or saved.directories.hotel_selection_refs != (control.hotel_selection_ref,)):
                raise DurableExecutionStateError()
        complete = value.outcome_type == "completed"
        state = await self.receipt(state, cap, **values, planning_ref=planning_ref,
            last_outcome="planning_ready" if complete else "failed", terminal_code=None if complete else "planning_incomplete")
        return goto(state, "prepare_output" if complete else "terminal")

    async def terminal(self, state):
        if state.terminal_code is not None:
            return goto(terminal_state(state, state.terminal_code), END)
        control, _ = await self.control(state, self.cap("terminal"))
        if control.terminal_code is None:
            refs = [ref for ref in state.artifact_refs if ref.artifact_type == "result_formatting.index"]
            if control.last_outcome != "planning_ready" or len(refs) != 1:
                raise DurableExecutionStateError()
            index = (await self.cap("terminal").read_only_artifact_repository.get(refs[0], expected_model=ResultIndexArtifact)).content
            if index.source_itinerary_ref != control.planning_ref or any(day.ref not in state.artifact_refs for day in index.days):
                raise DurableExecutionStateError()
            code = "workflow_degraded" if index.core.unknown_business_visit_count > 0 or getattr(index.core, "quality_issue_count", 0) > 0 or control.map_requested else "workflow_completed"
            return goto(terminal_state(state, code), END)
        return goto(terminal_state(state, control.terminal_code), END)
