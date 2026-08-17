"""Test-only fakes for the module 05 T01/T02 boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from datetime import date
from typing import Callable
from uuid import UUID, uuid4

from pydantic import BaseModel, TypeAdapter

from app.requirements import (
    ConfirmedTravelRequirement,
    EvidenceReceipt,
    NeedClarification,
    NeedConfirmation,
    RequirementConfirmation,
    RequirementDomainService,
    RequirementDomainState,
    RequirementDomainTransition,
    RequirementErrorCode,
    RequirementExtractionFailed,
    RequirementIntent,
    RequirementInvalidated,
    RequirementTurnInput,
    VerifiedFieldEvidence,
)
from app.workflow_contracts import (
    BudgetConsumeOutcome,
    BudgetConsumeRequest,
    DeliveryStatus,
    ExecutionBudgetKey,
    ExecutionBudgetSnapshot,
    OperationId,
    TravelSessionState,
    WorkflowPhase,
)
from app.requirement_subgraph.models import (
    RequirementClarificationInterrupt,
    RequirementConfirmationInterrupt,
    RequirementInterrupt,
    RequirementSubgraphCoordinatorLimits,
)
from app.requirement_subgraph.signals import (
    BudgetConsumed,
    BudgetExceeded,
    NodeReplayed,
    RequirementSubgraphSignal,
    ResumeAccepted,
    StartAccepted,
)


def fresh(model_type: object, value: BaseModel) -> BaseModel:
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
    payload = json.dumps(
        json_tree,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return adapter.validate_json(payload, strict=True)


class ScriptedRequirementDomainService(RequirementDomainService):
    def __init__(
        self,
        *,
        process_results: Sequence[RequirementDomainTransition] = (),
        confirm_results: Sequence[RequirementDomainTransition] = (),
        process_error: BaseException | None = None,
        confirm_error: BaseException | None = None,
    ) -> None:
        self.process_results = list(process_results)
        self.confirm_results = list(confirm_results)
        self.process_error = process_error
        self.confirm_error = confirm_error
        self.process_calls: list[RequirementTurnInput] = []
        self.confirm_calls: list[tuple[RequirementDomainState, RequirementConfirmation]] = []

    async def process_turn(self, input: RequirementTurnInput) -> RequirementDomainTransition:
        self.process_calls.append(fresh(RequirementTurnInput, input))  # type: ignore[arg-type]
        if self.process_error is not None:
            raise self.process_error
        if not self.process_results:
            raise AssertionError("no scripted process transition")
        return fresh(RequirementDomainTransition, self.process_results.pop(0))  # type: ignore[return-value]

    async def confirm(
        self,
        state: RequirementDomainState,
        action: RequirementConfirmation,
    ) -> RequirementDomainTransition:
        self.confirm_calls.append(
            (
                fresh(RequirementDomainState, state),  # type: ignore[arg-type]
                fresh(RequirementConfirmation, action),  # type: ignore[arg-type]
            )
        )
        if self.confirm_error is not None:
            raise self.confirm_error
        if not self.confirm_results:
            raise AssertionError("no scripted confirm transition")
        return fresh(RequirementDomainTransition, self.confirm_results.pop(0))  # type: ignore[return-value]


class RecordingRequirementSubgraphSignalSink:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        return_value: object | None = None,
        return_on_call: int | None = None,
    ) -> None:
        self.fail_on_call = fail_on_call
        self.return_value = return_value
        self.return_on_call = return_on_call
        self.calls: list[RequirementSubgraphSignal] = []

    def accept(self, signal: RequirementSubgraphSignal) -> object | None:
        call_number = len(self.calls) + 1
        if self.fail_on_call == call_number:
            raise RuntimeError("sink failure")
        if type(signal) not in (
            StartAccepted,
            ResumeAccepted,
            NodeReplayed,
            BudgetConsumed,
            BudgetExceeded,
        ):
            raise AssertionError("unexpected signal type")
        self.calls.append(fresh(RequirementSubgraphSignal, signal))  # type: ignore[arg-type]
        if self.return_on_call is None or self.return_on_call == call_number:
            return self.return_value
        return None


class ScriptedExecutionBudgetManager:
    def __init__(
        self,
        outcomes: Sequence[BudgetConsumeOutcome] = (),
        *,
        errors: Sequence[BaseException] = (),
    ) -> None:
        self.outcomes = list(outcomes)
        self.errors = list(errors)
        self.requests: list[BudgetConsumeRequest] = []

    async def consume(self, request: BudgetConsumeRequest) -> BudgetConsumeOutcome:
        self.requests.append(request)
        if self.errors:
            raise self.errors.pop(0)
        if not self.outcomes:
            raise AssertionError("no scripted budget outcome")
        return self.outcomes.pop(0)

    async def get_snapshot(self, *, key: ExecutionBudgetKey) -> ExecutionBudgetSnapshot:
        raise AssertionError("snapshot was not scripted")

    async def get_all_snapshots(self) -> tuple[ExecutionBudgetSnapshot, ...]:
        return ()


class PerRunBudgetManagerFactory:
    def __init__(
        self,
        builder: Callable[[UUID], object],
        *,
        error: BaseException | None = None,
    ) -> None:
        self.builder = builder
        self.error = error
        self.created: list[UUID] = []
        self.managers: dict[UUID, object] = {}

    def __call__(self, run_id: UUID) -> object:
        self.created.append(run_id)
        if self.error is not None:
            raise self.error
        manager = self.builder(run_id)
        self.managers[run_id] = manager
        return manager


class SequenceSecretHex:
    def __init__(self, values: Sequence[str]) -> None:
        self.values = list(values)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if not self.values:
            raise AssertionError("secret sequence exhausted")
        return self.values.pop(0)


class AsyncBarrierProbe:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.maximum_active = 0

    async def hold(self) -> None:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.entered.set()
        await self.release.wait()
        self.active -= 1


def make_initial_state(
    *,
    run_id: UUID | None = None,
    trace_id: UUID | None = None,
) -> TravelSessionState:
    return TravelSessionState(
        schema_version="2.0",
        run_id=run_id or uuid4(),
        trace_id=trace_id or uuid4(),
        phase=WorkflowPhase.REQUIREMENTS,
        delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code=None,
        terminal_outcome=None,
        requirement_state=RequirementDomainState(),
        requirement_outcome=None,
        artifact_refs=(),
    )


def make_turn(state: TravelSessionState, *, user_message: str = "输入") -> RequirementTurnInput:
    return RequirementTurnInput(
        schema_version="1.0",
        turn_id=uuid4(),
        user_message=user_message,
        session_date=date(2026, 8, 17),
        timezone="Asia/Shanghai",
        intent=RequirementIntent.INITIAL,
        current=state.requirement_state,
    )


def make_clarification_interrupt(message: str = "请补充") -> RequirementClarificationInterrupt:
    return RequirementClarificationInterrupt(message=message)


def make_confirmation_interrupt(
    summary: str = "请确认",
    *,
    source: str = "need_confirmation",
) -> RequirementConfirmationInterrupt:
    return RequirementConfirmationInterrupt(
        source_outcome_type=source,
        summary=summary,
    )


def _transition_state(
    state: TravelSessionState,
    *,
    confirmation_status: str,
) -> RequirementDomainState:
    return RequirementDomainState(
        schema_version=state.requirement_state.schema_version,
        requirement=state.requirement_state.requirement,
        metadata=state.requirement_state.metadata,
        issues=state.requirement_state.issues,
        confirmation_status=confirmation_status,
    )


def make_need_clarification_transition(
    state: TravelSessionState,
    *,
    message: str = "请补充",
) -> RequirementDomainTransition:
    domain_state = _transition_state(state, confirmation_status="collecting")
    return RequirementDomainTransition(
        state=domain_state,
        outcome=NeedClarification(
            requirement=domain_state.requirement,
            metadata=domain_state.metadata,
            issues=domain_state.issues,
            message=message,
        ),
    )


def make_need_confirmation_transition(
    state: TravelSessionState,
    *,
    summary: str = "请确认",
) -> RequirementDomainTransition:
    domain_state = _transition_state(
        state,
        confirmation_status="awaiting_confirmation",
    )
    return RequirementDomainTransition(
        state=domain_state,
        outcome=NeedConfirmation(
            requirement=domain_state.requirement,
            metadata=domain_state.metadata,
            summary=summary,
        ),
    )


def make_confirmed_transition(
    state: TravelSessionState,
    *,
    digest: str = "confirmed-digest",
) -> RequirementDomainTransition:
    domain_state = _transition_state(state, confirmation_status="confirmed")
    return RequirementDomainTransition(
        state=domain_state,
        outcome=ConfirmedTravelRequirement(
            schema_version="1.0",
            requirement=domain_state.requirement,
            metadata_without_raw_evidence=domain_state.metadata,
            confirmation_summary_digest=digest,
        ),
    )


def make_extraction_failed_transition(
    state: TravelSessionState,
    *,
    code: RequirementErrorCode = RequirementErrorCode.INPUT_TOO_LONG,
) -> RequirementDomainTransition:
    return RequirementDomainTransition(
        state=_transition_state(state, confirmation_status="collecting"),
        outcome=RequirementExtractionFailed(code=code),
    )


def make_invalidated_transition(
    state: TravelSessionState,
    *,
    summary: str = "已失效",
) -> RequirementDomainTransition:
    domain_state = _transition_state(
        state,
        confirmation_status="awaiting_confirmation",
    )
    after_digest = hashlib.sha256(
        domain_state.requirement.model_dump_json(exclude_none=False).encode("utf-8")
    ).hexdigest()
    return RequirementDomainTransition(
        state=domain_state,
        outcome=RequirementInvalidated(
            changed_fields=(),
            before_digest="a" * 64,
            after_digest=after_digest,
            reason="需求发生变化",
            summary=summary,
        ),
    )


__all__ = [
    "AsyncBarrierProbe",
    "PerRunBudgetManagerFactory",
    "RecordingRequirementSubgraphSignalSink",
    "ScriptedExecutionBudgetManager",
    "ScriptedRequirementDomainService",
    "SequenceSecretHex",
    "fresh",
    "make_clarification_interrupt",
    "make_confirmation_interrupt",
    "make_need_clarification_transition",
    "make_need_confirmation_transition",
    "make_confirmed_transition",
    "make_extraction_failed_transition",
    "make_invalidated_transition",
    "make_initial_state",
    "make_turn",
]
