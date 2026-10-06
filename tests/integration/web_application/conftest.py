"""Only consume a verified isolated identity; never parse a developer .env."""
import os
import psycopg
import pytest
import pytest_asyncio
from app.artifact_persistence import load_artifact_persistence_settings

@pytest.fixture(scope="session")
def pg_settings():
    prefix=os.environ.get("MODULE14_TEST_PREFIX","")
    token=os.environ.get("MODULE14_TEST_IDENTITY_TOKEN","")
    if not prefix.startswith("cta-m14-") or token!=prefix+"-identity":
        pytest.fail("M14 isolated identity required")
    settings=load_artifact_persistence_settings(environ=os.environ)
    if prefix.replace("-","_") not in settings.database:
        pytest.fail("M14 database outside isolated prefix")
    return settings

def connection_kwargs(settings):
    return dict(host=settings.host,port=settings.port,dbname=settings.database,user=settings.user,
        password=settings.password.get_secret_value(),autocommit=True,options="-c timezone=UTC")

@pytest_asyncio.fixture
async def pg_connection(pg_settings):
    async with await psycopg.AsyncConnection.connect(**connection_kwargs(pg_settings)) as connection:
        cursor=await connection.execute("SELECT current_database()")
        if await cursor.fetchone()!=(pg_settings.database,):
            pytest.fail("wrong isolated database")
        yield connection
