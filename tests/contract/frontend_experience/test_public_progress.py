from inspect import signature
from app.frontend_experience import PublicProgressPage
from app.web_application import WebApplicationService
import pytest
from tests.contract.web_application.test_http_boundaries import boundary
from app.web_application.store import DecisionError


def test_public_read_contract_and_no_internal_fields():
    assert tuple(signature(WebApplicationService.read_progress).parameters)==("self","owner","run_id","after")
    assert set(PublicProgressPage.model_fields)=={"schema_version","run_id","items","next_after","latest_sequence","coverage","settled"}


@pytest.mark.parametrize("query",["after=-1","after=513","after=true","after=1&after=2","unknown=1","after=１","after=","after=1.0"])
def test_progress_rejects_invalid_parameters_before_read(boundary,query):
    client,service,_=boundary
    client.get("/api/session")
    async def forbidden(**kwargs): raise AssertionError("invalid query must not enter progress read")
    service.read_progress=forbidden
    response=client.get("/api/runs/00000000-0000-4000-8000-000000000001/progress?"+query)
    assert response.status_code==422 and response.headers["cache-control"]=="no-store"


def test_progress_authentication_public_response_and_foreign_task(boundary):
    client,service,_=boundary
    path="/api/runs/00000000-0000-4000-8000-000000000001/progress"
    assert client.get(path).status_code==401
    client.get("/api/session")
    calls=[]
    async def read(*,owner,run_id,after=0):
        calls.append(after)
        if after==1: raise DecisionError("not_found")
        return PublicProgressPage(run_id=run_id,items=(),latest_sequence=0,coverage="unavailable")
    service.read_progress=read
    response=client.get(path)
    assert response.status_code==200 and response.json()["coverage"]=="unavailable"
    assert response.headers["cache-control"]=="no-store" and "trace_id" not in response.text
    assert client.get(path+"?after=1").status_code==404
    assert calls==[0,1]
