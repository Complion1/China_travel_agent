"""Module 11 对 Module 05 checkpoint serializer 的有界委托。"""

from __future__ import annotations

from typing import Any

from app.durable_execution.errors import (
    DurableExecutionConfigurationError,
    DurableExecutionStateError,
)
from app.durable_execution.models import DurableExecutionLimits
from app.requirement_subgraph import RequirementCheckpointSerializer


_ABSOLUTE_MAX_PAYLOAD_BYTES = 1_048_576
_REJECTED_TAGS = frozenset({"pickle", "pkl", "python_pickle"})


class BoundedCheckpointSerializer:
    """委托 Module 05，并在读写两侧执行同一实例边界。"""

    __slots__ = ("_delegate", "_max_payload_bytes")

    def __init__(
        self,
        *,
        limits: DurableExecutionLimits,
        delegate: RequirementCheckpointSerializer | None = None,
    ) -> None:
        if type(limits) is not DurableExecutionLimits:
            raise DurableExecutionConfigurationError() from None
        if delegate is None:
            delegate = RequirementCheckpointSerializer()
        if type(delegate) is not RequirementCheckpointSerializer:
            raise DurableExecutionConfigurationError() from None
        limit = limits.max_payload_bytes
        if type(limit) is not int or isinstance(limit, bool):
            raise DurableExecutionConfigurationError() from None
        if not 1 <= limit <= _ABSOLUTE_MAX_PAYLOAD_BYTES:
            raise DurableExecutionConfigurationError() from None
        self._delegate = delegate
        self._max_payload_bytes = limit

    @property
    def delegate_type(self) -> type[RequirementCheckpointSerializer]:
        return RequirementCheckpointSerializer

    @property
    def max_payload_bytes(self) -> int:
        return self._max_payload_bytes

    def _check_payload(self, value: object) -> tuple[str, bytes | bytearray]:
        if type(value) is not tuple or len(value) != 2:
            raise DurableExecutionStateError() from None
        tag, payload = value
        if type(tag) is not str or tag in _REJECTED_TAGS:
            raise DurableExecutionStateError() from None
        if type(payload) is not bytes and not (
            tag in {"bytes", "bytearray"} and type(payload) is bytearray
        ):
            raise DurableExecutionStateError() from None
        if len(payload) > self._max_payload_bytes:
            raise DurableExecutionStateError() from None
        if len(payload) > _ABSOLUTE_MAX_PAYLOAD_BYTES:
            raise DurableExecutionStateError() from None
        return tag, payload

    def dumps_typed(self, value: Any) -> tuple[str, bytes]:
        try:
            return self._check_payload(self._delegate.dumps_typed(value))
        except DurableExecutionStateError:
            raise
        except Exception:
            raise DurableExecutionStateError() from None

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        try:
            checked = self._check_payload(data)
            return self._delegate.loads_typed(checked)
        except DurableExecutionStateError:
            raise
        except Exception:
            raise DurableExecutionStateError() from None


__all__ = ["BoundedCheckpointSerializer"]
