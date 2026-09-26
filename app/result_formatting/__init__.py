"""Module 12 结构化结果与中文攻略的公共门面。"""

from .artifacts import (
    RESULT_FORMATTING_ARTIFACT_REGISTRATIONS,
    RESULT_FORMATTING_SCHEMA_VERSION,
    RESULT_FORMATTING_TEMPLATE_VERSION,
    ResultDayArtifact,
    ResultIndexArtifact,
    build_result_artifact_write_request,
)
from .errors import ResultFormattingFailureCode, ResultFormattingUsageError
from .models import (
    ResultFormattingFailed,
    ResultFormattingLoaded,
    ResultFormattingOutcome,
    ResultFormattingPrepared,
    ResultFormattingRequest,
    ResultIndexOutcome,
    ResultIndexReady,
    ResultReadOutcome,
    ResultReadRequest,
)
from .service import ResultFormattingService


__all__ = (
    "RESULT_FORMATTING_SCHEMA_VERSION",
    "RESULT_FORMATTING_TEMPLATE_VERSION",
    "RESULT_FORMATTING_ARTIFACT_REGISTRATIONS",
    "ResultFormattingRequest",
    "ResultReadRequest",
    "ResultFormattingPrepared",
    "ResultIndexReady",
    "ResultFormattingLoaded",
    "ResultFormattingFailed",
    "ResultFormattingOutcome",
    "ResultIndexOutcome",
    "ResultReadOutcome",
    "ResultFormattingFailureCode",
    "ResultDayArtifact",
    "ResultIndexArtifact",
    "ResultFormattingService",
    "ResultFormattingUsageError",
    "build_result_artifact_write_request",
)
