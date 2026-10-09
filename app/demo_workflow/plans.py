"""Stable operation identities and shared phase budgets (design §6)."""
from uuid import UUID
from datetime import timezone

from app.candidate_research import (
    CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY, CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY,
    CandidateResearchOperationPlan, ResearchIterationOperationIds,
)
from app.hotel_selection import HOTEL_PLACE_CALL_BUDGET_KEY, HotelCandidateOperationIds
from app.itinerary_planning import (
    ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY, ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
    ItineraryPlanningOperationPlan, PlanningInvocationOperationIds,
)
from app.requirement_subgraph import REQUIREMENTS_CLARIFICATION_ROUND_KEY
from app.requirements import ConfirmedTravelRequirement
from app.workflow_contracts import (
    OperationId, WorkflowPhase, ExecutionBudgetKey, ExecutionBudgetScope,
    ExecutionBudgetPlan, ExecutionBudgetLimit, TravelSessionState,
)

from .errors import DemoWorkflowUsageError
from .commands import canonical, digest


RESULT_WRITE_KEY = ExecutionBudgetKey(
    scope=ExecutionBudgetScope.PHASE, name="demo.result_write", phase=WorkflowPhase.OUTPUT_FORMATTING,
)
BUDGETED_PHASES = (
    WorkflowPhase.REQUIREMENTS, WorkflowPhase.HOTEL_SELECTION, WorkflowPhase.CANDIDATE_RESEARCH,
    WorkflowPhase.ITINERARY_PLANNING, WorkflowPhase.OUTPUT_FORMATTING,
)


def planning_request_digest(request):
    """M09 §15 request identity, using only public request DTO fields."""
    def reference(ref):
        tree = ref.model_dump(mode="json")
        tree["created_at"] = ref.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return tree
    def model_hash(model):
        return digest(canonical(model.model_dump(mode="json")))
    fields = dict(capability_scope="module09-format-recovery-v1", schema_version=request.schema_version,
        run_id=str(request.operation_plan.run_id), operation_plan=model_hash(request.operation_plan))
    if hasattr(request, "confirmed"):
        fields.update(confirmed=model_hash(request.confirmed), hotel_selection_ref=reference(request.hotel_selection_ref),
            candidate_set_ref=reference(request.candidate_set_ref))
        version, kind = "planning-fp-v6", "request_initial"
    else:
        fields.update(predecessor_planning_ref=reference(request.predecessor_planning_ref),
            supplemented_candidate_set_ref=reference(request.supplemented_candidate_set_ref),
            supplemental_operation_plan=model_hash(request.supplemental_operation_plan))
        version, kind = "planning-resume-fp-v7", "request_resume"
    return digest(canonical(dict(fingerprint_version=version, kind=kind, fields=fields)))


def bounded(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise DemoWorkflowUsageError()
    return value


def operation(run_id, phase, step):
    if type(run_id) is not UUID:
        raise DemoWorkflowUsageError()
    return OperationId(run_id=run_id, phase=phase, logical_step=step)


def hotel_plan(run_id):
    phase = WorkflowPhase.HOTEL_SELECTION
    return HotelCandidateOperationIds(
        search_operation_ids=tuple(operation(run_id, phase, f"hotel_search_{i}") for i in range(3)),
        candidate_set_operation_id=operation(run_id, phase, "hotel_candidates"),
    )


def research_plan(run_id, call=0, remaining_iterations=6, remaining_places=48):
    bounded(call, 0, 5)
    bounded(remaining_iterations, 0, 6)
    bounded(remaining_places, 0, 48)
    phase = WorkflowPhase.CANDIDATE_RESEARCH
    slots = tuple(ResearchIterationOperationIds(
        invocation_slot_index=i,
        action_operation_id=operation(run_id, phase, f"research_{call}_action_{i}"),
        judgment_operation_id=operation(run_id, phase, f"research_{call}_judgment_{i}"),
        candidate_set_operation_id=operation(run_id, phase, f"research_{call}_state_{i}"),
        place_observation_operation_ids=tuple(
            operation(run_id, phase, f"research_{call}_place_{i}_{j}")
            for j in range(min(8, max(0, remaining_places - 8 * i)))
        ),
    ) for i in range(remaining_iterations))
    return CandidateResearchOperationPlan(
        run_id=run_id, iteration_slots=slots,
        terminal_successor_operation_id=operation(run_id, phase, f"research_{call}_terminal"),
    )


def planning_plan(run_id, days, call=0, consumed_invocations=0, remaining_directions=None):
    bounded(days, 1, 60)
    bounded(call, 0, 5)
    bounded(consumed_invocations, 0, 19)
    if remaining_directions is None:
        remaining_directions = 8 * days + 2
    bounded(remaining_directions, 0, 8 * days + 2)
    phase = WorkflowPhase.ITINERARY_PLANNING
    def op(step):
        return operation(run_id, phase, f"planning_{call}_{step}")
    return ItineraryPlanningOperationPlan(
        run_id=run_id,
        invocation_slots=tuple(PlanningInvocationOperationIds(
            invocation_slot_index=i, action_operation_id=op(f"action_{i}"),
            planning_state_operation_id=op(f"state_{i}"),
        ) for i in range(consumed_invocations, 19)),
        hub_geocode_operation_ids=tuple(op(f"hub_{j}") for j in range(2)),
        matrix_operation_ids=tuple(op(f"matrix_{j}") for j in range(min(17, 19 - consumed_invocations) * days)),
        direction_operation_ids=tuple(op(f"direction_{j}") for j in range(remaining_directions)),
        terminal_successor_operation_id=op("terminal"),
    )


def days_for(state):
    if type(state) is not TravelSessionState or type(state.requirement_outcome) is not ConfirmedTravelRequirement:
        raise DemoWorkflowUsageError()
    return bounded(state.requirement_outcome.requirement.time_window.sightseeing_days, 1, 60)


class DemoBudgetPlanProvider:
    @staticmethod
    def _plan(run_id, values):
        if type(run_id) is not UUID:
            raise DemoWorkflowUsageError()
        return ExecutionBudgetPlan(schema_version="1.0", run_id=run_id, limits=tuple(
            ExecutionBudgetLimit(key=key, limit=limit) for key, limit in values
        ))

    def plan_for_start(self, *, run_id):
        return self._plan(run_id, ((REQUIREMENTS_CLARIFICATION_ROUND_KEY, 6),))

    def plan_for_phase(self, *, run_id, phase, state):
        if type(state) is not TravelSessionState or state.run_id != run_id or state.phase is not phase:
            raise DemoWorkflowUsageError()
        # Validate a fresh snapshot without mutating the caller's nested domain objects.
        state = TravelSessionState.model_validate_json(state.model_dump_json(), strict=True)
        days = days_for(state)
        if phase is WorkflowPhase.HOTEL_SELECTION:
            values = ((HOTEL_PLACE_CALL_BUDGET_KEY, 3),)
        elif phase is WorkflowPhase.CANDIDATE_RESEARCH:
            values = ((CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY, 6), (CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY, 48))
        elif phase is WorkflowPhase.ITINERARY_PLANNING:
            values = ((ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY, 19), (ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY, 8 * days + 2))
        elif phase is WorkflowPhase.OUTPUT_FORMATTING:
            values = ((RESULT_WRITE_KEY, days + 1),)
        else:
            raise DemoWorkflowUsageError()
        return self._plan(run_id, values)
