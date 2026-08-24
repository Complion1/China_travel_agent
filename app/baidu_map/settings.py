"""Baidu Maps Secret settings and legacy URL fail-closed handling."""

from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr

from app.baidu_map.errors import BaiduMapConfigurationError


class BaiduMapSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    api_key: SecretStr

    def __repr__(self) -> str:
        return "BaiduMapSettings(api_key=**********)"

    def __str__(self) -> str:
        return self.__repr__()


_PLACEHOLDERS = frozenset(
    {
        "changeme",
        "your-baidu-maps-api-key",
        "<your-baidu-maps-api-key>",
        "__baidu_maps_api_key_required__",
    }
)
_PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_project_env() -> None:
    """Load the fixed project-root .env without overriding process variables."""
    load_dotenv(dotenv_path=_PROJECT_ENV_FILE, override=False, interpolate=False)


def _configuration_error() -> BaiduMapConfigurationError:
    return BaiduMapConfigurationError()


def load_baidu_map_settings(
    environ: Mapping[str, str] | None = None,
) -> BaiduMapSettings:
    if environ is None:
        load_project_env()
        environ = os.environ

    if "BAIDU_MAPS_MCP_URL" in environ:
        raise _configuration_error()

    raw = environ.get("BAIDU_MAPS_API_KEY")
    if not isinstance(raw, str):
        raise _configuration_error()
    normalized = raw.strip()
    if not normalized or normalized.casefold() in _PLACEHOLDERS:
        raise _configuration_error()
    return BaiduMapSettings(api_key=SecretStr(normalized))


__all__ = ("BaiduMapSettings", "load_baidu_map_settings")
