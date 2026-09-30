from pathlib import Path
import json

import pytest

from app.itinerary_planning.prompt_loader import load_planning_prompt
from app.itinerary_planning.models import PlanningTool
from app.itinerary_planning.tooling import (
    ToolBatchError,
    ToolDispatchResult,
    ToolObservation,
    _CandidateEvidenceCandidate,
    _CandidateEvidenceObservation,
    tool_results,
)


def test_prompt_and_map_projection_have_no_forbidden_payload_terms() -> None:
    prompt = load_planning_prompt().text.lower()
    assert "route step" not in prompt
    source = Path(__file__).parents[3] / "app" / "itinerary_planning" / "tooling.py"
    text = source.read_text(encoding="utf-8").lower()
    assert "route step" not in text
    assert "instruction" not in text


def _valid_evidence() -> _CandidateEvidenceObservation:
    return _CandidateEvidenceObservation(
        status="success",
        candidates=(
            _CandidateEvidenceCandidate(
                candidate_handle="candidate-handle",
                state="eligible",
                details_status="not_requested",
            ),
        ),
    )


def test_tool_result_security_projection_preserves_call_id_for_private_typed_dto() -> None:
    observation = ToolObservation(
        call_id="security-call",
        tool_name=PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        payload=_valid_evidence(),
    )
    result = tool_results(ToolDispatchResult((observation,), terminal=False, cache_hits=0))[0]
    decoded = json.loads(result.content)
    assert result.call_id == "security-call"
    assert decoded["tool_name"] == PlanningTool.GET_CANDIDATE_EVIDENCE.value
    assert decoded["observation"]["data"]["candidates"][0]["candidate_handle"] == "candidate-handle"
    assert "CANARY" not in result.content
    assert "CANARY" not in repr(observation)


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "success", "candidate": "CANARY", "evidence": {"items": "CANARY"}, "items": ["CANARY"], "reason": "CANARY"},
        ["CANARY"],
        ToolBatchError("provider-error"),
        _CandidateEvidenceObservation.model_construct(status="success", candidates=(), evidence_handles=(), reason_code=None),
    ],
)
def test_evidence_projection_rejects_untrusted_shapes_without_canary_output(payload: object) -> None:
    observation = ToolObservation(
        call_id="security-invalid",
        tool_name=PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        payload=payload,
    )
    with pytest.raises(ToolBatchError) as error:
        tool_results(ToolDispatchResult((observation,), terminal=False, cache_hits=0))
    assert error.value.code == "tool_observation_invalid"
    assert "CANARY" not in repr(observation)


def test_evidence_projection_rejects_dto_subclass() -> None:
    class _ForgedEvidence(_CandidateEvidenceObservation):
        pass

    observation = ToolObservation(
        call_id="security-subclass",
        tool_name=PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        payload=_ForgedEvidence(**_valid_evidence().model_dump()),
    )
    with pytest.raises(ToolBatchError) as error:
        tool_results(ToolDispatchResult((observation,), terminal=False, cache_hits=0))
    assert error.value.code == "tool_observation_invalid"


def test_evidence_projection_fails_closed_when_typed_output_exceeds_capacity() -> None:
    candidate = _CandidateEvidenceCandidate(
        candidate_handle="candidate-handle",
        state="eligible",
        details_status="not_requested",
        evidence_handles=("h" * 128,) * 48,
    )
    payload = _CandidateEvidenceObservation(status="success", candidates=(candidate,) * 16)
    observation = ToolObservation(
        call_id="security-large",
        tool_name=PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        payload=payload,
    )
    with pytest.raises(ToolBatchError) as error:
        tool_results(ToolDispatchResult((observation,), terminal=False, cache_hits=0))
    assert error.value.code == "tool_observation_too_large"
