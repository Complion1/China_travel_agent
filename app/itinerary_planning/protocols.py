"""规划服务的公共 port 与最小内部运行时 port。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.llm_runtime import ModelRole

from .models import (
    ItineraryPlanningInitialRequest,
    ItineraryPlanningOutcome,
    ItineraryPlanningResumeRequest,
)


class ItineraryPlanningPort(Protocol):
    async def plan_initial(
        self, request: ItineraryPlanningInitialRequest
    ) -> ItineraryPlanningOutcome: ...

    async def plan_resume(
        self, request: ItineraryPlanningResumeRequest
    ) -> ItineraryPlanningOutcome: ...

    async def close_run(self, run_id: UUID) -> None: ...


class _ItineraryPlanningRuntimePort(Protocol):
    def start_operation(self, role: ModelRole) -> object: ...


class _ItineraryPlanningOperationPort(Protocol):
    async def ainvoke_tools(self, *args: object, **kwargs: object) -> object: ...

    async def acontinue_tools(self, *args: object, **kwargs: object) -> object: ...

    async def arepair_tools(self, *args: object, **kwargs: object) -> object: ...

    def close(self) -> None: ...


__all__ = ("ItineraryPlanningPort",)
