"""Explicit production composition root; owns resources and closes in verified order."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import date
import hashlib
import logging
import os
import time

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.artifact_persistence import (create_artifact_persistence_runtime,
    load_artifact_persistence_settings, DEMO_INTERACTION_STORE_LIMITS)
from app.baidu_map import (create_baidu_map_runtime, load_baidu_map_settings,
    BaiduMapError, BaiduMapRateLimitError, BaiduMapRequestError,
    BaiduMapConcurrencyError, BaiduMapToolUnavailableError)
from app.llm_runtime import DeepSeekRuntime, load_settings
from app.llm_runtime.provider import LangChainDeepSeekAdapter
from app.requirements import RequirementDomainService, EvidenceReceipt
from app.requirement_extraction import DeepSeekRequirementExtractor
from app.hotel_selection import HOTEL_ARTIFACT_REGISTRATIONS, HotelCandidateSelectionService
from app.candidate_research import CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS, CandidateResearchService
from app.itinerary_planning import ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS, ItineraryPlanningService
from app.result_formatting import RESULT_FORMATTING_ARTIFACT_REGISTRATIONS, ResultFormattingService
from app.demo_workflow import (DemoDiagnostics, DemoWorkflowDependencies, DEMO_ARTIFACT_REGISTRATIONS,
    create_demo_workflow_definition)
from app.durable_execution import (create_durable_execution_runtime,
    load_durable_execution_settings, DEMO_DURABLE_EXECUTION_LIMITS)
from app.workflow_contracts import (ArtifactSchemaRegistry, ArtifactRepositoryLimits,
    SystemUtcClock, SystemMonotonicClock, RandomUUIDFactory, NoOpSafeWorkflowEventSink)

from .errors import WebApplicationConfigurationError, WebApplicationDependencyError
from .models import fresh, utc
from .settings import WebApplicationSettings
from .security import load_pause_key, EncryptedPauseCredentialSink
from .store import WebMetadataStore
from .service import RunBoundExtractionInvocationSink, WebApplicationDependencies, WebApplicationService
from .migrate import verify_schema

_INSTANCE_LOCK = 18951345809454160
_LOG = logging.getLogger(__name__)


class _Sleeper:
    def sleep(self, seconds):
        time.sleep(seconds)

    async def async_sleep(self, seconds):
        await asyncio.sleep(seconds)


class _RequirementClock:
    def __init__(self, clock):
        self._clock = clock

    def today(self) -> date:
        from zoneinfo import ZoneInfo
        return utc(self._clock.now()).astimezone(ZoneInfo("Asia/Shanghai")).date()


class FingerprintEvidenceSink:
    def accept(self, *, turn_id, evidence):
        return tuple(EvidenceReceipt(field_path=item.field_path,
            evidence_ref=f"evidence-fingerprint:{turn_id}:{item.field_path.value}:{hashlib.sha256(item.text.encode('utf-8')).hexdigest()}",
            evidence_digest="sha256:" + hashlib.sha256(item.text.encode("utf-8")).hexdigest()) for item in evidence)


class _GuardedMapAdapter:
    """Borrow the same qualified adapter; public fatal errors invalidate this root."""
    def __init__(self, adapter, fault):
        self._adapter, self._fault = adapter, fault

    async def _call(self, method, request):
        try:
            return await method(request)
        except BaiduMapError as error:
            if not isinstance(error, (BaiduMapRateLimitError, BaiduMapRequestError,
                    BaiduMapConcurrencyError, BaiduMapToolUnavailableError)):
                self._fault()
            raise
        except asyncio.CancelledError:
            self._fault()
            raise

    async def geocode(self, request):
        return await self._call(self._adapter.geocode, request)

    async def reverse_geocode(self, request):
        return await self._call(self._adapter.reverse_geocode, request)

    async def search_places(self, request):
        return await self._call(self._adapter.search_places, request)

    async def get_place_details(self, request):
        return await self._call(self._adapter.get_place_details, request)

    async def calculate_route_matrix(self, request):
        return await self._call(self._adapter.calculate_route_matrix, request)

    async def plan_directions(self, request):
        return await self._call(self._adapter.plan_directions, request)

    async def get_weather(self, request):
        return await self._call(self._adapter.get_weather, request)


@asynccontextmanager
async def production_service(*, settings: WebApplicationSettings, environ=None):
    """No migration, .env loading, test switch, alternate graph or provider client."""
    source = os.environ if environ is None else environ
    settings = fresh(settings, WebApplicationSettings)
    key = load_pause_key(path=settings.pause_key_file)
    pg = load_artifact_persistence_settings(environ=source)
    durable_settings = load_durable_execution_settings(environ=source)
    model_settings = load_settings(environ=source)
    map_settings = load_baidu_map_settings(environ=source)
    placeholders = {"__OFFLINE_ONLY__", "__DEEPSEEK_API_KEY_REQUIRED__", "__BAIDU_MAPS_API_KEY_REQUIRED__"}
    if (len(pg.password.get_secret_value()) < 16 or pg.password.get_secret_value().casefold() in {"changeme", "your-password"} or
            model_settings.api_key.get_secret_value() in placeholders or map_settings.api_key.get_secret_value() in placeholders):
        raise WebApplicationConfigurationError()
    clock = SystemUtcClock()
    pool = connection = artifacts = model = definition = durable = service = monitor = None
    map_stack = AsyncExitStack()
    closing = None
    fault = asyncio.Event()
    guard_lock = asyncio.Lock()
    primary_error = None

    def signal_fault():
        fault.set()
        if service is not None:
            service._closing = True

    async def guard():
        if fault.is_set() or connection is None or connection.closed:
            signal_fault()
            raise WebApplicationDependencyError()
        try:
            async with guard_lock:
                result = await connection.execute("SELECT 1")
                if await result.fetchone() != (1,):
                    raise ValueError()
        except Exception:
            signal_fault()
            raise WebApplicationDependencyError() from None

    async def shutdown():
        # If M11 or the application cannot confirm task termination, stop here.
        # The supplier resources and session lock must remain owned, not be released early.
        if service is not None:
            await service.close()
        if durable is not None:
            await durable.close()
        if service is not None:
            await service._after_durable_close()
        failures = []
        actions = []
        if definition is not None: actions.append(definition.aclose)
        actions.append(map_stack.aclose)
        if model is not None: actions.append(model.aclose)
        if artifacts is not None: actions.append(artifacts.close)
        if pool is not None: actions.append(pool.close)
        if connection is not None: actions.append(connection.close)
        for action in actions:
            try:
                await action()
            except BaseException as error:
                failures.append(error)
        if failures:
            raise WebApplicationDependencyError() from None

    async def close_once():
        nonlocal closing
        if closing is None:
            closing = asyncio.create_task(shutdown())
        await asyncio.shield(closing)

    async def supervise():
        while not fault.is_set():
            try:
                await asyncio.wait_for(fault.wait(), timeout=1)
            except TimeoutError:
                try:
                    await guard()
                except WebApplicationDependencyError:
                    break
        await close_once()

    try:
        kwargs = dict(host=pg.host, port=pg.port, dbname=pg.database, user=pg.user,
            password=pg.password.get_secret_value(), autocommit=True,
            options="-c timezone=UTC -c statement_timeout=2000 -c lock_timeout=2000")
        pool = AsyncConnectionPool("", kwargs=kwargs, min_size=1, max_size=2, open=False, timeout=2)
        await pool.open(wait=True, timeout=5)
        connection = await AsyncConnection.connect(**kwargs)
        result = await connection.execute("SELECT pg_try_advisory_lock(%s)", (_INSTANCE_LOCK,))
        if await result.fetchone() != (True,):
            raise WebApplicationConfigurationError()
        await verify_schema(connection)
        store = WebMetadataStore(settings=settings, pool=pool, key=key, clock=clock)
        store._set_guard(guard)
        limits = ArtifactRepositoryLimits(max_artifact_bytes=1048576, max_artifacts=settings.artifact_max_count,
            max_total_content_bytes=settings.artifact_max_total_bytes)
        registry = ArtifactSchemaRegistry(HOTEL_ARTIFACT_REGISTRATIONS + CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS +
            ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS + RESULT_FORMATTING_ARTIFACT_REGISTRATIONS + DEMO_ARTIFACT_REGISTRATIONS)
        artifacts = await create_artifact_persistence_runtime(settings=pg, registry=registry, limits=limits,
            interaction_limits=DEMO_INTERACTION_STORE_LIMITS, interaction_fallback_dir=None)
        model = DeepSeekRuntime(provider=LangChainDeepSeekAdapter(model_settings), sleeper=_Sleeper())
        maps = await map_stack.enter_async_context(create_baidu_map_runtime(settings=map_settings, clock=clock))
        adapter = _GuardedMapAdapter(maps.adapter, signal_fault)
        diagnostics = DemoDiagnostics(sink=NoOpSafeWorkflowEventSink(), clock=clock,
            monotonic_clock=SystemMonotonicClock(), uuid_provider=RandomUUIDFactory(),
            interaction_secrets=tuple(value.encode("utf-8") for value in (
                pg.password.get_secret_value(), model_settings.api_key.get_secret_value(), map_settings.api_key.get_secret_value())))
        extraction = RunBoundExtractionInvocationSink(diagnostics=diagnostics)
        domain = RequirementDomainService(extractor=DeepSeekRequirementExtractor(runtime=model, invocation_sink=extraction),
            evidence_sink=FingerprintEvidenceSink(), clock=_RequirementClock(clock))
        definition = create_demo_workflow_definition(dependencies=DemoWorkflowDependencies(
            domain_service=domain, diagnostics=diagnostics, durable_limits=DEMO_DURABLE_EXECUTION_LIMITS,
            artifact_limits=limits, interaction_recorder=artifacts.interaction_recorder,
            hotel_factory=lambda cap: HotelCandidateSelectionService(adapter, cap.artifact_repository, cap.execution_budget_manager, clock),
            research_factory=lambda cap: CandidateResearchService(runtime=model, map_adapter=adapter,
                artifact_repository=cap.artifact_repository, budget_manager=cap.execution_budget_manager),
            planning_factory=lambda cap: ItineraryPlanningService(runtime=model, map_adapter=adapter,
                artifact_repository=cap.artifact_repository, budget_manager=cap.execution_budget_manager)))
        durable = await create_durable_execution_runtime(settings=durable_settings,
            artifact_repository=artifacts.repository, contract=definition.contract, command_codec=definition.command_codec,
            budget_plan_provider=definition.budget_plan_provider, graph_factory=definition.graph_factory,
            limits=definition.durable_limits, pause_credential_sink=EncryptedPauseCredentialSink(store=store),
            cancellation_state_factory=definition.cancellation_state_factory)
        def disk_probe():
            stat = os.statvfs(settings.data_volume_path)
            return stat.f_bavail * stat.f_frsize
        service = WebApplicationService(settings=settings, dependencies=WebApplicationDependencies(
            artifacts=artifacts, durable=durable, definition=definition, diagnostics=diagnostics,
            extraction_sink=extraction, result_service=ResultFormattingService(artifact_repository=artifacts.repository),
            store=store, clock=clock, disk_probe=disk_probe))
        await service._startup_reconcile()
        await service._ready()
        monitor = asyncio.create_task(supervise())
        yield service
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if monitor is not None:
            if not monitor.done():
                monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        try:
            await close_once()
        except BaseException:
            if primary_error is None:
                raise
            _LOG.error("resource_cleanup_failed")
