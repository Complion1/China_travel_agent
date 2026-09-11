"""Module 09 的零敏感 marker exceptions。"""

from __future__ import annotations

from typing import ClassVar


class ItineraryPlanningError(Exception):
    """规划边界错误；动态正文不进入公共异常。"""

    __slots__ = ("_code",)
    code: ClassVar[str] = "itinerary_planning_error"

    def __init__(self) -> None:
        object.__setattr__(self, "_code", self.code)
        Exception.__init__(self, self.code)

    def __getattribute__(self, name: str) -> object:
        if name == "__dict__":
            raise AttributeError(f"{type(self).__name__} has no mutable attributes")
        return super().__getattribute__(name)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "__traceback__":
            BaseException.__setattr__(self, name, value)
            return
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class ItineraryPlanningConfigurationError(ItineraryPlanningError):
    __slots__ = ()
    code: ClassVar[str] = "prompt_resource_invalid"


class ItineraryPlanningUsageError(ItineraryPlanningError):
    __slots__ = ()
    code: ClassVar[str] = "service_usage_error"


__all__ = (
    "ItineraryPlanningError",
    "ItineraryPlanningConfigurationError",
    "ItineraryPlanningUsageError",
)
