"""当前用途统计、安全停止原因和额外未知候选的完成边界。"""

import json
import copy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.candidate_research import (
    CandidateResearchArtifact, CandidateResearchV3Artifact, CandidateResearchService,
    candidate_supply_status,
)
from tests.unit.itinerary_planning.test_service_initial import _upstream
from tests.unit.candidate_research.test_service_initial import _request, _multi_place_search
from tests.support.candidate_research_fakes import FakeRuntime, FakeRepository, FakeBudgetManager
from tests.support.demo_workflow_fakes import research_boundaries


def blocked_supply(*, data=None, assessed=False, unsuitable=False, exhausted=False, empty=False):
    data = copy.deepcopy(data) if data is not None else _upstream(uuid4())[3].model_dump(mode="json")
    state = data["record"]["state"]
    for candidate in state["candidates"]:
        candidate["roles"][0].update(verdict="contradicted", reason_code="provider_category_contradicts")
        candidate["meal_intents"] = []
        for item in candidate["meal_assessments"]:
            item.update(verdict="no_match" if unsuitable else "unresolved",
                        reason_code="business_hours_conflicts" if unsuitable else "evidence_insufficient")
    if empty:
        state["candidates"] = []
    counts = dict(state["counts"], attraction_count=0, restaurant_count=0,
                  unique_eligible_count=0, admitted_slots=len(state["candidates"]))
    coverage = dict(state["coverage"], details_success_count=0, details_target=0)
    gap = dict(gap_type="candidate_count_shortfall", gap_id="gap:attraction",
               evidence_indexes=[], role="attraction", current_count=0, target_count=2)
    state.update(counts=counts, coverage=coverage, pending_gaps=[gap])
    data["record"].update(counts=counts, coverage=coverage, gaps=[gap],
                          stop_reasons=["iteration_limit_reached" if exhausted else "no_legal_action"])
    if exhausted:
        budgets = dict(state["budgets"], iterations_consumed=6)
        state["budgets"] = data["record"]["budgets"] = budgets
    if assessed:
        data["qualification_state"]["assessments"] = [
            dict(candidate_ordinal=candidate["admission_ordinal"], kind="meal", target=meal,
                 evidence_indexes=[0], evidence_digest="sha256:" + "a" * 64, verdict="unresolved")
            for candidate in state["candidates"] for meal in ("lunch", "dinner")]
    return CandidateResearchArtifact.model_validate_json(json.dumps(data), strict=True)


@pytest.mark.parametrize("options,expected", [
    ({"empty": True}, "research_no_candidates"),
    ({}, "research_judgments_unfinished"),
    ({"assessed": True}, "research_evidence_insufficient"),
    ({"unsuitable": True}, "research_candidates_unsuitable"),
    ({"exhausted": True}, "research_budget_exhausted"),
    ({"assessed": True, "exhausted": True}, "research_budget_exhausted"),
    ({"unsuitable": True, "exhausted": True}, "research_candidates_unsuitable"),
])
def test_zero_supply_uses_fixed_reason_and_keeps_unknown_distinct(options, expected):
    artifact = blocked_supply(**options)
    assert candidate_supply_status(artifact) == expected
    assert artifact.record.counts.unique_eligible_count == artifact.record.counts.restaurant_count == 0
    assert artifact.record.coverage.details_target == 0


def test_current_supply_rejects_role_only_counts_while_v3_keeps_its_original_shape():
    data = blocked_supply().model_dump(mode="json")
    for counts in (data["record"]["counts"], data["record"]["state"]["counts"]):
        counts.update(restaurant_count=2, unique_eligible_count=2)
    for coverage in (data["record"]["coverage"], data["record"]["state"]["coverage"]):
        coverage["details_target"] = 1
    with pytest.raises(ValidationError, match="role count|candidate count"):
        CandidateResearchArtifact.model_validate_json(json.dumps(data), strict=True)
    data["schema_version"] = "3.0"
    data.pop("qualification_state")
    legacy = CandidateResearchV3Artifact.model_validate_json(json.dumps(data), strict=True)
    assert legacy.record.counts.restaurant_count == 2
    assert legacy.model_dump(mode="json") == data


async def test_enough_qualified_supply_completes_with_an_unused_unknown_candidate():
    request, hotel_ref, hotel, _ = _request(uuid4(), place_width=8)
    model, maps = research_boundaries()
    maps.responses[4] = _multi_place_search(("a0", "unused"), category="museum")
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel)
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps,
        artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "completed", getattr(result, "failure", None)
        artifact = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
        assert artifact.record.state.candidates[-1].state.value == "withheld"
        assert result.counts.unique_eligible_count == 10
        assert result.counts.admitted_slots == 11
        assert (model.call_count, maps.call_count) == (4, 16)
        assert candidate_supply_status(artifact) is None
    finally:
        await service.aclose()


@pytest.mark.parametrize("directory,version", [
    ("model_action_refs", "1.0"), ("model_judgment_refs", "1.0"), ("model_judgment_refs", "2.0"),
])
async def test_current_candidate_set_rejects_legacy_model_reference_versions(directory, version):
    request, hotel_ref, hotel, _ = _request(uuid4(), place_width=8)
    model, maps = research_boundaries()
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel)
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps,
        artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "completed"
        artifact = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
        data = artifact.model_dump(mode="json")
        data["record"]["state"][directory][0]["schema_version"] = version
        with pytest.raises(ValidationError, match="current candidate model reference versions incompatible"):
            CandidateResearchArtifact.model_validate_json(json.dumps(data), strict=True)
    finally:
        await service.aclose()
