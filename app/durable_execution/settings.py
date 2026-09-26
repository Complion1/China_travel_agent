"""Module 11 的严格 PostgreSQL 设置加载器。"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.durable_execution.errors import DurableExecutionConfigurationError


_PORT_PATTERN = re.compile(r"[0-9]+")
_SETTING_NAMES = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)


class DurableExecutionSettings(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1)
    user: str = Field(min_length=1)
    password: SecretStr

    @model_validator(mode="after")
    def validate_password(self) -> "DurableExecutionSettings":
        if self.password.get_secret_value() == "":
            raise ValueError("password must not be empty")
        return self


def _configuration_failure() -> None:
    raise DurableExecutionConfigurationError() from None


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


def load_durable_execution_settings(
    *, environ: Mapping[str, str] | None = None
) -> DurableExecutionSettings:
    """只读取规定的五个环境键，不接受完整 DSN。"""

    source: Mapping[str, str] = os.environ if environ is None else environ
    try:
        host = _required_string(source, _SETTING_NAMES[0])
        raw_port = _required_string(source, _SETTING_NAMES[1])
        database = _required_string(source, _SETTING_NAMES[2])
        user = _required_string(source, _SETTING_NAMES[3])
        password = _required_string(source, _SETTING_NAMES[4])
        return DurableExecutionSettings(
            host=host,
            port=_parse_port(raw_port),
            database=database,
            user=user,
            password=SecretStr(password),
        )
    except DurableExecutionConfigurationError:
        raise
    except Exception:
        _configuration_failure()


__all__ = ["DurableExecutionSettings", "load_durable_execution_settings"]
