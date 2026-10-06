"""Safe zero-argument public error markers."""
class WebApplicationError(Exception):
    __slots__ = ()
    def __init__(self) -> None:
        super().__init__()
    def __str__(self) -> str:
        return type(self).__name__

class WebApplicationConfigurationError(WebApplicationError):
    pass
class WebApplicationDependencyError(WebApplicationError):
    pass
class WebApplicationUsageError(WebApplicationError):
    pass
class WebApplicationConflictError(WebApplicationError):
    pass
class WebApplicationCapacityError(WebApplicationError):
    pass
