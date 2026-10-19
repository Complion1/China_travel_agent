"""主图读取当前持久化候选集，准确输出零供给的安全终态。"""

from types import SimpleNamespace
import json

import pytest

from app.candidate_research import CandidateResearchIncomplete
from app.demo_workflow.terminal import changed
from app.workflow_contracts import ArtifactWriteRequest, WorkflowPhase, TravelSessionState
from tests.support.demo_workflow_fakes import hotel_nodes
from tests.unit.candidate_research.test_qualification_supply import blocked_supply, current_supply_fixture
from tests.unit.itinerary_planning.test_service_initial import _upstream


@pytest.mark.parametrize("options,code", [
    ({"empty": True}, "research_no_candidates"),
    ({}, "research_judgments_unfinished"),
    ({"assessed": True}, "research_evidence_insufficient"),
    ({"unsuitable": True}, "research_candidates_unsuitable"),
    ({"exhausted": True}, "research_budget_exhausted"),
])
async def test_persisted_candidate_status_routes_to_registered_terminal(options, code):
    nodes, state, _, _ = await hotel_nodes()
    cap = nodes.cap("research")
    hotel_ref, candidate_ref, hotel, source = _upstream(state.run_id)
    artifact = current_supply_fixture(blocked_supply(data=source.model_dump(mode="json"), **options))
    ref = await cap.artifact_repository.put(ArtifactWriteRequest(
        operation_id=candidate_ref.operation_id, artifact_type=candidate_ref.artifact_type,
        schema_version="5.0", content=artifact.model_dump(mode="json")))
    record = artifact.record
    result = CandidateResearchIncomplete.model_validate_json(
        json.dumps(dict(candidate_set_ref=ref.model_dump(mode="json"),
        counts=record.counts.model_dump(mode="json"), coverage=record.coverage.model_dump(mode="json"),
        budgets=record.budgets.model_dump(mode="json"), gaps=[item.model_dump(mode="json") for item in record.gaps],
        stop_reasons=[item.value for item in record.stop_reasons])), strict=True)
    async def research(request):
        return result
    nodes.dependencies.research_factory = lambda caps: SimpleNamespace(research_initial=research)
    state = await nodes.receipt(state, cap, last_outcome="hotel_selected", hotel_selection_ref=hotel_ref)
    command = await nodes.research(changed(state, phase=WorkflowPhase.CANDIDATE_RESEARCH))
    assert command.goto == "terminal"
    final = TravelSessionState.model_validate(command.update, strict=True)
    terminal = await nodes.terminal(final)
    assert terminal.update["terminal_code"] == code
    assert not any(item.artifact_type.startswith("itinerary_planning.") for item in final.artifact_refs)
