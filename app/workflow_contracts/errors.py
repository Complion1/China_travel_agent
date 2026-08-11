"""Public marker exceptions for workflow contracts."""


class WorkflowContractsError(Exception):
    """Base class for all workflow contract errors."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__()

    def __str__(self) -> str:
        return type(self).__name__


class WorkflowStateContractError(WorkflowContractsError):
    """Base class for workflow state contract errors."""


class UnsupportedWorkflowStateVersionError(WorkflowStateContractError):
    """The workflow state schema version is unsupported."""


class WorkflowStateValidationError(WorkflowStateContractError):
    """The workflow state is invalid."""


class TerminalContractError(WorkflowContractsError):
    """Base class for terminal contract errors."""


class TerminalRegistryConfigurationError(TerminalContractError):
    """The terminal registry configuration is invalid."""


class UnsupportedTerminalCodeError(TerminalContractError):
    """The terminal code is not registered."""


class TerminalStateMismatchError(TerminalContractError):
    """The state does not match its terminal registration."""


class ArtifactRepositoryError(WorkflowContractsError):
    """Base class for artifact repository errors."""


class ArtifactRegistryConfigurationError(ArtifactRepositoryError):
    """The artifact schema registry configuration is invalid."""


class ArtifactRequestValidationError(ArtifactRepositoryError):
    """An artifact request failed contract validation."""


class UnsupportedArtifactTypeError(ArtifactRepositoryError):
    """The artifact type is not registered."""


class UnsupportedArtifactVersionError(ArtifactRepositoryError):
    """The artifact schema version is not registered."""


class ArtifactExpectedModelMismatchError(ArtifactRepositoryError):
    """The expected artifact model does not match the registered model."""


class ArtifactNotFoundError(ArtifactRepositoryError):
    """The requested artifact was not found."""


class ArtifactConflictError(ArtifactRepositoryError):
    """An operation attempted to write conflicting artifact content."""


class ArtifactIntegrityError(ArtifactRepositoryError):
    """An artifact failed integrity validation."""


class ArtifactTooLargeError(ArtifactRepositoryError):
    """An artifact exceeds the per-artifact size limit."""


class ArtifactCapacityExceededError(ArtifactRepositoryError):
    """The artifact repository capacity would be exceeded."""


class ArtifactRepositoryDependencyError(ArtifactRepositoryError):
    """An injected artifact repository dependency failed."""


class ArtifactRepositoryUsageError(ArtifactRepositoryError):
    """The artifact repository was used outside its contract."""


class ExecutionBudgetError(WorkflowContractsError):
    """Base class for execution budget errors."""


class ExecutionBudgetPlanValidationError(ExecutionBudgetError):
    """The execution budget plan is invalid."""


class ExecutionBudgetRequestValidationError(ExecutionBudgetError):
    """An execution budget request failed contract validation."""


class BudgetKeyNotConfiguredError(ExecutionBudgetError):
    """The budget key is not configured in the plan."""


class BudgetOperationScopeMismatchError(ExecutionBudgetError):
    """The operation does not match the budget scope."""


class BudgetConsumeConflictError(ExecutionBudgetError):
    """A repeated operation has conflicting budget parameters."""


class ExecutionBudgetCapacityExceededError(ExecutionBudgetError):
    """The execution budget operation record capacity is exhausted."""


class ExecutionBudgetManagerUsageError(ExecutionBudgetError):
    """The execution budget manager was used outside its contract."""


class WorkflowReplaySafetyError(WorkflowContractsError):
    """Base class for replay safety errors."""


class OperationResultIndeterminateError(WorkflowReplaySafetyError):
    """A replay cannot determine whether an operation already completed."""


class SafeWorkflowEventError(WorkflowContractsError):
    """Base class for safe workflow event errors."""


class SafeWorkflowEventRegistryConfigurationError(SafeWorkflowEventError):
    """The safe workflow event registry configuration is invalid."""


class SafeWorkflowEventValidationError(SafeWorkflowEventError):
    """A safe workflow event failed contract validation."""


class UnsupportedWorkflowEventTypeError(SafeWorkflowEventError):
    """The workflow event type is not registered."""


class EventIdentityConflictError(SafeWorkflowEventError):
    """A repeated event ID has conflicting content."""


class EventSequenceConflictError(SafeWorkflowEventError):
    """A run sequence number has conflicting event content."""


class EventSinkContractError(SafeWorkflowEventError):
    """A safe workflow event sink violated its contract."""
