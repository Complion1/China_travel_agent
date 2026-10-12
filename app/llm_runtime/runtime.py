from __future__ import annotations

import asyncio
import inspect
import threading
import re
from functools import wraps
from collections.abc import Awaitable, Callable
from typing import Any, cast, Literal
from app.llm_runtime.interaction_recording import (
    InteractionCallIdentity, _recorded_call, _mark_admitted, _call_identity,
    _begin_attempt, _capture_failure, _attempt_processed,
)

from pydantic import BaseModel, ValidationError
from .diagnostics import record_error, validation_details, locations

from app.llm_runtime.models import (
    InvocationMetadata,
    LLMErrorCode,
    LLMInvocationFailure,
    LLMRuntimeError,
    LLMUsage,
    ModelRole,
    PromptEnvelope,
    StructuredInvocationOutcome,
    StructuredInvocationSuccess,
    ToolCallsReady,
    ToolContract,
    ToolResult,
    ToolTurnCompleted,
    ToolTurnRejected,
    ToolArgumentIssue,
    RejectedToolCall,
    ToolTurnOutcome,
    ValidatedToolCall,
)
from app.llm_runtime.profiles import ModelProfile, get_profile
from app.llm_runtime.protocols import (
    ProviderAdapter,
    ProviderFailure,
    ProviderJSONRequest,
    ProviderResponse,
    ProviderToolContinuation,
    ProviderToolRequest,
    Sleeper,
)
from app.llm_runtime.sizing import (
    canonical_json,
    canonical_request_bytes,
    check_byte_gate,
    structured_system_text,
    validate_consumer_schema,
    validate_tool_contract,
)

ProviderResult = ProviderResponse | ProviderFailure


def _tool_turn(kind: str):
    """Reject overlapping transitions without blocking the event loop."""
    def decorate(method):
        def claim(self):
            if not self._turn_lock.acquire(blocking=False):
                raise RuntimeError("tool_turn_in_progress")
            try:
                self._check_closed()
                if self._rejected_turn is not None and kind != "repair":
                    raise RuntimeError("tool_argument_repair_pending")
                if kind == "invoke" and self._tool_prompt is not None:
                    raise RuntimeError("tool_operation_already_started")
            except BaseException:
                self._turn_lock.release()
                raise
        if inspect.iscoroutinefunction(method):
            @wraps(method)
            async def asynchronous(self, *args, **kwargs):
                claim(self)
                try:
                    return await method(self, *args, **kwargs)
                except BaseException:
                    self.close()
                    raise
                finally:
                    self._turn_lock.release()
            return asynchronous
        @wraps(method)
        def synchronous(self, *args, **kwargs):
            claim(self)
            try:
                return method(self, *args, **kwargs)
            except BaseException:
                self.close()
                raise
            finally:
                self._turn_lock.release()
        return synchronous
    return decorate


def _argument_issues(error: ValidationError, schema: type[BaseModel]) -> tuple[ToolArgumentIssue, ...]:
    issues = []
    for item in validation_details(error, schema):
        code = item["type"]
        rule = ("invalid_json" if code == "json_invalid" else "missing" if code == "missing"
            else "extra" if code == "extra_forbidden" else "enum" if code in {"enum", "literal_error"}
            else "type" if code.endswith("_type") else "constraint" if code in {
                "too_short", "too_long", "greater_than", "greater_than_equal", "less_than", "less_than_equal",
                "string_too_short", "string_too_long", "string_pattern_mismatch", "multiple_of", "finite_number"}
            else "validation")
        parts = [str(p) if type(p) is int else p if re.fullmatch(r"[a-zA-Z0-9_-]+", p) else "unknown_field" for p in item["path"]]
        path = ".".join(parts) or None
        if path is not None and len(path) > 256:
            path = "field_path_too_long"
        issues.append(ToolArgumentIssue(rule=rule, field=path))
    return tuple(issues) or (ToolArgumentIssue(rule="validation", field=None),)


def _clamp_retry_after(seconds: float | None) -> float:
    if seconds is None:
        return 1.0
    return max(0.0, min(seconds, 30.0))


def _is_possible_duplicate(code: str) -> bool:
    return code in {"timeout", "transport_error", "provider_unavailable"}


def _runtime_error(
    code: LLMErrorCode,
    role: ModelRole,
    *,
    retryable: bool = False,
    possible_duplicate: bool = False,
    provider_status_code: int | None = None,
    retry_after_seconds: float | None = None,
) -> LLMRuntimeError:
    return LLMRuntimeError(
        code=code,
        role=role,
        retryable=retryable,
        possible_duplicate=possible_duplicate,
        provider_status_code=provider_status_code,
        retry_after_seconds=retry_after_seconds,
    )


def _failure(
    code: LLMErrorCode,
    role: ModelRole,
    *,
    retryable: bool = False,
    possible_duplicate: bool = False,
) -> LLMInvocationFailure:
    return LLMInvocationFailure(
        error=_runtime_error(
            code,
            role,
            retryable=retryable,
            possible_duplicate=possible_duplicate,
        )
    )


def _provider_failure_error(
    failure: ProviderFailure,
    role: ModelRole,
    possible_duplicate: bool,
) -> LLMRuntimeError:
    try:
        code = LLMErrorCode(failure.code)
    except ValueError:
        code = LLMErrorCode.MALFORMED_PROVIDER_RESPONSE
    return _runtime_error(
        code,
        role,
        retryable=failure.retryable,
        possible_duplicate=possible_duplicate,
        provider_status_code=failure.provider_status_code,
        retry_after_seconds=(
            _clamp_retry_after(failure.retry_after_seconds)
            if failure.retry_after_seconds is not None
            else None
        ),
    )


class ModelOperation:
    def __init__(self, profile: ModelProfile, provider: ProviderAdapter, sleeper: Sleeper):
        self._profile = profile
        self._provider = provider
        self._sleeper = sleeper
        self._semantic_invocations_used = 0
        self._provider_attempts_used = 0
        self._private_tool_history: list[dict[str, object]] = []
        self._tool_prompt: PromptEnvelope | None = None
        self._tool_contracts: tuple[ToolContract[Any], ...] = ()
        self._tool_definitions: tuple[dict[str, object], ...] = ()
        self._pending_call_ids: tuple[str, ...] = ()
        self._closed = False
        self._mode: str | None = None
        self._sync_lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._async_lock = asyncio.Lock()
        self._turn_lock = threading.Lock()
        self._allow_argument_repair = False
        self._rejected_turn: ToolTurnRejected | None = None

    @property
    def role(self) -> ModelRole:
        return self._profile.role

    @property
    def semantic_invocations_consumed(self) -> int:
        """Public read-only admission count, including admitted cancelled calls."""
        return self._semantic_invocations_used

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError("Operation closed")

    def _set_mode(self, mode: str) -> None:
        with self._sync_lock:
            self._check_closed()
            if self._mode is None:
                self._mode = mode
            elif self._mode != mode:
                raise RuntimeError(f"Cannot switch {self._mode} operation to {mode}")

    def _claim_semantic_sync(self) -> LLMErrorCode | None:
        with self._sync_lock:
            self._check_closed()
            if self._semantic_invocations_used >= self._profile.semantic_invocation_limit:
                return LLMErrorCode.QUOTA_EXHAUSTED
            self._semantic_invocations_used += 1
            return None

    async def _claim_semantic_async(self) -> LLMErrorCode | None:
        async with self._async_lock:
            self._check_closed()
            if self._semantic_invocations_used >= self._profile.semantic_invocation_limit:
                return LLMErrorCode.QUOTA_EXHAUSTED
            self._semantic_invocations_used += 1
            _mark_admitted(self._semantic_invocations_used - 1)
            return None

    def _record_attempt(self) -> None:
        with self._counter_lock:
            self._provider_attempts_used += 1

    @staticmethod
    def _resource_failure(response: ProviderResponse) -> ProviderFailure | None:
        if response.finish_reason != "insufficient_system_resource":
            return None
        return ProviderFailure(
            code="provider_unavailable",
            retryable=True,
            provider_status_code=None,
            retry_after_seconds=None,
        )

    def _invoke_sync(
        self,
        provider_call: Callable[[], ProviderResult],
    ) -> tuple[ProviderResponse | None, int, bool, LLMRuntimeError | None]:
        possible_duplicate = False
        for attempt in (1, 2):
            self._record_attempt()
            result = provider_call()
            if not isinstance(result, (ProviderResponse, ProviderFailure)):
                return None, attempt, possible_duplicate, _runtime_error(
                    LLMErrorCode.MALFORMED_PROVIDER_RESPONSE, self.role
                )
            if isinstance(result, ProviderResponse):
                resource_failure = self._resource_failure(result)
                if resource_failure is None:
                    return result, attempt, possible_duplicate, None
                result = resource_failure
            if not result.retryable or attempt == 2:
                return None, attempt, possible_duplicate, _provider_failure_error(
                    result, self.role, possible_duplicate
                )
            possible_duplicate = possible_duplicate or _is_possible_duplicate(result.code)
            self._sleeper.sleep(_clamp_retry_after(result.retry_after_seconds))
        raise AssertionError("unreachable retry loop")

    async def _invoke_async(
        self,
        provider_call: Callable[[], Awaitable[ProviderResult]],
    ) -> tuple[ProviderResponse | None, int, bool, LLMRuntimeError | None]:
        possible_duplicate = False
        for attempt in (1, 2):
            self._record_attempt()
            _begin_attempt(attempt)
            result = await provider_call()
            code = None
            if isinstance(result, ProviderFailure):
                try:
                    code = LLMErrorCode(result.code)
                except ValueError:
                    code = LLMErrorCode.MALFORMED_PROVIDER_RESPONSE
                await _capture_failure(code)
            elif not isinstance(result, ProviderResponse):
                code = LLMErrorCode.MALFORMED_PROVIDER_RESPONSE
                await _capture_failure(code)
            elif result.finish_reason == "insufficient_system_resource":
                code = LLMErrorCode.PROVIDER_UNAVAILABLE
            await _attempt_processed(code)
            if not isinstance(result, (ProviderResponse, ProviderFailure)):
                return None, attempt, possible_duplicate, _runtime_error(
                    LLMErrorCode.MALFORMED_PROVIDER_RESPONSE, self.role
                )
            if isinstance(result, ProviderResponse):
                resource_failure = self._resource_failure(result)
                if resource_failure is None:
                    return result, attempt, possible_duplicate, None
                result = resource_failure
            if not result.retryable or attempt == 2:
                return None, attempt, possible_duplicate, _provider_failure_error(
                    result, self.role, possible_duplicate
                )
            possible_duplicate = possible_duplicate or _is_possible_duplicate(result.code)
            await self._sleeper.async_sleep(_clamp_retry_after(result.retry_after_seconds))
        raise AssertionError("unreachable retry loop")

    def _metadata(
        self,
        response: ProviderResponse,
        attempts: int,
        possible_duplicate: bool,
    ) -> InvocationMetadata | None:
        if response.model_name != self._profile.model_name:
            return None
        if response.finish_reason not in {
            "stop",
            "tool_calls",
            "length",
            "content_filter",
            "insufficient_system_resource",
        }:
            return None
        required = {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "reasoning_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        }
        if set(response.usage) != required:
            return None
        try:
            usage = LLMUsage.model_validate(response.usage, strict=True)
            return InvocationMetadata(
                role=self.role,
                model_name=cast(Any, response.model_name),
                finish_reason=cast(Any, response.finish_reason),
                provider_attempts=attempts,
                possible_duplicate=possible_duplicate,
                usage=usage,
            )
        except ValidationError:
            return None

    def _decode_structured(
        self,
        response: ProviderResponse,
        schema: type[BaseModel],
        attempts: int,
        possible_duplicate: bool,
    ) -> StructuredInvocationOutcome:
        if response.model_name and response.model_name != self._profile.model_name:
            return _failure(LLMErrorCode.UNEXPECTED_MODEL, self.role, possible_duplicate=possible_duplicate)
        metadata = self._metadata(response, attempts, possible_duplicate)
        if metadata is None:
            return _failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE, self.role, possible_duplicate=possible_duplicate)
        if response.finish_reason == "length":
            return _failure(LLMErrorCode.OUTPUT_TRUNCATED, self.role, possible_duplicate=possible_duplicate)
        if response.finish_reason == "content_filter":
            return _failure(LLMErrorCode.CONTENT_FILTERED, self.role, possible_duplicate=possible_duplicate)
        if response.finish_reason != "stop" or response.tool_calls or response.content is None:
            return _failure(LLMErrorCode.STRUCTURED_OUTPUT_INVALID, self.role, possible_duplicate=possible_duplicate)
        try:
            validated = schema.model_validate_json(response.content, strict=True)
        except (ValidationError, TypeError, ValueError):
            return _failure(LLMErrorCode.STRUCTURED_OUTPUT_INVALID, self.role, possible_duplicate=possible_duplicate)
        return StructuredInvocationSuccess(value=validated, metadata=metadata)

    @_tool_turn("structured")
    def invoke_structured(
        self,
        prompt: PromptEnvelope,
        schema: type[BaseModel],
    ) -> StructuredInvocationOutcome:
        self._set_mode("sync")
        if validate_consumer_schema(schema):
            return _failure(LLMErrorCode.SCHEMA_UNSUPPORTED, self.role)
        json_schema = schema.model_json_schema()
        gate = check_byte_gate(
            len(canonical_request_bytes(profile=self._profile, prompt=prompt, json_schema=json_schema)),
            self._profile.max_request_utf8_bytes,
            self.role,
        )
        if gate is not None:
            return LLMInvocationFailure(error=gate)
        quota_error = self._claim_semantic_sync()
        if quota_error is not None:
            return _failure(quota_error, self.role)
        response, attempts, duplicate, error = self._invoke_sync(
            lambda: self._provider.invoke_json(
                ProviderJSONRequest(
                    system=structured_system_text(prompt.system_text, json_schema),
                    user=prompt.user_text,
                    json_schema=json_schema,
                    max_output_tokens=self._profile.max_output_tokens,
                    thinking_type=self._profile.thinking_type,
                    reasoning_effort=self._profile.reasoning_effort,
                    model=self._profile.model_name,
                    profile=self._profile,
                )
            )
        )
        if error is not None:
            return LLMInvocationFailure(error=error)
        assert response is not None
        return self._decode_structured(response, schema, attempts, duplicate)

    @_recorded_call
    @_tool_turn("structured")
    async def ainvoke_structured(
        self,
        prompt: PromptEnvelope,
        schema: type[BaseModel],
    ) -> StructuredInvocationOutcome:
        self._set_mode("async")
        if validate_consumer_schema(schema):
            return _failure(LLMErrorCode.SCHEMA_UNSUPPORTED, self.role)
        json_schema = schema.model_json_schema()
        gate = check_byte_gate(
            len(canonical_request_bytes(profile=self._profile, prompt=prompt, json_schema=json_schema)),
            self._profile.max_request_utf8_bytes,
            self.role,
        )
        if gate is not None:
            return LLMInvocationFailure(error=gate)
        quota_error = await self._claim_semantic_async()
        if quota_error is not None:
            return _failure(quota_error, self.role)
        response, attempts, duplicate, error = await self._invoke_async(
            lambda: self._provider.ainvoke_json(
                ProviderJSONRequest(
                    system=structured_system_text(prompt.system_text, json_schema),
                    user=prompt.user_text,
                    json_schema=json_schema,
                    max_output_tokens=self._profile.max_output_tokens,
                    thinking_type=self._profile.thinking_type,
                    reasoning_effort=self._profile.reasoning_effort,
                    model=self._profile.model_name,
                    profile=self._profile,
                )
            )
        )
        if error is not None:
            return LLMInvocationFailure(error=error)
        assert response is not None
        return self._decode_structured(response, schema, attempts, duplicate)

    def _prepare_tools(
        self, tools: tuple[ToolContract[Any], ...]
    ) -> tuple[tuple[dict[str, object], ...] | None, LLMErrorCode | None]:
        if not tools:
            return None, LLMErrorCode.INVALID_REQUEST
        names: set[str] = set()
        definitions: list[dict[str, object]] = []
        for tool in tools:
            if tool.name in names or validate_tool_contract(tool.name, tool.description, tool.args_schema):
                return None, LLMErrorCode.INVALID_REQUEST
            names.add(tool.name)
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.args_schema.model_json_schema(),
                    },
                }
            )
        return tuple(definitions), None

    @_tool_turn("invoke")
    def invoke_tools(
        self,
        prompt: PromptEnvelope,
        tools: tuple[ToolContract[Any], ...],
        *, allow_argument_repair: bool = False,
    ) -> ToolTurnOutcome:
        self._set_mode("sync")
        if type(allow_argument_repair) is not bool:
            return self._tool_failure(LLMErrorCode.INVALID_REQUEST)
        self._allow_argument_repair = allow_argument_repair
        definitions, preflight_error = self._prepare_tools(tools)
        if preflight_error is not None or definitions is None:
            return self._tool_failure(preflight_error or LLMErrorCode.INVALID_REQUEST)
        gate = check_byte_gate(
            len(
                canonical_request_bytes(
                    profile=self._profile,
                    prompt=prompt,
                    json_schema=None,
                    tools=definitions,
                )
            ),
            self._profile.max_request_utf8_bytes,
            self.role,
        )
        if gate is not None:
            self.close()
            return LLMInvocationFailure(error=gate)
        quota_error = self._claim_semantic_sync()
        if quota_error is not None:
            return self._tool_failure(quota_error)
        self._tool_prompt = prompt
        self._tool_contracts = tools
        self._tool_definitions = definitions
        response, attempts, duplicate, error = self._invoke_sync(
            lambda: self._provider.invoke_tools(
                ProviderToolRequest(
                    system=prompt.system_text,
                    user=prompt.user_text,
                    tools=definitions,
                    max_output_tokens=self._profile.max_output_tokens,
                    thinking_type=self._profile.thinking_type,
                    reasoning_effort=self._profile.reasoning_effort,
                    model=self._profile.model_name,
                    profile=self._profile,
                )
            )
        )
        if error is not None:
            self.close()
            return LLMInvocationFailure(error=error)
        assert response is not None
        return self._decode_tool_response(response, attempts, duplicate)

    async def _admit_tools_async(self, *, prompt, tools, definitions, history, admission_callback):
        async with self._async_lock:
            self._check_closed()
            gate = check_byte_gate(len(canonical_request_bytes(profile=self._profile, prompt=prompt,
                json_schema=None, tools=definitions, internal_tool_history=history)), self._profile.max_request_utf8_bytes, self.role)
            if gate is not None:
                return gate
            if self._semantic_invocations_used >= self._profile.semantic_invocation_limit:
                return _runtime_error(LLMErrorCode.QUOTA_EXHAUSTED, self.role)
            identity = _call_identity(self._semantic_invocations_used)
            if admission_callback is not None:
                await admission_callback(identity)
            with self._sync_lock:
                self._check_closed()
                self._semantic_invocations_used += 1
                _mark_admitted(self._semantic_invocations_used - 1)
                self._tool_prompt, self._tool_contracts, self._tool_definitions = prompt, tools, definitions
        return None

    @_recorded_call
    @_tool_turn("invoke")
    async def ainvoke_tools(
        self, prompt: PromptEnvelope, tools: tuple[ToolContract[Any], ...], *,
        admission_callback: Callable[[InteractionCallIdentity], Awaitable[None]] | None = None,
        request_stage: Literal["preparation", "transport", "submit_repair"] | None = None,
        allow_argument_repair: bool = False,
    ) -> ToolTurnOutcome:
        self._set_mode("async")
        if type(allow_argument_repair) is not bool:
            return self._tool_failure(LLMErrorCode.INVALID_REQUEST)
        self._allow_argument_repair = allow_argument_repair
        definitions, preflight_error = self._prepare_tools(tools)
        if preflight_error is not None or definitions is None:
            return self._tool_failure(preflight_error or LLMErrorCode.INVALID_REQUEST)
        gate = await self._admit_tools_async(prompt=prompt, tools=tools, definitions=definitions, history=(),
            admission_callback=admission_callback)
        if gate is not None:
            self.close()
            return LLMInvocationFailure(error=gate)
        response, attempts, duplicate, error = await self._invoke_async(
            lambda: self._provider.ainvoke_tools(
                ProviderToolRequest(
                    system=prompt.system_text,
                    user=prompt.user_text,
                    tools=definitions,
                    max_output_tokens=self._profile.max_output_tokens,
                    thinking_type=self._profile.thinking_type,
                    reasoning_effort=self._profile.reasoning_effort,
                    model=self._profile.model_name,
                    profile=self._profile,
                )
            )
        )
        if error is not None:
            self.close()
            return LLMInvocationFailure(error=error)
        assert response is not None
        return self._decode_tool_response(response, attempts, duplicate)

    def _decode_tool_response(
        self,
        response: ProviderResponse,
        attempts: int,
        possible_duplicate: bool,
    ) -> ToolTurnOutcome:
        self._check_closed()
        if response.model_name and response.model_name != self._profile.model_name:
            return self._tool_failure(LLMErrorCode.UNEXPECTED_MODEL)
        metadata = self._metadata(response, attempts, possible_duplicate)
        if metadata is None:
            return self._tool_failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)
        if response.finish_reason == "length":
            return self._tool_failure(LLMErrorCode.OUTPUT_TRUNCATED)
        if response.finish_reason == "content_filter":
            return self._tool_failure(LLMErrorCode.CONTENT_FILTERED)
        if response.finish_reason == "stop":
            if response.tool_calls or response.raw_tool_calls:
                return self._tool_failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)
            outcome = ToolTurnCompleted(final_text=response.content or "", metadata=metadata)
            self.close()
            return outcome
        if response.finish_reason != "tool_calls" or not (response.tool_calls or response.raw_tool_calls):
            return self._tool_failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)

        contracts = {tool.name: tool for tool in self._tool_contracts}
        raw_calls = response.raw_tool_calls
        if raw_calls is None:
            if self._allow_argument_repair:
                return self._tool_failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)
            try:
                raw_calls = tuple((call_id, name, canonical_json(args)) for call_id, name, args in response.tool_calls)
            except (TypeError, ValueError):
                return self._tool_failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)
        if not self._allow_argument_repair and response.raw_tool_calls is not None and len(response.tool_calls) != len(raw_calls):
            return self._tool_failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)
        if type(raw_calls) is not tuple or any(type(call) is not tuple or len(call) != 3 for call in raw_calls):
            return self._tool_failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)
        seen_ids: set[str] = set()
        validated_calls: list[ValidatedToolCall] = []
        # Entire batch protocol validation precedes ANY consumer schema checks.
        for call_id, tool_name, arguments in raw_calls:
            if type(call_id) is not str or not call_id.strip() or len(call_id) > 256 or call_id in seen_ids or type(tool_name) is not str or type(arguments) is not str:
                return self._tool_failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)
            seen_ids.add(call_id)
            contract = contracts.get(tool_name)
            if contract is None:
                return self._tool_failure(LLMErrorCode.UNKNOWN_TOOL)
        rejected_calls = []
        for call_id, tool_name, arguments in raw_calls:
            contract = contracts[tool_name]
            issues = ()
            try:
                validated_arguments = contract.args_schema.model_validate_json(
                    arguments,
                    strict=True,
                )
            except (ValidationError, TypeError, ValueError) as error:
                record_error("tool_arguments_invalid", role=self.role.value,
                             invocation=self._semantic_invocations_used,
                             tool=contract.name,
                             errors=validation_details(error, contract.args_schema) if isinstance(error, ValidationError) else [],
                             location=locations(error))
                if not isinstance(error, ValidationError):
                    return self._tool_failure(LLMErrorCode.TOOL_ARGUMENTS_INVALID)
                issues = _argument_issues(error, contract.args_schema)
            else:
                validated_calls.append(
                    ValidatedToolCall(call_id=call_id, tool_name=tool_name, arguments=validated_arguments)
                )
            rejected_calls.append(RejectedToolCall(call_id=call_id, tool_name=tool_name, issues=issues))

        if any(call.issues for call in rejected_calls):
            if not self._allow_argument_repair:
                return self._tool_failure(LLMErrorCode.TOOL_ARGUMENTS_INVALID)
            if len(rejected_calls) > 128:
                return self._tool_failure(LLMErrorCode.MALFORMED_PROVIDER_RESPONSE)
            outcome = ToolTurnRejected(error=_runtime_error(LLMErrorCode.TOOL_ARGUMENTS_INVALID, self.role),
                metadata=metadata, calls=tuple(rejected_calls))
            with self._sync_lock:
                self._check_closed()
                self._private_tool_history.append(self._assistant_history_message(response, raw_calls))
                self._rejected_turn = outcome
                self._pending_call_ids = ()
            return outcome
        with self._sync_lock:
            self._check_closed()
            self._private_tool_history.append(self._assistant_history_message(response, raw_calls))
            self._pending_call_ids = tuple(call.call_id for call in validated_calls)
            self._rejected_turn = None
        return ToolCallsReady(calls=tuple(validated_calls), metadata=metadata)

    @staticmethod
    def _assistant_history_message(response: ProviderResponse, raw_calls: tuple[tuple[str, str, str], ...]) -> dict[str, object]:
        message: dict[str, object] = {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                }
                for call_id, tool_name, arguments in raw_calls
            ],
        }
        if response.reasoning_content is not None:
            message["reasoning_content"] = response.reasoning_content
        return message

    def _validated_result_messages(
        self, results: tuple[ToolResult, ...]
    ) -> tuple[dict[str, object], ...] | None:
        if not self._pending_call_ids or len(results) != len(self._pending_call_ids):
            return None
        by_id: dict[str, ToolResult] = {}
        for result in results:
            if result.call_id in by_id or result.call_id not in self._pending_call_ids:
                return None
            by_id[result.call_id] = result
        return tuple(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": by_id[call_id].content,
            }
            for call_id in self._pending_call_ids
        )

    @_tool_turn("continue")
    def continue_tools(self, results: tuple[ToolResult, ...]) -> ToolTurnOutcome:
        self._set_mode("sync")
        result_messages = self._validated_result_messages(results)
        if result_messages is None or self._tool_prompt is None:
            return self._tool_failure(LLMErrorCode.INVALID_REQUEST)
        candidate_history = tuple(self._private_tool_history) + result_messages
        gate = check_byte_gate(
            len(
                canonical_request_bytes(
                    profile=self._profile,
                    prompt=self._tool_prompt,
                    json_schema=None,
                    tools=self._tool_definitions,
                    internal_tool_history=candidate_history,
                )
            ),
            self._profile.max_request_utf8_bytes,
            self.role,
        )
        if gate is not None:
            self.close()
            return LLMInvocationFailure(error=gate)
        quota_error = self._claim_semantic_sync()
        if quota_error is not None:
            return self._tool_failure(quota_error)
        messages = self._continuation_messages(candidate_history)
        response, attempts, duplicate, error = self._invoke_sync(
            lambda: self._provider.invoke_tool_continuation(
                ProviderToolContinuation(
                    messages=messages,
                    tools=self._tool_definitions,
                    max_output_tokens=self._profile.max_output_tokens,
                    thinking_type=self._profile.thinking_type,
                    reasoning_effort=self._profile.reasoning_effort,
                    model=self._profile.model_name,
                    profile=self._profile,
                )
            )
        )
        if error is not None:
            self.close()
            return LLMInvocationFailure(error=error)
        with self._sync_lock:
            self._check_closed()
            self._private_tool_history = list(candidate_history)
            self._pending_call_ids = ()
        assert response is not None
        return self._decode_tool_response(response, attempts, duplicate)

    @_recorded_call
    @_tool_turn("continue")
    async def acontinue_tools(
        self, results: tuple[ToolResult, ...], *, tools: tuple[ToolContract[Any], ...] | None = None,
        admission_callback: Callable[[InteractionCallIdentity], Awaitable[None]] | None = None,
        request_stage: Literal["preparation", "transport", "submit_repair"] | None = None,
    ) -> ToolTurnOutcome:
        self._set_mode("async")
        result_messages = self._validated_result_messages(results)
        if result_messages is None or self._tool_prompt is None:
            return self._tool_failure(LLMErrorCode.INVALID_REQUEST)
        effective_tools = self._tool_contracts if tools is None else tools
        definitions, preflight_error = self._prepare_tools(effective_tools)
        if preflight_error is not None or definitions is None:
            return self._tool_failure(preflight_error or LLMErrorCode.INVALID_REQUEST)
        candidate_history = tuple(self._private_tool_history) + result_messages
        gate = await self._admit_tools_async(prompt=self._tool_prompt, tools=effective_tools, definitions=definitions,
            history=candidate_history, admission_callback=admission_callback)
        if gate is not None:
            self.close()
            return LLMInvocationFailure(error=gate)
        messages = self._continuation_messages(candidate_history)
        response, attempts, duplicate, error = await self._invoke_async(
            lambda: self._provider.ainvoke_tool_continuation(
                ProviderToolContinuation(
                    messages=messages,
                    tools=self._tool_definitions,
                    max_output_tokens=self._profile.max_output_tokens,
                    thinking_type=self._profile.thinking_type,
                    reasoning_effort=self._profile.reasoning_effort,
                    model=self._profile.model_name,
                    profile=self._profile,
                )
            )
        )
        if error is not None:
            self.close()
            return LLMInvocationFailure(error=error)
        with self._sync_lock:
            self._check_closed()
            self._private_tool_history = list(candidate_history)
            self._pending_call_ids = ()
        assert response is not None
        return self._decode_tool_response(response, attempts, duplicate)

    def _repair_history(self, tools):
        if self._rejected_turn is None or self._tool_prompt is None:
            return None
        effective = self._tool_contracts if tools is None else tools
        definitions, error = self._prepare_tools(effective)
        if error is not None or definitions != self._tool_definitions:
            return None
        feedback = tuple({"role": "tool", "tool_call_id": result.call_id, "content": result.content}
            for result in self._rejected_turn.tool_results())
        return effective, definitions, tuple(self._private_tool_history) + feedback

    @_tool_turn("repair")
    def repair_tools(self, *, tools: tuple[ToolContract[Any], ...] | None = None) -> ToolTurnOutcome:
        self._set_mode("sync")
        prepared = self._repair_history(tools)
        if prepared is None:
            return self._tool_failure(LLMErrorCode.INVALID_REQUEST)
        effective, definitions, history = prepared
        gate = check_byte_gate(len(canonical_request_bytes(profile=self._profile, prompt=self._tool_prompt,
            json_schema=None, tools=definitions, internal_tool_history=history)), self._profile.max_request_utf8_bytes, self.role)
        if gate is not None:
            self.close()
            return LLMInvocationFailure(error=gate)
        quota = self._claim_semantic_sync()
        if quota is not None:
            return self._tool_failure(quota)
        response, attempts, duplicate, error = self._invoke_sync(lambda: self._provider.invoke_tool_continuation(
            ProviderToolContinuation(messages=self._continuation_messages(history), tools=definitions,
                max_output_tokens=self._profile.max_output_tokens, thinking_type=self._profile.thinking_type,
                reasoning_effort=self._profile.reasoning_effort, model=self._profile.model_name, profile=self._profile)))
        if error is not None:
            self.close()
            return LLMInvocationFailure(error=error)
        with self._sync_lock:
            self._check_closed()
            self._private_tool_history = list(history)
        return self._decode_tool_response(response, attempts, duplicate)

    @_recorded_call
    @_tool_turn("repair")
    async def arepair_tools(self, *, tools: tuple[ToolContract[Any], ...] | None = None,
        admission_callback: Callable[[InteractionCallIdentity], Awaitable[None]] | None = None,
        request_stage: Literal["preparation", "transport", "submit_repair"] | None = None) -> ToolTurnOutcome:
        self._set_mode("async")
        prepared = self._repair_history(tools)
        if prepared is None:
            return self._tool_failure(LLMErrorCode.INVALID_REQUEST)
        effective, definitions, history = prepared
        gate = await self._admit_tools_async(prompt=self._tool_prompt, tools=effective, definitions=definitions,
            history=history, admission_callback=admission_callback)
        if gate is not None:
            self.close()
            return LLMInvocationFailure(error=gate)
        response, attempts, duplicate, error = await self._invoke_async(lambda: self._provider.ainvoke_tool_continuation(
            ProviderToolContinuation(messages=self._continuation_messages(history), tools=definitions,
                max_output_tokens=self._profile.max_output_tokens, thinking_type=self._profile.thinking_type,
                reasoning_effort=self._profile.reasoning_effort, model=self._profile.model_name, profile=self._profile)))
        if error is not None:
            self.close()
            return LLMInvocationFailure(error=error)
        with self._sync_lock:
            self._check_closed()
            self._private_tool_history = list(history)
        return self._decode_tool_response(response, attempts, duplicate)

    def _continuation_messages(
        self, history: tuple[dict[str, object], ...]
    ) -> tuple[dict[str, object], ...]:
        assert self._tool_prompt is not None
        return (
            {"role": "system", "content": self._tool_prompt.system_text},
            {"role": "user", "content": self._tool_prompt.user_text},
            *history,
        )

    def _tool_failure(self, code: LLMErrorCode) -> LLMInvocationFailure:
        outcome = _failure(code, self.role)
        self.close()
        return outcome

    def close(self) -> None:
        with self._sync_lock:
            self._closed = True
            self._private_tool_history.clear()
            self._pending_call_ids = ()
            self._tool_prompt = None
            self._tool_contracts = ()
            self._tool_definitions = ()
            self._rejected_turn = None

    def __enter__(self) -> "ModelOperation":
        self._check_closed()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    async def __aenter__(self) -> "ModelOperation":
        self._check_closed()
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"ModelOperation(role={self._profile.role.value}, "
            f"semantic_used={self._semantic_invocations_used}, "
            f"provider_attempts={self._provider_attempts_used}, closed={self._closed})"
        )

    def __getstate__(self) -> None:
        raise TypeError("ModelOperation cannot be pickled")


class DeepSeekRuntime:
    def __init__(self, provider: ProviderAdapter, sleeper: Sleeper):
        self._provider = provider
        self._sleeper = sleeper
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        self._sync_close_done = False
        self._async_close_done = False

    def start_operation(self, role: ModelRole) -> ModelOperation:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Runtime closed")
        return ModelOperation(profile=get_profile(role), provider=self._provider, sleeper=self._sleeper)

    def close(self) -> None:
        with self._lifecycle_lock:
            self._closed = True
            if self._sync_close_done:
                return
            self._sync_close_done = True
        close = getattr(self._provider, "close", None)
        if callable(close):
            close()

    async def aclose(self) -> None:
        with self._lifecycle_lock:
            self._closed = True
            if self._async_close_done:
                return
            self._async_close_done = True
            self._sync_close_done = True
        close = getattr(self._provider, "aclose", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
            return
        close_sync = getattr(self._provider, "close", None)
        if callable(close_sync):
            close_sync()

    def __enter__(self) -> "DeepSeekRuntime":
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Runtime closed")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    async def __aenter__(self) -> "DeepSeekRuntime":
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Runtime closed")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return "DeepSeekRuntime()"
