"""07-4 选择合同：只接受 ready 候选集并输出稳定决策。"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.hotel_selection import (
    HotelDeclineCommand,
    HotelSelected,
    HotelSelectCommand,
    HotelSelectionRejectionCode,
    HotelSelectionRequest,
)
from tests.support.hotel_selection_fakes import (
    confirmed_requirement,
    make_no_result,
    make_service,
    map_success,
    operation_ids,
    place,
    selection_operation,
)


RUN_ID = UUID("00000000-0000-0000-0000-000000000037")


@pytest.mark.asyncio
async def test_selection_contract_binds_command_to_active_candidate_ref() -> None:
    service, _, _, _, _, candidate_ref = await _ready()
    request = HotelSelectionRequest(
        selection_operation_id=selection_operation(RUN_ID, "contract"),
        candidate_set_ref=candidate_ref,
        command=HotelSelectCommand(selected_uid="a"),
    )
    outcome = await service.record_selection(request, active_candidate_set_ref=candidate_ref)
    assert isinstance(outcome, HotelSelected)
    assert outcome.selection_ref.operation_id == request.selection_operation_id
    assert outcome.selected_candidate.uid == "a"


@pytest.mark.asyncio
async def test_decline_contract_is_typed_and_has_no_candidate_payload() -> None:
    service, _, _, _, _, candidate_ref = await _ready()
    request = HotelSelectionRequest(
        selection_operation_id=selection_operation(RUN_ID, "contract_decline"),
        candidate_set_ref=candidate_ref,
        command=HotelDeclineCommand(),
    )
    outcome = await service.record_selection(request, active_candidate_set_ref=candidate_ref)
    assert outcome.outcome_type == "declined"
    assert not hasattr(outcome, "selected_candidate")


@pytest.mark.asyncio
async def test_not_ready_candidate_set_cannot_be_selected() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000038")
    service, _, _, _, _ = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=run_id
    )
    discovery = await service.discover_candidates(confirmed_requirement(), operation_ids(run_id))
    request = HotelSelectionRequest(
        selection_operation_id=selection_operation(run_id, "not_ready_contract"),
        candidate_set_ref=discovery.candidate_set_ref,
        command=HotelDeclineCommand(),
    )
    outcome = await service.record_selection(
        request, active_candidate_set_ref=discovery.candidate_set_ref
    )
    assert outcome.reason is HotelSelectionRejectionCode.CANDIDATE_SET_NOT_READY


async def _ready():
    service, adapter, repository, budget, clock = make_service(
        (map_success(place("a", index=0), place("b", index=1), place("c", index=2)),),
        run_id=RUN_ID,
    )
    discovery = await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    return service, adapter, repository, budget, clock, discovery.candidate_set_ref
