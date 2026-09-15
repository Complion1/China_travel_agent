"""Module 10 PostgreSQL 连接配置及其安全环境变量加载器。"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import NoReturn

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.artifact_persistence.errors import ArtifactPersistenceConfigurationError


_PORT_PATTERN = re.compile(r"[0-9]+")
_SETTING_NAMES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)


class ArtifactPersistenceSettings(BaseModel):
    """严格、不可变且不允许额外字段的 PostgreSQL 连接设置。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1)
    user: str = Field(min_length=1)
    password: SecretStr

    @model_validator(mode="after")
    def _validate_password(self) -> "ArtifactPersistenceSettings":
        if self.password.get_secret_value() == "":
            raise ValueError("password must not be empty")
        return self


def _configuration_failure() -> NoReturn:
    raise ArtifactPersistenceConfigurationError() from None


def _required_string(environ: Mapping[str, str], name: str) -> str:
    try:
        value = environ[name]
    except Exception:
        _configuration_failure()
    if type(value) is not str or value == "":
        _configuration_failure()
    return value


def _parse_port(raw_port: str) -> int:
    if _PORT_PATTERN.fullmatch(raw_port) is None:
        _configuration_failure()
    try:
        port = int(raw_port, 10)
    except (TypeError, ValueError, OverflowError):
        _configuration_failure()
    if not 1 <= port <= 65535:
        _configuration_failure()
    return port


def load_artifact_persistence_settings(
    *,
    environ: Mapping[str, str] | None = None,
) -> ArtifactPersistenceSettings:
    """只从五个规定的环境变量加载并严格校验设置。"""

    source: Mapping[str, str] = os.environ if environ is None else environ
    try:
        host = _required_string(source, _SETTING_NAMES[0])
        raw_port = _required_string(source, _SETTING_NAMES[1])
        database = _required_string(source, _SETTING_NAMES[2])
        user = _required_string(source, _SETTING_NAMES[3])
        password = _required_string(source, _SETTING_NAMES[4])
        port = _parse_port(raw_port)
        return ArtifactPersistenceSettings(
            host=host,
            port=port,
            database=database,
            user=user,
            password=SecretStr(password),
        )
    except ArtifactPersistenceConfigurationError:
        raise
    except Exception:
        _configuration_failure()
