"""Registries for safe workflow event types and delivery failures."""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from app.workflow_contracts.errors import (
    EventSinkContractError,
    SafeWorkflowEventRegistryConfigurationError,
    SafeWorkflowEventValidationError,
    UnsupportedWorkflowEventTypeError,
)
from app.workflow_contracts.events.models import SafeWorkflowEvent
from app.workflow_contracts.identifiers import (
    EventDeliveryFailureCode,
    FieldRequirement,
    SafeCounterName,
    WorkflowEventType,
)


class SafeWorkflowEventRegistration(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    event_type: WorkflowEventType
    counter_names: tuple[SafeCounterName, ...]
    operation_id: FieldRequirement
    duration_ms: FieldRequirement
    terminal_code: FieldRequirement

    @model_validator(mode="after")
    def validate_counter_names(self) -> "SafeWorkflowEventRegistration":
        if len(self.counter_names) != len(set(self.counter_names)):
            raise ValueError("counter names must be unique")
        if self.counter_names != tuple(sorted(self.counter_names)):
            raise ValueError("counter names must be sorted")
        return self


TModel = TypeVar("TModel", bound=BaseModel)
_REGISTRATION_ADAPTER = TypeAdapter(SafeWorkflowEventRegistration)
_EVENT_ADAPTER = TypeAdapter(SafeWorkflowEvent)
_FAILURE_CODE_ADAPTER = TypeAdapter(EventDeliveryFailureCode)


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


_STANDARD_OCCURRENCE_EVENT_TYPES = frozenset(
    {
        "workflow.started",
        "workflow.resumed",
        "workflow.replayed",
        "workflow.terminal",
        "artifact.created",
        "artifact.reused",
        "budget.exceeded",
    }
)


class SafeWorkflowEventRegistry:
    __slots__ = ("_registrations", "_by_event_type")

    def __init__(self, registrations: tuple[SafeWorkflowEventRegistration, ...]) -> None:
        try:
            copied = tuple(
                _strict_copy(
                    registration,
                    _REGISTRATION_ADAPTER,
                    SafeWorkflowEventRegistryConfigurationError,
                )
                for registration in registrations
            )
            event_types = tuple(registration.event_type for registration in copied)
            if len(event_types) != len(set(event_types)):
                raise SafeWorkflowEventRegistryConfigurationError()
            self._registrations = copied
            self._by_event_type = {
                registration.event_type: registration for registration in copied
            }
        except SafeWorkflowEventRegistryConfigurationError:
            raise
        except Exception:
            raise SafeWorkflowEventRegistryConfigurationError() from None

    def resolve(
        self,
        event_type: WorkflowEventType,
    ) -> SafeWorkflowEventRegistration:
        try:
            return self._by_event_type[event_type]
        except (KeyError, TypeError):
            raise UnsupportedWorkflowEventTypeError() from None

    def validate(self, event: SafeWorkflowEvent) -> None:
        fresh_event = _strict_copy(
            event,
            _EVENT_ADAPTER,
            SafeWorkflowEventValidationError,
        )
        registration = self.resolve(fresh_event.event_type)
        actual_names = tuple(counter.name for counter in fresh_event.counter_deltas)
        if actual_names != registration.counter_names:
            raise SafeWorkflowEventValidationError()

        requirements = (
            (registration.operation_id, fresh_event.operation_id),
            (registration.duration_ms, fresh_event.duration_ms),
            (registration.terminal_code, fresh_event.terminal_code),
        )
        for requirement, value in requirements:
            if requirement is FieldRequirement.REQUIRED and value is None:
                raise SafeWorkflowEventValidationError()
            if requirement is FieldRequirement.FORBIDDEN and value is not None:
                raise SafeWorkflowEventValidationError()

        if fresh_event.event_type in _STANDARD_OCCURRENCE_EVENT_TYPES:
            if any(counter.delta != 1 for counter in fresh_event.counter_deltas):
                raise SafeWorkflowEventValidationError()


class EventDeliveryFailureCodeRegistry:
    __slots__ = ("_codes", "_code_set")

    def __init__(self, codes: tuple[EventDeliveryFailureCode, ...]) -> None:
        try:
            copied = tuple(
                _FAILURE_CODE_ADAPTER.validate_python(code, strict=True)
                for code in codes
            )
            if len(copied) != len(set(copied)):
                raise SafeWorkflowEventRegistryConfigurationError()
            self._codes = tuple(sorted(copied))
            self._code_set = frozenset(copied)
        except SafeWorkflowEventRegistryConfigurationError:
            raise
        except Exception:
            raise SafeWorkflowEventRegistryConfigurationError() from None

    def validate(self, code: EventDeliveryFailureCode) -> None:
        try:
            checked = _FAILURE_CODE_ADAPTER.validate_python(code, strict=True)
            if checked not in self._code_set:
                raise ValueError
        except Exception:
            raise EventSinkContractError() from None


STANDARD_SAFE_WORKFLOW_EVENT_REGISTRATIONS = (
    SafeWorkflowEventRegistration(
        event_type="workflow.started",
        counter_names=("workflow.started",),
        operation_id=FieldRequirement.FORBIDDEN,
        duration_ms=FieldRequirement.FORBIDDEN,
        terminal_code=FieldRequirement.FORBIDDEN,
    ),
    SafeWorkflowEventRegistration(
        event_type="workflow.resumed",
        counter_names=("workflow.resumed",),
        operation_id=FieldRequirement.FORBIDDEN,
        duration_ms=FieldRequirement.FORBIDDEN,
        terminal_code=FieldRequirement.FORBIDDEN,
    ),
    SafeWorkflowEventRegistration(
        event_type="workflow.replayed",
        counter_names=("workflow.replayed",),
        operation_id=FieldRequirement.REQUIRED,
        duration_ms=FieldRequirement.FORBIDDEN,
        terminal_code=FieldRequirement.FORBIDDEN,
    ),
    SafeWorkflowEventRegistration(
        event_type="workflow.terminal",
        counter_names=("workflow.terminal",),
        operation_id=FieldRequirement.FORBIDDEN,
        duration_ms=FieldRequirement.REQUIRED,
        terminal_code=FieldRequirement.REQUIRED,
    ),
    SafeWorkflowEventRegistration(
        event_type="artifact.created",
        counter_names=("artifact.created",),
        operation_id=FieldRequirement.REQUIRED,
        duration_ms=FieldRequirement.FORBIDDEN,
        terminal_code=FieldRequirement.FORBIDDEN,
    ),
    SafeWorkflowEventRegistration(
        event_type="artifact.reused",
        counter_names=("artifact.cache_hit",),
        operation_id=FieldRequirement.REQUIRED,
        duration_ms=FieldRequirement.FORBIDDEN,
        terminal_code=FieldRequirement.FORBIDDEN,
    ),
    SafeWorkflowEventRegistration(
        event_type="budget.consumed",
        counter_names=("budget.units_consumed",),
        operation_id=FieldRequirement.REQUIRED,
        duration_ms=FieldRequirement.FORBIDDEN,
        terminal_code=FieldRequirement.FORBIDDEN,
    ),
    SafeWorkflowEventRegistration(
        event_type="budget.exceeded",
        counter_names=("budget.exceeded",),
        operation_id=FieldRequirement.REQUIRED,
        duration_ms=FieldRequirement.FORBIDDEN,
        terminal_code=FieldRequirement.FORBIDDEN,
    ),
)

STANDARD_EVENT_DELIVERY_FAILURE_CODES = (
    "sink_unavailable",
    "sink_timeout",
    "sink_rejected",
    "sink_capacity_exceeded",
)
