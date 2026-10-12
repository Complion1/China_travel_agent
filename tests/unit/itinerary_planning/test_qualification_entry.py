"""零合格用途及伪造需求目录在规划外调之前处理。"""

import hashlib

from tests.unit.itinerary_planning.test_service_initial import _fixture
from tests.unit.candidate_research.test_qualification_supply import blocked_supply


async def test_zero_admitted_purposes_request_research_before_any_planning_call():
    service, request, runtime, model, repository = _fixture()
    original = repository.contents[request.candidate_set_ref.artifact_id]
    artifact = blocked_supply(data=original)
    _, payload = repository._encoded(artifact)
    ref = request.candidate_set_ref.model_copy(update=dict(
        content_hash="sha256:" + hashlib.sha256(payload).hexdigest(), size_bytes=len(payload)))
    repository.seed(ref, artifact)
    try:
        result = await service.plan_initial(request.model_copy(update={"candidate_set_ref": ref}))
        assert result.outcome_type == "candidate_supplement_requested", result
        assert result.budgets.model_invocations_consumed == result.budgets.direction_calls_consumed == 0
        assert model.call_count == 0
        assert not service._map_adapter.geocode_calls
        assert not service._map_adapter.matrix_calls
        assert not service._map_adapter.directions_calls
    finally:
        await service.aclose()


async def test_omitted_confirmed_avoid_directory_is_rejected_before_planning_calls():
    service, request, runtime, model, repository = _fixture()
    request.confirmed.requirement.preferences.avoid_places = ["合成避开要求"]
    try:
        result = await service.plan_initial(request)
        assert result.outcome_type == "failed"
        assert result.failure.code.value == "predecessor_invalid"
        assert model.call_count == 0
        assert not service._map_adapter.geocode_calls
        assert not service._map_adapter.matrix_calls
        assert not service._map_adapter.directions_calls
    finally:
        await service.aclose()
