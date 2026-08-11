"""Reusable ArtifactRepository contract helper and Protocol surface test."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import iscoroutinefunction, signature
from typing import Callable, get_origin, get_type_hints
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.workflow_contracts.artifacts.models import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactLookupOutcome,
    ArtifactReadResult,
    ArtifactRef,
    ArtifactRepositoryLimits,
    ArtifactWriteRequest,
)
from app.workflow_contracts.artifacts.registry import (
    ArtifactSchemaRegistration,
    ArtifactSchemaRegistry,
)
from app.workflow_contracts.artifacts.repository import ArtifactRepository, TArtifact
from app.workflow_contracts.errors import (
    ArtifactCapacityExceededError,
    ArtifactConflictError,
    ArtifactExpectedModelMismatchError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ArtifactRequestValidationError,
    ArtifactTooLargeError,
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase
from app.workflow_contracts.providers import UUIDFactory, UtcClock
from tests.unit.workflow_contracts.fakes import (
    FixedUtcClock,
    SequenceUUIDFactory,
    StrictSampleArtifact,
)


ArtifactRepositoryFactory = Callable[
    [ArtifactSchemaRegistry, UtcClock, UUIDFactory, ArtifactRepositoryLimits],
    ArtifactRepository,
]


class _OtherExpectedArtifact(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    value: int


def _registry() -> ArtifactSchemaRegistry:
    return ArtifactSchemaRegistry(
        (
            ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.0",
                model=StrictSampleArtifact,
            ),
            ArtifactSchemaRegistration(
                artifact_type="test.other_artifact",
                schema_version="1.0",
                model=StrictSampleArtifact,
            ),
            ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.1",
                model=StrictSampleArtifact,
            ),
        )
    )


def _operation(logical_step: str) -> OperationId:
    return OperationId(
        run_id=UUID("12345678-1234-5678-1234-567812345678"),
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step=logical_step,
    )


def _request(
    operation_id: OperationId,
    *,
    artifact_type: str = "test.sample_artifact",
    schema_version: str = "1.0",
    content: dict[str, object] | None = None,
) -> ArtifactWriteRequest:
    return ArtifactWriteRequest(
        operation_id=operation_id,
        artifact_type=artifact_type,
        schema_version=schema_version,
        content={
            "label": "contract",
            "count": 1,
            "ratio": 1.0,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": None,
        }
        if content is None
        else content,
    )


def _repository(
    factory: ArtifactRepositoryFactory,
    limits: ArtifactRepositoryLimits,
) -> ArtifactRepository:
    return factory(
        _registry(),
        FixedUtcClock(datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)),
        SequenceUUIDFactory(
            (
                UUID("87654321-4321-8765-4321-876543218765"),
                UUID("87654321-4321-8765-4321-876543218766"),
                UUID("87654321-4321-8765-4321-876543218767"),
                UUID("87654321-4321-8765-4321-876543218768"),
            )
        ),
        limits,
    )


async def assert_artifact_repository_contract(
    factory: ArtifactRepositoryFactory,
) -> None:
    generous_limits = ArtifactRepositoryLimits(
        max_artifact_bytes=4096,
        max_artifacts=8,
        max_total_content_bytes=32768,
    )

    # 1. lookup absent; 2. put then lookup found; 3. get returns fresh typed DTOs.
    repository = _repository(factory, generous_limits)
    first_request = _request(_operation("first_put"))
    absent = await repository.lookup(operation_id=first_request.operation_id)
    assert isinstance(absent, ArtifactAbsent)
    assert absent.operation_id == first_request.operation_id
    first_ref = await repository.put(first_request)
    found = await repository.lookup(operation_id=first_request.operation_id)
    assert isinstance(found, ArtifactFound)
    assert found.ref is not first_ref
    assert found.ref == first_ref
    assert found.ref.artifact_id == first_ref.artifact_id
    assert found.ref.artifact_type == first_ref.artifact_type
    assert found.ref.schema_version == first_ref.schema_version
    assert found.ref.content_hash == first_ref.content_hash
    assert found.ref.operation_id == first_ref.operation_id
    assert found.ref.created_at == first_ref.created_at
    assert found.ref.size_bytes == first_ref.size_bytes
    first_read = await repository.get(first_ref, expected_model=StrictSampleArtifact)
    second_read = await repository.get(first_ref, expected_model=StrictSampleArtifact)
    assert isinstance(first_read, ArtifactReadResult)
    assert type(first_read.content) is StrictSampleArtifact
    assert first_read.content == second_read.content
    assert first_read.content is not second_read.content

    # 4. Exact replay has all seven Ref fields unchanged.
    replay_ref = await repository.put(first_request)
    assert replay_ref.model_dump() == first_ref.model_dump()

    # 5. Same operation with different content conflicts and preserves the old value.
    with __import__("contextlib").suppress(ArtifactConflictError):
        await repository.put(
            _request(
                first_request.operation_id,
                content={
                    "label": "changed",
                    "count": 1,
                    "ratio": 1.0,
                    "occurred_on": "2026-08-11",
                    "observed_at": "2026-08-11T09:30:00Z",
                    "note": None,
                },
            )
        )
        raise AssertionError("content conflict was not raised")
    preserved = await repository.get(first_ref, expected_model=StrictSampleArtifact)
    assert preserved.content.label == "contract"

    # 6. Same operation with a different registered type/version conflicts.
    with __import__("contextlib").suppress(ArtifactConflictError):
        await repository.put(
            _request(
                first_request.operation_id,
                artifact_type="test.other_artifact",
            )
        )
        raise AssertionError("type/version conflict was not raised")

    with __import__("contextlib").suppress(ArtifactConflictError):
        await repository.put(
            _request(
                first_request.operation_id,
                schema_version="1.1",
            )
        )
        raise AssertionError("schema-version conflict was not raised")

    # 7. Different operations with identical content get different Refs but may share hash.
    other_request = _request(_operation("second_put"))
    other_ref = await repository.put(other_request)
    assert other_ref.artifact_id != first_ref.artifact_id
    assert other_ref.content_hash == first_ref.content_hash

    version_ref = await repository.put(
        _request(_operation("version_ref"), schema_version="1.1")
    )
    hash_ref = await repository.put(
        _request(
            _operation("hash_ref"),
            content={
                "label": "different-hash",
                "count": 1,
                "ratio": 1.0,
                "occurred_on": "2026-08-11",
                "observed_at": "2026-08-11T09:30:00Z",
                "note": None,
            },
        )
    )

    # 8. Unknown artifact ID; 9. any Ref field tampering; 10. expected-model identity.
    unknown_ref = first_ref.model_copy(
        update={"artifact_id": UUID("87654321-4321-8765-4321-876543219999")}
    )
    try:
        await repository.get(unknown_ref, expected_model=StrictSampleArtifact)
    except ArtifactNotFoundError:
        pass
    else:
        raise AssertionError("unknown artifact ID was not rejected")

    tampered_refs = {
        "artifact_id": first_ref.model_copy(
            update={"artifact_id": other_ref.artifact_id}
        ),
        "artifact_type": first_ref.model_copy(
            update={"artifact_type": "test.other_artifact"}
        ),
        "schema_version": first_ref.model_copy(
            update={"schema_version": version_ref.schema_version}
        ),
        "content_hash": first_ref.model_copy(
            update={"content_hash": hash_ref.content_hash}
        ),
        "operation_id": first_ref.model_copy(
            update={"operation_id": other_ref.operation_id}
        ),
        "created_at": first_ref.model_copy(
            update={"created_at": first_ref.created_at + timedelta(seconds=1)}
        ),
        "size_bytes": first_ref.model_copy(
            update={"size_bytes": first_ref.size_bytes + 1}
        ),
    }
    for field_name, tampered_ref in tampered_refs.items():
        assert getattr(tampered_ref, field_name) != getattr(first_ref, field_name)
        try:
            await repository.get(tampered_ref, expected_model=StrictSampleArtifact)
        except ArtifactIntegrityError:
            pass
        else:
            raise AssertionError(f"tampered Ref field was not rejected: {field_name}")

    try:
        await repository.get(first_ref, expected_model=_OtherExpectedArtifact)
    except ArtifactExpectedModelMismatchError:
        pass
    else:
        raise AssertionError("expected model mismatch was not rejected")

    # 11. Per-artifact capacity; 15. validation failure leaves lookup absent.
    too_small = _repository(
        factory,
        ArtifactRepositoryLimits(
            max_artifact_bytes=1,
            max_artifacts=8,
            max_total_content_bytes=32768,
        ),
    )
    too_large_request = _request(_operation("too_large"))
    try:
        await too_small.put(too_large_request)
    except ArtifactTooLargeError:
        pass
    else:
        raise AssertionError("per-artifact capacity was not enforced")
    too_large_absent = await too_small.lookup(
        operation_id=too_large_request.operation_id
    )
    assert isinstance(too_large_absent, ArtifactAbsent)
    assert too_large_absent.operation_id == too_large_request.operation_id

    invalid_request = ArtifactWriteRequest.model_construct(
        operation_id=_operation("invalid_request"),
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={"bad": object()},
    )
    invalid_repository = _repository(factory, generous_limits)
    try:
        await invalid_repository.put(invalid_request)
    except ArtifactRequestValidationError:
        pass
    else:
        raise AssertionError("invalid request was not rejected")
    invalid_absent = await invalid_repository.lookup(
        operation_id=invalid_request.operation_id
    )
    assert isinstance(invalid_absent, ArtifactAbsent)
    assert invalid_absent.operation_id == invalid_request.operation_id

    validation_repository = _repository(factory, generous_limits)
    validation_original_request = _request(_operation("validation_original"))
    validation_original_ref = await validation_repository.put(
        validation_original_request
    )
    invalid_replay = ArtifactWriteRequest.model_construct(
        operation_id=validation_original_request.operation_id,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={"bad": object()},
    )
    try:
        await validation_repository.put(invalid_replay)
    except ArtifactRequestValidationError:
        pass
    else:
        raise AssertionError("invalid replay was not rejected")
    assert (
        await validation_repository.get(
            validation_original_ref,
            expected_model=StrictSampleArtifact,
        )
    ).content.label == "contract"

    # 12. Count capacity; 16. capacity failure leaves lookup absent; 14. exact replay survives full capacity.
    count_limited = _repository(
        factory,
        ArtifactRepositoryLimits(
            max_artifact_bytes=4096,
            max_artifacts=1,
            max_total_content_bytes=32768,
        ),
    )
    count_first = _request(_operation("count_first"))
    count_ref = await count_limited.put(count_first)
    assert (await count_limited.put(count_first)).model_dump() == count_ref.model_dump()
    count_second = _request(_operation("count_second"))
    try:
        await count_limited.put(count_second)
    except ArtifactCapacityExceededError:
        pass
    else:
        raise AssertionError("artifact count capacity was not enforced")
    count_absent = await count_limited.lookup(
        operation_id=count_second.operation_id
    )
    assert isinstance(count_absent, ArtifactAbsent)
    assert count_absent.operation_id == count_second.operation_id
    assert (
        await count_limited.get(count_ref, expected_model=StrictSampleArtifact)
    ).content.label == "contract"

    # 13. Total-content capacity uses a probe Ref to avoid duplicating canonical JSON.
    probe = _repository(factory, generous_limits)
    probe_ref = await probe.put(_request(_operation("total_probe")))
    total_limited = _repository(
        factory,
        ArtifactRepositoryLimits(
            max_artifact_bytes=probe_ref.size_bytes,
            max_artifacts=2,
            max_total_content_bytes=probe_ref.size_bytes,
        ),
    )
    total_first = _request(_operation("total_first"))
    total_first_ref = await total_limited.put(total_first)
    total_second = _request(_operation("total_second"))
    try:
        await total_limited.put(total_second)
    except ArtifactCapacityExceededError:
        pass
    else:
        raise AssertionError("total content capacity was not enforced")
    total_absent = await total_limited.lookup(
        operation_id=total_second.operation_id
    )
    assert isinstance(total_absent, ArtifactAbsent)
    assert total_absent.operation_id == total_second.operation_id
    assert (
        await total_limited.get(
            total_first_ref,
            expected_model=StrictSampleArtifact,
        )
    ).content.label == "contract"

    # 17. A conflict never changes the original artifact.
    conflict_repository = _repository(factory, generous_limits)
    conflict_request = _request(_operation("conflict_original"))
    conflict_ref = await conflict_repository.put(conflict_request)
    try:
        await conflict_repository.put(
            _request(
                conflict_request.operation_id,
                content={
                    "label": "replacement",
                    "count": 1,
                    "ratio": 1.0,
                    "occurred_on": "2026-08-11",
                    "observed_at": "2026-08-11T09:30:00Z",
                    "note": None,
                },
            )
        )
    except ArtifactConflictError:
        pass
    else:
        raise AssertionError("original conflict was not raised")
    unchanged = await conflict_repository.get(
        conflict_ref,
        expected_model=StrictSampleArtifact,
    )
    assert unchanged.content.label == "contract"


def test_artifact_repository_protocol_surface() -> None:
    public_methods = {
        name for name in ArtifactRepository.__dict__ if not name.startswith("_")
    }
    assert public_methods == {"put", "lookup", "get"}

    put_signature = signature(ArtifactRepository.put)
    lookup_signature = signature(ArtifactRepository.lookup)
    get_signature = signature(ArtifactRepository.get)
    assert iscoroutinefunction(ArtifactRepository.put)
    assert iscoroutinefunction(ArtifactRepository.lookup)
    assert iscoroutinefunction(ArtifactRepository.get)

    assert list(put_signature.parameters) == ["self", "request"]
    assert list(lookup_signature.parameters) == ["self", "operation_id"]
    assert lookup_signature.parameters["operation_id"].kind.name == "KEYWORD_ONLY"
    assert list(get_signature.parameters) == ["self", "ref", "expected_model"]
    assert get_signature.parameters["expected_model"].kind.name == "KEYWORD_ONLY"
    put_hints = get_type_hints(ArtifactRepository.put)
    lookup_hints = get_type_hints(ArtifactRepository.lookup)
    get_hints = get_type_hints(ArtifactRepository.get)
    assert put_hints["request"] is ArtifactWriteRequest
    assert lookup_hints["operation_id"] is OperationId
    assert get_hints["ref"] is ArtifactRef
    assert get_hints["expected_model"] == type[TArtifact]
    assert put_hints["return"] is ArtifactRef
    assert lookup_hints["return"] == ArtifactLookupOutcome
    assert get_hints["return"] == ArtifactReadResult[TArtifact]
