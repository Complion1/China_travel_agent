"""Contract tests for the fake capability probe."""
from app.llm_runtime.models import CapabilityProbeReport, LLMErrorCode
from app.llm_runtime.probe import run_capability_probe
from app.llm_runtime.protocols import ProviderResponse
from app.llm_runtime.runtime import DeepSeekRuntime
from tests.unit.llm_runtime.fakes import RecordingSleeper, ScriptedProviderAdapter


USAGE = {
    "prompt_tokens": 10,
    "completion_tokens": 4,
    "total_tokens": 14,
    "reasoning_tokens": 2,
    "prompt_cache_hit_tokens": 3,
    "prompt_cache_miss_tokens": 7,
}


def test_capability_report_validator_corrects_contradiction():
    report = CapabilityProbeReport(
        models_endpoint_ok=False,
        thinking_ok=True,
        structured_output_ok=True,
        tool_call_ok=True,
        async_ok=True,
        usage_ok=True,
        passed=True,
    )
    assert report.passed is False


def test_probe_report_defaults_fail():
    report = CapabilityProbeReport()
    assert report.passed is False
    assert report.failures == ()


def test_probe_report_no_sensitive():
    report = CapabilityProbeReport()
    dumped = report.model_dump()
    assert "api_key" not in str(dumped)
    assert "prompt" not in str(dumped).lower()


def test_fake_probe_derives_every_capability_from_calls():
    provider = ScriptedProviderAdapter([
        ProviderResponse('{"status":"ok"}', (), "stop", "deepseek-v4-flash", USAGE, "private-1"),
        ProviderResponse(None, (("probe-call", "echo_probe", {"value": "probe"}),), "tool_calls", "deepseek-v4-flash", USAGE, "private-2"),
        ProviderResponse("done", (), "stop", "deepseek-v4-flash", USAGE, None),
        ProviderResponse('{"status":"ok"}', (), "stop", "deepseek-v4-flash", USAGE, "private-3"),
    ])
    report = run_capability_probe(provider, DeepSeekRuntime(provider, RecordingSleeper()))
    assert report.passed is True
    assert provider.call_count == 4
    assert len(provider.tool_continuations) == 1
    assert "private-" not in report.model_dump_json()


def test_fake_probe_reports_failed_real_steps():
    class MissingModelProvider(ScriptedProviderAdapter):
        def list_models(self) -> tuple[str, ...]:
            return ()

    provider = MissingModelProvider([
        ProviderResponse("not-json", (), "stop", "deepseek-v4-flash", USAGE, None),
        ProviderResponse("done", (), "stop", "deepseek-v4-flash", USAGE, None),
        ProviderResponse("not-json", (), "stop", "deepseek-v4-flash", USAGE, None),
    ])
    report = run_capability_probe(provider, DeepSeekRuntime(provider, RecordingSleeper()))
    assert report.passed is False
    assert report.models_endpoint_ok is False
    assert report.structured_output_ok is False
    assert report.tool_call_ok is False
    assert report.async_ok is False
    assert LLMErrorCode.MODEL_NOT_FOUND in report.failures


def test_probe_handles_models_and_async_control_plane_exceptions(monkeypatch):
    class BrokenModelsProvider(ScriptedProviderAdapter):
        def list_models(self) -> tuple[str, ...]:
            raise RuntimeError("offline")

    provider = BrokenModelsProvider([
        ProviderResponse('{"status":"ok"}', (), "stop", "deepseek-v4-flash", USAGE, None),
        ProviderResponse(None, (("probe-call", "echo_probe", {"value": "probe"}),), "tool_calls", "deepseek-v4-flash", USAGE, None),
        ProviderResponse("done", (), "stop", "deepseek-v4-flash", USAGE, None),
    ])
    def fail_async(coroutine):
        coroutine.close()
        raise RuntimeError("async unavailable")

    monkeypatch.setattr("app.llm_runtime.probe.asyncio.run", fail_async)
    report = run_capability_probe(provider, DeepSeekRuntime(provider, RecordingSleeper()))
    assert report.models_endpoint_ok is False
    assert report.async_ok is False
