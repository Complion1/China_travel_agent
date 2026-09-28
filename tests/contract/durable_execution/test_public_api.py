from __future__ import annotations

import inspect
from typing import get_args

import app.durable_execution as durable_execution
from app.durable_execution.models import DurableExecutionResult
from app.durable_execution.runtime import (
    DurableExecutionRuntime,
    create_durable_execution_runtime,
)


EXPECTED_PUBLIC_API = (
    "DurableExecutionSettings",
    "DurableExecutionLimits",
    "DEMO_DURABLE_EXECUTION_LIMITS",
    "load_durable_execution_settings",
    "NodeRecoveryClass",
    "DurableExecutionNodeRecoveryRule",
    "DurableExecutionCommandCodecDescriptor",
    "DurableExecutionContract",
    "DurableExecutionCommandCodec",
    "DurableExecutionBudgetPlanProvider",
    "DurableExecutionGraphCapabilities",
    "DurableExecutionNodeCapabilities",
    "DurableExecutionEncodedCommand",
    "DurableExecutionPauseEnvelope",
    "DurableExecutionPaused",
    "DurableExecutionTerminal",
    "DurableExecutionResult",
    "DurableCancellationReceipt",
    "DurableRunCursor",
    "DurableRunListRequest",
    "DurableRunPage",
    "DurableRunView",
    "DurablePauseCredentialSink",
    "DurableExecutionRuntime",
    "create_durable_execution_runtime",
    "DurableExecutionError",
    "DurableExecutionConfigurationError",
    "DurableExecutionDependencyError",
    "DurableExecutionUsageError",
    "DurableExecutionRunNotFoundError",
    "DurableExecutionRunAlreadyExistsError",
    "DurableExecutionRunBusyError",
    "DurableExecutionRunAwaitingInputError",
    "DurableExecutionResumeRejectedError",
    "DurableExecutionCapacityExceededError",
    "DurableExecutionIncompatibleRunError",
    "DurableExecutionRunBlockedError",
    "DurableExecutionStateError",
    "DurableExecutionCancellationPendingError",
)

EXPECTED_PUBLIC_MODULES = {
    "DurableExecutionSettings": "app.durable_execution.settings",
    "DurableExecutionLimits": "app.durable_execution.models",
    "DEMO_DURABLE_EXECUTION_LIMITS": "app.durable_execution.models",
    "load_durable_execution_settings": "app.durable_execution.settings",
    "NodeRecoveryClass": "app.durable_execution.models",
    "DurableExecutionNodeRecoveryRule": "app.durable_execution.models",
    "DurableExecutionCommandCodecDescriptor": "app.durable_execution.models",
    "DurableExecutionContract": "app.durable_execution.models",
    "DurableExecutionCommandCodec": "app.durable_execution.protocols",
    "DurableExecutionBudgetPlanProvider": "app.durable_execution.protocols",
    "DurableExecutionGraphCapabilities": "app.durable_execution.protocols",
    "DurableExecutionNodeCapabilities": "app.durable_execution.protocols",
    "DurableExecutionEncodedCommand": "app.durable_execution.models",
    "DurableExecutionPauseEnvelope": "app.durable_execution.models",
    "DurableExecutionPaused": "app.durable_execution.models",
    "DurableExecutionTerminal": "app.durable_execution.models",
    "DurableExecutionResult": "typing",
    "DurableCancellationReceipt": "app.durable_execution.models",
    "DurableRunCursor": "app.durable_execution.models",
    "DurableRunListRequest": "app.durable_execution.models",
    "DurableRunPage": "app.durable_execution.models",
    "DurableRunView": "app.durable_execution.models",
    "DurablePauseCredentialSink": "app.durable_execution.protocols",
    "DurableExecutionRuntime": "app.durable_execution.runtime",
    "create_durable_execution_runtime": "app.durable_execution.runtime",
    "DurableExecutionError": "app.durable_execution.errors",
    "DurableExecutionConfigurationError": "app.durable_execution.errors",
    "DurableExecutionDependencyError": "app.durable_execution.errors",
    "DurableExecutionUsageError": "app.durable_execution.errors",
    "DurableExecutionRunNotFoundError": "app.durable_execution.errors",
    "DurableExecutionRunAlreadyExistsError": "app.durable_execution.errors",
    "DurableExecutionRunBusyError": "app.durable_execution.errors",
    "DurableExecutionRunAwaitingInputError": "app.durable_execution.errors",
    "DurableExecutionResumeRejectedError": "app.durable_execution.errors",
    "DurableExecutionCapacityExceededError": "app.durable_execution.errors",
    "DurableExecutionIncompatibleRunError": "app.durable_execution.errors",
    "DurableExecutionRunBlockedError": "app.durable_execution.errors",
    "DurableExecutionStateError": "app.durable_execution.errors",
    "DurableExecutionCancellationPendingError": "app.durable_execution.errors",
}


def test_root_public_api_is_exact_and_does_not_expose_storage() -> None:
    assert type(durable_execution.__all__) is list
    assert tuple(durable_execution.__all__) == EXPECTED_PUBLIC_API
    assert tuple(dict.fromkeys(durable_execution.__all__)) == EXPECTED_PUBLIC_API
    assert {
        name: getattr(durable_execution, name).__module__
        for name in EXPECTED_PUBLIC_API
    } == EXPECTED_PUBLIC_MODULES
    result_union = get_args(durable_execution.DurableExecutionResult)[0]
    assert {
        member.__module__
        for member in get_args(result_union)
    } == {"app.durable_execution.models"}
    assert "AsyncPostgresSaver" not in durable_execution.__all__
    assert "ArtifactRepository" not in durable_execution.__all__
    assert "Repository" not in durable_execution.__all__
    assert "pool" not in {item.lower() for item in durable_execution.__all__}
    assert "SQL" not in durable_execution.__all__
    assert "history" not in durable_execution.__all__
    assert "delete" not in durable_execution.__all__


def test_runtime_and_factory_have_the_fixed_public_signatures() -> None:
    for method_name in ("start", "resume", "recover", "get_state", "request_cancel", "settle_cancel", "get_run", "list_runs", "get_terminal_budget_snapshots", "close", "__aenter__", "__aexit__"):
        assert callable(getattr(DurableExecutionRuntime, method_name))
    assert "DurableExecutionResult" in str(inspect.signature(DurableExecutionRuntime.start).return_annotation)
    assert DurableExecutionResult is not None


def test_runtime_entrypoints_and_factory_parameter_order_are_exact() -> None:
    expected = {
        "start": ("self", "run_id", "trace_id", "command"),
        "resume": ("self", "run_id", "pause_token", "command"),
        "recover": ("self", "run_id"),
        "get_state": ("self", "run_id"),
        "request_cancel": ("self", "run_id"),
        "settle_cancel": ("self", "run_id"),
        "get_run": ("self", "run_id"),
        "list_runs": ("self", "request"),
        "get_terminal_budget_snapshots": ("self", "run_id"),
        "close": ("self",),
        "__aenter__": ("self",),
        "__aexit__": ("self", "exc_type", "exc_value", "traceback"),
    }
    for method_name, parameters in expected.items():
        signature = inspect.signature(getattr(DurableExecutionRuntime, method_name))
        assert tuple(signature.parameters) == parameters
        if method_name == "__aexit__":
            assert all(
                parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                for parameter in list(signature.parameters.values())[1:]
            )
        else:
            assert all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in list(signature.parameters.values())[1:]
            )

    factory_signature = inspect.signature(create_durable_execution_runtime)
    assert tuple(factory_signature.parameters) == (
        "settings", "artifact_repository", "contract", "command_codec",
        "budget_plan_provider", "graph_factory", "limits",
        "pause_credential_sink", "cancellation_state_factory",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in factory_signature.parameters.values()
    )
    assert factory_signature.parameters["limits"].default is not inspect.Parameter.empty
