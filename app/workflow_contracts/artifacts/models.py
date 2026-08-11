"""Public DTOs for typed workflow artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.workflow_contracts.identifiers import (
    ArtifactSchemaVersion,
    ArtifactType,
    ContentHash,
    OperationId,
)


SAFE_INTEGER_MAX = 2**53 - 1

type JsonValue = (
    None
    | bool
    | int
    | float
    | str
    | list[JsonValue]
    | dict[str, JsonValue]
)


class ArtifactRef(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    artifact_id: UUID
    artifact_type: ArtifactType
    schema_version: ArtifactSchemaVersion
    content_hash: ContentHash
    operation_id: OperationId
    created_at: datetime
    size_bytes: int = Field(ge=1, le=SAFE_INTEGER_MAX)

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be UTC")
        return value


class ArtifactWriteRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    operation_id: OperationId
    artifact_type: ArtifactType
    schema_version: ArtifactSchemaVersion
    content: dict[str, JsonValue] = Field(repr=False)


TArtifact = TypeVar("TArtifact", bound=BaseModel)


class ArtifactReadResult(BaseModel, Generic[TArtifact]):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    ref: ArtifactRef
    content: TArtifact = Field(repr=False)


class ArtifactFound(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    outcome_type: Literal["found"] = "found"
    ref: ArtifactRef


class ArtifactAbsent(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    outcome_type: Literal["absent"] = "absent"
    operation_id: OperationId


ArtifactLookupOutcome = Annotated[
    ArtifactFound | ArtifactAbsent,
    Field(discriminator="outcome_type"),
]


class ArtifactRepositoryLimits(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    max_artifact_bytes: int = Field(ge=1, le=SAFE_INTEGER_MAX)
    max_artifacts: int = Field(ge=1, le=SAFE_INTEGER_MAX)
    max_total_content_bytes: int = Field(ge=1, le=SAFE_INTEGER_MAX)

    @model_validator(mode="after")
    def validate_total_capacity(self) -> "ArtifactRepositoryLimits":
        if self.max_artifact_bytes > self.max_total_content_bytes:
            raise ValueError("max_artifact_bytes exceeds max_total_content_bytes")
        return self
