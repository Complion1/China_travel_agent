"""Required fakes per v2.2 §5: RecordingRequirementExtractor, InMemoryEvidenceSink, FixedClock"""

from datetime import date
from collections.abc import Iterable
from uuid import uuid4

from uuid import UUID
from app.requirements.extraction_contract import (
    ExtractionAttemptOutcome,
    ExtractionAttemptSucceeded,
    ExtractionInput,
    ExtractionRepairFeedback,
    StrictRequirementPatchDTO,
)
from app.requirements.models import EvidenceReceipt, EvidenceSink, VerifiedFieldEvidence


def strict_dto(**overrides) -> StrictRequirementPatchDTO:
    """Build the fully-required strict schema while keeping test intent concise."""
    payload = {
        "destination": None,
        "arrival_hub_name": None,
        "arrival_hub_type": None,
        "arrival_datetime": None,
        "arrival_is_approximate": None,
        "departure_hub_name": None,
        "departure_hub_type": None,
        "departure_datetime": None,
        "departure_is_approximate": None,
        "sightseeing_start_date": None,
        "sightseeing_end_date": None,
        "sightseeing_days": None,
        "budget_total_cny": None,
        "budget_is_approximate": None,
        "adults_count": None,
        "children_count": None,
        "children_ages": None,
        "seniors_count": None,
        "room_count": None,
        "room_type_requirements": None,
        "mobility_constraints": None,
        "dietary_restrictions": None,
        "interests": None,
        "pace": None,
        "agent_decides_interests": None,
        "agent_decides_pace": None,
        "transport_preferences": None,
        "hotel_preferences": None,
        "must_visit_places": None,
        "avoid_places": None,
        "daily_time_preferences": None,
        "free_text_notes": None,
        "is_correction": False,
        "evidence": [],
    }
    payload.update(overrides)
    return StrictRequirementPatchDTO(**payload)


class FixedClock:
    """Injectable clock returning a fixed date."""
    def __init__(self, fixed: date):
        self._fixed = fixed

    def today(self) -> date:
        return self._fixed


class RecordingExtractionSession:
    """Async request-scoped session with a deterministic attempt sequence."""

    def __init__(
        self,
        responses: list[ExtractionAttemptOutcome | StrictRequirementPatchDTO | Exception],
    ) -> None:
        self._responses = responses
        self.extract_calls = 0
        self.repair_calls = 0
        self.repair_feedback: list[ExtractionRepairFeedback] = []

    def _next(self) -> ExtractionAttemptOutcome:
        if not self._responses:
            raise AssertionError("unexpected extractor call")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, StrictRequirementPatchDTO):
            return ExtractionAttemptSucceeded(value=response)
        return response

    async def extract(self) -> ExtractionAttemptOutcome:
        self.extract_calls += 1
        return self._next()

    async def repair(
        self,
        feedback: ExtractionRepairFeedback,
    ) -> ExtractionAttemptOutcome:
        self.repair_calls += 1
        self.repair_feedback.append(feedback)
        return self._next()


class RecordingRequirementExtractor:
    """Create one request-scoped async Session and record every request."""

    def __init__(
        self,
        responses: Iterable[ExtractionAttemptOutcome | StrictRequirementPatchDTO | Exception],
    ) -> None:
        self._responses = list(responses)
        self.calls: list[ExtractionInput] = []
        self.sessions: list[RecordingExtractionSession] = []

    def start_session(self, request: ExtractionInput) -> RecordingExtractionSession:
        self.calls.append(request)
        session = RecordingExtractionSession(self._responses)
        self.sessions.append(session)
        return session


class InMemoryEvidenceSink:
    """Stores evidence in memory; never touches a database."""
    def __init__(self):
        self.received: list[tuple[UUID, tuple[VerifiedFieldEvidence, ...]]] = []

    def accept(self, *, turn_id: UUID, evidence: tuple[VerifiedFieldEvidence, ...]) -> tuple[EvidenceReceipt, ...]:
        self.received.append((turn_id, evidence))
        return tuple(
            EvidenceReceipt(field_path=ev.field_path, evidence_ref=str(uuid4()), evidence_digest=ev.text[:16])
            for ev in evidence
        )
