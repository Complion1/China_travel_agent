"""Contract tests for workflow identifiers and public marker errors."""

from enum import Enum
from typing import Annotated, get_origin
from uuid import UUID

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.workflow_contracts import errors
from app.workflow_contracts.identifiers import (
    ArtifactSchemaVersion,
    ArtifactType,
    ContentHash,
    EventDeliveryFailureCode,
    ExecutionBudgetName,
    FieldRequirement,
    LogicalStep,
    OperationId,
    SafeCounterName,
    TerminalCode,
    UserActionCode,
    WorkflowEventType,
    WorkflowPhase,
)


def _assert_valid(annotation: object, value: object) -> None:
    assert TypeAdapter(annotation).validate_python(value, strict=True) == value


def _assert_invalid(annotation: object, value: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(annotation).validate_python(value, strict=True)


def test_error_hierarchy_is_complete_and_marker_only() -> None:
    expected_parents = {
        "WorkflowContractsError": None,
        "WorkflowStateContractError": "WorkflowContractsError",
        "UnsupportedWorkflowStateVersionError": "WorkflowStateContractError",
        "WorkflowStateValidationError": "WorkflowStateContractError",
        "TerminalContractError": "WorkflowContractsError",
        "TerminalRegistryConfigurationError": "TerminalContractError",
        "UnsupportedTerminalCodeError": "TerminalContractError",
        "TerminalStateMismatchError": "TerminalContractError",
        "ArtifactRepositoryError": "WorkflowContractsError",
        "ArtifactRegistryConfigurationError": "ArtifactRepositoryError",
        "ArtifactRequestValidationError": "ArtifactRepositoryError",
        "UnsupportedArtifactTypeError": "ArtifactRepositoryError",
        "UnsupportedArtifactVersionError": "ArtifactRepositoryError",
        "ArtifactExpectedModelMismatchError": "ArtifactRepositoryError",
        "ArtifactNotFoundError": "ArtifactRepositoryError",
        "ArtifactConflictError": "ArtifactRepositoryError",
        "ArtifactIntegrityError": "ArtifactRepositoryError",
        "ArtifactTooLargeError": "ArtifactRepositoryError",
        "ArtifactCapacityExceededError": "ArtifactRepositoryError",
        "ArtifactRepositoryDependencyError": "ArtifactRepositoryError",
        "ArtifactRepositoryUsageError": "ArtifactRepositoryError",
        "ExecutionBudgetError": "WorkflowContractsError",
        "ExecutionBudgetPlanValidationError": "ExecutionBudgetError",
        "ExecutionBudgetRequestValidationError": "ExecutionBudgetError",
        "BudgetKeyNotConfiguredError": "ExecutionBudgetError",
        "BudgetOperationScopeMismatchError": "ExecutionBudgetError",
        "BudgetConsumeConflictError": "ExecutionBudgetError",
        "ExecutionBudgetCapacityExceededError": "ExecutionBudgetError",
        "ExecutionBudgetManagerUsageError": "ExecutionBudgetError",
        "WorkflowReplaySafetyError": "WorkflowContractsError",
        "OperationResultIndeterminateError": "WorkflowReplaySafetyError",
        "SafeWorkflowEventError": "WorkflowContractsError",
        "SafeWorkflowEventRegistryConfigurationError": "SafeWorkflowEventError",
        "SafeWorkflowEventValidationError": "SafeWorkflowEventError",
        "UnsupportedWorkflowEventTypeError": "SafeWorkflowEventError",
        "EventIdentityConflictError": "SafeWorkflowEventError",
        "EventSequenceConflictError": "SafeWorkflowEventError",
        "EventSinkContractError": "SafeWorkflowEventError",
    }

    public_error_names = {
        name for name in vars(errors) if name.endswith("Error") and not name.startswith("_")
    }
    assert public_error_names == set(expected_parents)

    for name, parent_name in expected_parents.items():
        error_type = getattr(errors, name)
        if parent_name is not None:
            assert error_type.__bases__ == (getattr(errors, parent_name),)
        error = error_type()
        assert error.args == ()
        assert vars(error) == {}
        assert str(error) == name
        with pytest.raises(TypeError):
            error_type("do-not-store-input")


def test_enums_have_exact_members_and_values() -> None:
    assert list(WorkflowPhase) == [
        WorkflowPhase.REQUIREMENTS,
        WorkflowPhase.HOTEL_SELECTION,
        WorkflowPhase.CANDIDATE_RESEARCH,
        WorkflowPhase.ITINERARY_PLANNING,
        WorkflowPhase.VALIDATION,
        WorkflowPhase.BUDGET_EVALUATION,
        WorkflowPhase.OUTPUT_FORMATTING,
    ]
    assert [member.value for member in WorkflowPhase] == [
        "requirements",
        "hotel_selection",
        "candidate_research",
        "itinerary_planning",
        "validation",
        "budget_evaluation",
        "output_formatting",
    ]
    assert list(FieldRequirement) == [
        FieldRequirement.REQUIRED,
        FieldRequirement.OPTIONAL,
        FieldRequirement.FORBIDDEN,
    ]
    assert [member.value for member in FieldRequirement] == [
        "required",
        "optional",
        "forbidden",
    ]
    assert issubclass(WorkflowPhase, str)
    assert issubclass(FieldRequirement, str)
    assert issubclass(WorkflowPhase, Enum)
    assert issubclass(FieldRequirement, Enum)


@pytest.mark.parametrize(
    ("annotation", "valid", "invalid"),
    [
        (TerminalCode, ["a", "a" + "b" * 63], ["", "A", "a-b", "a" * 65]),
        (UserActionCode, ["a", "a" + "b" * 63], ["", "A", "a-b", "a" * 65]),
        (LogicalStep, ["a", "a" + "b" * 63], ["", "A", "a-b", "a" * 65]),
        (
            EventDeliveryFailureCode,
            ["a", "a" + "b" * 63],
            ["", "A", "a-b", "a" * 65],
        ),
        (
            ArtifactType,
            [
                "a.b",
                "a" + "x" * 31 + ".b" + "y" * 31,
            ],
            ["ab", "A.b", "a", "a.", "a.b.c", "a" * 33 + ".b", "a.b" + "c" * 32],
        ),
        (
            ExecutionBudgetName,
            [
                "a.b",
                "a" + "x" * 31 + ".b" + "y" * 31,
            ],
            ["ab", "A.b", "a", "a.", "a.b.c", "a" * 33 + ".b", "a.b" + "c" * 32],
        ),
        (
            WorkflowEventType,
            [
                "a.b",
                "a" + "x" * 31 + ".b" + "y" * 31,
            ],
            ["ab", "A.b", "a", "a.", "a.b.c", "a" * 33 + ".b", "a.b" + "c" * 32],
        ),
        (
            SafeCounterName,
            [
                "a.b",
                "a" + "x" * 31 + ".b" + "y" * 31,
            ],
            ["ab", "A.b", "a", "a.", "a.b.c", "a" * 33 + ".b", "a.b" + "c" * 32],
        ),
        (
            ArtifactSchemaVersion,
            ["0.0", "1.0", "1234567890.12345"],
            ["", "1", "01.0", "1.00", "1.0.0", "a.1", "12345678901.12345"],
        ),
        (
            ContentHash,
            ["sha256:" + "0" * 64, "sha256:" + "abcdef0123456789" * 4],
            ["sha256:" + "0" * 63, "sha256:" + "A" * 64, "sha256:" + "g" * 64, "md5:" + "0" * 64],
        ),
    ],
)
def test_constrained_identifier_vectors(
    annotation: object,
    valid: list[str],
    invalid: list[str],
) -> None:
    for value in valid:
        _assert_valid(annotation, value)
    for value in invalid:
        _assert_invalid(annotation, value)
    _assert_invalid(annotation, 123)


def test_operation_id_is_strict_frozen_hashable_and_safe() -> None:
    run_id = UUID("12345678-1234-5678-1234-567812345678")
    operation_id = OperationId(
        run_id=run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="collect_input",
    )
    same_operation_id = OperationId(
        run_id=run_id,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step="collect_input",
    )

    assert operation_id.model_dump() == {
        "run_id": run_id,
        "phase": WorkflowPhase.REQUIREMENTS,
        "logical_step": "collect_input",
    }
    assert operation_id.safe_display() == (
        "12345678-1234-5678-1234-567812345678:requirements:collect_input"
    )
    assert operation_id == same_operation_id
    assert hash(operation_id) == hash(same_operation_id)
    assert OperationId.__str__ is BaseModel.__str__
    assert str(operation_id) != operation_id.safe_display()

    with pytest.raises(ValidationError):
        OperationId(
            run_id=str(run_id),
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="collect_input",
        )
    with pytest.raises(ValidationError):
        OperationId(
            run_id=run_id,
            phase="requirements",
            logical_step="collect_input",
        )
    with pytest.raises(ValidationError):
        OperationId(
            run_id=run_id,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step="collect_input",
            extra="forbidden",
        )
    with pytest.raises(ValidationError):
        operation_id.logical_step = "changed"


@pytest.mark.parametrize("missing", ["run_id", "phase", "logical_step"])
def test_operation_id_fields_are_all_required(missing: str) -> None:
    values = {
        "run_id": UUID("12345678-1234-5678-1234-567812345678"),
        "phase": WorkflowPhase.REQUIREMENTS,
        "logical_step": "collect_input",
    }
    values.pop(missing)
    with pytest.raises(ValidationError):
        OperationId(**values)


def test_identifier_aliases_are_annotated_string_constraints() -> None:
    aliases = [
        TerminalCode,
        UserActionCode,
        LogicalStep,
        ArtifactType,
        ArtifactSchemaVersion,
        ContentHash,
        ExecutionBudgetName,
        WorkflowEventType,
        SafeCounterName,
        EventDeliveryFailureCode,
    ]
    for alias in aliases:
        assert get_origin(alias) is Annotated
