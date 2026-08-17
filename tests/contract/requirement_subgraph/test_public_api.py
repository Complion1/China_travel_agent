from __future__ import annotations

import inspect

import pytest

import app.requirement_subgraph as package
from app.requirement_subgraph import (
    RequirementSubgraphCapacityExceededError,
    RequirementSubgraphConfigurationError,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphError,
    RequirementSubgraphInputError,
    RequirementSubgraphResumeRejectedError,
    RequirementSubgraphRunFaultedError,
    RequirementSubgraphStateError,
)


EXPECTED_T01_T02_EXPORTS = {
    "RequirementClarificationInterrupt",
    "RequirementConfirmationInterrupt",
    "RequirementInterrupt",
    "RequirementClarificationResume",
    "RequirementConfirmationResume",
    "RequirementResume",
    "RequirementPauseEnvelope",
    "RequirementResumeEnvelope",
    "RequirementSubgraphPaused",
    "RequirementSubgraphConfirmed",
    "RequirementSubgraphDomainFailed",
    "RequirementSubgraphBudgetExceeded",
    "RequirementSubgraphResult",
    "RequirementSubgraphCoordinatorLimits",
    "DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS",
    "REQUIREMENTS_CLARIFICATION_ROUND_KEY",
    "DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT",
    "StartAccepted",
    "ResumeAccepted",
    "NodeReplayed",
    "BudgetConsumed",
    "BudgetExceeded",
    "RequirementSubgraphSignal",
    "RequirementSubgraphSignalSink",
    "NoOpRequirementSubgraphSignalSink",
    "RequirementSubgraphError",
    "RequirementSubgraphConfigurationError",
    "RequirementSubgraphInputError",
    "RequirementSubgraphResumeRejectedError",
    "RequirementSubgraphStateError",
    "RequirementSubgraphRunFaultedError",
    "RequirementSubgraphCapacityExceededError",
    "RequirementSubgraphCoordinator",
}


def test_package_root_exports_only_implemented_final_symbols():
    assert set(package.__all__) == EXPECTED_T01_T02_EXPORTS
    assert set(package.__all__) == {
        name for name in package.__all__ if not name.startswith("_")
    }
    assert not hasattr(package, "RequirementCheckpointSerializer")
    assert not hasattr(package, "RequirementSubgraphRuntime")
    assert not hasattr(package, "build_requirement_subgraph")


def test_public_dto_and_exception_symbols_are_real_objects():
    assert inspect.isclass(RequirementSubgraphCoordinatorLimits)
    error = RequirementSubgraphError()
    assert str(error) == "RequirementSubgraphError"
    assert error.args == ()


def test_exception_tree_is_zero_argument_and_exactly_inherited():
    expected = (
        (RequirementSubgraphConfigurationError, RequirementSubgraphError),
        (RequirementSubgraphInputError, RequirementSubgraphError),
        (RequirementSubgraphResumeRejectedError, RequirementSubgraphInputError),
        (RequirementSubgraphStateError, RequirementSubgraphError),
        (RequirementSubgraphRunFaultedError, RequirementSubgraphError),
        (RequirementSubgraphCapacityExceededError, RequirementSubgraphError),
    )
    for error_type, parent in expected:
        error = error_type()
        assert error.__class__.__bases__ == (parent,)
        assert error.args == ()
        assert str(error) == error_type.__name__
        assert error.__cause__ is None
        with pytest.raises(TypeError):
            error_type("unexpected data")
