from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.baidu_map import (
    DEFAULT_BAIDU_MAP_LIMITS,
    BaiduMapRuntime,
    BaiduMapSettings,
    GeocodeRequest,
)
from app.baidu_map._mcp_gateway import BaiduMapMcpGateway
from app.requirement_subgraph import (
    RequirementConfirmationResume,
    RequirementResumeEnvelope,
    RequirementSubgraphConfirmed,
    RequirementSubgraphPaused,
    create_demo_requirement_subgraph_runtime,
)
from app.requirements import (
    ConfirmedTravelRequirement,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainService,
    RequirementDomainState,
    RequirementDomainTransition,
    RequirementIntent,
    RequirementTurnInput,
    TravelRequirement,
)
from app.workflow_contracts import DeliveryStatus, TravelSessionState, WorkflowPhase
from tests.support.baidu_map_fake_mcp import FakeMcpEnvironment, structured_result


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 20, tzinfo=timezone.utc)


class DeterministicRequirementDomainService(RequirementDomainService):
    """No extractor/network: emits one confirmation pause and one confirmed state."""

    def __init__(self, destination: str) -> None:
        self.destination = destination
        self.process_calls = 0
        self.confirm_calls = 0

    def _state(self, status: str) -> RequirementDomainState:
        return RequirementDomainState(
            requirement=TravelRequirement(destination=self.destination),
            confirmation_status=status,
        )

    async def process_turn(self, input: RequirementTurnInput) -> RequirementDomainTransition:
        assert input.current == RequirementDomainState()
        self.process_calls += 1
        state = self._state("awaiting_confirmation")
        return RequirementDomainTransition(
            state=state,
            outcome=NeedConfirmation(
                requirement=state.requirement,
                metadata=state.metadata,
                summary="请确认目的地",
            ),
        )

    async def confirm(
        self, state: RequirementDomainState, action: RequirementConfirmation
    ) -> RequirementDomainTransition:
        assert state.requirement.destination == self.destination
        assert action.decision == "accept"
        self.confirm_calls += 1
        confirmed = self._state("confirmed")
        return RequirementDomainTransition(
            state=confirmed,
            outcome=ConfirmedTravelRequirement(
                schema_version="1.0",
                requirement=confirmed.requirement,
                metadata_without_raw_evidence=confirmed.metadata,
                confirmation_summary_digest="offline-confirmed-digest",
            ),
        )


def _initial_state() -> TravelSessionState:
    return TravelSessionState(
        schema_version="2.0",
        run_id=uuid4(),
        trace_id=uuid4(),
        phase=WorkflowPhase.REQUIREMENTS,
        delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=RequirementDomainState(),
        requirement_outcome=None,
        artifact_refs=(),
    )


@pytest.mark.asyncio
async def test_m2_confirmed_destination_performs_exactly_one_typed_geocode() -> None:
    destination = "杭州市"
    domain = DeterministicRequirementDomainService(destination)
    requirements_runtime = create_demo_requirement_subgraph_runtime(domain_service=domain)
    initial = _initial_state()
    turn = RequirementTurnInput(
        turn_id=uuid4(),
        user_message=f"去{destination}",
        session_date=date(2026, 8, 20),
        timezone="Asia/Shanghai",
        intent=RequirementIntent.INITIAL,
        current=initial.requirement_state,
    )
    paused = await requirements_runtime.start(state=initial, turn=turn)
    assert type(paused) is RequirementSubgraphPaused
    confirmed = await requirements_runtime.resume(
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
    assert confirmed_destination == destination

    environment = FakeMcpEnvironment()
    environment.set_result(
        "map_geocode",
        structured_result(
            {"status": 0, "result": {"location": {"lat": 30.25, "lng": 120.16}}}
        ),
    )
    gateway = await BaiduMapMcpGateway.create(
        settings=BaiduMapSettings(api_key=SecretStr("offline-only")),
        clock=FixedClock(),
        limits=DEFAULT_BAIDU_MAP_LIMITS,
        _client_factory=environment.factory,
    )
    map_runtime = BaiduMapRuntime(_gateway=gateway)
    outcome = await map_runtime.adapter.geocode(GeocodeRequest(address=confirmed_destination))
    assert outcome.result.formatted_address == destination
    assert environment.calls == [
        ("map_geocode", {"address": destination, "is_chinese_mainland": "true"})
    ]
    assert domain.process_calls == 1
    assert domain.confirm_calls == 1
    assert confirmed.state.artifact_refs == ()
    assert confirmed.state.delivery_status is DeliveryStatus.IN_PROGRESS
    await map_runtime.aclose()
