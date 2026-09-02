"""Module 08 公共入口测试替身：严格 DTO、可观测调用和确定性脚本。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError

from app.baidu_map import (
    BaiduCoordinate,
    MapCapability,
    MapProvenance,
    PlaceDetails,
    PlaceDetailsNoResult,
    PlaceDetailsSuccess,
    PlaceSearchNoResult,
    PlaceSearchSuccess,
    PlaceSummary,
)
from app.candidate_research.artifacts import (
    CandidateResearchArtifact,
    CandidateResearchModelActionArtifact,
    CandidateResearchModelJudgmentArtifact,
    CandidateResearchPlaceObservationArtifact,
)
from app.hotel_selection import HotelSelectedDecision, HotelSelectionArtifact
from app.hotel_selection.models import HotelCandidate, HotelCoordinate
from app.llm_runtime import (
    InvocationMetadata,
    LLMInvocationFailure,
    LLMUsage,
    ModelRole,
    PromptEnvelope,
    StructuredInvocationOutcome,
    StructuredInvocationSuccess,
)
from app.workflow_contracts import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactReadResult,
    ArtifactRef,
    ArtifactWriteRequest,
    BudgetConsumed,
    BudgetConsumeRequest,
    BudgetExceeded,
    ExecutionBudgetKey,
    ExecutionBudgetSnapshot,
    OperationId,
    WorkflowPhase,
)
from app.workflow_contracts import errors as workflow_errors
from app.workflow_contracts.errors import ExecutionBudgetError

RepoError = getattr(workflow_errors, "Artifact" + "RepositoryError")


ScriptValue = BaseModel | BaseException
ScriptStep = ScriptValue | Callable[[int], ScriptValue]


def _canonical(value: BaseModel | dict[str, object]) -> tuple[dict[str, object], bytes]:
    if isinstance(value, BaseModel):
        tree = value.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)
    else:
        tree = value
    encoded = json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return tree, encoded


def _ref(request: ArtifactWriteRequest, encoded: bytes) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid4(), artifact_type=request.artifact_type, schema_version=request.schema_version,
        content_hash="sha256:" + hashlib.sha256(encoded).hexdigest(), operation_id=request.operation_id,
        created_at=datetime.now(timezone.utc), size_bytes=max(1, len(encoded)),
    )


class FakeModelOperation:
    def __init__(self, responses: Iterable[ScriptStep] = ()) -> None:
        self.responses = list(responses)
        self.prompts: list[PromptEnvelope] = []
        self.schemas: list[type[BaseModel]] = []
        self.closed = False
        self.close_count = 0
        self.call_count = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def ainvoke_structured(self, prompt: PromptEnvelope, schema: type[BaseModel]) -> StructuredInvocationOutcome:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        self.call_count += 1
        self.entered.set()
        if self.block:
            await self.release.wait()
        if self.closed:
            raise RuntimeError("operation closed")
        if not self.responses:
            raise RuntimeError("no fake response")
        step = self.responses.pop(0)
        value = step(self.call_count) if callable(step) else step
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, (StructuredInvocationSuccess, LLMInvocationFailure)):
            raise RuntimeError("fake response is not a structured invocation outcome")
        return value

    def close(self) -> None:
        self.close_count += 1
        self.closed = True


class FakeRuntime:
    def __init__(self, operation: FakeModelOperation | None = None) -> None:
        self.operation = operation or FakeModelOperation()
        self.roles: list[ModelRole] = []
        self.start_count = 0

    def start_operation(self, role: ModelRole) -> FakeModelOperation:
        self.roles.append(role)
        self.start_count += 1
        return self.operation


class FakeMapAdapter:
    def __init__(self, responses: Iterable[ScriptStep] = ()) -> None:
        self.responses = list(responses)
        self.search_requests = []
        self.details_requests = []
        self.call_count = 0
        self.active_calls = 0
        self.max_active_calls = 0
        self.failures: list[BaseException] = []
        self.block = False
        self.release_after: int | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def _next(self) -> BaseModel:
        self.call_count += 1
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        self.entered.set()
        try:
            if self.release_after is not None and self.call_count >= self.release_after:
                self.release.set()
            if self.block:
                await self.release.wait()
            if not self.responses:
                raise RuntimeError("no fake map response")
            step = self.responses.pop(0)
            value = step(self.call_count) if callable(step) else step
            if isinstance(value, BaseException):
                raise value
            if not isinstance(value, BaseModel):
                raise RuntimeError("fake map response is not a DTO")
            return value
        except BaseException as error:
            self.failures.append(error)
            raise
        finally:
            self.active_calls -= 1

    async def search_places(self, request):
        self.search_requests.append(request)
        return await self._next()

    async def get_place_details(self, request):
        self.details_requests.append(request)
        return await self._next()


class FakeRepository:
    def __init__(self) -> None:
        self.refs: dict[OperationId, ArtifactRef] = {}
        self.contents: dict[UUID, dict[str, object]] = {}
        self.put_requests: list[ArtifactWriteRequest] = []
        self.lookup_calls: list[OperationId] = []
        self.get_calls: list[ArtifactRef] = []
        self.put_failures = 0
        self.get_failures = 0
        self.partial_put = False

    def seed(self, ref: ArtifactRef, content: BaseModel) -> None:
        tree, encoded = _canonical(content)
        self.refs[ref.operation_id] = ref
        self.contents[ref.artifact_id] = tree
        if ref.content_hash != "sha256:" + hashlib.sha256(encoded).hexdigest():
            raise ValueError("seed ref hash mismatch")

    async def lookup(self, *, operation_id: OperationId):
        self.lookup_calls.append(operation_id)
        ref = self.refs.get(operation_id)
        return ArtifactAbsent(operation_id=operation_id) if ref is None else ArtifactFound(ref=ref)

    async def get(self, ref: ArtifactRef, *, expected_model: type[BaseModel]):
        self.get_calls.append(ref)
        if self.get_failures:
            self.get_failures -= 1
            raise RepoError()
        tree = self.contents.get(ref.artifact_id)
        if tree is None:
            raise RepoError()
        try:
            content = TypeAdapter(expected_model).validate_json(
                json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                strict=True,
            )
        except (ValidationError, TypeError, ValueError):
            raise RepoError() from None
        return ArtifactReadResult(ref=ref, content=content)

    async def put(self, request: ArtifactWriteRequest) -> ArtifactRef:
        self.put_requests.append(request)
        tree, encoded = _canonical(request.content)
        expected_models = {
            "candidate_research.model_action": CandidateResearchModelActionArtifact,
            "candidate_research.model_judgment": CandidateResearchModelJudgmentArtifact,
            "candidate_research.place_observation": CandidateResearchPlaceObservationArtifact,
            "candidate_research.candidate_set": CandidateResearchArtifact,
        }
        expected = expected_models.get(request.artifact_type)
        if expected is not None:
            try:
                TypeAdapter(expected).validate_json(encoded, strict=True)
            except (ValidationError, TypeError, ValueError):
                raise RepoError() from None
        digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        existing = self.refs.get(request.operation_id)
        if existing is not None:
            if existing.content_hash != digest:
                raise RepoError()
            return existing
        if self.put_failures:
            self.put_failures -= 1
            if self.partial_put:
                ref = _ref(request, encoded)
                self.refs[request.operation_id] = ref
                self.contents[ref.artifact_id] = tree
                raise RepoError()
        ref = _ref(request, encoded)
        self.refs[request.operation_id] = ref
        self.contents[ref.artifact_id] = tree
        return ref


class FakeBudgetManager:
    def __init__(self, run_id: UUID, *, iteration_limit: int = 6, place_limit: int = 48) -> None:
        self.run_id = run_id
        self.iteration_limit = iteration_limit
        self.place_limit = place_limit
        self.consumed: dict[ExecutionBudgetKey, int] = {}
        self.records: dict[OperationId, BudgetConsumed | BudgetExceeded] = {}
        self.consume_calls: list[BudgetConsumeRequest] = []
        self.fail_on_consume: set[int] = set()
        self.consume_count = 0

    def _snapshot(self, key: ExecutionBudgetKey) -> ExecutionBudgetSnapshot:
        limit = self.iteration_limit if key.name == "research.iteration" else self.place_limit
        consumed = self.consumed.get(key, 0)
        return ExecutionBudgetSnapshot(schema_version="1.0", run_id=self.run_id, key=key, limit=limit, consumed=consumed, remaining=limit - consumed)

    async def consume(self, request: BudgetConsumeRequest):
        self.consume_count += 1
        self.consume_calls.append(request)
        if self.consume_count in self.fail_on_consume:
            raise ExecutionBudgetError()
        previous = self.records.get(request.operation_id)
        if previous is not None:
            if isinstance(previous, BudgetConsumed):
                return BudgetConsumed(disposition="replayed", operation_id=previous.operation_id, key=previous.key, amount=previous.amount, snapshot=previous.snapshot)
            return BudgetExceeded(disposition="replayed", operation_id=previous.operation_id, key=previous.key, amount=previous.amount, snapshot=previous.snapshot)
        snapshot = self._snapshot(request.key)
        if snapshot.remaining < request.amount:
            result = BudgetExceeded(disposition="rejected", operation_id=request.operation_id, key=request.key, amount=request.amount, snapshot=snapshot)
        else:
            self.consumed[request.key] = snapshot.consumed + request.amount
            result = BudgetConsumed(disposition="applied", operation_id=request.operation_id, key=request.key, amount=request.amount, snapshot=self._snapshot(request.key))
        self.records[request.operation_id] = result
        return result

    async def get_snapshot(self, *, key: ExecutionBudgetKey):
        return self._snapshot(key)

    async def get_all_snapshots(self):
        return ()


def map_provenance(capability: MapCapability, *, verified: bool = True) -> MapProvenance:
    return MapProvenance(capability=capability, retrieved_at=datetime.now(timezone.utc), verified=verified)


def search_success(uid: str = "place-1", *, verified: bool = True) -> PlaceSearchSuccess:
    return PlaceSearchSuccess(places=(PlaceSummary(uid=uid, name="Place", address="Road", coordinate=BaiduCoordinate(latitude=30.1, longitude=120.1), category="museum"),), provenance=map_provenance(MapCapability.PLACE_SEARCH, verified=verified))


def search_no_result() -> PlaceSearchNoResult:
    return PlaceSearchNoResult(provenance=map_provenance(MapCapability.PLACE_SEARCH))


def details_success(uid: str = "place-1") -> PlaceDetailsSuccess:
    return PlaceDetailsSuccess(details=PlaceDetails(uid=uid, name="Place", address="Road", coordinate=BaiduCoordinate(latitude=30.1, longitude=120.1), category="museum", brand=None, telephone=None, rating=4.0, business_hours=("10:00-20:00",)), provenance=map_provenance(MapCapability.PLACE_DETAILS))


def details_no_result() -> PlaceDetailsNoResult:
    return PlaceDetailsNoResult(provenance=map_provenance(MapCapability.PLACE_DETAILS))


def default_metadata() -> InvocationMetadata:
    return InvocationMetadata(role=ModelRole.CANDIDATE_RESEARCH, model_name="deepseek-v4-flash", finish_reason="stop", provider_attempts=1, possible_duplicate=False, usage=LLMUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2, reasoning_tokens=0, prompt_cache_hit_tokens=1, prompt_cache_miss_tokens=0))


def structured(value: BaseModel) -> StructuredInvocationSuccess:
    return StructuredInvocationSuccess(value=value, metadata=default_metadata())


def hotel_selection(run_id: UUID) -> tuple[ArtifactRef, HotelSelectionArtifact]:
    hotel_op = OperationId(run_id=run_id, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="hotel_selection")
    candidate_set_ref = ArtifactRef(artifact_id=uuid4(), artifact_type="hotel.candidate_set", schema_version="1.0", content_hash="sha256:" + "a" * 64, operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.HOTEL_SELECTION, logical_step="hotel_candidates"), created_at=datetime.now(timezone.utc), size_bytes=1)
    hotel = HotelCandidate(schema_version="1.0", uid="hotel-1", name="Hotel", address="Hotel Road", coordinate=HotelCoordinate(schema_version="1.0", coordinate_system="bd09ll", latitude=30.0, longitude=120.0), category="酒店")
    artifact = HotelSelectionArtifact(schema_version="1.0", candidate_set_ref=candidate_set_ref, decision=HotelSelectedDecision(schema_version="1.0", decision_type="selected", selected_candidate=hotel))
    _, encoded = _canonical(artifact)
    ref = ArtifactRef(artifact_id=uuid4(), artifact_type="hotel.selection", schema_version="1.0", content_hash="sha256:" + hashlib.sha256(encoded).hexdigest(), operation_id=hotel_op, created_at=datetime.now(timezone.utc), size_bytes=max(1, len(encoded)))
    return ref, artifact


__all__ = (
    "FakeBudgetManager", "FakeMapAdapter", "FakeModelOperation", "FakeRepository", "FakeRuntime",
    "default_metadata", "details_no_result", "details_success", "hotel_selection", "map_provenance",
    "search_no_result", "search_success", "structured",
)
