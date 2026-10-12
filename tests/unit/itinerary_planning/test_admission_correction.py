"""U09-02～05: real controller admission, obligation progress and explicit patches."""
from datetime import date
import json

import pytest

from app.baidu_map import MatrixTravelMode
from app.itinerary_planning import BusinessHoursState, PlanningStopReason, PurposeVisitIntent, ProposedItineraryPatch, VisitKind
from app.itinerary_planning.models import CalculateRouteMatrixArgs, GetCandidateEvidenceArgs, SubmitItineraryArgs
from app.itinerary_planning.service import _BusinessRejected
from app.llm_runtime import ToolCallsReady, ToolTurnCompleted, ValidatedToolCall
from tests.unit.itinerary_planning.test_phase_budget import invalid_submit, hubs
from tests.unit.itinerary_planning.test_service_initial import _fixture, _metadata
from tests.unit.itinerary_planning.test_purpose_admission import with_unknown_meals


def intent(kind=VisitKind.LUNCH, **values):
    return PurposeVisitIntent(date=date(2026,9,3),candidate_handle="candidate-handle-0000",visit_kind=kind,is_backup=False,**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("case",("missing","meal","combination","backup","must","duplicate","date","hours","hours_evidence","siblings","directions"))
async def test_invalid_query_batch_is_feedback_without_any_matrix_or_direction_execution(case):
    day = date(2026,9,4) if case=="date" else date(2026,9,3)
    intents = () if case=="missing" else (intent(VisitKind.ATTRACTION if case in {"must","hours","hours_evidence","date"} else VisitKind.LUNCH),)
    if case=="combination": intents=(intent(VisitKind.ATTRACTION_LUNCH),)
    if case=="backup": intents=(intent().model_copy(update={"is_backup":True}),)
    if case=="must": intents=(intent(VisitKind.ATTRACTION,must_group_ids=("unknown-must",)),)
    if case=="duplicate": intents=(intent(VisitKind.ATTRACTION),intent(VisitKind.ATTRACTION).model_copy(update={"is_backup":True}))
    if case=="date": intents=(intent(VisitKind.ATTRACTION).model_copy(update={"date":day}),)
    if case=="hours": intents=(intent(VisitKind.ATTRACTION,business_hours_state=BusinessHoursState.CONFLICTS),)
    if case=="hours_evidence": intents=(intent(VisitKind.ATTRACTION,business_hours_state=BusinessHoursState.COMPATIBLE),)
    query=ValidatedToolCall(call_id="bad-matrix",tool_name="calculate_route_matrix",arguments=CalculateRouteMatrixArgs(
        date=day,mode=MatrixTravelMode.WALKING,location_handles=("hotel-handle","candidate-handle-0000"),visit_intents=intents))
    if case == "directions":
        from app.baidu_map import TravelMode
        from app.itinerary_planning.models import PlanDirectionsArgs, DirectionLegIntent, TransportSemantic
        query = ValidatedToolCall(call_id="bad-directions",tool_name="plan_directions",arguments=PlanDirectionsArgs(
            legs=(DirectionLegIntent(leg_id="invalid-purpose",origin_handle="hotel-handle",destination_handle="candidate-handle-0000",
                route_mode=TravelMode.WALKING,transport_semantic=TransportSemantic.WALKING,alternatives_limit=1),),visit_intents=intents))
    calls=(ValidatedToolCall(call_id="read-sibling",tool_name="get_candidate_evidence",arguments=GetCandidateEvidenceArgs(candidate_handles=("candidate-handle-0000",))),query) if case=="siblings" else (query,)
    service,request,_,model,repository=_fixture(ToolCallsReady(calls=calls,metadata=_metadata()),
        ToolTurnCompleted(final_text="结束",metadata=_metadata()),declare_query_intents=False)
    request=with_unknown_meals(repository,request)
    window=request.confirmed.requirement.time_window
    window=window.model_copy(update={"arrival":window.arrival.model_copy(update={"local_datetime":"2026-09-03 08:00"})})
    request=request.model_copy(update={"confirmed":request.confirmed.model_copy(update={
        "requirement":request.confirmed.requirement.model_copy(update={"time_window":window})})})
    outcome=await service.plan_initial(request)
    assert outcome.outcome_type=="incomplete",outcome
    assert outcome.budgets.model_invocations_consumed==model.call_count==3
    assert outcome.budgets.direction_calls_consumed==0
    assert not service._map_adapter.matrix_calls and not service._map_adapter.directions_calls
    results=model.continuations[-1]
    assert len(results)==len(calls)
    for result in results:
        feedback=json.loads(result.content)["tool_result"]
        assert feedback["reason"]=="qualification_rejected" and feedback["issues"]
        assert all(issue["target"] and issue["rule"] and issue["field"] for issue in feedback["issues"])
        assert all(issue["field"].startswith("calls[") for issue in feedback["issues"])


def issue(purpose="lunch",rule="purpose_not_admitted",day="2026-09-03",handle="candidate-handle-0000",**values):
    return dict(target=f"{day}:main:{purpose}:0",rule=rule,field="draft.days.visits",candidate_handle=handle,
        actual=[],expected=purpose,evidence_handles=[],allowed_actions=["replace_candidate"],**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("change",("swap","move","meal","id","delete","new_route"))
async def test_progress_requires_resolving_original_obligations(change):
    service,request,*_=_fixture(hubs(),ToolTurnCompleted(final_text="结束",metadata=_metadata()))
    await service.plan_initial(request)
    run=service._runs[request.operation_plan.run_id]
    first=invalid_submit()
    assert service._rejection_progress(run,first,_BusinessRejected(issues=(issue(),)))
    second=invalid_submit(shift=30)
    problems=(issue(handle="candidate-handle-0001"),) if change=="swap" else (issue(day="2026-09-04"),) if change=="move" else (issue(),)
    if change=="id":
        second=first.model_copy(update={"draft":first.draft.model_copy(update={"arrival_transfer":first.draft.arrival_transfer.model_copy(update={
            "route_leg":first.draft.arrival_transfer.route_leg.model_copy(update={"leg_id":"changed-id"})})})})
    if change=="delete":
        problems=(dict(target="2026-09-03:main:lunch:required",rule="required_visit_missing",field="draft.days.visits",
            candidate_handle=None,actual=0,expected=1,evidence_handles=[],allowed_actions=["add_required_visit"]),)
    if change=="meal": problems=(issue("dinner"),)
    if change=="new_route":
        problems=(issue("route",rule="route_unavailable"),)
    can_continue=service._rejection_progress(run,second,_BusinessRejected(issues=problems))
    if change=="new_route":
        assert can_continue and run.correction_no_progress==0
    elif change=="id":
        assert not can_continue  # exact same-evidence plan cycle
    else:
        assert can_continue and run.correction_no_progress==1
        assert not service._rejection_progress(run,invalid_submit(shift=60),_BusinessRejected(issues=problems))
        assert run.correction_no_progress==2


@pytest.mark.asyncio
async def test_patch_only_replaces_explicit_dates_and_preserves_unlisted_routes():
    service,request,*_=_fixture(hubs(),ToolTurnCompleted(final_text="结束",metadata=_metadata()))
    await service.plan_initial(request)
    run=service._runs[request.operation_plan.run_id]
    full=invalid_submit()
    second_day=full.draft.days[0].model_copy(update={"date":date(2026,9,4)})
    run.last_proposal=full.draft.model_copy(update={"days":(*full.draft.days,second_day)})
    run.last_rejection=object()
    replacement=second_day.model_copy(update={"theme":"修正第二天"})
    patch=SubmitItineraryArgs(effective_policy=full.effective_policy,patch=ProposedItineraryPatch(days=(replacement,)))
    resolved=service._resolve_submission(run,patch)
    assert resolved.draft.days[0]==run.last_proposal.days[0]
    assert resolved.draft.days[1]==replacement
    assert resolved.draft.arrival_transfer==run.last_proposal.arrival_transfer
    assert resolved.draft.departure_transfer==run.last_proposal.departure_transfer
    assert len(patch.model_dump_json())<len(resolved.model_dump_json())
    with pytest.raises(_BusinessRejected):
        service._resolve_submission(run,patch.model_copy(update={"patch":ProposedItineraryPatch(days=(replacement.model_copy(update={"date":date(2026,9,5)}),))}))
    run.last_proposal=None
    with pytest.raises(_BusinessRejected): service._resolve_submission(run,patch)


@pytest.mark.parametrize("values", ({}, {"draft":invalid_submit().draft,"patch":ProposedItineraryPatch(days=invalid_submit().draft.days)}))
def test_submit_draft_patch_union_is_strict(values):
    from pydantic import ValidationError
    with pytest.raises(ValidationError): SubmitItineraryArgs(effective_policy=invalid_submit().effective_policy,**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("relevant", (False, True))
async def test_only_changed_judgment_for_same_hard_target_counts_as_evidence_progress(relevant):
    from app.candidate_research import MatchVerdict, ResearchRole, MealReasonCode, RoleVerdict, RoleReasonCode
    service,request,*_=_fixture(hubs(),ToolTurnCompleted(final_text="结束",metadata=_metadata()))
    await service.plan_initial(request)
    run=service._runs[request.operation_plan.run_id]
    original=run.candidates[0]
    assert service._rejection_progress(run,invalid_submit(),_BusinessRejected(issues=(issue(),)))
    if relevant:
        assessment=original.meal_assessments[0].model_copy(update={"verdict":MatchVerdict.UNRESOLVED,"reason_code":MealReasonCode.EVIDENCE_INSUFFICIENT})
        changed=original.model_copy(update={"meal_intents":tuple(meal for meal in original.meal_intents if meal.value!="lunch"),
            "meal_assessments":(assessment,original.meal_assessments[1])})
    else:
        changed=original.model_copy(update={"roles":tuple(item.model_copy(update={"verdict":RoleVerdict.UNRESOLVED,
            "reason_code":RoleReasonCode.EVIDENCE_INSUFFICIENT}) if item.role is ResearchRole.ATTRACTION else item for item in original.roles)})
    run.candidates=(changed,*run.candidates[1:])
    continuation=service._rejection_progress(run,invalid_submit(shift=30),_BusinessRejected(issues=(issue(),)))
    assert continuation
    assert run.correction_no_progress == (0 if relevant else 1)


@pytest.mark.asyncio
async def test_invalid_patch_does_not_erase_the_original_missing_meal_obligation():
    service,request,*_=_fixture(hubs(),ToolTurnCompleted(final_text="结束",metadata=_metadata()))
    await service.plan_initial(request)
    run=service._runs[request.operation_plan.run_id]
    assert service._rejection_progress(run,invalid_submit(),_BusinessRejected(issues=(issue(),)))
    assert service._rejection_progress(run,invalid_submit(shift=30),_BusinessRejected("patch_date_not_in_base","patch.days"))
    assert run.correction_no_progress == 1
    assert any(problem["rule"]=="required_visit_missing" and ":lunch:" in problem["target"] for problem in run.correction_issues)
    assert not service._rejection_progress(run,invalid_submit(shift=60),_BusinessRejected("patch_date_not_in_base","patch.days"))
    assert run.correction_no_progress == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("rule", ("must_match_unconfirmed", "business_hours_unverified", "business_hours_conflict"))
async def test_same_qualification_obligation_cannot_rotate_between_lunch_and_dinner(rule):
    service, request, *_ = _fixture(hubs(), ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    await service.plan_initial(request)
    run = service._runs[request.operation_plan.run_id]
    assert service._rejection_progress(run, invalid_submit(), _BusinessRejected(issues=(issue(rule=rule),)))
    assert service._rejection_progress(run, invalid_submit(shift=30), _BusinessRejected(issues=(issue("dinner", rule=rule),)))
    assert run.correction_no_progress == 1
    assert not service._rejection_progress(run, invalid_submit(shift=60), _BusinessRejected(issues=(issue(rule=rule),)))
    assert run.correction_no_progress == 2


@pytest.mark.asyncio
async def test_unchanged_hours_cannot_rotate_between_unverified_and_conflict_claims():
    service, request, *_ = _fixture(hubs(), ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    await service.plan_initial(request)
    run = service._runs[request.operation_plan.run_id]
    assert service._rejection_progress(run, invalid_submit(), _BusinessRejected(issues=(issue(rule="business_hours_unverified"),)))
    assert service._rejection_progress(run, invalid_submit(shift=30), _BusinessRejected(issues=(issue(rule="business_hours_conflict"),)))
    assert run.correction_no_progress == 1
    assert not service._rejection_progress(run, invalid_submit(shift=60), _BusinessRejected(issues=(issue(rule="business_hours_unverified"),)))
    assert run.correction_no_progress == 2


@pytest.mark.asyncio
async def test_query_cycle_fingerprint_includes_every_tool_in_the_batch():
    read = ValidatedToolCall(call_id="same-read", tool_name="get_candidate_evidence",
        arguments=GetCandidateEvidenceArgs(candidate_handles=("candidate-handle-0000",)))
    def batch(mode, identity):
        query = ValidatedToolCall(call_id=identity, tool_name="calculate_route_matrix", arguments=CalculateRouteMatrixArgs(
            date=date(2026, 9, 3), mode=mode, location_handles=("hotel-handle", "candidate-handle-0000"), visit_intents=(intent(),)))
        return ToolCallsReady(calls=(read, query), metadata=_metadata())
    service, request, _, model, repository = _fixture(batch(MatrixTravelMode.WALKING, "first"),
        batch(MatrixTravelMode.DRIVING, "different"), batch(MatrixTravelMode.WALKING, "repeat"), declare_query_intents=False)
    request = with_unknown_meals(repository, request)
    window = request.confirmed.requirement.time_window
    request = request.model_copy(update={"confirmed": request.confirmed.model_copy(update={"requirement":
        request.confirmed.requirement.model_copy(update={"time_window": window.model_copy(update={"arrival":
            window.arrival.model_copy(update={"local_datetime": "2026-09-03 08:00"})})})})})
    result = await service.plan_initial(request)
    assert result.stop_reasons == (PlanningStopReason.NO_PROGRESS,)
    assert result.budgets.model_invocations_consumed == model.call_count == 4
    run = service._runs[request.operation_plan.run_id]
    assert len(set(run.correction_plan_fingerprints)) == 2
    assert run.correction_no_progress == 2
    assert not service._map_adapter.matrix_calls and not service._map_adapter.directions_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ("compatible", "interval_conflict", "unverified_hours", "same_tool_duplicate", "different_purpose"))
async def test_cross_tool_visit_declarations_allow_optional_precision_without_laundering_claims(case):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.baidu_map import TravelMode
    from app.itinerary_planning import TimeInterval
    from app.itinerary_planning.models import PlanDirectionsArgs, DirectionLegIntent, TransportSemantic
    loose = intent(VisitKind.ATTRACTION)
    precise = loose.model_copy(update={"interval": TimeInterval(start_at=datetime(2026, 9, 3, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        end_at=datetime(2026, 9, 3, 13, tzinfo=ZoneInfo("Asia/Shanghai")))})
    if case == "interval_conflict":
        loose = precise.model_copy(update={"interval": precise.interval.model_copy(update={
            "start_at": datetime(2026, 9, 3, 11, tzinfo=ZoneInfo("Asia/Shanghai"))})})
    if case == "unverified_hours": loose = loose.model_copy(update={"business_hours_state": BusinessHoursState.COMPATIBLE})
    if case == "different_purpose": precise = precise.model_copy(update={"visit_kind": VisitKind.LUNCH})
    matrix = ValidatedToolCall(call_id="matrix", tool_name="calculate_route_matrix", arguments=CalculateRouteMatrixArgs(
        date=date(2026, 9, 3), mode=MatrixTravelMode.WALKING, location_handles=("hotel-handle", "candidate-handle-0000"),
        visit_intents=(loose, loose) if case == "same_tool_duplicate" else (loose,)))
    directions = ValidatedToolCall(call_id="directions", tool_name="plan_directions", arguments=PlanDirectionsArgs(
        legs=(DirectionLegIntent(leg_id="one-visit", origin_handle="hotel-handle", destination_handle="candidate-handle-0000",
            route_mode=TravelMode.WALKING, transport_semantic=TransportSemantic.WALKING, alternatives_limit=1),), visit_intents=(precise,)))
    service, request, _, model, _ = _fixture(ToolCallsReady(calls=(matrix, directions), metadata=_metadata()),
        ToolTurnCompleted(final_text="结束", metadata=_metadata()), declare_query_intents=False)
    from app.workflow_contracts import OperationId, WorkflowPhase
    plan = request.operation_plan
    request = request.model_copy(update={"operation_plan": plan.model_copy(update={
        "matrix_operation_ids": (OperationId(run_id=plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="compatible_matrix"),),
        "direction_operation_ids": (OperationId(run_id=plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="compatible_direction"),)})})
    result = await service.plan_initial(request)
    assert result.budgets.model_invocations_consumed == model.call_count == 3
    if case == "compatible":
        assert len(service._map_adapter.matrix_calls) == len(service._map_adapter.directions_calls) == 1
        assert result.budgets.direction_calls_consumed == 1
    else:
        assert not service._map_adapter.matrix_calls and not service._map_adapter.directions_calls
        rules = {item["rule"] for item in json.loads(model.continuations[-1][0].content)["tool_result"]["issues"]}
        assert {"interval_conflict": "intent_declaration_conflict", "unverified_hours": "business_hours_unverified",
            "same_tool_duplicate": "candidate_duplicate", "different_purpose": "candidate_duplicate"}[case] in rules


@pytest.mark.asyncio
async def test_changing_only_an_unresolved_meal_reason_does_not_reset_correction_streak():
    from app.candidate_research import MatchVerdict, MealReasonCode
    service, request, *_ = _fixture(hubs(), ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    await service.plan_initial(request)
    run = service._runs[request.operation_plan.run_id]
    original = run.candidates[0]
    unresolved = original.meal_assessments[0].model_copy(update={"verdict": MatchVerdict.UNRESOLVED,
        "reason_code": MealReasonCode.EVIDENCE_INSUFFICIENT})
    changed = original.model_copy(update={"meal_intents": tuple(meal for meal in original.meal_intents if meal.value != "lunch"),
        "meal_assessments": (unresolved, original.meal_assessments[1])})
    run.candidates = (changed, *run.candidates[1:])
    assert service._rejection_progress(run, invalid_submit(), _BusinessRejected(issues=(issue(),)))
    reason_only = changed.model_copy(update={"meal_assessments": (unresolved.model_copy(update={
        "reason_code": MealReasonCode.BUSINESS_HOURS_UNKNOWN}), changed.meal_assessments[1])})
    run.candidates = (reason_only, *run.candidates[1:])
    assert service._rejection_progress(run, invalid_submit(shift=30), _BusinessRejected(issues=(issue(),)))
    assert run.correction_no_progress == 1
    assert not service._rejection_progress(run, invalid_submit(shift=60), _BusinessRejected(issues=(issue(),)))
    assert run.correction_no_progress == 2


@pytest.mark.asyncio
async def test_full_submit_controller_stops_instead_of_rotating_an_unknown_meal_candidate():
    from tests.unit.itinerary_planning.test_service_initial import test_service_completed_requires_main_plan_and_valid_optional_backups
    await test_service_completed_requires_main_plan_and_valid_optional_backups("qualification_meal_rotation")
