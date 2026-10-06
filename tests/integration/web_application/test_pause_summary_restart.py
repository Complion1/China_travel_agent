"""Authenticated PG credentials and immutable, strictly checked terminal receipts."""
import json
import os
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from app.demo_workflow import make_cancelled_state
from app.web_application import WebApplicationConflictError, WebApplicationDependencyError
from app.web_application.security import EncryptedPauseCredentialSink, load_pause_key
from app.web_application.store import SummaryReceipt, DecisionError, WebMetadataStore
from tests.requirement_subgraph_fakes import make_initial_state, make_confirmed_transition
from tests.support.demo_workflow_fakes import diagnostics
from .test_admission_store import make_store, reserve_one
from .conftest import connection_kwargs


async def observed_summary(run_id=None, trace_id=None):
    state = make_initial_state()
    transition = make_confirmed_transition(state)
    state = type(state).model_validate({**state.model_dump(mode="python"),
        "run_id":run_id or state.run_id,"trace_id":trace_id or state.trace_id,
        "requirement_state":transition.state,"requirement_outcome":transition.outcome},strict=True)
    terminal = make_cancelled_state(state)
    observed = diagnostics()
    observed.start_run(run_id=terminal.run_id,trace_id=terminal.trace_id)
    summary = await observed.finish(state=terminal,budget_snapshots=())
    observed.release_run(run_id=terminal.run_id)
    return summary.summary


@pytest.mark.asyncio
async def test_pg_pause_replay_nonce_digest_generation_and_wrong_key(pg_settings):
    async with make_store(pg_settings,key=b"k"*32) as (store,database,clock):
        owner,_ = await store.bootstrap(cookie=None)
        record,_ = await reserve_one(store,owner)
        sink = EncryptedPauseCredentialSink(store=store)
        kwargs = dict(run_id=record.run_id,pause_generation=1,contract_digest="sha256:"+"a"*64,pause_token="b"*32)
        await sink.store(**kwargs)
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            before = await (await connection.execute("SELECT nonce,ciphertext FROM web_application.pause_credentials WHERE run_id=%s",(record.run_id,))).fetchone()
            assert b"b"*32 not in before[1] and len(before[0]) == 12
            await sink.store(**kwargs)
            assert await (await connection.execute("SELECT nonce,ciphertext FROM web_application.pause_credentials WHERE run_id=%s",(record.run_id,))).fetchone() == before
            await sink.store(**(kwargs|{"pause_generation":2}))
            nonces = await (await connection.execute("SELECT nonce FROM web_application.pause_credentials WHERE run_id=%s",(record.run_id,))).fetchall()
            assert len(set(nonce for nonce, in nonces)) == 2
        assert await store.read_pause(run_id=record.run_id,generation=1,digest=kwargs["contract_digest"]) == "b"*32
        with pytest.raises(WebApplicationConflictError):
            await sink.store(**(kwargs|{"pause_token":"c"*32}))
        for run,generation,digest in [(uuid4(),1,kwargs["contract_digest"]),(record.run_id,3,kwargs["contract_digest"]),(record.run_id,1,"sha256:"+"c"*64)]:
            with pytest.raises(DecisionError) as error:
                await store.read_pause(run_id=run,generation=generation,digest=digest)
            assert error.value.code == "pause_unavailable"
        # A different valid key authenticates neither old credentials nor a replay.
        other = WebMetadataStore(settings=store._settings,pool=store._pool,key=b"q"*32,clock=clock)
        with pytest.raises(DecisionError):
            await other.read_pause(run_id=record.run_id,generation=1,digest=kwargs["contract_digest"])


@pytest.mark.asyncio
async def test_real_summary_pg_replay_conflict_and_no_available_upgrade(pg_settings):
    async with make_store(pg_settings) as (store,_,_):
        owner,_ = await store.bootstrap(cookie=None)
        record,_ = await reserve_one(store,owner)
        summary = await observed_summary(record.run_id,uuid4())
        receipt = SummaryReceipt.freeze(run_id=summary.run_id,trace_id=summary.trace_id,
            terminal_code=summary.terminal_code,summary=summary)
        await store.save_summary(receipt=receipt)
        await store.save_summary(receipt=receipt)
        loaded = await store.get_summary(run_id=summary.run_id,trace_id=summary.trace_id,terminal_code=summary.terminal_code)
        assert loaded == receipt and loaded.summary() == summary
        unavailable = SummaryReceipt.freeze(run_id=summary.run_id,trace_id=summary.trace_id,
            terminal_code=summary.terminal_code,summary=None)
        with pytest.raises(WebApplicationConflictError):
            await store.save_summary(receipt=unavailable)
        second,_ = await reserve_one(store,owner,client_ip="192.0.2.2")
        missing_summary = await observed_summary(second.run_id,uuid4())
        missing = SummaryReceipt.freeze(run_id=second.run_id,trace_id=missing_summary.trace_id,
            terminal_code=missing_summary.terminal_code,summary=None)
        await store.save_summary(receipt=missing)
        assert (await store.get_summary(run_id=second.run_id,trace_id=missing.trace_id,terminal_code=missing.terminal_code)).summary() is None
        with pytest.raises(WebApplicationConflictError):
            await store.save_summary(receipt=SummaryReceipt.freeze(run_id=second.run_id,trace_id=missing.trace_id,
                terminal_code=missing.terminal_code,summary=missing_summary))


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption",["hash","identity","noncanonical","unsupported_schema"])
async def test_corruption_is_dependency_failure_not_missing_or_partial(pg_settings,corruption):
    import hashlib
    async with make_store(pg_settings) as (store,database,_):
        owner,_ = await store.bootstrap(cookie=None)
        record,_ = await reserve_one(store,owner)
        summary = await observed_summary(record.run_id,uuid4())
        receipt = SummaryReceipt.freeze(run_id=summary.run_id,trace_id=summary.trace_id,
            terminal_code=summary.terminal_code,summary=summary)
        await store.save_summary(receipt=receipt)
        data=json.loads(receipt.content_bytes)
        if corruption == "identity": data["trace_id"]=str(uuid4())
        if corruption == "unsupported_schema": data["schema_version"]="99.0"
        payload=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(",",":" )).encode()
        if corruption == "noncanonical": payload=b" "+payload
        digest="sha256:"+hashlib.sha256(payload).hexdigest() if corruption != "hash" else "sha256:"+"0"*64
        async with await psycopg.AsyncConnection.connect(**connection_kwargs(database)) as connection:
            await connection.execute("UPDATE web_application.run_summaries SET content_bytes=%s,size_bytes=%s,content_hash=%s WHERE run_id=%s",(payload,len(payload),digest,record.run_id))
        with pytest.raises(WebApplicationDependencyError) as error:
            await store.get_summary(run_id=summary.run_id,trace_id=summary.trace_id,terminal_code=summary.terminal_code)
        assert str(error.value) == "WebApplicationDependencyError" and error.value.__cause__ is None


@pytest.mark.asyncio
async def test_export_safe_restart_fixture_for_new_docker_process(pg_settings):
    root=Path(os.environ["MODULE14_EVIDENCE_DIR"])
    key=load_pause_key(path=root/"isolated-pause-key.json")
    async with make_store(pg_settings,key=key) as (store,database,_):
        owner,cookie=await store.bootstrap(cookie=None)
        record,_=await reserve_one(store,owner)
        summary=await observed_summary(record.run_id,uuid4())
        receipt=SummaryReceipt.freeze(run_id=record.run_id,trace_id=summary.trace_id,
            terminal_code=summary.terminal_code,summary=summary)
        await store.save_summary(receipt=receipt)
        digest="sha256:"+"a"*64
        await store.save_pause(run_id=record.run_id,pause_generation=1,contract_digest=digest,pause_token="c"*32)
        (root/"store-restart-fixture.json").write_text(json.dumps(dict(database=database.database,cookie=cookie,
            run_id=str(record.run_id),trace_id=str(summary.trace_id),terminal_code=summary.terminal_code,
            content_hash=receipt.content_hash,digest=digest)),encoding="utf-8")
