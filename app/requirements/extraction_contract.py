from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Optional, Protocol, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.requirements.models import (
    ChildAge,
    HubType,
    InterestTag,
    PeopleCount,
    PositiveInt,
    RequirementFieldPath,
    RequirementIntent,
    ShortText,
    TravelPace,
    TravelRequirement,
    VerifiedFieldEvidence,
)


class StrictFieldEvidenceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    field_path: RequirementFieldPath
    text: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class StrictRequirementPatchDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    destination: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None
    arrival_hub_name: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None
    arrival_hub_type: HubType | None
    arrival_datetime: str | None
    arrival_is_approximate: bool | None
    departure_hub_name: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None
    departure_hub_type: HubType | None
    departure_datetime: str | None
    departure_is_approximate: bool | None
    sightseeing_start_date: ShortText | None
    sightseeing_end_date: ShortText | None
    sightseeing_days: PositiveInt | None
    budget_total_cny: Annotated[float, Field(gt=0)] | None
    budget_is_approximate: bool | None
    adults_count: PeopleCount | None
    children_count: PeopleCount | None
    children_ages: Annotated[list[ChildAge], Field(max_length=100)] | None
    seniors_count: PeopleCount | None
    room_count: PositiveInt | None
    room_type_requirements: ShortText | None
    mobility_constraints: ShortText | None
    dietary_restrictions: ShortText | None
    interests: list[InterestTag] | None
    pace: TravelPace | None
    agent_decides_interests: bool | None
    agent_decides_pace: bool | None
    transport_preferences: ShortText | None
    hotel_preferences: ShortText | None
    must_visit_places: list[ShortText] | None
    avoid_places: list[ShortText] | None
    daily_time_preferences: ShortText | None
    free_text_notes: ShortText | None
    is_correction: bool
    evidence: list[StrictFieldEvidenceDTO]

    @model_validator(mode="after")
    def reject_non_extractable_evidence_paths(self) -> "StrictRequirementPatchDTO":
        extractable = {RequirementFieldPath(path) for path in ATTR_TO_PATH.values()}
        if any(item.field_path not in extractable for item in self.evidence):
            raise ValueError("evidence field_path is not extractable")
        return self


ATTR_TO_PATH: dict[str, str] = {
    "destination": RequirementFieldPath.DESTINATION.value,
    "arrival_hub_name": RequirementFieldPath.ARRIVAL_HUB_NAME.value,
    "arrival_hub_type": RequirementFieldPath.ARRIVAL_HUB_TYPE.value,
    "arrival_datetime": RequirementFieldPath.ARRIVAL_LOCAL_DATETIME.value,
    "arrival_is_approximate": RequirementFieldPath.ARRIVAL_IS_APPROXIMATE.value,
    "departure_hub_name": RequirementFieldPath.DEPARTURE_HUB_NAME.value,
    "departure_hub_type": RequirementFieldPath.DEPARTURE_HUB_TYPE.value,
    "departure_datetime": RequirementFieldPath.DEPARTURE_LOCAL_DATETIME.value,
    "departure_is_approximate": RequirementFieldPath.DEPARTURE_IS_APPROXIMATE.value,
    "sightseeing_start_date": RequirementFieldPath.SIGHTSEEING_START_DATE.value,
    "sightseeing_end_date": RequirementFieldPath.SIGHTSEEING_END_DATE.value,
    "sightseeing_days": RequirementFieldPath.SIGHTSEEING_DAYS.value,
    "budget_total_cny": RequirementFieldPath.BUDGET_TOTAL_CNY.value,
    "budget_is_approximate": RequirementFieldPath.BUDGET_IS_APPROXIMATE.value,
    "adults_count": RequirementFieldPath.ADULTS_COUNT.value,
    "children_count": RequirementFieldPath.CHILDREN_COUNT.value,
    "children_ages": RequirementFieldPath.CHILDREN_AGES.value,
    "seniors_count": RequirementFieldPath.SENIORS_COUNT.value,
    "room_count": RequirementFieldPath.ROOM_COUNT.value,
    "room_type_requirements": RequirementFieldPath.ROOM_TYPE_REQUIREMENTS.value,
    "mobility_constraints": RequirementFieldPath.MOBILITY_CONSTRAINTS.value,
    "dietary_restrictions": RequirementFieldPath.DIETARY_RESTRICTIONS.value,
    "interests": RequirementFieldPath.INTERESTS.value,
    "pace": RequirementFieldPath.PACE.value,
    "agent_decides_interests": RequirementFieldPath.AGENT_DECIDES_INTERESTS.value,
    "agent_decides_pace": RequirementFieldPath.AGENT_DECIDES_PACE.value,
    "transport_preferences": RequirementFieldPath.TRANSPORT_PREFERENCES.value,
    "hotel_preferences": RequirementFieldPath.HOTEL_PREFERENCES.value,
    "must_visit_places": RequirementFieldPath.MUST_VISIT_PLACES.value,
    "avoid_places": RequirementFieldPath.AVOID_PLACES.value,
    "daily_time_preferences": RequirementFieldPath.DAILY_TIME_PREFERENCES.value,
    "free_text_notes": RequirementFieldPath.FREE_TEXT_NOTES.value,
}

EXTRACTABLE_REQUIREMENT_FIELD_PATHS = frozenset(
    RequirementFieldPath(path) for path in ATTR_TO_PATH.values()
)


RequirementRepairCode: TypeAlias = Literal[
    "structured_output_invalid",
    "intent_mismatch",
    "evidence_missing",
    "evidence_duplicate",
    "evidence_not_substring",
    "orphan_evidence",
]


class ExtractionRepairFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: RequirementRepairCode
    field_paths: tuple[RequirementFieldPath, ...]

    @model_validator(mode="after")
    def validate_feedback(self) -> "ExtractionRepairFeedback":
        normalized = tuple(sorted(set(self.field_paths), key=lambda path: path.value))
        object.__setattr__(self, "field_paths", normalized)
        if self.code in {"structured_output_invalid", "intent_mismatch"}:
            if normalized:
                raise ValueError(f"{self.code} requires empty field_paths")
            return self
        if not normalized:
            raise ValueError(f"{self.code} requires non-empty field_paths")
        if any(path not in EXTRACTABLE_REQUIREMENT_FIELD_PATHS for path in normalized):
            raise ValueError("evidence feedback contains non-extractable field path")
        return self


class ExtractionAttemptSucceeded(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    outcome_type: Literal["succeeded"] = "succeeded"
    value: StrictRequirementPatchDTO


class ExtractionRepairRequired(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    outcome_type: Literal["repair_required"] = "repair_required"
    feedback: ExtractionRepairFeedback


ExtractionAttemptOutcome = Annotated[
    ExtractionAttemptSucceeded | ExtractionRepairRequired,
    Field(discriminator="outcome_type"),
]


class EvidenceVerified(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    evidence: tuple[VerifiedFieldEvidence, ...]


class ExtractionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    turn_id: UUID
    user_message: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    session_date: date
    timezone: Literal["Asia/Shanghai"]
    current_requirement: TravelRequirement
    intent: RequirementIntent


class RequirementExtractionSession(Protocol):
    async def extract(self) -> ExtractionAttemptOutcome: ...

    async def repair(
        self,
        feedback: ExtractionRepairFeedback,
    ) -> ExtractionAttemptOutcome: ...


class RequirementExtractor(Protocol):
    def start_session(self, request: ExtractionInput) -> RequirementExtractionSession: ...
