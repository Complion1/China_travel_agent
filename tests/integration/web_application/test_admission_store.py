"""Real short transactions and persistent anonymous/quota/handoff boundaries."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import secrets
from uuid import uuid4

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool
from app.web_application import WebMetadataStore, WebStartRequest, WebApplicationDependencyError, WebApplicationUsageError
from app.web_application.security import ip_hash
from app.web_application.store import DecisionError
from tests.unit.web_application.fakes import FixedClock, web_settings
from .test_migrations import scratch, legacy, migrate
from .conftest import connection_kwargs


@asynccontextmanager
async def make_store(pg_settings, *, settings=None, clock=None, key=None):
    database = await scratch(pg_settings,"s")
    legacy(database)
    assert migrate(database).returncode == 0
    pool = AsyncConnectionPool("", kwargs=connection_kwargs(database), min_size=1, max_size=2, open=False, timeout=2)
    await pool.open(wait=True, timeout=5)
    clock = clock or FixedClock()
    try:
        yield WebMetadataStore(settings=settings or web_settings(), pool=pool, key=key or secrets.token_bytes(32), clock=clock), database, clock
    finally:
        await pool.close()


async def reserve_one(store, owner, *, request=None, run=None, client_ip="192.0.2.1"):
    request = request or WebStartRequest(schema_version="1.0", request_id=uuid4(), user_message="新离线配额测试")
    fingerprint = store.fingerprint(route="start", run_id=None, generation=None, body=request.model_dump(mode="json"))
    return await store.reserve(owner=owner, request_id=request.request_id, run_id=run or uuid4(), fingerprint=fingerprint, ip=ip_hash(client_ip))


@pytest.mark.asyncio
async def test_fixed_thirty_day_bootstrap_never_slides_or_deletes(pg_settings):
    async with make_store(pg_settings) as (store, database, clock):
        owner, cookie = await store.bootstrap(cookie=None)
        assert cookie is not None and owner.expires_at - clock.now() == timedelta(days=30)
        clock.value += timedelta(days=10)
        same, replacement = await store.bootstrap(cookie=cookie)
        assert same == owner and replacement is None
        clock.value += timedelta(days=20)
        assert await store.validate_cookie(cookie=cookie) is None
        new_owner, replacement = await store.bootstrap(cookie=cookie)
        assert replacement != cookie and new_owner.visitor_hash != owner.visitor_hash
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT count(*) FROM web_application.visitors")).fetchone() == (2,)
        with pytest.raises(DecisionError) as error:
            await store.validate_owner(owner=owner)
        assert error.value.code == "csrf_rejected"


@pytest.mark.asyncio
async def test_exact_200_20_5_boundaries_and_counted_failure_never_refunds(pg_settings):
    async with make_store(pg_settings) as (store, database, clock):
        first, _ = await store.bootstrap(cookie=None)
        for i in range(5):
            record, created = await reserve_one(store, first)
            assert created
            await store.mark_request(record=record, status="dispatched")
            assert (await store.mark_request(record=record,status="rejected",reason="dependency_unavailable",release_admission=True)).status == "dispatched"
        with pytest.raises(DecisionError) as error:
            await reserve_one(store, first)
        assert error.value.code == "quota_exceeded" and error.value.retry_after_seconds == 3600
        for i in range(5,20):
            await reserve_one(store,first,client_ip=f"192.0.2.{i+1}")
        with pytest.raises(DecisionError):
            await reserve_one(store,first,client_ip="198.51.100.1")
        for batch in range(9):
            owner, _ = await store.bootstrap(cookie=None)
            for i in range(20):
                await reserve_one(store,owner,client_ip=f"2001:db8::{batch*20+i+1:x}")
        owner, _ = await store.bootstrap(cookie=None)
        quota = await store.quota(owner=owner, ip=ip_hash("203.0.113.1"))
        assert (quota.global_remaining,quota.visitor_remaining,quota.ip_remaining) == (0,20,5)
        with pytest.raises(DecisionError):
            await reserve_one(store,owner,client_ip="203.0.113.1")
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT count(*) FROM web_application.admissions WHERE status IN ('reserved','counted')")).fetchone() == (200,)


@pytest.mark.asyncio
async def test_rolling_open_lower_closed_upper_and_shanghai_midnight(pg_settings):
    from datetime import datetime, timezone
    clock = FixedClock(datetime(2026,10,5,15,59,59,tzinfo=timezone.utc))
    async with make_store(pg_settings, settings=web_settings(daily_global_limit=1, hourly_ip_limit=1), clock=clock) as (store,_,_):
        owner,_ = await store.bootstrap(cookie=None)
        await reserve_one(store,owner)
        assert (await store.quota(owner=owner,ip=ip_hash("192.0.2.1"))).retry_after_seconds == 3600
        clock.value += timedelta(seconds=1)
        quota = await store.quota(owner=owner,ip=ip_hash("192.0.2.1"))
        assert (quota.global_remaining,quota.ip_remaining,quota.retry_after_seconds) == (1,0,3599)
        clock.value += timedelta(seconds=3599)
        assert (await store.quota(owner=owner,ip=ip_hash("192.0.2.1"))).ip_remaining == 1
        # A future fixture timestamp is outside the closed upper bound now.
        clock.value -= timedelta(seconds=3601)
        assert (await store.quota(owner=owner,ip=ip_hash("192.0.2.1"))).ip_remaining == 1


@pytest.mark.asyncio
async def test_two_connections_one_atomic_reservation_no_partial_rows(pg_settings):
    async with make_store(pg_settings, settings=web_settings(daily_global_limit=1)) as (store,database,_):
        first,_ = await store.bootstrap(cookie=None)
        second,_ = await store.bootstrap(cookie=None)
        results = await asyncio.gather(reserve_one(store,first),reserve_one(store,second),return_exceptions=True)
        assert sum(type(item) is tuple for item in results) == 1
        assert sum(isinstance(item,DecisionError) and item.code == "quota_exceeded" for item in results) == 1
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            for table in ("run_owners","requests","admissions"):
                assert await (await connection.execute(f"SELECT count(*) FROM web_application.{table}")).fetchone() == (1,)


@pytest.mark.asyncio
async def test_same_id_concurrent_replay_conflict_and_failed_insert_rollback(pg_settings):
    async with make_store(pg_settings) as (store,database,_):
        owner,_ = await store.bootstrap(cookie=None)
        request = WebStartRequest(schema_version="1.0",request_id=uuid4(),user_message="相同正文")
        results = await asyncio.gather(reserve_one(store,owner,request=request),reserve_one(store,owner,request=request))
        assert sum(created for _,created in results) == 1 and results[0][0].run_id == results[1][0].run_id
        changed = WebStartRequest(schema_version="1.0",request_id=request.request_id,user_message="不同正文")
        with pytest.raises(DecisionError) as error:
            await reserve_one(store,owner,request=changed)
        assert error.value.code == "request_conflict"
        with pytest.raises(WebApplicationDependencyError):
            await reserve_one(store,owner,run=results[0][0].run_id)
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            assert await (await connection.execute("SELECT count(*) FROM web_application.requests")).fetchone() == (1,)
        assert (await store.get_request(owner=owner,request_id=request.request_id)).receipt().status == "confirming"


@pytest.mark.asyncio
async def test_pause_claim_unknown_retains_generation_known_reject_releases(pg_settings):
    async with make_store(pg_settings) as (store,_,_):
        owner,_ = await store.bootstrap(cookie=None)
        start,_ = await reserve_one(store,owner)
        kwargs = dict(owner=owner,run_id=start.run_id,kind="confirm",generation=1,fingerprint="a"*64)
        first,_ = await store.claim_control(request_id=uuid4(),**kwargs)
        await store.mark_request(record=first,status="uncertain",reason="dependency_unavailable")
        with pytest.raises(DecisionError) as error:
            await store.claim_control(request_id=uuid4(),**kwargs)
        assert error.value.code == "stale_pause"
        await store.mark_request(record=first,status="rejected",reason="stale_pause")
        second,created = await store.claim_control(request_id=uuid4(),**kwargs)
        assert created and second.request_id != first.request_id
        dispatched = await store.mark_request(record=second,status="dispatched")
        await store.mark_request(record=dispatched,status="rejected",reason="stale_pause")
        assert (await store.claim_control(request_id=uuid4(),**kwargs))[1]


@pytest.mark.asyncio
async def test_owner_cursor_twenty_rows_and_no_cross_visitor_access(pg_settings):
    async with make_store(pg_settings,settings=web_settings(daily_visitor_limit=30,hourly_ip_limit=30)) as (store,_,_):
        owner,_ = await store.bootstrap(cookie=None)
        other,_ = await store.bootstrap(cookie=None)
        runs=[]
        for _ in range(21):
            record,_ = await reserve_one(store,owner)
            runs.append(record.run_id)
        page,cursor = await store.history(owner=owner)
        assert len(page) == 20 and cursor is not None
        second,next_cursor = await store.history(owner=owner,cursor=cursor)
        assert len(second) == 1 and next_cursor is None and set(run for _,run in (*page,*second)) == set(runs)
        with pytest.raises(WebApplicationUsageError):
            await store.history(owner=other,cursor=cursor)
        for run in (runs[0],uuid4()):
            with pytest.raises(DecisionError) as error:
                await store.owns(owner=other,run_id=run)
            assert error.value.code == "not_found"
        assert await store.history(owner=other) == ((),None)
