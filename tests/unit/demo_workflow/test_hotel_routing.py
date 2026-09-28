import pytest

from app.demo_workflow import DemoHotelCommand
from app.demo_workflow.nodes import changed
from app.hotel_selection import HotelSelectCommand, HotelDeclineCommand
from app.workflow_contracts import DeliveryStatus, TravelSessionState
from tests.support.demo_workflow_fakes import hotel_nodes


def state_of(command):
    return TravelSessionState.model_validate(command.update, strict=True)


@pytest.mark.asyncio
async def test_three_submitted_unknown_uids_never_search_again():
    nodes, state, command, adapter = await hotel_nodes()
    discovered = await nodes.hotel_discover(state)
    assert discovered.goto == "hotel_wait"
    state = state_of(discovered)
    control, _ = await nodes.control(state, nodes.cap("hotel_select"))
    for count in (1, 2, 3):
        command[0] = DemoHotelCommand(schema_version="1.0", kind="hotel",
            candidate_set_ref=control.hotel_candidate_ref, action=HotelSelectCommand(selected_uid="missing"))
        result = await nodes.hotel_select(changed(state, delivery_status=DeliveryStatus.IN_PROGRESS))
        state = state_of(result)
        control, _ = await nodes.control(state, nodes.cap("hotel_select"))
        assert control.hotel_invalid_count == count
        assert result.goto == ("terminal" if count == 3 else "hotel_wait")
    terminal = state_of(await nodes.terminal(state))
    assert terminal.terminal_code == "hotel_input_exhausted"
    assert adapter.search_call_count == 1
    assert (await nodes.cap("hotel_select").execution_budget_manager.get_all_snapshots())[0].consumed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decline", [True, False])
async def test_real_hotel_selection_or_decline_is_persisted(decline):
    nodes, state, command, adapter = await hotel_nodes()
    state = state_of(await nodes.hotel_discover(state))
    control, _ = await nodes.control(state, nodes.cap("hotel_select"))
    command[0] = DemoHotelCommand(schema_version="1.0", kind="hotel", candidate_set_ref=control.hotel_candidate_ref,
        action=HotelDeclineCommand() if decline else HotelSelectCommand(selected_uid="hotel1"))
    result = await nodes.hotel_select(changed(state, delivery_status=DeliveryStatus.IN_PROGRESS))
    state = state_of(result)
    control, _ = await nodes.control(state, nodes.cap("hotel_select"))
    assert control.hotel_selection_ref in state.artifact_refs
    assert result.goto == ("terminal" if decline else "prepare_research")
    if decline:
        assert state_of(await nodes.terminal(state)).delivery_status is DeliveryStatus.CANCELLED
    assert adapter.search_call_count == 1
