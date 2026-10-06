"""Transactional, serialized forward-only M14 migrations; no upstream mutations."""
from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from app.artifact_persistence import load_artifact_persistence_settings
from app.web_application.errors import WebApplicationConfigurationError, WebApplicationDependencyError
from app.web_application.migrate import _verify_schema_transaction

def run():
    if context.is_offline_mode():
        raise WebApplicationConfigurationError()
    settings=load_artifact_persistence_settings()
    engine=create_engine(URL.create("postgresql+psycopg",username=settings.user,
        password=settings.password.get_secret_value(),host=settings.host,port=settings.port,database=settings.database),
        connect_args={"options":"-c timezone=UTC -c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SELECT pg_advisory_xact_lock(18951345809454157)"))
                for schema,head in (("artifact_store","m10_0002"),("durable_execution","m11_0002")):
                    if connection.execute(text(f"SELECT version_num FROM {schema}.alembic_version ORDER BY version_num")).fetchall()!=[(head,)]:
                        raise WebApplicationConfigurationError()
                connection.execute(text("CREATE SCHEMA IF NOT EXISTS web_application"))
                context.configure(connection=connection,target_metadata=None,transactional_ddl=True,
                    version_table="alembic_version",version_table_schema="web_application")
                with context.begin_transaction():
                    context.run_migrations()
                _verify_schema_transaction(connection)
    except WebApplicationConfigurationError:
        raise
    except Exception:
        raise WebApplicationDependencyError() from None
    finally:
        engine.dispose()

run()
