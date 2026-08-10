from __future__ import annotations

from typing import Literal

from app.llm_runtime import (
    LLMErrorCode,
    LLMInvocationFailure,
    LLMRuntimeError,
    ModelOperation,
    PromptEnvelope,
    StructuredInvocationSuccess,
)
from app.requirements import (
    ExtractionAttemptOutcome,
    ExtractionAttemptSucceeded,
    ExtractionInput,
    ExtractionRepairFeedback,
    ExtractionRepairRequired,
    StrictRequirementPatchDTO,
)

from app.requirement_extraction.events import (
    ExtractionInvocationEvent,
    ExtractionInvocationSink,
)
from app.requirement_extraction.prompt_loader import PROMPT_VERSION
from app.requirement_extraction.serialization import (
    serialize_extract_input,
    serialize_repair_input,
)


_SessionState = Literal[
    "new",
    "calling_extract",
    "repair_available",
    "calling_repair",
    "repair_finished",
    "terminal_runtime_failure",
]


class RequirementExtractionRuntimeError(Exception):
    def __init__(self, runtime_error: LLMRuntimeError):
        self.runtime_error = runtime_error
        super().__init__(runtime_error.code.value)


class DeepSeekRequirementExtractionSession:
    def __init__(
        self,
        *,
        request: ExtractionInput,
        operation: ModelOperation,
        sink: ExtractionInvocationSink,
        extract_prompt: str,
        repair_prompt: str,
    ) -> None:
        self._request = request
        self._operation = operation
        self._sink = sink
        self._extract_prompt = extract_prompt
        self._repair_prompt = repair_prompt
        self._state: _SessionState = "new"

    async def extract(self) -> ExtractionAttemptOutcome:
        if self._state != "new":
            raise RuntimeError("extract_already_called")
        self._state = "calling_extract"
        prompt = PromptEnvelope(
            system_text=self._extract_prompt,
            user_text=serialize_extract_input(self._request),
        )
        return await self._invoke(
            prompt=prompt,
            attempt_kind="extract",
            semantic_call_number=1,
        )

    async def repair(
        self,
        feedback: ExtractionRepairFeedback,
    ) -> ExtractionAttemptOutcome:
        if self._state == "new":
            raise RuntimeError("repair_before_extract")
        if self._state != "repair_available":
            raise RuntimeError("repair_not_allowed")
        self._state = "calling_repair"
        prompt = PromptEnvelope(
            system_text=self._repair_prompt,
            user_text=serialize_repair_input(self._request, feedback),
        )
        return await self._invoke(
            prompt=prompt,
            attempt_kind="repair",
            semantic_call_number=2,
        )

    async def _invoke(
        self,
        *,
        prompt: PromptEnvelope,
        attempt_kind: Literal["extract", "repair"],
        semantic_call_number: Literal[1, 2],
    ) -> ExtractionAttemptOutcome:
        outcome = await self._operation.ainvoke_structured(
            prompt,
            StrictRequirementPatchDTO,
        )
        final_state: _SessionState = (
            "repair_available" if attempt_kind == "extract" else "repair_finished"
        )

        if isinstance(outcome, StructuredInvocationSuccess):
            if not isinstance(outcome.value, StrictRequirementPatchDTO):
                self._state = final_state
                raise TypeError("unexpected_structured_value")
            result: ExtractionAttemptOutcome = ExtractionAttemptSucceeded(
                value=outcome.value
            )
            event = ExtractionInvocationEvent(
                turn_id=self._request.turn_id,
                prompt_version=PROMPT_VERSION,
                attempt_kind=attempt_kind,
                outcome_kind="succeeded",
                semantic_call_number=semantic_call_number,
                invocation_metadata=outcome.metadata,
                runtime_error=None,
            )
            self._state = final_state
            self._sink.accept(event)
            return result

        if not isinstance(outcome, LLMInvocationFailure):
            self._state = final_state
            raise TypeError("unexpected_structured_outcome")

        if outcome.error.code is LLMErrorCode.STRUCTURED_OUTPUT_INVALID:
            result = ExtractionRepairRequired(
                feedback=ExtractionRepairFeedback(
                    code="structured_output_invalid",
                    field_paths=(),
                )
            )
            event = ExtractionInvocationEvent(
                turn_id=self._request.turn_id,
                prompt_version=PROMPT_VERSION,
                attempt_kind=attempt_kind,
                outcome_kind="repair_required",
                semantic_call_number=semantic_call_number,
                invocation_metadata=None,
                runtime_error=None,
            )
            self._state = final_state
            self._sink.accept(event)
            return result

        event = ExtractionInvocationEvent(
            turn_id=self._request.turn_id,
            prompt_version=PROMPT_VERSION,
            attempt_kind=attempt_kind,
            outcome_kind="runtime_failure",
            semantic_call_number=semantic_call_number,
            invocation_metadata=None,
            runtime_error=outcome.error,
        )
        self._state = "terminal_runtime_failure"
        self._sink.accept(event)
        raise RequirementExtractionRuntimeError(outcome.error)
