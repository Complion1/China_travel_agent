"""AP-T01 settings、异常和秘密边界测试。"""

from __future__ import annotations

from urllib.parse import quote
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.artifact_persistence.errors import (
    ArtifactPersistenceConfigurationError,
    ArtifactPersistenceDependencyError,
    ArtifactPersistenceError,
)
from app.artifact_persistence.settings import (
    ArtifactPersistenceSettings,
    load_artifact_persistence_settings,
)


SENTINEL = "m10-secret-sentinel/p@ss"


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        "POSTGRES_HOST": "postgres",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "artifact_test",
        "POSTGRES_USER": "artifact_user",
        "POSTGRES_PASSWORD": SENTINEL,
    }
    values.update(overrides)
    return values


def _assert_configuration_error(environment: dict[str, str]) -> None:
    with pytest.raises(ArtifactPersistenceConfigurationError) as captured:
        load_artifact_persistence_settings(environ=environment)
    assert str(captured.value) == "ArtifactPersistenceConfigurationError"
    assert captured.value.__cause__ is None


def test_loader_reads_exactly_the_five_connection_variables() -> None:
    settings = load_artifact_persistence_settings(
        environ=_environment(POSTGRES_IMAGE="must-not-be-read")
    )

    assert settings.host == "postgres"
    assert settings.port == 5432
    assert settings.database == "artifact_test"
    assert settings.user == "artifact_user"
    assert isinstance(settings.password, SecretStr)
    assert settings.password.get_secret_value() == SENTINEL
    assert set(settings.model_dump()) == {"host", "port", "database", "user", "password"}


def test_loader_with_none_reads_the_current_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "process-postgres")
    monkeypatch.setenv("POSTGRES_PORT", "15432")
    monkeypatch.setenv("POSTGRES_DB", "process-db")
    monkeypatch.setenv("POSTGRES_USER", "process-user")
    monkeypatch.setenv("POSTGRES_PASSWORD", SENTINEL)

    settings = load_artifact_persistence_settings()

    assert settings.host == "process-postgres"
    assert settings.port == 15432
    assert settings.database == "process-db"
    assert settings.user == "process-user"


@pytest.mark.parametrize(
    "name",
    [
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ],
)
def test_missing_and_empty_values_fail_without_echoing_input(name: str) -> None:
    environment = _environment()
    environment.pop(name)
    _assert_configuration_error(environment)

    environment = _environment(**{name: ""})
    _assert_configuration_error(environment)


@pytest.mark.parametrize("raw_port", ["abc", "5432.0", "+5432", " 5432", "5432 ", ""])
def test_port_must_be_an_ascii_decimal_string(raw_port: str) -> None:
    _assert_configuration_error(_environment(POSTGRES_PORT=raw_port))


@pytest.mark.parametrize("raw_port", ["0", "65536", "999999999999999999999999999999"])
def test_port_must_be_in_the_postgresql_port_range(raw_port: str) -> None:
    _assert_configuration_error(_environment(POSTGRES_PORT=raw_port))


def test_settings_are_strict_frozen_and_forbid_extra_fields() -> None:
    settings = ArtifactPersistenceSettings(
        host="postgres",
        port=5432,
        database="artifact_test",
        user="artifact_user",
        password=SecretStr(SENTINEL),
    )

    assert settings.model_config == {"strict": True, "frozen": True, "extra": "forbid"}
    with pytest.raises((ValidationError, TypeError)):
        settings.host = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ArtifactPersistenceSettings(
            host="postgres",
            port="5432",  # type: ignore[arg-type]
            database="artifact_test",
            user="artifact_user",
            password=SecretStr(SENTINEL),
        )
    with pytest.raises(ValidationError):
        ArtifactPersistenceSettings(
            host="postgres",
            port=5432,
            database="artifact_test",
            user="artifact_user",
            password=SecretStr(SENTINEL),
            extra="forbidden",  # type: ignore[call-arg]
        )


def test_secret_repr_str_and_errors_do_not_leak_password_or_encoded_password(capsys: pytest.CaptureFixture[str]) -> None:
    settings = load_artifact_persistence_settings(environ=_environment())
    rendered = f"{settings!r} {settings} {settings.password!r} {settings.password}"
    assert SENTINEL not in rendered
    assert quote(SENTINEL, safe="") not in rendered

    _assert_configuration_error(
        _environment(POSTGRES_PORT="not-a-port", POSTGRES_PASSWORD=SENTINEL)
    )
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert quote(SENTINEL, safe="") not in captured.out + captured.err


def test_persistence_exceptions_are_zero_argument_safe_markers() -> None:
    for error_type in (
        ArtifactPersistenceError,
        ArtifactPersistenceConfigurationError,
        ArtifactPersistenceDependencyError,
    ):
        error = error_type()
        assert isinstance(error, ArtifactPersistenceError)
        assert str(error) == error_type.__name__
        assert error.__cause__ is None
        with pytest.raises(TypeError):
            error_type("secret-or-cause")  # type: ignore[call-arg]


def test_loader_does_not_accept_a_complete_dsn_or_read_image_setting() -> None:
    environment = {
        "DATABASE_URL": f"postgresql://user:{quote(SENTINEL, safe='')}@postgres:5432/db",
        "POSTGRES_IMAGE": "postgres:16-alpine",
    }
    _assert_configuration_error(environment)


def test_settings_module_has_no_database_import_or_unrelated_environment_read() -> None:
    source = Path("app/artifact_persistence/settings.py").read_text(encoding="utf-8")
    assert "psycopg" not in source
    assert "DATABASE_URL" not in source
    assert "POSTGRES_DSN" not in source
    assert "POSTGRES_IMAGE" not in source
