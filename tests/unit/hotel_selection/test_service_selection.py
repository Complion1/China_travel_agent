"""07-4 人工选择：严格引用、幂等决策与并发安全。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.hotel_selection import (
    HOTEL_PLACE_CALL_BUDGET_KEY,
    HotelCandidateSelectionService,
    HotelCandidatesInsufficient,
    HotelDeclineCommand,
    HotelSelected,
    HotelSelectCommand,
    HotelSelectionDeclined,
    HotelSelectionRejected,
    HotelSelectionRejectionCode,
    HotelSelectionRequest,
    HotelInputError,
    HotelReplayError,
)
from app.hotel_selection.artifacts import (
    HotelDiscoveryArtifact,
    HotelDeclinedDecision,
    HotelSelectedDecision,
    HotelSelectionArtifact,
    _artifact_write_request,
)
import app.hotel_selection.service as service_module
from app.workflow_contracts import ArtifactAbsent, ArtifactConflictError, ArtifactIntegrityError, ArtifactReadResult, ArtifactRepository, ArtifactFound, OperationId, WorkflowPhase
from tests.support.hotel_selection_fakes import (
    RecordingArtifactRepository,
    confirmed_requirement,
    make_no_result,
    make_service,
    map_success,
    operation_ids,
    place,
    selection_operation,
)


RUN_ID = UUID("00000000-0000-0000-0000-000000000017")


async def ready_context() -> tuple[object, object, object, object, object, object]:
    service, adapter, repository, budget, clock = make_service(
        (map_success(place("a", index=0), place("b", index=1), place("c", index=2)),),
        run_id=RUN_ID,
    )
    discovery = await service.discover_candidates(confirmed_requirement(), operation_ids(RUN_ID))
    assert discovery.outcome_type == "ready"
    return service, adapter, repository, budget, clock, discovery.candidate_set_ref


def request_for(candidate_ref, operation_id, command) -> HotelSelectionRequest:
    return HotelSelectionRequest(
        selection_operation_id=operation_id,
        candidate_set_ref=candidate_ref,
        command=command,
    )


class InvalidSelectionRequest(HotelSelectionRequest):
    """子类携带完整 stale ref，但应被 exact-type 入口拒绝。"""


class IntegrityCandidateRepository:
    def __init__(self, delegate: ArtifactRepository) -> None:
        self.delegate = delegate
        self.events: list[tuple[str, str]] = []

    async def put(self, request):
        self.events.append(("put", request.artifact_type))
        return await self.delegate.put(request)

    async def lookup(self, *, operation_id):
        self.events.append(("lookup", operation_id.logical_step))
        return await self.delegate.lookup(operation_id=operation_id)

    async def get(self, ref, *, expected_model):
        self.events.append(("get", ref.artifact_type))
        if expected_model is HotelDiscoveryArtifact:
            raise ArtifactIntegrityError()
        return await self.delegate.get(ref, expected_model=expected_model)


class IntegritySelectionRepository:
    def __init__(self, delegate: ArtifactRepository) -> None:
        self.delegate = delegate
        self.events: list[tuple[str, str]] = []

    async def put(self, request):
        self.events.append(("put", request.artifact_type))
        return await self.delegate.put(request)

    async def lookup(self, *, operation_id):
        self.events.append(("lookup", operation_id.logical_step))
        return await self.delegate.lookup(operation_id=operation_id)

    async def get(self, ref, *, expected_model):
        self.events.append(("get", ref.artifact_type))
        if expected_model is HotelSelectionArtifact:
            raise ArtifactIntegrityError()
        return await self.delegate.get(ref, expected_model=expected_model)


@pytest.mark.asyncio
async def test_select_and_decline_are_typed_and_do_not_call_map() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    before = await budget.get_snapshot(key=__import__("app.hotel_selection", fromlist=["HOTEL_PLACE_CALL_BUDGET_KEY"]).HOTEL_PLACE_CALL_BUDGET_KEY)
    before_calls = adapter.search_call_count
    selected = await service.record_selection(
        request_for(
            candidate_ref,
            selection_operation(RUN_ID, "selected"),
            HotelSelectCommand(selected_uid="a"),
        ),
        active_candidate_set_ref=candidate_ref,
    )
    assert isinstance(selected, HotelSelected)
    assert selected.selected_candidate.uid == "a"

    declined = await service.record_selection(
        request_for(
            candidate_ref,
            selection_operation(RUN_ID, "declined"),
            HotelDeclineCommand(),
        ),
        active_candidate_set_ref=candidate_ref,
    )
    assert isinstance(declined, HotelSelectionDeclined)
    assert adapter.search_call_count == before_calls
    after = await budget.get_snapshot(key=__import__("app.hotel_selection", fromlist=["HOTEL_PLACE_CALL_BUDGET_KEY"]).HOTEL_PLACE_CALL_BUDGET_KEY)
    assert after == before
    assert budget is not None and clock is not None and repository is not None


@pytest.mark.asyncio
async def test_selection_rejects_stale_not_ready_and_unknown_uid() -> None:
    service, _, _, _, _, candidate_ref = await ready_context()
    stale = candidate_ref.model_copy(update={"content_hash": "sha256:" + "0" * 64})
    stale_result = await service.record_selection(
        request_for(stale, selection_operation(RUN_ID, "stale"), HotelDeclineCommand()),
        active_candidate_set_ref=candidate_ref,
    )
    assert isinstance(stale_result, HotelSelectionRejected)
    assert stale_result.reason is HotelSelectionRejectionCode.STALE_CANDIDATE_SET

    missing = await service.record_selection(
        request_for(
            candidate_ref,
            selection_operation(RUN_ID, "missing"),
            HotelSelectCommand(selected_uid="missing"),
        ),
        active_candidate_set_ref=candidate_ref,
    )
    assert missing.reason is HotelSelectionRejectionCode.CANDIDATE_UID_NOT_FOUND

    insufficient_service, _, _, _, _ = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=UUID("00000000-0000-0000-0000-000000000018")
    )
    insufficient = await insufficient_service.discover_candidates(
        confirmed_requirement(), operation_ids(UUID("00000000-0000-0000-0000-000000000018"))
    )
    assert isinstance(insufficient, HotelCandidatesInsufficient)
    not_ready = await insufficient_service.record_selection(
        request_for(
            insufficient.candidate_set_ref,
            selection_operation(UUID("00000000-0000-0000-0000-000000000018"), "not_ready"),
            HotelDeclineCommand(),
        ),
        active_candidate_set_ref=insufficient.candidate_set_ref,
    )
    assert not_ready.reason is HotelSelectionRejectionCode.CANDIDATE_SET_NOT_READY


@pytest.mark.asyncio
async def test_not_ready_and_missing_uid_do_not_lookup_or_put_selection() -> None:
    service, adapter, repository, budget, clock = make_service(
        (make_no_result(), make_no_result(), make_no_result()),
        run_id=UUID("00000000-0000-0000-0000-000000000020"),
    )
    run_id = UUID("00000000-0000-0000-0000-000000000020")
    ids = operation_ids(run_id)
    insufficient = await service.discover_candidates(confirmed_requirement(), ids)
    recording = RecordingArtifactRepository(repository)
    guarded = HotelCandidateSelectionService(adapter, recording, budget, clock)
    not_ready = await guarded.record_selection(
        request_for(insufficient.candidate_set_ref, selection_operation(run_id, "not_ready_guard"), HotelDeclineCommand()),
        active_candidate_set_ref=insufficient.candidate_set_ref,
    )
    assert not_ready.reason is HotelSelectionRejectionCode.CANDIDATE_SET_NOT_READY
    assert recording.events == [("get", "hotel.candidate_set")]

    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    recording = RecordingArtifactRepository(repository)
    guarded = HotelCandidateSelectionService(adapter, recording, budget, clock)
    missing = await guarded.record_selection(
        request_for(candidate_ref, selection_operation(RUN_ID, "missing_guard"), HotelSelectCommand(selected_uid="missing")),
        active_candidate_set_ref=candidate_ref,
    )
    assert missing.reason is HotelSelectionRejectionCode.CANDIDATE_UID_NOT_FOUND
    assert recording.events == [("get", "hotel.candidate_set"), ("lookup", "hotel_selection_missing_guard")]


@pytest.mark.asyncio
async def test_selection_replay_and_decision_conflict_are_deterministic() -> None:
    service, _, _, _, _, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "replay")
    command = HotelSelectCommand(selected_uid="a")
    request = request_for(candidate_ref, operation_id, command)
    first = await service.record_selection(request, active_candidate_set_ref=candidate_ref)
    replay = await service.record_selection(request, active_candidate_set_ref=candidate_ref)
    conflict = await service.record_selection(
        request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="b")),
        active_candidate_set_ref=candidate_ref,
    )
    assert isinstance(first, HotelSelected)
    assert replay == first
    assert conflict.reason is HotelSelectionRejectionCode.DECISION_CONFLICT


@pytest.mark.asyncio
async def test_selection_priority_invalid_input_precedes_stale_candidate_set() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    recording = RecordingArtifactRepository(repository)
    wrapped = HotelCandidateSelectionService(adapter, recording, budget, clock)
    stale = candidate_ref.model_copy(update={"content_hash": "sha256:" + "0" * 64})
    valid_request = request_for(
        stale,
        selection_operation(RUN_ID, "priority_invalid"),
        HotelDeclineCommand(),
    )
    invalid_request = InvalidSelectionRequest(
        **valid_request.model_dump(mode="python")
    )
    assert invalid_request.candidate_set_ref != candidate_ref
    with pytest.raises(HotelInputError):
        await wrapped.record_selection(invalid_request, active_candidate_set_ref=candidate_ref)
    assert recording.events == []
    assert (await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)).consumed == 1
    assert adapter.search_call_count == 1


@pytest.mark.asyncio
async def test_selection_priority_stale_candidate_set_precedes_candidate_integrity() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    integrity_repository = IntegrityCandidateRepository(repository)
    wrapped = HotelCandidateSelectionService(adapter, integrity_repository, budget, clock)
    stale = candidate_ref.model_copy(update={"content_hash": "sha256:" + "0" * 64})
    outcome = await wrapped.record_selection(
        request_for(stale, selection_operation(RUN_ID, "priority_stale"), HotelDeclineCommand()),
        active_candidate_set_ref=candidate_ref,
    )
    assert outcome.reason is HotelSelectionRejectionCode.STALE_CANDIDATE_SET
    assert integrity_repository.events == []
    assert adapter.search_call_count == 1
    assert (await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)).consumed == 1


@pytest.mark.asyncio
async def test_selection_priority_candidate_integrity_precedes_operation_conflict() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    integrity_repository = IntegrityCandidateRepository(repository)
    wrapped = HotelCandidateSelectionService(
        adapter, integrity_repository, budget, clock
    )
    with pytest.raises(ArtifactIntegrityError):
        await wrapped.record_selection(
            request_for(candidate_ref, candidate_ref.operation_id, HotelDeclineCommand()),
            active_candidate_set_ref=candidate_ref,
        )
    assert integrity_repository.events == [("get", "hotel.candidate_set")]
    assert adapter.search_call_count == 1
    assert (await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)).consumed == 1


@pytest.mark.asyncio
async def test_selection_priority_operation_conflict_precedes_not_ready() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000021")
    service, adapter, repository, budget, clock = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=run_id
    )
    discovery = await service.discover_candidates(confirmed_requirement(), operation_ids(run_id))
    recording = RecordingArtifactRepository(repository)
    wrapped = HotelCandidateSelectionService(adapter, recording, budget, clock)
    outcome = await wrapped.record_selection(
        request_for(
            discovery.candidate_set_ref,
            discovery.candidate_set_ref.operation_id,
            HotelDeclineCommand(),
        ),
        active_candidate_set_ref=discovery.candidate_set_ref,
    )
    assert outcome.reason is HotelSelectionRejectionCode.DECISION_CONFLICT
    assert recording.events == [("get", "hotel.candidate_set")]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", (HotelDeclineCommand(), HotelSelectCommand(selected_uid="a")))
async def test_selection_priority_not_ready_precedes_selection_integrity(command) -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000022")
    service, adapter, repository, budget, clock = make_service(
        (make_no_result(), make_no_result(), make_no_result()), run_id=run_id
    )
    discovery = await service.discover_candidates(confirmed_requirement(), operation_ids(run_id))
    integrity_repository = IntegritySelectionRepository(repository)
    wrapped = HotelCandidateSelectionService(
        adapter, integrity_repository, budget, clock
    )
    outcome = await wrapped.record_selection(
        request_for(
            discovery.candidate_set_ref,
            selection_operation(run_id, "priority_not_ready"),
            command,
        ),
        active_candidate_set_ref=discovery.candidate_set_ref,
    )
    assert outcome.reason is HotelSelectionRejectionCode.CANDIDATE_SET_NOT_READY
    assert integrity_repository.events == [("get", "hotel.candidate_set")]
    assert adapter.search_call_count == 3
    assert (await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)).consumed == 3


@pytest.mark.asyncio
async def test_selection_priority_selection_integrity_precedes_recorded_conflict() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "priority_selection_integrity")
    await service.record_selection(
        request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="a")),
        active_candidate_set_ref=candidate_ref,
    )
    integrity_repository = IntegritySelectionRepository(repository)
    wrapped = HotelCandidateSelectionService(adapter, integrity_repository, budget, clock)
    with pytest.raises(ArtifactIntegrityError):
        await wrapped.record_selection(
            request_for(candidate_ref, operation_id, HotelDeclineCommand()),
            active_candidate_set_ref=candidate_ref,
        )
    assert integrity_repository.events == [
        ("get", "hotel.candidate_set"),
        ("lookup", "hotel_selection_priority_selection_integrity"),
        ("get", "hotel.selection"),
    ]
    assert adapter.search_call_count == 1
    assert (await budget.get_snapshot(key=HOTEL_PLACE_CALL_BUDGET_KEY)).consumed == 1


@pytest.mark.asyncio
async def test_selection_priority_recorded_conflict_precedes_unknown_uid() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "priority_unknown_uid")
    await service.record_selection(
        request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="a")),
        active_candidate_set_ref=candidate_ref,
    )
    recording = RecordingArtifactRepository(repository)
    wrapped = HotelCandidateSelectionService(adapter, recording, budget, clock)
    outcome = await wrapped.record_selection(
        request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="unknown")),
        active_candidate_set_ref=candidate_ref,
    )
    assert outcome.reason is HotelSelectionRejectionCode.DECISION_CONFLICT
    assert ("put", "hotel.selection") not in recording.events


@pytest.mark.asyncio
async def test_exact_replay_returns_original_ref_without_selection_put() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    recording = RecordingArtifactRepository(repository)
    wrapped = HotelCandidateSelectionService(adapter, recording, budget, clock)
    operation_id = selection_operation(RUN_ID, "exact_ref")
    request = request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="a"))
    first = await wrapped.record_selection(request, active_candidate_set_ref=candidate_ref)
    recording.events.clear()
    replay = await wrapped.record_selection(request, active_candidate_set_ref=candidate_ref)
    assert isinstance(first, HotelSelected)
    assert replay == first
    assert ("put", "hotel.selection") not in recording.events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_command", "second_command"),
    (
        (HotelSelectCommand(selected_uid="a"), HotelDeclineCommand()),
        (HotelDeclineCommand(), HotelSelectCommand(selected_uid="a")),
    ),
)
async def test_normal_replay_different_decision_is_conflict_without_second_put(
    first_command, second_command
) -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "normal_replay_conflict")
    first = await service.record_selection(
        request_for(candidate_ref, operation_id, first_command),
        active_candidate_set_ref=candidate_ref,
    )
    assert isinstance(first, (HotelSelected, HotelSelectionDeclined))
    first_read = await repository.get(first.selection_ref, expected_model=HotelSelectionArtifact)
    recording = RecordingArtifactRepository(repository)
    wrapped = HotelCandidateSelectionService(adapter, recording, budget, clock)
    conflict = await wrapped.record_selection(
        request_for(candidate_ref, operation_id, second_command),
        active_candidate_set_ref=candidate_ref,
    )
    assert isinstance(conflict, HotelSelectionRejected)
    assert conflict.reason is HotelSelectionRejectionCode.DECISION_CONFLICT
    assert ("put", "hotel.selection") not in recording.events
    current = await repository.get(first.selection_ref, expected_model=HotelSelectionArtifact)
    assert current.ref == first_read.ref
    assert current.content == first_read.content


@pytest.mark.asyncio
async def test_normal_replay_different_valid_active_candidate_ref_is_conflict_without_put() -> None:
    service, adapter, repository, budget, clock, original_ref = await ready_context()
    candidate_read = await repository.get(original_ref, expected_model=HotelDiscoveryArtifact)
    alternate_operation = OperationId(
        run_id=RUN_ID,
        phase=WorkflowPhase.HOTEL_SELECTION,
        logical_step="hotel_candidate_set_two",
    )
    alternate_ref = await repository.put(
        _artifact_write_request(
            operation_id=alternate_operation,
            artifact_type="hotel.candidate_set",
            artifact=candidate_read.content,
        )
    )
    operation_id = selection_operation(RUN_ID, "normal_replay_active_ref")
    first = await service.record_selection(
        request_for(original_ref, operation_id, HotelSelectCommand(selected_uid="a")),
        active_candidate_set_ref=original_ref,
    )
    first_read = await repository.get(first.selection_ref, expected_model=HotelSelectionArtifact)
    recording = RecordingArtifactRepository(repository)
    wrapped = HotelCandidateSelectionService(adapter, recording, budget, clock)
    conflict = await wrapped.record_selection(
        request_for(alternate_ref, operation_id, HotelSelectCommand(selected_uid="a")),
        active_candidate_set_ref=alternate_ref,
    )
    assert isinstance(conflict, HotelSelectionRejected)
    assert conflict.reason is HotelSelectionRejectionCode.DECISION_CONFLICT
    assert ("put", "hotel.selection") not in recording.events
    current = await repository.get(first.selection_ref, expected_model=HotelSelectionArtifact)
    assert current.ref == first_read.ref
    assert current.content == first_read.content


@pytest.mark.asyncio
async def test_selected_replay_rejects_forged_candidate_facts() -> None:
    service, _, repository, _, _, candidate_ref = await ready_context()
    candidate_read = await repository.get(candidate_ref, expected_model=HotelDiscoveryArtifact)
    original = candidate_read.content.result.candidates[0]
    forged = original.model_copy(update={"name": "FORGED-NAME"})
    operation_id = selection_operation(RUN_ID, "forged_found")
    artifact = HotelSelectionArtifact(
        schema_version="1.0",
        candidate_set_ref=candidate_ref,
        decision=HotelSelectedDecision(
            schema_version="1.0",
            decision_type="selected",
            selected_candidate=forged,
        ),
    )
    await repository.put(
        _artifact_write_request(
            operation_id=operation_id,
            artifact_type="hotel.selection",
            artifact=artifact,
        )
    )
    outcome = await service.record_selection(
        request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid=original.uid)),
        active_candidate_set_ref=candidate_ref,
    )
    assert isinstance(outcome, HotelSelectionRejected)
    assert outcome.reason is HotelSelectionRejectionCode.DECISION_CONFLICT


class ConflictOnceSelectionRepository:
    """让首次 selection lookup 观察 absent，随后 put 冲突并读到预置结果。"""

    def __init__(self, delegate: ArtifactRepository, operation_id: OperationId) -> None:
        self.delegate = delegate
        self.operation_id = operation_id
        self.first_lookup = True
        self.first_put = True

    async def put(self, request):
        if request.operation_id == self.operation_id and self.first_put:
            self.first_put = False
            raise ArtifactConflictError()
        return await self.delegate.put(request)

    async def get(self, ref, *, expected_model):
        return await self.delegate.get(ref, expected_model=expected_model)

    async def lookup(self, *, operation_id):
        if operation_id == self.operation_id and self.first_lookup:
            self.first_lookup = False
            return ArtifactAbsent(operation_id=operation_id)
        return await self.delegate.lookup(operation_id=operation_id)


@pytest.mark.asyncio
async def test_race_replay_rejects_forged_candidate_facts() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    candidate_read = await repository.get(candidate_ref, expected_model=HotelDiscoveryArtifact)
    original = candidate_read.content.result.candidates[0]
    forged = original.model_copy(update={"address": "FORGED-ADDRESS"})
    operation_id = selection_operation(RUN_ID, "forged_race")
    artifact = HotelSelectionArtifact(
        schema_version="1.0",
        candidate_set_ref=candidate_ref,
        decision=HotelSelectedDecision(
            schema_version="1.0",
            decision_type="selected",
            selected_candidate=forged,
        ),
    )
    await repository.put(
        _artifact_write_request(
            operation_id=operation_id,
            artifact_type="hotel.selection",
            artifact=artifact,
        )
    )
    raced = HotelCandidateSelectionService(
        adapter,
        ConflictOnceSelectionRepository(repository, operation_id),
        budget,
        clock,
    )
    outcome = await raced.record_selection(
        request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid=original.uid)),
        active_candidate_set_ref=candidate_ref,
    )
    assert isinstance(outcome, HotelSelectionRejected)
    assert outcome.reason is HotelSelectionRejectionCode.DECISION_CONFLICT


@pytest.mark.asyncio
async def test_active_ref_each_field_drift_is_stale_before_get() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    guard = RecordingArtifactRepository(repository)
    guarded = HotelCandidateSelectionService(adapter, guard, budget, clock)
    updates = (
        {"artifact_id": UUID("00000000-0000-0000-0000-000000000099")},
        {"artifact_type": "hotel.other"},
        {"schema_version": "2.0"},
        {"content_hash": "sha256:" + "0" * 64},
        {"operation_id": OperationId(run_id=RUN_ID, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="other_candidate_set")},
        {"created_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)},
        {"size_bytes": candidate_ref.size_bytes + 1},
    )
    for index, update in enumerate(updates):
        stale = candidate_ref.model_copy(update=update)
        outcome = await guarded.record_selection(
            request_for(stale, selection_operation(RUN_ID, f"stale_{index}"), HotelDeclineCommand()),
            active_candidate_set_ref=candidate_ref,
        )
        assert outcome.reason is HotelSelectionRejectionCode.STALE_CANDIDATE_SET
    assert guard.events == []


@pytest.mark.asyncio
async def test_selection_run_mismatch_is_decision_conflict() -> None:
    service, _, _, _, _, candidate_ref = await ready_context()
    other_run = UUID("00000000-0000-0000-0000-000000000019")
    outcome = await service.record_selection(
        request_for(candidate_ref, selection_operation(other_run, "run_mismatch"), HotelDeclineCommand()),
        active_candidate_set_ref=candidate_ref,
    )
    assert outcome.reason is HotelSelectionRejectionCode.DECISION_CONFLICT


@pytest.mark.asyncio
async def test_selection_operation_cannot_reuse_candidate_or_search_operation() -> None:
    service, _, _, _, _, candidate_ref = await ready_context()
    ids = operation_ids(RUN_ID)
    for operation_id in (candidate_ref.operation_id, *ids.search_operation_ids):
        result = await service.record_selection(
            request_for(candidate_ref, operation_id, HotelDeclineCommand()),
            active_candidate_set_ref=candidate_ref,
        )
        assert result.reason is HotelSelectionRejectionCode.DECISION_CONFLICT


class BarrierRepository:
    """仅阻塞两个选择 lookup，制造真实 put 冲突而不使用墙上等待。"""

    def __init__(self, delegate: ArtifactRepository, operation_id) -> None:
        self.delegate = delegate
        self.operation_id = operation_id
        self.barrier = asyncio.Barrier(2)
        self.waiters = 0

    async def put(self, request):
        return await self.delegate.put(request)

    async def get(self, ref, *, expected_model):
        return await self.delegate.get(ref, expected_model=expected_model)

    async def lookup(self, *, operation_id):
        if operation_id == self.operation_id and self.waiters < 2:
            self.waiters += 1
            await self.barrier.wait()
        return await self.delegate.lookup(operation_id=operation_id)


@pytest.mark.asyncio
async def test_selection_put_race_replays_same_decision() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "race")
    barrier_repository = BarrierRepository(repository, operation_id)
    raced_service = HotelCandidateSelectionService(adapter, barrier_repository, budget, clock)
    request = request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="a"))
    results = await asyncio.gather(
        raced_service.record_selection(request, active_candidate_set_ref=candidate_ref),
        raced_service.record_selection(request, active_candidate_set_ref=candidate_ref),
    )
    assert all(isinstance(result, HotelSelected) for result in results)
    assert results[0] == results[1]


@pytest.mark.asyncio
async def test_selection_put_race_different_decisions_is_stable_conflict() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "race_different")
    barrier_repository = BarrierRepository(repository, operation_id)
    raced_service = HotelCandidateSelectionService(adapter, barrier_repository, budget, clock)
    selected_request = request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="a"))
    declined_request = request_for(candidate_ref, operation_id, HotelDeclineCommand())
    results = await asyncio.gather(
        raced_service.record_selection(selected_request, active_candidate_set_ref=candidate_ref),
        raced_service.record_selection(declined_request, active_candidate_set_ref=candidate_ref),
    )
    assert sum(isinstance(result, (HotelSelected, HotelSelectionDeclined)) for result in results) == 1
    assert sum(isinstance(result, HotelSelectionRejected) and result.reason is HotelSelectionRejectionCode.DECISION_CONFLICT for result in results) == 1
    stored = await repository.lookup(operation_id=operation_id)
    assert isinstance(stored, ArtifactFound)


class AbsentAfterConflictRepository:
    def __init__(self, delegate: ArtifactRepository, operation_id: OperationId) -> None:
        self.delegate = delegate
        self.operation_id = operation_id
        self.first_lookup = True

    async def put(self, request):
        if request.operation_id == self.operation_id:
            raise ArtifactConflictError()
        return await self.delegate.put(request)

    async def get(self, ref, *, expected_model):
        return await self.delegate.get(ref, expected_model=expected_model)

    async def lookup(self, *, operation_id):
        if operation_id == self.operation_id and self.first_lookup:
            self.first_lookup = False
            return ArtifactAbsent(operation_id=operation_id)
        return ArtifactAbsent(operation_id=operation_id)


@pytest.mark.asyncio
async def test_selection_conflict_followed_by_absent_raises_replay_error() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "conflict_absent")
    guarded = HotelCandidateSelectionService(
        adapter,
        AbsentAfterConflictRepository(repository, operation_id),
        budget,
        clock,
    )
    with pytest.raises(HotelReplayError):
        await guarded.record_selection(
            request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="a")),
            active_candidate_set_ref=candidate_ref,
        )


class IntegrityAfterConflictRepository(AbsentAfterConflictRepository):
    def __init__(self, delegate: ArtifactRepository, operation_id: OperationId, race_ref) -> None:
        super().__init__(delegate, operation_id)
        self.race_ref = race_ref

    async def lookup(self, *, operation_id):
        if operation_id == self.operation_id and self.first_lookup:
            self.first_lookup = False
            return ArtifactAbsent(operation_id=operation_id)
        return ArtifactFound(ref=self.race_ref)

    async def get(self, ref, *, expected_model):
        if expected_model is HotelSelectionArtifact:
            raise ArtifactIntegrityError()
        return await self.delegate.get(ref, expected_model=expected_model)


class RefMismatchAfterConflictRepository(AbsentAfterConflictRepository):
    def __init__(self, delegate: ArtifactRepository, operation_id: OperationId, race_ref) -> None:
        super().__init__(delegate, operation_id)
        self.race_ref = race_ref

    async def lookup(self, *, operation_id):
        if operation_id == self.operation_id and self.first_lookup:
            self.first_lookup = False
            return ArtifactAbsent(operation_id=operation_id)
        return ArtifactFound(ref=self.race_ref)

    async def get(self, ref, *, expected_model):
        if expected_model is HotelSelectionArtifact:
            return ArtifactReadResult(
                ref=self.race_ref,
                content=HotelSelectionArtifact(
                    schema_version="1.0",
                    candidate_set_ref=self.race_ref,
                    decision=HotelDeclinedDecision(schema_version="1.0", decision_type="declined"),
                ),
            )
        return await self.delegate.get(ref, expected_model=expected_model)


@pytest.mark.asyncio
async def test_selection_conflict_followed_by_integrity_failure_propagates() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "conflict_integrity")
    guarded = HotelCandidateSelectionService(
        adapter,
        IntegrityAfterConflictRepository(repository, operation_id, candidate_ref),
        budget,
        clock,
    )
    with pytest.raises(ArtifactIntegrityError):
        await guarded.record_selection(
            request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="a")),
            active_candidate_set_ref=candidate_ref,
        )


@pytest.mark.asyncio
async def test_selection_conflict_followed_by_ref_mismatch_fails_closed() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "conflict_ref")
    guarded = HotelCandidateSelectionService(
        adapter,
        RefMismatchAfterConflictRepository(repository, operation_id, candidate_ref),
        budget,
        clock,
    )
    with pytest.raises(HotelReplayError):
        await guarded.record_selection(
            request_for(candidate_ref, operation_id, HotelSelectCommand(selected_uid="a")),
            active_candidate_set_ref=candidate_ref,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pair_case",
    (
        pytest.param("input>stale", id="input>stale"),
        pytest.param("stale>candidate-integrity", id="stale>candidate-integrity"),
        pytest.param("candidate-integrity>operation", id="candidate-integrity>operation"),
        pytest.param("operation>not-ready", id="operation>not-ready"),
        pytest.param("not-ready>selection-integrity", id="not-ready>selection-integrity"),
        pytest.param("selection-integrity>recorded-conflict", id="selection-integrity>recorded-conflict"),
        pytest.param("recorded-conflict>unknown-uid", id="recorded-conflict>unknown-uid"),
    ),
)
async def test_selection_adjacent_failure_priority_matrix(pair_case: str) -> None:
    if pair_case == "not-ready>selection-integrity":
        await test_selection_priority_not_ready_precedes_selection_integrity(HotelDeclineCommand())
        await test_selection_priority_not_ready_precedes_selection_integrity(
            HotelSelectCommand(selected_uid="a")
        )
        return
    cases = {
        "input>stale": test_selection_priority_invalid_input_precedes_stale_candidate_set,
        "stale>candidate-integrity": test_selection_priority_stale_candidate_set_precedes_candidate_integrity,
        "candidate-integrity>operation": test_selection_priority_candidate_integrity_precedes_operation_conflict,
        "operation>not-ready": test_selection_priority_operation_conflict_precedes_not_ready,
        "selection-integrity>recorded-conflict": test_selection_priority_selection_integrity_precedes_recorded_conflict,
        "recorded-conflict>unknown-uid": test_selection_priority_recorded_conflict_precedes_unknown_uid,
    }
    await cases[pair_case]()


def test_selection_outcome_rejects_an_unknown_decision_shape() -> None:
    invalid = HotelSelectionArtifact.model_construct(
        schema_version="1.0", candidate_set_ref=object(), decision=object()
    )
    with pytest.raises(HotelReplayError):
        HotelCandidateSelectionService._selection_outcome(invalid, object())


class CandidateRefMismatchRepository(RecordingArtifactRepository):
    async def get(self, ref, *, expected_model):
        read = await super().get(ref, expected_model=expected_model)
        if expected_model is HotelDiscoveryArtifact:
            wrong_operation = OperationId(
                run_id=read.ref.operation_id.run_id,
                phase=WorkflowPhase.HOTEL_SELECTION,
                logical_step="wrong_candidate_ref",
            )
            return read.model_copy(
                update={"ref": read.ref.model_copy(update={"operation_id": wrong_operation})}
            )
        return read


@pytest.mark.asyncio
async def test_candidate_read_ref_mismatch_fails_closed() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    wrapped = HotelCandidateSelectionService(
        adapter,
        CandidateRefMismatchRepository(repository),
        budget,
        clock,
    )
    with pytest.raises(HotelReplayError):
        await wrapped.record_selection(
            request_for(candidate_ref, selection_operation(RUN_ID, "candidate_ref"), HotelDeclineCommand()),
            active_candidate_set_ref=candidate_ref,
        )


class SelectionRefMismatchRepository(RecordingArtifactRepository):
    async def get(self, ref, *, expected_model):
        read = await super().get(ref, expected_model=expected_model)
        if expected_model is HotelSelectionArtifact:
            wrong_operation = OperationId(
                run_id=read.ref.operation_id.run_id,
                phase=WorkflowPhase.HOTEL_SELECTION,
                logical_step="wrong_selection_ref",
            )
            return read.model_copy(
                update={"ref": read.ref.model_copy(update={"operation_id": wrong_operation})}
            )
        return read


@pytest.mark.asyncio
async def test_selection_read_ref_mismatch_fails_closed() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    request = request_for(candidate_ref, selection_operation(RUN_ID, "read_ref"), HotelDeclineCommand())
    await service.record_selection(request, active_candidate_set_ref=candidate_ref)
    wrapped = HotelCandidateSelectionService(
        adapter,
        SelectionRefMismatchRepository(repository),
        budget,
        clock,
    )
    with pytest.raises(HotelReplayError):
        await wrapped.record_selection(request, active_candidate_set_ref=candidate_ref)


class UnexpectedSelectionLookupRepository(RecordingArtifactRepository):
    async def lookup(self, *, operation_id):
        if operation_id.logical_step.endswith("selection_lookup_unexpected"):
            return object()
        return await super().lookup(operation_id=operation_id)


@pytest.mark.asyncio
async def test_selection_unexpected_lookup_type_fails_closed() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    wrapped = HotelCandidateSelectionService(
        adapter,
        UnexpectedSelectionLookupRepository(repository),
        budget,
        clock,
    )
    request = request_for(
        candidate_ref,
        selection_operation(RUN_ID, "selection_lookup_unexpected"),
        HotelDeclineCommand(),
    )
    with pytest.raises(HotelReplayError):
        await wrapped.record_selection(request, active_candidate_set_ref=candidate_ref)


class ConflictRaceRepository(RecordingArtifactRepository):
    def __init__(self, delegate, operation_id, *, race_lookup):
        super().__init__(delegate)
        self.operation_id = operation_id
        self.first_lookup = True
        self.race_lookup = race_lookup

    async def put(self, request):
        if request.operation_id == self.operation_id:
            raise ArtifactConflictError()
        return await super().put(request)

    async def lookup(self, *, operation_id):
        if operation_id == self.operation_id:
            if self.first_lookup:
                self.first_lookup = False
                return ArtifactAbsent(operation_id=operation_id)
            if self.race_lookup == "unexpected":
                return object()
        return await super().lookup(operation_id=operation_id)


@pytest.mark.asyncio
async def test_selection_conflict_followup_unexpected_lookup_fails_closed() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "conflict_unexpected")
    wrapped = HotelCandidateSelectionService(
        adapter,
        ConflictRaceRepository(repository, operation_id, race_lookup="unexpected"),
        budget,
        clock,
    )
    with pytest.raises(HotelReplayError):
        await wrapped.record_selection(
            request_for(candidate_ref, operation_id, HotelDeclineCommand()),
            active_candidate_set_ref=candidate_ref,
        )


@pytest.mark.asyncio
async def test_selection_conflict_followup_same_decision_replays() -> None:
    service, adapter, repository, budget, clock, candidate_ref = await ready_context()
    operation_id = selection_operation(RUN_ID, "conflict_replay")
    request = request_for(candidate_ref, operation_id, HotelDeclineCommand())
    await service.record_selection(request, active_candidate_set_ref=candidate_ref)
    wrapped = HotelCandidateSelectionService(
        adapter,
        ConflictRaceRepository(repository, operation_id, race_lookup="found"),
        budget,
        clock,
    )
    replay = await wrapped.record_selection(request, active_candidate_set_ref=candidate_ref)
    assert isinstance(replay, HotelSelectionDeclined)
