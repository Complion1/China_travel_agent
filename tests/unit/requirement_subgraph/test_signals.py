from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.requirement_subgraph import (
    BudgetConsumed,
    BudgetExceeded,
    NodeReplayed,
    NoOpRequirementSubgraphSignalSink,
    ResumeAccepted,
    StartAccepted,
    RequirementSubgraphStateError,
)
from app.workflow_contracts import OperationId, WorkflowPhase


def _operation(prefix: str) -> OperationId:
    return OperationId(
        run_id=RUN_ID,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step=prefix + "f" * 32,
    )


RUN_ID = uuid4()
TRACE_ID = uuid4()


def test_accepted_signals_have_exact_fields_and_no_secret_fields():
    start = StartAccepted(run_id=RUN_ID, trace_id=TRACE_ID)
    resume = ResumeAccepted(run_id=RUN_ID, trace_id=TRACE_ID)
    assert start.signal_type == "start_accepted"
    assert resume.signal_type == "resume_accepted"
    assert tuple(StartAccepted.model_fields) == ("schema_version", "signal_type", "run_id", "trace_id")
    assert "token" not in start.model_dump_json().lower()
    assert "nonce" not in resume.model_dump_json().lower()


def test_all_signal_models_are_strict_frozen_extra_forbid_with_exact_fields_and_defaults():
    expected = {
        StartAccepted: ("schema_version", "signal_type", "run_id", "trace_id"),
        ResumeAccepted: ("schema_version", "signal_type", "run_id", "trace_id"),
        NodeReplayed: ("schema_version", "signal_type", "run_id", "trace_id", "operation_id"),
        BudgetConsumed: ("schema_version", "signal_type", "run_id", "trace_id", "operation_id", "amount"),
        BudgetExceeded: ("schema_version", "signal_type", "run_id", "trace_id", "operation_id"),
    }
    for signal_type, fields in expected.items():
        assert tuple(signal_type.model_fields) == fields
        assert signal_type.model_config["strict"] is True
        assert signal_type.model_config["frozen"] is True
        assert signal_type.model_config["extra"] == "forbid"
    assert BudgetConsumed(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        operation_id=_operation("clarification_round_"),
    ).amount == 1


@pytest.mark.parametrize(
    "case",
    [
        "node_run",
        "node_phase",
        "node_prefix",
        "consumed_run",
        "consumed_phase",
        "consumed_prefix",
        "consumed_amount",
        "exceeded_run",
        "exceeded_phase",
        "exceeded_prefix",
    ],
)
def test_signals_reject_wrong_run_phase_prefix_and_amount(case):
    signal_group, mutation = case.split("_", 1)
    if signal_group == "node":
        signal_type = NodeReplayed
        prefix = "requirement_pause_"
    elif signal_group == "consumed":
        signal_type = BudgetConsumed
        prefix = "clarification_round_"
    else:
        signal_type = BudgetExceeded
        prefix = "clarification_round_"
    operation = _operation(prefix)
    run_id = RUN_ID
    amount = 1
    if mutation == "run":
        operation = operation.model_copy(update={"run_id": uuid4()})
    elif mutation == "phase":
        operation = operation.model_copy(update={"phase": WorkflowPhase.HOTEL_SELECTION})
    elif mutation == "prefix":
        operation = operation.model_copy(update={"logical_step": "wrong_" + "f" * 32})
    else:
        amount = 2
    kwargs = {
        "run_id": run_id,
        "trace_id": TRACE_ID,
        "operation_id": operation,
    }
    if signal_type is BudgetConsumed:
        kwargs["amount"] = amount
    with pytest.raises(ValidationError):
        signal_type(**kwargs)


def test_replay_and_budget_signals_validate_operation_shape_and_security():
    replay = NodeReplayed(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        operation_id=_operation("requirement_pause_"),
    )
    consumed = BudgetConsumed(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        operation_id=_operation("clarification_round_"),
    )
    exceeded = BudgetExceeded(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        operation_id=_operation("clarification_round_"),
    )
    assert replay.operation_id.run_id == replay.run_id
    assert consumed.amount == 1
    assert exceeded.operation_id.phase is WorkflowPhase.REQUIREMENTS
    assert "nonce" not in json.dumps(replay.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("signal_type", "prefix"),
    [
        (NodeReplayed, "wrong_"),
        (BudgetConsumed, "requirement_pause_"),
        (BudgetExceeded, "requirement_pause_"),
    ],
)
def test_signal_operation_prefix_is_not_guessable(signal_type, prefix):
    with pytest.raises(ValidationError):
        signal_type(
            run_id=RUN_ID,
            trace_id=TRACE_ID,
            operation_id=_operation(prefix),
        )


def test_signal_run_mismatch_is_rejected():
    with pytest.raises(ValidationError):
        NodeReplayed(
            run_id=RUN_ID,
            trace_id=TRACE_ID,
            operation_id=OperationId(
                run_id=uuid4(),
                phase=WorkflowPhase.REQUIREMENTS,
                logical_step="requirement_pause_" + "1" * 32,
            ),
        )


def test_noop_sink_returns_none_after_fresh_validation():
    sink = NoOpRequirementSubgraphSignalSink()
    assert sink.accept(StartAccepted(run_id=RUN_ID, trace_id=TRACE_ID)) is None
    with pytest.raises(RequirementSubgraphStateError):
        sink.accept({"signal_type": "start_accepted"})  # type: ignore[arg-type]


def test_noop_sink_maps_invalid_typed_signal_to_zero_argument_state_error():
    signal = StartAccepted.model_construct(
        schema_version="1.0",
        signal_type="start_accepted",
        run_id="not-a-uuid",
        trace_id="not-a-uuid",
    )
    with pytest.raises(RequirementSubgraphStateError) as caught:
        NoOpRequirementSubgraphSignalSink().accept(signal)
    assert caught.value.args == ()
