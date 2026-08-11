"""Workflow phases, constrained identifiers, and operation identity."""

from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints


class WorkflowPhase(str, Enum):
    REQUIREMENTS = "requirements"
    HOTEL_SELECTION = "hotel_selection"
    CANDIDATE_RESEARCH = "candidate_research"
    ITINERARY_PLANNING = "itinerary_planning"
    VALIDATION = "validation"
    BUDGET_EVALUATION = "budget_evaluation"
    OUTPUT_FORMATTING = "output_formatting"


class FieldRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


TerminalCode = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
UserActionCode = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
LogicalStep = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
ArtifactType = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=65,
        pattern=r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$",
    ),
]
ArtifactSchemaVersion = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=16,
        pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$",
    ),
]
ContentHash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ExecutionBudgetName = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=65,
        pattern=r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$",
    ),
]
WorkflowEventType = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=65,
        pattern=r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$",
    ),
]
SafeCounterName = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=65,
        pattern=r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$",
    ),
]
EventDeliveryFailureCode = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]


class OperationId(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    run_id: UUID
    phase: WorkflowPhase
    logical_step: LogicalStep

    def safe_display(self) -> str:
        return f"{self.run_id}:{self.phase.value}:{self.logical_step}"
