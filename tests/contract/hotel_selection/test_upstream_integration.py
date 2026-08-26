"""07-3 上游边界合同：只消费已批准的 typed adapter 能力。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.baidu_map import (
    BaiduMapAdapter,
    MapCapability,
    MapProvenance,
    PlaceSearchSuccess,
)
from app.hotel_selection import HotelCandidateSelectionService
from app.hotel_selection.query_policy import project_candidates
from app.hotel_selection.errors import HotelInputError
from tests.support.hotel_selection_fakes import (
    ScriptedBaiduMapAdapter,
    confirmed_requirement,
    make_no_result,
    make_service,
    map_success,
    operation_ids,
    place,
)


def test_fake_implements_the_complete_upstream_adapter_protocol() -> None:
    fake = ScriptedBaiduMapAdapter(())
    assert isinstance(fake, BaiduMapAdapter)
    assert tuple(
        name
        for name in (
            "geocode",
            "reverse_geocode",
            "search_places",
            "get_place_details",
            "calculate_route_matrix",
            "plan_directions",
            "get_weather",
        )
        if callable(getattr(fake, name, None))
    ) == (
        "geocode",
        "reverse_geocode",
        "search_places",
        "get_place_details",
        "calculate_route_matrix",
        "plan_directions",
        "get_weather",
    )


def test_place_search_outcome_is_consumed_as_typed_upstream_data() -> None:
    outcome = PlaceSearchSuccess(
        places=(place("typed"),),
        provenance=MapProvenance(
            capability=MapCapability.PLACE_SEARCH,
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            verified=True,
        ),
    )
    assert outcome.places[0].uid == "typed"
    projected = project_candidates(outcome.places)
    assert projected[0].uid == "typed"


def test_constructor_rejects_non_adapter_or_missing_workflow_dependencies() -> None:
    with pytest.raises(HotelInputError):
        HotelCandidateSelectionService(object(), object(), object(), object())


@pytest.mark.asyncio
async def test_service_does_not_require_environment_or_global_runtime() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000047")
    service, adapter, _, _, _ = make_service(
        (map_success(place("one")), make_no_result(), make_no_result()), run_id=run_id
    )
    await service.discover_candidates(confirmed_requirement(), operation_ids(run_id))
    assert service.__dict__.keys() == {"_adapter", "_repository", "_budget_manager", "_utc_clock"}
    assert adapter.call_counts == {
        "geocode": 0,
        "reverse_geocode": 0,
        "get_place_details": 0,
        "calculate_route_matrix": 0,
        "plan_directions": 0,
        "get_weather": 0,
    }
