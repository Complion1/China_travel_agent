"""Zero-argument errors owned by the requirement subgraph."""


class RequirementSubgraphError(Exception):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__()

    def __str__(self) -> str:
        return type(self).__name__


class RequirementSubgraphConfigurationError(RequirementSubgraphError):
    """A coordinator dependency or binding is invalid."""


class RequirementSubgraphInputError(RequirementSubgraphError):
    """An input does not satisfy the public boundary contract."""


class RequirementSubgraphResumeRejectedError(RequirementSubgraphInputError):
    """A resume is not valid for the current pause."""


class RequirementSubgraphStateError(RequirementSubgraphError):
    """A state, result, or protocol invariant was violated."""


class RequirementSubgraphRunFaultedError(RequirementSubgraphError):
    """The run has been permanently faulted."""


class RequirementSubgraphCapacityExceededError(RequirementSubgraphError):
    """The bounded coordinator has no admission slot."""
