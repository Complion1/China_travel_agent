"""Loop-owned application scheduling and public upstream composition."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Callable, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.artifact_persistence import ArtifactPersistenceRuntime
from app.demo_workflow import (DemoDiagnostics, DemoWorkflowDefinition, DemoStartCommand,
    DemoRequirementCommand, DemoHotelCommand)
from app.durable_execution import (DurableExecutionRuntime, DurableRunView,
    DurableExecutionRunNotFoundError, DurableExecutionRunBusyError,
    DurableExecutionCancellationPendingError, DurableExecutionResumeRejectedError,
    DurableExecutionUsageError, DurableExecutionConfigurationError,
    DurableExecutionRunBlockedError, DurableExecutionIncompatibleRunError)
from app.requirement_extraction import ExtractionInvocationEvent
from app.requirements import (RequirementTurnInput, RequirementIntent, RequirementDomainState,
    RequirementConfirmation, ConfirmedTravelRequirement, validate_requirement_hubs)
from app.requirement_subgraph import RequirementClarificationResume, RequirementConfirmationResume
from app.hotel_selection import HotelSelectCommand, HotelDeclineCommand
from app.result_formatting import (ResultFormattingService, ResultReadRequest, ResultFormattingLoaded)
from app.workflow_contracts import UtcClock, TravelSessionState, DeliveryStatus
from app.frontend_experience import PublicProgressRecorder, PublicProgressPage

from .errors import (WebApplicationUsageError, WebApplicationDependencyError,
    WebApplicationError)
from .models import (WebVisitorContext, WebSessionView, WebAdmissionView, WebStartRequest,
    WebClarifyRequest, WebConfirmRequest, WebHotelRequest, WebControlRequest,
    WebCommandReceipt, WebRunView, WebHistoryPage, WebResultView, WebResultDay,
    WebSummaryView, WebSamplesView, WebInputIssue, WebDayReading, fresh, utc)
from .settings import WebApplicationSettings
from .security import ip_hash
from .store import WebMetadataStore, DecisionError, RequestRecord
from .summary import SummaryFinalizer
from .presentation import interaction_card, hotel_candidates

_LOG = logging.getLogger(__name__)
_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(slots=True)
class _Binding:
    run_id: UUID
    trace_id: UUID
    kind: str
    expected_turn: UUID | None
    allow_internal: bool
    active: bool = True


class RunBoundExtractionInvocationSink:
    def __init__(self, *, diagnostics: DemoDiagnostics):
        self._diagnostics = diagnostics
        self._binding = ContextVar("web_extraction_binding", default=None)
        self._slot_validator = None

    def _set_slot_validator(self, callback):
        if self._slot_validator is not None:
            raise WebApplicationUsageError()
        self._slot_validator = callback

    @contextmanager
    def bind(self, *, run_id: UUID, trace_id: UUID,
             kind: Literal["initial", "resume", "recover"], expected_turn_id: UUID | None,
             allow_internal_turn: bool):
        if (type(run_id) is not UUID or type(trace_id) is not UUID or
                kind not in {"initial", "resume", "recover"} or
                (expected_turn_id is not None and type(expected_turn_id) is not UUID) or
                type(allow_internal_turn) is not bool or
                (expected_turn_id is not None and allow_internal_turn) or
                (self._slot_validator is not None and not self._slot_validator(run_id, trace_id))):
            raise WebApplicationUsageError()
        binding = _Binding(run_id, trace_id, kind, expected_turn_id, allow_internal_turn)
        token = self._binding.set(binding)
        try:
            yield
        finally:
            binding.active = False
            self._binding.reset(token)

    def accept(self, event: ExtractionInvocationEvent) -> None:
        try:
            # Numeric Literal validation otherwise treats True as the integer 1.
            if type(event) is not ExtractionInvocationEvent or type(event.semantic_call_number) is not int:
                raise ValueError()
            event = fresh(event, ExtractionInvocationEvent)
            binding = self._binding.get()
            if binding is None or not binding.active or (self._slot_validator is not None and
                    not self._slot_validator(binding.run_id, binding.trace_id)):
                raise ValueError()
            if binding.expected_turn is None:
                if not binding.allow_internal:
                    raise ValueError()
                binding.expected_turn = event.turn_id
            if event.turn_id != binding.expected_turn:
                raise ValueError()
        except Exception:
            raise WebApplicationUsageError() from None
        with self._diagnostics.operation(run_id=binding.run_id, trace_id=binding.trace_id,
                kind=binding.kind, turn_id=event.turn_id):
            self._diagnostics.accept(event)


@dataclass(frozen=True)
class WebApplicationDependencies:
    artifacts: ArtifactPersistenceRuntime
    durable: DurableExecutionRuntime
    definition: DemoWorkflowDefinition
    diagnostics: DemoDiagnostics
    extraction_sink: RunBoundExtractionInvocationSink
    result_service: ResultFormattingService
    store: WebMetadataStore
    clock: UtcClock
    disk_probe: Callable[[], int]
    progress: PublicProgressRecorder | None = None


class WebApplicationService:
    def __init__(self, *, settings: WebApplicationSettings, dependencies: WebApplicationDependencies):
        try:
            self._settings = fresh(settings, WebApplicationSettings)
            if type(dependencies) is not WebApplicationDependencies or dependencies.extraction_sink._diagnostics is not dependencies.diagnostics:
                raise ValueError()
            self._deps = dependencies
            self._loop = asyncio.get_running_loop()
            self._closed = self._closing = False
            self._slot_run = self._slot_trace = self._slot_task = None
            self._jobs = set()
            self._posts = set()
            self._maintenance = {}
            self._preparations = set()
            self._observed = set()
            self._summaries = SummaryFinalizer(dependencies=dependencies, on_release=self._observed.discard)
            self._snapshots = ContextVar("web_safe_snapshot", default=False)
            dependencies.extraction_sink._set_slot_validator(lambda run, trace:
                self._slot_run == run and self._slot_trace == trace)
        except Exception:
            raise WebApplicationUsageError() from None

    def __repr__(self):
        return "WebApplicationService()"

    def _check(self):
        if self._closed or asyncio.get_running_loop() is not self._loop:
            raise WebApplicationUsageError()

    def _try_slot(self, run_id, trace_id=None):
        if self._closing:
            raise DecisionError("service_closing")
        if self._slot_run is not None:
            raise DecisionError("busy")
        if len(self._posts) >= 8:
            raise DecisionError("capacity_unavailable")
        self._slot_run, self._slot_trace, self._slot_task = run_id, trace_id, asyncio.current_task()

    def _release_slot(self, run_id, task):
        if self._slot_run == run_id and self._slot_task is task:
            self._slot_run = self._slot_trace = self._slot_task = None

    def _execution_stopped(self, run_id):
        # Preparing a command may reserve the same run's slot, but it has not
        # entered M11. Only a registered, unfinished business task is active.
        return (self._slot_run != run_id or self._slot_task not in self._jobs
            or self._slot_task.done())

    def _background(self, *, run_id, trace_id, record, work):
        task = self._loop.create_task(work)
        self._slot_run, self._slot_trace, self._slot_task = run_id, trace_id, task
        self._jobs.add(task)
        self._observed.add(run_id)
        def done(finished):
            self._jobs.discard(finished)
            self._release_slot(run_id, finished)
            cancelled = finished.cancelled()
            error = None if cancelled else finished.exception()
            safe_absent = not cancelled and (error is None or isinstance(error,
                (DurableExecutionUsageError, DurableExecutionConfigurationError)))
            if error is not None:
                _LOG.error("workflow_execution_failed")
            # Consume exceptions and keep post-processing strongly owned without retaining commands/results.
            if not self._closed:
                post = self._loop.create_task(self._after_execution(record=record, safe_absent=safe_absent))
                self._posts.add(post)
                post.add_done_callback(self._post_done)
        task.add_done_callback(done)
        return task

    def _post_done(self, task):
        self._posts.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOG.error("workflow_metadata_reconciliation_failed")

    async def _capacity(self):
        await self._deps.store.healthy()
        try:
            snapshot = await self._deps.artifacts.get_capacity_snapshot()
            free = self._deps.disk_probe()
            if type(free) is not int or not 0 <= free <= 2**63-1:
                raise ValueError()
        except Exception:
            raise WebApplicationDependencyError() from None
        return (free >= self._settings.min_free_disk_bytes and
            snapshot.artifact_count < snapshot.max_artifacts and
            snapshot.total_content_bytes < snapshot.max_total_content_bytes and len(self._posts) < 8)

    async def bootstrap_session(self, *, cookie: str | None) -> tuple[WebSessionView, str | None]:
        self._check()
        owner, raw = await self._deps.store.bootstrap(cookie=cookie)
        return WebSessionView(schema_version="1.0", csrf_token=self._deps.store.csrf(owner=owner),
            expires_at=owner.expires_at, visitor_ttl_seconds=2592000), raw

    async def _authenticate(self, *, cookie):
        self._check()
        return await self._deps.store.validate_cookie(cookie=cookie)

    def _verify_csrf(self, *, owner, supplied):
        self._check()
        return self._deps.store.verify_csrf(owner=owner, supplied=supplied)

    async def get_admission(self, *, owner: WebVisitorContext, client_ip: str) -> WebAdmissionView:
        self._check()
        owner = await self._deps.store.validate_owner(owner=owner)
        digest = ip_hash(client_ip)
        if self._closing:
            quota = await self._deps.store.quota(owner=owner, ip=digest)
            reason = "service_closing"
        else:
            capacity = await self._capacity()
            quota = await self._deps.store.quota(owner=owner, ip=digest)
            reason = ("capacity_unavailable" if not capacity else "busy" if self._slot_run is not None
                else "quota_exceeded" if quota.retry_after_seconds else None)
        return WebAdmissionView(schema_version="1.0", can_start=reason is None,
            global_remaining=quota.global_remaining, visitor_remaining=quota.visitor_remaining,
            ip_remaining=quota.ip_remaining, reason=reason,
            retry_after_seconds=quota.retry_after_seconds if reason == "quota_exceeded" else None)

    def _request_copy(self, request, expected):
        try:
            return fresh(request, expected)
        except Exception:
            raise DecisionError("invalid_request") from None

    async def _existing(self, *, owner, request, kind, run_id=None, generation=None):
        fingerprint = self._deps.store.fingerprint(route=kind, run_id=run_id, generation=generation,
            body=request.model_dump(mode="json"))
        record = await self._deps.store.get_request(owner=owner, request_id=request.request_id)
        if record is not None:
            self._deps.store.compare(record, kind=kind, fingerprint=fingerprint, run_id=run_id)
        return record, fingerprint

    async def get_request(self, *, owner: WebVisitorContext, request_id: UUID) -> WebCommandReceipt:
        self._check()
        record = await self._deps.store.get_request(owner=owner, request_id=request_id)
        if record is None:
            raise DecisionError("not_found")
        return record.receipt()

    async def _reconcile(self, *, record: RequestRecord, safe_absent=False):
        if record.kind != "start" or record.status not in {"claimed", "uncertain"}:
            return record
        try:
            await self._deps.durable.get_run(run_id=record.run_id)
        except DurableExecutionRunNotFoundError:
            if self._slot_run == record.run_id:
                return record
            if safe_absent and self._slot_run != record.run_id:
                return await self._deps.store.mark_request(record=record, status="rejected",
                    reason="dependency_unavailable", release_admission=True)
            return await self._deps.store.mark_request(record=record, status="uncertain", reason="admission_unconfirmed")
        except Exception:
            return await self._deps.store.mark_request(record=record, status="uncertain", reason="admission_unconfirmed")
        return await self._deps.store.mark_request(record=record, status="dispatched")

    async def _startup_reconcile(self):
        self._check()
        after = None
        while True:
            records = await self._deps.store.unconfirmed(after=after)
            if not records:
                return
            for record in records:
                await self._reconcile(record=record, safe_absent=False)
            after = records[-1].request_id

    async def start(self, *, owner: WebVisitorContext, request: WebStartRequest, client_ip: str) -> WebCommandReceipt:
        self._check()
        request = self._request_copy(request, WebStartRequest)
        record, fingerprint = await self._existing(owner=owner, request=request, kind="start")
        if record is not None:
            record = await self._reconcile(record=record)
            return record.receipt()
        run_id, trace_id, turn_id = uuid4(), uuid4(), uuid4()
        parent = asyncio.current_task()
        self._try_slot(run_id, trace_id)
        transferred = False
        created = False
        try:
            if not await self._capacity():
                raise DecisionError("capacity_unavailable")
            if self._closing:
                raise DecisionError("service_closing")
            record, created = await self._deps.store.reserve(owner=owner, request_id=request.request_id,
                run_id=run_id, fingerprint=fingerprint, ip=ip_hash(client_ip))
            if not created:
                return (await self._reconcile(record=record)).receipt()
            if self._closing:
                await self._deps.store.mark_request(record=record, status="rejected", reason="service_closing", release_admission=True)
                raise DecisionError("service_closing")
            turn = RequirementTurnInput(schema_version="1.0", turn_id=turn_id, user_message=request.user_message,
                session_date=utc(self._deps.clock.now()).astimezone(_SHANGHAI).date(), timezone="Asia/Shanghai",
                intent=RequirementIntent.INITIAL, current=RequirementDomainState(schema_version="1.1"))
            command = DemoStartCommand(schema_version="1.0", kind="start", turn=turn)
            self._deps.diagnostics.start_run(run_id=run_id, trace_id=trace_id)
            self._background(run_id=run_id, trace_id=trace_id, record=record,
                work=self._start_job(record=record, trace_id=trace_id, turn_id=turn_id, command=command))
            transferred = True
        except asyncio.CancelledError:
            if created and not transferred:
                await self._deps.store.mark_request(record=record, status="rejected",
                    reason="service_closing" if self._closing else "dependency_unavailable", release_admission=True)
                await self._release_context(record.run_id)
            raise
        except Exception:
            if created and not transferred:
                try:
                    await self._deps.store.mark_request(record=record, status="rejected",
                        reason="dependency_unavailable", release_admission=True)
                finally:
                    await self._release_context(record.run_id)
            raise
        finally:
            if not transferred:
                self._release_slot(run_id, parent)
        # Only the bounded confirmation wait belongs to this caller, never the execution.
        deadline = self._loop.time() + 2.0
        while self._loop.time() < deadline:
            try:
                async with asyncio.timeout(max(0.001, deadline - self._loop.time())):
                    record = await self._reconcile(record=record)
                if record.status == "dispatched":
                    return record.receipt()
                if record.status == "rejected":
                    raise DecisionError(record.reason)
            except (WebApplicationDependencyError, TimeoutError):
                pass
            await asyncio.sleep(min(0.05, max(0, deadline - self._loop.time())))
        raise DecisionError("admission_unconfirmed")

    async def _start_job(self, *, record, trace_id, turn_id, command):
        with self._progress_operation(record.run_id, trace_id), self._deps.diagnostics.operation(run_id=record.run_id, trace_id=trace_id, kind="initial", turn_id=turn_id):
            with self._deps.extraction_sink.bind(run_id=record.run_id, trace_id=trace_id, kind="initial",
                    expected_turn_id=turn_id, allow_internal_turn=False):
                return await self._deps.durable.start(run_id=record.run_id, trace_id=trace_id, command=command)

    def _progress_operation(self, run_id, trace_id):
        progress = self._deps.progress
        return nullcontext() if progress is None else progress.operation(run_id=run_id, trace_id=trace_id,
            valid=lambda: self._slot_run == run_id and self._slot_trace == trace_id
                and self._slot_task is not None and not self._slot_task.done())

    async def _save_public_requirement(self, view):
        progress = self._deps.progress
        if progress is None or not self._execution_stopped(view.run_id):
            return
        terminal = view.lifecycle == "closed" and view.activity == "terminal"
        if not terminal and not (view.lifecycle == "open" and view.activity == "awaiting_user" and not view.cancel_requested):
            return
        def same(current):
            return (current.run_id, current.trace_id, current.lifecycle, current.activity,
                current.pause_generation, current.delivery_status, current.terminal_code, current.cancel_requested) == (
                view.run_id, view.trace_id, view.lifecycle, view.activity, view.pause_generation,
                view.delivery_status, view.terminal_code, view.cancel_requested)
        try:
            state = fresh(await self._deps.durable.get_state(run_id=view.run_id), TravelSessionState)
            current = fresh(await self._deps.durable.get_run(run_id=view.run_id), DurableRunView)
            if not self._execution_stopped(view.run_id) or not same(current) or (state.run_id, state.trace_id) != (view.run_id, view.trace_id):
                return
            if terminal and (state.terminal_code != view.terminal_code or state.delivery_status != view.delivery_status):
                return
            with progress.operation(run_id=view.run_id, trace_id=view.trace_id,
                    valid=lambda: self._execution_stopped(view.run_id)):
                delivery = view.delivery_status.value
                await progress.record_requirement(state=state,
                    confirmed=type(state.requirement_outcome) is ConfirmedTravelRequirement,
                    event_key=f"requirement:{view.pause_generation or 0}:{delivery}:{view.terminal_code or 'pending'}")
        except (DurableExecutionRunBusyError, DurableExecutionCancellationPendingError):
            return
        except Exception:
            # Optional display records cannot affect terminal cleanup or a new pause.
            try:
                async with asyncio.timeout(0.04):
                    await progress.store.mark_limited(run_id=view.run_id, trace_id=view.trace_id)
            except Exception:
                pass
        if terminal:
            try:
                current = fresh(await self._deps.durable.get_run(run_id=view.run_id), DurableRunView)
                if self._execution_stopped(view.run_id) and same(current):
                    with progress.operation(run_id=view.run_id, trace_id=view.trace_id,
                            valid=lambda: self._execution_stopped(view.run_id)):
                        await progress.settle(run_id=view.run_id, trace_id=view.trace_id)
            except Exception:
                pass

    async def _owned_view(self, *, owner, run_id):
        await self._deps.store.owns(owner=owner, run_id=run_id)
        try:
            return fresh(await self._deps.durable.get_run(run_id=run_id), DurableRunView)
        except DurableExecutionRunNotFoundError:
            raise DecisionError("not_found") from None
        except Exception:
            raise WebApplicationDependencyError() from None

    async def _after_execution(self, *, record, safe_absent):
        if record.kind == "start":
            try:
                await self._reconcile(record=record, safe_absent=safe_absent)
            except Exception:
                # Admission metadata is independent of the already stopped
                # workflow. Its write failure cannot skip terminal cleanup.
                _LOG.error("workflow_metadata_reconciliation_failed")
        try:
            view = fresh(await self._deps.durable.get_run(run_id=record.run_id), DurableRunView)
        except DurableExecutionRunNotFoundError:
            if safe_absent:
                await self._release_context(record.run_id)
            return
        except Exception:
            # Without a reliable public state, retain the context for the
            # owner's verified shutdown and stop admitting more executions.
            self._closing = True
            raise WebApplicationDependencyError() from None
        await self._save_public_requirement(view)
        if view.lifecycle == "closed" and view.activity == "terminal":
            await self._summaries.begin(view=view, execution_stopped=self._execution_stopped(view.run_id))
        elif view.lifecycle == "blocked" or view.activity in {"blocked", "incompatible"}:
            await self._release_context(view.run_id)

    async def _release_context(self, run_id):
        try:
            self._deps.diagnostics.release_run(run_id=run_id)
        finally:
            await self._deps.definition.release_run(run_id)
            self._observed.discard(run_id)

    async def resume(self, *, owner: WebVisitorContext, run_id: UUID,
                     request: WebClarifyRequest | WebConfirmRequest | WebHotelRequest) -> WebCommandReceipt:
        self._check()
        kind = {WebClarifyRequest: "clarify", WebConfirmRequest: "confirm", WebHotelRequest: "hotel"}.get(type(request))
        if kind is None:
            raise DecisionError("invalid_request")
        request = self._request_copy(request, type(request))
        await self._deps.store.owns(owner=owner, run_id=run_id)
        record, fingerprint = await self._existing(owner=owner, request=request, kind=kind,
            run_id=run_id, generation=request.pause_generation)
        if record is not None:
            return record.receipt()
        view = await self._owned_view(owner=owner, run_id=run_id)
        parent = asyncio.current_task()
        self._try_slot(run_id, view.trace_id)
        transferred = False
        record = None
        try:
            await self._deps.store.healthy()
            if view.activity != "awaiting_user" or view.lifecycle != "open" or view.pause_generation != request.pause_generation or view.cancel_requested:
                raise DecisionError("stale_pause")
            record, created = await self._deps.store.claim_control(owner=owner, request_id=request.request_id,
                run_id=run_id, kind=kind, fingerprint=fingerprint, generation=request.pause_generation)
            if not created:
                return record.receipt()
            token = await self._deps.store.read_pause(run_id=run_id, generation=request.pause_generation,
                digest=self._deps.definition.contract.contract_digest)
            state = fresh(await self._deps.durable.get_state(run_id=run_id), TravelSessionState)
            current = fresh(await self._deps.durable.get_run(run_id=run_id), DurableRunView)
            if current.activity != "awaiting_user" or current.pause_generation != request.pause_generation:
                raise DecisionError("stale_pause")
            card = await interaction_card(state=state, repository=self._deps.artifacts.repository)
            if kind != {"clarification": "clarify", "confirmation": "confirm", "hotel": "hotel"}[card.kind]:
                raise DecisionError("stale_pause")
            turn_id, internal = None, False
            if kind == "clarify":
                turn_id = uuid4()
                turn = RequirementTurnInput(schema_version="1.0", turn_id=turn_id, user_message=request.user_message,
                    session_date=utc(self._deps.clock.now()).astimezone(_SHANGHAI).date(), timezone="Asia/Shanghai",
                    intent=RequirementIntent.SUPPLEMENT, current=state.requirement_state)
                command = DemoRequirementCommand(schema_version="1.0", kind="requirement",
                    resume=RequirementClarificationResume(turn=turn), map_requested=False)
            elif kind == "confirm":
                internal = request.decision == "revise"
                command = DemoRequirementCommand(schema_version="1.0", kind="requirement",
                    resume=RequirementConfirmationResume(action=RequirementConfirmation(decision=request.decision,
                        revision_text=request.revision_text)), map_requested=request.map_requested)
            else:
                ref, candidates = await hotel_candidates(state=state, repository=self._deps.artifacts.repository)
                if request.decision == "select" and request.ordinal > len(candidates):
                    raise DecisionError("invalid_request")
                action = HotelDeclineCommand() if request.decision == "decline" else HotelSelectCommand(selected_uid=candidates[request.ordinal - 1].uid)
                command = DemoHotelCommand(schema_version="1.0", kind="hotel", candidate_set_ref=ref, action=action)
            if self._closing or self._closed:
                raise DecisionError("service_closing")
            self._background(run_id=run_id, trace_id=view.trace_id, record=record,
                work=self._control_job(record=record, trace_id=view.trace_id, operation="resume",
                    command=command, token=token, turn_id=turn_id, internal=internal))
            transferred = True
            return WebCommandReceipt(schema_version="1.0", request_id=request.request_id, run_id=run_id,
                status="dispatched", reason=None)
        except asyncio.CancelledError:
            if record is not None and not transferred:
                await self._deps.store.mark_request(record=record, status="rejected",
                    reason="service_closing" if self._closing else "dependency_unavailable")
            raise
        except DecisionError as error:
            if record is not None and not transferred:
                await self._deps.store.mark_request(record=record, status="rejected", reason=error.code)
            raise
        except (DurableExecutionRunBusyError, DurableExecutionCancellationPendingError):
            if record is not None and not transferred:
                await self._deps.store.mark_request(record=record, status="rejected", reason="stale_pause")
            raise DecisionError("stale_pause") from None
        finally:
            if not transferred:
                self._release_slot(run_id, parent)

    async def _control_job(self, *, record, trace_id, operation, command=None, token=None, turn_id=None, internal=False):
        try:
            await self._deps.store.mark_request(record=record, status="dispatched")
            with self._progress_operation(record.run_id, trace_id), self._deps.diagnostics.operation(run_id=record.run_id, trace_id=trace_id, kind=operation, turn_id=turn_id):
                with self._deps.extraction_sink.bind(run_id=record.run_id, trace_id=trace_id, kind=operation,
                        expected_turn_id=turn_id, allow_internal_turn=internal):
                    if operation == "recover":
                        return await self._deps.durable.recover(run_id=record.run_id)
                    return await self._deps.durable.resume(run_id=record.run_id, pause_token=token, command=command)
        except (DurableExecutionResumeRejectedError, DurableExecutionRunBusyError, DurableExecutionCancellationPendingError):
            await self._deps.store.mark_request(record=record, status="rejected", reason="stale_pause")
            raise
        except Exception:
            await self._deps.store.mark_request(record=record, status="uncertain", reason="dependency_unavailable")
            raise

    async def recover(self, *, owner: WebVisitorContext, run_id: UUID, request: WebControlRequest) -> WebCommandReceipt:
        self._check()
        request = self._request_copy(request, WebControlRequest)
        view = await self._owned_view(owner=owner, run_id=run_id)
        record, fingerprint = await self._existing(owner=owner, request=request, kind="recover", run_id=run_id)
        if record is not None:
            return record.receipt()
        if view.activity != "recovery_required" or view.lifecycle != "open":
            raise DecisionError("recovery_unavailable")
        parent = asyncio.current_task()
        self._try_slot(run_id, view.trace_id)
        transferred = False
        record = None
        try:
            await self._deps.store.healthy()
            record, created = await self._deps.store.claim_control(owner=owner, request_id=request.request_id,
                run_id=run_id, kind="recover", fingerprint=fingerprint, generation=None)
            if not created:
                return record.receipt()
            if self._closing or self._closed:
                raise DecisionError("service_closing")
            self._background(run_id=run_id, trace_id=view.trace_id, record=record,
                work=self._control_job(record=record, trace_id=view.trace_id, operation="recover", internal=True))
            transferred = True
            return WebCommandReceipt(schema_version="1.0", request_id=request.request_id, run_id=run_id, status="dispatched", reason=None)
        except asyncio.CancelledError:
            if record is not None and not transferred:
                await self._deps.store.mark_request(record=record, status="rejected",
                    reason="service_closing" if self._closing else "dependency_unavailable")
            raise
        except DecisionError as error:
            if record is not None and not transferred:
                await self._deps.store.mark_request(record=record, status="rejected", reason=error.code)
            raise
        finally:
            if not transferred:
                self._release_slot(run_id, parent)

    async def cancel(self, *, owner: WebVisitorContext, run_id: UUID, request: WebControlRequest) -> WebCommandReceipt:
        self._check()
        request = self._request_copy(request, WebControlRequest)
        view = await self._owned_view(owner=owner, run_id=run_id)
        record, fingerprint = await self._existing(owner=owner, request=request, kind="cancel", run_id=run_id)
        if record is not None:
            return record.receipt()
        if self._closing:
            raise DecisionError("service_closing")
        if len(self._posts) + len(self._preparations) >= 8:
            raise DecisionError("busy")
        parent = asyncio.current_task()
        self._preparations.add(parent)
        try:
            record, created = await self._deps.store.claim_control(owner=owner, request_id=request.request_id,
                run_id=run_id, kind="cancel", fingerprint=fingerprint, generation=None)
            if not created:
                return record.receipt()
            if self._closing or self._closed:
                await self._deps.store.mark_request(record=record, status="rejected", reason="service_closing")
                raise DecisionError("service_closing")
            task = self._loop.create_task(self._cancel_intent(record=record, trace_id=view.trace_id))
            self._posts.add(task)
            task.add_done_callback(self._post_done)
        finally:
            self._preparations.discard(parent)
        return await asyncio.shield(task)

    async def _cancel_intent(self, *, record, trace_id):
        run_id = record.run_id
        try:
            receipt = await self._deps.durable.request_cancel(run_id=run_id)
            if receipt.status == "blocked":
                return (await self._deps.store.mark_request(record=record, status="rejected", reason="recovery_unavailable")).receipt()
            if receipt.status == "requested" and run_id not in self._maintenance and not self._closing:
                # The intent is known to be durable. Establish maintenance
                # before a separate metadata write can fail or be cancelled.
                task = self._loop.create_task(self._maintain_cancel(record=record, trace_id=trace_id))
                self._maintenance[run_id] = task
                task.add_done_callback(lambda done, run=run_id: self._maintenance_done(run, done))
            record = await self._deps.store.mark_request(record=record, status="dispatched")
            if receipt.status in {"cancelled", "already_terminal"} and self._execution_stopped(run_id):
                await self._after_execution(record=record, safe_absent=False)
            return record.receipt()
        except Exception:
            try:
                await self._deps.store.mark_request(record=record, status="uncertain", reason="dependency_unavailable")
            except Exception:
                _LOG.error("cancel_metadata_unconfirmed")
            raise WebApplicationDependencyError() from None

    def _maintenance_done(self, run, task):
        self._maintenance.pop(run, None)
        if not task.cancelled() and task.exception() is not None:
            _LOG.error("cancel_maintenance_failed")

    async def _settle_job(self, *, run_id):
        return await self._deps.durable.settle_cancel(run_id=run_id)

    async def _maintain_cancel(self, *, record, trace_id):
        attempt = 0
        while not self._closing:
            if self._slot_run is None:
                try:
                    self._try_slot(record.run_id, trace_id)
                except DecisionError as error:
                    if error.code == "service_closing":
                        return
                    if error.code not in {"busy", "capacity_unavailable"}:
                        raise
                else:
                    task = self._background(run_id=record.run_id, trace_id=trace_id, record=record,
                        work=self._settle_job(run_id=record.run_id))
                    try:
                        receipt = await asyncio.shield(task)
                        if receipt.status in {"cancelled", "already_terminal", "blocked"}:
                            return
                    except (DurableExecutionRunBusyError, DurableExecutionCancellationPendingError):
                        pass
            await asyncio.sleep((1, 2, 4, 5)[min(attempt, 3)])
            attempt += 1

    @contextmanager
    def _snapshot_scope(self):
        token = self._snapshots.set(True)
        try:
            yield
        finally:
            self._snapshots.reset(token)

    async def _project(self, *, view, details):
        card, actions, available = None, [], False
        if view.lifecycle == "open" and view.activity not in {"blocked", "incompatible"}:
            actions.append("cancel")
            if view.activity == "recovery_required":
                actions.append("recover")
            if details and view.activity == "awaiting_user" and not view.cancel_requested:
                try:
                    await self._deps.store.read_pause(run_id=view.run_id, generation=view.pause_generation,
                        digest=self._deps.definition.contract.contract_digest)
                    state = await self._deps.durable.get_state(run_id=view.run_id)
                    current = fresh(await self._deps.durable.get_run(run_id=view.run_id), DurableRunView)
                    if current.activity == "awaiting_user" and current.pause_generation == view.pause_generation and not current.cancel_requested:
                        card = await interaction_card(state=state, repository=self._deps.artifacts.repository)
                        actions.append({"clarification": "clarify", "confirmation": "confirm", "hotel": "hotel"}[card.kind])
                    view = current
                except (DurableExecutionRunBusyError, DurableExecutionCancellationPendingError):
                    view = fresh(await self._deps.durable.get_run(run_id=view.run_id), DurableRunView)
                except DecisionError as error:
                    if error.code != "pause_unavailable":
                        raise
        if details and self._deliverable(view):
            await self._load_result(view)
            available = True
        # State can change during the public state read. Derive every action
        # from the final view rather than retaining actions from an old pause.
        actions = []
        if view.lifecycle == "open" and view.activity not in {"blocked", "incompatible"}:
            actions.append("cancel")
            if view.activity == "recovery_required":
                actions.append("recover")
            if card is not None and view.activity == "awaiting_user" and not view.cancel_requested:
                actions.append({"clarification": "clarify", "confirmation": "confirm", "hotel": "hotel"}[card.kind])
            else:
                card = None
        else:
            card = None
        status = (await self._summary_view(view)).status
        input_issues = await self._input_issues(view) if details else ()
        progress = None
        if (view.lifecycle == "open" and view.activity == "executing" and not view.cancel_requested
                and view.phase is None and self._slot_run == view.run_id and self._slot_trace == view.trace_id
                and self._slot_task is not None and not self._slot_task.done()):
            progress = self._deps.diagnostics.get_active_phase(run_id=view.run_id, trace_id=view.trace_id)
        trip = None
        if self._deps.progress is not None:
            try:
                async with asyncio.timeout(0.20):
                    trip = await self._deps.progress.store.trip(run_id=view.run_id, trace_id=view.trace_id)
            except Exception:
                # Optional titles cannot change the task's status or available actions.
                pass
        return WebRunView(schema_version="1.0", run=view, card=card, actions=tuple(actions),
            result_available=available, summary_status=status, input_issues=input_issues, progress_phase=progress, trip=trip)

    async def _input_issues(self, view):
        if not (view.lifecycle == "closed" and view.activity == "terminal"
                and view.delivery_status is DeliveryStatus.FAILED
                and view.terminal_code in {"requirement_failed", "workflow_contract_failed"}):
            return ()
        try:
            state = fresh(await self._deps.durable.get_state(run_id=view.run_id), TravelSessionState)
            if (state.run_id != view.run_id or state.trace_id != view.trace_id
                    or state.terminal_code != view.terminal_code or state.delivery_status != view.delivery_status
                    or type(state.requirement_outcome) is not ConfirmedTravelRequirement):
                return ()
            issues = validate_requirement_hubs(state.requirement_outcome.requirement)
            return tuple(WebInputIssue(schema_version="1.0", field=issue.field_path,
                code=issue.code, message=issue.message) for issue in issues)
        except Exception:
            # Diagnosis is optional. Unverifiable historical state must not
            # change the task's terminal semantics or trigger recovery.
            return ()

    async def get_run(self, *, owner: WebVisitorContext, run_id: UUID) -> WebRunView:
        self._check()
        view = await self._owned_view(owner=owner, run_id=run_id)
        return await self._project(view=view, details=not self._snapshots.get())

    async def read_progress(self, *, owner: WebVisitorContext, run_id: UUID, after: int = 0) -> PublicProgressPage:
        self._check()
        view = await self._owned_view(owner=owner, run_id=run_id)
        if type(after) is not int or not 0 <= after <= 512:
            raise DecisionError("invalid_request")
        if self._deps.progress is None:
            return PublicProgressPage(run_id=run_id, items=(), latest_sequence=0, coverage="unavailable")
        try:
            return await self._deps.progress.store.read(run_id=run_id, trace_id=view.trace_id, after=after)
        except Exception:
            raise WebApplicationDependencyError() from None

    async def list_runs(self, *, owner: WebVisitorContext, cursor: str | None = None) -> WebHistoryPage:
        self._check()
        rows, next_cursor = await self._deps.store.history(owner=owner, cursor=cursor)
        semaphore = asyncio.Semaphore(2)
        async def read(row):
            async with semaphore:
                try:
                    view = await self._owned_view(owner=owner, run_id=row[1])
                    return await self._project(view=view, details=False)
                except DecisionError as error:
                    if error.code == "not_found":
                        return None
                    raise
        items = await asyncio.gather(*(read(row) for row in rows))
        return WebHistoryPage(schema_version="1.0", items=tuple(item for item in items if item is not None), next_cursor=next_cursor)

    @staticmethod
    def _deliverable(view):
        return view.lifecycle == "closed" and view.activity == "terminal" and view.delivery_status in {DeliveryStatus.DEMO_READY, DeliveryStatus.DEGRADED}

    async def _load_result(self, view):
        try:
            state = fresh(await self._deps.durable.get_state(run_id=view.run_id), TravelSessionState)
            refs = [ref for ref in state.artifact_refs if ref.artifact_type == "result_formatting.index"]
            if len(refs) != 1:
                raise ValueError()
            loaded = await self._deps.result_service.load(ResultReadRequest(schema_version="2.0", state=state, index_ref=refs[0]))
            if type(loaded) is not ResultFormattingLoaded:
                raise ValueError()
            loaded = fresh(loaded, ResultFormattingLoaded)
            return WebResultView(schema_version="1.0", result_schema_version="2.0", markdown=loaded.markdown,
                days=tuple(WebResultDay(schema_version="1.0", ordinal=day.data.day_no, markdown=day.markdown,
                    reading=WebDayReading(date=day.data.date, theme=day.data.theme, visit_count=len(day.data.visits),
                        route_count=len(day.data.route_legs),
                        transport_minutes=sum(route.scheduled_minutes for route in day.data.route_legs))) for day in loaded.days),
                delivery_status=state.delivery_status, terminal_code=state.terminal_code)
        except Exception:
            raise WebApplicationDependencyError() from None

    async def read_result(self, *, owner: WebVisitorContext, run_id: UUID) -> WebResultView:
        self._check()
        view = await self._owned_view(owner=owner, run_id=run_id)
        if not self._deliverable(view):
            raise DecisionError("result_not_ready")
        return await self._load_result(view)

    async def _summary_view(self, view):
        if view.lifecycle != "closed" or view.activity != "terminal" or view.terminal_code is None:
            return WebSummaryView(schema_version="1.0", status="not_finalized", summary=None, reason=None)
        receipt = await self._deps.store.get_summary(run_id=view.run_id, trace_id=view.trace_id, terminal_code=view.terminal_code)
        if receipt is not None:
            summary = receipt.summary()
            return WebSummaryView(schema_version="1.0", status="saved" if summary is not None else "process_history_unavailable",
                summary=summary, reason=None if summary is not None else "process_history_unavailable")
        pending = self._summaries.pending(run_id=view.run_id)
        return WebSummaryView(schema_version="1.0", status="pending_save" if pending else "not_saved", summary=None,
            reason=None if pending else "summary_not_saved")

    async def read_summary(self, *, owner: WebVisitorContext, run_id: UUID) -> WebSummaryView:
        self._check()
        return await self._summary_view(await self._owned_view(owner=owner, run_id=run_id))

    async def get_samples(self) -> WebSamplesView:
        self._check()
        try:
            root = Path(__file__).with_name("samples")
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            entries = []
            for entry in manifest["items"]:
                if entry["filename"] != "demo_trip.md":
                    raise ValueError()
                entries.append({name: value for name, value in entry.items() if name != "filename"} |
                    {"markdown": (root / "demo_trip.md").read_text(encoding="utf-8")})
            return WebSamplesView.model_validate_json(json.dumps({"schema_version":"1.0", "items":entries}, ensure_ascii=False), strict=True)
        except Exception:
            raise WebApplicationDependencyError() from None

    async def _ready(self):
        self._check()
        if self._closing:
            raise DecisionError("service_closing")
        await self._deps.store.healthy()
        await self._deps.artifacts.get_capacity_snapshot()

    async def close(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise WebApplicationUsageError()
        if self._closed:
            return
        self._closing = True
        preparing = set(self._preparations)
        if self._slot_task is not None and self._slot_task not in self._jobs:
            preparing.add(self._slot_task)
        preparing.discard(asyncio.current_task())
        preparing = {task for task in preparing if not task.done()}
        for task in preparing:
            task.cancel()
        if preparing:
            _, pending = await asyncio.wait(preparing, timeout=2.0)
            if pending:
                # Do not close borrowed resources or release the instance lock
                # while a preparation can still enter the durable runtime.
                raise WebApplicationDependencyError()
        maintenance = tuple(self._maintenance.values())
        for task in maintenance:
            task.cancel()
        if maintenance:
            _, pending = await asyncio.wait(maintenance, timeout=2.0)
            if pending:
                raise WebApplicationDependencyError()
        current = tuple(self._jobs)
        if current:
            await asyncio.wait(current, timeout=5.0)
        # Reconciliation and collection share the same eight-second window.
        deadline = self._loop.time() + 8.0
        posts = tuple(self._posts)
        if posts:
            done, pending = await asyncio.wait(posts, timeout=2.0)
            for task in pending:
                task.cancel()
            if pending:
                _, pending = await asyncio.wait(pending, timeout=2.0)
                if pending:
                    raise WebApplicationDependencyError()
        await self._summaries.before_durable_close(deadline=deadline)
        self._closed = True

    async def _after_durable_close(self):
        # Called by the sole resource owner after M11.close stopped execution.
        jobs = tuple(self._jobs)
        if jobs:
            for task in jobs:
                task.cancel()
            _, pending = await asyncio.wait(jobs, timeout=2.0)
            if pending:
                raise WebApplicationDependencyError()
        await self._summaries.after_durable_close()
        for run_id in tuple(self._observed):
            await self._release_context(run_id)
