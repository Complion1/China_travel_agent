"""Dedicated diagnostic DTOs and scopes (M02 §19); no persistence or HTTP I/O."""
from __future__ import annotations

import base64
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Iterator, Literal, Protocol
from uuid import UUID, uuid4
from functools import wraps
import asyncio

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.llm_runtime.models import LLMErrorCode, ModelRole

UInt = Annotated[int, Field(ge=0)]
Code = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")]
SafeField = Annotated[str, StringConstraints(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_.\[\]-]+$")]
Stage = Literal["preparation", "transport", "submit_repair"]
Category = Literal["preparation", "transport", "submit_repair", "supplement_request", "format_repair"]
Purpose = Literal["extract", "repair", "research_action", "research_judgment", "planning", "run"]
Kind = Literal["request", "response", "transport_failure", "validation", "tool_result", "call_summary", "run_summary"]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class BodySnapshot(_Strict):
    encoding: Literal["utf8", "base64"]
    content: str = Field(repr=False)
    retained_size_bytes: UInt
    content_hash: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    redacted: bool

    @model_validator(mode="after")
    def _integrity(self):
        raw = self.content.encode("utf-8") if self.encoding == "utf8" else base64.b64decode(self.content, validate=True)
        if len(raw) != self.retained_size_bytes or _hash(raw) != self.content_hash:
            raise ValueError("body_integrity_invalid")
        return self


def _hash(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def body_snapshot(raw: bytes, secrets: tuple[bytes, ...] = ()) -> BodySnapshot:
    retained = raw
    for secret in secrets:
        if secret:
            retained = retained.replace(secret, b"[REDACTED]")
    try:
        content, encoding = retained.decode("utf-8"), "utf8"
    except UnicodeDecodeError:
        content, encoding = base64.b64encode(retained).decode("ascii"), "base64"
    return BodySnapshot(encoding=encoding, content=content, retained_size_bytes=len(retained), content_hash=_hash(retained), redacted=retained != raw)


class RawUsage(_Strict):
    prompt_tokens: UInt | None
    cache_hit_tokens: UInt | None
    cache_miss_tokens: UInt | None
    completion_tokens: UInt | None
    reasoning_tokens: UInt | None
    total_tokens: UInt | None
    source: Literal["provider_response", "missing"]
    complete: bool

    @model_validator(mode="after")
    def _consistent(self):
        values = (self.prompt_tokens, self.cache_hit_tokens, self.cache_miss_tokens, self.completion_tokens, self.reasoning_tokens, self.total_tokens)
        known = all(value is not None for value in values)
        consistent = known and self.prompt_tokens == self.cache_hit_tokens + self.cache_miss_tokens and self.total_tokens == self.prompt_tokens + self.completion_tokens and self.reasoning_tokens <= self.completion_tokens
        if (self.complete and not consistent) or (self.source == "missing" and any(value is not None for value in values)):
            raise ValueError("usage_complete_invalid")
        return self


def missing_usage() -> RawUsage:
    return RawUsage(prompt_tokens=None, cache_hit_tokens=None, cache_miss_tokens=None, completion_tokens=None, reasoning_tokens=None, total_tokens=None, source="missing", complete=False)


def raw_usage(raw: bytes) -> RawUsage:
    try:
        response = json.loads(raw)
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            return missing_usage()
        details = usage.get("completion_tokens_details")
        details = details if isinstance(details, dict) else {}
        fields = {
            "prompt_tokens": usage.get("prompt_tokens"),
            "cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
            "cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": usage.get("reasoning_tokens", details.get("reasoning_tokens")),
            "total_tokens": usage.get("total_tokens"),
        }
        fields = {key: value if type(value) is int and value >= 0 else None for key, value in fields.items()}
        p, hit, miss, completion, reasoning, total = fields.values()
        complete = all(value is not None for value in fields.values()) and hit + miss == p and p + completion == total and reasoning <= completion
        return RawUsage(**fields, source="provider_response", complete=bool(complete))
    except (ValueError, TypeError):
        return missing_usage()


class RequestPayload(_Strict):
    kind: Literal["request"]
    body: BodySnapshot = Field(repr=False)


class ResponsePayload(_Strict):
    kind: Literal["response"]
    body: BodySnapshot = Field(repr=False)
    http_status: Annotated[int, Field(ge=100, le=599)]
    usage: RawUsage


class TransportFailurePayload(_Strict):
    kind: Literal["transport_failure"]
    code: LLMErrorCode
    response_received: bool


class ValidationPayload(_Strict):
    kind: Literal["validation"]
    stage: Literal["sdk", "tool_schema", "batch", "policy", "submit", "controller"]
    passed: bool
    code: Code | None
    field: SafeField | None
    budget_category: Category | None


class ToolResultPayload(_Strict):
    kind: Literal["tool_result"]
    tool_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    provider_tool_call_id: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None
    ordinal: Annotated[int, Field(ge=0, le=127)]
    outcome: Literal["success", "no_result", "rejected", "cached", "not_issued", "failure"]
    feedback: BodySnapshot = Field(repr=False)
    observation_ref_json: str | None = Field(repr=False)
    budget_category: Category | None


class CallSummaryPayload(_Strict):
    kind: Literal["call_summary"]
    admitted: bool
    observed_provider_attempts: Annotated[int, Field(ge=0, le=2)]
    usage: RawUsage
    stop_code: Code | None
    records_complete: bool
    record_failure_count: UInt


class RunSummaryPayload(_Strict):
    kind: Literal["run_summary"]
    observed_admitted_calls: UInt
    observed_provider_attempts: UInt
    known_usage: RawUsage
    records_complete: bool
    record_failure_count: UInt
    missing_record_ids: tuple[UUID, ...]
    stop_code: Code | None


Payload = Annotated[RequestPayload | ResponsePayload | TransportFailurePayload | ValidationPayload | ToolResultPayload | CallSummaryPayload | RunSummaryPayload, Field(discriminator="kind")]


class InteractionRecord(_Strict):
    schema_version: Literal["1.0"]
    record_id: UUID
    run_id: UUID
    trace_id: UUID
    call_id: UUID | None
    model_operation_id: UUID | None
    captured_at: datetime
    role: ModelRole | None
    purpose: Purpose
    logical_call_index: UInt | None
    provider_attempt: Annotated[int, Field(ge=1, le=2)] | None
    request_stage: Stage | None
    budget_category: Category | None
    record_kind: Kind
    payload: Payload = Field(repr=False)

    @model_validator(mode="after")
    def _identity(self):
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset().total_seconds() != 0:
            raise ValueError("captured_at_must_be_utc")
        if self.payload.kind != self.record_kind:
            raise ValueError("record_kind_mismatch")
        if self.record_kind == "run_summary":
            if self.purpose != "run" or any(value is not None for value in (self.call_id, self.role, self.model_operation_id, self.logical_call_index, self.provider_attempt)):
                raise ValueError("run_identity_invalid")
        elif self.call_id is None or self.role is None or self.purpose == "run":
            raise ValueError("call_identity_required")
        if self.record_kind in ("request", "response", "transport_failure") and self.provider_attempt is None:
            raise ValueError("provider_attempt_required")
        if self.role != ModelRole.ITINERARY_PLANNING and (self.request_stage is not None or self.budget_category is not None):
            raise ValueError("planning_association_invalid")
        payload_category = getattr(self.payload, "budget_category", None)
        if payload_category is not None and (self.role != ModelRole.ITINERARY_PLANNING or payload_category != self.budget_category):
            raise ValueError("payload_budget_category_invalid")
        return self


class InteractionWriteResult(_Strict):
    record_id: UUID
    status: Literal["saved", "fallback", "missing"]
    code: Code | None


class InteractionCallIdentity(_Strict):
    call_id: UUID
    logical_call_index: UInt | None

    async def record_validation(self, *, stage, passed, code=None, field=None, budget_category=None):
        call = _resolve_call(self)
        if call is not None and budget_category is not None:
            call.budget_category = budget_category
        return await _emit(ValidationPayload(kind="validation", stage=stage, passed=passed, code=code, field=field, budget_category=budget_category), identity=self)

    async def record_tool_result(self, *, tool_name, ordinal, outcome, feedback, provider_tool_call_id=None, observation_ref_json=None, budget_category=None):
        if observation_ref_json is not None:
            observation_ref_json = json.dumps(json.loads(observation_ref_json), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        call = _resolve_call(self)
        category = budget_category if budget_category is not None else (None if call is None else call.budget_category)
        return await _emit(ToolResultPayload(kind="tool_result", tool_name=tool_name, provider_tool_call_id=provider_tool_call_id,
            ordinal=ordinal, outcome=outcome, feedback=_body(feedback.encode("utf-8")), observation_ref_json=observation_ref_json, budget_category=category), identity=self)


class InteractionRecorder(Protocol):
    async def append(self, record: InteractionRecord) -> InteractionWriteResult: ...
    async def read_run(self, run_id: UUID) -> tuple[InteractionRecord, ...]: ...


class _NoopRecorder:
    async def append(self, record: InteractionRecord) -> InteractionWriteResult:
        return InteractionWriteResult(record_id=record.record_id, status="missing", code="recorder_not_configured")

    async def read_run(self, run_id: UUID) -> tuple[InteractionRecord, ...]:
        return ()


class _MemoryRecorder:
    def __init__(self) -> None:
        self.records: list[InteractionRecord] = []

    async def append(self, record: InteractionRecord) -> InteractionWriteResult:
        existing = next((item for item in self.records if item.record_id == record.record_id), None)
        if existing is not None and existing != record:
            return InteractionWriteResult(record_id=record.record_id, status="missing", code="record_conflict")
        if existing is None:
            self.records.append(record)
        return InteractionWriteResult(record_id=record.record_id, status="saved", code=None)

    async def read_run(self, run_id: UUID) -> tuple[InteractionRecord, ...]:
        return tuple(sorted((item for item in self.records if item.run_id == run_id), key=lambda item: (item.captured_at, item.record_id)))


@dataclass
class _CallContext:
    identity: InteractionCallIdentity
    purpose: Purpose
    role: ModelRole | None = None
    request_stage: Stage | None = None
    budget_category: Category | None = None
    admitted: bool = False
    attempts: int = 0
    claimed: bool = False
    responses: list[RawUsage] = field(default_factory=list)
    requests: set[int] = field(default_factory=set)
    received: set[int] = field(default_factory=set)
    validated: bool = False
    processed: set[int] = field(default_factory=set)
    failures: int = 0


@dataclass
class _RunContext:
    run_id: UUID
    trace_id: UUID
    recorder: InteractionRecorder
    secrets: tuple[bytes, ...] = field(repr=False)
    calls: dict[UUID, _CallContext] = field(default_factory=dict)
    missing: list[UUID] = field(default_factory=list)
    failures: int = 0


_run_context: ContextVar[_RunContext | None] = ContextVar("interaction_run", default=None)
_call_context: ContextVar[_CallContext | None] = ContextVar("interaction_call", default=None)


class _InteractionRunScope:
    """Reusable same-process scope, owned by the composition root across pauses."""
    def __init__(self, context: _RunContext):
        self._context = context
        self._tokens = ContextVar("interaction_tokens", default=())
        self._finished = False

    def __enter__(self):
        tokens = (_run_context.set(self._context), _call_context.set(None))
        self._tokens.set((*self._tokens.get(), tokens))
        return None

    def __exit__(self, *_):
        tokens = self._tokens.get()
        run_token, call_token = tokens[-1]
        self._tokens.set(tokens[:-1])
        _call_context.reset(call_token)
        _run_context.reset(run_token)

    async def finish(self, *, stop_code: str | None, complete_history: bool = True):
        if self._finished:
            return
        calls = tuple(self._context.calls.values())
        with self:
            await _emit(RunSummaryPayload(kind="run_summary", observed_admitted_calls=sum(call.admitted for call in calls),
                observed_provider_attempts=sum(call.attempts for call in calls), known_usage=_sum_usage(tuple(usage for call in calls for usage in _call_usages(call))),
                records_complete=complete_history and self._context.failures == 0 and all(_call_complete(call) for call in calls),
                record_failure_count=self._context.failures, missing_record_ids=tuple(self._context.missing), stop_code=stop_code))
        self._finished = True


def interaction_run_context(*, run_id: UUID, trace_id: UUID, recorder: InteractionRecorder | None, secrets: tuple[bytes, ...] = ()) -> _InteractionRunScope:
    return _InteractionRunScope(_RunContext(run_id, trace_id, recorder if recorder is not None else _NoopRecorder(), secrets))


@contextmanager
def interaction_call(purpose: Purpose) -> Iterator[InteractionCallIdentity]:
    if purpose == "run":
        raise ValueError("call_purpose_invalid")
    identity = InteractionCallIdentity(call_id=uuid4(), logical_call_index=None)
    context = _CallContext(identity, purpose)
    run = _run_context.get()
    if run is not None:
        run.calls[identity.call_id] = context
    token = _call_context.set(context)
    try:
        yield identity
    finally:
        _call_context.reset(token)


def _resolve_call(identity: InteractionCallIdentity | None = None) -> _CallContext | None:
    if identity is None:
        return _call_context.get()
    run = _run_context.get()
    return None if run is None else run.calls.get(identity.call_id)


def _register_secrets(secrets: tuple[bytes, ...]) -> None:
    run = _run_context.get()
    if run is not None:
        run.secrets = tuple(dict.fromkeys((*run.secrets, *secrets)))


def _body(raw: bytes, secrets: tuple[bytes, ...] = ()) -> BodySnapshot:
    run = _run_context.get()
    return body_snapshot(raw, secrets + (() if run is None else run.secrets))


async def _emit(payload: Payload, *, identity: InteractionCallIdentity | None = None, attempt: int | None = None) -> InteractionWriteResult | None:
    run, call = _run_context.get(), _resolve_call(identity)
    if run is None or (call is None and payload.kind != "run_summary"):
        return None
    record_id = uuid4()
    try:
        record = InteractionRecord(schema_version="1.0", record_id=record_id, run_id=run.run_id, trace_id=run.trace_id,
        call_id=None if call is None else call.identity.call_id, model_operation_id=None, captured_at=datetime.now(timezone.utc),
        role=None if call is None else call.role, purpose="run" if call is None else call.purpose,
        logical_call_index=None if call is None else call.identity.logical_call_index, provider_attempt=attempt,
        request_stage=None if call is None else call.request_stage, budget_category=None if call is None else call.budget_category,
        record_kind=payload.kind, payload=payload)
        result = await run.recorder.append(record)
        if result.record_id != record.record_id:
            raise ValueError("recorder_identity_invalid")
    except Exception:
        result = InteractionWriteResult(record_id=record_id, status="missing", code="recorder_write_failed")
    if result.status != "saved":
        run.failures += 1
        if call is not None:
            call.failures += 1
    if result.status == "missing":
        run.missing.append(record_id)
    return result


def _sum_usage(usages: tuple[RawUsage, ...]) -> RawUsage:
    if not usages:
        return missing_usage()
    keys = ("prompt_tokens", "cache_hit_tokens", "cache_miss_tokens", "completion_tokens", "reasoning_tokens", "total_tokens")
    fields = {key: (sum(value for usage in usages if (value := getattr(usage, key)) is not None) if any(getattr(usage, key) is not None for usage in usages) else None) for key in keys}
    return RawUsage(**fields, source="provider_response" if any(usage.source == "provider_response" for usage in usages) else "missing", complete=all(usage.complete for usage in usages))


def _call_complete(call: _CallContext) -> bool:
    expected = set(range(1, call.attempts + 1))
    return call.validated and call.failures == 0 and call.requests == expected and call.received == expected and call.processed == expected and len(call.responses) == call.attempts


def _call_usages(call: _CallContext) -> tuple[RawUsage, ...]:
    return (*call.responses, *(missing_usage() for _ in range(max(0, call.attempts - len(call.responses)))))


def _call_identity(index: int) -> InteractionCallIdentity:
    call = _call_context.get()
    identity = InteractionCallIdentity(call_id=uuid4() if call is None else call.identity.call_id, logical_call_index=index)
    if call is not None:
        call.identity = identity
    return identity


def _mark_admitted(index: int) -> None:
    _call_identity(index)
    call = _call_context.get()
    if call is not None:
        call.admitted = True


def _begin_attempt(attempt: int) -> None:
    call = _call_context.get()
    if call is not None:
        call.attempts = attempt


async def _capture_request(raw: bytes, secrets: tuple[bytes, ...]) -> None:
    call = _call_context.get()
    if call is None or not call.admitted:
        return
    call.requests.add(call.attempts)
    await _emit(RequestPayload(kind="request", body=_body(raw, secrets)), attempt=call.attempts)


async def _capture_response(raw: bytes, status: int, secrets: tuple[bytes, ...]) -> None:
    call = _call_context.get()
    if call is None or not call.admitted:
        return
    usage = raw_usage(raw)
    call.responses.append(usage)
    call.received.add(call.attempts)
    await _emit(ResponsePayload(kind="response", body=_body(raw, secrets), http_status=status, usage=usage), attempt=call.attempts)


async def _capture_failure(code: LLMErrorCode) -> None:
    call = _call_context.get()
    if call is not None and call.attempts not in call.received:
        await _emit(TransportFailurePayload(kind="transport_failure", code=code, response_received=False), attempt=call.attempts)
        call.received.add(call.attempts)


async def _attempt_processed(code: LLMErrorCode | None) -> None:
    call = _call_context.get()
    if call is not None:
        call.processed.add(call.attempts)
        await _emit(ValidationPayload(kind="validation", stage="sdk", passed=code is None,
            code=None if code is None else code.value, field=None, budget_category=None), attempt=call.attempts)


def _observer_failure() -> None:
    run, call = _run_context.get(), _call_context.get()
    if run is not None:
        run.failures += 1
        run.missing.append(uuid4())
    if call is not None:
        call.failures += 1


def _recorded_call(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        existing = _call_context.get()
        purpose = {ModelRole.REQUIREMENT_EXTRACTION: "extract", ModelRole.CANDIDATE_RESEARCH: "research_action", ModelRole.ITINERARY_PLANNING: "planning", ModelRole.REPLANNING: "planning"}[self.role]
        with (interaction_call(purpose) if existing is None else _existing_call_scope(existing)):
            call = _call_context.get()
            if call.claimed:
                raise RuntimeError("interaction_scope_already_used")
            call.claimed, call.role = True, self.role
            call.request_stage = kwargs.get("request_stage")
            try:
                result = await method(self, *args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception:
                call.validated = True
                await _emit(ValidationPayload(kind="validation", stage="controller", passed=False, code="invocation_control_failed", field=None, budget_category=None))
                await _emit(CallSummaryPayload(kind="call_summary", admitted=call.admitted, observed_provider_attempts=call.attempts,
                    usage=_sum_usage(_call_usages(call)), stop_code="invocation_control_failed", records_complete=_call_complete(call), record_failure_count=call.failures))
                raise
            error = getattr(result, "error", None)
            code = error.code.value if error is not None else None
            call.validated = True
            await _emit(ValidationPayload(kind="validation", stage="tool_schema" if code in {"tool_arguments_invalid", "unknown_tool"} else "sdk", passed=error is None, code=code, field=None, budget_category=None))
            await _emit(CallSummaryPayload(kind="call_summary", admitted=call.admitted, observed_provider_attempts=call.attempts,
                usage=_sum_usage(_call_usages(call)), stop_code=code, records_complete=_call_complete(call), record_failure_count=call.failures))
            return result
    return wrapped


@contextmanager
def _existing_call_scope(call):
    yield call.identity
