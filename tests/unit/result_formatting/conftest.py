"""Shared persisted Module 12 scenario fixture."""

import pytest_asyncio

from tests.unit.result_formatting.fakes import make_scenario


@pytest_asyncio.fixture
async def scenario():
    return await make_scenario()
