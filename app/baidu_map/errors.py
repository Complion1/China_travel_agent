"""Stable, zero-sensitive Baidu Maps exception ports."""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from app.baidu_map.models.common import MapCapability


class BaiduMapErrorCode(str, Enum):
    REQUEST_ERROR = "request_error"
    CONFIGURATION_ERROR = "configuration_error"
    AUTHENTICATION_ERROR = "authentication_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    TIMEOUT_ERROR = "timeout_error"
    TRANSPORT_ERROR = "transport_error"
    SESSION_ERROR = "session_error"
    TOOL_UNAVAILABLE = "tool_unavailable"
    TOOL_SCHEMA_ERROR = "tool_schema_error"
    PROTOCOL_ERROR = "protocol_error"
    RESPONSE_ERROR = "response_error"
    CONCURRENCY_ERROR = "concurrency_error"


class BaiduMapError(Exception):
    __slots__ = ("_capability",)

    code: ClassVar[BaiduMapErrorCode]

    def __init__(self, *, capability: MapCapability | None = None) -> None:
        object.__setattr__(self, "_capability", capability)
        super().__init__(self.code.value, capability.value if capability else "none")

    def __getattribute__(self, name: str) -> object:
        if name == "__dict__":
            raise AttributeError(f"{type(self).__name__} does not expose mutable attributes")
        return super().__getattribute__(name)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "__traceback__":
            BaseException.__setattr__(self, name, value)
            return
        if name == "_capability":
            try:
                object.__getattribute__(self, "_capability")
            except AttributeError:
                object.__setattr__(self, name, value)
                return
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    @property
    def capability(self) -> MapCapability | None:
        return self._capability

    def __str__(self) -> str:
        capability = self.capability.value if self.capability is not None else "none"
        return f"{type(self).__name__}(code={self.code.value}, capability={capability})"

    def __repr__(self) -> str:
        return self.__str__()


class BaiduMapRequestError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.REQUEST_ERROR


class BaiduMapConfigurationError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.CONFIGURATION_ERROR


class BaiduMapAuthenticationError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.AUTHENTICATION_ERROR


class BaiduMapRateLimitError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.RATE_LIMIT_ERROR


class BaiduMapTimeoutError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.TIMEOUT_ERROR


class BaiduMapTransportError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.TRANSPORT_ERROR


class BaiduMapSessionError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.SESSION_ERROR


class BaiduMapToolUnavailableError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.TOOL_UNAVAILABLE


class BaiduMapToolSchemaError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.TOOL_SCHEMA_ERROR


class BaiduMapProtocolError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.PROTOCOL_ERROR


class BaiduMapResponseError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.RESPONSE_ERROR


class BaiduMapConcurrencyError(BaiduMapError):
    __slots__ = ()
    code = BaiduMapErrorCode.CONCURRENCY_ERROR


__all__ = (
    "BaiduMapAuthenticationError",
    "BaiduMapConcurrencyError",
    "BaiduMapConfigurationError",
    "BaiduMapError",
    "BaiduMapErrorCode",
    "BaiduMapProtocolError",
    "BaiduMapRateLimitError",
    "BaiduMapRequestError",
    "BaiduMapResponseError",
    "BaiduMapSessionError",
    "BaiduMapTimeoutError",
    "BaiduMapToolSchemaError",
    "BaiduMapToolUnavailableError",
    "BaiduMapTransportError",
)
