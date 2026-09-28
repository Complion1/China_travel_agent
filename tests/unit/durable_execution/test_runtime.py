from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.durable_execution.errors import DurableExecutionUsageError
from app.durable_execution.models import NodeRecoveryClass
from app.durable_execution.runtime import _GraphCapabilities
from app.durable_execution.runtime import DurableExecutionRuntime
from app.workflow_contracts import (
    ArtifactFound, ArtifactReadResult, ArtifactRef, ArtifactWriteRequest,
    OperationId, WorkflowPhase,
)
from tests.support.durable_execution_fakes import (
    FakeBudgetPlanProvider, RUN_ID, fake_limits, make_recovery_contract,
)
from tests.requirement_subgraph_fakes import make_initial_state
from tests.support.durable_execution_fakes import TRACE_ID


class _Content(BaseModel):
    value: str


class _Fence:
    def __init__(self):
        self.calls = 0

    async def verify_fence(self, **kwargs):
        assert kwargs["run_id"] == RUN_ID
        self.calls += 1


class _Artifacts:
    def __init__(self, ref):
        self.ref = ref
        self.calls = []

    async def lookup(self, *, operation_id):
        self.calls.append("lookup")
        return ArtifactFound(ref=self.ref)

    async def get(self, ref, *, expected_model):
        self.calls.append("get")
        return ArtifactReadResult(ref=ref, content=_Content(value="safe"))

    async def put(self, request):
        self.calls.append("put")
        raise AssertionError("read only adapter must not delegate put")


@pytest.mark.asyncio
async def test_read_only_artifact_view_checks_binding_and_fence_without_journal():
    operation = OperationId(run_id=RUN_ID, phase=WorkflowPhase.REQUIREMENTS, logical_step="upstream")
    ref = ArtifactRef(
        artifact_id=uuid4(), artifact_type="test.upstream", schema_version="1.0",
        content_hash="sha256:" + "0" * 64, operation_id=operation,
        created_at=datetime.now(UTC), size_bytes=2,
    )
    repository, artifacts = _Fence(), _Artifacts(ref)
    graph = _GraphCapabilities(
        repository=repository, artifact_repository=artifacts,
        contract=make_recovery_contract(nodes=(("safe", NodeRecoveryClass.REPLAY_SAFE),)),
        provider=FakeBudgetPlanProvider(), limits=fake_limits(),
    )
    node = graph.for_node(node_name="safe")
    with pytest.raises(DurableExecutionUsageError):
        node.read_only_artifact_repository
    with graph.bind(run_id=RUN_ID, owner=uuid4(), fencing_token=1,
                    command=None, node_name="safe", checkpoint=None):
        view = node.read_only_artifact_repository
        assert (await view.lookup(operation_id=operation)).ref == ref
        assert (await view.get(ref, expected_model=_Content)).content.value == "safe"
        assert repository.calls == 4 and artifacts.calls == ["lookup", "get"]
        with pytest.raises(DurableExecutionUsageError):
            await view.put(ArtifactWriteRequest(
                operation_id=operation, artifact_type="test.upstream",
                schema_version="1.0", content={"value": "unsafe"},
            ))
        assert artifacts.calls == ["lookup", "get"]
        with pytest.raises(DurableExecutionUsageError):
            await view.lookup(operation_id=operation.model_copy(update={"run_id": uuid4()}))
    with pytest.raises(DurableExecutionUsageError):
        await view.lookup(operation_id=operation)


@pytest.mark.asyncio
async def test_query_race_is_conservative_and_stable_pointer_corruption_is_blocked(monkeypatch):
    pointer = ("", "checkpoint-one", "sha256:" + "1" * 64)
    row = {
        "run_id": RUN_ID, "trace_id": TRACE_ID, "created_at": datetime.now(UTC),
        "finalized_at": None, "lifecycle": "open", "pause_generation": 1,
        "cancel_requested_at": None,
        "latest_root_checkpoint_ns": pointer[0],
        "latest_root_checkpoint_id": pointer[1],
        "latest_root_checkpoint_digest": pointer[2],
    }
    class _Repository:
        def __init__(self, later):
            self.later = later
        async def get_run_row(self, *, run_id):
            return self.later
        async def get_lease_state(self, *, run_id):
            return {"active": False}
    async def contract(self, run_id):
        return None
    async def state(self, *, run_id):
        return make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID), None, pointer
    monkeypatch.setattr(DurableExecutionRuntime, "_check_stored_contract", contract)
    monkeypatch.setattr(DurableExecutionRuntime, "_read_verified_graph_state", state)
    runtime = object.__new__(DurableExecutionRuntime)
    runtime._repository = _Repository({**row, "latest_root_checkpoint_id": "checkpoint-two"})
    raced = await runtime._view_row(row)
    assert raced.activity == "executing" and raced.phase is None
    runtime._repository = _Repository(row)
    async def bad_state(self, *, run_id):
        return make_initial_state(run_id=RUN_ID, trace_id=TRACE_ID), None, ("", "forged", pointer[2])
    monkeypatch.setattr(DurableExecutionRuntime, "_read_verified_graph_state", bad_state)
    corrupted = await runtime._view_row(row)
    assert corrupted.activity == "blocked" and corrupted.phase is None
