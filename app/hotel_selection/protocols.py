"""Module 07 的最小人工选择端口。"""

from __future__ import annotations

from typing import Protocol

from app.requirements import ConfirmedTravelRequirement
from app.workflow_contracts import ArtifactRef

from .models import (
    HotelCandidateDiscoveryOutcome,
    HotelCandidateOperationIds,
    HotelSelectionOutcome,
    HotelSelectionRequest,
)


class HotelCandidateSelectionPort(Protocol):
    async def discover_candidates(
        self,
        confirmed: ConfirmedTravelRequirement,
        operation_ids: HotelCandidateOperationIds,
    ) -> HotelCandidateDiscoveryOutcome: ...

    async def record_selection(
        self,
        request: HotelSelectionRequest,
        *,
        active_candidate_set_ref: ArtifactRef,
    ) -> HotelSelectionOutcome: ...


__all__ = ("HotelCandidateSelectionPort",)
