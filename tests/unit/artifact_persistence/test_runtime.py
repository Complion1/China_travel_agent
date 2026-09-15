"""AP-T05 Runtime lifecycle and factory cleanup tests."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest
from pydantic import SecretStr
from psycopg_pool import AsyncConnectionPool

import app.artifact_persistence.runtime as runtime_module
from app.artifact_persistence import (
    ArtifactPersistenceConfigurationError,
    ArtifactPersistenceDependencyError,
    ArtifactPersistenceRuntime,
    ArtifactPersistenceSettings,
    create_artifact_persistence_runtime,
)
from app.artifact_persistence.repository import _PostgresArtifactRepository
from app.workflow_contracts.artifacts.in_memory import DEMO_ARTIFACT_REPOSITORY_LIMITS
from app.workflow_contracts.artifacts.models import ArtifactRepositoryLimits
from app.workflow_contracts.artifacts.registry import (
    ArtifactSchemaRegistration,
    ArtifactSchemaRegistry,
)
from app.workflow_contracts.errors import ArtifactRepositoryUsageError
from tests.unit.workflow_contracts.fakes import StrictSampleArtifact


class _FakePool(AsyncConnectionPool):
    instances: list["_FakePool"] = []

    def __init__(self, conninfo, **kwargs):
        self.conninfo = conninfo
        self.constructor_kwargs = kwargs
        self.open_calls: list[tuple[bool, float]] = []
        self.wait_calls: list[float] = []
        self.close_calls: list[float] = []
        self.is_closed = False
        self.close_error: BaseException | None = None
        type(self).instances.append(self)

    @property
    def closed(self) -> bool:
        return self.is_closed

    async def open(self, wait: bool = False, timeout: float = 30.0) -> None:
        self.open_calls.append((wait, timeout))

    async def wait(self, timeout: float = 30.0) -> None:
        self.wait_calls.append(timeout)

    async def close(self, timeout: float = 5.0) -> None:
        self.close_calls.append(timeout)
        self.is_closed = True
        if self.close_error is not None:
            raise self.close_error


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class _QueryCursor:
    def __init__(self, *, fetchones=(), fetchalls=()):
        self.fetchones = list(fetchones)
        self.fetchalls = list(fetchalls)

    async def execute(self, statement, parameters=None):
        return None

    async def fetchone(self):
        return self.fetchones.pop(0)

    async def fetchall(self):
        return self.fetchalls.pop(0)


class _QueryConnection:
    def __init__(self, cursor: _QueryCursor):
        self._cursor = cursor

    def transaction(self):
        return _AsyncContext(self)

    def cursor(self):
        return _AsyncContext(self._cursor)


class _QueryPool:
    def __init__(self, cursor: _QueryCursor | None = None, error: BaseException | None = None):
        self._cursor = cursor
        self._error = error

    def connection(self, **kwargs):
        if self._error is not None:
            raise self._error
        assert self._cursor is not None
        return _AsyncContext(_QueryConnection(self._cursor))


def _settings() -> ArtifactPersistenceSettings:
    return ArtifactPersistenceSettings(
        host="postgres",
        port=5432,
        database="module10_test",
        user="module10_user",
        password=SecretStr("runtime-secret-sentinel"),
    )


def _registry() -> ArtifactSchemaRegistry:
    return ArtifactSchemaRegistry(
        (
            ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.0",
                model=StrictSampleArtifact,
            ),
        )
    )


@pytest.mark.asyncio
async def test_factory_uses_fixed_pool_parameters_and_explicit_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakePool.instances.clear()
    monkeypatch.setattr(runtime_module, "AsyncConnectionPool", _FakePool)

    async def noop(*args):
        return None

    monkeypatch.setattr(runtime_module, "_check_session", noop)
    monkeypatch.setattr(runtime_module, "_check_schema", noop)
    monkeypatch.setattr(runtime_module, "_initialize_capacity", noop)
    monkeypatch.setattr(runtime_module, "_code_head", lambda: "m10_0001")

    runtime = await create_artifact_persistence_runtime(
        settings=_settings(),
        registry=_registry(),
    )
    pool = _FakePool.instances[-1]
    assert pool.conninfo == ""
    assert pool.constructor_kwargs["min_size"] == 1
    assert pool.constructor_kwargs["max_size"] == 4
    assert pool.constructor_kwargs["open"] is False
    assert pool.constructor_kwargs["timeout"] == 10.0
    assert pool.constructor_kwargs["kwargs"]["connect_timeout"] == 10
    assert "password" in pool.constructor_kwargs["kwargs"]
    assert "statement_timeout=10000" in pool.constructor_kwargs["kwargs"]["options"]
    assert "lock_timeout=5000" in pool.constructor_kwargs["kwargs"]["options"]
    assert pool.open_calls == [(False, 10.0)]
    assert pool.wait_calls == [10.0]
    assert runtime.repository is not None

    await runtime.close()
    await runtime.close()
    assert pool.close_calls == [5.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage",
    ["pool_construction", "open", "wait", "session", "code_head", "schema", "capacity"],
)
async def test_factory_closes_created_pool_after_each_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    _FakePool.instances.clear()
    with monkeypatch.context() as local:
        if failure_stage == "pool_construction":
            def fail_constructor(*args, **kwargs):
                raise RuntimeError("pool construction failure")

            local.setattr(runtime_module, "AsyncConnectionPool", fail_constructor)
        else:
            local.setattr(runtime_module, "AsyncConnectionPool", _FakePool)

            async def fail(*args):
                raise RuntimeError("synthetic failure")

            async def noop(*args):
                return None

            local.setattr(runtime_module, "_check_session", noop)
            local.setattr(runtime_module, "_check_schema", noop)
            local.setattr(runtime_module, "_initialize_capacity", noop)
            local.setattr(runtime_module, "_code_head", lambda: "m10_0001")

            if failure_stage == "open":
                async def fail_open(self, wait: bool = False, timeout: float = 30.0) -> None:
                    self.open_calls.append((wait, timeout))
                    raise RuntimeError("open failure")

                local.setattr(_FakePool, "open", fail_open)
            elif failure_stage == "wait":
                async def fail_wait(self, timeout: float = 30.0) -> None:
                    self.wait_calls.append(timeout)
                    raise RuntimeError("wait failure")

                local.setattr(_FakePool, "wait", fail_wait)
            elif failure_stage == "session":
                local.setattr(runtime_module, "_check_session", fail)
            elif failure_stage == "code_head":
                def fail_head() -> str:
                    raise RuntimeError("code head failure")

                local.setattr(runtime_module, "_code_head", fail_head)
            elif failure_stage == "schema":
                local.setattr(runtime_module, "_check_schema", fail)
            elif failure_stage == "capacity":
                local.setattr(runtime_module, "_initialize_capacity", fail)

        with pytest.raises(ArtifactPersistenceDependencyError):
            await create_artifact_persistence_runtime(
                settings=_settings(),
                registry=_registry(),
            )

    if failure_stage == "pool_construction":
        assert _FakePool.instances == []
    else:
        assert _FakePool.instances[-1].close_calls == [5.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("base_error", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
async def test_factory_preserves_base_exception_and_closes_pool(
    monkeypatch: pytest.MonkeyPatch,
    base_error: type[BaseException],
) -> None:
    _FakePool.instances.clear()
    monkeypatch.setattr(runtime_module, "AsyncConnectionPool", _FakePool)

    async def fail(*args):
        raise base_error()

    monkeypatch.setattr(runtime_module, "_check_session", fail)
    with pytest.raises(base_error):
        await create_artifact_persistence_runtime(
            settings=_settings(),
            registry=_registry(),
        )
    assert _FakePool.instances[-1].close_calls == [5.0]


@pytest.mark.asyncio
async def test_factory_rejects_forged_settings_registry_and_limits_before_pool_creation() -> None:
    with pytest.raises(ArtifactRepositoryUsageError):
        await create_artifact_persistence_runtime(
            settings=object(),  # type: ignore[arg-type]
            registry=_registry(),
        )

    with pytest.raises(ArtifactRepositoryUsageError):
        await create_artifact_persistence_runtime(
            settings=_settings(),
            registry=object(),  # type: ignore[arg-type]
        )

    forged_limits = ArtifactRepositoryLimits.model_construct(
        max_artifact_bytes=0,
        max_artifacts=1,
        max_total_content_bytes=1,
    )
    with pytest.raises(ArtifactRepositoryUsageError):
        await create_artifact_persistence_runtime(
            settings=_settings(),
            registry=_registry(),
            limits=forged_limits,
        )

    forged_settings = ArtifactPersistenceSettings.model_construct(
        host="postgres",
        port=5432,
        database="module10_test",
        user="module10_user",
        password=SecretStr(""),
    )
    with pytest.raises(ArtifactRepositoryUsageError):
        await create_artifact_persistence_runtime(
            settings=forged_settings,
            registry=_registry(),
        )


def test_code_head_rejects_loader_failure_and_multiple_heads(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_loader(config):
        raise RuntimeError("synthetic loader failure")

    monkeypatch.setattr(runtime_module.ScriptDirectory, "from_config", fail_loader)
    with pytest.raises(ArtifactPersistenceConfigurationError):
        runtime_module._code_head()

    class _MultipleHeads:
        def get_heads(self):
            return ["m10_0001", "m10_0002"]

    monkeypatch.setattr(
        runtime_module.ScriptDirectory,
        "from_config",
        lambda config: _MultipleHeads(),
    )
    with pytest.raises(ArtifactPersistenceConfigurationError):
        runtime_module._code_head()


@pytest.mark.asyncio
async def test_session_checks_map_mismatch_to_configuration_and_connection_error_to_dependency() -> None:
    mismatch = _QueryPool(
        _QueryCursor(fetchones=[("UTC", False, True, "read committed")])
    )
    with pytest.raises(ArtifactPersistenceConfigurationError):
        await runtime_module._check_session(mismatch)

    unavailable = _QueryPool(error=runtime_module.psycopg.OperationalError("down"))
    with pytest.raises(ArtifactPersistenceDependencyError):
        await runtime_module._check_session(unavailable)


@pytest.mark.asyncio
async def test_schema_and_capacity_checks_cover_fail_fast_and_singleton_initialization() -> None:
    bad_schema = _QueryPool(_QueryCursor(fetchalls=[[]]))
    with pytest.raises(ArtifactPersistenceConfigurationError):
        await runtime_module._check_schema(bad_schema, "m10_0001")

    limits = ArtifactRepositoryLimits(
        max_artifact_bytes=4096,
        max_artifacts=8,
        max_total_content_bytes=32768,
    )
    capacity_row = (1, 4096, 8, 32768, 0, 0)
    missing_capacity = _QueryPool(
        _QueryCursor(fetchones=[None, (False,), capacity_row])
    )
    await runtime_module._initialize_capacity(missing_capacity, limits)

    bad_capacity = _QueryPool(_QueryCursor(fetchones=[(1, 4096, 8, 32768, 9, 0)]))
    with pytest.raises(ArtifactPersistenceConfigurationError):
        await runtime_module._initialize_capacity(bad_capacity, limits)

    dependency = _QueryPool(error=runtime_module.psycopg.OperationalError("down"))
    with pytest.raises(ArtifactPersistenceDependencyError):
        await runtime_module._initialize_capacity(dependency, limits)


@pytest.mark.asyncio
async def test_context_manager_preserves_business_error_when_close_fails() -> None:
    pool = _FakePool("", open=False)
    pool.close_error = RuntimeError("close failure")
    repository = _PostgresArtifactRepository(
        pool=pool,
        registry=_registry(),
        limits=DEMO_ARTIFACT_REPOSITORY_LIMITS,
        loop=asyncio.get_running_loop(),
    )
    runtime = ArtifactPersistenceRuntime(
        pool=pool,
        repository=repository,
        loop=asyncio.get_running_loop(),
    )
    with pytest.raises(ValueError, match="business"):
        async with runtime:
            raise ValueError("business")


@pytest.mark.asyncio
async def test_context_manager_maps_close_failure_without_business_error() -> None:
    pool = _FakePool("", open=False)
    pool.close_error = RuntimeError("close failure")
    repository = _PostgresArtifactRepository(
        pool=pool,
        registry=_registry(),
        limits=DEMO_ARTIFACT_REPOSITORY_LIMITS,
        loop=asyncio.get_running_loop(),
    )
    runtime = ArtifactPersistenceRuntime(
        pool=pool,
        repository=repository,
        loop=asyncio.get_running_loop(),
    )
    with pytest.raises(ArtifactPersistenceDependencyError):
        async with runtime:
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("base_error", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
async def test_runtime_close_preserves_base_exception_and_remains_idempotent(
    base_error: type[BaseException],
) -> None:
    pool = _FakePool("", open=False)
    pool.close_error = base_error()
    repository = _PostgresArtifactRepository(
        pool=pool,
        registry=_registry(),
        limits=DEMO_ARTIFACT_REPOSITORY_LIMITS,
        loop=asyncio.get_running_loop(),
    )
    runtime = ArtifactPersistenceRuntime(
        pool=pool,
        repository=repository,
        loop=asyncio.get_running_loop(),
    )
    with pytest.raises(base_error):
        await runtime.close()
    await runtime.close()
    assert pool.close_calls == [5.0]


@pytest.mark.asyncio
async def test_runtime_and_repository_reject_use_in_a_real_second_event_loop() -> None:
    pool = _FakePool("", open=False)
    repository = _PostgresArtifactRepository(
        pool=pool,
        registry=_registry(),
        limits=DEMO_ARTIFACT_REPOSITORY_LIMITS,
        loop=asyncio.get_running_loop(),
    )
    runtime = ArtifactPersistenceRuntime(
        pool=pool,
        repository=repository,
        loop=asyncio.get_running_loop(),
    )

    async def enter_runtime() -> None:
        await runtime.__aenter__()

    async def lookup_repository() -> None:
        await repository.lookup(operation_id=object())

    with pytest.raises(ArtifactRepositoryUsageError):
        await asyncio.to_thread(asyncio.run, enter_runtime())
    with pytest.raises(ArtifactRepositoryUsageError):
        await asyncio.to_thread(asyncio.run, lookup_repository())
    await runtime.close()


@pytest.mark.asyncio
async def test_closed_runtime_rejects_repository_methods_before_database_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _FakePool("", open=False)
    repository = _PostgresArtifactRepository(
        pool=pool,
        registry=_registry(),
        limits=DEMO_ARTIFACT_REPOSITORY_LIMITS,
        loop=asyncio.get_running_loop(),
    )
    runtime = ArtifactPersistenceRuntime(
        pool=pool,
        repository=repository,
        loop=asyncio.get_running_loop(),
    )

    def forbidden_database_call(*args, **kwargs):
        raise AssertionError("database call after runtime close")

    monkeypatch.setattr(_FakePool, "connection", forbidden_database_call, raising=False)
    await runtime.close()

    with pytest.raises(ArtifactRepositoryUsageError):
        await repository.put(object())
    with pytest.raises(ArtifactRepositoryUsageError):
        await repository.lookup(operation_id=object())
    with pytest.raises(ArtifactRepositoryUsageError):
        await repository.get(object(), expected_model=StrictSampleArtifact)
