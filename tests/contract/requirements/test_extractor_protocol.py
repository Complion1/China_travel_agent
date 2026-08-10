"""DLG-T02: strict extractor port contract without a production adapter."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.requirements import (
    ExtractionInput,
    ExtractionAttemptSucceeded,
    ExtractionRepairFeedback,
    ExtractionRepairRequired,
    RequirementExtractor,
    RequirementFieldPath,
    RequirementIntent,
    StrictFieldEvidenceDTO,
    StrictRequirementPatchDTO,
    TravelRequirement,
)


def strict_payload(**overrides):
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
    return payload


class FakeSession:
    async def extract(self):
        return ExtractionAttemptSucceeded(value=StrictRequirementPatchDTO(
            **strict_payload(
                destination="杭州",
                evidence=[
                    StrictFieldEvidenceDTO(
                        field_path=RequirementFieldPath.DESTINATION, text="杭州"
                    )
                ],
            )
        ))

    async def repair(self, feedback: ExtractionRepairFeedback):
        return ExtractionRepairRequired(feedback=feedback)


class FakeExtractor:
    def start_session(self, request: ExtractionInput) -> FakeSession:
        self.request = request
        return FakeSession()


async def test_extractor_conforms_to_protocol_and_receives_fixed_context():
    extractor: RequirementExtractor = FakeExtractor()
    request = ExtractionInput(
        turn_id=uuid4(),
        user_message="杭州",
        session_date=date(2025, 6, 1),
        timezone="Asia/Shanghai",
        current_requirement=TravelRequirement(),
        intent=RequirementIntent.INITIAL,
    )
    session = extractor.start_session(request)
    outcome = await session.extract()
    assert isinstance(outcome, ExtractionAttemptSucceeded)
    assert outcome.value.destination == "杭州"
    assert outcome.value.evidence[0].field_path is RequirementFieldPath.DESTINATION


@pytest.mark.parametrize("missing", tuple(StrictRequirementPatchDTO.model_fields))
def test_every_strict_dto_property_is_required_even_when_nullable(missing):
    payload = strict_payload()
    payload.pop(missing)
    with pytest.raises(ValidationError):
        StrictRequirementPatchDTO(**payload)


def test_strict_dto_accepts_explicit_null_for_nullable_properties():
    dto = StrictRequirementPatchDTO(**strict_payload())
    assert dto.destination is None
    assert dto.children_ages is None
    assert dto.evidence == []


def test_strict_dto_rejects_extra_fields_and_field_path_strings():
    with pytest.raises(ValidationError):
        StrictRequirementPatchDTO(**strict_payload(extra_field="x"))
    with pytest.raises(ValidationError):
        StrictFieldEvidenceDTO(field_path="destination", text="杭州")


def test_strict_dto_enforces_boundaries():
    assert StrictRequirementPatchDTO(**strict_payload(destination="杭" * 64))
    with pytest.raises(ValidationError):
        StrictRequirementPatchDTO(**strict_payload(destination="杭" * 65))
    with pytest.raises(ValidationError):
        StrictRequirementPatchDTO(**strict_payload(adults_count=101))
    with pytest.raises(ValidationError):
        StrictRequirementPatchDTO(**strict_payload(children_ages=[18]))


def test_repair_feedback_sorts_deduplicates_and_enforces_path_rules():
    feedback = ExtractionRepairFeedback(
        code="evidence_missing",
        field_paths=(
            RequirementFieldPath.PACE,
            RequirementFieldPath.DESTINATION,
            RequirementFieldPath.PACE,
        ),
    )
    assert feedback.field_paths == (
        RequirementFieldPath.DESTINATION,
        RequirementFieldPath.PACE,
    )
    with pytest.raises(ValidationError):
        ExtractionRepairFeedback(
            code="structured_output_invalid",
            field_paths=(RequirementFieldPath.DESTINATION,),
        )
    with pytest.raises(ValidationError):
        ExtractionRepairFeedback(code="orphan_evidence", field_paths=())
    with pytest.raises(ValidationError):
        ExtractionRepairFeedback(
            code="evidence_missing",
            field_paths=(RequirementFieldPath.TOTAL_PEOPLE,),
        )


def test_strict_dto_rejects_derived_evidence_paths():
    with pytest.raises(ValidationError):
        StrictRequirementPatchDTO(
            **strict_payload(
                evidence=[
                    StrictFieldEvidenceDTO(
                        field_path=RequirementFieldPath.TOTAL_PEOPLE,
                        text="两人",
                    )
                ]
            )
        )
