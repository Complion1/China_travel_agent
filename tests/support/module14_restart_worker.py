"""New Docker interpreter verifies old PG receipts/results/pause without business replay."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID,uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

import pytest
from psycopg_pool import AsyncConnectionPool
from app.artifact_persistence import load_artifact_persistence_settings
from app.web_application import WebMetadataStore,WebHotelRequest
from app.web_application.security import load_pause_key,canonical_json
from app.web_application.store import DecisionError
from tests.unit.web_application.fakes import (FixedClock,web_settings,offline_boundaries,offline_factory,
    wait_for_view,wait_for_summary)
from tests.integration.web_application.conftest import connection_kwargs


def database_settings(data):
    prefix=os.environ.get("MODULE14_TEST_PREFIX","")
    if not prefix.startswith("cta-m14-") or os.environ.get("MODULE14_TEST_IDENTITY_TOKEN") != prefix+"-identity":
        raise ValueError()
    settings=load_artifact_persistence_settings(environ=os.environ)
    database=data["database"]
    if type(database) is not str or not database.startswith(settings.database+"_"):
        raise ValueError()
    return settings.model_copy(update={"database":database})


async def read_store(root):
    data=json.loads((root/"store-restart-fixture.json").read_text(encoding="utf-8"))
    database=database_settings(data)
    pool=AsyncConnectionPool("",kwargs=connection_kwargs(database),min_size=1,max_size=2,open=False)
    await pool.open(wait=True,timeout=5)
    try:
        clock=FixedClock()
        store=WebMetadataStore(settings=web_settings(),pool=pool,key=load_pause_key(path=root/"isolated-pause-key.json"),clock=clock)
        owner=await store.validate_cookie(cookie=data["cookie"])
        run,trace=UUID(data["run_id"]),UUID(data["trace_id"])
        await store.owns(owner=owner,run_id=run)
        receipt=await store.get_summary(run_id=run,trace_id=trace,terminal_code=data["terminal_code"])
        assert receipt.content_hash == data["content_hash"] and receipt.summary().run_id == run
        assert await store.read_pause(run_id=run,generation=1,digest=data["digest"]) == "c"*32
        wrong=WebMetadataStore(settings=web_settings(),pool=pool,key=b"q"*32,clock=clock)
        assert await wrong.get_summary(run_id=run,trace_id=trace,terminal_code=data["terminal_code"]) == receipt
        try:
            await wrong.read_pause(run_id=run,generation=1,digest=data["digest"])
        except DecisionError as error:
            assert error.code == "pause_unavailable"
        else:
            raise AssertionError()
    finally:
        await pool.close()


async def read_workflow(root,mode):
    data=json.loads((root/"workflow-restart-fixture.json").read_text(encoding="utf-8"))
    database=database_settings(data)
    provider,maps=await offline_boundaries()
    settings=web_settings(pause_key_file=root/"isolated-pause-key.json")
    with pytest.MonkeyPatch.context() as patch:
        factory=offline_factory(monkeypatch=patch,settings=settings,pg_settings=database,provider=provider,maps=maps)
        async with factory() as service:
            owner=await service._authenticate(cookie=data["cookie"])
            run,pause_run=UUID(data["run_id"]),UUID(data["pause_run_id"])
            summary=await service.read_summary(owner=owner,run_id=run)
            result=await service.read_result(owner=owner,run_id=run)
            assert summary.status == "saved" and str(summary.summary.trace_id) == data["trace_id"]
            assert "sha256:"+hashlib.sha256(canonical_json(summary.summary.model_dump(mode="json"))).hexdigest() == data["summary_hash"]
            assert hashlib.sha256(result.markdown.encode()).hexdigest() == data["result_hash"] and len(result.days) == 1
            assert "2026-09-03" in result.days[0].markdown and "2026-09-02" in result.markdown and "2026-09-04" in result.markdown
            waiting=await service.get_run(owner=owner,run_id=pause_run)
            if mode == "read":
                assert waiting.run.activity == "awaiting_user" and waiting.card.kind == "hotel"
                assert waiting.run.pause_generation == data["pause_generation"]
                assert (await service.read_summary(owner=owner,run_id=pause_run)).status == "not_finalized"
            elif mode == "decline":
                assert waiting.card.kind == "hotel"
                await service.resume(owner=owner,run_id=pause_run,request=WebHotelRequest(schema_version="1.0",request_id=uuid4(),
                    pause_generation=data["pause_generation"],decision="decline",ordinal=None))
                await wait_for_view(service,owner,pause_run,activity="terminal")
                unavailable=await wait_for_summary(service,owner,pause_run,status="process_history_unavailable")
                assert unavailable.summary is None
            else:
                assert waiting.run.activity == "terminal"
                assert (await service.read_summary(owner=owner,run_id=pause_run)).status == "process_history_unavailable"
            assert provider.calls == maps.calls == 0
    assert provider.closed and maps.closed


async def run(mode):
    root=Path(os.environ["MODULE14_EVIDENCE_DIR"])
    await read_store(root)
    await read_workflow(root,mode)
    print(json.dumps(dict(status="PASS",mode=mode,store_summary=True,workflow_result=True,
        model_calls=0,map_calls=0,new_interpreter=True),sort_keys=True))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=("read","decline","after-decline"),default="read")
    args=parser.parse_args()
    try:
        asyncio.run(run(args.mode))
    except Exception:
        print("M14 restart gate failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
