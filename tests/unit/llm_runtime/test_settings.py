"""LLM-T02: Settings tests"""
import pytest
from pydantic import SecretStr
import app.llm_runtime.settings as settings_module
from app.llm_runtime.settings import load_settings, DeepSeekSettings, DeepSeekConfigurationError


def test_load_settings_valid():
    s = load_settings({"DEEPSEEK_API_KEY": "sk-test123"})
    assert isinstance(s.api_key, SecretStr)


def test_load_settings_missing():
    with pytest.raises(DeepSeekConfigurationError):
        load_settings({})


def test_load_settings_blank():
    with pytest.raises(DeepSeekConfigurationError):
        load_settings({"DEEPSEEK_API_KEY": "   "})


def test_load_settings_placeholder():
    with pytest.raises(DeepSeekConfigurationError):
        load_settings({"DEEPSEEK_API_KEY": "changeme"})
    with pytest.raises(DeepSeekConfigurationError):
        load_settings({"DEEPSEEK_API_KEY": "CHANGEME"})


def test_settings_repr_no_secret():
    s = DeepSeekSettings(api_key=SecretStr("sk-test123"))
    r = repr(s)
    assert "***" in r
    assert "sk-test123" not in r


def test_settings_str_no_secret():
    s = DeepSeekSettings(api_key=SecretStr("sk-test123"))
    assert "***" in str(s)


def test_load_settings_reads_fixed_dotenv_when_mapping_is_omitted(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "_PROJECT_ENV_FILE", env_file)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    settings = load_settings()

    assert settings.api_key.get_secret_value() == "sk-from-dotenv"


def test_process_environment_takes_precedence_over_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "_PROJECT_ENV_FILE", env_file)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-process")

    settings = load_settings()

    assert settings.api_key.get_secret_value() == "sk-from-process"


def test_dotenv_value_is_not_interpolated(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=sk-$UNEXPANDED\nUNEXPANDED=wrong\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "_PROJECT_ENV_FILE", env_file)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    settings = load_settings()

    assert settings.api_key.get_secret_value() == "sk-$UNEXPANDED"
