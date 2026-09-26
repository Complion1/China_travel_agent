"""安全、零参数的 Module 11 公共异常。"""

from __future__ import annotations


class DurableExecutionError(Exception):
    """Module 11 的安全错误基类。"""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__()

    def __str__(self) -> str:
        return type(self).__name__


class DurableExecutionConfigurationError(DurableExecutionError):
    """配置或固定结构不符合合同。"""


class DurableExecutionDependencyError(DurableExecutionError):
    """数据库或其他基础依赖不可用。"""


class DurableExecutionUsageError(DurableExecutionError):
    """调用越过了公开生命周期或能力边界。"""


class DurableExecutionRunNotFoundError(DurableExecutionError):
    """指定 run 不存在。"""


class DurableExecutionRunAlreadyExistsError(DurableExecutionError):
    """指定 run 已存在。"""


class DurableExecutionRunBusyError(DurableExecutionError):
    """指定 run 当前由其他执行者持有。"""


class DurableExecutionRunAwaitingInputError(DurableExecutionError):
    """指定 run 正在等待用户输入。"""


class DurableExecutionResumeRejectedError(DurableExecutionError):
    """resume 输入不符合当前暂停合同。"""


class DurableExecutionCapacityExceededError(DurableExecutionError):
    """固定容量已耗尽。"""


class DurableExecutionIncompatibleRunError(DurableExecutionError):
    """run 与当前执行契约不兼容。"""


class DurableExecutionRunBlockedError(DurableExecutionError):
    """run 已被安全阻断。"""


class DurableExecutionStateError(DurableExecutionError):
    """状态或恢复证据不符合合同。"""


__all__ = [
    "DurableExecutionError",
    "DurableExecutionConfigurationError",
    "DurableExecutionDependencyError",
    "DurableExecutionUsageError",
    "DurableExecutionRunNotFoundError",
    "DurableExecutionRunAlreadyExistsError",
    "DurableExecutionRunBusyError",
    "DurableExecutionRunAwaitingInputError",
    "DurableExecutionResumeRejectedError",
    "DurableExecutionCapacityExceededError",
    "DurableExecutionIncompatibleRunError",
    "DurableExecutionRunBlockedError",
    "DurableExecutionStateError",
]
