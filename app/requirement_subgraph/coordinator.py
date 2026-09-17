"""Process-local admission, operation leases, and pause control."""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Callable, Literal, cast
from uuid import UUID

from langgraph.types import Command
from pydantic import BaseModel, TypeAdapter

from app.requirements import (
    NeedClarification,
    NeedConfirmation,
    RequirementDomainService,
    RequirementDomainState,
    RequirementInvalidated,
    RequirementTurnInput,
)
from app.workflow_contracts import (
    BudgetConsumeOutcome,
    BudgetConsumeRequest,
    BudgetConsumed as WorkflowBudgetConsumed,
    BudgetExceeded as WorkflowBudgetExceeded,
    ExecutionBudgetManager,
    OperationId,
    OperationResultIndeterminateError,
    TravelSessionState,
    WorkflowPhase,
)

from app.requirement_subgraph.errors import (
    RequirementSubgraphCapacityExceededError,
    RequirementSubgraphConfigurationError,
    RequirementSubgraphInputError,
    RequirementSubgraphResumeRejectedError,
    RequirementSubgraphRunFaultedError,
    RequirementSubgraphStateError,
)
from app.requirement_subgraph.models import (
    DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS,
    PauseToken,
    REQUIREMENTS_CLARIFICATION_ROUND_KEY,
    RequirementClarificationInterrupt,
    RequirementClarificationResume,
    RequirementConfirmationInterrupt,
    RequirementConfirmationResume,
    RequirementInterrupt,
    RequirementPauseEnvelope,
    RequirementResume,
    RequirementResumeEnvelope,
    RequirementSubgraphBudgetExceeded,
    RequirementSubgraphConfirmed,
    RequirementSubgraphCoordinatorLimits,
    RequirementSubgraphDurablePause,
    RequirementSubgraphDomainFailed,
    RequirementSubgraphPaused,
    RequirementSubgraphResult,
    _fresh_model,
    _pause_token_digest,
    _state_digest,
)
from app.requirement_subgraph.signals import (
    BudgetConsumed,
    BudgetExceeded,
    NodeReplayed,
    RequirementSubgraphSignal,
    RequirementSubgraphSignalSink,
    ResumeAccepted,
    StartAccepted,
)


_REQUIREMENT_RESUME_SENTINEL = "requirement_resume_v1"
_STATUS = Literal[
    "running",
    "paused",
    "resume_pending",
    "confirmed",
    "domain_failed",
    "budget_exceeded",
    "faulted",
]


def _new_secret_hex() -> str:
    return secrets.token_hex(16)


def _fresh_exact(model_type: type[BaseModel], value: object, error_type: type[Exception]) -> BaseModel:
    if type(value) is not model_type:
        raise error_type() from None
    try:
        return cast(BaseModel, _fresh_model(model_type, cast(BaseModel, value)))
    except Exception:
        raise error_type() from None


def _fresh_union(model_type: object, value: object, error_type: type[Exception]) -> object:
    if not isinstance(value, BaseModel):
        raise error_type() from None
    try:
        return _fresh_model(model_type, value)
    except Exception:
        raise error_type() from None


@dataclass(slots=True)
class _RunRecord:
    run_id: UUID
    trace_id: UUID
    manager: object
    loop: asyncio.AbstractEventLoop
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    status: _STATUS = "running"
    latest_state: TravelSessionState | None = None
    turn_slot: RequirementTurnInput | None = None
    action_slot: RequirementResumeEnvelope | None = None
    pause_token: str | None = None
    operation_nonce: str | None = None
    interrupt_descriptor: RequirementInterrupt | None = None
    durable_pause: RequirementSubgraphDurablePause | None = None
    pending_result: RequirementSubgraphResult | None = None
    lease_generation: int = 0
    active_lease: "_Lease | None" = None
    lease_resolved: bool = False


@dataclass(frozen=True, slots=True)
class _Lease:
    run_id: UUID
    generation: int


_CURRENT_LEASE: ContextVar[_Lease | None] = ContextVar(
    "requirement_subgraph_current_lease", default=None
)


class _OperationScope(AbstractAsyncContextManager[object]):
    def __init__(
        self,
        coordinator: "RequirementSubgraphCoordinator",
        *,
        kind: Literal["start", "resume", "durable_resume"],
        state: TravelSessionState | None = None,
        turn: RequirementTurnInput | None = None,
        envelope: RequirementResumeEnvelope | None = None,
        durable_pause: RequirementSubgraphDurablePause | None = None,
        resume: RequirementResume | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._kind = kind
        self._state: object | None = state
        self._turn: object | None = turn
        self._envelope: object | None = envelope
        self._durable_pause: object | None = durable_pause
        self._resume: object | None = resume
        self._record: _RunRecord | None = None
        self._lease_token: Token[_Lease | None] | None = None
        self._lock_acquired = False
        self._entered = False

    async def __aenter__(self) -> object:
        try:
            if self._kind == "start":
                value = await self._coordinator._enter_start(
                    self, self._state, self._turn
                )
            elif self._kind == "resume":
                value = await self._coordinator._enter_resume(
                    self, self._state, self._envelope
                )
            elif self._kind == "durable_resume":
                value = await self._coordinator._enter_durable_resume(
                    self,
                    self._state,
                    self._durable_pause,
                    self._resume,
                )
            else:
                raise RequirementSubgraphStateError() from None
            self._entered = True
            return value
        finally:
            self._state = None
            self._turn = None
            self._envelope = None
            self._durable_pause = None
            self._resume = None

    async def __aexit__(self, exc_type, exc, traceback) -> bool | None:
        return await self._coordinator._exit_scope(
            self,
            exc_type=exc_type,
            exception=exc,
        )


class RequirementSubgraphCoordinator:
    def __init__(
        self,
        *,
        domain_service: RequirementDomainService,
        execution_budget_manager_factory: Callable[[UUID], ExecutionBudgetManager],
        signal_sink: RequirementSubgraphSignalSink,
        limits: RequirementSubgraphCoordinatorLimits,
    ) -> None:
        if not callable(getattr(domain_service, "process_turn", None)) or not callable(
            getattr(domain_service, "confirm", None)
        ):
            raise RequirementSubgraphConfigurationError() from None
        if not callable(execution_budget_manager_factory):
            raise RequirementSubgraphConfigurationError() from None
        if not callable(getattr(signal_sink, "accept", None)):
            raise RequirementSubgraphConfigurationError() from None
        self._domain_service = domain_service
        self._manager_factory = execution_budget_manager_factory
        self._signal_sink = signal_sink
        self._limits = cast(
            RequirementSubgraphCoordinatorLimits,
            _fresh_exact(
                RequirementSubgraphCoordinatorLimits,
                limits,
                RequirementSubgraphConfigurationError,
            ),
        )
        self._records: dict[UUID, _RunRecord] = {}
        self._admission_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def start_operation(
        self,
        *,
        state: TravelSessionState,
        turn: RequirementTurnInput,
    ) -> AbstractAsyncContextManager[TravelSessionState]:
        return cast(
            AbstractAsyncContextManager[TravelSessionState],
            _OperationScope(self, kind="start", state=state, turn=turn),
        )

    def resume_operation(
        self,
        *,
        state: TravelSessionState,
        envelope: RequirementResumeEnvelope,
    ) -> AbstractAsyncContextManager[Command]:
        return cast(
            AbstractAsyncContextManager[Command],
            _OperationScope(self, kind="resume", state=state, envelope=envelope),
        )

    def _resume_durable_operation(
        self,
        *,
        state: TravelSessionState,
        durable_pause: RequirementSubgraphDurablePause,
        resume: RequirementResume,
    ) -> AbstractAsyncContextManager[Command]:
        return cast(
            AbstractAsyncContextManager[Command],
            _OperationScope(
                self,
                kind="durable_resume",
                state=state,
                durable_pause=durable_pause,
                resume=resume,
            ),
        )

    def _verify_durable_pause_token(
        self,
        *,
        durable_pause: RequirementSubgraphDurablePause,
        pause_token: PauseToken,
    ) -> None:
        fresh_pause = cast(
            RequirementSubgraphDurablePause,
            _fresh_exact(
                RequirementSubgraphDurablePause,
                durable_pause,
                RequirementSubgraphResumeRejectedError,
            ),
        )
        try:
            fresh_token = TypeAdapter(PauseToken).validate_python(
                pause_token,
                strict=True,
            )
        except Exception:
            raise RequirementSubgraphResumeRejectedError() from None
        nonce_matches_token = hmac.compare_digest(
            fresh_pause.operation_nonce,
            fresh_token,
        )
        digest_matches = hmac.compare_digest(
            fresh_pause.pause_token_digest,
            _pause_token_digest(fresh_pause.run_id, fresh_token),
        )
        if nonce_matches_token or not digest_matches:
            raise RequirementSubgraphResumeRejectedError() from None

    def _ensure_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise RequirementSubgraphConfigurationError() from None
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RequirementSubgraphConfigurationError() from None

    def resolve_result(
        self,
        *,
        state: TravelSessionState,
        interrupt_value: object | None,
    ) -> RequirementSubgraphResult:
        record = self._record_for_state(state, error_type=RequirementSubgraphStateError)
        self._require_lease(record)
        if record.lease_resolved:
            self._fault(record)
            raise RequirementSubgraphStateError() from None
        fresh_state = cast(
            TravelSessionState,
            _fresh_exact(TravelSessionState, state, RequirementSubgraphStateError),
        )
        if record.latest_state is None or fresh_state != record.latest_state:
            self._fault(record)
            raise RequirementSubgraphStateError() from None

        try:
            if record.pending_result is not None:
                if interrupt_value is not None:
                    raise RequirementSubgraphStateError()
                result = self._copy_result(record.pending_result)
                record.pending_result = None
                self._mark_resolved(record)
                self._settle(record, result.result_type)
                return result

            if interrupt_value is None:
                raise RequirementSubgraphStateError()
            descriptor = self._parse_interrupt(interrupt_value)
            if (
                record.pause_token is None
                or record.operation_nonce is None
                or record.interrupt_descriptor is None
                or descriptor != record.interrupt_descriptor
            ):
                raise RequirementSubgraphStateError()
            if record.durable_pause is None:
                record.durable_pause = RequirementSubgraphDurablePause(
                    run_id=fresh_state.run_id,
                    trace_id=fresh_state.trace_id,
                    state_digest=_state_digest(fresh_state),
                    pause_token_digest=_pause_token_digest(
                        fresh_state.run_id,
                        record.pause_token,
                    ),
                    operation_nonce=record.operation_nonce,
                    interrupt=descriptor,
                )
            result = RequirementSubgraphPaused(
                state=fresh_state,
                pause=RequirementPauseEnvelope(
                    pause_token=record.pause_token,
                    interrupt=descriptor,
                ),
                durable_pause=record.durable_pause,
            )
            result = self._copy_result(result)
            self._mark_resolved(record)
            record.status = "paused"
            return result
        except RequirementSubgraphStateError:
            self._fault(record)
            raise
        except Exception:
            self._fault(record)
            raise RequirementSubgraphStateError() from None

    @asynccontextmanager
    async def _runtime_read_scope(self, run_id: UUID):
        self._ensure_loop()
        if type(run_id) is not UUID:
            raise RequirementSubgraphInputError() from None
        record = self._records.get(run_id)
        if record is None:
            raise RequirementSubgraphInputError() from None
        await record.operation_lock.acquire()
        try:
            if record.status == "faulted":
                raise RequirementSubgraphRunFaultedError() from None
            yield record
        finally:
            record.operation_lock.release()

    async def _enter_start(
        self,
        scope: _OperationScope,
        state: object,
        turn: object | None,
    ) -> TravelSessionState:
        self._ensure_loop()
        fresh_state = cast(
            TravelSessionState,
            _fresh_exact(TravelSessionState, state, RequirementSubgraphInputError),
        )
        fresh_turn = cast(
            RequirementTurnInput,
            _fresh_exact(RequirementTurnInput, turn, RequirementSubgraphInputError),
        )
        self._validate_initial_state(fresh_state, fresh_turn)
        loop = self._loop
        assert loop is not None
        async with self._admission_lock:
            if fresh_state.run_id in self._records:
                raise RequirementSubgraphInputError() from None
            if len(self._records) >= self._limits.max_runs:
                raise RequirementSubgraphCapacityExceededError() from None
            manager = self._manager_factory(fresh_state.run_id)
            if not self._has_manager_surface(manager):
                raise RequirementSubgraphConfigurationError() from None
            record = _RunRecord(
                run_id=fresh_state.run_id,
                trace_id=fresh_state.trace_id,
                manager=manager,
                loop=loop,
                latest_state=fresh_state,
                turn_slot=cast(
                    RequirementTurnInput,
                    _fresh_exact(
                        RequirementTurnInput,
                        fresh_turn,
                        RequirementSubgraphStateError,
                    ),
                ),
            )
            self._records[fresh_state.run_id] = record
        await self._acquire_lease(scope, record)
        try:
            self._emit(
                StartAccepted(run_id=record.run_id, trace_id=record.trace_id)
            )
        except Exception:
            self._fault(record)
            await self._release_lease(scope, record)
            raise
        return cast(TravelSessionState, record.latest_state)

    async def _enter_resume(
        self,
        scope: _OperationScope,
        state: object,
        envelope: object | None,
    ) -> Command:
        self._ensure_loop()
        fresh_state = cast(
            TravelSessionState,
            _fresh_exact(TravelSessionState, state, RequirementSubgraphInputError),
        )
        fresh_envelope = cast(
            RequirementResumeEnvelope,
            _fresh_exact(
                RequirementResumeEnvelope,
                envelope,
                RequirementSubgraphResumeRejectedError,
            ),
        )
        record = self._records.get(fresh_state.run_id)
        if record is None:
            raise RequirementSubgraphResumeRejectedError() from None
        await record.operation_lock.acquire()
        scope._lock_acquired = True
        scope._record = record
        try:
            return await self._commit_resume(scope, fresh_state, fresh_envelope)
        except Exception:
            await self._release_lease(scope, record)
            raise

    async def _enter_durable_resume(
        self,
        scope: _OperationScope,
        state: object,
        durable_pause: object | None,
        resume: object | None,
    ) -> Command:
        self._ensure_loop()
        fresh_state = cast(
            TravelSessionState,
            _fresh_exact(TravelSessionState, state, RequirementSubgraphInputError),
        )
        fresh_pause = cast(
            RequirementSubgraphDurablePause,
            _fresh_exact(
                RequirementSubgraphDurablePause,
                durable_pause,
                RequirementSubgraphResumeRejectedError,
            ),
        )
        fresh_resume = cast(
            RequirementResume,
            _fresh_union(
                RequirementResume,
                resume,
                RequirementSubgraphResumeRejectedError,
            ),
        )
        self._validate_durable_pause(fresh_state, fresh_pause)
        loop = self._loop
        assert loop is not None
        async with self._admission_lock:
            record = self._records.get(fresh_state.run_id)
            if record is None:
                if len(self._records) >= self._limits.max_runs:
                    raise RequirementSubgraphCapacityExceededError() from None
                manager = self._manager_factory(fresh_state.run_id)
                if not self._has_manager_surface(manager):
                    raise RequirementSubgraphConfigurationError() from None
                ephemeral_token = _new_secret_hex()
                while ephemeral_token == fresh_pause.operation_nonce:
                    ephemeral_token = _new_secret_hex()
                record = _RunRecord(
                    run_id=fresh_state.run_id,
                    trace_id=fresh_state.trace_id,
                    manager=manager,
                    loop=loop,
                    status="paused",
                    latest_state=fresh_state,
                    pause_token=ephemeral_token,
                    operation_nonce=fresh_pause.operation_nonce,
                    interrupt_descriptor=fresh_pause.interrupt,
                    durable_pause=fresh_pause,
                )
                self._records[fresh_state.run_id] = record
        await record.operation_lock.acquire()
        scope._lock_acquired = True
        scope._record = record
        try:
            self._validate_durable_record(record, fresh_state, fresh_pause)
            if record.pause_token is None:
                raise RequirementSubgraphResumeRejectedError() from None
            envelope = RequirementResumeEnvelope(
                pause_token=record.pause_token,
                resume=fresh_resume,
            )
            return await self._commit_resume(scope, fresh_state, envelope)
        except Exception:
            await self._release_lease(scope, record)
            raise

    async def _commit_resume(
        self,
        scope: _OperationScope,
        state: TravelSessionState,
        envelope: RequirementResumeEnvelope,
    ) -> Command:
        record = scope._record
        if record is None:
            raise RequirementSubgraphStateError() from None
        wrote_resume = False
        try:
            if record.status == "faulted":
                raise RequirementSubgraphRunFaultedError() from None
            if record.status != "paused":
                raise RequirementSubgraphResumeRejectedError() from None
            self._validate_resume(record, state, envelope)
            record.action_slot = envelope
            record.status = "resume_pending"
            wrote_resume = True
            await self._activate_lease(scope, record)
            self._emit(
                ResumeAccepted(run_id=record.run_id, trace_id=record.trace_id)
            )
            return Command(resume=_REQUIREMENT_RESUME_SENTINEL)
        except Exception:
            if scope._lease_token is not None:
                self._fault(record)
            elif wrote_resume:
                record.action_slot = None
                if record.status == "resume_pending":
                    record.status = "paused"
            raise

    async def _acquire_lease(self, scope: _OperationScope, record: _RunRecord) -> None:
        await record.operation_lock.acquire()
        scope._lock_acquired = True
        scope._record = record
        try:
            await self._activate_lease(scope, record)
        except Exception:
            await self._release_lease(scope, record)
            raise

    async def _activate_lease(self, scope: _OperationScope, record: _RunRecord) -> None:
        record.lease_generation += 1
        lease = _Lease(record.run_id, record.lease_generation)
        record.active_lease = lease
        record.lease_resolved = False
        scope._lease_token = _CURRENT_LEASE.set(lease)

    async def _release_lease(self, scope: _OperationScope, record: _RunRecord) -> None:
        if scope._lease_token is not None:
            _CURRENT_LEASE.reset(scope._lease_token)
            scope._lease_token = None
        record.active_lease = None
        if scope._lock_acquired:
            scope._lock_acquired = False
            record.operation_lock.release()

    async def _exit_scope(
        self,
        scope: _OperationScope,
        *,
        exc_type: type[BaseException] | None,
        exception: BaseException | None,
    ) -> bool | None:
        record = scope._record
        if record is None:
            return None
        if scope._lease_token is None:
            await self._release_lease(scope, record)
            return None
        protocol_error: BaseException | None = None
        if exc_type is None and not record.lease_resolved:
            self._fault(record)
            protocol_error = RequirementSubgraphStateError()
        elif exc_type is not None and record.status in {"running", "resume_pending"}:
            self._fault(record)
        await self._release_lease(scope, record)
        if protocol_error is not None:
            raise protocol_error from None
        return None

    def _validate_initial_state(
        self,
        state: TravelSessionState,
        turn: RequirementTurnInput,
    ) -> None:
        if (
            state.schema_version != "2.0"
            or state.phase is not WorkflowPhase.REQUIREMENTS
            or state.delivery_status.value != "in_progress"
            or state.terminal_code is not None
            or state.terminal_outcome is not None
            or state.requirement_outcome is not None
            or state.requirement_state != RequirementDomainState()
            or state.artifact_refs
            or turn.current != state.requirement_state
        ):
            raise RequirementSubgraphInputError() from None

    def _validate_resume(
        self,
        record: _RunRecord,
        state: TravelSessionState,
        envelope: RequirementResumeEnvelope,
    ) -> None:
        if record.latest_state is None or state != record.latest_state:
            raise RequirementSubgraphResumeRejectedError() from None
        if record.pause_token is None or not hmac.compare_digest(
            record.pause_token, envelope.pause_token
        ):
            raise RequirementSubgraphResumeRejectedError() from None
        outcome = state.requirement_outcome
        resume = envelope.resume
        if type(outcome) is NeedClarification:
            if type(resume) is not RequirementClarificationResume:
                raise RequirementSubgraphResumeRejectedError() from None
            if resume.turn.current != state.requirement_state:
                raise RequirementSubgraphResumeRejectedError() from None
        elif type(outcome) in (NeedConfirmation, RequirementInvalidated):
            if type(resume) is not RequirementConfirmationResume:
                raise RequirementSubgraphResumeRejectedError() from None
        else:
            raise RequirementSubgraphResumeRejectedError() from None

    def _validate_durable_pause(
        self,
        state: TravelSessionState,
        durable_pause: RequirementSubgraphDurablePause,
    ) -> None:
        if (
            state.schema_version != "2.0"
            or state.phase is not WorkflowPhase.REQUIREMENTS
            or state.delivery_status.value != "awaiting_user"
            or state.terminal_code is not None
            or state.terminal_outcome is not None
            or durable_pause.run_id != state.run_id
            or durable_pause.trace_id != state.trace_id
            or durable_pause.state_digest != _state_digest(state)
            or not self._interrupt_matches_state(state, durable_pause.interrupt)
        ):
            raise RequirementSubgraphResumeRejectedError() from None

    def _validate_durable_record(
        self,
        record: _RunRecord,
        state: TravelSessionState,
        durable_pause: RequirementSubgraphDurablePause,
    ) -> None:
        if (
            record.status != "paused"
            or record.run_id != state.run_id
            or record.trace_id != state.trace_id
            or record.latest_state != state
            or record.operation_nonce != durable_pause.operation_nonce
            or record.interrupt_descriptor != durable_pause.interrupt
            or record.durable_pause != durable_pause
        ):
            raise RequirementSubgraphResumeRejectedError() from None

    @staticmethod
    def _interrupt_matches_state(
        state: TravelSessionState,
        interrupt: RequirementInterrupt,
    ) -> bool:
        outcome = state.requirement_outcome
        if type(outcome) is NeedClarification:
            return (
                type(interrupt) is RequirementClarificationInterrupt
                and interrupt.message == outcome.message
            )
        if type(outcome) is NeedConfirmation:
            return (
                type(interrupt) is RequirementConfirmationInterrupt
                and interrupt.source_outcome_type == "need_confirmation"
                and interrupt.summary == outcome.summary
            )
        if type(outcome) is RequirementInvalidated:
            return (
                type(interrupt) is RequirementConfirmationInterrupt
                and interrupt.source_outcome_type == "invalidated"
                and interrupt.summary == outcome.summary
            )
        return False

    def _record_for_state(
        self,
        state: object,
        *,
        error_type: type[Exception],
    ) -> _RunRecord:
        if type(state) is not TravelSessionState:
            raise error_type() from None
        record = self._records.get(cast(TravelSessionState, state).run_id)
        if record is None:
            raise error_type() from None
        if record.status == "faulted":
            raise RequirementSubgraphRunFaultedError() from None
        return record

    def _require_lease(self, record: _RunRecord) -> None:
        if record.status == "faulted":
            raise RequirementSubgraphRunFaultedError() from None
        lease = _CURRENT_LEASE.get()
        if (
            lease is None
            or record.active_lease is None
            or lease.run_id != record.run_id
            or lease.generation != record.active_lease.generation
        ):
            self._fault(record)
            raise RequirementSubgraphStateError() from None

    def _current_record(self) -> _RunRecord:
        lease = _CURRENT_LEASE.get()
        if lease is None:
            raise RequirementSubgraphStateError() from None
        record = self._records.get(lease.run_id)
        if record is None:
            raise RequirementSubgraphStateError() from None
        self._require_lease(record)
        return record

    def _emit(self, signal: RequirementSubgraphSignal) -> None:
        if type(signal) not in (StartAccepted, ResumeAccepted, NodeReplayed, BudgetConsumed, BudgetExceeded):
            raise RequirementSubgraphStateError() from None
        result = self._signal_sink.accept(signal)
        if result is not None:
            raise RequirementSubgraphStateError() from None

    def _fault(self, record: _RunRecord) -> None:
        record.status = "faulted"
        record.latest_state = None
        record.turn_slot = None
        record.action_slot = None
        record.pause_token = None
        record.operation_nonce = None
        record.interrupt_descriptor = None
        record.durable_pause = None
        record.pending_result = None

    def _settle(self, record: _RunRecord, result_type: str) -> None:
        record.status = cast(_STATUS, result_type)
        record.latest_state = None
        record.turn_slot = None
        record.action_slot = None
        record.pause_token = None
        record.operation_nonce = None
        record.interrupt_descriptor = None
        record.durable_pause = None

    def _mark_resolved(self, record: _RunRecord) -> None:
        if record.lease_resolved:
            self._fault(record)
            raise RequirementSubgraphStateError() from None
        record.lease_resolved = True

    @staticmethod
    def _has_manager_surface(manager: object) -> bool:
        return all(
            callable(getattr(manager, name, None))
            for name in ("consume", "get_snapshot", "get_all_snapshots")
        )

    @staticmethod
    def _parse_interrupt(value: object) -> RequirementInterrupt:
        if type(value) in (RequirementClarificationInterrupt, RequirementConfirmationInterrupt):
            return cast(
                RequirementInterrupt,
                _fresh_union(RequirementInterrupt, value, RequirementSubgraphStateError),
            )
        if type(value) is not dict:
            raise RequirementSubgraphStateError() from None
        try:
            adapter = TypeAdapter(RequirementInterrupt)
            first = adapter.validate_python(value, strict=True)
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
            return cast(RequirementInterrupt, adapter.validate_json(encoded, strict=True))
        except Exception:
            raise RequirementSubgraphStateError() from None

    def _copy_result(self, result: RequirementSubgraphResult) -> RequirementSubgraphResult:
        if type(result) not in (
            RequirementSubgraphPaused,
            RequirementSubgraphConfirmed,
            RequirementSubgraphDomainFailed,
            RequirementSubgraphBudgetExceeded,
        ):
            raise RequirementSubgraphStateError() from None
        return cast(
            RequirementSubgraphResult,
            _fresh_union(RequirementSubgraphResult, result, RequirementSubgraphStateError),
        )

    def _take_turn(self) -> RequirementTurnInput:
        record = self._current_record()
        if record.turn_slot is None:
            raise RequirementSubgraphStateError() from None
        turn = cast(
            RequirementTurnInput,
            _fresh_exact(RequirementTurnInput, record.turn_slot, RequirementSubgraphStateError),
        )
        record.turn_slot = None
        return turn

    def _take_resume(self) -> RequirementResumeEnvelope:
        record = self._current_record()
        if record.action_slot is None or record.status != "resume_pending":
            raise RequirementSubgraphStateError() from None
        envelope = cast(
            RequirementResumeEnvelope,
            _fresh_exact(
                RequirementResumeEnvelope,
                record.action_slot,
                RequirementSubgraphStateError,
            ),
        )
        record.action_slot = None
        return envelope

    def _set_latest_state(self, state: TravelSessionState) -> None:
        record = self._current_record()
        record.latest_state = cast(
            TravelSessionState,
            _fresh_exact(TravelSessionState, state, RequirementSubgraphStateError),
        )

    def _ensure_current_pause(
        self,
        *,
        state: TravelSessionState,
        interrupt: RequirementInterrupt,
    ) -> tuple[str, str, RequirementInterrupt]:
        record = self._current_record()
        fresh_state = cast(
            TravelSessionState,
            _fresh_exact(TravelSessionState, state, RequirementSubgraphStateError),
        )
        if record.latest_state is None or fresh_state != record.latest_state:
            self._fault(record)
            raise RequirementSubgraphStateError() from None
        fresh_interrupt = cast(
            RequirementInterrupt,
            _fresh_union(RequirementInterrupt, interrupt, RequirementSubgraphStateError),
        )
        if record.status == "resume_pending":
            if (
                record.pause_token is None
                or record.operation_nonce is None
                or record.interrupt_descriptor != fresh_interrupt
                or record.durable_pause is None
            ):
                self._fault(record)
                raise RequirementSubgraphStateError() from None
            return record.pause_token, record.operation_nonce, fresh_interrupt
        if record.status != "running":
            raise RequirementSubgraphStateError() from None
        token = _new_secret_hex()
        nonce = _new_secret_hex()
        while nonce == token:
            nonce = _new_secret_hex()
        if len(token) != 32 or len(nonce) != 32 or any(
            char not in "0123456789abcdef" for char in token + nonce
        ):
            self._fault(record)
            raise RequirementSubgraphStateError() from None
        record.pause_token = token
        record.operation_nonce = nonce
        record.interrupt_descriptor = fresh_interrupt
        record.durable_pause = None
        return token, nonce, fresh_interrupt

    def _take_pause_for_replay(self) -> tuple[str, RequirementInterrupt]:
        record = self._current_record()
        if (
            record.pause_token is None
            or record.operation_nonce is None
            or record.interrupt_descriptor is None
            or record.durable_pause is None
        ):
            raise RequirementSubgraphStateError() from None
        nonce = record.operation_nonce
        descriptor = record.interrupt_descriptor
        record.pause_token = None
        record.interrupt_descriptor = None
        record.durable_pause = None
        record.status = "running"
        return nonce, descriptor

    def _node_replayed_operation(self) -> OperationId:
        record = self._current_record()
        if record.operation_nonce is None:
            raise RequirementSubgraphStateError() from None
        operation = OperationId(
            run_id=record.run_id,
            phase=WorkflowPhase.REQUIREMENTS,
            logical_step=f"requirement_pause_{record.operation_nonce}",
        )
        self._emit(
            NodeReplayed(
                run_id=record.run_id,
                trace_id=record.trace_id,
                operation_id=operation,
            )
        )
        return operation

    async def _consume_budget(
        self,
        request: BudgetConsumeRequest,
    ) -> BudgetConsumeOutcome:
        record = self._current_record()
        if request.key != REQUIREMENTS_CLARIFICATION_ROUND_KEY:
            self._fault(record)
            raise RequirementSubgraphStateError() from None
        try:
            result = await cast(ExecutionBudgetManager, record.manager).consume(request)
            if type(result) not in (WorkflowBudgetConsumed, WorkflowBudgetExceeded):
                raise RequirementSubgraphStateError()
            fresh = _fresh_union(
                WorkflowBudgetConsumed | WorkflowBudgetExceeded,
                result,
                RequirementSubgraphStateError,
            )
            assert isinstance(fresh, (WorkflowBudgetConsumed, WorkflowBudgetExceeded))
            if (
                fresh.operation_id != request.operation_id
                or fresh.key != request.key
                or fresh.amount != request.amount
                or fresh.snapshot.run_id != request.operation_id.run_id
                or fresh.snapshot.key != request.key
            ):
                raise RequirementSubgraphStateError()
            if fresh.disposition == "replayed":
                raise OperationResultIndeterminateError() from None
            if isinstance(fresh, WorkflowBudgetConsumed) and fresh.disposition == "applied":
                self._emit(
                    BudgetConsumed(
                        run_id=request.operation_id.run_id,
                        trace_id=record.trace_id,
                        operation_id=request.operation_id,
                    )
                )
            elif isinstance(fresh, WorkflowBudgetExceeded) and fresh.disposition == "rejected":
                self._emit(
                    BudgetExceeded(
                        run_id=request.operation_id.run_id,
                        trace_id=record.trace_id,
                        operation_id=request.operation_id,
                    )
                )
            return cast(BudgetConsumeOutcome, fresh)
        except Exception:
            self._fault(record)
            raise

    def _set_local_result(self, result: RequirementSubgraphResult) -> None:
        record = self._current_record()
        fresh = self._copy_result(result)
        if record.latest_state is None or fresh.state != record.latest_state:
            self._fault(record)
            raise RequirementSubgraphStateError() from None
        record.pending_result = fresh

__all__ = ["RequirementSubgraphCoordinator"]
