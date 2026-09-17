"""Strict public DTOs for the requirement subgraph boundary."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from app.requirements import (
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainOutcome,
    RequirementDomainState,
    RequirementExtractionFailed,
    RequirementInvalidated,
    RequirementTurnInput,
    ConfirmedTravelRequirement,
)
from app.workflow_contracts import (
    BudgetExceeded as WorkflowBudgetExceeded,
    ContentHash,
    DeliveryStatus,
    ExecutionBudgetKey,
    ExecutionBudgetScope,
    OperationId,
    TravelSessionState,
    WorkflowPhase,
)


_HEX32 = r"^[0-9a-f]{32}$"
_CLARIFICATION_STEP = re.compile(r"^clarification_round_[0-9a-f]{32}$")

PauseToken = Annotated[str, StringConstraints(pattern=_HEX32)]

_PAUSE_TOKEN_DIGEST_DOMAIN = b"requirement-subgraph-pause-token-v1\x00"


def _fresh_model(model_type: object, value: BaseModel) -> object:
    """Perform the required Python-tree then JSON fresh validation."""

    adapter = TypeAdapter(model_type)
    tree = value.model_dump(
        mode="python",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    first = adapter.validate_python(tree, strict=True)
    json_tree = first.model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    encoded = json.dumps(
        json_tree,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return adapter.validate_json(encoded, strict=True)


def _canonical_model_bytes(model_type: object, value: BaseModel) -> bytes:
    fresh = _fresh_model(model_type, value)
    assert isinstance(fresh, BaseModel)
    return json.dumps(
        fresh.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _state_digest(state: TravelSessionState) -> ContentHash:
    return ContentHash(
        "sha256:"
        + hashlib.sha256(_canonical_model_bytes(TravelSessionState, state)).hexdigest()
    )


def _pause_token_digest(run_id: UUID, pause_token: str) -> ContentHash:
    payload = (
        _PAUSE_TOKEN_DIGEST_DOMAIN
        + str(run_id).encode("ascii")
        + b"\x00"
        + pause_token.encode("ascii")
    )
    return ContentHash("sha256:" + hashlib.sha256(payload).hexdigest())


class RequirementClarificationInterrupt(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    interrupt_type: Literal["clarification"] = "clarification"
    message: str


class RequirementConfirmationInterrupt(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    interrupt_type: Literal["confirmation"] = "confirmation"
    source_outcome_type: Literal["need_confirmation", "invalidated"]
    summary: str


RequirementInterrupt: TypeAlias = Annotated[
    RequirementClarificationInterrupt | RequirementConfirmationInterrupt,
    Field(discriminator="interrupt_type"),
]


class RequirementClarificationResume(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    resume_type: Literal["clarification"] = "clarification"
    turn: RequirementTurnInput = Field(repr=False)

    @model_validator(mode="after")
    def fresh_turn(self) -> "RequirementClarificationResume":
        object.__setattr__(
            self,
            "turn",
            _fresh_model(RequirementTurnInput, self.turn),
        )
        return self


class RequirementConfirmationResume(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    resume_type: Literal["confirmation"] = "confirmation"
    action: RequirementConfirmation = Field(repr=False)

    @model_validator(mode="after")
    def fresh_action(self) -> "RequirementConfirmationResume":
        object.__setattr__(
            self,
            "action",
            _fresh_model(RequirementConfirmation, self.action),
        )
        return self


RequirementResume: TypeAlias = Annotated[
    RequirementClarificationResume | RequirementConfirmationResume,
    Field(discriminator="resume_type"),
]


class RequirementPauseEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    pause_token: PauseToken = Field(repr=False)
    interrupt: RequirementInterrupt

    @model_validator(mode="after")
    def fresh_interrupt(self) -> "RequirementPauseEnvelope":
        object.__setattr__(
            self,
            "interrupt",
            _fresh_model(RequirementInterrupt, self.interrupt),
        )
        return self


class RequirementResumeEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    pause_token: PauseToken = Field(repr=False)
    resume: RequirementResume = Field(repr=False)

    @model_validator(mode="after")
    def fresh_resume(self) -> "RequirementResumeEnvelope":
        object.__setattr__(
            self,
            "resume",
            _fresh_model(RequirementResume, self.resume),
        )
        return self


class RequirementSubgraphDurablePause(BaseModel):
    """Safe evidence required to rehydrate one embedded paused operation."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    trace_id: UUID
    state_digest: ContentHash
    pause_token_digest: ContentHash = Field(repr=False)
    operation_nonce: PauseToken
    interrupt: RequirementInterrupt

    @model_validator(mode="after")
    def fresh_interrupt(self) -> "RequirementSubgraphDurablePause":
        object.__setattr__(
            self,
            "interrupt",
            _fresh_model(RequirementInterrupt, self.interrupt),
        )
        return self


def _requirements_state_base(state: TravelSessionState) -> None:
    if (
        state.phase is not WorkflowPhase.REQUIREMENTS
        or state.terminal_code is not None
        or state.terminal_outcome is not None
    ):
        raise ValueError("invalid requirement subgraph state envelope")


class RequirementSubgraphPaused(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    result_type: Literal["paused"] = "paused"
    state: TravelSessionState
    pause: RequirementPauseEnvelope
    durable_pause: RequirementSubgraphDurablePause

    @model_validator(mode="after")
    def validate_pause_contract(self) -> "RequirementSubgraphPaused":
        object.__setattr__(self, "state", _fresh_model(TravelSessionState, self.state))
        object.__setattr__(
            self,
            "pause",
            _fresh_model(RequirementPauseEnvelope, self.pause),
        )
        object.__setattr__(
            self,
            "durable_pause",
            _fresh_model(RequirementSubgraphDurablePause, self.durable_pause),
        )
        _requirements_state_base(self.state)
        if self.state.delivery_status is not DeliveryStatus.AWAITING_USER:
            raise ValueError("paused result requires awaiting_user")
        outcome = self.state.requirement_outcome
        interrupt = self.pause.interrupt
        if type(outcome) is NeedClarification:
            if (
                type(interrupt) is not RequirementClarificationInterrupt
                or interrupt.message != outcome.message
            ):
                raise ValueError("clarification interrupt mismatch")
        elif type(outcome) is NeedConfirmation:
            if (
                type(interrupt) is not RequirementConfirmationInterrupt
                or interrupt.source_outcome_type != "need_confirmation"
                or interrupt.summary != outcome.summary
            ):
                raise ValueError("confirmation interrupt mismatch")
        elif type(outcome) is RequirementInvalidated:
            if (
                type(interrupt) is not RequirementConfirmationInterrupt
                or interrupt.source_outcome_type != "invalidated"
                or interrupt.summary != outcome.summary
            ):
                raise ValueError("invalidated interrupt mismatch")
        else:
            raise ValueError("paused result requires a waiting outcome")
        durable = self.durable_pause
        if (
            durable.run_id != self.state.run_id
            or durable.trace_id != self.state.trace_id
            or durable.state_digest != _state_digest(self.state)
            or durable.pause_token_digest
            != _pause_token_digest(self.state.run_id, self.pause.pause_token)
            or durable.operation_nonce == self.pause.pause_token
            or durable.interrupt != interrupt
        ):
            raise ValueError("durable pause mismatch")
        return self


class RequirementSubgraphConfirmed(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    result_type: Literal["confirmed"] = "confirmed"
    state: TravelSessionState

    @model_validator(mode="after")
    def validate_confirmed_contract(self) -> "RequirementSubgraphConfirmed":
        object.__setattr__(self, "state", _fresh_model(TravelSessionState, self.state))
        _requirements_state_base(self.state)
        if (
            self.state.delivery_status.value != "in_progress"
            or type(self.state.requirement_outcome) is not ConfirmedTravelRequirement
        ):
            raise ValueError("confirmed result mismatch")
        return self


class RequirementSubgraphDomainFailed(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    result_type: Literal["domain_failed"] = "domain_failed"
    state: TravelSessionState

    @model_validator(mode="after")
    def validate_domain_failed_contract(self) -> "RequirementSubgraphDomainFailed":
        object.__setattr__(self, "state", _fresh_model(TravelSessionState, self.state))
        _requirements_state_base(self.state)
        if (
            self.state.delivery_status.value != "in_progress"
            or type(self.state.requirement_outcome) is not RequirementExtractionFailed
        ):
            raise ValueError("domain failed result mismatch")
        return self


class RequirementSubgraphCoordinatorLimits(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    max_runs: int = Field(ge=1, le=2**53 - 1)


REQUIREMENTS_CLARIFICATION_ROUND_KEY = ExecutionBudgetKey(
    scope=ExecutionBudgetScope.PHASE,
    name="requirements.clarification_round",
    phase=WorkflowPhase.REQUIREMENTS,
)
DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS = RequirementSubgraphCoordinatorLimits(
    max_runs=256,
)
DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT = 6


class RequirementSubgraphBudgetExceeded(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    result_type: Literal["budget_exceeded"] = "budget_exceeded"
    state: TravelSessionState
    budget_outcome: WorkflowBudgetExceeded

    @model_validator(mode="after")
    def validate_budget_contract(self) -> "RequirementSubgraphBudgetExceeded":
        object.__setattr__(self, "state", _fresh_model(TravelSessionState, self.state))
        object.__setattr__(
            self,
            "budget_outcome",
            _fresh_model(WorkflowBudgetExceeded, self.budget_outcome),
        )
        _requirements_state_base(self.state)
        outcome = self.state.requirement_outcome
        budget = self.budget_outcome
        if (
            self.state.delivery_status.value != "in_progress"
            or type(outcome)
            not in (NeedClarification, NeedConfirmation, RequirementInvalidated)
            or type(budget) is not WorkflowBudgetExceeded
            or budget.disposition != "rejected"
            or budget.amount != 1
            or budget.key != REQUIREMENTS_CLARIFICATION_ROUND_KEY
            or budget.operation_id.run_id != self.state.run_id
            or budget.operation_id.phase is not WorkflowPhase.REQUIREMENTS
            or _CLARIFICATION_STEP.fullmatch(budget.operation_id.logical_step) is None
            or budget.snapshot.run_id != self.state.run_id
            or budget.snapshot.key != budget.key
            or budget.snapshot.key != REQUIREMENTS_CLARIFICATION_ROUND_KEY
        ):
            raise ValueError("budget exceeded result mismatch")
        return self


RequirementSubgraphResult: TypeAlias = Annotated[
    RequirementSubgraphPaused
    | RequirementSubgraphConfirmed
    | RequirementSubgraphDomainFailed
    | RequirementSubgraphBudgetExceeded,
    Field(discriminator="result_type"),
]


__all__ = [
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
    "DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT",
    "REQUIREMENTS_CLARIFICATION_ROUND_KEY",
    "PauseToken",
]
