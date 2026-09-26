"""Module 11 的唯一显式迁移入口。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.durable_execution.errors import (
    DurableExecutionConfigurationError,
    DurableExecutionDependencyError,
)
from app.durable_execution.repository import (
    EXPECTED_REVISION,
    check_durable_execution_schema,
    migration_advisory_key,
)
from app.durable_execution.settings import DurableExecutionSettings, load_durable_execution_settings


_ALEMBIC_CONFIG = Path(__file__).with_name("alembic.ini")
_LOCK_TIMEOUT_SECONDS = 5


def _connection_kwargs(settings: DurableExecutionSettings) -> dict[str, object]:
    return {
        "host": settings.host,
        "port": settings.port,
        "dbname": settings.database,
        "user": settings.user,
        "password": settings.password.get_secret_value(),
    }


async def _run_migration(settings: DurableExecutionSettings) -> None:
    lock_connection: AsyncConnection | None = None
    lock_key = migration_advisory_key(domain="migration")
    try:
        lock_connection = await AsyncConnection.connect(
            **_connection_kwargs(settings),
            autocommit=True,
            options="-c search_path=durable_execution,pg_catalog -c timezone=UTC",
        )
        await lock_connection.execute("SET statement_timeout = '5s'")
        await lock_connection.execute("SELECT pg_advisory_lock(%s)", (lock_key,))

        config = Config(str(_ALEMBIC_CONFIG))
        await asyncio.to_thread(command.upgrade, config, "head")

        async with await AsyncConnection.connect(
            **_connection_kwargs(settings),
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
            options="-c search_path=durable_execution,pg_catalog -c timezone=UTC",
        ) as saver_connection:
            saver = AsyncPostgresSaver(saver_connection)
            await saver.setup()

        async with await AsyncConnection.connect(
            **_connection_kwargs(settings),
            autocommit=True,
            options="-c search_path=durable_execution,pg_catalog -c timezone=UTC",
        ) as verification_connection:
            await check_durable_execution_schema(
                verification_connection,
                expected_revision=EXPECTED_REVISION,
            )
    except (
        DurableExecutionConfigurationError,
        DurableExecutionDependencyError,
    ):
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise DurableExecutionDependencyError() from None
    finally:
        if lock_connection is not None:
            try:
                await lock_connection.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    (lock_key,),
                )
            except BaseException:
                pass
            try:
                await lock_connection.close()
            except BaseException:
                pass


async def main() -> None:
    settings = load_durable_execution_settings()
    await _run_migration(settings)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (DurableExecutionConfigurationError, DurableExecutionDependencyError):
        raise SystemExit(1)
