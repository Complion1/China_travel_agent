#!/usr/bin/env python
"""§16.3: Live capability probe for deepseek-v4-flash. Requires DEEPSEEK_API_KEY."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.llm_runtime.settings import load_settings
from app.llm_runtime.provider import LangChainDeepSeekAdapter
from app.llm_runtime.probe import run_capability_probe
from app.llm_runtime.runtime import DeepSeekRuntime


class RealSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    async def async_sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

settings = load_settings()
provider = LangChainDeepSeekAdapter(settings)
runtime = DeepSeekRuntime(provider=provider, sleeper=RealSleeper())
try:
    report = run_capability_probe(provider, runtime)
finally:
    asyncio.run(runtime.aclose())
print(report.model_dump_json(indent=2))
if not report.passed:
    sys.exit(1)
