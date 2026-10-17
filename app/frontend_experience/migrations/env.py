"""Explicit transactional migration, serialized independently from older schemas."""
from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from app.artifact_persistence import load_artifact_persistence_settings
from app.frontend_experience import PublicProgressError
from app.frontend_experience.migrate import verify_transaction


def run():
    if context.is_offline_mode():
        raise PublicProgressError()
    settings = load_artifact_persistence_settings()
    engine = create_engine(URL.create("postgresql+psycopg", username=settings.user,
        password=settings.password.get_secret_value(), host=settings.host, port=settings.port, database=settings.database),
        connect_args={"options":"-c timezone=UTC -c statement_timeout=10000 -c lock_timeout=2000"})
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SELECT pg_advisory_xact_lock(18951345809454178)"))
            if connection.execute(text("SELECT version_num FROM web_application.alembic_version")).fetchall() != [("m14_0001_web_application",)]:
                raise PublicProgressError()
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS frontend_experience"))
            context.configure(connection=connection, target_metadata=None, transactional_ddl=True,
                version_table="alembic_version", version_table_schema="frontend_experience")
            with context.begin_transaction():
                context.run_migrations()
            verify_transaction(connection)
    except Exception:
        raise PublicProgressError() from None
    finally:
        engine.dispose()


run()
