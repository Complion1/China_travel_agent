"""True-PG capacity contract, enabled by the W03 isolated migration gate."""
import pytest
import os
from app.artifact_persistence import create_artifact_persistence_runtime, load_artifact_persistence_settings
from app.workflow_contracts import ArtifactSchemaRegistry
from tests.integration.artifact_persistence.conftest import TEST_REPOSITORY_LIMITS

@pytest.fixture(scope="session")
def artifact_persistence_settings():
    settings=load_artifact_persistence_settings(environ=os.environ)
    prefix=os.environ.get("MODULE10_TEST_PREFIX", "")
    token=os.environ.get("MODULE10_TEST_IDENTITY_TOKEN", "")
    m14_prefix=os.environ.get("MODULE14_TEST_PREFIX", "")
    m14_token=os.environ.get("MODULE14_TEST_IDENTITY_TOKEN", "")
    m14_mode=(m14_prefix == prefix and m14_token == token
        and prefix.replace("-", "_") in settings.database)
    m10_mode=settings.database == prefix+"-identity-ap"
    if (not prefix.startswith("cta-m14-") or token != prefix+"-identity"
            or not (m14_mode or m10_mode) or settings.host not in {"postgres", "m14-test-postgres"}):
        pytest.fail("explicit M14 or upstream regression identity required")
    database=os.environ.get("MODULE14_CAPACITY_DATABASE", "")
    if database != settings.database+"_cap":
        pytest.fail("explicit isolated capacity database required")
    return settings.model_copy(update={"database":database})

@pytest.mark.asyncio
async def test_capacity_snapshot_roundtrip_on_isolated_postgres(artifact_persistence_settings, migrated_artifact_database):
    runtime=await create_artifact_persistence_runtime(settings=artifact_persistence_settings,
        registry=ArtifactSchemaRegistry(()),limits=TEST_REPOSITORY_LIMITS)
    try:
        snapshot=await runtime.get_capacity_snapshot()
        assert snapshot.schema_version=="1.0"
        assert snapshot.artifact_count>=0
        assert snapshot==await runtime.get_capacity_snapshot()
    finally:
        await runtime.close()
