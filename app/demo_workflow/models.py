"""Strict commands for the production demo root graph."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.requirements import RequirementDomainState, RequirementIntent, RequirementTurnInput, RequirementDomainService
from app.requirement_subgraph import RequirementResume, RequirementConfirmationResume, RequirementClarificationResume
from app.hotel_selection import HotelSelectionCommand, HotelSelectCommand, HotelDeclineCommand
from app.workflow_contracts import ArtifactRef
from app.workflow_contracts import ArtifactRepositoryLimits, TravelSessionState
from app.durable_execution import DurableExecutionLimits, DurableExecutionContract, DurableExecutionNodeCapabilities, DurableExecutionGraphCapabilities
from app.hotel_selection import HotelCandidateSelectionPort
from app.candidate_research import CandidateResearchPort
from app.itinerary_planning import ItineraryPlanningPort
from langgraph.graph import StateGraph

if TYPE_CHECKING:
    from .diagnostics import DemoDiagnostics
    from .commands import DemoCommandCodec
    from .plans import DemoBudgetPlanProvider


def fresh(value, expected):
    if isinstance(expected, type) and type(value) is not expected:
        raise ValueError("exact DTO required")
    adapter = TypeAdapter(expected)
    first = adapter.validate_python(value.model_dump(mode="python"), strict=True)
    return adapter.validate_json(first.model_dump_json(), strict=True)


class DemoStartCommand(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["1.0"]
    kind: Literal["start"]
    turn: RequirementTurnInput = Field(repr=False)

    @model_validator(mode="after")
    def validate_turn(self):
        turn = fresh(self.turn, RequirementTurnInput)
        if turn.intent is not RequirementIntent.INITIAL or turn.current != RequirementDomainState():
            raise ValueError("start requires initial requirements")
        object.__setattr__(self, "turn", turn)
        return self


class DemoRequirementCommand(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["1.0"]
    kind: Literal["requirement"]
    resume: RequirementResume = Field(repr=False)
    map_requested: bool

    @model_validator(mode="after")
    def validate_resume(self):
        if type(self.resume) not in {RequirementConfirmationResume, RequirementClarificationResume}:
            raise ValueError("exact resume type required")
        resume = fresh(self.resume, type(self.resume))
        if self.map_requested and not (
            type(resume) is RequirementConfirmationResume and resume.action.decision == "accept"
        ):
            raise ValueError("map choice requires final acceptance")
        object.__setattr__(self, "resume", resume)
        return self


class DemoHotelCommand(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["1.0"]
    kind: Literal["hotel"]
    candidate_set_ref: ArtifactRef = Field(repr=False)
    action: HotelSelectionCommand = Field(repr=False)

    @model_validator(mode="after")
    def validate_action(self):
        if type(self.action) not in {HotelSelectCommand, HotelDeclineCommand}:
            raise ValueError("exact selection type required")
        object.__setattr__(self, "candidate_set_ref", fresh(self.candidate_set_ref, ArtifactRef))
        object.__setattr__(self, "action", fresh(self.action, type(self.action)))
        return self


DemoResumeCommand: TypeAlias = Annotated[
    DemoRequirementCommand | DemoHotelCommand, Field(discriminator="kind")
]


@dataclass(frozen=True)
class DemoWorkflowDependencies:
    domain_service: RequirementDomainService
    hotel_factory: Callable[[DurableExecutionNodeCapabilities], HotelCandidateSelectionPort]
    research_factory: Callable[[DurableExecutionNodeCapabilities], CandidateResearchPort]
    planning_factory: Callable[[DurableExecutionNodeCapabilities], ItineraryPlanningPort]
    diagnostics: DemoDiagnostics
    durable_limits: DurableExecutionLimits
    artifact_limits: ArtifactRepositoryLimits

    def __post_init__(self):
        object.__setattr__(self, "durable_limits", fresh(self.durable_limits, DurableExecutionLimits))
        object.__setattr__(self, "artifact_limits", fresh(self.artifact_limits, ArtifactRepositoryLimits))


@dataclass(frozen=True)
class DemoWorkflowDefinition:
    contract: DurableExecutionContract
    command_codec: DemoCommandCodec
    budget_plan_provider: DemoBudgetPlanProvider
    graph_factory: Callable[[DurableExecutionGraphCapabilities], StateGraph]
    cancellation_state_factory: Callable[[TravelSessionState], TravelSessionState]
    release_run: Callable[[UUID], Awaitable[None]]
    aclose: Callable[[], Awaitable[None]]
    durable_limits: DurableExecutionLimits
    artifact_limits: ArtifactRepositoryLimits
