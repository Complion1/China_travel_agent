"""Finite public errors; never retain command or provider content."""


class DemoWorkflowConfigurationError(Exception):
    def __init__(self):
        super().__init__(type(self).__name__)


class DemoWorkflowUsageError(Exception):
    def __init__(self):
        super().__init__(type(self).__name__)
