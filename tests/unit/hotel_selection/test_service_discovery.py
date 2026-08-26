"""07-3 候选发现：顺序、重放、预算、证据与身份安全。"""

from __future__ import annotations

import asyncio
from enum import Enum
from uuid import UUID

import pytest

from app.baidu_map import (
    BaiduMapAuthenticationError,
    BaiduMapError,
    BaiduMapConcurrencyError,
    BaiduMapConfigurationError,
    BaiduMapProtocolError,
    BaiduMapRateLimitError,
    BaiduMapRequestError,
    BaiduMapResponseError,
    BaiduMapSessionError,
    BaiduMapTimeoutError,
    BaiduMapToolSchemaError,
    BaiduMapToolUnavailableError,
    BaiduMapTransportError,
    MapCapability,
)
from app.hotel_selection import (
    HOTEL_PLACE_CALL_BUDGET_KEY,
    HotelCandidateDiscoveryFailureCode,
    HotelCandidatesInsufficient,
    HotelCandidatesReady,
    HotelInputError,
    HotelMapFailureCode,
    HotelReplayError,
)
from app.hotel_selection.artifacts import HotelDiscoveryArtifact, HotelPlaceSearchArtifact
from app.hotel_selection.models import HotelCandidateOperationIds
import app.hotel_selection.service as service_module
from app.hotel_selection.service import HotelCandidateSelectionService
from app.requirements import ConfirmedTravelRequirement
from app.workflow_contracts import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactConflictError,
    ArtifactIntegrityError,
    BudgetConsumed,
    OperationResultIndeterminateError,
    OperationId,
    WorkflowPhase,
)
from app.hotel_selection.errors import HotelCandidateIdentityConflictError
from tests.support.hotel_selection_fakes import (
    RecordingArtifactRepository,
    RecordingBudgetManager,
    SharedEventSink,
    confirmed_requirement,
    make_no_result,
    make_service,
    map_success,
    operation_ids,
    place,
)


RUN_ID = UUID("00000000-0000-0000-0000-000000000007")


class InvalidConfirmedRequirement(ConfirmedTravelRequirement):
    """子类用于证明 exact-type 入口拒绝，不污染字段序列化。"""


class UnknownMapCode(str, Enum):
    UNKNOWN = "unknown"


class UnknownMapError(BaiduMapError):
    code = UnknownMapCode.UNKNOWN


class TamperCandidateReadRepository(RecordingArtifactRepository):
    def __init__(self, delegate, operation_id: OperationId) -> None:
        super().__init__(delegate)
        self.operation_id = operation_id

    async def get(self, ref, *, expected_model):
        self.events.append(("get", ref.artifact_type))
        read = await self.delegate.get(ref, expected_model=expected_model)
        if expected_model is HotelDiscoveryArtifact:
            wrong_operation = OperationId(
                run_id=self.operation_id.run_id,
                phase=WorkflowPhase.HOTEL_SELECTION,
                logical_step="wrong_candidate_read",
            )
            return read.model_copy(update={"ref": read.ref.model_copy(update={"operation_id": wrong_operation})})
        return read


class TamperSearchReadRepository(RecordingArtifactRepository):
    def __init__(self, delegate, mode: str) -> None:
        super().__init__(delegate)
        self.mode = mode

    async def get(self, ref, *, expected_model):
        self.events.append(("get", ref.artifact_type))
        read = await self.delegate.get(ref, expected_model=expected_model)
        if expected_model is not HotelPlaceSearchArtifact:
            return read
        if self.mode == "none":
            return read
        if self.mode == "ordinal":
            return read.model_copy(update={"content": read.content.model_copy(update={"search_ordinal": 2})})
        if self.mode == "request_hash":
            return read.model_copy(update={"content": read.content.model_copy(update={"request_hash": "sha256:" + "1" * 64})})
        wrong_operation = OperationId(
            run_id=ref.operation_id.run_id,
            phase=WorkflowPhase.HOTEL_SELECTION,
            logical_step="wrong_search_read",
        )
        return read.model_copy(update={"ref": read.ref.model_copy(update={"operation_id": wrong_operation})})


async def _prepare_search_replay(mode: str, first_response: object):
    service, adapter, repository, budget, clock = make_service(
        (first_response, make_no_result(), make_no_result()), run_id=RUN_ID
    )
    recording = TamperSearchReadRepository(repository, mode)
    recording.suppress_candidate_set_puts = 1
    wrapped_budget = RecordingBudgetManager(budget)
    wrapped_service = HotelCandidateSelectionService(adapter, recording, wrapped_budget, clock)
    ids = operation_ids(RUN_ID)
    with pytest.raises(ArtifactConflictError):
        await wrapped_service.discover_candidates(confirmed_requirement(), ids)
    refs = []
    for operation_id in ids.search_operation_ids:
        found = await repository.lookup(operation_id=operation_id)
        assert isinstance(found, ArtifactFound)
        refs.append(found.ref)
    recording.events.clear()
    wrapped_budget.events.clear()
    calls_before = adapter.search_call_count
    outcome = await wrapped_service.discover_candidates(confirmed_requirement(), ids)
    return outcome, refs, recording, wrapped_budget, calls_before, adapter


@pytest.mark.asyncio
async def test_discovery_runs_strictly_sequentially_and_stops_after_three() -> None:
    service, adapter, repository, budget, _ = make_service(
        (
            map_success(place("a", index=0), place("b", index=1)),
            map_success(place("c", index=2)),
            map_success(place("unused", index=3)),
        ),
        run_id=RUN_ID,
    )
    ids = operation_ids(RUN_ID)
    outcome = await service.discover_candidates(confirmed_requirement(), ids)
    assert isinstance(outcome, HotelCandidatesReady)
    assert tuple(item.uid for item in outcome.candidates) == ("a", "b", "c")
    assert adapter.search_call_count == 2
    assert tuple(request.query for request in adapter.requests) == ("酒店", "宾馆")
    lookup = await repository.lookup(operation_id=ids.candidate_set_operation_id)
    assert isinstance(lookup, ArtifactFound)
    read = await repository.get(lookup.ref, expected_model=HotelDiscoveryArtifact)
    assert len(read.content.search_refs) == 2
    assert read.content.search_operation_ids == ids.search_operation_ids


@pytest.mark.asyncio
async def test_first_search_replay_uses_original_success_ref_without_budget_or_adapter() -> None:
    outcome, refs, recording, budget, calls_before, adapter = await _prepare_search_replay(
        "none", map_success(place("only"))
    )
    assert isinstance(outcome, HotelCandidatesInsufficient)
    assert outcome.qualified_candidates[0].uid == "only"
    assert adapter.search_call_count == calls_before
    assert budget.events == []
    assert refs[0] is not None
    assert ("get", "hotel.place_search") in recording.events
    assert ("put", "hotel.place_search") not in recording.events
    candidate = await recording.delegate.get(
        outcome.candidate_set_ref, expected_model=HotelDiscoveryArtifact
    )
    assert candidate.content.search_refs == tuple(refs)


@pytest.mark.asyncio
async def test_first_search_replay_uses_original_no_result_ref_and_advances() -> None:
    outcome, refs, recording, budget, calls_before, adapter = await _prepare_search_replay(
        "none", make_no_result()
    )
    assert isinstance(outcome, HotelCandidatesInsufficient)
    assert adapter.search_call_count == calls_before
    assert budget.events == []
    assert refs[0] is not None and recording.events.count(("get", "hotel.place_search")) == 3
    assert ("put", "hotel.place_search") not in recording.events
    candidate = await recording.delegate.get(
        outcome.candidate_set_ref, expected_model=HotelDiscoveryArtifact
    )
    assert candidate.content.search_refs == tuple(refs)


@pytest.mark.asyncio
async def test_found_failed_search_replay_returns_original_failure_ref_without_call() -> None:
    service, adapter, repository, budget, clock = make_service(
        (BaiduMapTimeoutError(capability=MapCapability.PLACE_SEARCH),), run_id=RUN_ID
    )
    recording = RecordingArtifactRepository(repository)
    wrapped_budget = RecordingBudgetManager(budget)
    wrapped = HotelCandidateSelectionService(adapter, recording, wrapped_budget, clock)
    ids = operation_ids(RUN_ID)
    first = await wrapped.discover_candidates(confirmed_requirement(), ids)
    found = await repository.lookup(operation_id=ids.search_operation_ids[0])
    assert isinstance(found, ArtifactFound)
    wrapped_budget.events.clear()
    calls_before = adapter.search_call_count
    second = await wrapped.discover_candidates(confirmed_requirement(), ids)
    assert first.failure_search_ref == second.failure_search_ref == found.ref
    assert second.reason is HotelCandidateDiscoveryFailureCode.MAP_CALL_FAILED
    assert adapter.search_call_count == calls_before
    assert wrapped_budget.events == []


@pytest.mark.asyncio
async def test_discovery_filters_non_hotels_and_returns_insufficient_after_three() -> None:
    service, adapter, _, _, _ = make_service(
        (
            map_success(place("shop", category="购物", index=0)),
            map_success(place("guesthouse", category="宾馆", index=1)),
            make_no_result(),
        ),
        run_id=RUN_ID,
    )
    outcome = await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert isinstance(outcome, HotelCandidatesInsufficient)
    assert tuple(item.uid for item in outcome.qualified_candidates) == ("guesthouse",)
    assert adapter.search_call_count == 3


@pytest.mark.asyncio
async def test_no_result_is_safe_evidence_and_no_adapter_call_is_replayed() -> None:
    ids = operation_ids(RUN_ID)
    service, adapter, repository, budget, _ = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=RUN_ID
    )
    first = await service.discover_candidates(confirmed_requirement(), ids)
    second = await service.discover_candidates(confirmed_requirement(), ids)
    assert isinstance(first, HotelCandidatesInsufficient)
    assert second == first
    assert adapter.search_call_count == 3
    assert (await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)).consumed == 3
    for operation_id in ids.search_operation_ids:
        lookup = await repository.lookup(operation_id=operation_id)
        assert isinstance(lookup, ArtifactFound)
        read = await repository.get(lookup.ref, expected_model=HotelPlaceSearchArtifact)
        assert read.content.evidence.outcome_type == "no_result"


@pytest.mark.asyncio
async def test_candidate_set_fast_replay_does_not_enter_search_slot() -> None:
    service, adapter, repository, budget, clock = make_service(
        (map_success(place("a"), place("b"), place("c")),), run_id=RUN_ID
    )
    recording = RecordingArtifactRepository(repository)
    wrapped_budget = RecordingBudgetManager(budget)
    wrapped = HotelCandidateSelectionService(adapter, recording, wrapped_budget, clock)
    ids = operation_ids(RUN_ID)
    first = await wrapped.discover_candidates(confirmed_requirement(), ids)
    recording.events.clear()
    wrapped_budget.events.clear()
    calls_before = adapter.search_call_count
    second = await wrapped.discover_candidates(confirmed_requirement(), ids)
    assert second == first
    assert adapter.search_call_count == calls_before
    assert wrapped_budget.events == []
    assert recording.events == [
        ("lookup", "hotel_candidate_set"),
        ("get", "hotel.candidate_set"),
    ]


@pytest.mark.asyncio
async def test_unverified_result_fails_without_candidates_and_is_persisted() -> None:
    service, adapter, repository, _, _ = make_service(
        (map_success(place("a"), verified=False),), run_id=RUN_ID
    )
    ids = operation_ids(RUN_ID)
    outcome = await service.discover_candidates(confirmed_requirement(), ids)
    assert outcome.outcome_type == "failed"
    assert outcome.reason is HotelCandidateDiscoveryFailureCode.MAP_CALL_FAILED
    assert outcome.qualified_count == 0
    assert adapter.search_call_count == 1
    lookup = await repository.lookup(operation_id=ids.search_operation_ids[0])
    assert isinstance(lookup, ArtifactFound)
    read = await repository.get(lookup.ref, expected_model=HotelPlaceSearchArtifact)
    assert read.content.evidence.failure_code is HotelMapFailureCode.UNVERIFIED_RESULT
    assert not isinstance(await repository.lookup(operation_id=ids.candidate_set_operation_id), ArtifactFound)


@pytest.mark.asyncio
async def test_typed_map_failure_is_mapped_and_stops_immediately() -> None:
    error = BaiduMapTimeoutError(capability=MapCapability.PLACE_SEARCH)
    service, adapter, _, _, _ = make_service((error,), run_id=RUN_ID)
    outcome = await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert outcome.reason is HotelCandidateDiscoveryFailureCode.MAP_CALL_FAILED
    assert outcome.failed_search_ordinal == 1
    assert adapter.search_call_count == 1


@pytest.mark.asyncio
async def test_budget_exhaustion_happens_before_adapter_and_has_no_search_ref() -> None:
    service, adapter, _, _, _ = make_service((map_success(place("a")),), run_id=RUN_ID, budget_limit=0)
    outcome = await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert outcome.reason is HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED
    assert outcome.failure_search_ref is None
    assert adapter.search_call_count == 0


@pytest.mark.asyncio
async def test_replayed_budget_consumption_is_indeterminate() -> None:
    service, _, repository, budget, _ = make_service((map_success(place("a")),), run_id=RUN_ID)
    ids = operation_ids(RUN_ID)
    from app.hotel_selection import HOTEL_PLACE_CALL_BUDGET_KEY
    from app.workflow_contracts import BudgetConsumeRequest

    request = BudgetConsumeRequest(
        operation_id=ids.search_operation_ids[0],
        key=HOTEL_PLACE_CALL_BUDGET_KEY,
        amount=1,
    )
    await budget.consume(request)
    with pytest.raises(OperationResultIndeterminateError):
        await service.discover_candidates(confirmed_requirement(), ids)
    assert not isinstance(await repository.lookup(operation_id=ids.search_operation_ids[0]), ArtifactFound)


@pytest.mark.asyncio
async def test_cancelled_map_call_is_not_converted_to_domain_failure() -> None:
    service, adapter, _, _, _ = make_service((asyncio.CancelledError(),), run_id=RUN_ID)
    with pytest.raises(asyncio.CancelledError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert adapter.search_call_count == 1


@pytest.mark.asyncio
async def test_cross_response_uid_identity_conflict_is_rejected() -> None:
    first = place("same", index=0)
    second = place("same", index=1)
    service, _, _, _, _ = make_service(
        (map_success(first), map_success(second)), run_id=RUN_ID
    )
    with pytest.raises(HotelCandidateIdentityConflictError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))


@pytest.mark.asyncio
async def test_search_replay_digest_mismatch_is_rejected() -> None:
    service, _, repository, _, _ = make_service(
        (map_success(place("a")), make_no_result(), make_no_result()), run_id=RUN_ID
    )
    ids = operation_ids(RUN_ID)
    await service.discover_candidates(confirmed_requirement(), ids)
    changed = confirmed_requirement(hotel_preference="湖景")
    with pytest.raises(HotelReplayError):
        await service.discover_candidates(changed, ids)


@pytest.mark.asyncio
async def test_candidate_set_replay_requires_digest_query_plan_and_all_search_operations() -> None:
    service, _, repository, _, clock = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=RUN_ID
    )
    ids = operation_ids(RUN_ID)
    confirmed = confirmed_requirement()
    await service.discover_candidates(confirmed, ids)
    with pytest.raises(HotelReplayError):
        await service.discover_candidates(
            confirmed.model_copy(update={"confirmation_summary_digest": "sha256:other"}), ids
        )
    with pytest.raises(HotelReplayError):
        await service.discover_candidates(confirmed_requirement(hotel_preference="湖景"), ids)
    variant = HotelCandidateOperationIds(
        search_operation_ids=(
            OperationId(run_id=RUN_ID, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="hotel_search_variant"),
            ids.search_operation_ids[1],
            ids.search_operation_ids[2],
        ),
        candidate_set_operation_id=ids.candidate_set_operation_id,
    )
    with pytest.raises(HotelReplayError):
        await service.discover_candidates(confirmed, variant)
    tampered_service = HotelCandidateSelectionService(
        service._adapter,
        TamperCandidateReadRepository(repository, ids.candidate_set_operation_id),
        service._budget_manager,
        clock,
    )
    with pytest.raises(HotelReplayError):
        await tampered_service.discover_candidates(confirmed, ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("position", (0, 1, 2))
async def test_candidate_set_replay_rejects_each_search_operation_position(position: int) -> None:
    service, _, _, _, _ = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=RUN_ID
    )
    confirmed = confirmed_requirement()
    ids = operation_ids(RUN_ID)
    await service.discover_candidates(confirmed, ids)
    variants = list(ids.search_operation_ids)
    variants[position] = OperationId(
        run_id=RUN_ID,
        phase=WorkflowPhase.HOTEL_SELECTION,
        logical_step=f"hotel_search_variant_{position + 1}",
    )
    changed = HotelCandidateOperationIds(
        search_operation_ids=tuple(variants),
        candidate_set_operation_id=ids.candidate_set_operation_id,
    )
    with pytest.raises(HotelReplayError):
        await service.discover_candidates(confirmed, changed)


@pytest.mark.asyncio
async def test_discovery_priority_candidate_set_integrity_precedes_search_integrity() -> None:
    service, adapter, repository, budget, clock = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=RUN_ID
    )
    ids = operation_ids(RUN_ID)
    await service.discover_candidates(confirmed_requirement(), ids)
    search_tampered = TamperSearchReadRepository(repository, "ordinal")
    candidate_tampered = TamperCandidateReadRepository(
        search_tampered, ids.candidate_set_operation_id
    )
    recording_budget = RecordingBudgetManager(budget)
    wrapped = HotelCandidateSelectionService(
        adapter, candidate_tampered, recording_budget, clock
    )
    with pytest.raises(HotelReplayError):
        await wrapped.discover_candidates(confirmed_requirement(), ids)
    assert ("get", "hotel.place_search") not in candidate_tampered.events
    assert recording_budget.events == []


@pytest.mark.asyncio
async def test_discovery_priority_search_integrity_precedes_budget_exceeded() -> None:
    service, adapter, repository, budget, clock = make_service(
        (map_success(place("a")), make_no_result(), make_no_result()), run_id=RUN_ID
    )
    ids = operation_ids(RUN_ID)
    recording = RecordingArtifactRepository(repository, suppress_candidate_set_puts=1)
    wrapped_budget = RecordingBudgetManager(budget)
    wrapped = HotelCandidateSelectionService(adapter, recording, wrapped_budget, clock)
    with pytest.raises(ArtifactConflictError):
        await wrapped.discover_candidates(confirmed_requirement(), ids)
    wrapped_budget.events.clear()
    tampered = TamperSearchReadRepository(repository, "ordinal")
    replay = HotelCandidateSelectionService(adapter, tampered, wrapped_budget, clock)
    with pytest.raises(HotelReplayError):
        await replay.discover_candidates(confirmed_requirement(), ids)
    assert wrapped_budget.events == []
    assert adapter.search_call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("low_path", ("typed_failure", "unverified"))
async def test_discovery_priority_budget_exceeded_precedes_low_result(low_path: str) -> None:
    low = (
        BaiduMapTimeoutError(capability=MapCapability.PLACE_SEARCH)
        if low_path == "typed_failure"
        else map_success(place("ignored"), verified=False)
    )
    service, adapter, _, budget, _ = make_service((low,), run_id=RUN_ID, budget_limit=0)
    outcome = await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert outcome.reason is HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED
    assert adapter.search_call_count == 0
    assert (await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)).consumed == 0


@pytest.mark.asyncio
async def test_discovery_priority_typed_failure_precedes_uid_conflict() -> None:
    first = place("same", index=0)
    duplicate = place("same", index=1)
    service, adapter, _, _, _ = make_service(
        (map_success(first), BaiduMapTimeoutError(capability=MapCapability.PLACE_SEARCH), map_success(duplicate)),
        run_id=RUN_ID,
    )
    outcome = await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert outcome.reason is HotelCandidateDiscoveryFailureCode.MAP_CALL_FAILED
    assert outcome.failed_search_ordinal == 2
    assert adapter.search_call_count == 2


@pytest.mark.asyncio
async def test_discovery_priority_uid_conflict_precedes_insufficient_result() -> None:
    service, adapter, _, _, _ = make_service(
        (map_success(place("same", index=0)), map_success(place("same", index=1)), make_no_result()),
        run_id=RUN_ID,
    )
    with pytest.raises(HotelCandidateIdentityConflictError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert adapter.search_call_count == 2


@pytest.mark.asyncio
async def test_discovery_priority_invalid_input_precedes_candidate_set_replay() -> None:
    service, adapter, repository, budget, clock = make_service(
        (map_success(place("a"), place("b"), place("c")),), run_id=RUN_ID
    )
    ids = operation_ids(RUN_ID)
    await service.discover_candidates(confirmed_requirement(), ids)
    tampered = TamperCandidateReadRepository(repository, ids.candidate_set_operation_id)
    wrapped_budget = RecordingBudgetManager(budget)
    wrapped = HotelCandidateSelectionService(adapter, tampered, wrapped_budget, clock)
    invalid_confirmed = InvalidConfirmedRequirement(
        **confirmed_requirement().model_dump(mode="python")
    )
    with pytest.raises(HotelInputError):
        await wrapped.discover_candidates(invalid_confirmed, ids)
    assert adapter.search_call_count == 1
    assert tampered.events == []
    assert wrapped_budget.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ("ordinal", "request_hash", "ref"))
async def test_search_replay_rejects_ordinal_request_hash_and_ref_mismatch(mode: str) -> None:
    with pytest.raises(HotelReplayError):
        await _prepare_search_replay(mode, map_success(place("only")))


@pytest.mark.asyncio
async def test_third_search_reaches_ready_with_three_refs_and_three_budget_consumes() -> None:
    service, adapter, repository, budget, _ = make_service(
        (map_success(place("a")), map_success(place("b")), map_success(place("c"))),
        run_id=UUID("00000000-0000-0000-0000-000000000008"),
    )
    run_id = UUID("00000000-0000-0000-0000-000000000008")
    ids = operation_ids(run_id)
    outcome = await service.discover_candidates(confirmed_requirement(), ids)
    assert isinstance(outcome, HotelCandidatesReady)
    assert adapter.search_call_count == 3
    snapshot = await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)
    assert snapshot.consumed == 3
    candidate = await repository.get(outcome.candidate_set_ref, expected_model=HotelDiscoveryArtifact)
    assert len(candidate.content.search_refs) == 3


@pytest.mark.asyncio
async def test_first_search_reaches_ready_and_stops_after_current_response() -> None:
    service, adapter, repository, budget, _ = make_service(
        (map_success(place("a", index=0), place("b", index=1), place("c", index=2)),),
        run_id=UUID("00000000-0000-0000-0000-000000000010"),
    )
    run_id = UUID("00000000-0000-0000-0000-000000000010")
    ids = operation_ids(run_id)
    outcome = await service.discover_candidates(confirmed_requirement(), ids)
    assert isinstance(outcome, HotelCandidatesReady)
    assert adapter.search_call_count == 1
    assert (await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)).consumed == 1
    candidate = await repository.get(outcome.candidate_set_ref, expected_model=HotelDiscoveryArtifact)
    assert len(candidate.content.search_refs) == 1


@pytest.mark.asyncio
async def test_three_no_results_consume_exactly_three_and_record_event_order() -> None:
    sink = SharedEventSink()
    service, adapter, repository, budget, clock = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=RUN_ID
        , event_sink=sink
    )
    recording = RecordingArtifactRepository(repository, event_sink=sink)
    wrapped_budget = RecordingBudgetManager(budget, event_sink=sink)
    wrapped = HotelCandidateSelectionService(adapter, recording, wrapped_budget, clock)
    outcome = await wrapped.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert isinstance(outcome, HotelCandidatesInsufficient)
    assert [event[0] for event in recording.events] == ["lookup", "lookup", "put", "lookup", "put", "lookup", "put", "put"]
    assert [event[0] for event in wrapped_budget.events] == ["consume", "consume", "consume"]
    assert adapter.search_call_count == 3
    assert sink.events == [
        ("repository", "lookup:hotel_candidate_set"),
        ("repository", "lookup:hotel_search_1"),
        ("budget", "consume:hotel_search_1"),
        ("adapter", "search:酒店"),
        ("repository", "put:hotel.place_search"),
        ("repository", "lookup:hotel_search_2"),
        ("budget", "consume:hotel_search_2"),
        ("adapter", "search:宾馆"),
        ("repository", "put:hotel.place_search"),
        ("repository", "lookup:hotel_search_3"),
        ("budget", "consume:hotel_search_3"),
        ("adapter", "search:住宿"),
        ("repository", "put:hotel.place_search"),
        ("repository", "put:hotel.candidate_set"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    (
        BaiduMapRequestError,
        BaiduMapConfigurationError,
        BaiduMapAuthenticationError,
        BaiduMapRateLimitError,
        BaiduMapTimeoutError,
        BaiduMapTransportError,
        BaiduMapSessionError,
        BaiduMapToolUnavailableError,
        BaiduMapToolSchemaError,
        BaiduMapProtocolError,
        BaiduMapResponseError,
        BaiduMapConcurrencyError,
    ),
)
async def test_every_public_typed_map_error_maps_to_safe_failure_code(error_type) -> None:
    service, _, repository, _, _ = make_service(
        (error_type(capability=MapCapability.PLACE_SEARCH),), run_id=RUN_ID
    )
    ids = operation_ids(RUN_ID)
    outcome = await service.discover_candidates(confirmed_requirement(), ids)
    assert outcome.reason is HotelCandidateDiscoveryFailureCode.MAP_CALL_FAILED
    found = await repository.lookup(operation_id=ids.search_operation_ids[0])
    assert isinstance(found, ArtifactFound)
    artifact = await repository.get(found.ref, expected_model=HotelPlaceSearchArtifact)
    assert artifact.content.evidence.failure_code.value == error_type.code.value


@pytest.mark.asyncio
async def test_cancellation_has_no_search_or_candidate_artifact() -> None:
    service, adapter, repository, _, _ = make_service((asyncio.CancelledError(),), run_id=RUN_ID)
    ids = operation_ids(RUN_ID)
    with pytest.raises(asyncio.CancelledError):
        await service.discover_candidates(confirmed_requirement(), ids)
    assert adapter.search_call_count == 1
    assert not isinstance(await repository.lookup(operation_id=ids.search_operation_ids[0]), ArtifactFound)
    assert not isinstance(await repository.lookup(operation_id=ids.candidate_set_operation_id), ArtifactFound)


@pytest.mark.asyncio
async def test_search_and_candidate_set_put_conflicts_fail_closed() -> None:
    service, adapter, repository, budget, clock = make_service((map_success(place("a")),), run_id=RUN_ID)
    search_recording = RecordingArtifactRepository(
        repository,
        put_failure=ArtifactConflictError(),
        put_failure_artifact_type="hotel.place_search",
    )
    with pytest.raises(ArtifactConflictError):
        await HotelCandidateSelectionService(adapter, search_recording, budget, clock).discover_candidates(
            confirmed_requirement(), operation_ids(RUN_ID)
        )
    service, adapter, repository, budget, clock = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=UUID("00000000-0000-0000-0000-000000000009")
    )
    candidate_recording = RecordingArtifactRepository(
        repository,
        put_failure=ArtifactConflictError(),
        put_failure_artifact_type="hotel.candidate_set",
    )
    with pytest.raises(ArtifactConflictError):
        await HotelCandidateSelectionService(adapter, candidate_recording, budget, clock).discover_candidates(
            confirmed_requirement(), operation_ids(UUID("00000000-0000-0000-0000-000000000009"))
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact_type", ("hotel.place_search", "hotel.candidate_set"))
async def test_search_and_candidate_set_put_integrity_failures_propagate(artifact_type: str) -> None:
    run_id = RUN_ID if artifact_type == "hotel.place_search" else UUID("00000000-0000-0000-0000-000000000011")
    responses = (map_success(place("a")),) if artifact_type == "hotel.place_search" else (
        make_no_result(), make_no_result(), make_no_result()
    )
    service, adapter, repository, budget, clock = make_service(responses, run_id=run_id)
    recording = RecordingArtifactRepository(
        repository,
        put_failure=ArtifactIntegrityError(),
        put_failure_artifact_type=artifact_type,
    )
    with pytest.raises(ArtifactIntegrityError):
        await HotelCandidateSelectionService(adapter, recording, budget, clock).discover_candidates(
            confirmed_requirement(), operation_ids(run_id)
        )


@pytest.mark.asyncio
async def test_search_get_integrity_failure_is_not_downgraded() -> None:
    service, _, repository, _, clock = make_service(
        (map_success(place("a")), make_no_result(), make_no_result()), run_id=RUN_ID
    )
    recording = RecordingArtifactRepository(repository, suppress_candidate_set_puts=1)
    with pytest.raises(ArtifactConflictError):
        await HotelCandidateSelectionService(service._adapter, recording, service._budget_manager, clock).discover_candidates(
            confirmed_requirement(), operation_ids(RUN_ID)
        )
    class IntegrityRepository(RecordingArtifactRepository):
        async def get(self, ref, *, expected_model):
            self.events.append(("get", ref.artifact_type))
            if expected_model is HotelPlaceSearchArtifact:
                raise ArtifactIntegrityError()
            return await self.delegate.get(ref, expected_model=expected_model)
    with pytest.raises(ArtifactIntegrityError):
        await HotelCandidateSelectionService(
            service._adapter,
            IntegrityRepository(repository),
            service._budget_manager,
            clock,
        ).discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pair_case",
    (
        pytest.param("input>candidate-set", id="input>candidate-set"),
        pytest.param("candidate-set>search", id="candidate-set>search"),
        pytest.param("search>budget", id="search>budget"),
        pytest.param("budget>typed-or-unverified", id="budget>typed-or-unverified"),
        pytest.param("typed>uid", id="typed>uid"),
        pytest.param("uid>insufficient", id="uid>insufficient"),
    ),
)
async def test_discovery_adjacent_failure_priority_matrix(pair_case: str) -> None:
    if pair_case == "budget>typed-or-unverified":
        await test_discovery_priority_budget_exceeded_precedes_low_result("typed_failure")
        await test_discovery_priority_budget_exceeded_precedes_low_result("unverified")
        return
    cases = {
        "input>candidate-set": test_discovery_priority_invalid_input_precedes_candidate_set_replay,
        "candidate-set>search": test_discovery_priority_candidate_set_integrity_precedes_search_integrity,
        "search>budget": test_discovery_priority_search_integrity_precedes_budget_exceeded,
        "typed>uid": test_discovery_priority_typed_failure_precedes_uid_conflict,
        "uid>insufficient": test_discovery_priority_uid_conflict_precedes_insufficient_result,
    }
    await cases[pair_case]()


def test_service_fresh_and_constructor_boundaries_fail_closed() -> None:
    with pytest.raises(HotelInputError):
        service_module._fresh(object, object())
    service, adapter, repository, budget, clock = make_service(())
    with pytest.raises(HotelInputError):
        HotelCandidateSelectionService(object(), repository, budget, clock)
    with pytest.raises(HotelInputError):
        HotelCandidateSelectionService(adapter, object(), budget, clock)


@pytest.mark.asyncio
@pytest.mark.parametrize("clock", ("raises", "naive"))
async def test_clock_failures_are_not_leaked_or_converted(clock: str) -> None:
    failure = BaiduMapRequestError(capability=MapCapability.PLACE_SEARCH)
    service, _, _, _, _ = make_service((failure,), run_id=RUN_ID)

    class BadClock:
        def now(self):
            if clock == "raises":
                raise ValueError("clock backend detail")
            return __import__("datetime").datetime(2026, 1, 1)

    service._utc_clock = BadClock()
    with pytest.raises(HotelInputError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))


@pytest.mark.asyncio
async def test_discovery_replay_and_search_unexpected_lookup_types_fail_closed() -> None:
    service, _, _, _, _ = make_service((), run_id=RUN_ID)

    class CandidateUnexpectedRepository:
        async def lookup(self, *, operation_id):
            return object()

        async def get(self, ref, *, expected_model):
            raise AssertionError("get must not run")

        async def put(self, request):
            raise AssertionError("put must not run")

    service._repository = CandidateUnexpectedRepository()
    with pytest.raises(HotelReplayError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))

    service, _, repository, _, _ = make_service((), run_id=RUN_ID)

    class SearchUnexpectedRepository:
        async def lookup(self, *, operation_id):
            if operation_id.logical_step == "hotel_candidate_set":
                return ArtifactAbsent(operation_id=operation_id)
            return object()

        async def get(self, ref, *, expected_model):
            raise AssertionError("get must not run")

        async def put(self, request):
            raise AssertionError("put must not run")

    service._repository = SearchUnexpectedRepository()
    with pytest.raises(HotelReplayError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert repository is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("budget_value", ("unknown", "disposition"))
async def test_budget_outcomes_outside_contract_fail_closed(budget_value: str) -> None:
    service, _, _, _, _ = make_service((), run_id=RUN_ID)

    class WeirdBudget:
        async def consume(self, request):
            if budget_value == "unknown":
                return object()
            return BudgetConsumed.model_construct(disposition="unexpected")

        async def get_snapshot(self, *, key):
            raise AssertionError("snapshot must not run")

        async def get_all_snapshots(self):
            raise AssertionError("snapshots must not run")

    service._budget_manager = WeirdBudget()
    with pytest.raises(HotelReplayError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))


@pytest.mark.asyncio
async def test_unknown_map_error_code_and_invalid_map_outcome_fail_closed() -> None:
    service, _, _, _, _ = make_service(
        (UnknownMapError(capability=MapCapability.PLACE_SEARCH),), run_id=RUN_ID
    )
    with pytest.raises(HotelInputError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))

    service, _, _, _, _ = make_service((object(),), run_id=RUN_ID)
    with pytest.raises(HotelInputError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))


@pytest.mark.asyncio
async def test_missing_search_evidence_is_replay_error() -> None:
    service, _, _, _, _ = make_service((), run_id=RUN_ID)

    async def no_evidence(**kwargs):
        return object(), None, None

    service._read_search_slot = no_evidence
    with pytest.raises(HotelReplayError):
        await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
