"""Contract tests for Artifact DTOs and discriminated outcomes."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from app.workflow_contracts.artifacts.models import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactLookupOutcome,
    ArtifactReadResult,
    ArtifactRef,
    ArtifactRepositoryLimits,
    ArtifactWriteRequest,
    SAFE_INTEGER_MAX,
)
from app.workflow_contracts.identifiers import WorkflowPhase
from app.workflow_contracts.identifiers import OperationId
from tests.unit.workflow_contracts.fakes import StrictSampleArtifact


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
ARTIFACT_ID = UUID("87654321-4321-8765-4321-876543218765")
OPERATION_ID = OperationId(
    run_id=RUN_ID,
    phase=WorkflowPhase.REQUIREMENTS,
    logical_step="collect_artifact",
)
CREATED_AT = datetime(2026, 8, 11, 9, 30, tzinfo=timezone.utc)


def _ref(**overrides: object) -> ArtifactRef:
    values: dict[str, object] = {
        "artifact_id": ARTIFACT_ID,
        "artifact_type": "test.sample_artifact",
        "schema_version": "1.0",
        "content_hash": "sha256:" + "0" * 64,
        "operation_id": OPERATION_ID,
        "created_at": CREATED_AT,
        "size_bytes": 1,
    }
    values.update(overrides)
    return ArtifactRef(**values)


def test_artifact_dtos_are_strict_frozen_forbidden_and_content_is_repr_hidden() -> None:
    content = {
        "label": "secret-artifact-content",
        "count": 1,
        "ratio": 1.0,
        "occurred_on": "2026-08-11",
        "observed_at": "2026-08-11T01:30:00Z",
        "note": None,
    }
    request = ArtifactWriteRequest(
        operation_id=OPERATION_ID,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content=content,
    )
    assert "secret-artifact-content" not in repr(request)
    assert dict(request.model_config) == {
        "strict": True,
        "frozen": True,
        "extra": "forbid",
    }
    assert dict(ArtifactReadResult.model_config) == dict(request.model_config)
    assert dict(ArtifactRef.model_config) == dict(request.model_config)
    assert dict(ArtifactRepositoryLimits.model_config) == dict(request.model_config)

    with pytest.raises(ValidationError):
        ArtifactWriteRequest(
            operation_id=OPERATION_ID,
            artifact_type="test.sample_artifact",
            schema_version="1.0",
            content={"count": "1"},
            extra="forbidden",
        )
    with pytest.raises(ValidationError):
        request.operation_id = OPERATION_ID

    for field_name in ("operation_id", "artifact_type", "schema_version", "content"):
        values = {
            "operation_id": OPERATION_ID,
            "artifact_type": "test.sample_artifact",
            "schema_version": "1.0",
            "content": {},
        }
        values.pop(field_name)
        with pytest.raises(ValidationError):
            ArtifactWriteRequest(**values)


def test_artifact_ref_uses_utc_offset_and_safe_size() -> None:
    assert _ref(size_bytes=SAFE_INTEGER_MAX).size_bytes == SAFE_INTEGER_MAX

    with pytest.raises(ValidationError):
        _ref(created_at=datetime(2026, 8, 11, 9, 30))
    with pytest.raises(ValidationError):
        _ref(created_at=datetime(2026, 8, 11, 17, 30, tzinfo=timezone(timedelta(hours=8))))
    with pytest.raises(ValidationError):
        _ref(size_bytes=0)
    with pytest.raises(ValidationError):
        _ref(size_bytes=SAFE_INTEGER_MAX + 1)


def test_limits_are_positive_safe_and_per_artifact_cannot_exceed_total() -> None:
    limits = ArtifactRepositoryLimits(
        max_artifact_bytes=1,
        max_artifacts=2,
        max_total_content_bytes=1,
    )
    assert limits.max_artifact_bytes == 1
    with pytest.raises(ValidationError):
        ArtifactRepositoryLimits(
            max_artifact_bytes=2,
            max_artifacts=1,
            max_total_content_bytes=1,
        )
    with pytest.raises(ValidationError):
        ArtifactRepositoryLimits(
            max_artifact_bytes=1,
            max_artifacts=0,
            max_total_content_bytes=1,
        )


def test_lookup_outcome_is_discriminated_and_read_content_is_typed() -> None:
    found = TypeAdapter(ArtifactLookupOutcome).validate_python(
        {"outcome_type": "found", "ref": _ref()}, strict=True
    )
    absent = TypeAdapter(ArtifactLookupOutcome).validate_python(
        {"outcome_type": "absent", "operation_id": OPERATION_ID}, strict=True
    )
    assert isinstance(found, ArtifactFound)
    assert isinstance(absent, ArtifactAbsent)
    assert found.outcome_type == "found"
    assert absent.outcome_type == "absent"

    content = StrictSampleArtifact(
        label="typed",
        count=1,
        ratio=1.0,
        occurred_on=date(2026, 8, 11),
        observed_at=CREATED_AT,
        note=None,
    )
    result = ArtifactReadResult[StrictSampleArtifact](ref=_ref(), content=content)
    assert "typed" not in repr(result)
    assert result.content is content

    with pytest.raises(ValidationError):
        TypeAdapter(ArtifactLookupOutcome).validate_python(
            {"outcome_type": "unknown", "operation_id": OPERATION_ID}, strict=True
        )
