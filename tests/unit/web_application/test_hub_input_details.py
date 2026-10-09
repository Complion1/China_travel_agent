"""Owned terminal diagnosis is finite, optional, and never resumes a run."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.demo_workflow.terminal import terminal_state
from app.durable_execution import DurableRunView
from app.requirements import (ConfirmedTravelRequirement, HubType, RequirementDomainState,
    RequirementFieldPath, NeedClarification, validate_requirement_hubs)
from app.web_application import (WebApplicationDependencies, WebApplicationService,
    WebInputIssue, WebRunView, WebHistoryPage, RunBoundExtractionInvocationSink)
from app.web_application.models import fresh
from app.web_application.presentation import _clarification_details
from app.web_application.store import DecisionError
from app.workflow_contracts import TravelSessionState, WorkflowPhase, DeliveryStatus
from tests.support.demo_workflow_fakes import diagnostics
from tests.unit.requirements.test_requirement_service import complete_requirement
from .fakes import web_settings, FixedClock


def legacy_state(code="workflow_contract_failed", *, valid=False):
    req = complete_requirement("合肥")
    if not valid:
        req.time_window.departure.hub_name = "上海"
        req.time_window.departure.hub_type = None
    domain = RequirementDomainState(requirement=req, confirmation_status="confirmed")
    confirmed = ConfirmedTravelRequirement(schema_version="1.0", requirement=req,
        metadata_without_raw_evidence=domain.metadata, confirmation_summary_digest="a" * 64)
    state = TravelSessionState(schema_version="2.0", run_id=uuid4(), trace_id=uuid4(),
        phase=WorkflowPhase.ITINERARY_PLANNING, delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code=None, terminal_outcome=None, requirement_state=domain, requirement_outcome=confirmed, artifact_refs=())
    state = terminal_state(state, code)
    now = datetime.now(timezone.utc)
    view = DurableRunView(run_id=state.run_id, trace_id=state.trace_id, created_at=now, finalized_at=now,
        lifecycle="closed", activity="terminal", phase=state.phase, delivery_status=state.delivery_status,
        terminal_code=code, pause_generation=None, cancel_requested=False)
    return state, view


def service_for(state, view, *, mode="ok"):
    calls = []
    async def read_state(**kwargs):
        calls.append("state")
        if mode == "error":
            raise RuntimeError("synthetic-private-state-text")
        if mode == "cancel":
            raise asyncio.CancelledError()
        return state
    async def read_run(**kwargs):
        calls.append("view")
        return view
    async def owns(**kwargs):
        calls.append("owns")
        if mode == "foreign":
            raise DecisionError("not_found")
    async def summary(**kwargs): return None
    diagnostic = diagnostics()
    deps = WebApplicationDependencies(artifacts=None, durable=SimpleNamespace(get_state=read_state, get_run=read_run),
        definition=None, diagnostics=diagnostic, extraction_sink=RunBoundExtractionInvocationSink(diagnostics=diagnostic),
        result_service=None, store=SimpleNamespace(owns=owns, get_summary=summary), clock=FixedClock(), disk_probe=lambda: 10**10)
    return WebApplicationService(settings=web_settings(), dependencies=deps), calls


@pytest.mark.parametrize("code", ["requirement_failed", "workflow_contract_failed"])
async def test_owned_failed_detail_has_fixed_field_issues_without_rewriting_terminal(code):
    state, view = legacy_state(code)
    service, calls = service_for(state, view)
    result = await service.get_run(owner=None, run_id=view.run_id)
    assert calls == ["owns", "view", "state"]
    assert result.run == view and result.actions == () and result.card is None
    assert len(result.input_issues) == 2
    assert {issue.field for issue in result.input_issues} == {
        RequirementFieldPath.DEPARTURE_HUB_NAME, RequirementFieldPath.DEPARTURE_HUB_TYPE}
    assert "上海" not in result.model_dump_json() and "合肥" not in result.model_dump_json()
    assert fresh(result, WebRunView) == result
    assert state.terminal_code == code and state.requirement_outcome.requirement.time_window.departure.hub_type is None
    await service.close()


@pytest.mark.parametrize("code,details,valid", [
    ("workflow_contract_failed", False, False), ("requirement_failed", False, False),
    ("workflow_provider_failed", True, False), ("workflow_map_failed", True, False),
    ("workflow_contract_failed", True, True),
])
async def test_list_sse_and_other_failures_do_not_diagnose(code, details, valid):
    state, view = legacy_state(code, valid=valid)
    service, calls = service_for(state, view)
    result = await service._project(view=view, details=details)
    assert result.input_issues == ()
    assert calls == (["state"] if details and code == "workflow_contract_failed" else [])
    await service.close()


@pytest.mark.parametrize("mismatch", ["run_id", "trace_id", "terminal_code", "corrupted"])
async def test_unverified_state_retains_original_failure(mismatch):
    state, view = legacy_state()
    if mismatch in {"run_id", "trace_id"}:
        state = state.model_copy(update={mismatch: uuid4()})
    elif mismatch == "terminal_code":
        state = terminal_state(state, "requirement_failed")
    else:
        state = state.model_copy(update={"requirement_state": "private-corruption"})
    service, _ = service_for(state, view)
    result = await service._project(view=view, details=True)
    assert result.input_issues == () and result.run == view
    await service.close()


async def test_dependency_failure_and_foreign_owner_and_cancellation_are_safe():
    state, view = legacy_state()
    service, _ = service_for(state, view, mode="error")
    result = await service.get_run(owner=None, run_id=view.run_id)
    assert result.run == view and result.input_issues == ()
    assert "synthetic-private" not in result.model_dump_json()
    await service.close()
    foreign, calls = service_for(state, view, mode="foreign")
    with pytest.raises(DecisionError) as rejected:
        await foreign.get_run(owner=None, run_id=view.run_id)
    assert rejected.value.code == "not_found"
    assert calls == ["owns"]
    await foreign.close()
    cancelled, _ = service_for(state, view, mode="cancel")
    with pytest.raises(asyncio.CancelledError):
        await cancelled._project(view=view, details=True)
    await cancelled.close()


def test_input_issue_schema_defaults_finite_messages_and_unique_field_limit():
    state, view = legacy_state()
    old = WebRunView(schema_version="1.0", run=view, card=None, actions=(), result_available=False, summary_status="not_saved")
    assert old.input_issues == ()
    assert WebRunView.model_validate_json(old.model_dump_json(exclude={"input_issues"}), strict=True).input_issues == ()
    domain_issues = validate_requirement_hubs(state.requirement_outcome.requirement)
    issue = WebInputIssue(schema_version="1.0", field=domain_issues[0].field_path,
        code=domain_issues[0].code, message=domain_issues[0].message)
    for changes in ({"field": RequirementFieldPath.DESTINATION}, {"code": "private_code"},
            {"message": "synthetic-private-response"}, {"code": "hub_time_missing"}):
        with pytest.raises(ValidationError): fresh(issue.model_copy(update=changes), WebInputIssue)
    with pytest.raises(ValidationError): fresh(old.model_copy(update={"input_issues": (issue, issue)}), WebRunView)
    with pytest.raises(ValidationError): fresh(old.model_copy(update={"input_issues": (issue,) * 7}), WebRunView)
    with pytest.raises(ValidationError): WebHistoryPage(schema_version="1.0",
        items=(old.model_copy(update={"input_issues": (issue,)}),), next_cursor=None)


def test_ambiguous_existing_hub_is_asked_again_with_destination_name_and_type_options():
    state, _ = legacy_state()
    req = state.requirement_outcome.requirement
    issues = validate_requirement_hubs(req)
    clarification = NeedClarification(requirement=req, metadata=state.requirement_state.metadata,
        issues=issues, message="请补充枢纽")
    questions, _ = _clarification_details(clarification)
    selected = {question.field: question for question in questions}
    assert selected[RequirementFieldPath.DEPARTURE_HUB_NAME].label == "离开合肥的机场/车站"
    question = selected[RequirementFieldPath.DEPARTURE_HUB_TYPE]
    assert question.kind == "single"
    assert [(option.value, option.label) for option in question.options] == [
        ("airport", "机场"), ("railway_station", "火车站或高铁站")]
