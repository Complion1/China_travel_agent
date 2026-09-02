"""Module 08 对 Module 13 暴露的最小异步端口。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from app.llm_runtime import ModelRole, PromptEnvelope, StructuredInvocationOutcome

from .models import (
    CandidateResearchInitialRequest,
    CandidateResearchOutcome,
    CandidateResearchSupplementalRequest,
)


class _CandidateResearchModelOperationPort(Protocol):
    async def ainvoke_structured(
        self, prompt: PromptEnvelope, schema: type[BaseModel]
    ) -> StructuredInvocationOutcome: ...

    def close(self) -> None: ...


class _CandidateResearchRuntimePort(Protocol):
    def start_operation(self, role: ModelRole) -> _CandidateResearchModelOperationPort: ...


class CandidateResearchPort(Protocol):
    async def research_initial(self, request: CandidateResearchInitialRequest) -> CandidateResearchOutcome: ...

    async def research_supplemental(self, request: CandidateResearchSupplementalRequest) -> CandidateResearchOutcome: ...

    async def close_run(self, run_id: UUID) -> None: ...


__all__ = ("CandidateResearchPort",)
