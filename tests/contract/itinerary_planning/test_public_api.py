import app.itinerary_planning as planning


EXPECTED = (
    "PurposeVisitIntent", "ProposedItineraryPatch",
    "ITINERARY_PLANNING_SCHEMA_VERSION", "ITINERARY_PLANNING_MODEL_INVOCATION_BUDGET_KEY", "ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY", "ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS",
    "PlanningDayKind", "VisitKind", "BusinessHoursState", "PlanningTool", "PlanningStopReason", "TransportSemantic", "PlanningFailureCode",
    "PlanningInvocationOperationIds", "ItineraryPlanningOperationPlan", "ItineraryPlanningInitialRequest", "ItineraryPlanningResumeRequest",
    "CandidateLocationRef", "HotelLocationRef", "HubLocationRef", "PlanningLocationRef", "TimeInterval", "RouteTimeInterval", "DailyTimeRule",
    "TimeSensitiveMustAnchor", "TransportPolicyRule", "EffectivePlanningPolicy", "ScheduledVisit", "RouteAlternativeSummary", "RouteLeg",
    "BoundaryTransfer", "BackupOption", "DayPlan", "ItineraryDraft", "PlanningGap", "PlanningGapSummary", "PlanningBudgetSummary", "PlanningSummary",
    "LLMPlanningFailure", "MapPlanningFailure", "ControlPlanningFailure", "PlanningFailure", "ItineraryPlanningCompleted", "ItineraryPlanningIncomplete",
    "ItineraryPlanningCandidateSupplementRequested", "ItineraryPlanningFailed", "ItineraryPlanningOutcome", "GetCandidateEvidenceArgs", "GeocodeHubArgs",
    "CalculateRouteMatrixArgs", "PlanDirectionsArgs", "SubmitItineraryArgs", "RequestCandidateSupplementArgs", "ItineraryPlanningModelActionArtifact",
    "ItineraryPlanningHubGeocodeObservationArtifact", "ItineraryPlanningRouteMatrixObservationArtifact", "ItineraryPlanningDirectionsObservationArtifact",
    "ItineraryPlanningArtifact", "ItineraryPlanningError", "ItineraryPlanningConfigurationError", "ItineraryPlanningUsageError", "ItineraryPlanningPort", "ItineraryPlanningService",
)


def test_facade_is_the_exact_final_module_09_projection() -> None:
    assert planning.__all__ == EXPECTED
    assert planning.ItineraryPlanningService.__name__ == "ItineraryPlanningService"
    assert not hasattr(planning, "models")
    assert not hasattr(planning, "artifacts")
    assert planning.ITINERARY_PLANNING_SCHEMA_VERSION == "6.0"


def test_facade_does_not_leak_artifact_locals_or_conversion_helpers() -> None:
    forbidden_fragments = ("Snapshot", "Record", "Directories", "PlanningState", "_to_", "_from_")
    assert not any(any(fragment in name for fragment in forbidden_fragments) for name in planning.__all__)
    assert not any(name.startswith("_") and ("snapshot" in name.lower() or "record" in name.lower()) for name in vars(planning))

    import importlib

    models = importlib.import_module("app.itinerary_planning.models")
    for name in (
        "CandidateLocationRef", "HotelLocationRef", "HubLocationRef", "TimeInterval", "RouteTimeInterval",
        "DailyTimeRule", "TimeSensitiveMustAnchor", "TransportPolicyRule", "EffectivePlanningPolicy",
        "ScheduledVisit", "RouteAlternativeSummary", "RouteLeg", "BoundaryTransfer", "BackupOption",
        "DayPlan", "ItineraryDraft", "PlanningBudgetSummary", "PlanningSummary",
    ):
        assert tuple(getattr(models, name).model_fields)[0] == "schema_version"
