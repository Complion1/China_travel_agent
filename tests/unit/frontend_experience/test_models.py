from datetime import date, datetime, timezone
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.frontend_experience import PublicFact, PublicTrip, PublicRecordBody, PublicRecord, PublicProgressPage
from app.workflow_contracts import WorkflowPhase


def body(**changes):
    return PublicRecordBody(**(dict(phase=WorkflowPhase.REQUIREMENTS, kind="requirement", status="proposed",
        title="旅行需求", facts=(PublicFact(label="目的地", value="合肥"),)) | changes))


@pytest.mark.parametrize("changes", [dict(kind="unknown"), dict(title="任意标题"), dict(phase=WorkflowPhase.HOTEL_SELECTION),
    dict(status="done"), dict(extra="not public"), dict(facts=(PublicFact(label="条目",value="x"),)*25)])
def test_record_catalog_and_bounds(changes):
    with pytest.raises(ValidationError): body(**changes)


@pytest.mark.parametrize("days", [True, 0, 61, "3"])
def test_trip_strict_and_consistent(days):
    with pytest.raises(ValidationError):
        PublicTrip(city="合肥", start_date=date(2026,10,23), end_date=date(2026,10,25), days=days)


def test_utf8_limit_and_time():
    with pytest.raises(ValidationError): body(facts=tuple(PublicFact(label="标签"*30,value="中"*512) for _ in range(24)))
    with pytest.raises(ValidationError): PublicRecord(sequence=1,occurred_at=datetime(2026,10,16),body=body())
    with pytest.raises(ValidationError): PublicRecord(sequence=True,occurred_at=datetime.now(timezone.utc),body=body())


def test_page_validation_and_roundtrip():
    record=PublicRecord(sequence=1,occurred_at=datetime.now(timezone.utc),body=body())
    page=PublicProgressPage(run_id=uuid4(),items=(record,),latest_sequence=1,coverage="recorded",settled=True)
    assert PublicProgressPage.model_validate_json(page.model_dump_json(),strict=True)==page
    for changes in (dict(next_after=1),dict(items=(record,record)),dict(coverage="unavailable"),dict(latest_sequence=0)):
        with pytest.raises(ValidationError): PublicProgressPage.model_validate(page.model_dump() | changes,strict=True)


def test_legacy_day_and_strict_optional_reading():
    from app.web_application import WebResultDay
    from app.web_application.models import WebDayReading
    legacy = WebResultDay(schema_version="1.0", ordinal=1, markdown="原日块")
    assert legacy.reading is None
    reading = dict(date=date(2026,10,23), theme="初览合肥", visit_count=3, route_count=4, transport_minutes=130)
    current = legacy.model_copy(update={"reading":WebDayReading(**reading)})
    assert WebResultDay.model_validate_json(current.model_dump_json(),strict=True)==current
    for changes in (dict(visit_count=True), dict(visit_count=7), dict(route_count=8),
            dict(transport_minutes=2**53), dict(theme=""), dict(extra="internal")):
        with pytest.raises(ValidationError): WebDayReading(**(reading | changes))
