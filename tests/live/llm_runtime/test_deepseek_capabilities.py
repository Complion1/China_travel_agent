"""Live DeepSeek capability contract. Run this directory explicitly."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.llm_runtime import DeepSeekConfigurationError
from app.llm_runtime.probe import run_capability_probe
from app.llm_runtime.provider import LangChainDeepSeekAdapter
from app.llm_runtime.runtime import DeepSeekRuntime
from app.llm_runtime.settings import load_settings


class _RealSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    async def async_sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def test_deepseek_v4_flash_capabilities_live():
    try:
        settings = load_settings()
    except DeepSeekConfigurationError:
        pytest.fail(
            "DEEPSEEK_API_KEY is required in the process environment or project .env",
            pytrace=False,
        )
    provider = LangChainDeepSeekAdapter(settings)
    runtime = DeepSeekRuntime(provider=provider, sleeper=_RealSleeper())
    try:
        report = run_capability_probe(provider, runtime)
    finally:
        runtime.close()
    assert report.passed, report.model_dump(mode="json")
