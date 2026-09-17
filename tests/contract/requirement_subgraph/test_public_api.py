from __future__ import annotations

import inspect

import pytest

import app.requirement_subgraph as package
from app.requirement_subgraph import (
    RequirementSubgraphCapacityExceededError,
    RequirementSubgraphConfigurationError,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphEmbeddedDriver,
    RequirementSubgraphError,
    RequirementSubgraphInputError,
    RequirementSubgraphResumeRejectedError,
    RequirementSubgraphRunFaultedError,
    RequirementSubgraphStateError,
    create_requirement_subgraph_embedded_driver,
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
    "RequirementSubgraphDurablePause",
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
    "RequirementSubgraphEmbeddedDriver",
    "RequirementCheckpointSerializer",
    "RequirementSubgraphRuntime",
    "build_requirement_subgraph",
    "create_demo_requirement_subgraph_runtime",
    "create_requirement_subgraph_embedded_driver",
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


def test_embedded_driver_public_surface_is_exact_and_has_no_checkpoint_identity():
    constructor = inspect.signature(RequirementSubgraphEmbeddedDriver)
    assert tuple(constructor.parameters) == ("graph", "coordinator")
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in constructor.parameters.values()
    )
    assert tuple(
        parameter.annotation for parameter in constructor.parameters.values()
    ) == ("CompiledStateGraph", "RequirementSubgraphCoordinator")
    assert all(
        parameter.default is inspect.Signature.empty
        for parameter in constructor.parameters.values()
    )
    assert constructor.return_annotation == "None"
    assert isinstance(RequirementSubgraphEmbeddedDriver.graph, property)
    assert isinstance(RequirementSubgraphEmbeddedDriver.limits, property)
    assert RequirementSubgraphEmbeddedDriver.graph.fset is None
    assert RequirementSubgraphEmbeddedDriver.limits.fset is None
    assert inspect.signature(
        RequirementSubgraphEmbeddedDriver.graph.fget
    ).return_annotation == "CompiledStateGraph"
    assert inspect.signature(
        RequirementSubgraphEmbeddedDriver.limits.fget
    ).return_annotation == "RequirementSubgraphCoordinatorLimits"
    expected = {
        "start_operation": (
            ("self", "state", "turn"),
            (inspect.Parameter.POSITIONAL_OR_KEYWORD,) + (inspect.Parameter.KEYWORD_ONLY,) * 2,
            (inspect.Signature.empty, "TravelSessionState", "RequirementTurnInput"),
            "AbstractAsyncContextManager[TravelSessionState]",
        ),
        "verify_pause_token": (
            ("self", "durable_pause", "pause_token"),
            (inspect.Parameter.POSITIONAL_OR_KEYWORD,) + (inspect.Parameter.KEYWORD_ONLY,) * 2,
            (inspect.Signature.empty, "RequirementSubgraphDurablePause", "PauseToken"),
            "None",
        ),
        "resume_accepted_operation": (
            ("self", "state", "durable_pause", "resume"),
            (inspect.Parameter.POSITIONAL_OR_KEYWORD,) + (inspect.Parameter.KEYWORD_ONLY,) * 3,
            (
                inspect.Signature.empty,
                "TravelSessionState",
                "RequirementSubgraphDurablePause",
                "RequirementResume",
            ),
            "AbstractAsyncContextManager[Command]",
        ),
        "resolve_result": (
            ("self", "state", "interrupt_value"),
            (inspect.Parameter.POSITIONAL_OR_KEYWORD,) + (inspect.Parameter.KEYWORD_ONLY,) * 2,
            (inspect.Signature.empty, "TravelSessionState", "object | None"),
            "RequirementSubgraphResult",
        ),
    }
    for name, (names, kinds, annotations, return_annotation) in expected.items():
        method = getattr(RequirementSubgraphEmbeddedDriver, name)
        signature = inspect.signature(method)
        assert tuple(signature.parameters) == names
        assert tuple(parameter.kind for parameter in signature.parameters.values()) == kinds
        assert tuple(
            parameter.annotation for parameter in signature.parameters.values()
        ) == annotations
        assert all(
            parameter.default is inspect.Signature.empty
            for parameter in signature.parameters.values()
        )
        assert signature.return_annotation == return_annotation
        assert "checkpoint_id" not in names
        assert "checkpoint_ns" not in names

    factory = inspect.signature(create_requirement_subgraph_embedded_driver)
    assert tuple(factory.parameters) == (
        "domain_service",
        "execution_budget_manager_factory",
        "signal_sink",
        "limits",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in factory.parameters.values()
    )
    assert tuple(parameter.annotation for parameter in factory.parameters.values()) == (
        "RequirementDomainService",
        "Callable[[UUID], ExecutionBudgetManager]",
        "RequirementSubgraphSignalSink",
        "RequirementSubgraphCoordinatorLimits",
    )
    assert factory.parameters["domain_service"].default is inspect.Signature.empty
    assert (
        factory.parameters["execution_budget_manager_factory"].default
        is inspect.Signature.empty
    )
    assert (
        type(factory.parameters["signal_sink"].default).__name__
        == "NoOpRequirementSubgraphSignalSink"
    )
    assert factory.parameters["limits"].default == package.DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS
    assert factory.return_annotation == "RequirementSubgraphEmbeddedDriver"


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
