"""M14 receives a definition and borrowed capabilities, not an HTTP server."""
from dataclasses import replace
import pytest

from app.demo_workflow import create_demo_workflow_definition, DemoWorkflowConfigurationError
from tests.support.demo_workflow_fakes import full_dependencies


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["payload", "operations", "artifacts"])
async def test_inadequate_explicit_capacity_rejected_before_provider_work(limit):
    dependencies, _, providers = await full_dependencies()
    if limit == "artifacts":
        dependencies = replace(dependencies,artifact_limits=dependencies.artifact_limits.model_copy(update={"max_artifacts":60}))
    else:
        changes = {"max_payload_bytes":1} if limit == "payload" else {"max_managed_operations_per_run":1,"max_journal_rows_per_run":4}
        dependencies = replace(dependencies,durable_limits=dependencies.durable_limits.model_copy(update=changes))
    with pytest.raises(DemoWorkflowConfigurationError):
        create_demo_workflow_definition(dependencies=dependencies)
    assert providers.research_model.call_count == providers.planning_model.call_count == 0
    assert not providers.extractor.calls and providers.hotel_map.search_call_count == 0


@pytest.mark.asyncio
async def test_application_receives_exact_limits_and_separate_cleanup():
    dependencies, _, providers = await full_dependencies()
    definition = create_demo_workflow_definition(dependencies=dependencies)
    assert definition.durable_limits == dependencies.durable_limits
    assert definition.artifact_limits == dependencies.artifact_limits
    assert callable(definition.graph_factory) and callable(definition.cancellation_state_factory)
    await definition.aclose()
    assert providers.research_model.call_count == providers.planning_model.call_count == 0
    assert not providers.research_model.closed and not providers.planning_model.closed
