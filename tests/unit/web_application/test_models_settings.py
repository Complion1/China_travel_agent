import json
from pathlib import Path
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.web_application import *
from app.web_application.settings import load_web_application_settings
from app.web_application.models import fresh
from .fakes import web_settings

def start(**changes):
    return dict(schema_version="1.0", request_id=str(uuid4()), user_message="杭州旅游") | changes

@pytest.mark.parametrize("changes", [{"extra": "secret"}, {"user_message": ""}, {"user_message": "字"*8001}, {"schema_version":"2.0"}, {"request_id":False}])
def test_start_rejects_extra_and_invalid(changes):
    with pytest.raises(ValidationError):
        WebStartRequest.model_validate_json(json.dumps(start(**changes)))

def test_character_limit_and_fresh_construct_boundary():
    request = WebStartRequest.model_validate_json(json.dumps(start(user_message="字"*8000)))
    assert len(request.user_message.encode()) == 24000
    with pytest.raises(ValidationError):
        fresh(request.model_copy(update={"user_message": "x"*8001}), WebStartRequest)
    with pytest.raises(ValidationError):
        request.user_message = "other"

def test_uuid_must_be_canonical_json():
    for raw in [str(uuid4()).upper(), uuid4().hex]:
        with pytest.raises(ValidationError):
            WebStartRequest.model_validate_json(json.dumps(start(request_id=raw)))


def test_utc_dates_survive_strict_json_roundtrip_without_type_coercion():
    from datetime import datetime,timezone,date
    owner=WebVisitorContext(schema_version="1.0",visitor_hash="a"*64,expires_at=datetime(2026,10,5,tzinfo=timezone.utc))
    session=WebSessionView(schema_version="1.0",csrf_token="a"*43,expires_at=owner.expires_at,visitor_ttl_seconds=2592000)
    sample=WebSampleView(schema_version="1.0",slug="demo",title="合成样例",generated_date=date(2026,10,5),
        source_kind="synthetic",realtime=False,markdown="完整样例")
    for value in (owner,session,sample):
        assert fresh(value,type(value)) == value
    for timestamp in ("2026-10-05T00:00:00", "2026-10-05T08:00:00+08:00",123):
        with pytest.raises(ValidationError):
            WebVisitorContext.model_validate_json(json.dumps(dict(schema_version="1.0",visitor_hash="a"*64,expires_at=timestamp)),strict=True)
    with pytest.raises(ValidationError):
        fresh(session.model_copy(update={"visitor_ttl_seconds":2592000.0}),WebSessionView)
    with pytest.raises(ValidationError):
        fresh(sample.model_copy(update={"realtime":0}),WebSampleView)

@pytest.mark.parametrize("generation", [True, -1, 2**53, 1.0, "1"])
def test_generation_is_strict_safe_integer(generation):
    with pytest.raises(ValidationError):
        WebClarifyRequest.model_validate_json(json.dumps(start(pause_generation=generation)))

@pytest.mark.parametrize("changes", [
    dict(decision="accept",revision_text="x",map_requested=False),
    dict(decision="revise",revision_text=None,map_requested=False),
    dict(decision="revise",revision_text="x",map_requested=True)])
def test_confirmation_union(changes):
    with pytest.raises(ValidationError):
        WebConfirmRequest(schema_version="1.0", request_id=uuid4(), pause_generation=0, **changes)

def test_hotel_and_admission_and_receipt_unions():
    with pytest.raises(ValidationError):
        WebHotelRequest(schema_version="1.0",request_id=uuid4(),pause_generation=0,decision="decline",ordinal=1)
    with pytest.raises(ValidationError):
        WebAdmissionView(schema_version="1.0",can_start=True,global_remaining=0,visitor_remaining=1,ip_remaining=1,reason=None,retry_after_seconds=None)
    with pytest.raises(ValidationError):
        WebCommandReceipt(schema_version="1.0",request_id=uuid4(),run_id=None,status="uncertain",reason=None)

@pytest.mark.parametrize("field,value", [("port",True),("visitor_ttl_seconds",1),("public_origin","https://example.com"),
    ("public_origin","http://127.0.0.1:8080/"),("public_origin","http://user@127.0.0.1"),
    ("pause_key_file",Path("relative")),("min_free_disk_bytes",-1)])
def test_settings_reject_bad_configuration(field,value):
    data=web_settings().model_dump()
    data[field]=value
    with pytest.raises(ValidationError):
        WebApplicationSettings(**data)

@pytest.mark.parametrize("origin", ["http://127.0.0.1:80", "http://localhost:",
    "http://127.0.0.1:08081", "http://[2001:0db8::1]:8080", "http://[fe80::1%eth0]:8080"])
def test_origin_rejects_browser_normalization_and_scopes(origin):
    data=web_settings().model_dump() | {"public_origin":origin}
    with pytest.raises(ValidationError):
        WebApplicationSettings(**data)

@pytest.mark.parametrize("origin", ["http://127.0.0.1", "http://localhost:8080", "http://203.0.113.10:8080", "http://[2001:db8::1]:8080"])
def test_browser_canonical_origins_are_accepted(origin):
    settings=WebApplicationSettings(**(web_settings().model_dump() | {"public_origin":origin}))
    assert settings.public_origin==origin

def test_explicit_environment_and_no_secret_repr():
    settings=load_web_application_settings(environ={"WEB_PUBLIC_ORIGIN":"http://localhost:8080",
        "WEB_PAUSE_KEY_FILE":"/private/key.json","WEB_DATA_VOLUME_PATH":"/data"})
    assert settings.host == "localhost:8080"
    assert "/private/key.json" not in repr(settings)
    for changes in [{"WEB_PORT":"True"},{"WEB_BAD":"1"},{"WEB_PAUSE_KEY_FILE":"relative"}]:
        with pytest.raises(WebApplicationConfigurationError):
            load_web_application_settings(environ={"WEB_PUBLIC_ORIGIN":"http://127.0.0.1",
                "WEB_PAUSE_KEY_FILE":"/tmp/key","WEB_DATA_VOLUME_PATH":"/tmp"} | changes)

def test_fixed_summary_and_error_projections():
    for status,reason in [("not_finalized",None),("pending_save",None),("not_saved","summary_not_saved"),
                         ("process_history_unavailable","process_history_unavailable")]:
        assert WebSummaryView(schema_version="1.0",status=status,summary=None,reason=reason).status == status
    with pytest.raises(ValidationError):
        WebApiError(schema_version="1.0",code="busy",message="private exception",request_id=None,retry_after_seconds=None)


def question_payload(**changes):
    return dict(schema_version="1.0", field="party.children_count", label="儿童数量", kind="number",
        options=[], minimum=0, maximum=100, item_count=None) | changes


@pytest.mark.parametrize("changes", [
    {"extra":"private"}, {"field":"not_a_field"}, {"field":"budget.total_cny"},
    {"minimum":True}, {"maximum":"100"}, {"minimum":-1}, {"maximum":101},
    {"minimum":2,"maximum":1}, {"minimum":None}, {"kind":"text"},
    {"item_count":1}, {"kind":"ages","item_count":0}, {"kind":"ages","item_count":True},
    {"kind":"single","minimum":None,"maximum":None,"options":[]},
    {"options":[{"schema_version":"1.0","value":"x","label":"无"}]},
    {"kind":"single","minimum":None,"maximum":None,"options":[
        {"schema_version":"1.0","value":"same","label":"甲"},
        {"schema_version":"1.0","value":"same","label":"乙"}]}])
def test_clarification_question_rejects_illegal_public_shapes(changes):
    with pytest.raises(ValidationError):
        WebClarificationQuestion.model_validate_json(json.dumps(question_payload(**changes)),strict=True)


def test_clarification_card_compatibility_unique_fields_and_nested_forgery():
    child = WebClarificationQuestion.model_validate_json(json.dumps(question_payload()),strict=True)
    data=dict(schema_version="1.0",kind="clarification",title="补充旅行需求",paragraphs=("问题",),facts=(),choices=())
    assert WebInteractionCard(**data).questions == ()
    assert fresh(WebInteractionCard(**data,questions=(child,)),WebInteractionCard).questions == (child,)
    for changes in (dict(kind="hotel",questions=(child,)),dict(questions=(child,child))):
        with pytest.raises(ValidationError):
            WebInteractionCard(**(data|changes))
    bad=child.model_copy(update={"minimum":True})
    with pytest.raises(ValidationError):
        fresh(WebInteractionCard.model_construct(**data,questions=(bad,)),WebInteractionCard)
    for value in ("", "unsafe value", "A", "x"*65):
        with pytest.raises(ValidationError):
            WebClarificationOption(schema_version="1.0",value=value,label="选项")


async def clarification_card(requirement, *, issues=(), metadata=None):
    from app.requirements import RequirementDomainState, NeedClarification, FieldMetadataRegistry
    from app.workflow_contracts import TravelSessionState
    from tests.requirement_subgraph_fakes import make_initial_state
    from app.web_application.presentation import interaction_card
    domain=RequirementDomainState(requirement=requirement,metadata=metadata or FieldMetadataRegistry(),issues=issues)
    outcome=NeedClarification(requirement=domain.requirement,metadata=domain.metadata,issues=issues,message="领域当前问题 canary")
    initial=make_initial_state()
    state=TravelSessionState.model_validate(dict(initial.model_dump(mode="python"),
        requirement_state=domain,requirement_outcome=outcome),strict=True)
    return await interaction_card(state=state,repository=None)


@pytest.mark.asyncio
async def test_real_domain_missing_questions_and_no_private_projection():
    from datetime import date
    from app.requirements import RequirementDomainService, RequirementTurnInput, RequirementDomainState, RequirementIntent, RequirementFieldPath, StrictFieldEvidenceDTO
    from app.workflow_contracts import TravelSessionState
    from tests.unit.requirements.fakes import RecordingRequirementExtractor, InMemoryEvidenceSink, FixedClock, strict_dto
    from tests.requirement_subgraph_fakes import make_initial_state
    from app.web_application.presentation import interaction_card
    service=RequirementDomainService(extractor=RecordingRequirementExtractor([
        strict_dto(destination="杭州",evidence=[StrictFieldEvidenceDTO(field_path=RequirementFieldPath.DESTINATION,text="杭州")])]),
        evidence_sink=InMemoryEvidenceSink(),clock=FixedClock(date(2026,10,5)))
    transition=await service.process_turn(RequirementTurnInput(schema_version="1.0",turn_id=uuid4(),
        user_message="杭州",session_date=date(2026,10,5),timezone="Asia/Shanghai",intent=RequirementIntent.INITIAL,current=RequirementDomainState()))
    state=TravelSessionState.model_validate(dict(make_initial_state().model_dump(mode="python"),
        requirement_state=transition.state,requirement_outcome=transition.outcome),strict=True)
    card=await interaction_card(state=state,repository=None)
    fields={question.field.value for question in card.questions}
    assert "destination" not in fields and "preferences.interests" in fields and "preferences.pace" in fields
    assert "party.children_count" in fields and "party.children_ages" not in fields
    assert "preferences.hotel_preferences" not in fields
    assert not any(field.startswith("budget.") for field in fields)
    assert set(json.loads(card.model_dump_json())) == {"schema_version","kind","title","paragraphs","facts","choices","questions"}
    assert not any(key in card.model_dump_json() for key in ("source_turn_id","evidence_ref","pause_token","normalization"))


@pytest.mark.asyncio
async def test_complete_fields_agent_decision_and_pending_confirmation_are_not_reasked():
    from app.requirements import FieldMetadataRegistry, FieldMetadata, FieldStatus, RequirementFieldPath
    from tests.unit.result_formatting.fakes import make_confirmed
    requirement=(await make_confirmed(budget_total_cny=None)).outcome.requirement.model_copy(deep=True)
    requirement.preferences.interests=None;requirement.preferences.agent_decides_interests=True
    requirement.preferences.pace=None;requirement.preferences.agent_decides_pace=True
    metadata=FieldMetadataRegistry(metadata={RequirementFieldPath.SIGHTSEEING_START_DATE:FieldMetadata(status=FieldStatus.PENDING_CONFIRMATION)})
    card=await clarification_card(requirement,metadata=metadata)
    assert card.questions == () and card.paragraphs == ("领域当前问题 canary",)


@pytest.mark.asyncio
async def test_empty_interests_and_agent_decision_issue_paths_have_choices():
    from app.requirements import RequirementIssue, RequirementFieldPath
    from tests.unit.result_formatting.fakes import make_confirmed
    requirement=(await make_confirmed(budget_total_cny=None)).outcome.requirement.model_copy(deep=True)
    requirement.preferences.interests=[]
    card=await clarification_card(requirement)
    assert [question.field.value for question in card.questions] == ["preferences.interests"]
    requirement.preferences.interests=None;requirement.preferences.agent_decides_interests=True
    requirement.preferences.pace=None;requirement.preferences.agent_decides_pace=True
    issues=(RequirementIssue(code="requirement_conflict",field_path=RequirementFieldPath.AGENT_DECIDES_INTERESTS,message="兴趣选择问题"),
            RequirementIssue(code="requirement_conflict",field_path=RequirementFieldPath.AGENT_DECIDES_PACE,message="节奏选择问题"))
    card=await clarification_card(requirement,issues=issues)
    assert {question.field.value for question in card.questions} == {"preferences.interests","preferences.pace"}
    assert {fact.value for fact in card.facts} == {"由 Agent 决定"}
    assert len(card.facts) == 2
    requirement.preferences.interests=[]
    empty=await clarification_card(requirement,issues=issues)
    assert empty.questions == card.questions and empty.facts == card.facts


@pytest.mark.asyncio
@pytest.mark.parametrize("count,ages,needs_age",[(0,[],False),(2,[],True),(2,[6],True),(2,[6,8],False)])
async def test_children_questions_use_explicit_count_and_preserve_conflict_fact(count,ages,needs_age):
    from app.requirements import RequirementIssue, RequirementFieldPath
    from tests.unit.result_formatting.fakes import make_confirmed
    requirement=(await make_confirmed(budget_total_cny=None)).outcome.requirement.model_copy(deep=True)
    requirement.party.children_count=count;requirement.party.children_ages=ages
    issues=() if len(ages)==count or not ages else (RequirementIssue(code="children_age_mismatch",
        field_path=RequirementFieldPath.CHILDREN_AGES,message="年龄数量不一致"),)
    card=await clarification_card(requirement,issues=issues)
    questions=[question for question in card.questions if question.field is RequirementFieldPath.CHILDREN_AGES]
    assert bool(questions) is needs_age
    if questions: assert questions[0].item_count == count and questions[0].minimum == 0 and questions[0].maximum == 17
    if ages and needs_age: assert ("目前记录：儿童年龄","6") in [(fact.label,fact.value) for fact in card.facts]


@pytest.mark.asyncio
async def test_current_conflict_is_readonly_and_unknown_issue_still_has_message():
    from app.requirements import RequirementIssue, RequirementFieldPath, FieldMetadataRegistry, FieldMetadata, FieldStatus
    from tests.unit.result_formatting.fakes import make_confirmed
    requirement=(await make_confirmed(budget_total_cny=None)).outcome.requirement
    issue=RequirementIssue(code="requirement_conflict",field_path=RequirementFieldPath.DESTINATION,message="不能覆盖已存城市")
    metadata=FieldMetadataRegistry(metadata={RequirementFieldPath.PACE:FieldMetadata(status=FieldStatus.INVALID)})
    card=await clarification_card(requirement,issues=(issue,),metadata=metadata)
    assert {question.field.value for question in card.questions} == {"destination","preferences.pace"}
    assert ("目前记录：目的城市",requirement.destination) in [(fact.label,fact.value) for fact in card.facts]
    assert not hasattr(card.questions[0],"selected")
    issue=RequirementIssue(code="value_out_of_range",field_path=RequirementFieldPath.MUST_VISIT_PLACES,message="列表问题")
    assert (await clarification_card(requirement,issues=(issue,))).paragraphs == card.paragraphs
