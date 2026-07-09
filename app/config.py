from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE_PATHS = (
    PROJECT_ROOT / ".env.local",
    PROJECT_ROOT / ".env",
)


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="utf-8").splitlines()

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue

        os.environ.setdefault(key, _strip_optional_quotes(value.strip()))


def _load_project_env_files() -> None:
    for path in ENV_FILE_PATHS:
        _load_env_file(path)


def _read_windows_user_env(name: str) -> str | None:
    if os.name != "nt":
        return None

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None

    if not isinstance(value, str) or not value.strip():
        return None
    return os.path.expandvars(value.strip())


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value

    value = _read_windows_user_env(name)
    if value:
        os.environ.setdefault(name, value)
        return value

    return default


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = get_env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _get_int_env(name: str, default: int) -> int:
    value = get_env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float_env(name: str, default: float) -> float:
    value = get_env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    agent_cache_version: str = "v1"
    baidu_map_auth_token: str | None = None
    result_ttl_seconds: int = 300
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout_seconds: float = 30
    deepseek_max_retries: int = 2
    langsmith_tracing: bool = False
    langsmith_project: str = "china-travel-agent"


def load_settings() -> Settings:
    _load_project_env_files()
    return Settings(
        agent_cache_version=get_env("TRAVEL_AGENT_CACHE_VERSION", "v1") or "v1",
        baidu_map_auth_token=get_env("BAIDU_MAP_AUTH_TOKEN"),
        result_ttl_seconds=_get_int_env("TRAVEL_AGENT_RESULT_TTL_SECONDS", 300),
        deepseek_api_key=get_env("DEEPSEEK_API_KEY"),
        deepseek_base_url=get_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        or "https://api.deepseek.com",
        deepseek_model=get_env("DEEPSEEK_MODEL", "deepseek-chat") or "deepseek-chat",
        deepseek_timeout_seconds=_get_float_env("DEEPSEEK_TIMEOUT_SECONDS", 30),
        deepseek_max_retries=_get_int_env("DEEPSEEK_MAX_RETRIES", 2),
        langsmith_tracing=_get_bool_env("LANGSMITH_TRACING"),
        langsmith_project=get_env("LANGSMITH_PROJECT", "china-travel-agent")
        or "china-travel-agent",
    )


settings = load_settings()
