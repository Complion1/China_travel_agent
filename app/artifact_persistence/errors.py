"""安全的 Module 10 启动异常。"""


class ArtifactPersistenceError(Exception):
    """所有 Artifact 持久化启动错误的安全基类。"""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__()

    def __str__(self) -> str:
        return type(self).__name__


class ArtifactPersistenceConfigurationError(ArtifactPersistenceError):
    """环境或数据库结构配置不符合设计。"""


class ArtifactPersistenceDependencyError(ArtifactPersistenceError):
    """PostgreSQL、网络或连接池依赖不可用。"""
