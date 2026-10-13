"""新任务的网页问题与持久命令版本保持一致，旧状态仍可还原。"""

from datetime import date
from uuid import uuid4

import pytest

from app.demo_workflow.commands import DemoCommandCodec
from app.demo_workflow.models import DemoStartCommand
from app.requirements import NeedClarification, RequirementDomainState, RequirementIntent, RequirementTurnInput, TravelRequirement
from app.web_application.presentation import interaction_card
from app.workflow_contracts import parse_travel_session_state_json, serialize_travel_session_state


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["1.0", "1.1"])
async def test_initial_command_and_clarification_preserve_collection_version(version):
    turn = RequirementTurnInput(turn_id=uuid4(), user_message="我想去杭州", session_date=date(2026, 10, 13),
        intent=RequirementIntent.INITIAL, current=RequirementDomainState(schema_version=version))
    command = DemoStartCommand(schema_version="1.0", kind="start", turn=turn)
    codec = DemoCommandCodec(max_payload_bytes=1_048_576)
    run, trace = uuid4(), uuid4()
    encoded = codec.encode_initial(run_id=run, trace_id=trace, command=command)
    assert codec.decode_initial(run_id=run, trace_id=trace, encoded=encoded) == command
    state = codec.initial_state(run_id=run, trace_id=trace, decoded_command=command)
    raw = serialize_travel_session_state(state)
    assert serialize_travel_session_state(parse_travel_session_state_json(raw)) == raw
    domain = RequirementDomainState(schema_version=version, requirement=TravelRequirement(destination="杭州"))
    outcome = NeedClarification(requirement=domain.requirement, metadata=domain.metadata, issues=(), message="请补充旅行信息")
    state = type(state).model_validate({**state.model_dump(mode="python"), "requirement_state": domain, "requirement_outcome": outcome}, strict=True)
    card = await interaction_card(state=state, repository=None)
    fields = {question.field.value for question in card.questions}
    assert "time_window.arrival.hub_name" in fields and "preferences.interests" in fields
    if version == "1.1":
        assert not any(field.startswith(("party.", "rooms.", "constraints.")) for field in fields)
    else:
        assert {"party.adults_count", "rooms.room_count", "constraints.dietary_restrictions"} <= fields
