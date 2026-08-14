"""Security and strict-boundary tests for module 04 contracts."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID
import warnings

import pytest
from app.requirements import (
    FieldMetadataRegistry,
    NeedClarification,
    RequirementDomainState,
    RequirementFieldPath,
    RequirementIssue,
    TravelRequirement,
)
from pydantic import TypeAdapter, ValidationError

import app.workflow_contracts as contracts
from tests.unit.workflow_contracts.fakes import (
    FixedUtcClock,
    StrictSampleArtifact,
)


CANARY = "MODULE04_CANARY_SECRET_7f91"
STATE_CANARY = "MODULE04_STATE_CANARY_4e2a"
OUTCOME_CANARY = "MODULE04_OUTCOME_CANARY_8b71"
RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
TRACE_ID = UUID("22345678-1234-5678-1234-567812345678")
ARTIFACT_ID = UUID("32345678-1234-5678-1234-567812345678")
EVENT_ID = UUID("42345678-1234-5678-1234-567812345678")
CREATED_AT = datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)


def _operation(step: str = "security_check") -> contracts.OperationId:
    return contracts.OperationId(
        run_id=RUN_ID,
        phase=contracts.WorkflowPhase.REQUIREMENTS,
        logical_step=step,
    )


def _artifact_ref() -> contracts.ArtifactRef:
    return contracts.ArtifactRef(
        artifact_id=ARTIFACT_ID,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content_hash="sha256:" + "0" * 64,
        operation_id=_operation(),
        created_at=CREATED_AT,
        size_bytes=1,
    )


def _artifact_registry() -> contracts.ArtifactSchemaRegistry:
    return contracts.ArtifactSchemaRegistry(
        (
            contracts.ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.0",
                model=StrictSampleArtifact,
            ),
        )
    )


def _artifact_request(*, note: object = None) -> contracts.ArtifactWriteRequest:
    return contracts.ArtifactWriteRequest(
        operation_id=_operation("artifact_security"),
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={
            "label": "security",
            "count": 1,
            "ratio": 1.0,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": note,
        },
    )


def _valid_state() -> contracts.TravelSessionState:
    return contracts.TravelSessionState(
        schema_version="2.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        phase=contracts.WorkflowPhase.REQUIREMENTS,
        delivery_status=contracts.DeliveryStatus.IN_PROGRESS,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=RequirementDomainState(),
        requirement_outcome=None,
        artifact_refs=(),
    )


def _sensitive_nonempty_state() -> contracts.TravelSessionState:
    requirement = TravelRequirement(destination=STATE_CANARY)
    metadata = FieldMetadataRegistry()
    issue = RequirementIssue(
        code="sensitive_issue",
        field_path=RequirementFieldPath.DESTINATION,
        message="sensitive issue",
    )
    state = RequirementDomainState(
        requirement=requirement,
        metadata=metadata,
        issues=(issue,),
    )
    outcome = NeedClarification(
        requirement=requirement,
        metadata=metadata,
        issues=(issue,),
        message=OUTCOME_CANARY,
    )
    return contracts.TravelSessionState(
        schema_version="2.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        phase=contracts.WorkflowPhase.REQUIREMENTS,
        delivery_status=contracts.DeliveryStatus.AWAITING_USER,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=state,
        requirement_outcome=outcome,
        artifact_refs=(),
    )


def _valid_event() -> contracts.SafeWorkflowEvent:
    return contracts.SafeWorkflowEvent(
        schema_version="1.0",
        event_id=EVENT_ID,
        sequence_number=1,
        occurred_at=CREATED_AT,
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        operation_id=None,
        phase=contracts.WorkflowPhase.REQUIREMENTS,
        event_type="workflow.started",
        counter_deltas=(
            contracts.SafeCounterDelta(name="workflow.started", delta=1),
        ),
        duration_ms=None,
        terminal_code=None,
    )


def _valid_budget_manager() -> contracts.InMemoryExecutionBudgetManager:
    key = contracts.ExecutionBudgetKey(
        scope=contracts.ExecutionBudgetScope.PHASE,
        name="test.security_budget",
        phase=contracts.WorkflowPhase.REQUIREMENTS,
    )
    plan = contracts.ExecutionBudgetPlan(
        schema_version="1.0",
        run_id=RUN_ID,
        limits=(contracts.ExecutionBudgetLimit(key=key, limit=2),),
    )
    return contracts.InMemoryExecutionBudgetManager(
        plan=plan,
        limits=contracts.ExecutionBudgetManagerLimits(max_operation_records=4),
    )


def _valid_summary() -> contracts.RunSummary:
    return contracts.RunSummary(
        schema_version="1.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        terminal_code="workflow_completed",
        duration_ms=1,
        cache_hits=1,
        event_delivery_failures=0,
        counters=(contracts.SafeCounterValue(name="artifact.cache_hit", value=1),),
        budget_snapshots=(),
    )


@pytest.mark.parametrize(
    ("value", "make_repr"),
    [
        pytest.param(
            CANARY,
            lambda: contracts.TravelSessionState(
                schema_version="2.0",
                run_id=RUN_ID,
                trace_id=TRACE_ID,
                phase=contracts.WorkflowPhase.REQUIREMENTS,
                delivery_status=contracts.DeliveryStatus.IN_PROGRESS,
                terminal_code=None,
                terminal_outcome=None,
                requirement_state=RequirementDomainState(),
                requirement_outcome=NeedClarification(
                    requirement=TravelRequirement(),
                    metadata=FieldMetadataRegistry(),
                    issues=(),
                    message=CANARY,
                ),
                artifact_refs=(),
            ),
            id="WF-STATE-REPR-CANARY",
        ),
        pytest.param(
            CANARY,
            lambda: _artifact_request(note=CANARY),
            id="ART-REPO-REQUEST-REPR-CANARY",
        ),
        pytest.param(
            CANARY,
            lambda: contracts.ArtifactReadResult(
                ref=_artifact_ref(),
                content=StrictSampleArtifact(
                    label="security",
                    count=1,
                    ratio=1.0,
                    occurred_on=date(2026, 8, 11),
                    observed_at=CREATED_AT,
                    note=CANARY,
                ),
            ),
            id="ART-REPO-RESULT-REPR-CANARY",
        ),
        pytest.param(CANARY, _valid_event, id="SAFE-EVT-REPR-CANARY"),
        pytest.param(CANARY, _valid_summary, id="RUN-SUM-REPR-CANARY"),
    ],
)
def test_canary_is_not_in_sensitive_repr(value: str, make_repr) -> None:
    assert value == CANARY
    assert CANARY not in repr(make_repr())


def test_nonempty_requirement_state_and_outcome_canaries_are_not_in_repr() -> None:
    state = _sensitive_nonempty_state()
    assert type(state).model_fields["requirement_state"].repr is False
    assert type(state).model_fields["requirement_outcome"].repr is False
    assert STATE_CANARY not in repr(state)
    assert OUTCOME_CANARY not in repr(state)


def test_sensitive_event_and_summary_surfaces_have_no_raw_payload_fields() -> None:
    forbidden = {
        "details",
        "metadata",
        "payload",
        "message",
        "raw_payload",
        "prompt",
        "provider_response",
    }
    assert not forbidden.intersection(contracts.SafeWorkflowEvent.model_fields)
    assert not forbidden.intersection(contracts.RunSummary.model_fields)
    assert CANARY not in repr(_valid_event())
    assert CANARY not in repr(_valid_summary())


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        pytest.param("phase", CANARY, id="WF-STATE-SCALAR"),
        pytest.param("run_id", str(RUN_ID), id="WF-STATE-UUID"),
        pytest.param("trace_id", CREATED_AT, id="WF-STATE-DATETIME"),
        pytest.param("terminal_outcome", b"payload", id="WF-STATE-BYTES"),
        pytest.param("terminal_outcome", (), id="WF-STATE-TUPLE"),
        pytest.param("artifact_refs", {"not-a-ref"}, id="WF-STATE-SET"),
    ],
)
def test_state_python_tree_preflight_rejects_forged_values(
    field_name: str,
    bad_value: object,
) -> None:
    values = {
        "schema_version": "2.0",
        "run_id": RUN_ID,
        "trace_id": TRACE_ID,
        "phase": contracts.WorkflowPhase.REQUIREMENTS,
        "delivery_status": contracts.DeliveryStatus.IN_PROGRESS,
        "terminal_code": None,
        "terminal_outcome": None,
        "requirement_state": RequirementDomainState(),
        "requirement_outcome": None,
        "artifact_refs": (),
    }
    values[field_name] = bad_value
    forged = contracts.TravelSessionState.model_construct(**values)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(contracts.WorkflowStateValidationError) as error_info:
            contracts.serialize_travel_session_state(forged)
    assert CANARY not in str(error_info.value)
    assert CANARY not in repr(error_info.value)


@pytest.mark.parametrize(
    "bad_value",
    [
        pytest.param(datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc), id="ART-REPO-DATETIME"),
        pytest.param(UUID("52345678-1234-5678-1234-567812345678"), id="ART-REPO-UUID"),
        pytest.param(b"bytes", id="ART-REPO-BYTES"),
        pytest.param(("tuple",), id="ART-REPO-TUPLE"),
        pytest.param({"set"}, id="ART-REPO-SET"),
    ],
)
def test_artifact_content_preflight_rejects_json_washing(bad_value: object) -> None:
    forged = contracts.ArtifactWriteRequest.model_construct(
        operation_id=_operation("artifact_forged"),
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={
            "label": "security",
            "count": 1,
            "ratio": 1.0,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": bad_value,
        },
    )
    with pytest.raises(contracts.ArtifactRequestValidationError) as error_info:
        _artifact_registry().prepare(forged)
    assert CANARY not in str(error_info.value)
    assert CANARY not in repr(error_info.value)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        pytest.param("sequence_number", "1", id="SAFE-EVT-SCALAR"),
        pytest.param("event_id", str(EVENT_ID), id="SAFE-EVT-UUID"),
        pytest.param("occurred_at", b"datetime", id="SAFE-EVT-BYTES"),
        pytest.param("operation_id", (), id="SAFE-EVT-TUPLE"),
        pytest.param("counter_deltas", {"not-a-counter"}, id="SAFE-EVT-SET"),
    ],
)
def test_event_python_tree_preflight_rejects_forged_values(
    field_name: str,
    bad_value: object,
) -> None:
    values = _valid_event().model_dump()
    values[field_name] = bad_value
    forged = contracts.SafeWorkflowEvent.model_construct(**values)
    registry = contracts.SafeWorkflowEventRegistry(
        contracts.STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(contracts.SafeWorkflowEventValidationError) as error_info:
            registry.validate(forged)
    assert CANARY not in str(error_info.value)
    assert CANARY not in repr(error_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        pytest.param("amount", "1", id="EXEC-BUD-SCALAR"),
        pytest.param("operation_id", str(_operation()), id="EXEC-BUD-UUID"),
        pytest.param("key", b"key", id="EXEC-BUD-BYTES"),
        pytest.param("amount", (), id="EXEC-BUD-TUPLE"),
        pytest.param("key", {"key"}, id="EXEC-BUD-SET"),
    ],
)
async def test_budget_python_tree_preflight_rejects_forged_values(
    field_name: str,
    bad_value: object,
) -> None:
    key = contracts.ExecutionBudgetKey(
        scope=contracts.ExecutionBudgetScope.PHASE,
        name="test.security_budget",
        phase=contracts.WorkflowPhase.REQUIREMENTS,
    )
    values: dict[str, object] = {
        "operation_id": _operation("budget_forged"),
        "key": key,
        "amount": 1,
    }
    values[field_name] = bad_value
    forged = contracts.BudgetConsumeRequest.model_construct(**values)
    with pytest.raises(contracts.ExecutionBudgetRequestValidationError) as error_info:
        await _valid_budget_manager().consume(forged)
    assert CANARY not in str(error_info.value)
    assert CANARY not in repr(error_info.value)


def test_nested_mutation_is_revalidated_at_behavior_boundaries() -> None:
    request = _artifact_request(note="safe")
    request.content["note"] = {"nested"}
    with pytest.raises(contracts.ArtifactRequestValidationError):
        _artifact_registry().prepare(request)

    outcome = NeedClarification(
        requirement=TravelRequirement(),
        metadata=FieldMetadataRegistry(),
        issues=(),
        message="safe",
    )
    state_with_mutated_nested_outcome = contracts.TravelSessionState(
        schema_version="2.0",
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        phase=contracts.WorkflowPhase.REQUIREMENTS,
        delivery_status=contracts.DeliveryStatus.IN_PROGRESS,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=RequirementDomainState(),
        requirement_outcome=outcome,
        artifact_refs=(),
    )
    state_with_mutated_nested_outcome.requirement_outcome.message = object()  # type: ignore[union-attr]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(contracts.WorkflowStateValidationError):
            contracts.serialize_travel_session_state(state_with_mutated_nested_outcome)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("state", id="WF-STATE-CANARY-ERROR"),
        pytest.param("artifact", id="ART-REPO-CANARY-ERROR"),
        pytest.param("budget", id="EXEC-BUD-CANARY-ERROR"),
        pytest.param("event", id="SAFE-EVT-CANARY-ERROR"),
    ],
)
@pytest.mark.asyncio
async def test_typed_errors_do_not_expose_canary(case: str) -> None:
    if case == "state":
        forged = contracts.TravelSessionState.model_construct(
            schema_version="2.0",
            run_id=RUN_ID,
            trace_id=TRACE_ID,
            phase=CANARY,
            delivery_status=contracts.DeliveryStatus.IN_PROGRESS,
            terminal_code=None,
            terminal_outcome=None,
            requirement_state=RequirementDomainState(),
            requirement_outcome=None,
            artifact_refs=(),
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(contracts.WorkflowStateValidationError) as error_info:
                contracts.serialize_travel_session_state(forged)
    elif case == "artifact":
        forged = contracts.ArtifactWriteRequest.model_construct(
            operation_id=_operation("artifact_canary"),
            artifact_type=CANARY,
            schema_version="1.0",
            content={},
        )
        with pytest.raises(contracts.ArtifactRequestValidationError) as error_info:
            _artifact_registry().prepare(forged)
    elif case == "budget":
        forged = contracts.BudgetConsumeRequest.model_construct(
            operation_id=_operation("budget_canary"),
            key=CANARY,
            amount=1,
        )
        with pytest.raises(contracts.ExecutionBudgetRequestValidationError) as error_info:
            await _valid_budget_manager().consume(forged)
    else:
        forged = contracts.SafeWorkflowEvent.model_construct(
            schema_version="1.0",
            event_id=EVENT_ID,
            sequence_number=1,
            occurred_at=CREATED_AT,
            run_id=RUN_ID,
            trace_id=TRACE_ID,
            operation_id=None,
            phase=contracts.WorkflowPhase.REQUIREMENTS,
            event_type=CANARY,
            counter_deltas=(),
            duration_ms=None,
            terminal_code=None,
        )
        registry = contracts.SafeWorkflowEventRegistry(
            contracts.STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(contracts.SafeWorkflowEventValidationError) as error_info:
                registry.validate(forged)

    assert CANARY not in str(error_info.value)
    assert CANARY not in repr(error_info.value)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        pytest.param("duration_ms", "1", id="RUN-SUM-SCALAR"),
        pytest.param("run_id", str(RUN_ID), id="RUN-SUM-UUID"),
        pytest.param("trace_id", CREATED_AT, id="RUN-SUM-DATETIME"),
        pytest.param("counters", b"counters", id="RUN-SUM-BYTES"),
        pytest.param("budget_snapshots", ("not-a-snapshot",), id="RUN-SUM-TUPLE"),
        pytest.param("counters", {"counter"}, id="RUN-SUM-SET"),
    ],
)
def test_summary_forged_values_are_not_json_washed(
    field_name: str,
    bad_value: object,
) -> None:
    values = _valid_summary().model_dump(mode="python")
    values[field_name] = bad_value
    forged = contracts.RunSummary.model_construct(**values)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(ValidationError):
            TypeAdapter(contracts.RunSummary).validate_python(
                forged.model_dump(mode="python"),
                strict=True,
            )
