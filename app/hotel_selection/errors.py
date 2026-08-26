"""安全、稳定的 Module 07 领域异常。"""

from __future__ import annotations

from typing import ClassVar


class HotelModuleError(Exception):
    """Module 07 异常基类。"""

    __slots__ = ()
    code: ClassVar[str] = "hotel_module_error"

    def __init__(self) -> None:
        super().__init__(self.code)

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"__traceback__", "__cause__", "__context__", "__suppress_context__"}:
            super().__setattr__(name, value)
            return
        raise AttributeError(f"{name} is read-only")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{name} is read-only")

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code})"


class HotelInputError(HotelModuleError):
    """输入或确定性查询计划不符合合同。"""

    code: ClassVar[str] = "invalid_input"


class HotelReplayError(HotelModuleError):
    """Artifact 重放与当前逻辑操作不一致。"""

    code: ClassVar[str] = "artifact_replay_mismatch"


class HotelCandidateIdentityConflictError(HotelModuleError):
    """同一百度 UID 的身份字段发生冲突。"""

    code: ClassVar[str] = "candidate_identity_conflict"


__all__ = (
    "HotelModuleError",
    "HotelInputError",
    "HotelReplayError",
    "HotelCandidateIdentityConflictError",
)
