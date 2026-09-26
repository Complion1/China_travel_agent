"""Module 11 的在线 Alembic 环境。"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.durable_execution.errors import (
    DurableExecutionConfigurationError,
    DurableExecutionDependencyError,
)
from app.durable_execution.settings import load_durable_execution_settings


SCHEMA_NAME = "durable_execution"
VERSION_TABLE_NAME = "alembic_version"


def _dependency_failure() -> None:
    raise DurableExecutionDependencyError() from None


def run_migrations_online() -> None:
    try:
        settings = load_durable_execution_settings()
        url = URL.create(
            drivername="postgresql+psycopg",
            username=settings.user,
            password=settings.password.get_secret_value(),
            host=settings.host,
            port=settings.port,
            database=settings.database,
        )
        engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "options": "-c search_path=durable_execution,pg_catalog -c timezone=UTC",
            },
        )
    except DurableExecutionConfigurationError:
        raise
    except Exception:
        _dependency_failure()

    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("CREATE SCHEMA IF NOT EXISTS durable_execution"))
                context.configure(
                    connection=connection,
                    target_metadata=None,
                    transactional_ddl=True,
                    version_table=VERSION_TABLE_NAME,
                    version_table_schema=SCHEMA_NAME,
                )
                with context.begin_transaction():
                    context.run_migrations()
    except (
        DurableExecutionConfigurationError,
        DurableExecutionDependencyError,
    ):
        raise
    except Exception:
        _dependency_failure()
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


def run_migrations() -> None:
    if context.is_offline_mode():
        raise DurableExecutionConfigurationError() from None
    run_migrations_online()


run_migrations()
