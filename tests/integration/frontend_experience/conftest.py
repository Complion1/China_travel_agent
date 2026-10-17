import os
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool
from app.frontend_experience import PublicProgressStore
from tests.integration.web_application.conftest import pg_settings, pg_connection, connection_kwargs
from tests.unit.web_application.fakes import FixedClock


@pytest_asyncio.fixture
async def progress_store(pg_settings,pg_connection):
    prefix=os.environ.get("MODULE24_TEST_PREFIX","")
    assert prefix.startswith("cta-m24-") and os.environ.get("MODULE24_TEST_IDENTITY_TOKEN")==prefix+"-identity"
    assert prefix.replace("-","_") in pg_settings.database
    await pg_connection.execute("TRUNCATE frontend_experience.public_records,frontend_experience.progress_runs")
    await pg_connection.execute("UPDATE frontend_experience.capacity SET record_count=0")
    async with AsyncConnectionPool("",kwargs=connection_kwargs(pg_settings),min_size=1,max_size=2) as pool:
        yield PublicProgressStore(pool=pool,clock=FixedClock())
