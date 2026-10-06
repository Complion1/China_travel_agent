from datetime import datetime,date,timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import pytest
from pydantic import SecretStr
from app.requirements import VerifiedFieldEvidence, RequirementFieldPath
from app.artifact_persistence import ArtifactPersistenceSettings
from app.llm_runtime import DeepSeekSettings
from app.baidu_map import BaiduMapSettings, BaiduMapSessionError, BaiduMapRateLimitError
from app.web_application import assembly, WebApplicationConfigurationError
from .fakes import FixedClock, web_settings


def test_domain_clock_and_evidence_are_public_and_do_not_store_original_text():
    clock=assembly._RequirementClock(FixedClock(datetime(2026,10,5,16,0,tzinfo=timezone.utc)))
    assert clock.today() == date(2026,10,6)
    text="杭州合成测试证据"
    evidence=(VerifiedFieldEvidence(field_path=RequirementFieldPath.DESTINATION,text=text),)
    turn=uuid4()
    receipts=assembly.FingerprintEvidenceSink().accept(turn_id=turn,evidence=evidence)
    digest=hashlib.sha256(text.encode()).hexdigest()
    assert receipts[0].evidence_ref == f"evidence-fingerprint:{turn}:destination:{digest}"
    assert receipts[0].evidence_digest == "sha256:"+digest and text not in repr(receipts)


@pytest.mark.asyncio
async def test_map_fault_uses_public_error_no_reconnect_and_preserves_exception():
    signals=[]
    class Adapter:
        async def geocode(self,request): raise BaiduMapSessionError()
    proxy=assembly._GuardedMapAdapter(Adapter(),lambda:signals.append("fault"))
    with pytest.raises(BaiduMapSessionError): await proxy.geocode(object())
    assert signals == ["fault"]
    class RateLimited:
        async def geocode(self,request): raise BaiduMapRateLimitError()
    with pytest.raises(BaiduMapRateLimitError): await assembly._GuardedMapAdapter(RateLimited(),lambda:signals.append("fault")).geocode(object())
    assert signals == ["fault"]


@pytest.mark.asyncio
async def test_failed_own_schema_start_closes_pool_and_lock_without_vendor_calls(monkeypatch,tmp_path):
    order=[]
    monkeypatch.setattr(assembly,"load_pause_key",lambda **kwargs:b"k"*32)
    pg=ArtifactPersistenceSettings(host="isolated",port=5432,database="isolated",user="isolated",password=SecretStr("synthetic-long-password-32"))
    monkeypatch.setattr(assembly,"load_artifact_persistence_settings",lambda **kwargs:pg)
    monkeypatch.setattr(assembly,"load_durable_execution_settings",lambda **kwargs:object())
    monkeypatch.setattr(assembly,"load_settings",lambda **kwargs:DeepSeekSettings(api_key=SecretStr("synthetic-model-key")))
    monkeypatch.setattr(assembly,"load_baidu_map_settings",lambda **kwargs:BaiduMapSettings(api_key=SecretStr("synthetic-map-key")))
    class Pool:
        def __init__(self,*args,**kwargs): assert kwargs["max_size"] == 2; order.append("pool-create")
        async def open(self,**kwargs): order.append("pool-open")
        async def close(self): order.append("pool-close")
    class Connection:
        @classmethod
        async def connect(cls,**kwargs): order.append("lock-create"); return cls()
        async def execute(self,*args): return self
        async def fetchone(self): return (True,)
        async def close(self): order.append("lock-close")
    async def verify(connection): raise WebApplicationConfigurationError()
    monkeypatch.setattr(assembly,"AsyncConnectionPool",Pool)
    monkeypatch.setattr(assembly,"AsyncConnection",Connection)
    monkeypatch.setattr(assembly,"verify_schema",verify)
    async def forbidden(**kwargs): raise AssertionError("vendor must not start")
    monkeypatch.setattr(assembly,"create_artifact_persistence_runtime",forbidden)
    with pytest.raises(WebApplicationConfigurationError):
        async with assembly.production_service(settings=web_settings(),environ={}): pass
    assert order == ["pool-create","pool-open","lock-create","pool-close","lock-close"]
