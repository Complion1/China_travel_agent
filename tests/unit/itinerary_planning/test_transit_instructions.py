from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.baidu_map import TravelMode
from app.itinerary_planning.artifacts import RouteAlternativeSummarySnapshot, TransitRouteAlternativeSummarySnapshot, _to_route_alternative_snapshot, _from_route_alternative_snapshot
from app.itinerary_planning.models import RouteAlternativeSummary, TransportSemantic
from app.itinerary_planning.tooling import _project_directions_observation
from tests.unit.itinerary_planning.test_map_execution import _setup, _spec


@pytest.mark.asyncio
async def test_transit_steps_survive_map_cache_and_artifact_conversion_but_not_llm_projection():
    run_id, key, adapter, budget, executor = _setup(2)
    spec = replace(_spec(run_id, 0), transport_semantic=TransportSemantic.PUBLIC_TRANSIT, route_mode=TravelMode.TRANSIT)
    result = await executor.plan_directions((spec,), direction_budget_key=key)
    observation = result.observations[0]
    summary = observation.alternatives[0]
    assert summary.transit_instructions
    snapshot = _to_route_alternative_snapshot(summary)
    restored = _from_route_alternative_snapshot(TransitRouteAlternativeSummarySnapshot.model_validate_json(snapshot.model_dump_json()))
    assert restored.transit_instructions == summary.transit_instructions
    assert "transit_instructions" not in str(_project_directions_observation(observation))
    assert summary.transit_instructions[0] not in repr(summary)
    replay = await executor.plan_directions((spec,), direction_budget_key=key)
    assert replay.observations[0].alternatives[0].transit_instructions == summary.transit_instructions
    assert len(adapter.directions_calls) == 1


def test_legacy_summary_canonical_json_is_unchanged_and_step_count_is_checked():
    legacy = '{"schema_version":"1.0","alternative_index":0,"distance_m":10,"duration_s":5,"step_count":1}'
    for model in (RouteAlternativeSummary, RouteAlternativeSummarySnapshot):
        assert model.model_validate_json(legacy).model_dump_json() == legacy
    for model, version in ((RouteAlternativeSummary, "1.0"), (TransitRouteAlternativeSummarySnapshot, "1.1")):
        for instructions in (("一", "二"), (" ",), ("x" * 1025,)):
            with pytest.raises(ValidationError):
                model(schema_version=version, alternative_index=0, distance_m=10, duration_s=5, step_count=1, transit_instructions=instructions)
