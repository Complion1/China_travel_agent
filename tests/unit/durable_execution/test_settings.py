from __future__ import annotations

import inspect

import pytest
from pydantic import SecretStr, ValidationError

from app.durable_execution.errors import DurableExecutionConfigurationError
from app.durable_execution.settings import (
    DurableExecutionSettings,
    load_durable_execution_settings,
)


def _environment() -> dict[str, str]:
    return {
        "POSTGRES_HOST": "postgres",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "durable_execution_test",
        "POSTGRES_USER": "durable_execution_test",
        "POSTGRES_PASSWORD": "test-only-secret",
        "UNRELATED": "ignored",
    }


def test_loader_reads_only_the_five_fixed_keys_and_hides_password() -> None:
    settings = load_durable_execution_settings(environ=_environment())
    assert settings.host == "postgres"
    assert settings.port == 5432
    assert settings.database == "durable_execution_test"
    assert settings.user == "durable_execution_test"
    assert isinstance(settings.password, SecretStr)
    assert settings.password.get_secret_value() == "test-only-secret"
    assert "test-only-secret" not in repr(settings)


@pytest.mark.parametrize(
    "name,value",
    [
        ("POSTGRES_HOST", ""),
        ("POSTGRES_PORT", "0"),
        ("POSTGRES_PORT", "65536"),
        ("POSTGRES_PORT", "5432.0"),
        ("POSTGRES_DB", ""),
        ("POSTGRES_USER", ""),
        ("POSTGRES_PASSWORD", ""),
    ],
)
def test_invalid_environment_is_a_zero_parameter_configuration_error(name: str, value: str) -> None:
    environment = _environment()
    environment[name] = value
    with pytest.raises(DurableExecutionConfigurationError) as caught:
        load_durable_execution_settings(environ=environment)
    assert str(caught.value) == "DurableExecutionConfigurationError"
    assert caught.value.__cause__ is None


def test_missing_environment_is_rejected() -> None:
    environment = _environment()
    del environment["POSTGRES_PASSWORD"]
    with pytest.raises(DurableExecutionConfigurationError):
        load_durable_execution_settings(environ=environment)


def test_settings_are_strict_frozen_and_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        DurableExecutionSettings(
            host="postgres",
            port="5432",
            database="db",
            user="user",
            password="secret",
        )
    settings = DurableExecutionSettings(
        host="postgres",
        port=5432,
        database="db",
        user="user",
        password="secret",
    )
    with pytest.raises(ValidationError):
        settings.host = "other"
    with pytest.raises(ValidationError):
        DurableExecutionSettings(
            host="postgres",
            port=5432,
            database="db",
            user="user",
            password="secret",
            extra="rejected",
        )


def test_loader_signature_is_keyword_only_and_injectable() -> None:
    signature = inspect.signature(load_durable_execution_settings)
    assert tuple(signature.parameters) == ("environ",)
    assert signature.parameters["environ"].kind is inspect.Parameter.KEYWORD_ONLY
