"""严格请求与有限 outcome；不拥有工作流状态或持久化。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.workflow_contracts import ArtifactRef, TravelSessionState

from .artifacts import ResultDayArtifact, ResultIndexArtifact
from .errors import ResultFormattingFailureCode


class _ResultModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    def __repr_args__(self):
        return ()


class ResultFormattingRequest(_ResultModel):
    schema_version: Literal["2.0"]
    state: TravelSessionState = Field(repr=False)
    itinerary_ref: ArtifactRef = Field(repr=False)


class ResultReadRequest(_ResultModel):
    schema_version: Literal["2.0"]
    state: TravelSessionState = Field(repr=False)
    index_ref: ArtifactRef = Field(repr=False)


class ResultFormattingPrepared(_ResultModel):
    outcome_type: Literal["prepared"]
    schema_version: Literal["2.0"]
    days: Annotated[tuple[ResultDayArtifact, ...], Field(min_length=1, max_length=60)] = Field(repr=False)


class ResultIndexReady(_ResultModel):
    outcome_type: Literal["index_ready"]
    schema_version: Literal["2.0"]
    index: ResultIndexArtifact = Field(repr=False)


class ResultFormattingLoaded(_ResultModel):
    outcome_type: Literal["loaded"]
    schema_version: Literal["2.0"]
    index_ref: ArtifactRef = Field(repr=False)
    index: ResultIndexArtifact = Field(repr=False)
    days: Annotated[tuple[ResultDayArtifact, ...], Field(min_length=1, max_length=60)] = Field(repr=False)
    markdown: str = Field(repr=False)


class ResultFormattingFailed(_ResultModel):
    outcome_type: Literal["failed"]
    schema_version: Literal["2.0"]
    code: ResultFormattingFailureCode


ResultFormattingOutcome = Annotated[ResultFormattingPrepared | ResultFormattingFailed, Field(discriminator="outcome_type")]
ResultIndexOutcome = Annotated[ResultIndexReady | ResultFormattingFailed, Field(discriminator="outcome_type")]
ResultReadOutcome = Annotated[ResultFormattingLoaded | ResultFormattingFailed, Field(discriminator="outcome_type")]
