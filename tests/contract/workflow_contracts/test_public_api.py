"""Contract checks for the stable module 04 package-root API."""

from __future__ import annotations

import asyncio
from inspect import iscoroutinefunction, signature

import app.workflow_contracts as root
from app.workflow_contracts import artifacts, budgets, events
from app.workflow_contracts.artifacts import in_memory as artifact_memory
from app.workflow_contracts.artifacts import models as artifact_models
from app.workflow_contracts.artifacts import registry as artifact_registry
from app.workflow_contracts.artifacts import repository as artifact_repository
from app.workflow_contracts.budgets import in_memory as budget_memory
from app.workflow_contracts.budgets import manager as budget_manager
from app.workflow_contracts.budgets import models as budget_models
from app.workflow_contracts.events import models as event_models
from app.workflow_contracts.events import registry as event_registry
from app.workflow_contracts.events import sink as event_sink
from app.workflow_contracts import errors, identifiers, providers, state, summary


EXPECTED_PUBLIC_NAMES = (
    "WorkflowPhase",
    "DeliveryStatus",
    "TerminalCategory",
    "FieldRequirement",
    "TerminalCode",
    "UserActionCode",
    "LogicalStep",
    "OperationId",
    "TerminalOutcome",
    "TravelSessionState",
    "TerminalCodeRegistration",
    "TerminalCodeRegistry",
    "BASE_TERMINAL_CODE_REGISTRATIONS",
    "serialize_travel_session_state",
    "parse_travel_session_state_json",
    "UtcClock",
    "MonotonicClock",
    "UUIDFactory",
    "SystemUtcClock",
    "SystemMonotonicClock",
    "RandomUUIDFactory",
    "ArtifactType",
    "ArtifactSchemaVersion",
    "ContentHash",
    "JsonValue",
    "ArtifactRef",
    "ArtifactWriteRequest",
    "ArtifactReadResult",
    "ArtifactFound",
    "ArtifactAbsent",
    "ArtifactLookupOutcome",
    "ArtifactSchemaRegistration",
    "ArtifactSchemaRegistry",
    "ArtifactRepository",
    "ArtifactRepositoryLimits",
    "DEMO_ARTIFACT_REPOSITORY_LIMITS",
    "InMemoryArtifactRepository",
    "ExecutionBudgetScope",
    "ExecutionBudgetName",
    "ExecutionBudgetKey",
    "ExecutionBudgetLimit",
    "ExecutionBudgetPlan",
    "ExecutionBudgetSnapshot",
    "BudgetConsumeRequest",
    "BudgetConsumed",
    "BudgetExceeded",
    "BudgetConsumeOutcome",
    "ExecutionBudgetManager",
    "ExecutionBudgetManagerLimits",
    "DEMO_EXECUTION_BUDGET_MANAGER_LIMITS",
    "InMemoryExecutionBudgetManager",
    "WorkflowEventType",
    "SafeCounterName",
    "EventDeliveryFailureCode",
    "SafeCounterDelta",
    "SafeWorkflowEvent",
    "SafeWorkflowEventRegistration",
    "SafeWorkflowEventRegistry",
    "STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS",
    "EventDeliveryFailureCodeRegistry",
    "STANDARD_EVENT_DELIVERY_FAILURE_CODES",
    "EventEmitted",
    "EventDeliveryFailed",
    "EventEmissionOutcome",
    "SafeWorkflowEventSink",
    "NoOpSafeWorkflowEventSink",
    "SafeCounterValue",
    "RunSummary",
    "WorkflowContractsError",
    "WorkflowStateContractError",
    "UnsupportedWorkflowStateVersionError",
    "WorkflowStateValidationError",
    "TerminalContractError",
    "TerminalRegistryConfigurationError",
    "UnsupportedTerminalCodeError",
    "TerminalStateMismatchError",
    "ArtifactRepositoryError",
    "ArtifactRegistryConfigurationError",
    "ArtifactRequestValidationError",
    "UnsupportedArtifactTypeError",
    "UnsupportedArtifactVersionError",
    "ArtifactExpectedModelMismatchError",
    "ArtifactNotFoundError",
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactTooLargeError",
    "ArtifactCapacityExceededError",
    "ArtifactRepositoryDependencyError",
    "ArtifactRepositoryUsageError",
    "ExecutionBudgetError",
    "ExecutionBudgetPlanValidationError",
    "ExecutionBudgetRequestValidationError",
    "BudgetKeyNotConfiguredError",
    "BudgetOperationScopeMismatchError",
    "BudgetConsumeConflictError",
    "ExecutionBudgetCapacityExceededError",
    "ExecutionBudgetManagerUsageError",
    "WorkflowReplaySafetyError",
    "OperationResultIndeterminateError",
    "SafeWorkflowEventError",
    "SafeWorkflowEventRegistryConfigurationError",
    "SafeWorkflowEventValidationError",
    "UnsupportedWorkflowEventTypeError",
    "EventIdentityConflictError",
    "EventSequenceConflictError",
    "EventSinkContractError",
)


EXPECTED_OBJECTS = {
    "WorkflowPhase": identifiers.WorkflowPhase,
    "DeliveryStatus": state.DeliveryStatus,
    "TerminalCategory": state.TerminalCategory,
    "FieldRequirement": identifiers.FieldRequirement,
    "TerminalCode": identifiers.TerminalCode,
    "UserActionCode": identifiers.UserActionCode,
    "LogicalStep": identifiers.LogicalStep,
    "OperationId": identifiers.OperationId,
    "TerminalOutcome": state.TerminalOutcome,
    "TravelSessionState": state.TravelSessionState,
    "TerminalCodeRegistration": state.TerminalCodeRegistration,
    "TerminalCodeRegistry": state.TerminalCodeRegistry,
    "BASE_TERMINAL_CODE_REGISTRATIONS": state.BASE_TERMINAL_CODE_REGISTRATIONS,
    "serialize_travel_session_state": state.serialize_travel_session_state,
    "parse_travel_session_state_json": state.parse_travel_session_state_json,
    "UtcClock": providers.UtcClock,
    "MonotonicClock": providers.MonotonicClock,
    "UUIDFactory": providers.UUIDFactory,
    "SystemUtcClock": providers.SystemUtcClock,
    "SystemMonotonicClock": providers.SystemMonotonicClock,
    "RandomUUIDFactory": providers.RandomUUIDFactory,
    "ArtifactType": identifiers.ArtifactType,
    "ArtifactSchemaVersion": identifiers.ArtifactSchemaVersion,
    "ContentHash": identifiers.ContentHash,
    "JsonValue": artifact_models.JsonValue,
    "ArtifactRef": artifact_models.ArtifactRef,
    "ArtifactWriteRequest": artifact_models.ArtifactWriteRequest,
    "ArtifactReadResult": artifact_models.ArtifactReadResult,
    "ArtifactFound": artifact_models.ArtifactFound,
    "ArtifactAbsent": artifact_models.ArtifactAbsent,
    "ArtifactLookupOutcome": artifact_models.ArtifactLookupOutcome,
    "ArtifactSchemaRegistration": artifact_registry.ArtifactSchemaRegistration,
    "ArtifactSchemaRegistry": artifact_registry.ArtifactSchemaRegistry,
    "ArtifactRepository": artifact_repository.ArtifactRepository,
    "ArtifactRepositoryLimits": artifact_models.ArtifactRepositoryLimits,
    "DEMO_ARTIFACT_REPOSITORY_LIMITS": artifact_memory.DEMO_ARTIFACT_REPOSITORY_LIMITS,
    "InMemoryArtifactRepository": artifact_memory.InMemoryArtifactRepository,
    "ExecutionBudgetScope": budget_models.ExecutionBudgetScope,
    "ExecutionBudgetName": identifiers.ExecutionBudgetName,
    "ExecutionBudgetKey": budget_models.ExecutionBudgetKey,
    "ExecutionBudgetLimit": budget_models.ExecutionBudgetLimit,
    "ExecutionBudgetPlan": budget_models.ExecutionBudgetPlan,
    "ExecutionBudgetSnapshot": budget_models.ExecutionBudgetSnapshot,
    "BudgetConsumeRequest": budget_models.BudgetConsumeRequest,
    "BudgetConsumed": budget_models.BudgetConsumed,
    "BudgetExceeded": budget_models.BudgetExceeded,
    "BudgetConsumeOutcome": budget_models.BudgetConsumeOutcome,
    "ExecutionBudgetManager": budget_manager.ExecutionBudgetManager,
    "ExecutionBudgetManagerLimits": budget_models.ExecutionBudgetManagerLimits,
    "DEMO_EXECUTION_BUDGET_MANAGER_LIMITS": budget_memory.DEMO_EXECUTION_BUDGET_MANAGER_LIMITS,
    "InMemoryExecutionBudgetManager": budget_memory.InMemoryExecutionBudgetManager,
    "WorkflowEventType": identifiers.WorkflowEventType,
    "SafeCounterName": identifiers.SafeCounterName,
    "EventDeliveryFailureCode": identifiers.EventDeliveryFailureCode,
    "SafeCounterDelta": event_models.SafeCounterDelta,
    "SafeWorkflowEvent": event_models.SafeWorkflowEvent,
    "SafeWorkflowEventRegistration": event_registry.SafeWorkflowEventRegistration,
    "SafeWorkflowEventRegistry": event_registry.SafeWorkflowEventRegistry,
    "STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS": event_registry.STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS,
    "EventDeliveryFailureCodeRegistry": event_registry.EventDeliveryFailureCodeRegistry,
    "STANDARD_EVENT_DELIVERY_FAILURE_CODES": event_registry.STANDARD_EVENT_DELIVERY_FAILURE_CODES,
    "EventEmitted": event_models.EventEmitted,
    "EventDeliveryFailed": event_models.EventDeliveryFailed,
    "EventEmissionOutcome": event_models.EventEmissionOutcome,
    "SafeWorkflowEventSink": event_sink.SafeWorkflowEventSink,
    "NoOpSafeWorkflowEventSink": event_sink.NoOpSafeWorkflowEventSink,
    "SafeCounterValue": summary.SafeCounterValue,
    "RunSummary": summary.RunSummary,
}


ERROR_NAMES = (
    "WorkflowContractsError",
    "WorkflowStateContractError",
    "UnsupportedWorkflowStateVersionError",
    "WorkflowStateValidationError",
    "TerminalContractError",
    "TerminalRegistryConfigurationError",
    "UnsupportedTerminalCodeError",
    "TerminalStateMismatchError",
    "ArtifactRepositoryError",
    "ArtifactRegistryConfigurationError",
    "ArtifactRequestValidationError",
    "UnsupportedArtifactTypeError",
    "UnsupportedArtifactVersionError",
    "ArtifactExpectedModelMismatchError",
    "ArtifactNotFoundError",
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactTooLargeError",
    "ArtifactCapacityExceededError",
    "ArtifactRepositoryDependencyError",
    "ArtifactRepositoryUsageError",
    "ExecutionBudgetError",
    "ExecutionBudgetPlanValidationError",
    "ExecutionBudgetRequestValidationError",
    "BudgetKeyNotConfiguredError",
    "BudgetOperationScopeMismatchError",
    "BudgetConsumeConflictError",
    "ExecutionBudgetCapacityExceededError",
    "ExecutionBudgetManagerUsageError",
    "WorkflowReplaySafetyError",
    "OperationResultIndeterminateError",
    "SafeWorkflowEventError",
    "SafeWorkflowEventRegistryConfigurationError",
    "SafeWorkflowEventValidationError",
    "UnsupportedWorkflowEventTypeError",
    "EventIdentityConflictError",
    "EventSequenceConflictError",
    "EventSinkContractError",
)


def test_public_api_exact_name_order_and_identity() -> None:
    assert tuple(root.__all__) == EXPECTED_PUBLIC_NAMES
    assert len(root.__all__) == len(set(root.__all__))
    assert set(EXPECTED_OBJECTS) == set(EXPECTED_PUBLIC_NAMES) - set(ERROR_NAMES)
    for name, expected in EXPECTED_OBJECTS.items():
        assert getattr(root, name) is expected

    for name in ERROR_NAMES:
        expected = getattr(errors, name)
        assert getattr(root, name) is expected
        assert issubclass(expected, errors.WorkflowContractsError)
        assert str(expected()) == name


def test_public_enums_registrations_limits_and_dto_fields() -> None:
    assert tuple(item.value for item in root.WorkflowPhase) == (
        "requirements",
        "hotel_selection",
        "candidate_research",
        "itinerary_planning",
        "validation",
        "budget_evaluation",
        "output_formatting",
    )
    assert tuple(item.value for item in root.DeliveryStatus) == (
        "in_progress",
        "awaiting_user",
        "demo_ready",
        "degraded",
        "failed",
        "cancelled",
    )
    assert tuple(item.value for item in root.TerminalCategory) == (
        "input",
        "configuration",
        "provider",
        "tool",
        "validation",
        "budget",
        "internal",
    )
    assert tuple(item.value for item in root.FieldRequirement) == (
        "required",
        "optional",
        "forbidden",
    )

    assert tuple(item.terminal_code for item in root.BASE_TERMINAL_CODE_REGISTRATIONS) == (
        "workflow_completed",
        "cancelled_by_user",
    )
    assert root.DEMO_ARTIFACT_REPOSITORY_LIMITS.model_dump() == {
        "max_artifact_bytes": 1_048_576,
        "max_artifacts": 2_048,
        "max_total_content_bytes": 67_108_864,
    }
    assert root.DEMO_EXECUTION_BUDGET_MANAGER_LIMITS.model_dump() == {
        "max_operation_records": 4_096,
    }
    assert tuple(item.event_type for item in root.STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS) == (
        "workflow.started",
        "workflow.resumed",
        "workflow.replayed",
        "workflow.terminal",
        "artifact.created",
        "artifact.reused",
        "budget.consumed",
        "budget.exceeded",
    )
    assert root.STANDARD_EVENT_DELIVERY_FAILURE_CODES == (
        "sink_unavailable",
        "sink_timeout",
        "sink_rejected",
        "sink_capacity_exceeded",
    )

    assert tuple(root.OperationId.model_fields) == ("run_id", "phase", "logical_step")
    assert tuple(root.ArtifactRef.model_fields) == (
        "artifact_id",
        "artifact_type",
        "schema_version",
        "content_hash",
        "operation_id",
        "created_at",
        "size_bytes",
    )
    assert tuple(root.ArtifactWriteRequest.model_fields) == (
        "operation_id",
        "artifact_type",
        "schema_version",
        "content",
    )
    assert tuple(root.SafeWorkflowEvent.model_fields) == (
        "schema_version",
        "event_id",
        "sequence_number",
        "occurred_at",
        "run_id",
        "trace_id",
        "operation_id",
        "phase",
        "event_type",
        "counter_deltas",
        "duration_ms",
        "terminal_code",
    )
    assert tuple(root.RunSummary.model_fields) == (
        "schema_version",
        "run_id",
        "trace_id",
        "terminal_code",
        "duration_ms",
        "cache_hits",
        "event_delivery_failures",
        "counters",
        "budget_snapshots",
    )


def test_public_protocol_signatures_are_exact() -> None:
    put = signature(root.ArtifactRepository.put)
    lookup = signature(root.ArtifactRepository.lookup)
    get = signature(root.ArtifactRepository.get)
    assert iscoroutinefunction(root.ArtifactRepository.put)
    assert iscoroutinefunction(root.ArtifactRepository.lookup)
    assert iscoroutinefunction(root.ArtifactRepository.get)
    assert list(put.parameters) == ["self", "request"]
    assert list(lookup.parameters) == ["self", "operation_id"]
    assert lookup.parameters["operation_id"].kind.name == "KEYWORD_ONLY"
    assert list(get.parameters) == ["self", "ref", "expected_model"]
    assert get.parameters["expected_model"].kind.name == "KEYWORD_ONLY"

    consume = signature(root.ExecutionBudgetManager.consume)
    snapshot = signature(root.ExecutionBudgetManager.get_snapshot)
    all_snapshots = signature(root.ExecutionBudgetManager.get_all_snapshots)
    assert iscoroutinefunction(root.ExecutionBudgetManager.consume)
    assert iscoroutinefunction(root.ExecutionBudgetManager.get_snapshot)
    assert iscoroutinefunction(root.ExecutionBudgetManager.get_all_snapshots)
    assert list(consume.parameters) == ["self", "request"]
    assert list(snapshot.parameters) == ["self", "key"]
    assert snapshot.parameters["key"].kind.name == "KEYWORD_ONLY"
    assert list(all_snapshots.parameters) == ["self"]

    emit = signature(root.SafeWorkflowEventSink.emit)
    assert iscoroutinefunction(root.SafeWorkflowEventSink.emit)
    assert list(emit.parameters) == ["self", "event"]
    assert iscoroutinefunction(root.NoOpSafeWorkflowEventSink.emit)
    assert list(signature(root.NoOpSafeWorkflowEventSink.emit).parameters) == [
        "self",
        "event",
    ]


def test_private_names_and_import_side_effects_are_not_public() -> None:
    forbidden_names = (
        "PreparedArtifact",
        "_canonical_json_bytes",
        "_content_hash",
        "_ArtifactRecord",
        "_BudgetOperationRecord",
        "_lock",
        "RecordingSafeWorkflowEventSink",
        "StrictSampleArtifact",
        "TypeAdapter",
    )
    assert all(name not in root.__all__ for name in forbidden_names)
    assert all(not hasattr(root, name) for name in forbidden_names)

    assert artifacts.__dict__.keys() >= {"__doc__"}
    assert budgets.__dict__.keys() >= {"__doc__"}
    assert events.__dict__.keys() >= {"__doc__"}
    assert not any(
        isinstance(value, (artifact_memory.InMemoryArtifactRepository,
                           budget_memory.InMemoryExecutionBudgetManager,
                           event_sink.NoOpSafeWorkflowEventSink,
                           asyncio.Lock))
        for value in root.__dict__.values()
    )
