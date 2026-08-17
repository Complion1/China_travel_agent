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
from app.requirement_subgraph import (
    RequirementCheckpointSerializer,
    RequirementSubgraphRuntime,
    build_requirement_subgraph,
    create_demo_requirement_subgraph_runtime,
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
    "RequirementCheckpointSerializer",
    "RequirementSubgraphRuntime",
    "build_requirement_subgraph",
    "create_demo_requirement_subgraph_runtime",
}


def test_package_root_exports_only_implemented_final_symbols():
    assert set(package.__all__) == EXPECTED_T01_T02_EXPORTS
    assert set(package.__all__) == {
        name for name in package.__all__ if not name.startswith("_")
    }
    assert package.RequirementCheckpointSerializer is RequirementCheckpointSerializer
    assert package.RequirementSubgraphRuntime is RequirementSubgraphRuntime
    assert package.build_requirement_subgraph is build_requirement_subgraph
    assert (
        package.create_demo_requirement_subgraph_runtime
        is create_demo_requirement_subgraph_runtime
    )


def test_public_dto_and_exception_symbols_are_real_objects():
    assert inspect.isclass(RequirementSubgraphCoordinatorLimits)
    error = RequirementSubgraphError()
    assert str(error) == "RequirementSubgraphError"
    assert error.args == ()


def test_t03_t04_public_controls_have_frozen_signatures_and_no_checkpoint_identity():
    assert tuple(inspect.signature(build_requirement_subgraph).parameters) == (
        "coordinator",
        "checkpointer",
    )
    assert tuple(inspect.signature(RequirementSubgraphRuntime.start).parameters) == (
        "self",
        "state",
        "turn",
    )
    assert tuple(inspect.signature(RequirementSubgraphRuntime.resume).parameters) == (
        "self",
        "run_id",
        "envelope",
    )
    assert tuple(inspect.signature(RequirementSubgraphRuntime.get_state).parameters) == (
        "self",
        "run_id",
    )
    for method in (
        RequirementSubgraphRuntime.start,
        RequirementSubgraphRuntime.resume,
        RequirementSubgraphRuntime.get_state,
    ):
        assert "checkpoint_id" not in inspect.signature(method).parameters
        assert "checkpoint_ns" not in inspect.signature(method).parameters


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
