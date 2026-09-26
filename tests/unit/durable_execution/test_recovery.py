from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.durable_execution.errors import (
    DurableExecutionRunBlockedError,
    DurableExecutionStateError,
)
from app.durable_execution.models import (
    DurableExecutionPauseEnvelope,
    DurableExecutionPaused,
    DurableExecutionTerminal,
    NodeRecoveryClass,
)
from app.durable_execution.runtime import DurableExecutionRuntime
from app.workflow_contracts import (
    ArtifactFound,
    ArtifactRef,
    DeliveryStatus,
    OperationId,
    OperationResultIndeterminateError,
    TravelSessionState,
    WorkflowPhase,
)
from tests.requirement_subgraph_fakes import make_confirmed_transition, make_initial_state
from tests.support.durable_execution_fakes import FakeArtifactRepository, RUN_ID, TRACE_ID, make_contract


def _operation_id() -> OperationId:
    return OperationId(run_id=RUN_ID, phase=WorkflowPhase.REQUIREMENTS, logical_step="city")


def _artifact_ref(operation_id: OperationId) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid4(),
        artifact_type="test.result",
        schema_version="1.0",
        content_hash="sha256:" + "a" * 64,
        operation_id=operation_id,
        created_at=datetime.now(UTC),
        size_bytes=2,
    )


def _state_with_refs(
    *,
    delivery_status: DeliveryStatus,
    artifact_refs: tuple[ArtifactRef, ...] = (),
) -> TravelSessionState:
    initial = make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID)
    transition = make_confirmed_transition(initial)
    return TravelSessionState(
        schema_version="2.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        phase=WorkflowPhase.REQUIREMENTS,
        delivery_status=delivery_status,
        terminal_code="workflow_completed" if delivery_status is DeliveryStatus.DEMO_READY else None,
        terminal_outcome=None,
        requirement_state=transition.state,
        requirement_outcome=transition.outcome,
        artifact_refs=artifact_refs,
    )


def _terminal_state_with_refs(*refs: ArtifactRef) -> TravelSessionState:
    return _state_with_refs(
        delivery_status=DeliveryStatus.DEMO_READY,
        artifact_refs=tuple(refs),
    )


class _RecoveryRepository:
    def __init__(self, evidence) -> None:
        self.evidence = tuple(evidence)
        self.block_facts = []

    async def get_recovery_operation_evidence(self, *, run_id):
        assert run_id == RUN_ID
        return self.evidence

    async def block_run(self, *, run_id, facts):
        assert run_id == RUN_ID
        self.block_facts.append(facts)


class _ManagedOperationRepository:
    def __init__(self, evidence, *, owner, fencing_token) -> None:
        self.evidence = tuple(evidence)
        self.owner = owner
        self.fencing_token = fencing_token
        self.transitions = []

    async def get_latest_operation_evidence(self, *, run_id, owner, fencing_token):
        assert run_id == RUN_ID
        assert owner == self.owner
        assert fencing_token == self.fencing_token
        return self.evidence

    async def append_journal_transition(self, **values):
        assert values["run_id"] == RUN_ID
        assert values["owner"] == self.owner
        assert values["fencing_token"] == self.fencing_token
        self.transitions.append(values)


def _journal_row(operation_id: OperationId, *, node_name="requirements", state="execution_permitted"):
    return {
        "operation_phase": operation_id.phase.value,
        "operation_logical_step": operation_id.logical_step,
        "node_name": node_name,
        "journal_state": state,
        "artifact_id": None,
        "artifact_type": None,
        "artifact_schema_version": None,
        "artifact_content_hash": None,
        "artifact_created_at": None,
        "artifact_size_bytes": None,
    }


def _artifact_journal_row(operation_id: OperationId, ref: ArtifactRef, *, state="artifact_confirmed"):
    row = _journal_row(operation_id, node_name="requirements", state=state)
    row.update({
        "artifact_id": ref.artifact_id,
        "artifact_type": ref.artifact_type,
        "artifact_schema_version": ref.schema_version,
        "artifact_content_hash": ref.content_hash,
        "artifact_created_at": ref.created_at,
        "artifact_size_bytes": ref.size_bytes,
    })
    return row


def _journal_guarded_contract(node_name="requirements"):
    return SimpleNamespace(node_rules=(SimpleNamespace(
        node_name=node_name,
        recovery_class=NodeRecoveryClass.JOURNAL_GUARDED,
    ),))


def _runtime(*, repository, artifacts, contract=None) -> DurableExecutionRuntime:
    runtime = object.__new__(DurableExecutionRuntime)
    runtime._repository = repository
    runtime._artifact_repository = artifacts
    runtime._contract = make_contract() if contract is None else contract
    runtime._capabilities = SimpleNamespace(drivers={})
    return runtime


def test_terminal_dto_strictly_copies_and_preserves_artifact_refs() -> None:
    ref = _artifact_ref(_operation_id())
    state = _terminal_state_with_refs(ref)

    result = DurableExecutionTerminal(state=state)

    assert result.state == state
    assert result.state is not state
    assert result.state.artifact_refs == (ref,)
    assert result.state.artifact_refs[0] is not ref
    assert result.state.artifact_refs[0].operation_id is not ref.operation_id


def test_paused_dto_strictly_copies_and_preserves_artifact_refs() -> None:
    ref = _artifact_ref(_operation_id())
    state = _state_with_refs(
        delivery_status=DeliveryStatus.AWAITING_USER,
        artifact_refs=(ref,),
    )
    pause = DurableExecutionPauseEnvelope(pause_token="a" * 32)

    result = DurableExecutionPaused(state=state, pause=pause)

    assert result.state == state
    assert result.state is not state
    assert result.state.artifact_refs == (ref,)
    assert result.state.artifact_refs[0] is not ref
    assert result.state.artifact_refs[0].operation_id is not ref.operation_id
    assert result.pause is not pause


def test_terminal_dto_without_artifact_refs_remains_valid() -> None:
    result = DurableExecutionTerminal(
        state=_state_with_refs(delivery_status=DeliveryStatus.DEMO_READY)
    )

    assert result.state.artifact_refs == ()


@pytest.mark.parametrize(
    "invalid_case",
    ("corrupt_ref", "run_mismatch", "duplicate", "unsorted"),
)
def test_terminal_dto_rejects_invalid_artifact_ref_state(invalid_case: str) -> None:
    first = _artifact_ref(_operation_id())
    state = _terminal_state_with_refs(first)

    if invalid_case == "corrupt_ref":
        invalid_ref = first.model_copy(update={"content_hash": "invalid"})
        invalid_state = state.model_copy(update={"artifact_refs": (invalid_ref,)})
    elif invalid_case == "run_mismatch":
        foreign_operation = OperationId(
            run_id=TRACE_ID,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="city",
        )
        invalid_state = state.model_copy(
            update={"artifact_refs": (_artifact_ref(foreign_operation),)}
        )
    elif invalid_case == "duplicate":
        invalid_state = state.model_copy(update={"artifact_refs": (first, first)})
    else:
        first_ref = _artifact_ref(OperationId(
            run_id=RUN_ID,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="a_step",
        ))
        second_ref = _artifact_ref(OperationId(
            run_id=RUN_ID,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="b_step",
        ))
        invalid_state = state.model_copy(update={"artifact_refs": (second_ref, first_ref)})

    with pytest.raises(ValidationError):
        DurableExecutionTerminal(state=invalid_state)


@pytest.mark.asyncio
async def test_replay_safe_node_with_no_side_effect_evidence_is_recoverable() -> None:
    repository = _RecoveryRepository(())
    artifacts = FakeArtifactRepository(outcomes={})
    runtime = _runtime(repository=repository, artifacts=artifacts)

    related = await runtime._validate_recovery_node(
        run_id=RUN_ID,
        node_name="requirements/城市",
        state=make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID),
        evidence=(),
    )

    assert related == ()
    assert artifacts.lookup_calls == []
    assert artifacts.put_calls == []
    assert repository.block_facts == []


@pytest.mark.asyncio
async def test_journal_guarded_permission_and_exact_artifact_are_reconciled_without_put() -> None:
    operation_id = _operation_id()
    ref = _artifact_ref(operation_id)
    evidence = _journal_row(operation_id)
    repository = _RecoveryRepository((evidence,))
    artifacts = FakeArtifactRepository(outcomes={operation_id: ArtifactFound(ref=ref)})
    runtime = _runtime(repository=repository, artifacts=artifacts)

    related = await runtime._validate_recovery_node(
        run_id=RUN_ID,
        node_name="requirements",
        state=make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID),
        evidence=(evidence,),
    )

    assert related == (evidence,)
    assert artifacts.lookup_calls == [operation_id]
    assert artifacts.put_calls == []
    assert repository.block_facts == []


@pytest.mark.asyncio
async def test_artifact_confirmed_closes_with_exact_checkpoint_and_current_fence() -> None:
    operation_id = _operation_id()
    ref = _artifact_ref(operation_id)
    owner = uuid4()
    fencing_token = 7
    pointer = ("", "checkpoint-7", "sha256:" + "b" * 64)
    repository = _ManagedOperationRepository(
        (_artifact_journal_row(operation_id, ref),),
        owner=owner,
        fencing_token=fencing_token,
    )
    runtime = _runtime(
        repository=repository,
        artifacts=FakeArtifactRepository(outcomes={}),
        contract=_journal_guarded_contract(),
    )

    await runtime._confirm_managed_operations(
        run_id=RUN_ID,
        state=_terminal_state_with_refs(ref),
        pointer=pointer,
        owner=owner,
        fencing_token=fencing_token,
    )

    expected_artifact = {
        "artifact_id": ref.artifact_id,
        "artifact_type": ref.artifact_type,
        "artifact_schema_version": ref.schema_version,
        "artifact_content_hash": ref.content_hash,
        "artifact_created_at": ref.created_at,
        "artifact_size_bytes": ref.size_bytes,
    }
    assert [item["journal_state"] for item in repository.transitions] == [
        "checkpoint_confirmed",
        "completed",
    ]
    assert all(item["operation_phase"] == operation_id.phase.value for item in repository.transitions)
    assert all(item["logical_step"] == operation_id.logical_step for item in repository.transitions)
    assert all(item["node_name"] == "requirements" for item in repository.transitions)
    assert all(item["artifact"] == expected_artifact for item in repository.transitions)
    assert all(item["checkpoint"] == pointer for item in repository.transitions)
    assert all(item["owner"] == owner and item["fencing_token"] == fencing_token for item in repository.transitions)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_case", ("missing_ref", "artifact_mismatch", "unregistered_node"))
async def test_artifact_confirmed_ref_or_node_conflict_is_rejected(invalid_case: str) -> None:
    operation_id = _operation_id()
    ref = _artifact_ref(operation_id)
    row = _artifact_journal_row(operation_id, ref)
    node_name = "requirements"
    state = _terminal_state_with_refs(ref)
    if invalid_case == "missing_ref":
        state = _terminal_state_with_refs()
    elif invalid_case == "artifact_mismatch":
        row["artifact_content_hash"] = "sha256:" + "c" * 64
    else:
        row["node_name"] = "unregistered"

    owner = uuid4()
    fencing_token = 7
    repository = _ManagedOperationRepository(
        (row,), owner=owner, fencing_token=fencing_token,
    )
    runtime = _runtime(
        repository=repository,
        artifacts=FakeArtifactRepository(outcomes={}),
        contract=_journal_guarded_contract(node_name),
    )

    with pytest.raises(DurableExecutionStateError):
        await runtime._confirm_managed_operations(
            run_id=RUN_ID,
            state=state,
            pointer=("", "checkpoint-7", "sha256:" + "b" * 64),
            owner=owner,
            fencing_token=fencing_token,
        )

    assert repository.transitions == []


@pytest.mark.asyncio
async def test_checkpoint_confirmed_exact_retry_and_completed_are_idempotent() -> None:
    operation_id = _operation_id()
    ref = _artifact_ref(operation_id)
    owner = uuid4()
    fencing_token = 7
    pointer = ("", "checkpoint-7", "sha256:" + "b" * 64)
    confirmed = _artifact_journal_row(operation_id, ref, state="checkpoint_confirmed")
    confirmed.update({"checkpoint_ns": pointer[0], "checkpoint_id": pointer[1], "checkpoint_digest": pointer[2]})
    repository = _ManagedOperationRepository(
        (confirmed,), owner=owner, fencing_token=fencing_token,
    )
    runtime = _runtime(
        repository=repository,
        artifacts=FakeArtifactRepository(outcomes={}),
        contract=_journal_guarded_contract(),
    )

    await runtime._confirm_managed_operations(
        run_id=RUN_ID,
        state=_terminal_state_with_refs(ref),
        pointer=pointer,
        owner=owner,
        fencing_token=fencing_token,
    )

    assert [item["journal_state"] for item in repository.transitions] == [
        "checkpoint_confirmed",
        "completed",
    ]
    assert repository.transitions[0]["checkpoint"] == pointer

    completed = _artifact_journal_row(operation_id, ref, state="completed")
    repository = _ManagedOperationRepository(
        (completed,), owner=owner, fencing_token=fencing_token,
    )
    runtime = _runtime(
        repository=repository,
        artifacts=FakeArtifactRepository(outcomes={}),
        contract=_journal_guarded_contract(),
    )

    await runtime._confirm_managed_operations(
        run_id=RUN_ID,
        state=_terminal_state_with_refs(ref),
        pointer=pointer,
        owner=owner,
        fencing_token=fencing_token,
    )

    assert repository.transitions == []


@pytest.mark.asyncio
async def test_journal_guarded_permission_without_artifact_blocks_as_indeterminate() -> None:
    operation_id = _operation_id()
    evidence = _journal_row(operation_id)
    repository = _RecoveryRepository((evidence,))
    artifacts = FakeArtifactRepository(outcomes={})
    runtime = _runtime(repository=repository, artifacts=artifacts)

    with pytest.raises(OperationResultIndeterminateError) as caught:
        await runtime._validate_recovery_node(
            run_id=RUN_ID,
            node_name="requirements",
            state=make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID),
            evidence=(evidence,),
        )
    assert type(caught.value) is OperationResultIndeterminateError

    assert artifacts.lookup_calls == [operation_id]
    assert artifacts.put_calls == []
    assert len(repository.block_facts) == 1
    assert repository.block_facts[0].artifact_missing_after_permission is True
    assert repository.block_facts[0].operation_id == operation_id


@pytest.mark.asyncio
async def test_journal_guarded_operation_id_conflict_blocks_without_put() -> None:
    operation_id = _operation_id()
    other_operation_id = OperationId(
        run_id=RUN_ID,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="another_city",
    )
    evidence = _journal_row(operation_id)
    repository = _RecoveryRepository((evidence,))
    artifacts = FakeArtifactRepository(
        outcomes={operation_id: ArtifactFound(ref=_artifact_ref(other_operation_id))}
    )
    runtime = _runtime(repository=repository, artifacts=artifacts)

    with pytest.raises(DurableExecutionStateError) as caught:
        await runtime._validate_recovery_node(
            run_id=RUN_ID,
            node_name="requirements",
            state=make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID),
            evidence=(evidence,),
        )
    assert type(caught.value) is DurableExecutionStateError

    assert artifacts.lookup_calls == [operation_id]
    assert artifacts.put_calls == []
    assert repository.block_facts[0].cause.reason.value == "artifact_invalid"


@pytest.mark.asyncio
async def test_nonrecoverable_qualified_node_blocks_without_artifact_lookup() -> None:
    repository = _RecoveryRepository(())
    artifacts = FakeArtifactRepository(outcomes={})
    contract = SimpleNamespace(node_rules=(SimpleNamespace(
        node_name="requirements/process_initial_turn",
        recovery_class=NodeRecoveryClass.NON_RECOVERABLE,
    ),))
    runtime = _runtime(repository=repository, artifacts=artifacts, contract=contract)

    with pytest.raises(OperationResultIndeterminateError) as caught:
        await runtime._validate_recovery_node(
            run_id=RUN_ID,
            node_name="requirements/process_initial_turn",
            state=make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID),
            evidence=(),
        )
    assert type(caught.value) is OperationResultIndeterminateError

    assert artifacts.lookup_calls == []
    assert artifacts.put_calls == []
    assert repository.block_facts[0].cause.reason.value == "non_recoverable_dispatch"
