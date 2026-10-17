"""Small immutable routing receipts; business evidence remains upstream."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.workflow_contracts import (
    ArtifactRef, ArtifactSchemaRegistration, ContentHash, TerminalCode,
    BASE_TERMINAL_CODE_REGISTRATIONS, DeliveryStatus, FieldRequirement,
    TerminalCategory, TerminalCodeRegistration,
    WorkflowPhase,
)


_CATEGORIES = {
    "workflow_degraded": "validation", "requirement_failed": "input",
    "hotel_input_exhausted": "input", "hotel_selection_rejected": "input",
    "hotel_candidates_insufficient": "validation", "research_no_candidates": "validation",
    "planning_incomplete": "validation", "workflow_provider_failed": "provider",
    "workflow_map_failed": "tool", "workflow_budget_exhausted": "budget",
    "workflow_configuration_failed": "configuration", "workflow_contract_failed": "internal",
    "workflow_output_failed": "validation",
    "research_context_invalid": "internal", "research_correction_exhausted": "validation",
    "research_judgment_invalid": "validation", "research_state_invalid": "internal",
    "workflow_replay_mismatch": "internal",
    "workflow_model_output_invalid": "validation",
    "research_judgments_unfinished": "validation", "research_evidence_insufficient": "validation",
    "research_candidates_unsuitable": "validation", "research_recovery_exhausted": "validation",
    "research_budget_exhausted": "budget",
}
DEMO_TERMINAL_REGISTRATIONS = BASE_TERMINAL_CODE_REGISTRATIONS + tuple(
    TerminalCodeRegistration(
        terminal_code=code,
        delivery_status=DeliveryStatus.DEGRADED if code == "workflow_degraded" else DeliveryStatus.FAILED,
        outcome_requirement=FieldRequirement.REQUIRED,
        category=TerminalCategory(category), recoverable=False, user_action_codes=(),
    ) for code, category in _CATEGORIES.items()
)


class DemoControlArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["2.0"]
    sequence: Annotated[int, Field(ge=0, le=31)]
    previous_ref: ArtifactRef | None
    hotel_invalid_count: Annotated[int, Field(ge=0, le=3)]
    map_requested: bool
    supplement_count: Annotated[int, Field(ge=0, le=5)]
    handled_gap_fingerprints: Annotated[tuple[ContentHash, ...], Field(max_length=5)]
    hotel_candidate_ref: ArtifactRef | None
    hotel_selection_ref: ArtifactRef | None
    candidate_set_ref: ArtifactRef | None
    planning_ref: ArtifactRef | None
    last_outcome: Literal[
        "hotel_ready", "hotel_invalid", "hotel_selected", "hotel_declined",
        "research_ready", "supplement_required", "planning_ready", "failed",
    ]
    terminal_code: TerminalCode | None
    supplemental_plan_call: Annotated[int, Field(ge=1, le=5)] | None

    @model_validator(mode="after")
    def validate_receipt(self):
        fingerprints = self.handled_gap_fingerprints
        if len(set(fingerprints)) != len(fingerprints) or len(fingerprints) != self.supplement_count:
            raise ValueError("invalid supplemental history")
        if (self.previous_ref is None) != (self.sequence == 0):
            raise ValueError("invalid control predecessor")
        if self.previous_ref is not None and (
            self.previous_ref.artifact_type != "demo.control"
            or self.previous_ref.schema_version != "2.0"
            or self.previous_ref.operation_id.logical_step != f"control_{self.sequence - 1:02d}"
        ):
            raise ValueError("invalid control predecessor")
        if self.terminal_code is not None and (
            self.last_outcome not in {"failed", "hotel_declined"}
            or self.terminal_code not in {*_CATEGORIES, "cancelled_by_user"}
            or self.terminal_code == "workflow_degraded"
        ):
            raise ValueError("invalid failure receipt")
        if self.last_outcome == "hotel_declined" and self.terminal_code != "cancelled_by_user":
            raise ValueError("decline must cancel")
        if self.last_outcome == "failed" and self.terminal_code is None:
            raise ValueError("failure requires code")
        for ref, artifact_type, phase in (
            (self.hotel_candidate_ref, "hotel.candidate_set", WorkflowPhase.HOTEL_SELECTION),
            (self.hotel_selection_ref, "hotel.selection", WorkflowPhase.HOTEL_SELECTION),
            (self.candidate_set_ref, "candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH),
            (self.planning_ref, "itinerary_planning.itinerary", WorkflowPhase.ITINERARY_PLANNING),
        ):
            if ref is not None and (ref.artifact_type != artifact_type or ref.schema_version not in ({"2.0", "3.0", "4.0", "5.0", "6.0"} if artifact_type == "itinerary_planning.itinerary" else {"1.0", "2.0", "3.0", "4.0"} if artifact_type == "candidate_research.candidate_set" else {"1.0"}) or ref.operation_id.phase is not phase):
                raise ValueError("upstream reference contract mismatch")
        refs = tuple(ref for ref in (self.previous_ref, self.hotel_candidate_ref,
            self.hotel_selection_ref, self.candidate_set_ref, self.planning_ref) if ref is not None)
        if len({ref.operation_id.run_id for ref in refs}) > 1:
            raise ValueError("cross-run receipt")
        return self


DEMO_ARTIFACT_REGISTRATIONS = (
    ArtifactSchemaRegistration(artifact_type="demo.control", schema_version="2.0", model=DemoControlArtifact),
)
