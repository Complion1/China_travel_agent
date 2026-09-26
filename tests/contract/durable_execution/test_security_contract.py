from __future__ import annotations

from pathlib import Path

import pytest

from app.durable_execution.errors import (
    DurableExecutionCapacityExceededError,
    DurableExecutionConfigurationError,
    DurableExecutionDependencyError,
    DurableExecutionError,
    DurableExecutionIncompatibleRunError,
    DurableExecutionResumeRejectedError,
    DurableExecutionRunAlreadyExistsError,
    DurableExecutionRunAwaitingInputError,
    DurableExecutionRunBlockedError,
    DurableExecutionRunBusyError,
    DurableExecutionRunNotFoundError,
    DurableExecutionStateError,
    DurableExecutionUsageError,
)
from app.durable_execution.models import DurableExecutionPauseEnvelope
from app.durable_execution.settings import DurableExecutionSettings


ERROR_TYPES = (
    DurableExecutionError,
    DurableExecutionConfigurationError,
    DurableExecutionDependencyError,
    DurableExecutionUsageError,
    DurableExecutionRunNotFoundError,
    DurableExecutionRunAlreadyExistsError,
    DurableExecutionRunBusyError,
    DurableExecutionRunAwaitingInputError,
    DurableExecutionResumeRejectedError,
    DurableExecutionCapacityExceededError,
    DurableExecutionIncompatibleRunError,
    DurableExecutionRunBlockedError,
    DurableExecutionStateError,
)


def test_error_hierarchy_is_zero_parameter_and_unlinked() -> None:
    assert all(issubclass(error_type, DurableExecutionError) for error_type in ERROR_TYPES)
    for error_type in ERROR_TYPES:
        error = error_type()
        assert str(error) == error_type.__name__
        assert repr(error) == error_type.__name__ + "()"
        assert error.__cause__ is None
        with pytest.raises(TypeError):
            error_type("secret")


def test_secret_and_pause_token_are_not_rendered() -> None:
    settings = DurableExecutionSettings(
        host="postgres",
        port=5432,
        database="db",
        user="user",
        password="secret-value",
    )
    envelope = DurableExecutionPauseEnvelope(pause_token="b" * 32)
    assert "secret-value" not in repr(settings)
    assert "b" * 32 not in repr(envelope)


def test_runtime_keeps_storage_private_and_uses_fixed_graph_options() -> None:
    source = (Path(__file__).parents[3] / "app" / "durable_execution" / "runtime.py").read_text(encoding="utf-8")
    assert "AsyncPostgresSaver" not in source
    assert "pickle" not in source
    assert "importlib" not in source
    assert "durability=\"sync\"" in source
    assert "recursion_limit" in source
    assert "checkpoint_ns" in source


def test_checkpointer_uses_only_official_saver_public_write_read_methods() -> None:
    source = (Path(__file__).parents[3] / "app" / "durable_execution" / "checkpointer.py").read_text(encoding="utf-8")
    assert "AsyncPostgresSaver" in source
    assert "._saver._" not in source
    assert ".from_conn_string" not in source
    assert ".setup(" not in source
    assert "CREATE TABLE" not in source
    assert "DELETE FROM" not in source
