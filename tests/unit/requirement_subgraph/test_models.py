from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.requirements import (
    ConfirmedTravelRequirement,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainState,
    RequirementDomainTransition,
    RequirementErrorCode,
    RequirementExtractionFailed,
    RequirementInvalidated,
)
from app.workflow_contracts import (
    BudgetExceeded as WorkflowBudgetExceeded,
    DeliveryStatus,
    ExecutionBudgetKey,
    ExecutionBudgetScope,
    ExecutionBudgetSnapshot,
    OperationId,
    WorkflowPhase,
)
from app.requirement_subgraph import (
    DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS,
    DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT,
    REQUIREMENTS_CLARIFICATION_ROUND_KEY,
    RequirementClarificationInterrupt,
    RequirementClarificationResume,
    RequirementConfirmationInterrupt,
    RequirementConfirmationResume,
    RequirementPauseEnvelope,
    RequirementResumeEnvelope,
    RequirementSubgraphBudgetExceeded,
    RequirementSubgraphConfirmed,
    RequirementSubgraphDomainFailed,
    RequirementSubgraphPaused,
)
from tests.requirement_subgraph_fakes import make_initial_state, make_turn


def _waiting_state(outcome: object, *, status: str = "awaiting_user"):
    transition = RequirementDomainTransition(
        state=outcome.requirement if False else RequirementDomainState(),
        outcome=outcome,
    )
    return make_initial_state().model_copy(
        update={
            "delivery_status": status,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
        }
    )


def _clarification_state():
    state = RequirementDomainState()
    outcome = NeedClarification(
        requirement=state.requirement,
        metadata=state.metadata,
        issues=(),
        message="请补充需求",
    )
    transition = RequirementDomainTransition(state=state, outcome=outcome)
    return make_initial_state().model_copy(
        update={
            "delivery_status": DeliveryStatus.AWAITING_USER,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
        }
    )


def _confirmation_state():
    state = RequirementDomainState(confirmation_status="awaiting_confirmation")
    outcome = NeedConfirmation(
        requirement=state.requirement,
        metadata=state.metadata,
        summary="请确认需求",
    )
    transition = RequirementDomainTransition(state=state, outcome=outcome)
    return make_initial_state().model_copy(
        update={
            "delivery_status": DeliveryStatus.AWAITING_USER,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
        }
    )


def _invalidated_state():
    state = RequirementDomainState(confirmation_status="awaiting_confirmation")
    after_digest = hashlib.sha256(
        state.requirement.model_dump_json(exclude_none=False).encode()
    ).hexdigest()
    invalidated = RequirementInvalidated(
        changed_fields=(),
        before_digest="before",
        after_digest=after_digest,
        reason="changed",
        summary="需求已变化",
    )
    transition = RequirementDomainTransition(state=state, outcome=invalidated)
    return make_initial_state().model_copy(
        update={
            "delivery_status": DeliveryStatus.AWAITING_USER,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
        }
    )


def _budget_fixture():
    state = _clarification_state().model_copy(
        update={"delivery_status": DeliveryStatus.IN_PROGRESS}
    )
    operation = OperationId(
        run_id=state.run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="clarification_round_" + "d" * 32,
    )
    snapshot = ExecutionBudgetSnapshot(
        schema_version="1.0",
        run_id=state.run_id,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        limit=6,
        consumed=6,
        remaining=0,
    )
    budget = WorkflowBudgetExceeded(
        disposition="rejected",
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
        snapshot=snapshot,
    )
    return state, budget


def _result_state_with_outcome(state, outcome):
    return state.model_copy(
        update={
            "delivery_status": DeliveryStatus.IN_PROGRESS,
            "requirement_outcome": outcome,
        }
    )


def test_model_configs_and_field_order_are_frozen():
    assert tuple(RequirementClarificationInterrupt.model_fields) == (
        "schema_version",
        "interrupt_type",
        "message",
    )
    assert tuple(RequirementResumeEnvelope.model_fields) == (
        "schema_version",
        "pause_token",
        "resume",
    )
    assert RequirementClarificationInterrupt.model_config["strict"] is True
    assert RequirementClarificationInterrupt.model_config["frozen"] is True
    assert RequirementClarificationInterrupt.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError):
        RequirementClarificationInterrupt(message="x", extra="bad")
    with pytest.raises(ValidationError):
        RequirementClarificationInterrupt(message="x").message = "changed"


@pytest.mark.parametrize(
    "model_type",
    [
        RequirementClarificationInterrupt,
        RequirementConfirmationInterrupt,
        RequirementClarificationResume,
        RequirementConfirmationResume,
        RequirementPauseEnvelope,
        RequirementResumeEnvelope,
        RequirementSubgraphPaused,
        RequirementSubgraphConfirmed,
        RequirementSubgraphDomainFailed,
        RequirementSubgraphBudgetExceeded,
        type(DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS),
    ],
)
def test_every_public_dto_is_strict_frozen_and_extra_forbid(model_type):
    assert model_type.model_config["strict"] is True
    assert model_type.model_config["frozen"] is True
    assert model_type.model_config["extra"] == "forbid"


def test_dto_field_order_discriminators_and_defaults_are_exact():
    assert tuple(RequirementClarificationInterrupt.model_fields) == (
        "schema_version",
        "interrupt_type",
        "message",
    )
    assert tuple(RequirementConfirmationInterrupt.model_fields) == (
        "schema_version",
        "interrupt_type",
        "source_outcome_type",
        "summary",
    )
    assert tuple(RequirementClarificationResume.model_fields) == (
        "schema_version",
        "resume_type",
        "turn",
    )
    assert tuple(RequirementConfirmationResume.model_fields) == (
        "schema_version",
        "resume_type",
        "action",
    )
    assert tuple(RequirementPauseEnvelope.model_fields) == (
        "schema_version",
        "pause_token",
        "interrupt",
    )
    assert tuple(RequirementResumeEnvelope.model_fields) == (
        "schema_version",
        "pause_token",
        "resume",
    )
    assert tuple(RequirementSubgraphPaused.model_fields) == (
        "schema_version",
        "result_type",
        "state",
        "pause",
    )
    assert tuple(RequirementSubgraphConfirmed.model_fields) == (
        "schema_version",
        "result_type",
        "state",
    )
    assert tuple(RequirementSubgraphDomainFailed.model_fields) == (
        "schema_version",
        "result_type",
        "state",
    )
    assert tuple(RequirementSubgraphBudgetExceeded.model_fields) == (
        "schema_version",
        "result_type",
        "state",
        "budget_outcome",
    )
    assert RequirementConfirmationInterrupt(
        source_outcome_type="need_confirmation", summary="s"
    ).schema_version == "1.0"
    assert RequirementConfirmationInterrupt(
        source_outcome_type="need_confirmation", summary="s"
    ).interrupt_type == "confirmation"
    assert RequirementClarificationResume(turn=make_turn(_clarification_state())).resume_type == "clarification"
    assert RequirementConfirmationResume(
        action=RequirementConfirmation(decision="accept", revision_text=None)
    ).resume_type == "confirmation"


@pytest.mark.parametrize("token", ["A" * 32, "a" * 31, "a" * 33, "g" * 32, "a" * 32 + " "])
def test_pause_token_requires_exact_lower_hex_shape(token):
    with pytest.raises(ValidationError):
        RequirementPauseEnvelope(
            pause_token=token,
            interrupt=RequirementClarificationInterrupt(message="m"),
        )
    with pytest.raises(ValidationError):
        RequirementResumeEnvelope(
            pause_token=token,
            resume=RequirementClarificationResume(turn=make_turn(_clarification_state())),
        )


def test_pause_and_resume_repr_hide_token_and_nested_payload():
    state = _clarification_state()
    token = "a" * 32
    pause = RequirementPauseEnvelope(
        pause_token=token,
        interrupt=RequirementClarificationInterrupt(message="USER_TEXT"),
    )
    resume = RequirementResumeEnvelope(
        pause_token=token,
        resume=RequirementClarificationResume(turn=make_turn(state, user_message="USER_TEXT")),
    )
    assert token not in repr(pause)
    assert token not in repr(resume)
    assert "USER_TEXT" not in repr(resume)


def test_versions_discriminators_limits_and_budget_key_are_exact():
    interrupt = RequirementClarificationInterrupt(message="m")
    assert interrupt.schema_version == "1.0"
    assert interrupt.interrupt_type == "clarification"
    assert DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS.max_runs == 256
    assert DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT == 6
    assert REQUIREMENTS_CLARIFICATION_ROUND_KEY.scope.value == "phase"
    assert REQUIREMENTS_CLARIFICATION_ROUND_KEY.name == "requirements.clarification_round"
    assert REQUIREMENTS_CLARIFICATION_ROUND_KEY.phase is WorkflowPhase.REQUIREMENTS


def test_resume_envelope_hides_token_and_nested_user_text_from_repr_and_copies_input():
    state = make_initial_state()
    turn = make_turn(state, user_message="TOP_SECRET_USER_TEXT")
    token = "a" * 32
    envelope = RequirementResumeEnvelope(
        pause_token=token,
        resume=RequirementClarificationResume(turn=turn),
    )
    rendered = repr(envelope)
    assert token not in rendered
    assert "TOP_SECRET_USER_TEXT" not in rendered
    turn.current.requirement.preferences.must_visit_places.append("mutated")
    assert envelope.resume.turn.current.requirement.preferences.must_visit_places == []


def test_pause_payload_is_safe_and_json_round_trip_preserves_union_branch():
    state = _clarification_state()
    pause = RequirementPauseEnvelope(
        pause_token="b" * 32,
        interrupt=RequirementClarificationInterrupt(message="请补充需求"),
    )
    result = RequirementSubgraphPaused(state=state, pause=pause)
    adapter = TypeAdapter(
        RequirementSubgraphPaused
    )
    tree = result.model_dump(mode="json", exclude_none=False)
    restored = adapter.validate_json(
        json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        strict=True,
    )
    assert restored == result
    payload = json.dumps(tree, ensure_ascii=False, sort_keys=True)
    assert "pause_token" in payload
    assert "requirement" not in pause.interrupt.model_dump_json()


def test_paused_cross_field_invariants_reject_wrong_message_or_branch():
    state = _clarification_state()
    with pytest.raises(ValidationError):
        RequirementSubgraphPaused(
            state=state,
            pause=RequirementPauseEnvelope(
                pause_token="c" * 32,
                interrupt=RequirementClarificationInterrupt(message="wrong"),
            ),
        )
    with pytest.raises(ValidationError):
        RequirementSubgraphPaused(
            state=state,
            pause=RequirementPauseEnvelope(
                pause_token="c" * 32,
                interrupt=RequirementConfirmationInterrupt(
                    source_outcome_type="need_confirmation",
                    summary="wrong branch",
                ),
            ),
        )


@pytest.mark.parametrize(
    "case",
    [
        "clarification_message",
        "confirmation_source",
        "confirmation_summary",
        "invalidated_source",
        "invalidated_summary",
    ],
)
def test_paused_confirmation_invalidated_and_clarification_display_fields_must_match(case):
    if case == "clarification_message":
        state = _clarification_state()
        interrupt = RequirementClarificationInterrupt(message="wrong")
    elif case == "confirmation_source":
        state = _confirmation_state()
        interrupt = RequirementConfirmationInterrupt(
            source_outcome_type="invalidated", summary="请确认"
        )
    elif case == "confirmation_summary":
        state = _confirmation_state()
        interrupt = RequirementConfirmationInterrupt(
            source_outcome_type="need_confirmation", summary="wrong"
        )
    elif case == "invalidated_source":
        state = _invalidated_state()
        interrupt = RequirementConfirmationInterrupt(
            source_outcome_type="need_confirmation", summary="需求已变化"
        )
    else:
        state = _invalidated_state()
        interrupt = RequirementConfirmationInterrupt(
            source_outcome_type="invalidated", summary="wrong"
        )
    with pytest.raises(ValidationError):
        RequirementSubgraphPaused(
            state=state,
            pause=RequirementPauseEnvelope(pause_token="c" * 32, interrupt=interrupt),
        )


@pytest.mark.parametrize("result_type", ["confirmed", "domain_failed"])
def test_confirmed_and_domain_failed_results_reject_wrong_outcome_class(result_type):
    state = _clarification_state().model_copy(
        update={"delivery_status": DeliveryStatus.IN_PROGRESS}
    )
    result_model = (
        RequirementSubgraphConfirmed
        if result_type == "confirmed"
        else RequirementSubgraphDomainFailed
    )
    with pytest.raises(ValidationError):
        result_model(state=state)


def test_confirmed_and_domain_failed_results_require_exact_outcome_classes():
    state = RequirementDomainState(confirmation_status="confirmed")
    confirmed = ConfirmedTravelRequirement(
        schema_version="1.0",
        requirement=state.requirement,
        metadata_without_raw_evidence=state.metadata,
        confirmation_summary_digest="digest",
    )
    transition = RequirementDomainTransition(state=state, outcome=confirmed)
    confirmed_state = make_initial_state().model_copy(
        update={
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
            "delivery_status": DeliveryStatus.IN_PROGRESS,
        }
    )
    assert RequirementSubgraphConfirmed(state=confirmed_state).state == confirmed_state

    failed_state = RequirementDomainState()
    failed = RequirementExtractionFailed(code=RequirementErrorCode.INPUT_TOO_LONG)
    failed_transition = RequirementDomainTransition(state=failed_state, outcome=failed)
    domain_failed_state = make_initial_state().model_copy(
        update={
            "requirement_state": failed_transition.state,
            "requirement_outcome": failed_transition.outcome,
            "delivery_status": DeliveryStatus.IN_PROGRESS,
        }
    )
    assert RequirementSubgraphDomainFailed(state=domain_failed_state).state == domain_failed_state


def test_budget_exceeded_result_checks_operation_snapshot_and_rejected_disposition():
    state = _clarification_state()
    operation = OperationId(
        run_id=state.run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="clarification_round_" + "d" * 32,
    )
    snapshot = ExecutionBudgetSnapshot(
        schema_version="1.0",
        run_id=state.run_id,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        limit=6,
        consumed=6,
        remaining=0,
    )
    budget = WorkflowBudgetExceeded(
        disposition="rejected",
        operation_id=operation,
        key=REQUIREMENTS_CLARIFICATION_ROUND_KEY,
        amount=1,
        snapshot=snapshot,
    )
    result = RequirementSubgraphBudgetExceeded(
        state=state.model_copy(update={"delivery_status": DeliveryStatus.IN_PROGRESS}),
        budget_outcome=budget,
    )
    assert result.budget_outcome.disposition == "rejected"
    with pytest.raises(ValidationError):
        RequirementSubgraphBudgetExceeded(
            state=state.model_copy(update={"delivery_status": DeliveryStatus.IN_PROGRESS}),
            budget_outcome=budget.model_copy(update={"disposition": "replayed"}),
        )


@pytest.mark.parametrize(
    "mismatch_kind",
    [
        "wrong_operation_run",
        "wrong_key",
        "wrong_phase",
        "wrong_amount",
        "wrong_snapshot_run",
        "wrong_snapshot_key",
        "wrong_logical_step_prefix",
        "replayed_disposition",
    ],
)
def test_budget_exceeded_result_rejects_each_single_cross_field_mismatch(mismatch_kind):
    state, budget = _budget_fixture()
    other_key = ExecutionBudgetKey(
        scope=ExecutionBudgetScope.PHASE,
        name="other.budget",
        phase=WorkflowPhase.REQUIREMENTS,
    )
    if mismatch_kind == "wrong_operation_run":
        invalid_budget = budget.model_copy(
            update={"operation_id": budget.operation_id.model_copy(update={"run_id": uuid4()})}
        )
    elif mismatch_kind == "wrong_key":
        invalid_budget = budget.model_copy(update={"key": other_key})
    elif mismatch_kind == "wrong_phase":
        invalid_budget = budget.model_copy(
            update={
                "operation_id": budget.operation_id.model_copy(
                    update={"phase": WorkflowPhase.HOTEL_SELECTION}
                )
            }
        )
    elif mismatch_kind == "wrong_amount":
        invalid_budget = budget.model_copy(update={"amount": 2})
    elif mismatch_kind == "wrong_snapshot_run":
        invalid_budget = budget.model_copy(
            update={"snapshot": budget.snapshot.model_copy(update={"run_id": uuid4()})}
        )
    elif mismatch_kind == "wrong_snapshot_key":
        invalid_budget = budget.model_copy(
            update={"snapshot": budget.snapshot.model_copy(update={"key": other_key})}
        )
    elif mismatch_kind == "wrong_logical_step_prefix":
        invalid_budget = budget.model_copy(
            update={
                "operation_id": budget.operation_id.model_copy(
                    update={"logical_step": "requirement_pause_" + "d" * 32}
                )
            }
        )
    else:
        invalid_budget = budget.model_copy(update={"disposition": "replayed"})
    with pytest.raises(ValidationError):
        RequirementSubgraphBudgetExceeded(
            state=state,
            budget_outcome=invalid_budget,
        )


def test_invalidated_state_requires_digest_and_resume_branch_is_confirmation():
    state = RequirementDomainState(confirmation_status="awaiting_confirmation")
    digest = hashlib.sha256(state.requirement.model_dump_json(exclude_none=False).encode()).hexdigest()
    invalidated = RequirementInvalidated(
        changed_fields=(),
        before_digest="before",
        after_digest=digest,
        reason="changed",
        summary="新的摘要",
    )
    transition = RequirementDomainTransition(state=state, outcome=invalidated)
    workflow_state = make_initial_state().model_copy(
        update={
            "delivery_status": DeliveryStatus.AWAITING_USER,
            "requirement_state": transition.state,
            "requirement_outcome": transition.outcome,
        }
    )
    result = RequirementSubgraphPaused(
        state=workflow_state,
        pause=RequirementPauseEnvelope(
            pause_token="e" * 32,
            interrupt=RequirementConfirmationInterrupt(
                source_outcome_type="invalidated",
                summary="新的摘要",
            ),
        ),
    )
    assert result.pause.interrupt.source_outcome_type == "invalidated"
