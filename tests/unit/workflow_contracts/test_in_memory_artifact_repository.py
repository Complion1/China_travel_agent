"""InMemoryArtifactRepository implementation and concurrency tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.workflow_contracts.artifacts.in_memory import InMemoryArtifactRepository
from app.workflow_contracts.artifacts.models import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactRef,
    ArtifactRepositoryLimits,
    ArtifactWriteRequest,
)
from app.workflow_contracts.artifacts.registry import (
    ArtifactSchemaRegistration,
    ArtifactSchemaRegistry,
)
from app.workflow_contracts.errors import (
    ArtifactCapacityExceededError,
    ArtifactConflictError,
    ArtifactRepositoryDependencyError,
    ArtifactRepositoryUsageError,
    ArtifactRequestValidationError,
    ArtifactTooLargeError,
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase
from tests.unit.workflow_contracts.fakes import (
    FixedUtcClock,
    SequenceUUIDFactory,
    StrictSampleArtifact,
)


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
ARTIFACT_ID = UUID("87654321-4321-8765-4321-876543218765")
SECOND_ARTIFACT_ID = UUID("87654321-4321-8765-4321-876543218766")


def _registry() -> ArtifactSchemaRegistry:
    return ArtifactSchemaRegistry(
        (
            ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.0",
                model=StrictSampleArtifact,
            ),
        )
    )


def _operation(logical_step: str) -> OperationId:
    return OperationId(
        run_id=RUN_ID,
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step=logical_step,
    )


def _request(
    logical_step: str,
    *,
    label: str = "repository",
) -> ArtifactWriteRequest:
    return ArtifactWriteRequest(
        operation_id=_operation(logical_step),
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={
            "label": label,
            "count": 1,
            "ratio": 1.0,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": None,
        },
    )


def _limits(
    *,
    max_artifact_bytes: int = 4096,
    max_artifacts: int = 8,
    max_total_content_bytes: int = 32768,
) -> ArtifactRepositoryLimits:
    return ArtifactRepositoryLimits(
        max_artifact_bytes=max_artifact_bytes,
        max_artifacts=max_artifacts,
        max_total_content_bytes=max_total_content_bytes,
    )


def _repository(
    *,
    limits: ArtifactRepositoryLimits | None = None,
    uuid_values: tuple[UUID, ...] = (ARTIFACT_ID, SECOND_ARTIFACT_ID),
    clock: object | None = None,
    uuid_factory: object | None = None,
) -> InMemoryArtifactRepository:
    return InMemoryArtifactRepository(
        registry=_registry(),
        utc_clock=FixedUtcClock(
            datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)
        )
        if clock is None
        else clock,
        uuid_factory=SequenceUUIDFactory(uuid_values)
        if uuid_factory is None
        else uuid_factory,
        limits=_limits() if limits is None else limits,
    )


class RaisingUUIDFactory:
    def new(self) -> UUID:
        raise RuntimeError("uuid provider failed")


class WrongUUIDFactory:
    def new(self) -> str:
        return "not-a-uuid"


class RaisingClock:
    def now(self) -> datetime:
        raise RuntimeError("clock failed")


class NaiveClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 11, 9, 30)


class NonUtcClock:
    def now(self) -> datetime:
        from datetime import timedelta

        return datetime(2026, 8, 11, 17, 30, tzinfo=timezone(timedelta(hours=8)))


class WrongClock:
    def now(self) -> str:
        return "2026-08-11T09:30:00Z"


def test_limits_are_strictly_copied_and_invalid_forged_limits_are_rejected() -> None:
    limits = _limits(max_artifact_bytes=4096)
    repository = _repository(limits=limits)
    object.__setattr__(limits, "max_artifact_bytes", 1)

    async def put_once() -> None:
        await repository.put(_request("copied_limits"))

    asyncio.run(put_once())

    forged_values = (
        {"max_artifact_bytes": 0, "max_artifacts": 1, "max_total_content_bytes": 1},
        {"max_artifact_bytes": 2, "max_artifacts": 1, "max_total_content_bytes": 1},
    )
    for values in forged_values:
        forged = ArtifactRepositoryLimits.model_construct(**values)
        with pytest.raises(ArtifactRepositoryUsageError):
            _repository(limits=forged)


@pytest.mark.parametrize(
    ("clock", "uuid_factory"),
    [
        (None, RaisingUUIDFactory()),
        (None, WrongUUIDFactory()),
        (RaisingClock(), None),
        (NaiveClock(), None),
        (NonUtcClock(), None),
        (WrongClock(), None),
    ],
    ids=["uuid-error", "uuid-type", "clock-error", "clock-naive", "clock-non-utc", "clock-type"],
)
def test_uuid_and_clock_failures_are_dependency_errors_without_partial_state(
    clock: object | None,
    uuid_factory: object | None,
) -> None:
    repository = _repository(clock=clock, uuid_factory=uuid_factory)
    request = _request("dependency_failure")

    async def exercise() -> None:
        with pytest.raises(ArtifactRepositoryDependencyError):
            await repository.put(request)
        absent = await repository.lookup(operation_id=request.operation_id)
        assert isinstance(absent, ArtifactAbsent)

    asyncio.run(exercise())


def test_uuid_collision_does_not_retry_or_overwrite() -> None:
    repository = _repository(uuid_values=(ARTIFACT_ID, ARTIFACT_ID))
    first = _request("collision_first")
    second = _request("collision_second")

    async def exercise() -> None:
        first_ref = await repository.put(first)
        with pytest.raises(ArtifactRepositoryDependencyError):
            await repository.put(second)
        assert isinstance(
            await repository.lookup(operation_id=second.operation_id),
            ArtifactAbsent,
        )
        read = await repository.get(first_ref, expected_model=StrictSampleArtifact)
        assert read.content.label == "repository"

    asyncio.run(exercise())


def test_loop_owner_rejects_cross_event_loop_use() -> None:
    repository = _repository()
    operation = _operation("loop_owner")

    async def first_call() -> None:
        assert isinstance(
            await repository.lookup(operation_id=operation),
            ArtifactAbsent,
        )

    asyncio.run(first_call())
    with pytest.raises(ArtifactRepositoryUsageError):
        asyncio.run(first_call())


@pytest.mark.asyncio
async def test_concurrent_same_key_same_content_replays_one_ref() -> None:
    repository = _repository(uuid_values=(ARTIFACT_ID,))
    request = _request("same_concurrent")
    barrier = asyncio.Barrier(2)

    async def call() -> ArtifactRef:
        await barrier.wait()
        return await repository.put(request)

    first, second = await asyncio.gather(call(), call())
    assert first.model_dump() == second.model_dump()
    assert first.artifact_id == ARTIFACT_ID


@pytest.mark.asyncio
async def test_concurrent_same_key_different_content_has_one_conflict() -> None:
    repository = _repository(uuid_values=(ARTIFACT_ID,))
    first = _request("conflicting_concurrent", label="first")
    second = _request("conflicting_concurrent", label="second")
    barrier = asyncio.Barrier(2)

    async def call(request: ArtifactWriteRequest) -> object:
        await barrier.wait()
        try:
            return await repository.put(request)
        except ArtifactConflictError as error:
            return error

    results = await asyncio.gather(call(first), call(second))
    assert sum(isinstance(result, ArtifactRef) for result in results) == 1
    assert sum(isinstance(result, ArtifactConflictError) for result in results) == 1


@pytest.mark.asyncio
async def test_capacity_and_validation_failures_leave_no_partial_state() -> None:
    repository = _repository(
        limits=_limits(max_artifact_bytes=1, max_artifacts=1, max_total_content_bytes=1)
    )
    too_large = _request("too_large")
    with pytest.raises(ArtifactTooLargeError):
        await repository.put(too_large)
    assert isinstance(
        await repository.lookup(operation_id=too_large.operation_id),
        ArtifactAbsent,
    )

    forged = ArtifactWriteRequest.model_construct(
        operation_id=_operation("forged_request"),
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={"bad": object()},
    )
    with pytest.raises(ArtifactRequestValidationError):
        await repository.put(forged)
    assert isinstance(
        await repository.lookup(operation_id=forged.operation_id),
        ArtifactAbsent,
    )


@pytest.mark.asyncio
async def test_exact_replay_survives_full_capacity_and_public_results_are_fresh() -> None:
    repository = _repository(limits=_limits(max_artifacts=1))
    first = _request("full_replay")
    first_ref = await repository.put(first)
    replay = await repository.put(first)
    assert replay.model_dump() == first_ref.model_dump()
    assert replay is not first_ref

    second = _request("full_new")
    with pytest.raises(ArtifactCapacityExceededError):
        await repository.put(second)
    found = await repository.lookup(operation_id=first.operation_id)
    assert isinstance(found, ArtifactFound)
    assert found.ref is not first_ref
    read = await repository.get(first_ref, expected_model=StrictSampleArtifact)
    assert read.content.label == "repository"
    assert not hasattr(read, "canonical_bytes")


def test_repository_has_only_protocol_public_methods() -> None:
    public_methods = {
        name
        for name in InMemoryArtifactRepository.__dict__
        if not name.startswith("_")
    }
    assert public_methods == {"put", "lookup", "get"}
