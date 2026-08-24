from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import io
import inspect
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from pydantic import SecretStr

from app.baidu_map import (
    BaiduMapProtocolError,
    BaiduMapSettings,
    BaiduMapTransportError,
    GeocodeRequest,
)
from app.baidu_map import _mcp_gateway as gateway_module
from app.baidu_map._mcp_gateway import BaiduMapMcpGateway
from tests.support.baidu_map_fake_mcp import FakeMcpEnvironment, structured_result


class Clock:
    def now(self):
        return datetime(2026, 8, 20, tzinfo=timezone.utc)


async def _gateway(environment):
    from app.baidu_map import DEFAULT_BAIDU_MAP_LIMITS

    return await BaiduMapMcpGateway.create(
        settings=BaiduMapSettings(api_key=SecretStr("AK-CANARY-NEVER-LOG")),
        clock=Clock(), limits=DEFAULT_BAIDU_MAP_LIMITS, _client_factory=environment.factory,
    )


@pytest.mark.asyncio
async def test_public_wrapped_exception_has_no_supplier_context_or_payload() -> None:
    environment = FakeMcpEnvironment()
    environment.set_result("map_geocode", structured_result({"status": 0}, is_error=True))
    gateway = await _gateway(environment)
    with pytest.raises(BaiduMapProtocolError) as captured:
        await gateway.geocode(GeocodeRequest(address="ADDRESS-CANARY"))
    error = captured.value
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = f"{error!s} {error!r} {error.args!r}"
    assert "CANARY" not in rendered
    assert "status" not in rendered
    traceback = error.__traceback__
    while traceback is not None:
        for name, value in traceback.tb_frame.f_locals.items():
            if name in {"self"}:
                continue
            assert "ADDRESS-CANARY" not in repr(value)
            assert "AK-CANARY" not in repr(value)
        traceback = traceback.tb_next
    await gateway.aclose()


@pytest.mark.asyncio
async def test_log_record_factory_redacts_provider_namespace_and_preserves_application() -> None:
    environment = FakeMcpEnvironment()
    before = logging.getLogRecordFactory()
    gateway = await _gateway(environment)
    factory = logging.getLogRecordFactory()
    provider = factory(
        "mcp.client.streamable_http", logging.INFO, __file__, 1,
        "https://mcp.map.baidu.com/mcp?ak=AK-CANARY raw SESSION-CANARY", (), None,
    )
    application = factory("application.mcp", logging.INFO, __file__, 1, "APP-CANARY", (), None)
    assert "CANARY" not in provider.getMessage()
    assert application.getMessage() == "APP-CANARY"
    await gateway.aclose()
    assert logging.getLogRecordFactory() is before


@pytest.mark.asyncio
async def test_log_guard_covers_sdk_path_root_client_exception_and_extra_canaries() -> None:
    spec = importlib.util.find_spec("mcp")
    assert spec is not None and spec.submodule_search_locations
    sdk_path = str(Path(next(iter(spec.submodule_search_locations))) / "client" / "sensitive.py")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s %(provider_payload)s", defaults={"provider_payload": "clean"}))
    provider_logger = logging.getLogger("mcp.security.contract")
    prior = (provider_logger.level, provider_logger.propagate, tuple(provider_logger.handlers))
    provider_logger.setLevel(logging.DEBUG)
    provider_logger.propagate = False
    provider_logger.addHandler(handler)
    gateway = await _gateway(FakeMcpEnvironment())
    try:
        try:
            raise RuntimeError("EXCEPTION-CANARY")
        except RuntimeError:
            provider_logger.info(
                "AK-ENDPOINT-CANARY raw SESSION-ID-CANARY",
                extra={"provider_payload": "PAYLOAD-CANARY"},
                exc_info=True,
            )
        factory = logging.getLogRecordFactory()
        root_record = factory("root", logging.DEBUG, sdk_path, 1, "ROOT-CANARY", (), None)
        client_record = factory("client", logging.INFO, sdk_path, 1, "CLIENT-CANARY", (), None)
        app_record = factory("root", logging.INFO, __file__, 1, "APP-PATH-CANARY", (), None)
        assert "CANARY" not in root_record.getMessage()
        assert "CANARY" not in client_record.getMessage()
        assert app_record.getMessage() == "APP-PATH-CANARY"
        assert "CANARY" not in stream.getvalue()
    finally:
        await gateway.aclose()
        provider_logger.removeHandler(handler)
        provider_logger.setLevel(prior[0])
        provider_logger.propagate = prior[1]
        provider_logger.handlers[:] = list(prior[2])


@pytest.mark.asyncio
async def test_log_guard_sanitizes_handlers_added_after_guard_before_formatter() -> None:
    gateway = await _gateway(FakeMcpEnvironment())
    sdk_spec = importlib.util.find_spec("mcp")
    assert sdk_spec is not None and sdk_spec.submodule_search_locations
    sdk_path = str(Path(next(iter(sdk_spec.submodule_search_locations))) / "client" / "runtime_added.py")
    configured: list[tuple[logging.Logger, tuple[int, bool, bool, tuple[logging.Handler, ...]]]] = []

    def configure(name: str) -> tuple[logging.Logger, io.StringIO]:
        logger = logging.getLogger(name)
        configured.append((logger, (logger.level, logger.disabled, logger.propagate, tuple(logger.handlers))))
        logger.setLevel(logging.DEBUG)
        logger.disabled = False
        logger.propagate = False
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(
            logging.Formatter(
                "%(message)s %(provider_payload)s %(endpoint)s",
                defaults={"provider_payload": "clean", "endpoint": "clean"},
            )
        )
        logger.addHandler(handler)
        return logger, stream

    try:
        provider_logger, provider_stream = configure("mcp.runtime_added")
        provider_logger.info(
            "AK-ENDPOINT-CANARY raw SESSION-ID-CANARY",
            extra={
                "provider_payload": "PROVIDER-PAYLOAD-CANARY",
                "endpoint": "https://mcp.map.baidu.com/mcp?ak=AK-CANARY",
            },
        )
        assert "[baidu-map-provider-log-redacted] clean clean" in provider_stream.getvalue()
        assert "CANARY" not in provider_stream.getvalue()

        for logger_name, marker in (("root", "ROOT"), ("client", "CLIENT")):
            sdk_logger, sdk_stream = configure(logger_name)
            record = logging.getLogRecordFactory()(
                logger_name, logging.INFO, sdk_path, 1, f"{marker}-MESSAGE-CANARY", (), None
            )
            record.provider_payload = f"{marker}-PAYLOAD-CANARY"
            record.endpoint = "https://mcp.map.baidu.com/mcp?ak=AK-CANARY"
            sdk_logger.handle(record)
            assert "[baidu-map-provider-log-redacted] clean clean" in sdk_stream.getvalue()
            assert "CANARY" not in sdk_stream.getvalue()

        application_logger, application_stream = configure("application.runtime_added")
        application_handler = application_logger.handlers[-1]
        application_handler.setFormatter(
            logging.Formatter("%(message)s %(application_extra)s")
        )
        application_logger.info(
            "APP-MESSAGE-CANARY", extra={"application_extra": "APP-EXTRA-CANARY"}
        )
        assert "APP-MESSAGE-CANARY APP-EXTRA-CANARY" in application_stream.getvalue()
    finally:
        for logger, prior in reversed(configured):
            level, disabled, propagate, handlers = prior
            logger.handlers[:] = list(handlers)
            logger.setLevel(level)
            logger.disabled = disabled
            logger.propagate = propagate
        await gateway.aclose()


def test_endpoint_has_exactly_one_ak_query_and_redirects_classify_as_transport() -> None:
    endpoint = gateway_module._build_endpoint(
        BaiduMapSettings(api_key=SecretStr("offline key with spaces"))
    )
    parsed = urlsplit(endpoint)
    assert (parsed.scheme, parsed.netloc, parsed.path, parsed.fragment) == (
        "https", "mcp.map.baidu.com", "/mcp", ""
    )
    assert parse_qs(parsed.query) == {"ak": ["offline key with spaces"]}
    assert endpoint.count("?") == 1 and endpoint.count("ak=") == 1
    source = Path(gateway_module.__file__).read_text(encoding="utf-8")
    assert "follow_redirects=False" in source
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(302, request=request)
    error = httpx.HTTPStatusError("redirect", request=request, response=response)
    assert gateway_module._classify_transport(error) is BaiduMapTransportError
    assert gateway_module._classify_transport(McpError(ErrorData(code=-1, message="offline"))) is BaiduMapProtocolError
    assert gateway_module._is_under("\0", ()) is False
    gateway_module._release_log_guard()


def test_package_root_discovery_fails_closed_when_sensitive_namespace_is_absent(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module.importlib.util, "find_spec", lambda _: None)
    assert gateway_module._package_roots() == ()


@pytest.mark.asyncio
async def test_overlapping_runtimes_restore_factory_only_after_last_close_reverse_order() -> None:
    before = logging.getLogRecordFactory()
    first = await _gateway(FakeMcpEnvironment())
    installed = logging.getLogRecordFactory()
    second = await _gateway(FakeMcpEnvironment())
    assert logging.getLogRecordFactory() is installed
    await first.aclose()
    assert logging.getLogRecordFactory() is installed
    await second.aclose()
    assert logging.getLogRecordFactory() is before


@pytest.mark.asyncio
async def test_application_factory_change_during_guard_is_not_overwritten() -> None:
    before = logging.getLogRecordFactory()
    environment = FakeMcpEnvironment()
    gateway = await _gateway(environment)
    replacement = lambda *args, **kwargs: logging.LogRecord(*args, **kwargs)
    logging.setLogRecordFactory(replacement)
    await gateway.aclose()
    assert logging.getLogRecordFactory() is replacement
    logging.setLogRecordFactory(before)


@pytest.mark.asyncio
async def test_application_call_handlers_change_during_guard_is_not_overwritten() -> None:
    before = logging.Logger.callHandlers
    gateway = await _gateway(FakeMcpEnvironment())
    replacement = before
    try:
        logging.Logger.callHandlers = replacement  # type: ignore[assignment]
        await gateway.aclose()
        assert logging.Logger.callHandlers is replacement
    finally:
        logging.Logger.callHandlers = before  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_cancelled_sent_call_releases_raw_references_before_delayed_finish() -> None:
    environment = FakeMcpEnvironment()
    provider_entered = asyncio.Event()
    provider_cancelled = asyncio.Event()
    finish_entered = asyncio.Event()
    finish_release = asyncio.Event()
    provider_error: asyncio.CancelledError | None = None

    async def handler(arguments):
        nonlocal provider_error
        assert arguments["address"] == "ADDRESS-CANARY"
        provider_entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError as error:
            provider_error = error
            provider_cancelled.set()
            raise

    environment.set_handler("map_geocode", handler)
    gateway = await _gateway(environment)
    original_finish = gateway._finish

    async def delayed_finish(task, **kwargs):
        finish_entered.set()
        await finish_release.wait()
        return await original_finish(task, **kwargs)

    gateway._finish = delayed_finish  # type: ignore[method-assign]
    call = asyncio.create_task(gateway.geocode(GeocodeRequest(address="ADDRESS-CANARY")))
    await provider_entered.wait()
    call.cancel("cancel-message")
    await provider_cancelled.wait()
    await finish_entered.wait()

    stack = list(call.get_stack())
    for frame in stack:
        assert frame.f_locals.get("request") is None
        assert frame.f_locals.get("fresh") is None
        assert frame.f_locals.get("arguments") is None
        for name, value in frame.f_locals.items():
            if name != "self":
                rendered = repr(value)
                assert "ADDRESS-CANARY" not in rendered
                assert "cancel-message" not in rendered
        invoke = frame.f_locals.get("invoke")
        if inspect.iscoroutine(invoke) and invoke.cr_frame is not None:
            invoke_frame = invoke.cr_frame
            assert invoke_frame.f_locals.get("request") is None
            assert invoke_frame.f_locals.get("arguments") is None
            for name, value in invoke_frame.f_locals.items():
                if name != "self":
                    rendered = repr(value)
                    assert "ADDRESS-CANARY" not in rendered
                    assert "CallToolRequest" not in rendered
                    assert "CallToolRequestParams" not in rendered
                    assert "CallToolResult" not in rendered

    finish_release.set()
    with pytest.raises(asyncio.CancelledError) as captured:
        await call
    assert provider_error is not None
    assert captured.value is provider_error
    assert captured.value.args == ("cancel-message",)
    traceback = captured.value.__traceback__
    while traceback is not None:
        if "app/baidu_map" in traceback.tb_frame.f_code.co_filename.replace("\\", "/"):
            for name, value in traceback.tb_frame.f_locals.items():
                    if name != "self":
                        rendered = repr(value)
                        assert "ADDRESS-CANARY" not in rendered
                        assert "CallToolRequest" not in rendered
                    assert "CallToolRequestParams" not in rendered
                    assert "CallToolResult" not in rendered
        traceback = traceback.tb_next
    assert environment.call_count == 1
    assert gateway._in_flight == set()
    assert gateway._semaphore._value == 3
    assert gateway._broken is True
    await gateway.aclose()
