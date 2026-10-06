import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.artifact_persistence import ArtifactCapacitySnapshot, ArtifactPersistenceRuntime, ArtifactPersistenceConfigurationError, ArtifactPersistenceDependencyError
from app.workflow_contracts import ArtifactRepositoryUsageError
from app.workflow_contracts.artifacts.in_memory import DEMO_ARTIFACT_REPOSITORY_LIMITS

class Pool:
    def __init__(self, row):
        self.row=row
        self.statements=[]
    @asynccontextmanager
    async def connection(self, **kwargs):
        yield self
    @asynccontextmanager
    async def cursor(self):
        yield self
    async def execute(self, sql):
        self.statements.append(sql)
    async def fetchone(self):
        return self.row

def runtime(row):
    pool=Pool(row)
    return ArtifactPersistenceRuntime(pool=pool, repository=SimpleNamespace(_limits=DEMO_ARTIFACT_REPOSITORY_LIMITS),
        loop=asyncio.get_running_loop()),pool

@pytest.mark.asyncio
async def test_capacity_is_fresh_readonly_and_closed_loop_safe():
    limits=DEMO_ARTIFACT_REPOSITORY_LIMITS
    row=(limits.max_artifact_bytes,limits.max_artifacts,limits.max_total_content_bytes,1,100)
    resource,pool=runtime(row)
    first=await resource.get_capacity_snapshot()
    pool.row=(*row[:3],2,200)
    second=await resource.get_capacity_snapshot()
    assert first.artifact_count==1 and second.artifact_count==2
    assert all(statement.startswith("SELECT ") for statement in pool.statements)
    resource._closed=True
    with pytest.raises(ArtifactRepositoryUsageError):
        await resource.get_capacity_snapshot()

@pytest.mark.asyncio
@pytest.mark.parametrize("row",[None,(1048576,1,10,0,0),(1048576,2048,67108864,True,0),(1048576,999,67108864,0,0)])
async def test_missing_corrupt_or_drift_is_configuration_error(row):
    resource,_=runtime(row)
    with pytest.raises(ArtifactPersistenceConfigurationError):
        await resource.get_capacity_snapshot()

@pytest.mark.asyncio
async def test_cross_loop_dependency_and_cancellation_remain_distinct():
    limits=DEMO_ARTIFACT_REPOSITORY_LIMITS
    resource,pool=runtime((limits.max_artifact_bytes,limits.max_artifacts,limits.max_total_content_bytes,0,0))
    with pytest.raises(ArtifactRepositoryUsageError):
        await asyncio.to_thread(lambda: asyncio.run(resource.get_capacity_snapshot()))
    async def unavailable():
        raise OSError("private database details")
    pool.fetchone=unavailable
    with pytest.raises(ArtifactPersistenceDependencyError) as caught:
        await resource.get_capacity_snapshot()
    assert "private" not in str(caught.value)
    async def cancelled():
        raise asyncio.CancelledError()
    pool.fetchone=cancelled
    with pytest.raises(asyncio.CancelledError):
        await resource.get_capacity_snapshot()

@pytest.mark.parametrize("field,value",[("max_artifacts",True),("artifact_count",-1),("total_content_bytes",2**53)])
def test_snapshot_rejects_unsafe_numbers(field,value):
    values=dict(schema_version="1.0",max_artifact_bytes=1,max_artifacts=2,max_total_content_bytes=3,artifact_count=0,total_content_bytes=0)
    with pytest.raises(ValidationError):
        ArtifactCapacitySnapshot(**(values|{field:value}))
