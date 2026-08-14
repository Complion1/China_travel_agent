"""Workflow session state, terminal registrations, and the state JSON codec."""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    model_validator,
)

from app.requirements import (
    RequirementDomainOutcome,
    RequirementDomainState,
    RequirementDomainTransition,
)
from app.workflow_contracts.artifacts.models import ArtifactRef
from app.workflow_contracts.errors import (
    TerminalRegistryConfigurationError,
    TerminalStateMismatchError,
    UnsupportedTerminalCodeError,
    UnsupportedWorkflowStateVersionError,
    WorkflowStateValidationError,
)
from app.workflow_contracts.identifiers import (
    FieldRequirement,
    TerminalCode,
    UserActionCode,
    WorkflowPhase,
)


_REQUIREMENT_STATE_ADAPTER = TypeAdapter(RequirementDomainState)
_REQUIREMENT_OUTCOME_ADAPTER = TypeAdapter(RequirementDomainOutcome)


def _python_tree(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(
            mode="python",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
    return value


def _fresh_requirement_state(
    value: object,
    *,
    json_input: bool = False,
) -> RequirementDomainState:
    tree = _python_tree(value)
    if json_input:
        first = _REQUIREMENT_STATE_ADAPTER.validate_json(
            json.dumps(
                tree,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
            strict=True,
        )
    else:
        first = _REQUIREMENT_STATE_ADAPTER.validate_python(tree, strict=True)
    json_tree = first.model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    payload = json.dumps(
        json_tree,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return _REQUIREMENT_STATE_ADAPTER.validate_json(payload, strict=True)


def _fresh_requirement_outcome(
    value: object,
    *,
    json_input: bool = False,
) -> RequirementDomainOutcome:
    tree = _python_tree(value)
    if json_input:
        first = _REQUIREMENT_OUTCOME_ADAPTER.validate_json(
            json.dumps(
                tree,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
            strict=True,
        )
    else:
        first = _REQUIREMENT_OUTCOME_ADAPTER.validate_python(tree, strict=True)
    json_tree = first.model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    payload = json.dumps(
        json_tree,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return _REQUIREMENT_OUTCOME_ADAPTER.validate_json(payload, strict=True)


class DeliveryStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    AWAITING_USER = "awaiting_user"
    DEMO_READY = "demo_ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TerminalCategory(str, Enum):
    INPUT = "input"
    CONFIGURATION = "configuration"
    PROVIDER = "provider"
    TOOL = "tool"
    VALIDATION = "validation"
    BUDGET = "budget"
    INTERNAL = "internal"


class TerminalOutcome(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"]
    terminal_code: TerminalCode
    category: TerminalCategory
    recoverable: bool
    user_action_code: UserActionCode | None
    trace_id: UUID

    @model_validator(mode="after")
    def validate_recovery_action(self) -> "TerminalOutcome":
        if not self.recoverable and self.user_action_code is not None:
            raise ValueError("nonrecoverable outcome cannot have a user action")
        return self


_NONTERMINAL_STATUSES = frozenset(
    {DeliveryStatus.IN_PROGRESS, DeliveryStatus.AWAITING_USER}
)
_TERMINAL_STATUSES = frozenset(
    {
        DeliveryStatus.DEMO_READY,
        DeliveryStatus.DEGRADED,
        DeliveryStatus.FAILED,
        DeliveryStatus.CANCELLED,
    }
)
_PHASE_ORDER = {phase: index for index, phase in enumerate(WorkflowPhase)}


class TravelSessionState(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["2.0"]
    run_id: UUID
    trace_id: UUID
    phase: WorkflowPhase
    delivery_status: DeliveryStatus
    terminal_code: TerminalCode | None
    terminal_outcome: TerminalOutcome | None
    requirement_state: RequirementDomainState = Field(repr=False)
    requirement_outcome: RequirementDomainOutcome | None = Field(repr=False)
    artifact_refs: tuple[ArtifactRef, ...]

    @model_validator(mode="before")
    @classmethod
    def strict_requirement_preflight(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "requirement_state" in payload:
            payload["requirement_state"] = _fresh_requirement_state(
                payload["requirement_state"],
                json_input=info.mode == "json",
            )
        if payload.get("requirement_outcome") is not None:
            payload["requirement_outcome"] = _fresh_requirement_outcome(
                payload["requirement_outcome"],
                json_input=info.mode == "json",
            )
        if info.mode == "json" and isinstance(payload.get("artifact_refs"), list):
            payload["artifact_refs"] = tuple(payload["artifact_refs"])
        return payload

    @model_validator(mode="after")
    def validate_state_invariants(self) -> "TravelSessionState":
        if self.requirement_outcome is not None:
            transition = RequirementDomainTransition(
                state=self.requirement_state,
                outcome=self.requirement_outcome,
            )
            object.__setattr__(
                self,
                "requirement_state",
                _fresh_requirement_state(transition.state),
            )
            object.__setattr__(
                self,
                "requirement_outcome",
                _fresh_requirement_outcome(transition.outcome),
            )
        elif self.requirement_state != RequirementDomainState():
            raise ValueError("outcome is required after initial state")

        if self.delivery_status in _NONTERMINAL_STATUSES:
            if self.terminal_code is not None or self.terminal_outcome is not None:
                raise ValueError("nonterminal state cannot have terminal fields")
        elif self.delivery_status is DeliveryStatus.DEMO_READY:
            if self.terminal_code is None or self.terminal_outcome is not None:
                raise ValueError("demo_ready state has invalid terminal fields")
        elif self.delivery_status in _TERMINAL_STATUSES:
            if self.terminal_code is None or self.terminal_outcome is None:
                raise ValueError("terminal state requires terminal fields")

        if self.requirement_outcome is None and (
            self.phase is not WorkflowPhase.REQUIREMENTS
            or self.delivery_status is not DeliveryStatus.IN_PROGRESS
            or self.terminal_code is not None
            or self.terminal_outcome is not None
            or self.artifact_refs
        ):
            raise ValueError("empty requirement outcome is only valid for initial state")

        if self.terminal_outcome is not None:
            if self.terminal_outcome.terminal_code != self.terminal_code:
                raise ValueError("terminal code does not match outcome")
            if self.terminal_outcome.trace_id != self.trace_id:
                raise ValueError("trace id does not match outcome")

        artifact_ids = [ref.artifact_id for ref in self.artifact_refs]
        operation_ids = [ref.operation_id for ref in self.artifact_refs]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("duplicate artifact id")
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("duplicate operation id")
        if any(ref.operation_id.run_id != self.run_id for ref in self.artifact_refs):
            raise ValueError("artifact ref run mismatch")
        sort_keys = [
            (_PHASE_ORDER[ref.operation_id.phase], ref.operation_id.logical_step)
            for ref in self.artifact_refs
        ]
        if sort_keys != sorted(sort_keys):
            raise ValueError("artifact refs are not canonically sorted")
        return self


class TerminalCodeRegistration(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    terminal_code: TerminalCode
    delivery_status: DeliveryStatus
    outcome_requirement: FieldRequirement
    category: TerminalCategory | None
    recoverable: bool | None
    user_action_codes: tuple[UserActionCode, ...]

    @model_validator(mode="after")
    def validate_registration_semantics(self) -> "TerminalCodeRegistration":
        if self.delivery_status not in _TERMINAL_STATUSES:
            raise ValueError("registration must use a terminal delivery status")
        action_codes = self.user_action_codes
        if len(action_codes) != len(set(action_codes)) or action_codes != tuple(
            sorted(action_codes)
        ):
            raise ValueError("user action codes must be unique and sorted")
        if self.outcome_requirement is FieldRequirement.FORBIDDEN:
            if (
                self.category is not None
                or self.recoverable is not None
                or action_codes
            ):
                raise ValueError("forbidden outcome has semantic fields")
        elif self.outcome_requirement is FieldRequirement.REQUIRED:
            if self.category is None or self.recoverable is None:
                raise ValueError("required outcome lacks semantic fields")
            if not self.recoverable and action_codes:
                raise ValueError("nonrecoverable registration has actions")
        else:
            raise ValueError("terminal outcome must be required or forbidden")
        return self


_STATE_ADAPTER = TypeAdapter(TravelSessionState)
_REGISTRATION_ADAPTER = TypeAdapter(TerminalCodeRegistration)
TModel = TypeVar("TModel", bound=BaseModel)


def _strict_copy(
    value: TModel,
    adapter: TypeAdapter[TModel],
    error_type: type[Exception],
) -> TModel:
    try:
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
        payload = json.dumps(
            json_tree,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return adapter.validate_json(payload, strict=True)
    except Exception:
        raise error_type() from None


def _json_bytes(value: TravelSessionState) -> bytes:
    tree = value.model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    return json.dumps(
        tree,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class _ParsedObject(dict[str, object]):
    __slots__ = ("duplicates",)

    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        super().__init__()
        seen: set[str] = set()
        duplicates: list[str] = []
        for key, value in pairs:
            if key in seen:
                duplicates.append(key)
            seen.add(key)
            self[key] = value
        self.duplicates = tuple(duplicates)


_INVALID_NUMBER = object()


def _has_duplicate_key(value: object) -> bool:
    if isinstance(value, _ParsedObject):
        if value.duplicates:
            return True
        return any(_has_duplicate_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_duplicate_key(item) for item in value)
    return False


def _has_invalid_number(value: object) -> bool:
    if value is _INVALID_NUMBER:
        return True
    if isinstance(value, _ParsedObject):
        return any(_has_invalid_number(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_invalid_number(item) for item in value)
    return False


def serialize_travel_session_state(state: TravelSessionState) -> bytes:
    try:
        fresh_state = _strict_copy(
            state,
            _STATE_ADAPTER,
            WorkflowStateValidationError,
        )
        candidate = _json_bytes(fresh_state)
        reparsed = parse_travel_session_state_json(candidate)
        return _json_bytes(reparsed)
    except WorkflowStateValidationError:
        raise
    except Exception:
        raise WorkflowStateValidationError() from None


def parse_travel_session_state_json(data: bytes) -> TravelSessionState:
    if type(data) is not bytes:
        raise WorkflowStateValidationError()
    try:
        text = data.decode("utf-8")
        if text.startswith("\ufeff"):
            raise ValueError("BOM is not allowed")
        tree = json.loads(
            text,
            object_pairs_hook=_ParsedObject,
            parse_constant=lambda _value: _INVALID_NUMBER,
        )
    except Exception:
        raise WorkflowStateValidationError() from None

    if not isinstance(tree, _ParsedObject):
        raise WorkflowStateValidationError()
    if "schema_version" in tree.duplicates:
        raise WorkflowStateValidationError()

    version = tree.get("schema_version")
    if type(version) is not str or version != "2.0":
        raise UnsupportedWorkflowStateVersionError()
    if _has_duplicate_key(tree) or _has_invalid_number(tree):
        raise WorkflowStateValidationError()

    try:
        checked_json = json.dumps(
            tree,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return _STATE_ADAPTER.validate_json(checked_json, strict=True)
    except Exception:
        raise WorkflowStateValidationError() from None


class TerminalCodeRegistry:
    __slots__ = ("_registrations", "_by_code")

    def __init__(self, registrations: tuple[TerminalCodeRegistration, ...]) -> None:
        try:
            copied = tuple(
                _strict_copy(
                    registration,
                    _REGISTRATION_ADAPTER,
                    TerminalRegistryConfigurationError,
                )
                for registration in registrations
            )
            codes = tuple(registration.terminal_code for registration in copied)
            if len(codes) != len(set(codes)):
                raise TerminalRegistryConfigurationError()
            self._registrations = copied
            self._by_code = {
                registration.terminal_code: registration for registration in copied
            }
        except TerminalRegistryConfigurationError:
            raise
        except Exception:
            raise TerminalRegistryConfigurationError() from None

    def resolve(self, terminal_code: TerminalCode) -> TerminalCodeRegistration:
        try:
            registration = self._by_code[terminal_code]
        except (KeyError, TypeError):
            raise UnsupportedTerminalCodeError() from None
        return registration

    def validate_state(self, state: TravelSessionState) -> None:
        fresh_state = _strict_copy(
            state,
            _STATE_ADAPTER,
            TerminalStateMismatchError,
        )
        if fresh_state.delivery_status in _NONTERMINAL_STATUSES:
            return
        if fresh_state.delivery_status is DeliveryStatus.DEMO_READY:
            if fresh_state.terminal_code is None:
                raise TerminalStateMismatchError()
            registration = self.resolve(fresh_state.terminal_code)
            if (
                registration.delivery_status is not DeliveryStatus.DEMO_READY
                or registration.outcome_requirement is not FieldRequirement.FORBIDDEN
            ):
                raise TerminalStateMismatchError()
            return
        if fresh_state.terminal_code is None or fresh_state.terminal_outcome is None:
            raise TerminalStateMismatchError()

        registration = self.resolve(fresh_state.terminal_code)
        outcome = fresh_state.terminal_outcome
        if registration.delivery_status is not fresh_state.delivery_status:
            raise TerminalStateMismatchError()
        if registration.outcome_requirement is FieldRequirement.FORBIDDEN:
            if outcome is not None:
                raise TerminalStateMismatchError()
            return
        if outcome is None:
            raise TerminalStateMismatchError()
        if outcome.category is not registration.category:
            raise TerminalStateMismatchError()
        if outcome.recoverable is not registration.recoverable:
            raise TerminalStateMismatchError()
        if (
            outcome.user_action_code is not None
            and outcome.user_action_code not in registration.user_action_codes
        ):
            raise TerminalStateMismatchError()


BASE_TERMINAL_CODE_REGISTRATIONS = (
    TerminalCodeRegistration(
        terminal_code="workflow_completed",
        delivery_status=DeliveryStatus.DEMO_READY,
        outcome_requirement=FieldRequirement.FORBIDDEN,
        category=None,
        recoverable=None,
        user_action_codes=(),
    ),
    TerminalCodeRegistration(
        terminal_code="cancelled_by_user",
        delivery_status=DeliveryStatus.CANCELLED,
        outcome_requirement=FieldRequirement.REQUIRED,
        category=TerminalCategory.INPUT,
        recoverable=False,
        user_action_codes=(),
    ),
)
