"""RF-SOURCE/RF-ROUTE: source-bound projections, without service I/O."""

from datetime import timedelta

import pytest

from app.result_formatting.projection import ProjectionFailure, project_result
from tests.unit.result_formatting.fakes import (
    SOURCE_TIME,
    START_DATE,
    WRITE_TIME,
    make_scenario,
    replace_itinerary,
    tree,
)


def project(scenario):
    return project_result(scenario.request, scenario.itinerary, scenario.evidence)


@pytest.mark.asyncio
async def test_saved_sources_preserve_facts_and_distinguish_route_estimate(scenario):
    core, days = project(scenario)

    assert core.requirement.destination == "杭州"
    assert core.requirement.budget_total_cny == 10000.5
    assert core.requirement.children_ages == (8,)
    assert core.requirement.room_type_requirements == "家庭房"
    assert core.requirement.transport_preferences == "少换乘"
    assert core.effective_pace == "标准"
    assert core.weather_status == "not_collected"
    assert core.map_status == "deferred"
    assert core.cost_status == "unverified"
    assert days[0].day_no == 1 and days[0].date == START_DATE
    assert [place.kind for place in core.places[:3]] == ["hotel", "arrival_hub", "departure_hub"]
    assert core.places[1].uid is None and core.places[2].uid is None
    assert core.places[1].name == "杭州东站"
    assert core.places[1].address == "合成arrival枢纽地址"
    assert all(source.retrieved_at_utc == SOURCE_TIME for source in core.sources)
    assert SOURCE_TIME != WRITE_TIME
    assert all(source.verified is True for source in core.sources)
    assert {source.capability for source in core.sources} == {
        "place_search", "place_details", "geocoding", "directions", "route_matrix",
    }
    route = days[0].route_legs[0]
    assert (route.distance_m, route.duration_s, route.scheduled_minutes) == (1201, 601, 30)
    assert route.interval.end_at_utc - route.interval.start_at_utc == timedelta(minutes=30)
    assert core.departure_transfer.processing_minutes == 120
    assert core.departure_transfer.approximate_extra_minutes == 30


@pytest.mark.asyncio
async def test_combination_visit_and_asymmetric_backup_distance_remain_attached(scenario):
    core, days = project(scenario)
    day = days[0]
    assert [visit.visit_kind for visit in day.visits] == ["attraction_lunch", "attraction", "dinner"]
    assert len(day.visits) == 3
    assert [item.backup_kind for item in day.backups] == ["attraction", "lunch", "dinner"]
    assert [item.distance_m for item in day.backups] == [402, 403, 504]
    assert day.backups[0].core_visit_id == day.backups[1].core_visit_id == day.visits[0].visit_id
    assert day.backups[2].core_visit_id == day.visits[2].visit_id
    assert {item.place_id for item in day.backups}.isdisjoint(visit.place_id for visit in day.visits)
    assert core.unknown_business_visit_count == 2
    assert core.unknown_business_backup_count == 3
    place = next(place for place in core.places if place.place_id == day.visits[0].place_id)
    assert place.business_hours == ("09:00-21:00",)
    assert place.business_hours_source_id in day.visits[0].business_source_ids
    assert all(visit.requires_verification for visit in day.visits[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [1, 3])
async def test_projection_keeps_day_order_and_is_deterministic(days):
    scenario = await make_scenario(days=days)
    first = project(scenario)
    second = project(scenario)
    assert first == second
    core, day_data = first
    assert len(day_data) == days == core.requirement.sightseeing_days
    assert tuple(item.day_no for item in day_data) == tuple(range(1, days + 1))
    assert tuple(item.date for item in day_data) == tuple(START_DATE + timedelta(days=i) for i in range(days))
    assert tuple(source.source_id for source in core.sources) == tuple(range(1, len(core.sources) + 1))
    assert len({source.artifact_ref for source in core.sources}) == len(core.sources)


@pytest.mark.asyncio
async def test_utc_cross_midnight_and_zero_duration_are_preserved():
    crossing = await make_scenario(cross_midnight=True)
    _, crossing_days = project(crossing)
    original = crossing.itinerary.record.draft.days[0].route_legs[-1].interval
    actual = crossing_days[0].route_legs[-1].interval
    assert actual.start_at_utc == original.start_at_utc
    assert actual.end_at_utc == original.end_at_utc
    assert actual.start_at_utc.date() == START_DATE
    assert original.local_start_date == START_DATE + timedelta(days=1)

    zero = await make_scenario(zero_route=True)
    _, zero_days = project(zero)
    leg = zero_days[0].route_legs[-1]
    assert (leg.distance_m, leg.duration_s, leg.scheduled_minutes) == (0, 0, 0)
    assert leg.interval.start_at_utc == leg.interval.end_at_utc


@pytest.mark.asyncio
async def test_transit_only_does_not_invent_visits_or_candidate_sources():
    scenario = await make_scenario(transit_only=True)
    core, days = project(scenario)
    assert days[0].day_kind == "transit_only"
    assert days[0].visits == days[0].route_legs == days[0].backups == ()
    assert len(core.places) == 3
    assert all(source.capability != "place_details" for source in core.sources)
    assert (core.unknown_business_visit_count, core.unknown_business_backup_count) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["requirement", "main_route", "backup_direction", "alternative"])
async def test_structurally_valid_saved_source_mismatches_fail(scenario, mismatch):
    payload = tree(scenario.itinerary)
    draft = payload["record"]["draft"]
    if mismatch == "requirement":
        draft["requirement_digest"] = "sha256:" + "0" * 64
    elif mismatch == "main_route":
        draft["arrival_transfer"]["route_leg"]["distance_m"] += 1
    elif mismatch == "backup_direction":
        # 600 is backup->core; the required core->backup distance is 402.
        draft["days"][0]["backups"][0]["distance_m"] = 600
    else:
        draft["days"][0]["route_legs"][0]["alternative_index"] = 1
    changed = await replace_itinerary(scenario, payload)
    with pytest.raises(ProjectionFailure) as failure:
        project(changed)
    assert failure.value.code == "source_mismatch"
