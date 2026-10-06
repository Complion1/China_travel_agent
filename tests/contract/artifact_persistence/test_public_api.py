"""Public Module 10 export and signature contract."""

from __future__ import annotations

import inspect

import app.artifact_persistence as artifact_persistence
from app.artifact_persistence import create_artifact_persistence_runtime


def test_package_root_exports_exactly_the_frozen_seven_symbols() -> None:
    expected = [
        "ArtifactPersistenceSettings",
        "ArtifactPersistenceRuntime",
        "ArtifactPersistenceError",
        "ArtifactPersistenceConfigurationError",
        "ArtifactPersistenceDependencyError",
        "create_artifact_persistence_runtime",
        "load_artifact_persistence_settings",
        "InteractionStoreLimits",
        "DEMO_INTERACTION_STORE_LIMITS",
        "InteractionStoreReadError",
        "ArtifactCapacitySnapshot",
    ]
    assert artifact_persistence.__all__ == expected
    assert not hasattr(artifact_persistence, "_PostgresArtifactRepository")
    assert not hasattr(artifact_persistence, "AsyncConnectionPool")


def test_factory_signature_is_exact_and_default_limits_are_module04_defaults() -> None:
    signature = inspect.signature(create_artifact_persistence_runtime)
    assert list(signature.parameters) == ["settings", "registry", "limits", "interaction_limits", "interaction_fallback_dir"]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    from app.workflow_contracts.artifacts.in_memory import DEMO_ARTIFACT_REPOSITORY_LIMITS

    assert signature.parameters["limits"].default is DEMO_ARTIFACT_REPOSITORY_LIMITS
