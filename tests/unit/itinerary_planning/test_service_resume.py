"""09-6 候选补充恢复的最小端到端回归。"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from uuid import UUID, uuid4

import pytest

from app.baidu_map import MatrixTravelMode
from app.candidate_research import CandidateResearchArtifact, CandidateResearchOperationPlan, CandidateResearchSupplementalRequest, CandidateCountShortfallGap, MealIntent, ResearchMode, ResearchRole
from app.itinerary_planning import (
    ItineraryPlanningCandidateSupplementRequested,
    ItineraryPlanningFailed,
    ItineraryPlanningIncomplete,
    ItineraryPlanningResumeRequest,
    PlanningTool,
    PlanningFailureCode,
    PlanningStopReason,
)
from app.itinerary_planning.models import (
    CandidateDetailsTarget,
    ProposedAreaCoverageTarget,
    ProposedAvoidResolutionTarget,
    ProposedCandidateCountTarget,
    ProposedCandidateDetailsTarget,
    ProposedEffectivePlanningPolicy,
    ProposedMealCoverageTarget,
    ProposedMustResolutionTarget,
    ProposedReplacementTarget,
    ProposedSupplementGap,
)
from app.llm_runtime import ToolCallsReady, ToolTurnCompleted, ValidatedToolCall
from app.requirements import TravelPace
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase

from tests.unit.itinerary_planning.test_service_initial import (
    _expected_must_groups,
    _fixture,
    _metadata,
    _plan,
    _seed_requirement_groups,
)
from tests.support.itinerary_planning_fakes import FakeRepository
from tests.unit.itinerary_planning.test_phase_budget import hubs
from app.itinerary_planning.service import _BusinessRejected, _confirmed_digest, _fresh, _handles


def _supplement_call() -> ValidatedToolCall:
    args = __import__(
        "app.itinerary_planning.models",
        fromlist=["RequestCandidateSupplementArgs"],
    ).RequestCandidateSupplementArgs(
        effective_policy=ProposedEffectivePlanningPolicy(
            effective_pace=TravelPace.NORMAL,
            daily_time_rules=(),
            time_sensitive_must=(),
            transport_rules=(),
        ),
        draft=None,
        proposed_gaps=(ProposedSupplementGap(
            source_gap_type="candidate_supply",
            date=None,
            target=ProposedCandidateCountTarget(
                role=ResearchRole.ATTRACTION,
                current_count=1,
                target_count=2,
            ),
            evidence_handles=("evidence-handle-0000",),
        ),),
    )
    return ValidatedToolCall(
        call_id="supplement_0",
        tool_name="request_candidate_supplement",
        arguments=args,
    )


def _seed_supplemented_candidate(
    repository: FakeRepository,
    candidate: CandidateResearchArtifact,
    run_id: UUID,
    predecessor_ref: ArtifactRef,
    awaiting: ItineraryPlanningCandidateSupplementRequested,
) -> ArtifactRef:
    state = candidate.record.state
    first = state.candidates[0]
    second = first.model_copy(update={
        "admission_ordinal": 1,
        "core": first.core.model_copy(update={
            "uid": "candidate-2",
            "name": "补充景点",
            "address": "杭州补充路",
            "coordinate": first.core.coordinate.model_copy(update={"latitude": 30.26, "longitude": 120.17}),
        }),
    })
    counts = state.counts.model_copy(update={
        "attraction_count": 2,
        "restaurant_count": 2,
        "unique_eligible_count": 2,
        "admitted_slots": 2,
    })
    supplemented_state = state.model_copy(update={"candidates": (first, second), "counts": counts})
    supplemented_record = candidate.record.model_copy(update={"state": supplemented_state, "counts": counts})
    gap = awaiting.gaps[0]
    original = CandidateResearchSupplementalRequest(
        predecessor_candidate_set_ref=predecessor_ref,
        gaps=tuple(CandidateCountShortfallGap(gap_id=item.gap_id, evidence_refs=item.evidence_refs,
            role=item.target.role, current_count=item.target.current_count, target_count=item.target.target_count) for item in awaiting.gaps),
        operation_plan=_supplemental_plan(run_id),
    )
    original_bytes = json.dumps(original.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    candidate = candidate.model_copy(update={
        "mode": ResearchMode.SUPPLEMENTAL,
        "predecessor_ref": predecessor_ref,
        "invocation_request_digest": "sha256:" + hashlib.sha256(original_bytes).hexdigest(),
        "record": supplemented_record,
    })
    base = ArtifactRef(
        artifact_id=uuid4(),
        artifact_type="candidate_research.candidate_set",
        schema_version="1.0",
        content_hash="sha256:" + "0" * 64,
        operation_id=OperationId(
            run_id=run_id,
            phase=WorkflowPhase.CANDIDATE_RESEARCH,
            logical_step="candidate_set_supplemented",
        ),
        created_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        size_bytes=1,
    )
    _, encoded = FakeRepository._encoded(candidate)
    ref = base.model_copy(update={
        "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    })
    repository.seed(ref, candidate)
    return ref


def _supplemental_plan(run_id: UUID) -> CandidateResearchOperationPlan:
    return CandidateResearchOperationPlan(run_id=run_id, iteration_slots=(),
        terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="candidate_set_supplemented"))


def _reseed_successor_groups(
    repository: FakeRepository,
    ref: ArtifactRef,
    candidate: CandidateResearchArtifact,
    groups: tuple[object, ...],
) -> ArtifactRef:
    state = candidate.record.state
    coverage = state.coverage.model_copy(update={
        "must_item_count": sum(len(group.requirement_item_ids) for group in groups),
        "resolved_must_item_count": 0,
    })
    updated_state = state.model_copy(update={"requirement_group_states": groups, "coverage": coverage})
    updated_record = candidate.record.model_copy(update={"state": updated_state, "coverage": coverage})
    updated = candidate.model_copy(update={"record": updated_record})
    updated = CandidateResearchArtifact.model_validate_json(updated.model_dump_json(exclude_none=False), strict=True)
    _, encoded = FakeRepository._encoded(updated)
    ref = ref.model_copy(update={
        "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    })
    repository.seed(ref, updated)
    return ref


@pytest.mark.asyncio
async def test_reconstructed_supplement_preserves_every_supply_target_and_tuple_order() -> None:
    import app.candidate_research as research
    import app.itinerary_planning.models as planning
    from app.itinerary_planning import ItineraryPlanningArtifact
    from app.itinerary_planning.artifacts import _to_gap_summary_snapshot
    from app.itinerary_planning.service import _supplement_request
    from tests.contract.itinerary_planning.test_upstream_integration import _supplement_targets
    service, request, _, _, repository = _fixture(ToolCallsReady(calls=(_supplement_call(),), metadata=_metadata()))
    awaiting = await service.plan_initial(request)
    source = (await repository.get(awaiting.planning_ref, expected_model=ItineraryPlanningArtifact)).content
    run = service._runs[request.operation_plan.run_id]
    expected_types = {
        "candidate_count": research.CandidateCountShortfallGap, "area_coverage": research.AreaCoverageMissingGap,
        "meal_coverage": research.MealCoverageMissingGap, "must_resolution": research.MustVisitResolutionMissingGap,
        "avoid_resolution": research.AvoidResolutionMissingGap, "candidate_details": research.CandidateDetailsMissingGap,
        "replacement": research.ReplacementCandidateNeededGap,
    }
    targets = _supplement_targets()
    gaps = tuple(planning.CandidateSupplyGap(gap_id=f"mapped-{index}", date=None, target=target, evidence_refs=awaiting.gaps[0].evidence_refs) for index, target in enumerate(targets))
    snapshots = tuple(_to_gap_summary_snapshot(service._gap_summary_from_gap(run, gap)) for gap in gaps)
    source = source.model_copy(update={"record": source.record.model_copy(update={"gaps": snapshots})})
    restored = _supplement_request(source, _supplemental_plan(run.run_id))
    assert restored.predecessor_candidate_set_ref == request.candidate_set_ref
    assert tuple(item.gap_id for item in restored.gaps) == tuple(item.gap_id for item in gaps)
    for original, actual in zip(gaps, restored.gaps, strict=True):
        assert type(actual) is expected_types[original.target.target_type]
        assert actual.evidence_refs == original.evidence_refs
        for name in type(original.target).model_fields:
            if name in {"schema_version", "target_type"}:
                continue
            value = getattr(original.target, name)
            if name == "anchor":
                expected = None if value is None else research.HotelAnchorRef() if value.ref_type == "hotel" else research.CandidateAnchorRef(uid=value.uid)
                assert actual.anchor == expected
            else:
                assert getattr(actual, name) == value


@pytest.mark.asyncio
async def test_real_runtime_resume_selects_new_candidate_from_visible_context() -> None:
    from app.llm_runtime import DeepSeekRuntime
    from app.llm_runtime.protocols import ProviderResponse
    from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper

    class Provider(ScriptedProviderAdapter):
        def __init__(self):
            super().__init__([])
            self.initial_handles = set()
            self.new_handle = None
            self.hotel = None
            self.sightseeing_start_date = None

        def response(self, calls):
            self.call_count += 1
            return ProviderResponse(content=None if calls else "结束", tool_calls=calls,
                finish_reason="tool_calls" if calls else "stop", model_name="deepseek-v4-flash",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                    "reasoning_tokens": 0, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1}, reasoning_content=None,
                raw_tool_calls=tuple((call_id, name, json.dumps(arguments, ensure_ascii=False)) for call_id, name, arguments in calls))

        async def ainvoke_tools(self, value):
            context = json.loads(value.user.split("BEGIN_TYPED_PLANNING_DATA\n")[1].split("\nEND_TYPED_PLANNING_DATA")[0])
            self.initial_handles = {item["handle"] for item in context["handles"] if item["kind"] == "candidate"}
            self.hotel = next(item["handle"] for item in context["handles"] if item["kind"] == "hotel")
            self.sightseeing_start_date = context["sightseeing_start_date"]
            evidence = next(item["handle"] for item in context["handles"] if item["kind"] == "candidate_evidence")
            args = _supplement_call().arguments.model_dump(mode="json")
            args["proposed_gaps"][0]["evidence_handles"] = [evidence]
            return self.response((("await_supply", "request_candidate_supplement", args),))

        async def ainvoke_tool_continuation(self, value):
            latest = [item for item in value.messages if item["role"] == "tool"][-1]
            data = json.loads(latest["content"])
            if self.call_count == 1:
                assert latest["tool_call_id"] == "await_supply"
                assert data["tool_result"]["status"] == "candidate_supplemented"
                assert "planning_context" not in data
                state = data["planning_state"]
                visible_handles = {
                    candidate["candidate_handle"]
                    for observation in state["candidates"]
                    for candidate in observation["candidates"]
                }
                self.new_handle = next(
                    handle for handle in visible_handles
                    if handle not in self.initial_handles
                )
                return self.response(tuple((call.call_id, call.tool_name, call.arguments.model_dump(mode="json")) for call in hubs().calls))
            if self.call_count == 2:
                return self.response((("new_matrix", "calculate_route_matrix", {"date": self.sightseeing_start_date, "mode": "walking", "location_handles": [self.hotel, self.new_handle]}),))
            assert latest["tool_call_id"] == "new_matrix"
            return self.response(())

    service, request, _, _, repository = _fixture()
    provider = Provider()
    service._runtime = DeepSeekRuntime(provider=provider, sleeper=RecordingSleeper())
    awaiting = await service.plan_initial(request)
    assert awaiting.outcome_type == "candidate_supplement_requested"
    original = (await repository.get(request.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    supplied = _seed_supplemented_candidate(repository, original, request.operation_plan.run_id, request.candidate_set_ref, awaiting)
    plan = _plan(request.operation_plan.run_id, suffix="visible_resume", slots=tuple(range(awaiting.budgets.model_invocations_consumed,19)))
    plan = plan.model_copy(update={"matrix_operation_ids": (OperationId(run_id=plan.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="visible_resume_matrix"),)})
    result = await service.plan_resume(ItineraryPlanningResumeRequest(predecessor_planning_ref=awaiting.planning_ref,
        supplemented_candidate_set_ref=supplied, operation_plan=plan, supplemental_operation_plan=_supplemental_plan(plan.run_id)))
    assert result.outcome_type == "incomplete", result
    assert provider.call_count == 4 and provider.new_handle is not None
    assert len(service._map_adapter.matrix_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ("missing", "forged", "duplicate"))
async def test_resume_rejects_successor_with_invalid_must_item_coverage_before_mutation(case: str) -> None:
    service, request, runtime, operation, repository = _fixture(
        ToolCallsReady(calls=(_supplement_call(),), metadata=_metadata()),
    )
    preferences = request.confirmed.requirement.preferences.model_copy(
        update={"must_visit_places": ["西湖", "灵隐寺"]},
    )
    confirmed = request.confirmed.model_copy(update={
        "requirement": request.confirmed.requirement.model_copy(update={"preferences": preferences}),
    })
    expected = _expected_must_groups(confirmed)
    request = _seed_requirement_groups(repository, request, expected).model_copy(update={"confirmed": confirmed})
    awaiting = await service.plan_initial(request)
    assert isinstance(awaiting, ItineraryPlanningCandidateSupplementRequested), awaiting
    source = (await repository.get(request.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    supplied_ref = _seed_supplemented_candidate(
        repository, source, request.operation_plan.run_id, request.candidate_set_ref, awaiting,
    )
    supplied = (await repository.get(supplied_ref, expected_model=CandidateResearchArtifact)).content
    if case == "missing":
        actual = ()
    elif case == "forged":
        actual = (
            expected[0].model_copy(update={"requirement_item_ids": ("reqitem:must_visit:0:forged",)}),
            expected[1],
        )
    else:
        actual = (expected[0], expected[1].model_copy(update={
            "requirement_item_ids": expected[0].requirement_item_ids,
        }))
    supplied_ref = _reseed_successor_groups(repository, supplied_ref, supplied, actual)
    run = service._runs[request.operation_plan.run_id]
    before = (
        run.candidate_ref,
        run.candidates,
        run.requirement_group_states,
        run.pending_call_id,
        run.plan,
        run.invocation_count,
    )
    writes = len(repository.put_requests)

    class NoBudgetCalls:
        async def get_snapshot(self, **kwargs):
            raise AssertionError("invalid must coverage must be rejected before budget access")

        async def consume(self, value):
            raise AssertionError("invalid must coverage must not consume budget")

    service._budget = NoBudgetCalls()
    resume_plan = _plan(run.run_id, suffix="invalid_must_resume")
    result = await service.plan_resume(ItineraryPlanningResumeRequest(
        predecessor_planning_ref=awaiting.planning_ref,
        supplemented_candidate_set_ref=supplied_ref,
        operation_plan=resume_plan,
        supplemental_operation_plan=_supplemental_plan(run.run_id),
    ))

    assert isinstance(result, ItineraryPlanningFailed), result.model_dump(mode="json")
    assert result.failure.code is PlanningFailureCode.PREDECESSOR_INVALID
    assert (
        run.candidate_ref,
        run.candidates,
        run.requirement_group_states,
        run.pending_call_id,
        run.plan,
        run.invocation_count,
    ) == before
    assert len(repository.put_requests) == writes
    assert runtime.start_count == operation.call_count == 1
    assert not service._map_adapter.matrix_calls and not service._map_adapter.directions_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ("original_predecessor", "target", "gap_order", "evidence_order", "digest", "pending_call", "corrupt_content"))
async def test_resume_rejects_foreign_supplement_without_mutating_pending_run(mismatch: str) -> None:
    call = _supplement_call()
    if mismatch == "gap_order":
        extra = call.arguments.proposed_gaps[0].model_copy(update={"target": ProposedCandidateCountTarget(role=ResearchRole.RESTAURANT, current_count=1, target_count=2)})
        call = call.model_copy(update={"arguments": call.arguments.model_copy(update={"proposed_gaps": (*call.arguments.proposed_gaps, extra)})})
    if mismatch == "evidence_order":
        gap = call.arguments.proposed_gaps[0].model_copy(update={"evidence_handles": ("evidence-handle-0000", "evidence-handle-0001")})
        call = call.model_copy(update={"arguments": call.arguments.model_copy(update={"proposed_gaps": (gap,)})})
    service, request, runtime, operation, repository = _fixture(ToolCallsReady(calls=(call,), metadata=_metadata()))
    if mismatch == "evidence_order":
        candidate = (await repository.get(request.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
        state = candidate.record.state
        extra_ref = state.observation_refs[0].model_copy(update={"artifact_id": uuid4(), "operation_id": OperationId(run_id=request.operation_plan.run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="second_evidence"), "content_hash": "sha256:" + "8" * 64})
        state = state.model_copy(update={"observation_refs": (*state.observation_refs, extra_ref),
            "candidates": tuple(item.model_copy(update={"observation_indexes": (0, 1)}) for item in state.candidates)})
        candidate = candidate.model_copy(update={"record": candidate.record.model_copy(update={"state": state})})
        _, encoded = FakeRepository._encoded(candidate)
        candidate_ref = request.candidate_set_ref.model_copy(update={"size_bytes": len(encoded), "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest()})
        repository.seed(candidate_ref, candidate)
        request = request.model_copy(update={"candidate_set_ref": candidate_ref})
    awaiting = await service.plan_initial(request)
    candidate = (await repository.get(request.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    ref = _seed_supplemented_candidate(repository, candidate, request.operation_plan.run_id, request.candidate_set_ref, awaiting)
    supplied = (await repository.get(ref, expected_model=CandidateResearchArtifact)).content
    expected = "request_mismatch"
    if mismatch == "original_predecessor":
        supplied = supplied.model_copy(update={"predecessor_ref": request.candidate_set_ref.model_copy(update={"artifact_id": uuid4()})})
    elif mismatch in {"target", "gap_order", "evidence_order"}:
        gap = awaiting.gaps[0]
        other_gaps = tuple(CandidateCountShortfallGap(gap_id=item.gap_id, evidence_refs=item.evidence_refs[::-1] if mismatch == "evidence_order" else item.evidence_refs, role=item.target.role,
            current_count=item.target.current_count, target_count=item.target.target_count + (1 if mismatch == "target" else 0)) for item in awaiting.gaps)
        if mismatch == "gap_order":
            other_gaps = other_gaps[::-1]
        other = CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=request.candidate_set_ref,
            gaps=other_gaps, operation_plan=_supplemental_plan(request.operation_plan.run_id))
        encoded = json.dumps(other.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        supplied = supplied.model_copy(update={"invocation_request_digest": "sha256:" + hashlib.sha256(encoded).hexdigest()})
    elif mismatch == "digest":
        supplied = supplied.model_copy(update={"invocation_request_digest": "sha256:" + "1" * 64})
    elif mismatch == "pending_call":
        expected = "request_mismatch"
        from app.itinerary_planning import ItineraryPlanningArtifact
        source = (await repository.get(awaiting.planning_ref, expected_model=ItineraryPlanningArtifact)).content
        changed = source.model_copy(update={"record": source.record.model_copy(update={"pending_call_id": "other_pending", "state": source.record.state.model_copy(update={"pending_call_id": "other_pending"})})})
        _, encoded = FakeRepository._encoded(changed)
        changed_ref = awaiting.planning_ref.model_copy(update={"content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)})
        repository.seed(changed_ref, changed)
        awaiting = awaiting.model_copy(update={"planning_ref": changed_ref})
    else:
        expected = "operation_result_indeterminate"
    if mismatch != "corrupt_content":
        _, encoded = FakeRepository._encoded(supplied)
        ref = ref.model_copy(update={"content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)})
        repository.seed(ref, supplied)
    else:
        repository.contents[ref.artifact_id]["invocation_request_digest"] = "sha256:" + "2" * 64
    class NoBudgetCalls:
        def __init__(self):
            self.calls = 0
        async def get_snapshot(self, **kwargs):
            self.calls += 1
            raise AssertionError("rejected resume must not query budgets")
        async def consume(self, value):
            self.calls += 1
            raise AssertionError("rejected resume must not consume budgets")
    budget = NoBudgetCalls()
    service._budget = budget
    writes = len(repository.put_requests)
    run = service._runs[request.operation_plan.run_id]
    original_state = (run.candidate_ref, run.candidates, run.pending_call_id, run.plan, run.invocation_count)
    result = await service.plan_resume(ItineraryPlanningResumeRequest(predecessor_planning_ref=awaiting.planning_ref,
        supplemented_candidate_set_ref=ref, operation_plan=_plan(run.run_id, suffix="reject_resume"), supplemental_operation_plan=_supplemental_plan(run.run_id)))
    assert result.failure.code.value == expected
    assert budget.calls == 0
    assert (run.candidate_ref, run.candidates, run.pending_call_id, run.plan, run.invocation_count) == original_state
    assert len(repository.put_requests) == writes
    assert runtime.start_count == operation.call_count == 1
    assert not service._map_adapter.matrix_calls and not service._map_adapter.directions_calls


@pytest.mark.asyncio
async def test_supplement_outcome_converts_artifact_evidence_and_resume_uses_remaining_slots() -> None:
    evidence = ToolCallsReady(
        calls=(ValidatedToolCall(
            call_id="evidence_before_supplement",
            tool_name="get_candidate_evidence",
            arguments=__import__("app.itinerary_planning.models", fromlist=["GetCandidateEvidenceArgs"]).GetCandidateEvidenceArgs(candidate_handles=("candidate-handle-0000",)),
        ),),
        metadata=_metadata(),
    )
    supplement = ToolCallsReady(
        calls=(_supplement_call(),),
        metadata=_metadata(),
    )
    service, request, runtime, operation, repository = _fixture(
        evidence,
        supplement,
        ToolTurnCompleted(final_text="继续", metadata=_metadata()),
    )

    awaiting = await service.plan_initial(request)

    assert isinstance(awaiting, ItineraryPlanningCandidateSupplementRequested)
    assert awaiting.gaps[0].gap_type == "candidate_supply"
    candidate_artifact = repository.contents[request.candidate_set_ref.artifact_id]
    supplemented_content = CandidateResearchArtifact.model_validate_json(
        json.dumps(candidate_artifact, ensure_ascii=False),
        strict=True,
    )
    assert awaiting.gaps[0].evidence_refs == (supplemented_content.record.state.observation_refs[0],)
    supplemented_ref = _seed_supplemented_candidate(
        repository,
        supplemented_content,
        request.operation_plan.run_id,
        request.candidate_set_ref,
        awaiting,
    )
    same_content_ref = request.candidate_set_ref.model_copy(update={
        "artifact_id": uuid4(),
        "operation_id": OperationId(
            run_id=request.operation_plan.run_id,
            phase=WorkflowPhase.CANDIDATE_RESEARCH,
            logical_step="candidate_set_same_content",
        ),
    })
    repository.seed(same_content_ref, supplemented_content)
    same_content = await service.plan_resume(ItineraryPlanningResumeRequest(
        supplemental_operation_plan=_supplemental_plan(request.operation_plan.run_id),
        predecessor_planning_ref=awaiting.planning_ref,
        supplemented_candidate_set_ref=same_content_ref,
        operation_plan=_plan(request.operation_plan.run_id, suffix="resume_same_content", slots=tuple(range(awaiting.budgets.model_invocations_consumed,19))),
    ))
    assert same_content.failure.code.value == "predecessor_invalid"
    resume = ItineraryPlanningResumeRequest(
        supplemental_operation_plan=_supplemental_plan(request.operation_plan.run_id),
        predecessor_planning_ref=awaiting.planning_ref,
        supplemented_candidate_set_ref=supplemented_ref,
        operation_plan=_plan(request.operation_plan.run_id, suffix="resume", slots=tuple(range(awaiting.budgets.model_invocations_consumed,19))),
    )

    result = await service.plan_resume(resume)
    replay = await service.plan_resume(resume)

    assert isinstance(result, ItineraryPlanningIncomplete), result.failure.code
    assert result.stop_reasons == (PlanningStopReason.NO_TERMINAL_SUBMISSION,)
    assert runtime.start_count == 1
    assert len(operation.continuations) == 2
    assert operation.continuations[1][0].call_id == "supplement_0"
    assert replay == result
    assert any(
        item.artifact_type == "itinerary_planning.model_action"
        and item.operation_id.logical_step == "resume_action_2"
        for item in repository.put_requests
    )


@pytest.mark.asyncio
async def test_public_loop_accepts_supplement_with_one_transport_call_remaining() -> None:
    matrix_calls = tuple(
        ValidatedToolCall(
            call_id=f"matrix_{index}",
            tool_name="calculate_route_matrix",
            arguments=__import__(
                "app.itinerary_planning.models",
                fromlist=["CalculateRouteMatrixArgs"],
            ).CalculateRouteMatrixArgs(
                date=date(2026, 9, 3),
                mode=tuple(MatrixTravelMode)[index // 2],
                location_handles=("candidate-handle-0000", "hotel-handle") if index % 2 else ("hotel-handle", "candidate-handle-0000"),
            ),
        )
        for index in range(4)
    )
    responses = tuple(
        ToolCallsReady(calls=(call,), metadata=_metadata())
        for call in matrix_calls
    ) + (ToolCallsReady(calls=(_supplement_call(),), metadata=_metadata()),)
    service, request, _, operation, _ = _fixture(*responses)
    base = request.operation_plan
    matrix_ids = tuple(
        OperationId(
            run_id=base.run_id,
            phase=WorkflowPhase.ITINERARY_PLANNING,
            logical_step=f"no_legal_matrix_{index}",
        )
        for index in range(4)
    )
    request = request.model_copy(update={
        "operation_plan": base.model_copy(update={"matrix_operation_ids": matrix_ids}),
    })

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningCandidateSupplementRequested)
    assert result.budgets.model_invocations_consumed == 6
    assert operation.call_count == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_with_sixth_supplement", (False, True))
async def test_five_supplements_have_independent_allowance_and_remaining_phase_calls(finish_with_sixth_supplement: bool) -> None:
    from app.candidate_research import AreaCoverageMissingGap, CandidateAnchorRef
    from app.itinerary_planning.models import CalculateRouteMatrixArgs
    responses = []
    for index, radius in enumerate((1, 3, 5, 10, 1, 3)):
        original_call = _supplement_call()
        target = ProposedAreaCoverageTarget(role=ResearchRole.ATTRACTION if index < 4 else ResearchRole.RESTAURANT,
            anchor_handle="candidate-handle-0000", radius_km=radius)
        gap = original_call.arguments.proposed_gaps[0].model_copy(update={"target": target})
        responses.append(ToolCallsReady(calls=(original_call.model_copy(update={"call_id": f"supply_{index}",
            "arguments": original_call.arguments.model_copy(update={"proposed_gaps": (gap,)})}),), metadata=_metadata()))
    if not finish_with_sixth_supplement:
        responses[5:] = [hubs(), *(ToolCallsReady(calls=(ValidatedToolCall(call_id=f"matrix_{index}", tool_name="calculate_route_matrix",
            arguments=CalculateRouteMatrixArgs(date=date(2026, 9, 3), mode=tuple(MatrixTravelMode)[index // 2],
                location_handles=("hotel-handle", "candidate-handle-0000") if index % 2 else ("candidate-handle-0000", "hotel-handle"))),), metadata=_metadata()) for index in range(5)),
            ToolTurnCompleted(final_text="结束", metadata=_metadata())]
    service, request, runtime, operation, repository = _fixture(*responses)
    run_id = request.operation_plan.run_id
    awaiting = await service.plan_initial(request)
    previous_ref = request.candidate_set_ref
    original_candidate = (await repository.get(previous_ref, expected_model=CandidateResearchArtifact)).content
    for index in range(5):
        assert awaiting.outcome_type == "candidate_supplement_requested", awaiting
        assert awaiting.budgets.model_invocations_consumed == index + 1
        assert awaiting.budgets.supplement_request_used == index + 1
        upstream_plan = _supplemental_plan(run_id).model_copy(update={"terminal_successor_operation_id": OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=f"five_research_{index}")})
        gap = awaiting.gaps[0]
        upstream_request = CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=previous_ref,
            gaps=(AreaCoverageMissingGap(gap_id=gap.gap_id, evidence_refs=gap.evidence_refs, role=gap.target.role,
                anchor=CandidateAnchorRef(uid=gap.target.anchor.uid), radius_km=gap.target.radius_km),), operation_plan=upstream_plan)
        encoded_request = json.dumps(upstream_request.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        supplied = original_candidate.model_copy(update={"mode": ResearchMode.SUPPLEMENTAL, "predecessor_ref": previous_ref,
            "invocation_request_digest": "sha256:" + hashlib.sha256(encoded_request).hexdigest()})
        _, encoded = FakeRepository._encoded(supplied)
        successor_ref = previous_ref.model_copy(update={"artifact_id": uuid4(), "operation_id": upstream_plan.terminal_successor_operation_id,
            "size_bytes": len(encoded), "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest()})
        repository.seed(successor_ref, supplied)
        plan = _plan(run_id, suffix=f"five_resume_{index}", slots=tuple(range(awaiting.budgets.model_invocations_consumed,19)))
        if index == 4 and not finish_with_sixth_supplement:
            plan = plan.model_copy(update={"matrix_operation_ids": tuple(OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"five_matrix_{i}") for i in range(5))})
        awaiting = await service.plan_resume(ItineraryPlanningResumeRequest(predecessor_planning_ref=awaiting.planning_ref,
            supplemented_candidate_set_ref=successor_ref, operation_plan=plan, supplemental_operation_plan=upstream_plan))
        previous_ref = successor_ref
    assert awaiting.outcome_type == ("failed" if finish_with_sixth_supplement else "incomplete"), (getattr(getattr(awaiting, "failure", None), "code", None), operation.call_count)
    assert runtime.start_count == 1
    assert operation.call_count == (6 if finish_with_sixth_supplement else 12)
    assert awaiting.budgets.model_invocations_consumed == (6 if finish_with_sixth_supplement else 12)
    assert awaiting.budgets.supplement_request_used == 5
    if finish_with_sixth_supplement:
        assert awaiting.failure.code is PlanningFailureCode.MODEL_CONTRACT_INVALID
        assert "request_candidate_supplement" not in {tool.name for tool in operation.tool_contracts[-1]}
    assert len([item for item in repository.put_requests if item.artifact_type == "itinerary_planning.itinerary" and item.content["record"]["record_type"] == "awaiting_candidate_supplement"]) == 5


@pytest.mark.asyncio
async def test_terminal_only_non_submit_is_recorded_as_rejected_and_does_not_dispatch() -> None:
    matrix_calls = tuple(
        ValidatedToolCall(
            call_id=f"terminal_matrix_{index}",
            tool_name=PlanningTool.CALCULATE_ROUTE_MATRIX.value,
            arguments=__import__(
                "app.itinerary_planning.models",
                fromlist=["CalculateRouteMatrixArgs"],
            ).CalculateRouteMatrixArgs(
                date=date(2026, 9, 3),
                mode=tuple(MatrixTravelMode)[index // 2],
                location_handles=("candidate-handle-0000", "hotel-handle") if index % 2 else ("hotel-handle", "candidate-handle-0000"),
            ),
        )
        for index in range(5)
    )
    terminal_evidence = ValidatedToolCall(
        call_id="terminal_evidence",
        tool_name=PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        arguments=__import__(
            "app.itinerary_planning.models",
            fromlist=["GetCandidateEvidenceArgs"],
        ).GetCandidateEvidenceArgs(candidate_handles=("candidate-handle-0000",)),
    )
    responses = tuple(
        ToolCallsReady(calls=(call,), metadata=_metadata())
        for call in matrix_calls
    ) + (ToolCallsReady(calls=(terminal_evidence,), metadata=_metadata()),)
    service, request, _, operation, repository = _fixture(*responses)
    base = request.operation_plan
    matrix_ids = tuple(
        OperationId(
            run_id=base.run_id,
            phase=WorkflowPhase.ITINERARY_PLANNING,
            logical_step=f"terminal_matrix_{index}",
        )
        for index in range(5)
    )
    request = request.model_copy(update={
        "operation_plan": base.model_copy(update={"matrix_operation_ids": matrix_ids}),
    })

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningFailed)
    assert result.failure.code is PlanningFailureCode.MODEL_CONTRACT_INVALID
    assert result.budgets.model_invocations_consumed == 7
    assert operation.call_count == 7
    terminal_actions = [
        item for item in repository.put_requests
        if item.artifact_type == "itinerary_planning.model_action"
        and item.operation_id == request.operation_plan.invocation_slots[6].action_operation_id
    ]
    assert len(terminal_actions) == 1
    assert terminal_actions[0].content["disposition"] == "controller_rejected"


@pytest.mark.asyncio
async def test_target_conversion_and_safe_read_helpers_cover_all_typed_target_kinds() -> None:
    service, request, *_ = _fixture(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    await service.plan_initial(request)
    run = service._runs[request.operation_plan.run_id]

    targets = (
        ProposedCandidateCountTarget(role=ResearchRole.ATTRACTION, current_count=1, target_count=2),
        ProposedAreaCoverageTarget(role=ResearchRole.ATTRACTION, anchor_handle="candidate-handle-0000", radius_km=1),
        ProposedMealCoverageTarget(meal_intent=MealIntent.LUNCH, anchor_handle="candidate-handle-0000", radius_km=1),
        ProposedMustResolutionTarget(requirement_group_id="must", requirement_item_ids=("item",)),
        ProposedAvoidResolutionTarget(requirement_group_id="avoid", requirement_item_ids=("item",), candidate_handles=("candidate-handle-0000",)),
        ProposedCandidateDetailsTarget(candidate_handles=("candidate-handle-0000",)),
        ProposedReplacementTarget(rejected_candidate_handle="candidate-handle-0000", role=ResearchRole.ATTRACTION, anchor_handle=None, radius_km=None),
    )
    converted = tuple(service._target(run, target) for target in targets)
    assert converted[0].target_type == "candidate_count"
    assert converted[1].target_type == "area_coverage"
    assert converted[2].target_type == "meal_coverage"
    assert converted[3].target_type == "must_resolution"
    assert converted[4].target_type == "avoid_resolution"
    assert converted[5].target_type == "candidate_details"
    assert converted[6].target_type == "replacement"
    assert isinstance(converted[5], CandidateDetailsTarget)
    assert service._read_evidence(run, ("candidate-handle-0000",)).status == "success"
    assert service._read_evidence(run, ("missing",)).status == "no_result"
    assert service._location(run, "hotel-handle")[1] == "hotel-handle"
    with pytest.raises(_BusinessRejected):
        service._coordinate(run, object())
    assert service._direction_key(run).name == "planning.direction_call"
    assert _handles({"origin_handle": "a", "nested": [{"candidate_handles": ["b", 1]}]}) == ("a", "b")
    assert _confirmed_digest(request.confirmed) == request.confirmed.confirmation_summary_digest
    assert _confirmed_digest(request.confirmed.model_copy(update={"confirmation_summary_digest": "invalid"})).startswith("sha256:")
    with pytest.raises(ValueError):
        _fresh(request, type(request.operation_plan))
