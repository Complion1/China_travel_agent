"""使用实际 M08 产物验证规划入口的来源边界。"""
import json
from uuid import UUID

import pytest

from app.candidate_research import (
    CandidateResearchService, CandidateResearchV4Artifact,
    CandidateResearchV2ModelActionArtifact, CandidateResearchV3ModelJudgmentArtifact,
)
from app.candidate_research.fingerprints import content_digest, canonical_json_bytes
from app.itinerary_planning import ItineraryPlanningService
from app.llm_runtime import ToolTurnCompleted
from tests.support.candidate_research_fakes import FakeRuntime, FakeBudgetManager
from tests.support.demo_workflow_fakes import research_boundaries
from tests.unit.candidate_research.test_inline_correction import audited
from tests.unit.candidate_research.test_service_initial import _request
from tests.unit.itinerary_planning.test_service_initial import _fixture, _metadata


def historical_v4(artifact, sources):
    """无纠错的真实来源按独立旧模型生成只读版本4测试数据。"""
    transformed = {}

    def refs(value):
        if isinstance(value, list):
            return [refs(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "artifact_id" in value and "operation_id" in value:
            identity = UUID(value["artifact_id"])
            if identity in sources and not sources[identity].ref.artifact_type.endswith("invocation_attempt"):
                return source(identity)[0].model_dump(mode="json")
        return {key: refs(item) for key, item in value.items()}

    def source(identity):
        if identity in transformed:
            return transformed[identity]
        saved = sources[identity]
        value = saved.content.model_dump(mode="json")
        model = type(saved.content)
        if saved.ref.artifact_type.endswith("model_action"):
            model = CandidateResearchV2ModelActionArtifact
            value.update(schema_version="2.0")
            value.pop("attempt_refs")
        elif saved.ref.artifact_type.endswith("model_judgment"):
            model = CandidateResearchV3ModelJudgmentArtifact
            value.update(schema_version="3.0")
            value.pop("attempt_refs")
        content = model.model_validate_json(json.dumps(refs(value)), strict=True)
        ref = saved.ref.model_copy(update={"schema_version": content.schema_version,
            "content_hash": content_digest(content), "size_bytes": len(canonical_json_bytes(content))})
        transformed[identity] = ref, content
        return ref, content

    value = artifact.model_dump(mode="json")
    value.update(schema_version="4.0")
    value.pop("invocation_attempt_refs")
    for budget in (value["record"]["budgets"], value["record"]["state"]["budgets"]):
        budget.pop("correction_limit")
        budget.pop("corrections_consumed")
    old = CandidateResearchV4Artifact.model_validate_json(json.dumps(refs(value)), strict=True)
    return old, list(transformed.values())


@pytest.mark.parametrize("version", ["4.0", "5.0"])
@pytest.mark.parametrize("omit_sources", [False, True])
async def test_initial_public_entry_verifies_real_research_before_external_calls(version, omit_sources):
    unit_service, request, _, planning_model, repo = _fixture(ToolTurnCompleted(final_text="done", metadata=_metadata()))
    planner = ItineraryPlanningService(runtime=unit_service._runtime, map_adapter=unit_service._map_adapter,
        artifact_repository=repo, budget_manager=unit_service._budget)
    initial, *_ = _request(request.operation_plan.run_id, place_width=8)
    initial = initial.model_copy(update={"confirmed": request.confirmed, "hotel_selection_ref": request.hotel_selection_ref})
    model, maps = research_boundaries()
    researcher = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps,
        artifact_repository=repo, budget_manager=FakeBudgetManager(initial.operation_plan.run_id))
    try:
        outcome = await researcher.research_initial(initial)
        assert outcome.outcome_type == "completed", getattr(outcome, "failure", None)
        artifact, sources = await audited(repo, outcome.candidate_set_ref)
        if version == "4.0":
            artifact, old_sources = historical_v4(artifact, sources)
            for ref, content in old_sources:
                repo.seed(ref, content)
        if omit_sources:
            value = artifact.model_dump(mode="json")
            if version == "5.0":
                value["invocation_attempt_refs"] = []
            else:
                value["record"]["state"]["model_action_refs"] = []
                value["qualification_state"]["steps"] = []
            artifact = type(artifact).model_validate_json(json.dumps(value), strict=True)
        ref = outcome.candidate_set_ref.model_copy(update={"schema_version": version,
            "content_hash": content_digest(artifact), "size_bytes": len(canonical_json_bytes(artifact))})
        repo.seed(ref, artifact)
        result = await planner.plan_initial(request.model_copy(update={"candidate_set_ref": ref}))
        if omit_sources:
            assert result.outcome_type == "failed"
            assert planning_model.call_count == 0
        else:
            assert result.outcome_type == "incomplete", getattr(result, "failure", None)
            assert planning_model.call_count == 1
        assert not planner._map_adapter.geocode_calls
        assert not planner._map_adapter.matrix_calls
        assert not planner._map_adapter.directions_calls
    finally:
        await researcher.aclose()
        await planner.aclose()