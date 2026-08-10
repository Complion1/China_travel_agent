from __future__ import annotations

from app.llm_runtime import DeepSeekRuntime, ModelRole
from app.requirements import ExtractionInput

from app.requirement_extraction.events import ExtractionInvocationSink
from app.requirement_extraction.prompt_loader import (
    load_extract_prompt,
    load_repair_prompt,
)
from app.requirement_extraction.session import DeepSeekRequirementExtractionSession


class DeepSeekRequirementExtractor:
    def __init__(
        self,
        *,
        runtime: DeepSeekRuntime,
        invocation_sink: ExtractionInvocationSink,
    ) -> None:
        self._runtime = runtime
        self._sink = invocation_sink

    def start_session(
        self,
        request: ExtractionInput,
    ) -> DeepSeekRequirementExtractionSession:
        operation = self._runtime.start_operation(ModelRole.REQUIREMENT_EXTRACTION)
        return DeepSeekRequirementExtractionSession(
            request=request,
            operation=operation,
            sink=self._sink,
            extract_prompt=load_extract_prompt(),
            repair_prompt=load_repair_prompt(),
        )
