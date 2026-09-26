"""固定且不包含业务正文的 Module 12 错误合同。"""

from enum import Enum


class ResultFormattingUsageError(Exception):
    def __init__(self) -> None:
        super().__init__()

    def __str__(self) -> str:
        return type(self).__name__

    def __repr__(self) -> str:
        return type(self).__name__


class ResultFormattingFailureCode(str, Enum):
    INPUT_NOT_READY = "input_not_ready"
    ITINERARY_NOT_COMPLETED = "itinerary_not_completed"
    SOURCE_MISMATCH = "source_mismatch"
    ARTIFACT_NOT_FOUND = "artifact_not_found"
    ARTIFACT_INTEGRITY_ERROR = "artifact_integrity_error"
    ARTIFACT_CONTRACT_ERROR = "artifact_contract_error"
    ARTIFACT_DEPENDENCY_ERROR = "artifact_dependency_error"
    RESULT_TOO_LARGE = "result_too_large"
    UNSUPPORTED_RESULT_VERSION = "unsupported_result_version"
    UNSAFE_TEXT = "unsafe_text"
    INTERNAL_ERROR = "internal_error"
