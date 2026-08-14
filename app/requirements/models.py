from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import Enum
from typing import Annotated, Literal, Optional, Protocol, Tuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator


ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
DestinationText = Annotated[str, StringConstraints(min_length=1, max_length=64)]
HubNameText = Annotated[str, StringConstraints(min_length=1, max_length=128)]
LocalDateText = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
LocalDateTimeText = Annotated[
    str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
]
PeopleCount = Annotated[int, Field(ge=0, le=100)]
PositivePeopleCount = Annotated[int, Field(ge=1, le=100)]
PositiveInt = Annotated[int, Field(ge=1)]
ChildAge = Annotated[int, Field(ge=0, le=17)]


class SchemaVersion(str, Enum):
    V1 = "1.0"


class RequirementFieldPath(str, Enum):
    DESTINATION = "destination"
    ARRIVAL_HUB_NAME = "time_window.arrival.hub_name"
    ARRIVAL_HUB_TYPE = "time_window.arrival.hub_type"
    ARRIVAL_LOCAL_DATETIME = "time_window.arrival.local_datetime"
    ARRIVAL_IS_APPROXIMATE = "time_window.arrival.is_approximate"
    DEPARTURE_HUB_NAME = "time_window.departure.hub_name"
    DEPARTURE_HUB_TYPE = "time_window.departure.hub_type"
    DEPARTURE_LOCAL_DATETIME = "time_window.departure.local_datetime"
    DEPARTURE_IS_APPROXIMATE = "time_window.departure.is_approximate"
    SIGHTSEEING_START_DATE = "time_window.sightseeing_start_date"
    SIGHTSEEING_END_DATE = "time_window.sightseeing_end_date"
    SIGHTSEEING_DAYS = "time_window.sightseeing_days"
    HOTEL_CHECK_IN_DATE = "time_window.hotel_check_in_date"
    HOTEL_CHECK_OUT_DATE = "time_window.hotel_check_out_date"
    HOTEL_NIGHTS = "time_window.hotel_nights"
    BUDGET_TOTAL_CNY = "budget.total_cny"
    BUDGET_IS_APPROXIMATE = "budget.is_approximate"
    BUDGET_SCOPE = "budget.scope"
    ADULTS_COUNT = "party.adults_count"
    CHILDREN_COUNT = "party.children_count"
    CHILDREN_AGES = "party.children_ages"
    SENIORS_COUNT = "party.seniors_count"
    TOTAL_PEOPLE = "party.total_people"
    ROOM_COUNT = "rooms.room_count"
    ROOM_TYPE_REQUIREMENTS = "rooms.room_type_requirements"
    MOBILITY_CONSTRAINTS = "constraints.mobility_constraints"
    DIETARY_RESTRICTIONS = "constraints.dietary_restrictions"
    INTERESTS = "preferences.interests"
    PACE = "preferences.pace"
    AGENT_DECIDES_INTERESTS = "preferences.agent_decides_interests"
    AGENT_DECIDES_PACE = "preferences.agent_decides_pace"
    TRANSPORT_PREFERENCES = "preferences.transport_preferences"
    HOTEL_PREFERENCES = "preferences.hotel_preferences"
    MUST_VISIT_PLACES = "preferences.must_visit_places"
    AVOID_PLACES = "preferences.avoid_places"
    DAILY_TIME_PREFERENCES = "preferences.daily_time_preferences"
    FREE_TEXT_NOTES = "preferences.free_text_notes"
    INFERRED_SCENARIOS = "inferred_scenarios"


class FieldStatus(str, Enum):
    MISSING = "missing"
    EXTRACTED = "extracted"
    DERIVED = "derived"
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    CONFLICT = "conflict"
    INVALID = "invalid"


class RequirementIntent(str, Enum):
    INITIAL = "initial"
    SUPPLEMENT = "supplement"
    CORRECTION = "correction"
    CONFIRMATION_REVISION = "confirmation_revision"


class HubType(str, Enum):
    AIRPORT = "airport"
    RAILWAY_STATION = "railway_station"


class InterestTag(str, Enum):
    CULTURE = "人文"
    NATURE = "自然"
    FOOD = "美食"
    CHECKIN = "打卡"


class TravelPace(str, Enum):
    RELAXED = "轻松"
    NORMAL = "标准"
    INTENSE = "紧凑"


# -- TravelRequirement subtree models --

class TravelTimeWindowArrival(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    hub_name: Optional[HubNameText] = None
    hub_type: Optional[HubType] = None
    local_datetime: Optional[LocalDateTimeText] = None
    is_approximate: bool = False


class TravelTimeWindowDeparture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    hub_name: Optional[HubNameText] = None
    hub_type: Optional[HubType] = None
    local_datetime: Optional[LocalDateTimeText] = None
    is_approximate: bool = False


class TravelTimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    arrival: TravelTimeWindowArrival = Field(default_factory=TravelTimeWindowArrival)
    departure: TravelTimeWindowDeparture = Field(default_factory=TravelTimeWindowDeparture)
    sightseeing_start_date: Optional[LocalDateText] = None
    sightseeing_end_date: Optional[LocalDateText] = None
    sightseeing_days: Optional[PositiveInt] = None
    hotel_check_in_date: Optional[LocalDateText] = None
    hotel_check_out_date: Optional[LocalDateText] = None
    hotel_nights: Optional[PositiveInt] = None


class TravelBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    total_cny: Optional[Annotated[float, Field(gt=0)]] = None
    is_approximate: bool = False
    scope: Literal["destination_only"] = "destination_only"


class TravelParty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    adults_count: Optional[PeopleCount] = None
    children_count: Optional[PeopleCount] = None
    children_ages: Annotated[list[ChildAge], Field(max_length=100)] = Field(default_factory=list)
    seniors_count: Optional[PeopleCount] = None
    total_people: Optional[PositivePeopleCount] = None


class TravelRooms(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    room_count: Optional[PositiveInt] = None
    room_type_requirements: Optional[ShortText] = None


class TravelConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mobility_constraints: Optional[ShortText] = None
    dietary_restrictions: Optional[ShortText] = None


class TravelPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    interests: Optional[list[InterestTag]] = None
    pace: Optional[TravelPace] = None
    agent_decides_interests: bool = False
    agent_decides_pace: bool = False
    transport_preferences: Optional[ShortText] = None
    hotel_preferences: Optional[ShortText] = None
    must_visit_places: list[ShortText] = Field(default_factory=list)
    avoid_places: list[ShortText] = Field(default_factory=list)
    daily_time_preferences: Optional[ShortText] = None
    free_text_notes: Optional[ShortText] = None


class TravelRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    destination: Optional[DestinationText] = None
    time_window: TravelTimeWindow = Field(default_factory=TravelTimeWindow)
    budget: TravelBudget = Field(default_factory=TravelBudget)
    party: TravelParty = Field(default_factory=TravelParty)
    rooms: TravelRooms = Field(default_factory=TravelRooms)
    constraints: TravelConstraints = Field(default_factory=TravelConstraints)
    preferences: TravelPreferences = Field(default_factory=TravelPreferences)
    inferred_scenarios: list[ShortText] = Field(default_factory=list)


# -- Field metadata --

class NormalizationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    raw_value: str
    normalized_value: str
    rule_name: str


class RequirementIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str
    field_path: RequirementFieldPath
    message: str


class FieldMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: FieldStatus = FieldStatus.MISSING
    source_turn_id: Optional[UUID] = None
    evidence_ref: Optional[str] = None
    evidence_digest: Optional[str] = None
    normalization: Optional[NormalizationRecord] = None
    issues: list[RequirementIssue] = Field(default_factory=list)


class FieldMetadataRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    metadata: dict[RequirementFieldPath, FieldMetadata] = Field(default_factory=dict)


# -- RequirementPatch (domain after normalization) --

class RequirementEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field_path: RequirementFieldPath
    text: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    turn_id: Optional[UUID] = None
    digest: str = ""
    ref: str = ""


class RequirementPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    destination: Optional[str] = None
    time_window: Optional[TravelTimeWindow] = None
    budget: Optional[TravelBudget] = None
    party: Optional[TravelParty] = None
    rooms: Optional[TravelRooms] = None
    constraints: Optional[TravelConstraints] = None
    preferences: Optional[TravelPreferences] = None
    evidence: list[RequirementEvidence] = Field(default_factory=list)
    is_correction: bool = False


# -- §6: RequirementDomainState --

class RequirementDomainState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"
    requirement: TravelRequirement = Field(default_factory=TravelRequirement)
    metadata: FieldMetadataRegistry = Field(default_factory=FieldMetadataRegistry)
    issues: Tuple[RequirementIssue, ...] = ()
    confirmation_status: Literal["collecting", "awaiting_confirmation", "confirmed"] = "collecting"


# -- §6.4: Outcome discriminated union --

class NeedClarification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome_type: Literal["need_clarification"] = "need_clarification"
    requirement: TravelRequirement
    metadata: FieldMetadataRegistry
    issues: tuple[RequirementIssue, ...]
    message: str


class NeedConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome_type: Literal["need_confirmation"] = "need_confirmation"
    requirement: TravelRequirement
    metadata: FieldMetadataRegistry
    summary: str


class ConfirmedTravelRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome_type: Literal["confirmed"] = "confirmed"
    schema_version: Literal["1.0"]
    requirement: TravelRequirement
    metadata_without_raw_evidence: FieldMetadataRegistry
    confirmation_summary_digest: Annotated[str, StringConstraints(min_length=1)]


class RequirementErrorCode(str, Enum):
    INPUT_TOO_LONG = "input_too_long"
    VALUE_OUT_OF_RANGE = "value_out_of_range"
    STRICT_SCHEMA_ERROR = "strict_schema_error"
    INTENT_MISMATCH = "intent_mismatch"
    EVIDENCE_MISSING = "evidence_missing"
    EVIDENCE_DUPLICATE = "evidence_duplicate"
    EVIDENCE_NOT_SUBSTRING = "evidence_not_substring"
    ORPHAN_EVIDENCE = "orphan_evidence"
    NORMALIZATION_ERROR = "normalization_error"
    REQUIREMENT_CONFLICT = "requirement_conflict"
    MANUAL_TEMPLATE_ERROR = "manual_template_error"
    CONFIRMATION_NOT_READY = "confirmation_not_ready"


class RequirementExtractionFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome_type: Literal["extraction_failed"] = "extraction_failed"
    code: RequirementErrorCode


class RequirementInvalidated(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome_type: Literal["invalidated"] = "invalidated"
    changed_fields: tuple[RequirementFieldPath, ...]
    before_digest: Annotated[str, StringConstraints(min_length=1)]
    after_digest: Annotated[str, StringConstraints(min_length=1)]
    reason: Annotated[str, StringConstraints(min_length=1)]
    summary: Annotated[str, StringConstraints(min_length=1)]


RequirementDomainOutcome = Annotated[
    NeedClarification | NeedConfirmation | ConfirmedTravelRequirement
    | RequirementExtractionFailed | RequirementInvalidated,
    Field(discriminator="outcome_type"),
]


class RequirementDomainTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    state: RequirementDomainState
    outcome: RequirementDomainOutcome

    @model_validator(mode="after")
    def validate_consistency(self) -> "RequirementDomainTransition":
        state = _fresh_model(RequirementDomainState, self.state)
        outcome = _fresh_model(RequirementDomainOutcome, self.outcome)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "outcome", outcome)

        if isinstance(outcome, NeedClarification):
            if (
                state.confirmation_status != "collecting"
                or state.requirement != outcome.requirement
                or state.metadata != outcome.metadata
                or state.issues != outcome.issues
            ):
                raise ValueError("need_clarification state mismatch")
        elif isinstance(outcome, NeedConfirmation):
            if (
                state.confirmation_status != "awaiting_confirmation"
                or state.issues
                or state.requirement != outcome.requirement
                or state.metadata != outcome.metadata
            ):
                raise ValueError("need_confirmation state mismatch")
        elif isinstance(outcome, ConfirmedTravelRequirement):
            if (
                state.confirmation_status != "confirmed"
                or state.issues
                or state.requirement != outcome.requirement
                or state.metadata != outcome.metadata_without_raw_evidence
            ):
                raise ValueError("confirmed state mismatch")
        elif isinstance(outcome, RequirementInvalidated):
            if (
                state.confirmation_status != "awaiting_confirmation"
                or state.issues
                or outcome.after_digest != _requirement_digest(state.requirement)
            ):
                raise ValueError("invalidated state mismatch")

        return self


def _fresh_model(model_type, value):
    adapter = TypeAdapter(model_type)
    python_tree = value.model_dump(
        mode="python",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    validated = adapter.validate_python(python_tree, strict=True)
    payload = validated.model_dump(
        mode="json",
        exclude_none=False,
        by_alias=False,
        exclude_computed_fields=True,
    )
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return adapter.validate_json(encoded, strict=True)


def _requirement_digest(requirement: TravelRequirement) -> str:
    return hashlib.sha256(
        requirement.model_dump_json(exclude_none=False).encode("utf-8")
    ).hexdigest()


# -- §6.2: RequirementTurnInput --

class RequirementTurnInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"
    turn_id: UUID
    user_message: Annotated[str, StringConstraints(min_length=1)]
    session_date: date
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    intent: RequirementIntent
    current: RequirementDomainState


class RequirementConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["accept", "revise"]
    revision_text: Optional[
        Annotated[str, StringConstraints(min_length=1)]
    ]

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "RequirementConfirmation":
        if self.decision == "accept" and self.revision_text is not None:
            raise ValueError("accept requires revision_text to be None")
        if self.decision == "revise" and self.revision_text is None:
            raise ValueError("revise requires non-empty revision_text")
        return self


# -- §6.5: Size boundaries --

MAX_LENGTHS: dict[str, int] = {
    "destination": 64,
    "hub_name": 128,
    "room_type": 500,
    "constraint_text": 500,
    "other_free_text": 500,
    "evidence_text": 1000,
}

MAX_PEOPLE_COUNT = 100
MAX_TOTAL_PEOPLE = 100
MAX_CHILD_AGE = 17
MAX_USER_MESSAGE = 8000


class VerifiedFieldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field_path: RequirementFieldPath
    text: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


# -- §8.3: EvidenceSink Protocol --

class EvidenceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field_path: RequirementFieldPath
    evidence_ref: str
    evidence_digest: str


class EvidenceSink(Protocol):
    def accept(
        self,
        *,
        turn_id: UUID,
        evidence: tuple[VerifiedFieldEvidence, ...],
    ) -> tuple[EvidenceReceipt, ...]: ...


class ManualTemplateSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    template_version: str
    content: str


# -- §13: Clock Protocol --

class Clock(Protocol):
    def today(self) -> date: ...


# Domain error types
class NormalizationError(Exception): ...
class MergeConflictError(Exception): ...
