"""Validated read-only capacity projection; never changes repository limits."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

_MAX = 2**53 - 1

class ArtifactCapacitySnapshot(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["1.0"]
    max_artifact_bytes: int = Field(ge=1, le=_MAX)
    max_artifacts: int = Field(ge=1, le=_MAX)
    max_total_content_bytes: int = Field(ge=1, le=_MAX)
    artifact_count: int = Field(ge=0, le=_MAX)
    total_content_bytes: int = Field(ge=0, le=_MAX)

    @model_validator(mode="after")
    def validate_capacity(self):
        if (self.max_total_content_bytes < self.max_artifact_bytes or
                self.artifact_count > self.max_artifacts or
                self.total_content_bytes > self.max_total_content_bytes):
            raise ValueError("invalid capacity")
        return self
