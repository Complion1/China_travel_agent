"""阶段 07-1 至 07-4 的稳定包根投影。"""

import app.hotel_selection as hotel_selection


EXPECTED = (
    "HOTEL_ARTIFACT_REGISTRATIONS",
    "HOTEL_PLACE_CALL_BUDGET_KEY",
    "HOTEL_SELECTION_SCHEMA_VERSION",
    "HotelCandidate",
    "HotelCandidateDiscoveryFailed",
    "HotelCandidateDiscoveryFailureCode",
    "HotelCandidateDiscoveryOutcome",
    "HotelCandidateOperationIds",
    "HotelCandidateSelectionPort",
    "HotelCandidateSelectionService",
    "HotelCandidatesInsufficient",
    "HotelCandidatesInsufficientRecord",
    "HotelCandidatesReady",
    "HotelCandidatesReadyRecord",
    "HotelCoordinate",
    "HotelDiscoveryArtifact",
    "HotelDiscoveryResult",
    "HotelInputError",
    "HotelMapFailureCode",
    "HotelModuleError",
    "HotelPlaceEvidence",
    "HotelPlaceSearchArtifact",
    "HotelReplayError",
    "HotelCandidateIdentityConflictError",
    "HotelSearchEvidence",
    "HotelSearchFailedEvidence",
    "HotelSearchNoResultEvidence",
    "HotelSearchSuccessEvidence",
    "HotelSelectCommand",
    "HotelDeclineCommand",
    "HotelSelectedDecision",
    "HotelDeclinedDecision",
    "HotelSelectionArtifact",
    "HotelSelectionCommand",
    "HotelSelectionDecision",
    "HotelSelectionDeclined",
    "HotelSelectionOutcome",
    "HotelSelectionRejected",
    "HotelSelectionRejectionCode",
    "HotelSelectionRequest",
    "HotelSelected",
)


def test_stage_projection_is_exact_and_complete() -> None:
    assert hotel_selection.__all__ == EXPECTED
    assert len(hotel_selection.__all__) == 41
    assert all(hasattr(hotel_selection, name) for name in EXPECTED)


def test_upstream_and_private_reexports_are_absent() -> None:
    for name in (
        "ConfirmedTravelRequirement",
        "OperationId",
        "ArtifactSchemaRegistry",
        "PlaceSearchRequest",
        "_HOTEL_CATEGORY_TOKENS",
        "hash_request",
        "build_queries",
    ):
        assert not hasattr(hotel_selection, name)
