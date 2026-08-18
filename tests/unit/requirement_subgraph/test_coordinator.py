from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import app.requirement_subgraph.coordinator as coordinator_module
from app.requirements import (
    ConfirmedTravelRequirement,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainState,
    RequirementDomainTransition,
    RequirementInvalidated,
)
from app.workflow_contracts import (
    BudgetConsumed as WorkflowBudgetConsumed,
    BudgetExceeded as WorkflowBudgetExceeded,
    BudgetConsumeRequest,
    DeliveryStatus,
    ExecutionBudgetKey,
    ExecutionBudgetScope,
    ExecutionBudgetSnapshot,
    OperationId,
    OperationResultIndeterminateError,
    WorkflowPhase,
)
from app.requirement_subgraph import (
    RequirementClarificationResume,
    RequirementPauseEnvelope,
    RequirementResumeEnvelope,
    RequirementSubgraphCapacityExceededError,
    RequirementSubgraphConfigurationError,
    RequirementSubgraphInputError,
    RequirementSubgraphResumeRejectedError,
    RequirementSubgraphRunFaultedError,
    RequirementSubgraphStateError,
    RequirementSubgraphCoordinator,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphBudgetExceeded,
    RequirementSubgraphConfirmed,
    REQUIREMENTS_CLARIFICATION_ROUND_KEY,
)
from tests.requirement_subgraph_fakes import (
    PerRunBudgetManagerFactory,
    RecordingRequirementSubgraphSignalSink,
    ScriptedExecutionBudgetManager,
    ScriptedRequirementDomainService,
    SequenceSecretHex,
    make_initial_state,
    make_turn,
)


def _coordinator(
    *,
    sink: RecordingRequirementSubgraphSignalSink | None = None,
    factory: PerRunBudgetManagerFactory | None = None,
    service: ScriptedRequirementDomainService | None = None,
    max_runs: int = 256,
) -> tuple[RequirementSubgraphCoordinator, RecordingRequirementSubgraphSignalSink, PerRunBudgetManagerFactory]:
    actual_sink = sink or RecordingRequirementSubgraphSignalSink()
    actual_factory = factory or PerRunBudgetManagerFactory(
        lambda _run_id: ScriptedExecutionBudgetManager()
    )
    actual_service = service or ScriptedRequirementDomainService()
    return (
        RequirementSubgraphCoordinator(
            domain_service=actual_service,
            execution_budget_manager_factory=actual_factory,
            signal_sink=actual_sink,
            limits=RequirementSubgraphCoordinatorLimits(max_runs=max_runs),
        ),
        actual_sink,
        actual_factory,
    )


def _waiting_state(initial, message: str = "请补充"):
    domain_state = RequirementDomainState()
    transition = RequirementDomainTransition(
        state=domain_state,
        outcome=NeedClarification(
            requirement=domain_state.requirement,
            metadata=domain_state.metadata,
            issues=(),
            message=message,
        ),
    )
    return initial.model_copy(
        update={
            "delivery_status": DeliveryStatus.AWAITING_USER,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
        }
    )


def _confirmed_state(initial):
    domain_state = RequirementDomainState(confirmation_status="confirmed")
    transition = RequirementDomainTransition(
        state=domain_state,
        outcome=ConfirmedTravelRequirement(
            schema_version="1.0",
            requirement=domain_state.requirement,
            metadata_without_raw_evidence=domain_state.metadata,
            confirmation_summary_digest="digest",
        ),
    )
    return initial.model_copy(
        update={
            "delivery_status": DeliveryStatus.IN_PROGRESS,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
        }
    )


def _confirmation_state(initial, summary: str = "请确认"):
    domain_state = RequirementDomainState(confirmation_status="awaiting_confirmation")
    transition = RequirementDomainTransition(
        state=domain_state,
        outcome=NeedConfirmation(
            requirement=domain_state.requirement,
            metadata=domain_state.metadata,
            summary=summary,
        ),
    )
    return initial.model_copy(
        update={
            "delivery_status": DeliveryStatus.AWAITING_USER,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
        }
    )


def _invalidated_state(initial, summary: str = "需求已变化"):
    domain_state = RequirementDomainState(confirmation_status="awaiting_confirmation")
    after_digest = hashlib.sha256(
        domain_state.requirement.model_dump_json(exclude_none=False).encode()
    ).hexdigest()
    transition = RequirementDomainTransition(
        state=domain_state,
        outcome=RequirementInvalidated(
            changed_fields=(),
            before_digest="before",
            after_digest=after_digest,
            reason="changed",
            summary=summary,
        ),
    )
    return initial.model_copy(
        update={
            "delivery_status": DeliveryStatus.AWAITING_USER,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
        }
    )


def _confirmation_resume_envelope(waiting, token):
    return RequirementResumeEnvelope(
        pause_token=token,
        resume=coordinator_module.RequirementConfirmationResume(
            action=RequirementConfirmation(decision="accept", revision_text=None),
        ),
    )


async def _start_and_pause_as(coordinator, initial, waiting, interrupt):
    async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
        coordinator._ensure_current_pause(state=initial, interrupt=interrupt)
        coordinator._set_latest_state(waiting)
        return coordinator.resolve_result(
            state=waiting,
            interrupt_value=interrupt.model_dump(mode="json", exclude_none=False),
        )


def _assert_paused_record_unchanged(record, waiting, token):
    assert record.status == "paused"
    assert record.latest_state == waiting
    assert record.action_slot is None
    assert record.pause_token == token
    assert record.active_lease is None
    assert not record.operation_lock.locked()


async def _assert_resume_rejected_without_side_effects(
    coordinator,
    *,
    state,
    envelope,
    waiting,
    token,
    factory,
    service,
    sink,
    run_id,
    signal_count,
):
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        async with coordinator.resume_operation(state=state, envelope=envelope):
            pass
    _assert_paused_record_unchanged(coordinator._records[run_id], waiting, token)
    assert factory.managers[run_id].requests == []
    assert service.process_calls == []
    assert service.confirm_calls == []
    assert len(sink.calls) == signal_count


def _resume_envelope(waiting, token):
    return RequirementResumeEnvelope(
        pause_token=token,
        resume=RequirementClarificationResume(turn=make_turn(waiting)),
    )


def _assert_fault_tombstone(record):
    assert record.status == "faulted"
    assert record.latest_state is None
    assert record.turn_slot is None
    assert record.action_slot is None
    assert record.pause_token is None
    assert record.operation_nonce is None
    assert record.interrupt_descriptor is None
    assert record.pending_result is None
    assert record.active_lease is None
    assert not record.operation_lock.locked()


async def _start_and_pause(coordinator, initial):
    interrupt = {"schema_version": "1.0", "interrupt_type": "clarification", "message": "请补充"}
    waiting = _waiting_state(initial)
    async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
        coordinator._ensure_current_pause(
            state=initial,
            interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
        )
        coordinator._set_latest_state(waiting)
        result = coordinator.resolve_result(state=waiting, interrupt_value=interrupt)
    return waiting, result


@pytest.mark.asyncio
async def test_start_admits_once_emits_after_commit_and_resolves_once():
    coordinator, sink, factory = _coordinator()
    initial = make_initial_state()
    waiting, result = await _start_and_pause(coordinator, initial)
    assert result.result_type == "paused"
    assert result.pause.pause_token not in repr(result)
    assert len(factory.created) == 1
    assert [signal.signal_type for signal in sink.calls] == ["start_accepted"]
    assert coordinator._records[initial.run_id].status == "paused"
    assert coordinator._records[initial.run_id].latest_state == waiting


@pytest.mark.asyncio
async def test_resume_rejects_confirmation_resume_for_clarification_pause_without_side_effects():
    service = ScriptedRequirementDomainService()
    coordinator, sink, factory = _coordinator(service=service)
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    token = coordinator._records[initial.run_id].pause_token
    assert token is not None
    await _assert_resume_rejected_without_side_effects(
        coordinator,
        state=waiting,
        envelope=_confirmation_resume_envelope(waiting, token),
        waiting=waiting,
        token=token,
        factory=factory,
        service=service,
        sink=sink,
        run_id=initial.run_id,
        signal_count=1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_kind", ["confirmation", "invalidated"])
async def test_resume_rejects_clarification_resume_for_confirmation_pause_without_side_effects(
    pause_kind,
):
    service = ScriptedRequirementDomainService()
    coordinator, sink, factory = _coordinator(service=service)
    initial = make_initial_state()
    waiting = (
        _confirmation_state(initial)
        if pause_kind == "confirmation"
        else _invalidated_state(initial)
    )
    interrupt = (
        coordinator_module.RequirementConfirmationInterrupt(
            source_outcome_type=(
                "need_confirmation" if pause_kind == "confirmation" else "invalidated"
            ),
            summary="请确认" if pause_kind == "confirmation" else "需求已变化",
        )
    )
    await _start_and_pause_as(coordinator, initial, waiting, interrupt)
    token = coordinator._records[initial.run_id].pause_token
    assert token is not None
    await _assert_resume_rejected_without_side_effects(
        coordinator,
        state=waiting,
        envelope=_resume_envelope(waiting, token),
        waiting=waiting,
        token=token,
        factory=factory,
        service=service,
        sink=sink,
        run_id=initial.run_id,
        signal_count=1,
    )


@pytest.mark.asyncio
async def test_resume_rejects_run_a_token_for_run_b_without_mutating_either_pause():
    service = ScriptedRequirementDomainService()
    coordinator, sink, factory = _coordinator(service=service)
    run_a = make_initial_state()
    run_b = make_initial_state()
    waiting_a, _ = await _start_and_pause(coordinator, run_a)
    waiting_b, _ = await _start_and_pause(coordinator, run_b)
    token_a = coordinator._records[run_a.run_id].pause_token
    token_b = coordinator._records[run_b.run_id].pause_token
    assert token_a is not None and token_b is not None
    await _assert_resume_rejected_without_side_effects(
        coordinator,
        state=waiting_b,
        envelope=_resume_envelope(waiting_b, token_a),
        waiting=waiting_b,
        token=token_b,
        factory=factory,
        service=service,
        sink=sink,
        run_id=run_b.run_id,
        signal_count=2,
    )
    _assert_paused_record_unchanged(coordinator._records[run_a.run_id], waiting_a, token_a)
    assert factory.managers[run_a.run_id].requests == []


@pytest.mark.asyncio
async def test_rotated_old_token_is_rejected_without_new_side_effects(monkeypatch):
    sequence = SequenceSecretHex(["1" * 32, "2" * 32, "3" * 32, "4" * 32])
    monkeypatch.setattr(coordinator_module, "_new_secret_hex", sequence)
    service = ScriptedRequirementDomainService()
    coordinator, sink, factory = _coordinator(service=service)
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    old_token = coordinator._records[initial.run_id].pause_token
    assert old_token == "1" * 32
    async with coordinator.resume_operation(
        state=waiting,
        envelope=_resume_envelope(waiting, old_token),
    ):
        coordinator._take_resume()
        interrupt = coordinator_module.RequirementClarificationInterrupt(message="请补充")
        coordinator._ensure_current_pause(state=waiting, interrupt=interrupt)
        coordinator._take_pause_for_replay()
        coordinator._node_replayed_operation()
        coordinator._ensure_current_pause(state=waiting, interrupt=interrupt)
        coordinator.resolve_result(
            state=waiting,
            interrupt_value=interrupt.model_dump(mode="json", exclude_none=False),
        )
    new_token = coordinator._records[initial.run_id].pause_token
    assert new_token == "3" * 32
    signal_count = len(sink.calls)
    await _assert_resume_rejected_without_side_effects(
        coordinator,
        state=waiting,
        envelope=_resume_envelope(waiting, old_token),
        waiting=waiting,
        token=new_token,
        factory=factory,
        service=service,
        sink=sink,
        run_id=initial.run_id,
        signal_count=signal_count,
    )


@pytest.mark.asyncio
async def test_used_token_after_terminal_resolution_is_rejected_without_reopening_terminal():
    service = ScriptedRequirementDomainService()
    coordinator, sink, factory = _coordinator(service=service)
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    token = coordinator._records[initial.run_id].pause_token
    assert token is not None
    async with coordinator.resume_operation(
        state=waiting,
        envelope=_resume_envelope(waiting, token),
    ):
        coordinator._take_resume()
        terminal_state = _confirmed_state(waiting)
        coordinator._set_latest_state(terminal_state)
        coordinator._set_local_result(RequirementSubgraphConfirmed(state=terminal_state))
        coordinator.resolve_result(state=terminal_state, interrupt_value=None)
    record = coordinator._records[initial.run_id]
    assert record.status == "confirmed"
    signal_count = len(sink.calls)
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        async with coordinator.resume_operation(
            state=waiting,
            envelope=_resume_envelope(waiting, token),
        ):
            pass
    assert record.status == "confirmed"
    assert record.latest_state is None
    assert record.action_slot is None
    assert record.pause_token is None
    assert record.operation_nonce is None
    assert record.active_lease is None
    assert not record.operation_lock.locked()
    assert factory.managers[initial.run_id].requests == []
    assert service.process_calls == []
    assert service.confirm_calls == []
    assert len(sink.calls) == signal_count


@pytest.mark.asyncio
async def test_same_run_resume_operations_are_serialized_until_first_lease_releases():
    coordinator, _, _ = _coordinator()
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    token = coordinator._records[initial.run_id].pause_token
    assert token is not None
    envelope = _resume_envelope(waiting, token)
    first_entered = asyncio.Event()
    second_attempted = asyncio.Event()
    release = asyncio.Event()

    async def first_resume():
        with pytest.raises(RequirementSubgraphStateError):
            async with coordinator.resume_operation(state=waiting, envelope=envelope):
                first_entered.set()
                await release.wait()

    async def second_resume():
        second_attempted.set()
        with pytest.raises(RequirementSubgraphRunFaultedError):
            async with coordinator.resume_operation(state=waiting, envelope=envelope):
                pass

    first_task = asyncio.create_task(first_resume())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    second_task = asyncio.create_task(second_resume())
    await asyncio.wait_for(second_attempted.wait(), timeout=1)
    assert not second_task.done()
    assert coordinator._records[initial.run_id].operation_lock.locked()
    release.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)
    _assert_fault_tombstone(coordinator._records[initial.run_id])


@pytest.mark.asyncio
async def test_different_runs_can_hold_resume_leases_concurrently():
    coordinator, _, _ = _coordinator()
    first = make_initial_state()
    second = make_initial_state()
    first_waiting, _ = await _start_and_pause(coordinator, first)
    second_waiting, _ = await _start_and_pause(coordinator, second)
    first_token = coordinator._records[first.run_id].pause_token
    second_token = coordinator._records[second.run_id].pause_token
    assert first_token is not None and second_token is not None
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_resume(waiting, entered):
        token = coordinator._records[waiting.run_id].pause_token
        assert token is not None
        async with coordinator.resume_operation(
            state=waiting,
            envelope=_resume_envelope(waiting, token),
        ):
            coordinator._take_resume()
            coordinator._ensure_current_pause(
                state=waiting,
                interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
            )
            result = coordinator.resolve_result(
                state=waiting,
                interrupt_value={
                    "schema_version": "1.0",
                    "interrupt_type": "clarification",
                    "message": "请补充",
                },
            )
            assert result.result_type == "paused"
            entered.set()
            await release.wait()

    first_task = asyncio.create_task(hold_resume(first_waiting, first_entered))
    second_task = asyncio.create_task(hold_resume(second_waiting, second_entered))
    await asyncio.wait_for(
        asyncio.gather(first_entered.wait(), second_entered.wait()), timeout=1
    )
    assert coordinator._records[first.run_id].operation_lock.locked()
    assert coordinator._records[second.run_id].operation_lock.locked()
    release.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)
    assert coordinator._records[first.run_id].status == "paused"
    assert coordinator._records[second.run_id].status == "paused"


@pytest.mark.asyncio
async def test_two_concurrent_starts_do_not_oversell_admission_capacity():
    coordinator, sink, factory = _coordinator(max_runs=1)
    first = make_initial_state()
    second = make_initial_state()
    first_entered = asyncio.Event()
    second_attempted = asyncio.Event()
    release = asyncio.Event()

    async def first_start():
        with pytest.raises(RequirementSubgraphStateError):
            async with coordinator.start_operation(state=first, turn=make_turn(first)):
                first_entered.set()
                await release.wait()

    async def second_start():
        second_attempted.set()
        with pytest.raises(RequirementSubgraphCapacityExceededError):
            async with coordinator.start_operation(state=second, turn=make_turn(second)):
                pass

    first_task = asyncio.create_task(first_start())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    second_task = asyncio.create_task(second_start())
    await asyncio.wait_for(second_attempted.wait(), timeout=1)
    release.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)
    assert len(factory.created) == 1
    assert [signal.signal_type for signal in sink.calls] == ["start_accepted"]
    _assert_fault_tombstone(coordinator._records[first.run_id])
    assert second.run_id not in coordinator._records


@pytest.mark.asyncio
async def test_resume_validates_token_branch_current_and_emits_before_command():
    coordinator, sink, _ = _coordinator()
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    record = coordinator._records[initial.run_id]
    token = record.pause_token
    assert token is not None
    envelope = _resume_envelope(waiting, token)
    async with coordinator.resume_operation(state=waiting, envelope=envelope) as command:
        assert command.resume == "requirement_resume_v1"
        taken = coordinator._take_resume()
        assert taken.pause_token == token
        coordinator._ensure_current_pause(
            state=waiting,
            interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
        )
        result = coordinator.resolve_result(
            state=waiting,
            interrupt_value={
                "schema_version": "1.0",
                "interrupt_type": "clarification",
                "message": "请补充",
            },
        )
        assert result.result_type == "paused"
    assert [signal.signal_type for signal in sink.calls] == [
        "start_accepted",
        "resume_accepted",
    ]


@pytest.mark.asyncio
async def test_resume_rechecks_faulted_state_after_wait_and_never_reopens_tombstone():
    coordinator, _, _ = _coordinator()
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    token = coordinator._records[initial.run_id].pause_token
    assert token is not None
    envelope = _resume_envelope(waiting, token)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first_resume():
        with pytest.raises(RequirementSubgraphStateError):
            async with coordinator.resume_operation(state=waiting, envelope=envelope):
                entered.set()
                await release.wait()

    async def second_resume():
        with pytest.raises(RequirementSubgraphRunFaultedError):
            async with coordinator.resume_operation(state=waiting, envelope=envelope):
                pass

    first_task = asyncio.create_task(first_resume())
    await asyncio.wait_for(entered.wait(), timeout=1)
    second_task = asyncio.create_task(second_resume())
    release.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)
    _assert_fault_tombstone(coordinator._records[initial.run_id])


@pytest.mark.asyncio
async def test_resume_rechecks_settled_state_after_wait_without_overwriting_terminal():
    coordinator, _, _ = _coordinator()
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    token = coordinator._records[initial.run_id].pause_token
    assert token is not None
    envelope = _resume_envelope(waiting, token)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first_resume():
        async with coordinator.resume_operation(state=waiting, envelope=envelope):
            coordinator._take_resume()
            terminal_state = _confirmed_state(waiting)
            coordinator._set_latest_state(terminal_state)
            coordinator._set_local_result(RequirementSubgraphConfirmed(state=terminal_state))
            coordinator.resolve_result(state=terminal_state, interrupt_value=None)
            entered.set()
            await release.wait()

    async def second_resume():
        with pytest.raises(RequirementSubgraphResumeRejectedError):
            async with coordinator.resume_operation(state=waiting, envelope=envelope):
                pass

    first_task = asyncio.create_task(first_resume())
    await asyncio.wait_for(entered.wait(), timeout=1)
    second_task = asyncio.create_task(second_resume())
    release.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)
    record = coordinator._records[initial.run_id]
    assert record.status == "confirmed"
    assert record.latest_state is None
    assert record.action_slot is None
    assert not record.operation_lock.locked()


@pytest.mark.asyncio
async def test_resume_rechecks_new_pause_after_wait_without_overwriting_rotated_pause(monkeypatch):
    sequence = SequenceSecretHex(["1" * 32, "2" * 32, "3" * 32, "4" * 32])
    monkeypatch.setattr(coordinator_module, "_new_secret_hex", sequence)
    coordinator, _, _ = _coordinator()
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    old_token = coordinator._records[initial.run_id].pause_token
    assert old_token == "1" * 32
    envelope = _resume_envelope(waiting, old_token)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first_resume():
        async with coordinator.resume_operation(state=waiting, envelope=envelope):
            coordinator._take_resume()
            interrupt = coordinator_module.RequirementClarificationInterrupt(message="请补充")
            coordinator._ensure_current_pause(state=waiting, interrupt=interrupt)
            coordinator._take_pause_for_replay()
            coordinator._node_replayed_operation()
            coordinator._ensure_current_pause(state=waiting, interrupt=interrupt)
            coordinator.resolve_result(
                state=waiting,
                interrupt_value={
                    "schema_version": "1.0",
                    "interrupt_type": "clarification",
                    "message": "请补充",
                },
            )
            entered.set()
            await release.wait()

    async def second_resume():
        with pytest.raises(RequirementSubgraphResumeRejectedError):
            async with coordinator.resume_operation(state=waiting, envelope=envelope):
                pass

    first_task = asyncio.create_task(first_resume())
    await asyncio.wait_for(entered.wait(), timeout=1)
    second_task = asyncio.create_task(second_resume())
    release.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)
    record = coordinator._records[initial.run_id]
    assert record.status == "paused"
    assert record.pause_token == "3" * 32
    assert record.operation_nonce == "4" * 32
    assert record.action_slot is None
    assert not record.operation_lock.locked()


@pytest.mark.asyncio
async def test_accepted_resume_sink_failure_preserves_exception_and_faults_once():
    sink = RecordingRequirementSubgraphSignalSink(fail_on_call=2)
    coordinator, _, _ = _coordinator(sink=sink)
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    token = coordinator._records[initial.run_id].pause_token
    assert token is not None
    with pytest.raises(RuntimeError, match="sink failure"):
        async with coordinator.resume_operation(
            state=waiting,
            envelope=_resume_envelope(waiting, token),
        ):
            pass
    record = coordinator._records[initial.run_id]
    _assert_fault_tombstone(record)
    with pytest.raises(RequirementSubgraphRunFaultedError):
        async with coordinator.resume_operation(
            state=waiting,
            envelope=_resume_envelope(waiting, token),
        ):
            pass


@pytest.mark.asyncio
async def test_accepted_resume_sink_non_none_faults_without_reopening():
    sink = RecordingRequirementSubgraphSignalSink(return_value="not-none", return_on_call=2)
    coordinator, _, _ = _coordinator(sink=sink)
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    token = coordinator._records[initial.run_id].pause_token
    assert token is not None
    with pytest.raises(RequirementSubgraphStateError):
        async with coordinator.resume_operation(
            state=waiting,
            envelope=_resume_envelope(waiting, token),
        ):
            pass
    _assert_fault_tombstone(coordinator._records[initial.run_id])


@pytest.mark.asyncio
async def test_operation_scope_drops_complete_input_references_on_all_entry_paths():
    initial = make_initial_state()
    coordinator, _, _ = _coordinator()
    turn = make_turn(initial, user_message="scope_secret")
    scope = coordinator.start_operation(state=initial, turn=turn)
    await scope.__aenter__()
    assert scope._state is None
    assert scope._turn is None
    assert scope._envelope is None
    coordinator._take_turn()
    waiting = _waiting_state(initial)
    coordinator._ensure_current_pause(
        state=initial,
        interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
    )
    coordinator._set_latest_state(waiting)
    coordinator.resolve_result(
        state=waiting,
        interrupt_value={"schema_version": "1.0", "interrupt_type": "clarification", "message": "请补充"},
    )
    await scope.__aexit__(None, None, None)
    assert coordinator._records[initial.run_id].turn_slot is None

    bad_scope = coordinator.start_operation(state=object(), turn=turn)  # type: ignore[arg-type]
    with pytest.raises(RequirementSubgraphInputError):
        await bad_scope.__aenter__()
    assert bad_scope._state is None
    assert bad_scope._turn is None
    assert bad_scope._envelope is None

    failing_sink = RecordingRequirementSubgraphSignalSink(fail_on_call=1)
    failing_coordinator, _, _ = _coordinator(sink=failing_sink)
    failing_scope = failing_coordinator.start_operation(
        state=make_initial_state(),
        turn=make_turn(initial, user_message="sink_secret"),
    )
    with pytest.raises(RuntimeError, match="sink failure"):
        await failing_scope.__aenter__()
    assert failing_scope._state is None
    assert failing_scope._turn is None
    assert failing_scope._envelope is None

    body_coordinator, _, _ = _coordinator()
    body_state = make_initial_state()
    body_scope = body_coordinator.start_operation(
        state=body_state,
        turn=make_turn(body_state, user_message="body_secret"),
    )
    await body_scope.__aenter__()
    body_error = RuntimeError("body failure")
    await body_scope.__aexit__(RuntimeError, body_error, None)
    assert body_scope._state is None
    assert body_scope._turn is None
    assert body_scope._envelope is None
    _assert_fault_tombstone(body_coordinator._records[body_state.run_id])


@pytest.mark.asyncio
async def test_async_with_body_exception_is_original_instance_and_not_masked_by_missing_resolve():
    coordinator, _, _ = _coordinator()
    state = make_initial_state()
    scope = coordinator.start_operation(state=state, turn=make_turn(state, user_message="body_secret"))

    class CountingLock:
        def __init__(self, delegate):
            self.delegate = delegate
            self.release_count = 0

        async def acquire(self):
            return await self.delegate.acquire()

        def release(self):
            self.release_count += 1
            return self.delegate.release()

        def locked(self):
            return self.delegate.locked()

    body_error = RuntimeError("preserve this exact instance")
    try:
        async with scope:
            record = coordinator._records[state.run_id]
            counting_lock = CountingLock(record.operation_lock)
            record.operation_lock = counting_lock
            raise body_error
    except RuntimeError as caught:
        assert caught is body_error
    else:
        pytest.fail("body exception was swallowed")
    assert scope._state is None
    assert scope._turn is None
    assert scope._envelope is None
    _assert_fault_tombstone(coordinator._records[state.run_id])
    assert counting_lock.release_count == 1


@pytest.mark.asyncio
async def test_same_pause_reentry_uses_zero_new_secrets_and_next_pause_rotates(monkeypatch):
    sequence = SequenceSecretHex(["1" * 32, "2" * 32, "3" * 32, "4" * 32])
    monkeypatch.setattr(coordinator_module, "_new_secret_hex", sequence)
    coordinator, _, _ = _coordinator()
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    assert sequence.calls == 2
    record = coordinator._records[initial.run_id]
    token = record.pause_token
    envelope = RequirementResumeEnvelope(
        pause_token=token,
        resume=RequirementClarificationResume(turn=make_turn(waiting)),
    )
    async with coordinator.resume_operation(state=waiting, envelope=envelope):
        coordinator._ensure_current_pause(
            state=waiting,
            interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
        )
        assert sequence.calls == 2
        nonce, _ = coordinator._take_pause_for_replay()
        assert nonce == "2" * 32
        operation = coordinator._node_replayed_operation()
        assert operation.logical_step == "requirement_pause_" + "2" * 32
        next_interrupt = coordinator_module.RequirementClarificationInterrupt(message="请补充")
        coordinator._ensure_current_pause(state=waiting, interrupt=next_interrupt)
        assert sequence.calls == 4
        coordinator.resolve_result(
            state=waiting,
            interrupt_value={
                "schema_version": "1.0",
                "interrupt_type": "clarification",
                "message": "请补充",
            },
        )


@pytest.mark.asyncio
async def test_missing_or_duplicate_resolve_faults_and_clears_sensitive_slots():
    coordinator, _, _ = _coordinator()
    initial = make_initial_state()
    with pytest.raises(RequirementSubgraphStateError):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            pass
    record = coordinator._records[initial.run_id]
    assert record.status == "faulted"
    assert record.turn_slot is None and record.pause_token is None

    coordinator2, _, _ = _coordinator()
    initial2 = make_initial_state()
    waiting2, _ = await _start_and_pause(coordinator2, initial2)
    with pytest.raises(RequirementSubgraphInputError):
        async with coordinator2.start_operation(state=initial2, turn=make_turn(initial2)):
            pass
    assert coordinator2._records[initial2.run_id].status == "paused"
    assert waiting2.run_id == initial2.run_id


@pytest.mark.asyncio
async def test_second_resolve_faults_same_lease():
    coordinator, _, _ = _coordinator()
    initial = make_initial_state()
    waiting = _waiting_state(initial)
    async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
        coordinator._ensure_current_pause(
            state=initial,
            interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
        )
        coordinator._set_latest_state(waiting)
        value = {"schema_version": "1.0", "interrupt_type": "clarification", "message": "请补充"}
        coordinator.resolve_result(state=waiting, interrupt_value=value)
        with pytest.raises(RequirementSubgraphStateError):
            coordinator.resolve_result(state=waiting, interrupt_value=value)
    assert coordinator._records[initial.run_id].status == "faulted"


@pytest.mark.asyncio
async def test_factory_surface_and_factory_exception_fail_before_capacity_or_signal():
    bad_factory = PerRunBudgetManagerFactory(lambda _run_id: object())
    coordinator, sink, bad_factory = _coordinator(factory=bad_factory)
    initial = make_initial_state()
    with pytest.raises(RequirementSubgraphConfigurationError):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            pass
    assert sink.calls == []
    assert coordinator._records == {}

    factory_error = RuntimeError("factory error")
    factory = PerRunBudgetManagerFactory(
        lambda _run_id: ScriptedExecutionBudgetManager(), error=factory_error
    )
    coordinator2, sink2, _ = _coordinator(factory=factory)
    with pytest.raises(RuntimeError, match="factory error"):
        async with coordinator2.start_operation(state=initial, turn=make_turn(initial)):
            pass
    assert sink2.calls == []
    assert coordinator2._records == {}


@pytest.mark.asyncio
async def test_capacity_256_boundary_and_existing_run_remains_readable():
    coordinator, sink, factory = _coordinator(max_runs=2)
    first = make_initial_state()
    second = make_initial_state()
    third = make_initial_state()
    first_waiting, _ = await _start_and_pause(coordinator, first)
    await _start_and_pause(coordinator, second)
    with pytest.raises(RequirementSubgraphCapacityExceededError):
        async with coordinator.start_operation(state=third, turn=make_turn(third)):
            pass
    first_token = coordinator._records[first.run_id].pause_token
    assert first_token is not None
    async with coordinator.resume_operation(
        state=first_waiting,
        envelope=_resume_envelope(first_waiting, first_token),
    ):
        coordinator._take_resume()
        coordinator._ensure_current_pause(
            state=first_waiting,
            interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
        )
        coordinator.resolve_result(
            state=first_waiting,
            interrupt_value={"schema_version": "1.0", "interrupt_type": "clarification", "message": "请补充"},
        )
    assert len(factory.created) == 2
    assert [signal.signal_type for signal in sink.calls] == [
        "start_accepted",
        "start_accepted",
        "resume_accepted",
    ]


@pytest.mark.asyncio
async def test_real_capacity_256_commits_256_and_rejects_257_before_all_side_effects():
    service = ScriptedRequirementDomainService()
    coordinator, sink, factory = _coordinator(service=service, max_runs=256)
    admitted = [make_initial_state() for _ in range(256)]
    first_waiting = None
    for index, initial in enumerate(admitted):
        waiting, _ = await _start_and_pause(coordinator, initial)
        if index == 0:
            first_waiting = waiting
    assert first_waiting is not None
    overflow = make_initial_state()
    with pytest.raises(RequirementSubgraphCapacityExceededError):
        async with coordinator.start_operation(state=overflow, turn=make_turn(overflow)):
            pass
    assert len(coordinator._records) == 256
    assert len(factory.created) == 256
    assert len(sink.calls) == 256
    assert all(signal.signal_type == "start_accepted" for signal in sink.calls)
    assert service.process_calls == []
    assert service.confirm_calls == []
    assert all(manager.requests == [] for manager in factory.managers.values())
    assert overflow.run_id not in coordinator._records

    first_record = coordinator._records[first_waiting.run_id]
    first_token = first_record.pause_token
    assert first_token is not None
    async with coordinator.resume_operation(
        state=first_waiting,
        envelope=_resume_envelope(first_waiting, first_token),
    ):
        coordinator._take_resume()
        coordinator._ensure_current_pause(
            state=first_waiting,
            interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
        )
        coordinator.resolve_result(
            state=first_waiting,
            interrupt_value={
                "schema_version": "1.0",
                "interrupt_type": "clarification",
                "message": "请补充",
            },
        )
    assert len(coordinator._records) == 256
    assert len(factory.created) == 256
    assert len(sink.calls) == 257
    assert sink.calls[-1].signal_type == "resume_accepted"
    assert service.process_calls == []
    assert service.confirm_calls == []
    assert all(manager.requests == [] for manager in factory.managers.values())


@pytest.mark.asyncio
async def test_sink_exception_after_commit_faults_run_and_cannot_retry():
    sink = RecordingRequirementSubgraphSignalSink(fail_on_call=1)
    coordinator, _, factory = _coordinator(sink=sink)
    initial = make_initial_state()
    with pytest.raises(RuntimeError, match="sink failure"):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            pass
    assert coordinator._records[initial.run_id].status == "faulted"
    assert len(factory.created) == 1
    with pytest.raises(RequirementSubgraphInputError):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            pass


@pytest.mark.asyncio
async def test_sink_non_none_return_is_state_error_and_faults_run():
    sink = RecordingRequirementSubgraphSignalSink(return_value="not-none")
    coordinator, _, _ = _coordinator(sink=sink)
    initial = make_initial_state()
    with pytest.raises(RequirementSubgraphStateError):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            pass
    assert coordinator._records[initial.run_id].status == "faulted"


@pytest.mark.asyncio
async def test_wrong_token_stale_state_and_unknown_run_have_zero_resume_signal():
    coordinator, sink, _ = _coordinator()
    initial = make_initial_state()
    waiting, _ = await _start_and_pause(coordinator, initial)
    record = coordinator._records[initial.run_id]
    assert record.pause_token is not None
    bad_token = "f" * 32 if record.pause_token != "f" * 32 else "e" * 32
    wrong = RequirementResumeEnvelope(
        pause_token=bad_token,
        resume=RequirementClarificationResume(turn=make_turn(waiting)),
    )
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        async with coordinator.resume_operation(state=waiting, envelope=wrong):
            pass
    stale = RequirementResumeEnvelope(
        pause_token=record.pause_token,
        resume=RequirementClarificationResume(turn=make_turn(initial)),
    )
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        async with coordinator.resume_operation(state=initial, envelope=stale):
            pass
    unknown = make_initial_state()
    unknown_envelope = RequirementResumeEnvelope(
        pause_token="1" * 32,
        resume=RequirementClarificationResume(turn=make_turn(unknown)),
    )
    with pytest.raises(RequirementSubgraphResumeRejectedError):
        async with coordinator.resume_operation(state=unknown, envelope=unknown_envelope):
            pass
    assert [signal.signal_type for signal in sink.calls] == ["start_accepted"]


@pytest.mark.asyncio
async def test_non_lease_resolve_faults_active_run():
    coordinator, _, _ = _coordinator()
    initial = make_initial_state()
    with pytest.raises(RequirementSubgraphRunFaultedError):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            with pytest.raises(RequirementSubgraphStateError):
                await asyncio.to_thread(
                    coordinator.resolve_result,
                    state=initial,
                    interrupt_value=None,
                )
            coordinator._current_record()


@pytest.mark.asyncio
async def test_budget_response_correlation_and_signal_order():
    initial = make_initial_state()
    operation = OperationId(
        run_id=initial.run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="clarification_round_" + "1" * 32,
    )
    request = BudgetConsumeRequest(
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
    )
    snapshot = ExecutionBudgetSnapshot(
        schema_version="1.0",
        run_id=initial.run_id,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        limit=6,
        consumed=1,
        remaining=5,
    )
    outcome = WorkflowBudgetConsumed(
        disposition="applied",
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
        snapshot=snapshot,
    )
    manager = ScriptedExecutionBudgetManager([outcome])
    factory = PerRunBudgetManagerFactory(lambda _run_id: manager)
    coordinator, sink, _ = _coordinator(factory=factory)
    async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
        returned = await coordinator._consume_budget(request)
        assert returned.disposition == "applied"
        waiting = _waiting_state(initial)
        coordinator._set_latest_state(waiting)
        coordinator._ensure_current_pause(
            state=waiting,
            interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
        )
        coordinator.resolve_result(
            state=waiting,
            interrupt_value={"schema_version": "1.0", "interrupt_type": "clarification", "message": "请补充"},
        )
    assert [signal.signal_type for signal in sink.calls] == ["start_accepted", "budget_consumed"]


@pytest.mark.asyncio
async def test_budget_rejected_emits_once_without_domain_call_and_resolves_local_result():
    initial = make_initial_state()
    operation = OperationId(
        run_id=initial.run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="clarification_round_" + "2" * 32,
    )
    snapshot = ExecutionBudgetSnapshot(
        schema_version="1.0",
        run_id=initial.run_id,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        limit=6,
        consumed=6,
        remaining=0,
    )
    outcome = WorkflowBudgetExceeded(
        disposition="rejected",
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
        snapshot=snapshot,
    )
    manager = ScriptedExecutionBudgetManager([outcome])
    factory = PerRunBudgetManagerFactory(lambda _run_id: manager)
    service = ScriptedRequirementDomainService()
    sink = RecordingRequirementSubgraphSignalSink()
    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=factory,
        signal_sink=sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=256),
    )
    request = BudgetConsumeRequest(
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
    )
    async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
        returned = await coordinator._consume_budget(request)
        assert returned.disposition == "rejected"
        budget_state = _waiting_state(initial).model_copy(
            update={"delivery_status": DeliveryStatus.IN_PROGRESS}
        )
        local_result = RequirementSubgraphBudgetExceeded(
            state=budget_state,
            budget_outcome=outcome,
        )
        coordinator._set_latest_state(budget_state)
        coordinator._set_local_result(local_result)
        resolved = coordinator.resolve_result(state=budget_state, interrupt_value=None)
        assert resolved.result_type == "budget_exceeded"
    assert [signal.signal_type for signal in sink.calls] == [
        "start_accepted",
        "budget_exceeded",
    ]
    assert service.process_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch_kind",
    ["operation_id", "key", "amount", "snapshot_run_id", "snapshot_key"],
)
async def test_budget_correlation_mismatch_faults_before_signal_or_domain_call(mismatch_kind):
    initial = make_initial_state()
    operation = OperationId(
        run_id=initial.run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="clarification_round_" + "3" * 32,
    )
    snapshot = ExecutionBudgetSnapshot(
        schema_version="1.0",
        run_id=initial.run_id,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        limit=6,
        consumed=1,
        remaining=5,
    )
    outcome = WorkflowBudgetConsumed(
        disposition="applied",
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
        snapshot=snapshot,
    )
    wrong_key = ExecutionBudgetKey(
        scope=ExecutionBudgetScope.PHASE,
        name="other.budget",
        phase=WorkflowPhase.REQUIREMENTS,
    )
    mismatch_values = {
        "operation_id": OperationId(
            run_id=uuid4(),
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="clarification_round_" + "4" * 32,
        ),
        "key": wrong_key,
        "amount": 2,
        "snapshot_run_id": snapshot.model_copy(update={"run_id": uuid4()}),
        "snapshot_key": snapshot.model_copy(update={"key": wrong_key}),
    }
    if mismatch_kind.startswith("snapshot_"):
        mismatched = outcome.model_copy(update={"snapshot": mismatch_values[mismatch_kind]})
    else:
        mismatched = outcome.model_copy(update={mismatch_kind: mismatch_values[mismatch_kind]})
    manager = ScriptedExecutionBudgetManager([mismatched])
    factory = PerRunBudgetManagerFactory(lambda _run_id: manager)
    service = ScriptedRequirementDomainService()
    sink = RecordingRequirementSubgraphSignalSink()
    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=factory,
        signal_sink=sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=256),
    )
    request = BudgetConsumeRequest(
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
    )
    with pytest.raises(RequirementSubgraphStateError):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            await coordinator._consume_budget(request)
    _assert_fault_tombstone(coordinator._records[initial.run_id])
    assert [signal.signal_type for signal in sink.calls] == ["start_accepted"]
    assert service.process_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome_kind", ["consumed", "exceeded"])
async def test_budget_replayed_always_propagates_upstream_indeterminate_and_faults(outcome_kind):
    initial = make_initial_state()
    operation = OperationId(
        run_id=initial.run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="clarification_round_" + "a" * 32,
    )
    snapshot = ExecutionBudgetSnapshot(
        schema_version="1.0",
        run_id=initial.run_id,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        limit=6,
        consumed=1,
        remaining=5,
    )
    if outcome_kind == "consumed":
        outcome = WorkflowBudgetConsumed(
            disposition="replayed",
            operation_id=operation,
            key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
            amount=1,
            snapshot=snapshot,
        )
    else:
        outcome = WorkflowBudgetExceeded(
            disposition="replayed",
            operation_id=operation,
            key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
            amount=1,
            snapshot=snapshot,
        )
    manager = ScriptedExecutionBudgetManager([outcome])
    factory = PerRunBudgetManagerFactory(lambda _run_id: manager)
    service = ScriptedRequirementDomainService()
    sink = RecordingRequirementSubgraphSignalSink()
    coordinator = RequirementSubgraphCoordinator(
        domain_service=service,
        execution_budget_manager_factory=factory,
        signal_sink=sink,
        limits=RequirementSubgraphCoordinatorLimits(max_runs=256),
    )
    request = BudgetConsumeRequest(
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
    )
    with pytest.raises(OperationResultIndeterminateError):
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            await coordinator._consume_budget(request)
    record = coordinator._records[initial.run_id]
    _assert_fault_tombstone(record)
    assert [signal.signal_type for signal in sink.calls] == ["start_accepted"]
    assert service.process_calls == []


def test_coordinator_binds_first_event_loop_and_rejects_second_loop():
    coordinator, _, _ = _coordinator()
    initial = make_initial_state()

    async def first() -> None:
        waiting = _waiting_state(initial)
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            coordinator._ensure_current_pause(
                state=initial,
                interrupt=coordinator_module.RequirementClarificationInterrupt(message="请补充"),
            )
            coordinator._set_latest_state(waiting)
            coordinator.resolve_result(
                state=waiting,
                interrupt_value={"schema_version": "1.0", "interrupt_type": "clarification", "message": "请补充"},
            )

    asyncio.run(first())

    async def second() -> None:
        other = make_initial_state()
        with pytest.raises(RequirementSubgraphConfigurationError):
            async with coordinator.start_operation(state=other, turn=make_turn(other)):
                pass

    asyncio.run(second())


def _install_manual_lease(coordinator, state, *, manager=None):
    record = coordinator_module._RunRecord(
        run_id=state.run_id,
        trace_id=state.trace_id,
        manager=manager or ScriptedExecutionBudgetManager(),
        loop=asyncio.get_running_loop(),
        latest_state=state,
    )
    lease = coordinator_module._Lease(state.run_id, 1)
    record.active_lease = lease
    coordinator._records[state.run_id] = record
    token = coordinator_module._CURRENT_LEASE.set(lease)
    return record, token


def test_coordinator_constructor_and_fresh_helpers_reject_invalid_contracts(monkeypatch):
    service = ScriptedRequirementDomainService()
    factory = PerRunBudgetManagerFactory(lambda _run_id: ScriptedExecutionBudgetManager())
    sink = RecordingRequirementSubgraphSignalSink()
    limits = RequirementSubgraphCoordinatorLimits(max_runs=1)
    with pytest.raises(RequirementSubgraphConfigurationError) as caught:
        RequirementSubgraphCoordinator(
            domain_service=object(),
            execution_budget_manager_factory=factory,
            signal_sink=sink,
            limits=limits,
        )
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphConfigurationError) as caught:
        RequirementSubgraphCoordinator(
            domain_service=service,
            execution_budget_manager_factory=object(),  # type: ignore[arg-type]
            signal_sink=sink,
            limits=limits,
        )
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphConfigurationError) as caught:
        RequirementSubgraphCoordinator(
            domain_service=service,
            execution_budget_manager_factory=factory,
            signal_sink=object(),  # type: ignore[arg-type]
            limits=limits,
        )
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphConfigurationError) as caught:
        RequirementSubgraphCoordinator(
            domain_service=service,
            execution_budget_manager_factory=factory,
            signal_sink=sink,
            limits=object(),  # type: ignore[arg-type]
        )
    assert caught.value.args == ()

    state = make_initial_state()
    coordinator_for_validation, _, _ = _coordinator()
    with pytest.raises(RequirementSubgraphInputError) as caught:
        coordinator_for_validation._validate_initial_state(
            state,
            make_turn(state).model_copy(
                update={
                    "current": RequirementDomainState(
                        confirmation_status="awaiting_confirmation"
                    )
                }
            ),
        )
    assert caught.value.args == ()
    assert coordinator_module._fresh_exact(
        type(state), state, RequirementSubgraphInputError
    ) == state
    with pytest.raises(RequirementSubgraphInputError) as caught:
        coordinator_module._fresh_exact(type(state), {}, RequirementSubgraphInputError)
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphInputError) as caught:
        coordinator_module._fresh_union(
            NeedClarification | NeedConfirmation,
            object(),
            RequirementSubgraphInputError,
        )
    assert caught.value.args == ()
    waiting = _waiting_state(state)
    assert coordinator_module._fresh_union(
        NeedClarification | NeedConfirmation,
        waiting.requirement_outcome,
        RequirementSubgraphInputError,
    ) == waiting.requirement_outcome

    def fail_fresh(_model_type, _value):
        raise ValueError("fresh failure")

    monkeypatch.setattr(coordinator_module, "_fresh_model", fail_fresh)
    with pytest.raises(RequirementSubgraphInputError) as caught:
        coordinator_module._fresh_exact(type(state), state, RequirementSubgraphInputError)
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphInputError) as caught:
        coordinator_module._fresh_union(
            NeedClarification | NeedConfirmation,
            waiting.requirement_outcome,
            RequirementSubgraphInputError,
        )
    assert caught.value.args == ()


def test_coordinator_requires_running_loop_for_ensure_loop():
    coordinator, _, _ = _coordinator()
    with pytest.raises(RequirementSubgraphConfigurationError) as caught:
        coordinator._ensure_loop()
    assert caught.value.args == ()


@pytest.mark.asyncio
async def test_operation_scope_invalid_kind_clears_complete_input_slots():
    coordinator, _, _ = _coordinator()
    scope = coordinator_module._OperationScope(coordinator, kind="invalid")  # type: ignore[arg-type]
    with pytest.raises(RequirementSubgraphStateError) as caught:
        await scope.__aenter__()
    assert caught.value.args == ()
    assert scope._state is None
    assert scope._turn is None
    assert scope._envelope is None


@pytest.mark.asyncio
async def test_coordinator_internal_protocol_guards_cover_zero_side_effect_failures():
    initial = make_initial_state()
    coordinator, sink, factory = _coordinator()
    waiting = _waiting_state(initial)
    interrupt = coordinator_module.RequirementClarificationInterrupt(message="请补充")

    record, token = _install_manual_lease(coordinator, initial)
    try:
        with pytest.raises(RequirementSubgraphStateError) as caught:
            await coordinator._commit_resume(
                coordinator_module._OperationScope(coordinator, kind="resume"),
                initial,
                _resume_envelope(waiting, "a" * 32),
            )
        assert caught.value.args == ()
    finally:
        coordinator_module._CURRENT_LEASE.reset(token)
        coordinator._records.pop(initial.run_id, None)

    with pytest.raises(RequirementSubgraphStateError) as caught:
        coordinator._record_for_state(object(), error_type=RequirementSubgraphStateError)
    assert caught.value.args == ()
    with pytest.raises(RequirementSubgraphStateError) as caught:
        coordinator._record_for_state(initial, error_type=RequirementSubgraphStateError)
    assert caught.value.args == ()

    record, token = _install_manual_lease(coordinator, initial)
    try:
        assert coordinator._current_record() is record
        saved_lease = record.active_lease
        record.active_lease = None
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._require_lease(record)
        assert caught.value.args == ()
        record.status = "running"
        record.active_lease = saved_lease
        record.lease_resolved = True
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._mark_resolved(record)
        assert caught.value.args == ()
        record.status = "running"
        record.lease_resolved = False
        record.active_lease = saved_lease
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._parse_interrupt(object())
        assert caught.value.args == ()
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._parse_interrupt({"interrupt_type": "unknown"})
        assert caught.value.args == ()
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._copy_result(object())
        assert caught.value.args == ()
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._emit(object())  # type: ignore[arg-type]
        assert caught.value.args == ()
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._take_turn()
        assert caught.value.args == ()
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._take_resume()
        assert caught.value.args == ()
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._take_pause_for_replay()
        assert caught.value.args == ()
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._node_replayed_operation()
        assert caught.value.args == ()
    finally:
        coordinator._fault(record)
        record.active_lease = None
        coordinator_module._CURRENT_LEASE.reset(token)
        coordinator._records.pop(initial.run_id, None)

    record, token = _install_manual_lease(coordinator, initial)
    try:
        wrong_key = ExecutionBudgetKey(
            scope=ExecutionBudgetScope.PHASE,
            name="other.budget",
            phase=WorkflowPhase.REQUIREMENTS,
        )
        request = BudgetConsumeRequest(
            operation_id=OperationId(
                run_id=initial.run_id,
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="clarification_round_" + "a" * 32,
            ),
            key=wrong_key,
            amount=1,
        )
        with pytest.raises(RequirementSubgraphStateError) as caught:
            await coordinator._consume_budget(request)
        assert caught.value.args == ()
    finally:
        record.active_lease = None
        coordinator_module._CURRENT_LEASE.reset(token)
        coordinator._records.pop(initial.run_id, None)


@pytest.mark.asyncio
async def test_coordinator_rejects_invalid_budget_result_before_signal():
    initial = make_initial_state()
    manager = ScriptedExecutionBudgetManager(outcomes=[object()])
    factory = PerRunBudgetManagerFactory(lambda _run_id: manager)
    coordinator, sink, _ = _coordinator(factory=factory)
    request = BudgetConsumeRequest(
        operation_id=OperationId(
            run_id=initial.run_id,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="clarification_round_" + "a" * 32,
        ),
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
    )
    with pytest.raises(RequirementSubgraphStateError) as caught:
        async with coordinator.start_operation(state=initial, turn=make_turn(initial)):
            await coordinator._consume_budget(request)
    assert caught.value.args == ()
    assert len(sink.calls) == 1


@pytest.mark.asyncio
async def test_coordinator_validates_pause_resume_and_secret_defensive_paths(monkeypatch):
    initial = make_initial_state()
    coordinator, _, _ = _coordinator()
    interrupt = coordinator_module.RequirementClarificationInterrupt(message="请补充")
    waiting, _ = await _start_and_pause(coordinator, initial)
    record = coordinator._records[initial.run_id]

    wrong_turn = make_turn(waiting).model_copy(
        update={"current": RequirementDomainState(confirmation_status="awaiting_confirmation")}
    )
    wrong_envelope = RequirementResumeEnvelope(
        pause_token=record.pause_token,
        resume=RequirementClarificationResume(turn=wrong_turn),
    )
    with pytest.raises(RequirementSubgraphResumeRejectedError) as caught:
        coordinator._validate_resume(record, waiting, wrong_envelope)
    assert caught.value.args == ()

    confirmed = _confirmed_state(initial)
    record.latest_state = confirmed
    with pytest.raises(RequirementSubgraphResumeRejectedError) as caught:
        coordinator._validate_resume(record, confirmed, wrong_envelope)
    assert caught.value.args == ()

    coordinator._fault(record)
    with pytest.raises(RequirementSubgraphRunFaultedError) as caught:
        coordinator._record_for_state(initial, error_type=RequirementSubgraphStateError)
    assert caught.value.args == ()

    coordinator, _, _ = _coordinator()
    record, token = _install_manual_lease(coordinator, initial)
    try:
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._ensure_current_pause(state=waiting, interrupt=interrupt)
        assert caught.value.args == ()
    finally:
        coordinator._fault(record)
        record.active_lease = None
        coordinator_module._CURRENT_LEASE.reset(token)

    coordinator, _, _ = _coordinator()
    record, token = _install_manual_lease(coordinator, initial)
    try:
        record.status = "resume_pending"
        record.pause_token = "a" * 32
        record.operation_nonce = "b" * 32
        record.interrupt_descriptor = interrupt
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._ensure_current_pause(
                state=initial,
                interrupt=coordinator_module.RequirementClarificationInterrupt(message="wrong"),
            )
        assert caught.value.args == ()
    finally:
        record.active_lease = None
        coordinator_module._CURRENT_LEASE.reset(token)

    coordinator, _, _ = _coordinator()
    record, token = _install_manual_lease(coordinator, initial)
    try:
        record.status = "paused"
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._ensure_current_pause(state=initial, interrupt=interrupt)
        assert caught.value.args == ()
    finally:
        record.active_lease = None
        coordinator_module._CURRENT_LEASE.reset(token)

    coordinator, _, _ = _coordinator()
    record, token = _install_manual_lease(coordinator, initial)
    try:
        monkeypatch.setattr(
            coordinator_module,
            "_new_secret_hex",
            SequenceSecretHex(["a" * 32, "a" * 32, "b" * 32]),
        )
        token_value, nonce, _ = coordinator._ensure_current_pause(
            state=initial,
            interrupt=interrupt,
        )
        assert token_value != nonce
    finally:
        record.active_lease = None
        coordinator_module._CURRENT_LEASE.reset(token)

    coordinator, _, _ = _coordinator()
    record, token = _install_manual_lease(coordinator, initial)
    try:
        monkeypatch.setattr(
            coordinator_module,
            "_new_secret_hex",
            SequenceSecretHex(["g" * 32, "h" * 32]),
        )
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._ensure_current_pause(state=initial, interrupt=interrupt)
        assert caught.value.args == ()
    finally:
        record.active_lease = None
        coordinator_module._CURRENT_LEASE.reset(token)


@pytest.mark.asyncio
async def test_coordinator_scope_cleanup_and_local_result_mismatch_fault():
    initial = make_initial_state()
    coordinator, _, _ = _coordinator()
    scope = coordinator_module._OperationScope(coordinator, kind="start")
    assert await coordinator._exit_scope(scope, exc_type=None, exception=None) is None

    waiting = _waiting_state(initial)
    record, token = _install_manual_lease(coordinator, initial)
    try:
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._set_local_result(
                RequirementSubgraphConfirmed(state=_confirmed_state(initial))
            )
        assert caught.value.args == ()
    finally:
        record.active_lease = None
        coordinator_module._CURRENT_LEASE.reset(token)

    with pytest.raises(RequirementSubgraphStateError) as caught:
        coordinator._current_record()
    assert caught.value.args == ()

    missing = make_initial_state()
    token = coordinator_module._CURRENT_LEASE.set(
        coordinator_module._Lease(missing.run_id, 1)
    )
    try:
        with pytest.raises(RequirementSubgraphStateError) as caught:
            coordinator._current_record()
        assert caught.value.args == ()
    finally:
        coordinator_module._CURRENT_LEASE.reset(token)
