"""Public, bounded projections of saved workflow facts."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from app.workflow_contracts import WorkflowPhase


class PublicProgressError(Exception):
    def __init__(self):
        super().__init__("public_progress_unavailable")


class PublicModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", revalidate_instances="always")
    schema_version: Literal["1.0"] = "1.0"


class PublicFact(PublicModel):
    label: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    value: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class PublicTrip(PublicModel):
    city: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    start_date: date
    end_date: date
    days: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def consistent_dates(self):
        if (self.end_date - self.start_date).days + 1 != self.days:
            raise ValueError("inconsistent travel dates")
        return self


Kind = Literal["requirement", "hotel_search", "hotel_candidates", "hotel_selection",
    "research_action", "place_observation", "research_judgment", "research_state",
    "planning_action", "hub_observation", "route_matrix", "directions", "planning_state",
    "result_day", "result_index"]
RECORD_CATALOG = {
    "requirement": (WorkflowPhase.REQUIREMENTS, "旅行需求"),
    "hotel_search": (WorkflowPhase.HOTEL_SELECTION, "酒店地点查询"),
    "hotel_candidates": (WorkflowPhase.HOTEL_SELECTION, "酒店候选"),
    "hotel_selection": (WorkflowPhase.HOTEL_SELECTION, "酒店选择"),
    "research_action": (WorkflowPhase.CANDIDATE_RESEARCH, "研究动作"),
    "place_observation": (WorkflowPhase.CANDIDATE_RESEARCH, "地点查询结果"),
    "research_judgment": (WorkflowPhase.CANDIDATE_RESEARCH, "候选用途判断"),
    "research_state": (WorkflowPhase.CANDIDATE_RESEARCH, "候选研究进展"),
    "planning_action": (WorkflowPhase.ITINERARY_PLANNING, "规划动作"),
    "hub_observation": (WorkflowPhase.ITINERARY_PLANNING, "抵离枢纽核验"),
    "route_matrix": (WorkflowPhase.ITINERARY_PLANNING, "路线矩阵核验"),
    "directions": (WorkflowPhase.ITINERARY_PLANNING, "路线核验"),
    "planning_state": (WorkflowPhase.ITINERARY_PLANNING, "行程提交校验"),
    "result_day": (WorkflowPhase.OUTPUT_FORMATTING, "攻略日块已保存"),
    "result_index": (WorkflowPhase.OUTPUT_FORMATTING, "攻略索引已保存"),
}


class PublicRecordBody(PublicModel):
    phase: WorkflowPhase
    kind: Kind
    status: Literal["proposed", "observed", "accepted", "rejected", "failed"]
    title: str = Field(min_length=1, max_length=80)
    facts: tuple[PublicFact, ...] = Field(max_length=24)
    trip: PublicTrip | None = None

    @model_validator(mode="after")
    def catalog_and_size(self):
        if (self.phase, self.title) != RECORD_CATALOG[self.kind]:
            raise ValueError("fixed record catalog required")
        if len(canonical(self)) > 16384:
            raise ValueError("public record too large")
        return self


class PublicRecord(PublicModel):
    sequence: int = Field(ge=1, le=512)
    occurred_at: datetime
    body: PublicRecordBody

    @model_validator(mode="after")
    def utc_time(self):
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != timedelta(0):
            raise ValueError("UTC required")
        return self


class PublicProgressPage(PublicModel):
    run_id: UUID
    items: tuple[PublicRecord, ...] = Field(max_length=50)
    next_after: int | None = Field(default=None, ge=1, le=512)
    latest_sequence: int = Field(ge=0, le=512)
    coverage: Literal["recorded", "limited", "unavailable"]
    settled: bool = False

    @model_validator(mode="after")
    def consistent_page(self):
        sequences = tuple(item.sequence for item in self.items)
        if sequences != tuple(sorted(set(sequences))) or (sequences and sequences[-1] > self.latest_sequence):
            raise ValueError("invalid public sequence")
        if self.next_after is not None and (not sequences or self.next_after != sequences[-1]
                or self.next_after >= self.latest_sequence):
            raise ValueError("invalid page cursor")
        if self.coverage == "unavailable" and (self.items or self.latest_sequence or self.settled):
            raise ValueError("unavailable has no saved data")
        return self


def canonical(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
