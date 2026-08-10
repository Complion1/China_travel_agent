from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr

from app.llm_runtime.models import DeepSeekConfigurationError, LLMErrorCode


class DeepSeekSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    api_key: SecretStr

    def __repr__(self) -> str:
        return "DeepSeekSettings(api_key=SecretStr('***'))"

    def __str__(self) -> str:
        return self.__repr__()


_PLACEHOLDERS = frozenset({"changeme", "your-api-key", "<your-api-key>"})
_PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_project_env() -> None:
    """Load the fixed project-root .env without overriding process variables."""
    load_dotenv(
        dotenv_path=_PROJECT_ENV_FILE,
        override=False,
        interpolate=False,
    )


def load_settings(
    environ: Mapping[str, str] | None = None,
) -> DeepSeekSettings:
    if environ is None:
        load_project_env()
        environ = os.environ
    raw = environ.get("DEEPSEEK_API_KEY")
    if raw is None or not raw.strip() or raw.strip().lower() in _PLACEHOLDERS:
        raise DeepSeekConfigurationError(LLMErrorCode.CONFIGURATION_ERROR)
    return DeepSeekSettings(api_key=SecretStr(raw.strip()))
