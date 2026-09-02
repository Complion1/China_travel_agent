import app.candidate_research as root
from app.candidate_research.models import SupplementalGap


EXPECTED = (
    "CANDIDATE_RESEARCH_SCHEMA_VERSION", "CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY", "CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY", "CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS",
    "ResearchRole", "RoleVerdict", "CandidateState", "MatchVerdict", "MealIntent", "ResearchInvocationKind", "ResearchTool", "SearchAreaMode", "ResearchMode", "ResearchObjectiveKind", "CandidateResearchStopReason", "RoleReasonCode", "InterestReasonCode", "MealReasonCode", "RequirementReasonCode", "CandidateResearchControlFailureCode", "HotelAnchorRef", "CandidateAnchorRef", "ResearchAnchorRef", "ResearchIterationOperationIds", "CandidateResearchOperationPlan", "CandidateResearchInitialRequest", "CandidateResearchSupplementalRequest", "CandidateCountShortfallGap", "InterestCoverageMissingGap", "AreaCoverageMissingGap", "MealCoverageMissingGap", "MustVisitResolutionMissingGap", "CandidateDetailsMissingGap", "ReplacementCandidateNeededGap", "SupplementalGap", "ProposedSearchAction", "ProposedDetailsAction", "ProposedAction", "ProposedActionBatch", "NoUsefulActionProposed", "ActionModelOutcome", "EvidenceHandle", "RoleJudgmentUpdate", "InterestJudgmentUpdate", "MealJudgmentUpdate", "RequirementVerdictException", "RequirementGroupJudgmentUpdate", "JudgmentPatch", "CandidateRecord", "ResearchCountSummary", "ResearchCoverageSummary", "KnownLLMUsage", "ResearchBudgetSummary", "ResearchGapSummary", "LLMResearchFailure", "MapResearchFailure", "ControlResearchFailure", "CandidateResearchFailure", "CandidateResearchCompleted", "CandidateResearchIncomplete", "CandidateResearchFailed", "CandidateResearchOutcome", "CandidateResearchModelActionArtifact", "CandidateResearchModelJudgmentArtifact", "CandidateResearchPlaceObservationArtifact", "CandidateResearchArtifact", "CandidateResearchError", "CandidateResearchConfigurationError", "CandidateResearchUsageError", "CandidateResearchPort", "CandidateResearchService",
)

# 发布前勘误新增 avoid-resolution 输入分支；终态 summary 仍仅通过 union 暴露。
EXPECTED = EXPECTED[:32] + ("AvoidResolutionMissingGap",) + EXPECTED[32:]


def test_exact_public_api_and_identity():
    assert root.__all__ == EXPECTED
    assert all(hasattr(root, name) for name in EXPECTED)
    assert root.SupplementalGap is SupplementalGap
    for forbidden in ("CandidateResearchGap", "CandidateCore", "RoleAssessment", "prompting", "fingerprints", "judgments"):
        assert forbidden not in root.__all__
        if forbidden not in {"prompting", "fingerprints", "judgments"}:
            assert not hasattr(root, forbidden)


def test_budget_keys_are_frozen_module04_keys():
    assert root.CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY.model_dump(mode="json") == {
        "scope": "phase", "name": "research.iteration", "phase": "candidate_research",
    }
    assert root.CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY.model_dump(mode="json") == {
        "scope": "phase", "name": "research.place_call", "phase": "candidate_research",
    }
