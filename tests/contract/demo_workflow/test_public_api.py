import app.demo_workflow as public


def test_production_exports_are_exact():
    assert set(public.__all__) == {
        "DemoStartCommand", "DemoRequirementCommand", "DemoHotelCommand", "DemoResumeCommand",
        "DemoControlArtifact", "DEMO_ARTIFACT_REGISTRATIONS", "DEMO_TERMINAL_REGISTRATIONS",
        "DemoCommandCodec", "DemoBudgetPlanProvider", "DemoWorkflowConfigurationError", "DemoWorkflowUsageError",
        "DemoWorkflowDependencies", "DemoWorkflowDefinition", "DemoDiagnostics", "DemoSummaryView",
        "make_cancelled_state", "create_demo_workflow_definition",
    }
    assert all(hasattr(public, name) for name in public.__all__)
