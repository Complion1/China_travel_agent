"""LIVE-QUALIFICATION M2 probe; T05 only collects, without settings, Secret, or network access."""

from __future__ import annotations

from functools import wraps

import pytest


def _count_only(name: str):
    """Decorator for the live M2 boundary: count calls, never inspect content."""

    def decorate(method):
        @wraps(method)
        async def wrapped(self, *args, **kwargs):
            self.state.increment(name)
            return await method(self, *args, **kwargs)

        return wrapped

    return decorate


class _M2CallCounts:
    def __init__(self) -> None:
        self.values = {
            "geocode": 0,
            "reverse_geocode": 0,
            "search_places": 0,
            "get_place_details": 0,
            "calculate_route_matrix": 0,
            "plan_directions": 0,
            "get_weather": 0,
            "map_launch": 0,
        }

    def increment(self, name: str) -> None:
        self.values[name] += 1


class _CountingAdapter:
    def __init__(self, delegate, state: _M2CallCounts) -> None:
        self.delegate = delegate
        self.state = state

    @_count_only("geocode")
    async def geocode(self, request):
        return await self.delegate.geocode(request)

    @_count_only("reverse_geocode")
    async def reverse_geocode(self, request):
        return await self.delegate.reverse_geocode(request)

    @_count_only("search_places")
    async def search_places(self, request):
        return await self.delegate.search_places(request)

    @_count_only("get_place_details")
    async def get_place_details(self, request):
        return await self.delegate.get_place_details(request)

    @_count_only("calculate_route_matrix")
    async def calculate_route_matrix(self, request):
        return await self.delegate.calculate_route_matrix(request)

    @_count_only("plan_directions")
    async def plan_directions(self, request):
        return await self.delegate.plan_directions(request)

    @_count_only("get_weather")
    async def get_weather(self, request):
        return await self.delegate.get_weather(request)


class _CountingMapLaunch:
    def __init__(self, delegate, state: _M2CallCounts) -> None:
        self.delegate = delegate
        self.state = state

    def get_availability(self):
        return self.delegate.get_availability()

    @_count_only("map_launch")
    async def create_map_launch(self, request):
        return await self.delegate.create_map_launch(request)


@pytest.mark.asyncio
async def test_m2_confirmed_destination_one_live_geocode() -> None:
    from datetime import date
    from uuid import uuid4

    from app.baidu_map import (
        GeocodeRequest,
        GeocodeSuccess,
        MapOutcomeStatus,
        create_baidu_map_runtime,
        load_baidu_map_settings,
    )
    from app.requirement_subgraph import (
        RequirementConfirmationResume,
        RequirementResumeEnvelope,
        RequirementSubgraphConfirmed,
        RequirementSubgraphPaused,
        create_demo_requirement_subgraph_runtime,
    )
    from app.requirements import (
        RequirementConfirmation,
        RequirementIntent,
        RequirementTurnInput,
    )
    from app.workflow_contracts import DeliveryStatus, SystemUtcClock
    from tests.integration.baidu_map.test_m2_offline_vertical import (
        DeterministicRequirementDomainService,
        _initial_state,
    )

    domain = DeterministicRequirementDomainService("杭州市")
    requirement_runtime = create_demo_requirement_subgraph_runtime(domain_service=domain)
    initial = _initial_state()
    paused = await requirement_runtime.start(
        state=initial,
        turn=RequirementTurnInput(
            turn_id=uuid4(),
            user_message="去杭州市",
            session_date=date(2026, 8, 20),
            timezone="Asia/Shanghai",
            intent=RequirementIntent.INITIAL,
            current=initial.requirement_state,
        ),
    )
    assert type(paused) is RequirementSubgraphPaused
    confirmed = await requirement_runtime.resume(
        run_id=initial.run_id,
        envelope=RequirementResumeEnvelope(
            pause_token=paused.pause.pause_token,
            resume=RequirementConfirmationResume(
                action=RequirementConfirmation(decision="accept", revision_text=None)
            ),
        ),
    )
    assert type(confirmed) is RequirementSubgraphConfirmed
    confirmed_destination = confirmed.state.requirement_state.requirement.destination
    assert confirmed_destination is not None
    counts = _M2CallCounts()
    async with create_baidu_map_runtime(
        settings=load_baidu_map_settings(), clock=SystemUtcClock()
    ) as runtime:
        counted_adapter = _CountingAdapter(runtime.adapter, counts)
        counted_map_launch = _CountingMapLaunch(runtime.map_launch, counts)
        outcome = await counted_adapter.geocode(GeocodeRequest(address=confirmed_destination))
        assert type(outcome) is GeocodeSuccess
        assert outcome.status is MapOutcomeStatus.SUCCESS
    assert counts.values == {
        "geocode": 1,
        "reverse_geocode": 0,
        "search_places": 0,
        "get_place_details": 0,
        "calculate_route_matrix": 0,
        "plan_directions": 0,
        "get_weather": 0,
        "map_launch": 0,
    }
    assert confirmed.state.artifact_refs == ()
    assert confirmed.state.delivery_status is DeliveryStatus.IN_PROGRESS
