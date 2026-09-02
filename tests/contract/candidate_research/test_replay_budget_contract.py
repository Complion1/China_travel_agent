from __future__ import annotations

from app.candidate_research import (
    CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY,
    CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY,
)


def test_research_budget_keys_are_phase_scoped_and_stable() -> None:
    assert CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY.scope.value == "phase"
    assert CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY.name == "research.iteration"
    assert CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY.scope.value == "phase"
    assert CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY.name == "research.place_call"
    assert CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY.phase.value == "candidate_research"
    assert CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY.phase.value == "candidate_research"
