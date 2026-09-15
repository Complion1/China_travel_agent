"""Module 10 的在线、事务性 Alembic 环境。"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.artifact_persistence.errors import (
    ArtifactPersistenceConfigurationError,
    ArtifactPersistenceDependencyError,
)
from app.artifact_persistence.settings import load_artifact_persistence_settings


SCHEMA_NAME = "artifact_store"
VERSION_TABLE_NAME = "alembic_version"
_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS artifact_store"


def _safe_database_failure() -> None:
    raise ArtifactPersistenceDependencyError() from None


def run_migrations_online() -> None:
    """在一个连接和一个最外层事务中引导 schema 并执行 Alembic。"""

    try:
        settings = load_artifact_persistence_settings()
        url = URL.create(
            drivername="postgresql+psycopg",
            username=settings.user,
            password=settings.password.get_secret_value(),
            host=settings.host,
            port=settings.port,
            database=settings.database,
        )
        engine = create_engine(url, pool_pre_ping=True)
    except ArtifactPersistenceConfigurationError:
        raise
    except Exception:
        _safe_database_failure()

    try:
        try:
            with engine.connect() as connection:
                with connection.begin():
                    connection.execute(text(_CREATE_SCHEMA))
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
            ArtifactPersistenceConfigurationError,
            ArtifactPersistenceDependencyError,
        ):
            raise
        except Exception:
            _safe_database_failure()
    except BaseException:
        try:
            engine.dispose()
        except BaseException:
            pass
        raise

    try:
        engine.dispose()
    except Exception:
        _safe_database_failure()


def run_migrations() -> None:
    """只支持在线迁移；离线模式安全拒绝。"""

    if context.is_offline_mode():
        raise ArtifactPersistenceConfigurationError() from None
    run_migrations_online()


run_migrations()
