"""07-3 重放与预算合同：公共端口不得携带隐式运行时。"""

from __future__ import annotations

import inspect
from uuid import UUID

import pytest

from app.hotel_selection import (
    HOTEL_PLACE_CALL_BUDGET_KEY,
    HotelCandidateSelectionPort,
    HotelCandidateSelectionService,
)
from app.hotel_selection.query_policy import build_queries, hash_request
from app.workflow_contracts import BudgetConsumeRequest, WorkflowPhase
from tests.support.hotel_selection_fakes import (
    confirmed_requirement,
    make_service,
    map_success,
    operation_ids,
    place,
)


def test_port_and_constructor_are_explicit_and_minimal() -> None:
    discover = inspect.signature(HotelCandidateSelectionPort.discover_candidates)
    select = inspect.signature(HotelCandidateSelectionPort.record_selection)
    constructor = inspect.signature(HotelCandidateSelectionService.__init__)
    assert tuple(discover.parameters) == ("self", "confirmed", "operation_ids")
    assert tuple(select.parameters) == ("self", "request", "active_candidate_set_ref")
    assert select.parameters["active_candidate_set_ref"].kind is inspect.Parameter.KEYWORD_ONLY
    assert tuple(constructor.parameters) == (
        "self",
        "adapter",
        "repository",
        "budget_manager",
        "utc_clock",
    )


def test_only_frozen_hotel_place_budget_key_is_used() -> None:
    assert HOTEL_PLACE_CALL_BUDGET_KEY.name == "hotel.place_call"
    assert HOTEL_PLACE_CALL_BUDGET_KEY.phase is WorkflowPhase.HOTEL_SELECTION
    assert "agent" not in repr(HOTEL_PLACE_CALL_BUDGET_KEY).lower()


@pytest.mark.asyncio
async def test_each_new_search_consumes_once_and_replay_does_not_call_adapter() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000027")
    service, adapter, _, budget, _ = make_service(
        (map_success(place("a")), map_success(place("b")), map_success(place("c"))),
        run_id=run_id,
    )
    ids = operation_ids(run_id)
    await service.discover_candidates(confirmed_requirement(), ids)
    consumed = await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)
    assert consumed.consumed == 3
    await service.discover_candidates(confirmed_requirement(), ids)
    assert adapter.search_call_count == 3


def test_budget_request_carries_operation_and_single_frozen_key() -> None:
    operation_id = operation_ids(UUID("00000000-0000-0000-0000-000000000028")).search_operation_ids[0]
    request = BudgetConsumeRequest(
        operation_id=operation_id,
        key=HOTEL_PLACE_CALL_BUDGET_KEY,
        amount=1,
    )
    assert request.amount == 1
    assert request.key == HOTEL_PLACE_CALL_BUDGET_KEY
