from __future__ import annotations

import ast
import inspect
from datetime import date

import app.requirement_subgraph as package_module
import app.requirement_subgraph.checkpoint as checkpoint_module
import app.requirement_subgraph.coordinator as coordinator_module
import app.requirement_subgraph.graph as graph_module
import app.requirement_subgraph.runtime as runtime_module
from app.requirement_extraction import DeepSeekRequirementExtractor
from app.requirements import RequirementDomainService
from tests.unit.requirements.fakes import FixedClock, InMemoryEvidenceSink


def _imports(module):
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def test_production_imports_remain_inside_module_05_and_shared_contracts():
    modules = (
        package_module,
        checkpoint_module,
        coordinator_module,
        graph_module,
        runtime_module,
    )
    forbidden_prefixes = (
        "app.llm_runtime",
        "app.requirement_extraction",
        "app.hotel",
        "app.itinerary",
        "app.validation",
        "app.network",
        "app.database",
        "asyncpg",
        "psycopg",
        "requests",
    )
    for module in modules:
        imports = tuple(_imports(module))
        assert not any(
            imported == forbidden or imported.startswith(forbidden + ".")
            for imported in imports
            for forbidden in forbidden_prefixes
        )
        source = inspect.getsource(module)
        assert "__import__" not in source
        assert "importlib" not in source


def test_no_future_module_stub_or_dynamic_loader_is_reachable_from_package():
    assert not hasattr(package_module, "RequirementGraphRuntime")
    assert not hasattr(package_module, "RequirementSubgraphCheckpointStore")
    assert set(package_module.__all__) == {
        "RequirementClarificationInterrupt",
        "RequirementConfirmationInterrupt",
        "RequirementInterrupt",
        "RequirementClarificationResume",
        "RequirementConfirmationResume",
        "RequirementResume",
        "RequirementPauseEnvelope",
        "RequirementResumeEnvelope",
        "RequirementSubgraphPaused",
        "RequirementSubgraphConfirmed",
        "RequirementSubgraphDomainFailed",
        "RequirementSubgraphBudgetExceeded",
        "RequirementSubgraphResult",
        "RequirementSubgraphCoordinatorLimits",
        "DEMO_REQUIREMENT_SUBGRAPH_COORDINATOR_LIMITS",
        "REQUIREMENTS_CLARIFICATION_ROUND_KEY",
        "DEMO_REQUIREMENTS_CLARIFICATION_ROUND_LIMIT",
        "StartAccepted",
        "ResumeAccepted",
        "NodeReplayed",
        "BudgetConsumed",
        "BudgetExceeded",
        "RequirementSubgraphSignal",
        "RequirementSubgraphSignalSink",
        "NoOpRequirementSubgraphSignalSink",
        "RequirementSubgraphError",
        "RequirementSubgraphConfigurationError",
        "RequirementSubgraphInputError",
        "RequirementSubgraphResumeRejectedError",
        "RequirementSubgraphStateError",
        "RequirementSubgraphRunFaultedError",
        "RequirementSubgraphCapacityExceededError",
        "RequirementCheckpointSerializer",
        "RequirementSubgraphCoordinator",
        "RequirementSubgraphRuntime",
        "build_requirement_subgraph",
        "create_demo_requirement_subgraph_runtime",
    }


def test_real_module03_adapter_is_an_assembly_input_not_a_module05_import():
    signature = inspect.signature(DeepSeekRequirementExtractor)
    assert tuple(signature.parameters) == ("runtime", "invocation_sink")
    adapter = DeepSeekRequirementExtractor(
        runtime=object(),
        invocation_sink=object(),
    )
    service = RequirementDomainService(
        extractor=adapter,
        evidence_sink=InMemoryEvidenceSink(),
        clock=FixedClock(date(2026, 8, 17)),
    )
    assert callable(service.process_turn)
    assert callable(service.confirm)
