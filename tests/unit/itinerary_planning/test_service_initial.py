"""09-5 initial/ReAct/生命周期的最小端到端回归。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.baidu_map import (
    BaiduCoordinate,
    GeocodeCandidate,
    GeocodeSuccess,
    MapCapability,
    MatrixTravelMode,
    TravelMode,
)
from app.candidate_research import (
    CandidateState,
    CandidateResearchArtifact,
    MealIntent,
    MatchVerdict,
    ResearchMode,
    ResearchRole,
    RequirementReasonCode,
    RoleReasonCode,
    RoleVerdict,
)
from app.candidate_research.artifacts import (
    ArtifactCandidateCoreSnapshot,
    ArtifactCandidateDetailsMissingSummary,
    ArtifactCandidateDetailsStateSnapshot,
    ArtifactCandidateRecordSnapshot,
    ArtifactCoordinateSnapshot,
    ArtifactKnownLLMUsageSnapshot,
    ArtifactResearchBudgetSummarySnapshot,
    ArtifactResearchCountSummarySnapshot,
    ArtifactResearchCoverageSummarySnapshot,
    ArtifactRequirementMatchSnapshot,
    ArtifactRequirementGroupState,
    ArtifactRoleAssessmentSnapshot,
    CandidateSetStateSnapshot,
    IncompleteCandidateSetRecord,
    SearchCoverageState,
)
from app.candidate_research import CandidateResearchStopReason
from app.hotel_selection import HotelSelectedDecision, HotelSelectionArtifact
from app.hotel_selection.models import HotelCandidate, HotelCoordinate
from app.itinerary_planning import (
    ItineraryPlanningCompleted,
    ItineraryPlanningFailed,
    ItineraryPlanningIncomplete,
    ItineraryPlanningInitialRequest,
    ItineraryPlanningOperationPlan,
    ItineraryPlanningService,
    PlanningFailureCode,
    PlanningIterationOperationIds,
    PlanningStopReason,
    PlanningTool,
    ItineraryPlanningUsageError,
)
from app.itinerary_planning.models import (
    BackupOption,
    CalculateRouteMatrixArgs,
    DayPlan,
    ItineraryDraft,
    PlanningDayKind,
    ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
    ITINERARY_PLANNING_ITERATION_BUDGET_KEY,
    ProposedBoundaryTransfer,
    ProposedDayPlan,
    ProposedDailyTimeRule,
    ProposedEffectivePlanningPolicy,
    ProposedItineraryDraft,
    ProposedSupplementGap,
    ProposedCandidateCountTarget,
    ProposedScheduledVisit,
    ProposedTimeSensitiveMustAnchor,
    ProposedBackupOption,
    CandidateLocationRef,
    ProposedRouteLeg,
    ProposedTransportPolicyRule,
    HubLocationRef,
    HotelLocationRef,
    RequestCandidateSupplementArgs,
    SubmitItineraryArgs,
    ScheduledVisit,
    TimeInterval,
    TransportSemantic,
    VisitKind,
)
from app.llm_runtime import (
    InvocationMetadata,
    LLMUsage,
    ModelRole,
    ToolCallsReady,
    ToolTurnCompleted,
    ValidatedToolCall,
)
from app.requirements import (
    ConfirmedTravelRequirement,
    FieldMetadataRegistry,
    HubType,
    InterestTag,
    TravelPreferences,
    TravelPace,
    TravelRequirement,
    TravelTimeWindow,
    TravelTimeWindowArrival,
    TravelTimeWindowDeparture,
)
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase
from app.itinerary_planning.artifacts import ItineraryPlanningModelActionArtifact

from tests.support.itinerary_planning_fakes import (
    FakeMapAdapter,
    FakeModelOperation,
    FakeRepository,
    FakeRuntime,
    make_planning_budget_manager,
    map_provenance,
    matrix_success,
)
from app.itinerary_planning.fingerprints import (
    _canonical_hash,
    fingerprint_directions,
    fingerprint_directions_handle,
    fingerprint_planning_context,
)
from app.itinerary_planning.service import _Indeterminate, _confirmed_digest, _fresh
from app.itinerary_planning.tooling import (
    ToolBatchError,
    ToolDispatchResult,
    ToolObservation,
    _CandidateEvidenceObservation,
)


UTC_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _ref(run_id: UUID, artifact_type: str, phase: WorkflowPhase, step: str, content: str = "d") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid4(), artifact_type=artifact_type, schema_version="1.0",
        content_hash="sha256:" + content * 64,
        operation_id=OperationId(run_id=run_id, phase=phase, logical_step=step.replace("-", "_")),
        created_at=UTC_NOW, size_bytes=1,
    )


def _confirmed() -> ConfirmedTravelRequirement:
    return ConfirmedTravelRequirement(
        outcome_type="confirmed", schema_version="1.0",
        requirement=TravelRequirement(
            destination="杭州",
            time_window=TravelTimeWindow(
                sightseeing_start_date="2026-09-03", sightseeing_end_date="2026-09-03", sightseeing_days=1,
                arrival=TravelTimeWindowArrival(
                    hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
                    local_datetime="2026-09-02 18:00",
                ),
                departure=TravelTimeWindowDeparture(
                    hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
                    local_datetime="2026-09-04 18:00",
                ),
            ),
            preferences=TravelPreferences(
                interests=[InterestTag.CULTURE, InterestTag.NATURE],
                agent_decides_pace=True,
            ),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(),
        confirmation_summary_digest="sha256:" + "c" * 64,
    )


def _upstream(run_id: UUID) -> tuple[ArtifactRef, ArtifactRef, HotelSelectionArtifact, CandidateResearchArtifact]:
    hotel_ref = _ref(run_id, "hotel.selection", WorkflowPhase.HOTEL_SELECTION, "hotel-selection", "a")
    hotel_candidates_ref = _ref(run_id, "hotel.candidate_set", WorkflowPhase.HOTEL_SELECTION, "hotel-candidates", "b")
    hotel = HotelSelectionArtifact(
        schema_version="1.0", candidate_set_ref=hotel_candidates_ref,
        decision=HotelSelectedDecision(
            schema_version="1.0", decision_type="selected",
            selected_candidate=HotelCandidate(
                schema_version="1.0", uid="hotel-1", name="杭州酒店", address="杭州路1号",
                coordinate=HotelCoordinate(schema_version="1.0", coordinate_system="bd09ll", latitude=30.25, longitude=120.16),
                category="酒店",
            ),
        ),
    )
    _, hotel_encoded = FakeRepository._encoded(hotel)
    hotel_ref = ArtifactRef(
        artifact_id=hotel_ref.artifact_id, artifact_type=hotel_ref.artifact_type, schema_version=hotel_ref.schema_version,
        content_hash="sha256:" + hashlib.sha256(hotel_encoded).hexdigest(), operation_id=hotel_ref.operation_id,
        created_at=hotel_ref.created_at, size_bytes=len(hotel_encoded),
    )
    evidence_ref = _ref(run_id, "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH, "candidate-evidence", "e")
    candidate = ArtifactCandidateRecordSnapshot(
        schema_version="1.0", admission_ordinal=0,
        core=ArtifactCandidateCoreSnapshot(
            uid="candidate-1", name="西湖景点", address="杭州西湖路",
            coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=30.24, longitude=120.15),
            provider_category="museum",
        ),
        state=CandidateState.ELIGIBLE,
        roles=(
            ArtifactRoleAssessmentSnapshot(role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(0,)),
            ArtifactRoleAssessmentSnapshot(role=ResearchRole.RESTAURANT, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(0,)),
        ),
        interests=(InterestTag.CULTURE, InterestTag.NATURE), meal_intents=(MealIntent.LUNCH, MealIntent.DINNER),
        must_matches=(), avoid_matches=(),
        details=ArtifactCandidateDetailsStateSnapshot(status="not_requested", observation_index=None),
        observation_indexes=(0,), dietary_restrictions="not_evaluated", mobility_constraints="not_evaluated", budget_fit="not_evaluated",
    )
    zero_usage = ArtifactKnownLLMUsageSnapshot(
        prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0,
        prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0,
    )
    counts = ArtifactResearchCountSummarySnapshot(
        attraction_count=1, attraction_target=2, restaurant_count=1, restaurant_target=1,
        unique_eligible_count=1, admitted_slots=1, candidate_capacity=128,
    )
    coverage = ArtifactResearchCoverageSummarySnapshot(
        effective_interests=(InterestTag.CULTURE, InterestTag.NATURE), covered_interests=(), covered_meals=(),
        must_item_count=0, resolved_must_item_count=0, details_success_count=0, details_target=1,
        initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False,
    )
    budgets = ArtifactResearchBudgetSummarySnapshot(
        iteration_limit=6, iterations_consumed=0, place_call_limit=48, place_calls_consumed=0,
        semantic_invocation_limit=12, semantic_invocations_consumed=0, known_provider_attempts=0,
        provider_attempts_complete=True, known_usage=zero_usage, usage_complete=True,
    )
    gap = ArtifactCandidateDetailsMissingSummary(
        gap_type="candidate_details_missing", gap_id="gap:details", evidence_indexes=(0,),
        candidate_uids=("candidate-1",),
    )
    state = CandidateSetStateSnapshot(
        confirmed_requirement_digest=_confirmed().confirmation_summary_digest,
        hotel_selection_ref=hotel_ref, effective_interests=(InterestTag.CULTURE, InterestTag.NATURE),
        model_action_refs=(), model_judgment_refs=(), observation_refs=(evidence_ref,), candidates=(candidate,),
        requirement_group_states=(), search_coverage=SearchCoverageState(
            records=(), initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False,
        ), counts=counts, coverage=coverage, budgets=budgets, pending_gaps=(gap,), iteration_no_progress_streak=0,
    )
    candidate_artifact = CandidateResearchArtifact(
        schema_version="1.0", run_id=run_id, mode=ResearchMode.INITIAL, predecessor_ref=None,
        invocation_request_digest="sha256:" + "f" * 64,
        record=IncompleteCandidateSetRecord(
            record_type="incomplete", transition_kind="normal", state=state,
            counts=counts, coverage=coverage, budgets=budgets, gaps=(gap,), stop_reasons=(CandidateResearchStopReason.USEFUL_SUPPLY_NOT_FOUND,),
        ),
    )
    candidate_ref = _ref(run_id, "candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH, "candidate-set", "f")
    _, candidate_encoded = FakeRepository._encoded(candidate_artifact)
    candidate_ref = ArtifactRef(
        artifact_id=candidate_ref.artifact_id, artifact_type=candidate_ref.artifact_type, schema_version=candidate_ref.schema_version,
        content_hash="sha256:" + hashlib.sha256(candidate_encoded).hexdigest(), operation_id=candidate_ref.operation_id,
        created_at=candidate_ref.created_at, size_bytes=len(candidate_encoded),
    )
    return hotel_ref, candidate_ref, hotel, candidate_artifact


def _plan(run_id: UUID, *, suffix: str = "initial", slots: tuple[int, ...] = (0, 1, 2, 3, 4, 5)) -> ItineraryPlanningOperationPlan:
    phase = WorkflowPhase.ITINERARY_PLANNING
    iteration_slots = tuple(
        PlanningIterationOperationIds(
            invocation_slot_index=index,
            action_operation_id=OperationId(run_id=run_id, phase=phase, logical_step=f"{suffix}_action_{index}"),
            planning_state_operation_id=OperationId(run_id=run_id, phase=phase, logical_step=f"{suffix}_state_{index}"),
        )
        for index in slots
    )
    return ItineraryPlanningOperationPlan(
        run_id=run_id, iteration_slots=iteration_slots, hub_geocode_operation_ids=(), matrix_operation_ids=(),
        direction_operation_ids=(),
        terminal_action_operation_id=OperationId(run_id=run_id, phase=phase, logical_step=f"{suffix}_terminal_action"),
        terminal_successor_operation_id=OperationId(run_id=run_id, phase=phase, logical_step=f"{suffix}_terminal"),
    )


def _metadata() -> InvocationMetadata:
    return InvocationMetadata(
        role=ModelRole.ITINERARY_PLANNING, model_name="deepseek-v4-flash", finish_reason="stop",
        provider_attempts=1, possible_duplicate=False,
        usage=LLMUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0),
    )


def _fixture(*responses: object):
    run_id = uuid4()
    hotel_ref, candidate_ref, hotel, candidate = _upstream(run_id)
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel)
    repository.seed(candidate_ref, candidate)
    operation = FakeModelOperation(responses)
    runtime = FakeRuntime(operation)
    service = ItineraryPlanningService(
        runtime=runtime, map_adapter=FakeMapAdapter(), artifact_repository=repository,
        budget_manager=make_planning_budget_manager(run_id),
    )
    request = ItineraryPlanningInitialRequest(
        confirmed=_confirmed(), hotel_selection_ref=hotel_ref, candidate_set_ref=candidate_ref,
        operation_plan=_plan(run_id),
    )
    return service, request, runtime, operation, repository


@pytest.mark.asyncio
async def test_optional_backups_cannot_start_a_supplement_cycle():
    from app.itinerary_planning.models import ProposedAreaCoverageTarget
    args = RequestCandidateSupplementArgs(
        effective_policy=ProposedEffectivePlanningPolicy(
            effective_pace=TravelPace.NORMAL, daily_time_rules=(),
            time_sensitive_must=(), transport_rules=(),
        ), draft=None,
        proposed_gaps=(ProposedSupplementGap(
            source_gap_type="backup_unavailable", date=date(2026, 9, 3),
            target=ProposedAreaCoverageTarget(role=ResearchRole.ATTRACTION,
                anchor_handle="candidate-handle-0000", radius_km=3),
            evidence_handles=("evidence-handle-0000",),
        ),),
    )
    response = ToolCallsReady(calls=(ValidatedToolCall(call_id="optional_backup",
        tool_name=PlanningTool.REQUEST_CANDIDATE_SUPPLEMENT.value, arguments=args),), metadata=_metadata())
    service, request, _, operation, _ = _fixture(response, ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    try:
        result = await service.plan_initial(request)
        assert result.outcome_type != "awaiting_candidate_supplement"
        assert not any(gap.gap_type == "backup_unavailable" for gap in result.gaps)
        assert result.budgets.direction_calls_consumed == 0
        assert isinstance(result, ItineraryPlanningIncomplete)
        assert operation.call_count == 1
        assert operation.continuations == []
        assert service._runs[request.operation_plan.run_id].supplement_fingerprints == []
    finally:
        await service.aclose()


def _test_requirement_id(prefix: str, tree: dict[str, object]) -> str:
    payload = json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()[:16]}"


def _expected_must_groups(confirmed: ConfirmedTravelRequirement) -> tuple[ArtifactRequirementGroupState, ...]:
    digest = confirmed.confirmation_summary_digest
    grouped: list[dict[str, object]] = []
    by_text: dict[str, dict[str, object]] = {}
    for index, text in enumerate(confirmed.requirement.preferences.must_visit_places):
        item_id = _test_requirement_id(
            f"reqitem:must_visit:{index}",
            {
                "confirmed_requirement_digest": digest,
                "field": "must_visit",
                "index": index,
                "text": text,
            },
        )
        group = by_text.get(text)
        if group is None:
            group = {"first_index": index, "text": text, "item_ids": [item_id]}
            by_text[text] = group
            grouped.append(group)
        else:
            cast(list[str], group["item_ids"]).append(item_id)
    return tuple(
        ArtifactRequirementGroupState(
            group_id=_test_requirement_id(
                f"reqgroup:must_visit:{group['first_index']}",
                {
                    "confirmed_requirement_digest": digest,
                    "field": "must_visit",
                    "item_ids": group["item_ids"],
                    "text": group["text"],
                },
            ),
            requirement_kind="must_visit",
            requirement_item_ids=tuple(cast(list[str], group["item_ids"])),
            evaluated_candidate_ordinals=(),
            default_verdict=MatchVerdict.UNRESOLVED,
            default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT,
            default_evidence_indexes=(0,),
            exceptions=(),
        )
        for group in grouped
    )


def _seed_requirement_groups(
    repository: FakeRepository,
    request: ItineraryPlanningInitialRequest,
    groups: tuple[ArtifactRequirementGroupState, ...],
) -> ItineraryPlanningInitialRequest:
    candidate = CandidateResearchArtifact.model_validate_json(
        json.dumps(repository.contents[request.candidate_set_ref.artifact_id], ensure_ascii=False),
        strict=True,
    )
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
    ref = request.candidate_set_ref.model_copy(update={
        "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    })
    repository.seed(ref, updated)
    return request.model_copy(update={"candidate_set_ref": ref})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    (
        "destination_missing",
        "start_missing",
        "end_missing",
        "days_missing",
        "days_over_limit",
        "days_mismatch",
        "arrival_hub_missing",
        "arrival_type_missing",
        "arrival_time_missing",
        "arrival_after_start",
        "departure_hub_missing",
        "departure_type_missing",
        "departure_time_missing",
        "departure_before_end",
        "pace_missing",
        "pace_conflicting",
        "interests_missing",
        "interests_conflicting",
    ),
)
async def test_initial_rejects_invalid_supported_module01_fields_before_upstream_reads(
    case: str,
) -> None:
    service, request, runtime, operation, repository = _fixture(
        ToolTurnCompleted(final_text="不应进入规划循环", metadata=_metadata()),
    )
    requirement = request.confirmed.requirement
    window = requirement.time_window
    preferences = requirement.preferences
    if case == "destination_missing":
        requirement = requirement.model_copy(update={"destination": None})
    elif case == "start_missing":
        window = window.model_copy(update={"sightseeing_start_date": None})
    elif case == "end_missing":
        window = window.model_copy(update={"sightseeing_end_date": None})
    elif case == "days_missing":
        window = window.model_copy(update={"sightseeing_days": None})
    elif case == "days_over_limit":
        window = window.model_copy(update={"sightseeing_days": 61})
    elif case == "days_mismatch":
        window = window.model_copy(update={"sightseeing_days": 2})
    elif case == "arrival_hub_missing":
        window = window.model_copy(update={"arrival": window.arrival.model_copy(update={"hub_name": None})})
    elif case == "arrival_type_missing":
        window = window.model_copy(update={"arrival": window.arrival.model_copy(update={"hub_type": None})})
    elif case == "arrival_time_missing":
        window = window.model_copy(update={"arrival": window.arrival.model_copy(update={"local_datetime": None})})
    elif case == "arrival_after_start":
        window = window.model_copy(update={"arrival": window.arrival.model_copy(update={"local_datetime": "2026-09-04 09:00"})})
    elif case == "departure_hub_missing":
        window = window.model_copy(update={"departure": window.departure.model_copy(update={"hub_name": None})})
    elif case == "departure_type_missing":
        window = window.model_copy(update={"departure": window.departure.model_copy(update={"hub_type": None})})
    elif case == "departure_time_missing":
        window = window.model_copy(update={"departure": window.departure.model_copy(update={"local_datetime": None})})
    elif case == "departure_before_end":
        window = window.model_copy(update={"departure": window.departure.model_copy(update={"local_datetime": "2026-09-02 18:00"})})
    elif case == "pace_missing":
        preferences = preferences.model_copy(update={"pace": None, "agent_decides_pace": False})
    elif case == "pace_conflicting":
        preferences = preferences.model_copy(update={"pace": TravelPace.NORMAL, "agent_decides_pace": True})
    elif case == "interests_missing":
        preferences = preferences.model_copy(update={"interests": None, "agent_decides_interests": False})
    elif case == "interests_conflicting":
        preferences = preferences.model_copy(update={"agent_decides_interests": True})
    else:
        raise AssertionError(case)
    requirement = requirement.model_copy(update={"time_window": window, "preferences": preferences})
    confirmed = request.confirmed.model_copy(update={"requirement": requirement})

    result = await service.plan_initial(request.model_copy(update={"confirmed": confirmed}))

    assert isinstance(result, ItineraryPlanningFailed), result.model_dump(mode="json")
    assert result.failure.code is PlanningFailureCode.INPUT_INVALID
    assert repository.get_calls == []
    assert repository.put_requests == []
    assert runtime.start_count == 0
    assert operation.call_count == 0
    assert not service._map_adapter.geocode_calls
    assert not service._map_adapter.matrix_calls
    assert not service._map_adapter.directions_calls


@pytest.mark.asyncio
async def test_missing_confirmed_must_group_is_rejected_before_any_side_effect() -> None:
    service, request, runtime, operation, repository = _fixture(
        ToolTurnCompleted(final_text="不应进入规划循环", metadata=_metadata()),
    )
    preferences = request.confirmed.requirement.preferences.model_copy(
        update={"must_visit_places": ["西湖"]},
    )
    confirmed = request.confirmed.model_copy(update={
        "requirement": request.confirmed.requirement.model_copy(update={"preferences": preferences}),
    })
    request = request.model_copy(update={"confirmed": confirmed})

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningFailed), result.model_dump(mode="json")
    assert result.failure.code is PlanningFailureCode.PREDECESSOR_INVALID
    assert runtime.start_count == 0
    assert operation.call_count == 0
    assert not service._map_adapter.geocode_calls
    assert not service._map_adapter.matrix_calls
    assert not service._map_adapter.directions_calls
    assert repository.put_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ("partial", "forged", "duplicate"))
async def test_initial_rejects_incomplete_or_forged_must_item_coverage(case: str) -> None:
    service, request, runtime, operation, repository = _fixture(
        ToolTurnCompleted(final_text="不应进入规划循环", metadata=_metadata()),
    )
    preferences = request.confirmed.requirement.preferences.model_copy(
        update={"must_visit_places": ["西湖", "灵隐寺"]},
    )
    confirmed = request.confirmed.model_copy(update={
        "requirement": request.confirmed.requirement.model_copy(update={"preferences": preferences}),
    })
    expected = _expected_must_groups(confirmed)
    if case == "partial":
        actual = expected[:1]
    elif case == "forged":
        actual = (
            expected[0].model_copy(update={"requirement_item_ids": ("reqitem:must_visit:0:forged",)}),
            expected[1],
        )
    else:
        actual = (expected[0], expected[1].model_copy(update={
            "requirement_item_ids": expected[0].requirement_item_ids,
        }))
    request = _seed_requirement_groups(repository, request, actual).model_copy(update={"confirmed": confirmed})

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningFailed), result.model_dump(mode="json")
    assert result.failure.code is PlanningFailureCode.PREDECESSOR_INVALID
    assert runtime.start_count == 0
    assert operation.call_count == 0
    assert not service._map_adapter.geocode_calls
    assert not service._map_adapter.matrix_calls
    assert not service._map_adapter.directions_calls
    assert repository.put_requests == []


@pytest.mark.asyncio
async def test_initial_accepts_distinct_item_ids_for_identical_must_texts() -> None:
    service, request, runtime, operation, repository = _fixture(
        ToolTurnCompleted(final_text="继续", metadata=_metadata()),
    )
    preferences = request.confirmed.requirement.preferences.model_copy(
        update={"must_visit_places": ["西湖", "西湖"]},
    )
    confirmed = request.confirmed.model_copy(update={
        "requirement": request.confirmed.requirement.model_copy(update={"preferences": preferences}),
    })
    expected = _expected_must_groups(confirmed)
    assert len(expected) == 1 and len(expected[0].requirement_item_ids) == 2
    request = _seed_requirement_groups(repository, request, expected).model_copy(update={"confirmed": confirmed})

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
    assert runtime.start_count == 1
    assert operation.call_count == 1


@pytest.mark.asyncio
async def test_initial_ordinary_text_returns_incomplete_and_exact_terminal_replay() -> None:
    response = ToolTurnCompleted(final_text="需要更多规划信息", metadata=_metadata())
    service, request, runtime, operation, repository = _fixture(response)

    first = await service.plan_initial(request)
    second = await service.plan_initial(request)

    assert isinstance(first, ItineraryPlanningIncomplete)
    assert first.stop_reasons == (PlanningStopReason.NO_TERMINAL_SUBMISSION,)
    assert second == first
    assert runtime.start_count == 1
    assert operation.call_count == 1
    assert len(repository.put_requests) == 2
    assert '"direction_slots_total":0' in operation.prompts[0].user_text
    assert '"remaining_direction_slots":0' in operation.prompts[0].user_text


@pytest.mark.asyncio
async def test_initial_projects_supported_structured_fields_but_not_deferred_text() -> None:
    service, request, _, operation, _ = _fixture(
        ToolTurnCompleted(final_text="结束", metadata=_metadata()),
    )
    preferences = request.confirmed.requirement.preferences.model_copy(update={
        "transport_preferences": "DEFERRED_TRANSPORT_SENTINEL",
        "hotel_preferences": "DEFERRED_HOTEL_SENTINEL",
        "daily_time_preferences": "DEFERRED_DAILY_SENTINEL",
        "free_text_notes": "DEFERRED_NOTES_SENTINEL",
    })
    constraints = request.confirmed.requirement.constraints.model_copy(update={
        "mobility_constraints": "DEFERRED_MOBILITY_SENTINEL",
        "dietary_restrictions": "DEFERRED_DIET_SENTINEL",
    })
    requirement = request.confirmed.requirement.model_copy(update={
        "preferences": preferences,
        "constraints": constraints,
    })
    request = request.model_copy(update={
        "confirmed": request.confirmed.model_copy(update={"requirement": requirement}),
    })

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
    context_text = operation.prompts[0].user_text
    context = json.loads(
        context_text.split("BEGIN_TYPED_PLANNING_DATA\n", 1)[1].split(
            "\nEND_TYPED_PLANNING_DATA", 1
        )[0]
    )
    assert context["destination"] == "杭州"
    assert context["sightseeing_start_date"] == "2026-09-03"
    assert context["sightseeing_end_date"] == "2026-09-03"
    assert context["sightseeing_days"] == 1
    assert context["agent_decides_pace"] is True
    assert context["confirmed"]["arrival"]["local_datetime"] == "2026-09-02 18:00"
    assert context["confirmed"]["departure"]["local_datetime"] == "2026-09-04 18:00"
    for sentinel in (
        "DEFERRED_TRANSPORT_SENTINEL",
        "DEFERRED_HOTEL_SENTINEL",
        "DEFERRED_DAILY_SENTINEL",
        "DEFERRED_NOTES_SENTINEL",
        "DEFERRED_MOBILITY_SENTINEL",
        "DEFERRED_DIET_SENTINEL",
    ):
        assert sentinel not in context_text


@pytest.mark.asyncio
async def test_empty_tool_calls_are_failed_with_replayable_terminal_artifact() -> None:
    service, request, _, _, repository = _fixture(ToolCallsReady(calls=(), metadata=_metadata()))

    result = await service.plan_initial(request)

    assert result.outcome_type == "failed"
    assert result.failure.code is PlanningFailureCode.MODEL_CONTRACT_INVALID
    assert result.budgets is not None and result.budgets.iterations_consumed == 0
    terminal = [item for item in repository.put_requests if item.artifact_type == "itinerary_planning.itinerary"]
    assert terminal
    replay = await service.plan_initial(request)
    assert replay == result


@pytest.mark.asyncio
async def test_six_business_cycles_use_terminal_only_once() -> None:
    responses = [
        ToolCallsReady(
            calls=(ValidatedToolCall(
                call_id=f"evidence-{index}", tool_name=PlanningTool.GET_CANDIDATE_EVIDENCE.value,
                arguments=__import__("app.itinerary_planning.models", fromlist=["GetCandidateEvidenceArgs"]).GetCandidateEvidenceArgs(candidate_handles=("candidate-handle-0000",)),
            ),),
            metadata=_metadata(),
        )
        for index in range(6)
    ]
    responses.append(ToolTurnCompleted(final_text="未提交", metadata=_metadata()))
    service, request, _, operation, _ = _fixture(*responses)

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
    assert result.stop_reasons == (PlanningStopReason.NO_PROGRESS,)
    assert result.budgets.iterations_consumed == 3
    assert operation.call_count == 3
    assert len(operation.continuations) == 2


@pytest.mark.asyncio
async def test_close_run_keeps_exact_replay_but_rejects_new_request_and_aclose_is_usage_error() -> None:
    service, request, _, _, _ = _fixture(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    first = await service.plan_initial(request)
    await service.close_run(request.operation_plan.run_id)
    replay = await service.plan_initial(request)
    assert replay == first

    closed = await service.plan_initial(request.model_copy(update={"operation_plan": _plan(request.operation_plan.run_id, suffix="new")}))
    assert closed.outcome_type == "failed"
    assert closed.failure.code is PlanningFailureCode.RUN_CONTEXT_CLOSED
    await service.aclose()
    await service.aclose()
    with pytest.raises(ItineraryPlanningUsageError):
        await service.plan_initial(request)

    # The invalid object proves the closed check runs before resume request parsing.
    with pytest.raises(ItineraryPlanningUsageError):
        await service.plan_resume(object())


@pytest.mark.asyncio
async def test_service_dispatches_matrix_and_directions_then_persists_both_observation_families() -> None:
    matrix_call = ValidatedToolCall(
        call_id="matrix_0",
        tool_name=PlanningTool.CALCULATE_ROUTE_MATRIX.value,
        arguments=__import__("app.itinerary_planning.models", fromlist=["CalculateRouteMatrixArgs"]).CalculateRouteMatrixArgs(
            date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING, location_handles=("candidate-handle-0000", "hotel-handle"),
        ),
    )
    direction_call = ValidatedToolCall(
        call_id="direction_0",
        tool_name=PlanningTool.PLAN_DIRECTIONS.value,
        arguments=__import__("app.itinerary_planning.models", fromlist=["PlanDirectionsArgs", "DirectionLegIntent"]).PlanDirectionsArgs(legs=(
            __import__("app.itinerary_planning.models", fromlist=["DirectionLegIntent"]).DirectionLegIntent(
                leg_id="leg-0", origin_handle="candidate-handle-0000", destination_handle="hotel-handle",
                transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1,
            ),
        )),
    )
    service, request, _, operation, repository = _fixture(
        ToolCallsReady(calls=(matrix_call,), metadata=_metadata()),
        ToolCallsReady(calls=(direction_call,), metadata=_metadata()),
        ToolTurnCompleted(final_text="结束", metadata=_metadata()),
    )
    base = request.operation_plan
    matrix_id = OperationId(run_id=base.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="matrix_0")
    direction_id = OperationId(run_id=base.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step="direction_0")
    plan = base.model_copy(update={"matrix_operation_ids": (matrix_id,), "direction_operation_ids": (direction_id,)})
    request = request.model_copy(update={"operation_plan": plan})

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete), f"{type(result).__name__}: {result.failure.model_dump(mode='json')}"
    assert len(operation.continuations) == 2
    for continuation in operation.continuations:
        payloads = tuple(json.loads(item.content) for item in continuation)
        assert all("planning_context" not in payload for payload in payloads)
        assert sum("planning_state" in payload for payload in payloads) == 1
        state = next(payload["planning_state"] for payload in payloads if "planning_state" in payload)
        assert state["candidates"] == []
        assert state["matrix_slots_total"] == 1
        assert state["remaining_matrix_slots"] in {0, 1}
    assert any(item.artifact_type == "itinerary_planning.route_matrix_observation" for item in repository.put_requests)
    assert any(item.artifact_type == "itinerary_planning.directions_observation" for item in repository.put_requests)


@pytest.mark.asyncio
async def test_public_initial_accepts_cross_midnight_hotel_closure_and_marks_next_day_partial() -> None:
    service, request, _, operation, repository = _fixture()
    candidate_artifact = CandidateResearchArtifact.model_validate_json(
        json.dumps(repository.contents[request.candidate_set_ref.artifact_id], ensure_ascii=False),
        strict=True,
    )
    first = candidate_artifact.record.state.candidates[0]
    candidates = tuple(
        first.model_copy(update={
            "admission_ordinal": index,
            "core": first.core.model_copy(update={
                "uid": f"candidate-{index + 1}",
                "name": f"跨午夜候选{index + 1}",
                "address": f"跨午夜路{index + 1}",
                "coordinate": first.core.coordinate.model_copy(update={
                    "latitude": 30.24 + index * 0.001,
                    "longitude": 120.15 + index * 0.001,
                }),
            }),
        })
        for index in range(8)
    )
    counts = candidate_artifact.record.state.counts.model_copy(update={
        "attraction_count": 8,
        "restaurant_count": 8,
        "unique_eligible_count": 8,
        "admitted_slots": 8,
    })
    state = candidate_artifact.record.state.model_copy(update={"candidates": candidates, "counts": counts})
    coverage = state.coverage.model_copy(update={"details_target": 3})
    state = state.model_copy(update={"coverage": coverage})
    expanded = candidate_artifact.model_copy(update={
        "record": candidate_artifact.record.model_copy(update={"state": state, "counts": counts, "coverage": coverage}),
    })
    _, encoded = FakeRepository._encoded(expanded)
    expanded_ref = request.candidate_set_ref.model_copy(update={
        "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    })
    repository.seed(expanded_ref, expanded)

    shanghai = ZoneInfo("Asia/Shanghai")
    window = request.confirmed.requirement.time_window.model_copy(update={
        "sightseeing_start_date": "2026-09-03",
        "sightseeing_end_date": "2026-09-04",
        "sightseeing_days": 2,
        "arrival": TravelTimeWindowArrival(
            hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
            local_datetime="2026-09-02 08:00",
        ),
        "departure": TravelTimeWindowDeparture(
            hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
            local_datetime="2026-09-05 20:00",
        ),
    })
    preferences = request.confirmed.requirement.preferences.model_copy(update={
        "daily_time_preferences": "首日可安排至次日零点半",
    })
    confirmed = request.confirmed.model_copy(update={
        "requirement": request.confirmed.requirement.model_copy(update={
            "time_window": window,
            "preferences": preferences,
        }),
    })
    state = state.model_copy(update={"confirmed_requirement_digest": _confirmed_digest(confirmed)})
    expanded = expanded.model_copy(update={
        "record": candidate_artifact.record.model_copy(update={"state": state, "counts": counts, "coverage": coverage}),
    })
    _, encoded = FakeRepository._encoded(expanded)
    expanded_ref = request.candidate_set_ref.model_copy(update={
        "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    })
    repository.seed(expanded_ref, expanded)
    request = request.model_copy(update={
        "confirmed": confirmed,
        "candidate_set_ref": expanded_ref,
    })

    hub = BaiduCoordinate(latitude=30.29, longitude=120.21)
    hotel = BaiduCoordinate(latitude=30.25, longitude=120.16)
    coordinates = {
        f"candidate-handle-{index:04d}": BaiduCoordinate(
            latitude=30.24 + index * 0.001, longitude=120.15 + index * 0.001
        )
        for index in range(8)
    }
    service._map_adapter = FakeMapAdapter(
        geocode_factory=lambda _: GeocodeSuccess(
            result=GeocodeCandidate(formatted_address="杭州东站", coordinate=hub, confidence=0.98),
            provenance=map_provenance(MapCapability.GEOCODING),
        ),
        directions_factory=lambda value: __import__(
            "tests.support.itinerary_planning_fakes", fromlist=["directions_success"]
        ).directions_success(
            duration_s=1800
            if (
                (value.origin.coordinate == hub and value.destination.coordinate == hotel)
                or (value.origin.coordinate == hotel and value.destination.coordinate == hub)
                or (value.origin.coordinate == coordinates["candidate-handle-0002"] and value.destination.coordinate == hotel)
            )
            else 0,
            distance_m=300
            if (
                (value.origin.coordinate == hub and value.destination.coordinate == hotel)
                or (value.origin.coordinate == hotel and value.destination.coordinate == hub)
                or (value.origin.coordinate == coordinates["candidate-handle-0002"] and value.destination.coordinate == hotel)
            )
            else 0
        ),
    )

    base = request.operation_plan
    phase = WorkflowPhase.ITINERARY_PLANNING
    hub_ids = tuple(
        OperationId(run_id=base.run_id, phase=phase, logical_step=f"cross_midnight_hub_{kind}")
        for kind in ("arrival", "departure")
    )
    matrix_ids = tuple(
        OperationId(run_id=base.run_id, phase=phase, logical_step=f"cross_midnight_matrix_{index}")
        for index in range(2)
    )
    direction_ids = tuple(
        OperationId(run_id=base.run_id, phase=phase, logical_step=f"cross_midnight_direction_{index}")
        for index in range(8)
    )
    request = request.model_copy(update={"operation_plan": base.model_copy(update={
        "hub_geocode_operation_ids": hub_ids,
        "matrix_operation_ids": matrix_ids,
        "direction_operation_ids": direction_ids,
    })})

    models = __import__(
        "app.itinerary_planning.models",
        fromlist=["GeocodeHubArgs", "PlanDirectionsArgs", "DirectionLegIntent", "ProposedDailyTimeRule"],
    )
    geocode_calls = (
        ValidatedToolCall(call_id="cross_arrival", tool_name=PlanningTool.GEOCODE_HUB.value, arguments=models.GeocodeHubArgs(endpoint_kind="arrival")),
        ValidatedToolCall(call_id="cross_departure", tool_name=PlanningTool.GEOCODE_HUB.value, arguments=models.GeocodeHubArgs(endpoint_kind="departure")),
    )
    matrix_calls = (
        ValidatedToolCall(call_id="cross_matrix_day1", tool_name=PlanningTool.CALCULATE_ROUTE_MATRIX.value, arguments=CalculateRouteMatrixArgs(
            date=date(2026, 9, 3), mode=MatrixTravelMode.WALKING,
            location_handles=tuple(f"candidate-handle-{index:04d}" for index in range(6)),
        )),
        ValidatedToolCall(call_id="cross_matrix_day2", tool_name=PlanningTool.CALCULATE_ROUTE_MATRIX.value, arguments=CalculateRouteMatrixArgs(
            date=date(2026, 9, 4), mode=MatrixTravelMode.WALKING,
            location_handles=("candidate-handle-0006", "candidate-handle-0007"),
        )),
    )
    arrival_ref = HubLocationRef(endpoint_kind="arrival", geocode_observation_index=0)
    departure_ref = HubLocationRef(endpoint_kind="departure", geocode_observation_index=1)
    hotel_ref = HotelLocationRef()
    locations = {
        "hub-location-arrival": arrival_ref,
        "hub-location-departure": departure_ref,
        "hotel-handle": hotel_ref,
        **{
            handle: CandidateLocationRef(admission_ordinal=index, uid=f"candidate-{index + 1}")
            for index, handle in enumerate(coordinates)
        },
    }
    location_coordinates = {"hub-location-arrival": hub, "hub-location-departure": hub, "hotel-handle": hotel, **coordinates}
    route_specs = (
        ("cross-arrival", "hub-location-arrival", "hotel-handle", datetime(2026, 9, 2, 8, 30, tzinfo=shanghai), datetime(2026, 9, 2, 9, tzinfo=shanghai)),
        ("cross-departure", "hotel-handle", "hub-location-departure", datetime(2026, 9, 5, 18, 30, tzinfo=shanghai), datetime(2026, 9, 5, 19, tzinfo=shanghai)),
        ("cross-day1-out", "hotel-handle", "candidate-handle-0000", datetime(2026, 9, 3, 10, 0, tzinfo=shanghai), datetime(2026, 9, 3, 10, 0, tzinfo=shanghai)),
        ("cross-day1-lunch", "candidate-handle-0000", "candidate-handle-0001", datetime(2026, 9, 3, 11, 0, tzinfo=shanghai), datetime(2026, 9, 3, 11, 0, tzinfo=shanghai)),
        ("cross-day1-dinner", "candidate-handle-0001", "candidate-handle-0002", datetime(2026, 9, 3, 12, 30, tzinfo=shanghai), datetime(2026, 9, 3, 12, 30, tzinfo=shanghai)),
        ("cross-day1-close", "candidate-handle-0002", "hotel-handle", datetime(2026, 9, 4, 0, 0, tzinfo=shanghai), datetime(2026, 9, 4, 0, 30, tzinfo=shanghai)),
        ("cross-day2-out", "hotel-handle", "candidate-handle-0006", datetime(2026, 9, 4, 10, 0, tzinfo=shanghai), datetime(2026, 9, 4, 10, 0, tzinfo=shanghai)),
        ("cross-day2-close", "candidate-handle-0006", "hotel-handle", datetime(2026, 9, 4, 11, 0, tzinfo=shanghai), datetime(2026, 9, 4, 11, 0, tzinfo=shanghai)),
    )
    context = fingerprint_planning_context(
        base.run_id, _confirmed_digest(request.confirmed), request.hotel_selection_ref,
        _canonical_hash("杭州东站"), _canonical_hash("杭州东站"),
    )
    direction_intents = []
    direction_handles = []
    for index, (leg_id, origin, destination, _, _) in enumerate(route_specs):
        fingerprint = fingerprint_directions(
            context, locations[origin], location_coordinates[origin], locations[destination],
            location_coordinates[destination], TravelMode.WALKING, 1,
        )
        direction_handles.append(fingerprint_directions_handle(direction_ids[index], index, fingerprint))
        direction_intents.append(models.DirectionLegIntent(
            leg_id=leg_id, origin_handle=origin, destination_handle=destination,
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1,
        ))
    directions_call = ValidatedToolCall(
        call_id="cross_directions", tool_name=PlanningTool.PLAN_DIRECTIONS.value,
        arguments=models.PlanDirectionsArgs(legs=tuple(direction_intents)),
    )

    def route(index: int) -> ProposedRouteLeg:
        leg_id, origin, destination, start_at, end_at = route_specs[index]
        return ProposedRouteLeg(
            leg_id=leg_id, origin_handle=origin, destination_handle=destination,
            start_at=start_at, end_at=end_at, transport_semantic=TransportSemantic.WALKING,
            route_mode=TravelMode.WALKING, directions_handle=direction_handles[index], alternative_index=0,
        )

    unknown = __import__("app.itinerary_planning.models", fromlist=["BusinessHoursState"]).BusinessHoursState.UNKNOWN
    day1 = ProposedDayPlan(
        date=date(2026, 9, 3), day_kind=PlanningDayKind.FULL, theme="跨午夜首日",
        open_intervals=(TimeInterval(start_at=datetime(2026, 9, 3, 9, 0, tzinfo=shanghai), end_at=datetime(2026, 9, 4, 0, 30, tzinfo=shanghai)),),
        visits=(
            ProposedScheduledVisit(visit_id="cross-attraction", candidate_handle="candidate-handle-0000", visit_kind=VisitKind.ATTRACTION, start_at=datetime(2026, 9, 3, 10, 0, tzinfo=shanghai), end_at=datetime(2026, 9, 3, 11, 0, tzinfo=shanghai), duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(), business_hours_state=unknown, business_evidence_handles=()),
            ProposedScheduledVisit(visit_id="cross-lunch", candidate_handle="candidate-handle-0001", visit_kind=VisitKind.LUNCH, start_at=datetime(2026, 9, 3, 11, 30, tzinfo=shanghai), end_at=datetime(2026, 9, 3, 12, 30, tzinfo=shanghai), duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(), business_hours_state=unknown, business_evidence_handles=()),
            ProposedScheduledVisit(visit_id="cross-dinner", candidate_handle="candidate-handle-0002", visit_kind=VisitKind.DINNER, start_at=datetime(2026, 9, 3, 17, 30, tzinfo=shanghai), end_at=datetime(2026, 9, 3, 19, 30, tzinfo=shanghai), duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(), business_hours_state=unknown, business_evidence_handles=()),
        ),
        route_legs=(route(2), route(3), route(4), route(5)),
        backups=(
            ProposedBackupOption(backup_kind="attraction", core_visit_id="cross-attraction", candidate_handle="candidate-handle-0003", matrix_handle="matrix-handle-0000", matrix_mode=MatrixTravelMode.WALKING),
            ProposedBackupOption(backup_kind="lunch", core_visit_id="cross-lunch", candidate_handle="candidate-handle-0004", matrix_handle="matrix-handle-0000", matrix_mode=MatrixTravelMode.WALKING),
            ProposedBackupOption(backup_kind="dinner", core_visit_id="cross-dinner", candidate_handle="candidate-handle-0005", matrix_handle="matrix-handle-0000", matrix_mode=MatrixTravelMode.WALKING),
        ),
    )
    day2 = ProposedDayPlan(
        date=date(2026, 9, 4), day_kind=PlanningDayKind.PARTIAL, theme="跨午夜次日",
        open_intervals=(TimeInterval(start_at=datetime(2026, 9, 4, 9, 0, tzinfo=shanghai), end_at=datetime(2026, 9, 4, 12, 0, tzinfo=shanghai)),),
        visits=(ProposedScheduledVisit(visit_id="cross-next-day", candidate_handle="candidate-handle-0006", visit_kind=VisitKind.ATTRACTION, start_at=datetime(2026, 9, 4, 10, 0, tzinfo=shanghai), end_at=datetime(2026, 9, 4, 11, 0, tzinfo=shanghai), duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(), business_hours_state=unknown, business_evidence_handles=()),),
        route_legs=(route(6), route(7)),
        backups=(ProposedBackupOption(backup_kind="attraction", core_visit_id="cross-next-day", candidate_handle="candidate-handle-0007", matrix_handle="matrix-handle-0001", matrix_mode=MatrixTravelMode.WALKING),),
    )
    proposal = ProposedItineraryDraft(
        arrival_transfer=ProposedBoundaryTransfer(transfer_kind="arrival", hub_handle="hub-location-arrival", hotel_handle="hotel-handle", route_leg=route(0)),
        days=(day1, day2),
        departure_transfer=ProposedBoundaryTransfer(transfer_kind="departure", hub_handle="hub-location-departure", hotel_handle="hotel-handle", route_leg=route(1)),
        upstream_gap_ids=(),
    )
    submit = SubmitItineraryArgs(
        effective_policy=ProposedEffectivePlanningPolicy(
            effective_pace=TravelPace.RELAXED,
            daily_time_rules=(models.ProposedDailyTimeRule(
                rule_id="cross-midnight-window", date=date(2026, 9, 3),
                start_at=datetime(2026, 9, 3, 9, 0, tzinfo=shanghai), end_at=datetime(2026, 9, 4, 0, 30, tzinfo=shanghai),
                strength="hard", source_field="daily_time_preferences",
            ),),
            time_sensitive_must=(), transport_rules=(),
        ),
        draft=proposal,
    )
    operation.responses.extend((
        ToolCallsReady(calls=geocode_calls, metadata=_metadata()),
        ToolCallsReady(calls=matrix_calls, metadata=_metadata()),
        ToolCallsReady(calls=(directions_call,), metadata=_metadata()),
        ToolCallsReady(calls=(ValidatedToolCall(call_id="cross_submit", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=submit),), metadata=_metadata()),
        ToolTurnCompleted(final_text="延迟规则首版不启用", metadata=_metadata()),
    ))

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
    assert result.itinerary_ref is None
    assert result.budgets.direction_calls_consumed == 8
    terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
    assert result.itinerary_ref is None
    from app.itinerary_planning import ItineraryPlanningArtifact
    terminal = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
    assert terminal.record_type == "incomplete"
    assert terminal.draft is None


@pytest.mark.asyncio
async def test_invalid_submit_is_rejected_as_business_feedback_and_consumes_one_iteration() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    route = ProposedRouteLeg(
        leg_id="invalid-leg",
        origin_handle="candidate-handle-0000",
        destination_handle="hotel-handle",
        start_at=datetime(2026, 9, 3, 9, 0, tzinfo=shanghai),
        end_at=datetime(2026, 9, 3, 9, 0, tzinfo=shanghai),
        transport_semantic=TransportSemantic.WALKING,
        route_mode=TravelMode.WALKING,
        directions_handle="hotel-handle",
        alternative_index=0,
    )
    args = SubmitItineraryArgs(
        effective_policy=ProposedEffectivePlanningPolicy(
            effective_pace=TravelPace.NORMAL,
            daily_time_rules=(), time_sensitive_must=(), transport_rules=(),
        ),
        draft=ProposedItineraryDraft(
            arrival_transfer=ProposedBoundaryTransfer(transfer_kind="arrival", hub_handle="hotel-handle", hotel_handle="hotel-handle", route_leg=route),
            days=(ProposedDayPlan(date=date(2026, 9, 3), day_kind=PlanningDayKind.PARTIAL, theme="空白日", open_intervals=(), visits=(), route_legs=(), backups=()),),
            departure_transfer=ProposedBoundaryTransfer(transfer_kind="departure", hub_handle="hotel-handle", hotel_handle="hotel-handle", route_leg=route.model_copy(update={"leg_id": "invalid-leg-2"})),
            upstream_gap_ids=(),
        ),
    )
    service, request, _, operation, _ = _fixture(
        ToolCallsReady(calls=(ValidatedToolCall(call_id="submit_0", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=args),), metadata=_metadata()),
        ToolTurnCompleted(final_text="继续", metadata=_metadata()),
    )

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
    assert result.budgets.iterations_consumed == 1
    assert operation.call_count == 2


@pytest.mark.asyncio
async def test_confirmed_hubs_are_geocoded_in_order_and_persisted_as_observations() -> None:
    service, request, _, operation, repository = _fixture(
        ToolCallsReady(calls=(
            ValidatedToolCall(call_id="arrival_0", tool_name=PlanningTool.GEOCODE_HUB.value, arguments=__import__("app.itinerary_planning.models", fromlist=["GeocodeHubArgs"]).GeocodeHubArgs(endpoint_kind="arrival")),
            ValidatedToolCall(call_id="departure_0", tool_name=PlanningTool.GEOCODE_HUB.value, arguments=__import__("app.itinerary_planning.models", fromlist=["GeocodeHubArgs"]).GeocodeHubArgs(endpoint_kind="departure")),
        ), metadata=_metadata()),
        ToolTurnCompleted(final_text="结束", metadata=_metadata()),
    )
    window = request.confirmed.requirement.time_window.model_copy(update={
        "arrival": TravelTimeWindowArrival(
            hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
            local_datetime="2026-09-02 18:00",
        ),
        "departure": TravelTimeWindowDeparture(
            hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
            local_datetime="2026-09-04 18:00",
        ),
    })
    confirmed = request.confirmed.model_copy(update={
        "requirement": request.confirmed.requirement.model_copy(update={"time_window": window}),
    })
    request = request.model_copy(update={"confirmed": confirmed})
    service._map_adapter = FakeMapAdapter(geocode_factory=lambda _: GeocodeSuccess(
        result=GeocodeCandidate(
            formatted_address="杭州东站",
            coordinate=BaiduCoordinate(latitude=30.29, longitude=120.21),
            confidence=0.98,
        ),
        provenance=map_provenance(MapCapability.GEOCODING),
    ))
    base = request.operation_plan
    hub_ids = tuple(OperationId(run_id=base.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"hub_{kind}") for kind in ("arrival", "departure"))
    request = request.model_copy(update={"operation_plan": base.model_copy(update={"hub_geocode_operation_ids": hub_ids})})

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
    assert operation.call_count == 2
    assert len([item for item in repository.put_requests if item.artifact_type == "itinerary_planning.hub_geocode_observation"]) == 2


@pytest.mark.asyncio
async def test_service_rejects_transit_only_day_without_actual_boundary_transfer() -> None:
    service, request, _, operation, repository = _fixture()
    shanghai = ZoneInfo("Asia/Shanghai")
    window = request.confirmed.requirement.time_window.model_copy(update={
        "arrival": TravelTimeWindowArrival(
            hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
            local_datetime="2026-09-02 08:00",
        ),
        "departure": TravelTimeWindowDeparture(
            hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
            local_datetime="2026-09-04 20:00",
        ),
    })
    request = request.model_copy(update={
        "confirmed": request.confirmed.model_copy(update={
            "requirement": request.confirmed.requirement.model_copy(update={"time_window": window}),
        }),
    })
    service._map_adapter = FakeMapAdapter(geocode_factory=lambda _: GeocodeSuccess(
        result=GeocodeCandidate(
            formatted_address="杭州东站",
            coordinate=BaiduCoordinate(latitude=30.29, longitude=120.21),
            confidence=0.98,
        ),
        provenance=map_provenance(MapCapability.GEOCODING),
    ))
    base = request.operation_plan
    phase = WorkflowPhase.ITINERARY_PLANNING
    hub_ids = tuple(OperationId(run_id=base.run_id, phase=phase, logical_step=f"completed_hub_{kind}") for kind in ("arrival", "departure"))
    direction_ids = tuple(OperationId(run_id=base.run_id, phase=phase, logical_step=f"completed_direction_{index}") for index in range(2))
    request = request.model_copy(update={"operation_plan": base.model_copy(update={
        "hub_geocode_operation_ids": hub_ids, "direction_operation_ids": direction_ids,
    })})
    geocode_args = __import__("app.itinerary_planning.models", fromlist=["GeocodeHubArgs"])
    direction_args = __import__("app.itinerary_planning.models", fromlist=["PlanDirectionsArgs", "DirectionLegIntent"])
    calls = (
        ValidatedToolCall(call_id="geocode_arrival", tool_name=PlanningTool.GEOCODE_HUB.value, arguments=geocode_args.GeocodeHubArgs(endpoint_kind="arrival")),
        ValidatedToolCall(call_id="geocode_departure", tool_name=PlanningTool.GEOCODE_HUB.value, arguments=geocode_args.GeocodeHubArgs(endpoint_kind="departure")),
    )
    direction_call = ValidatedToolCall(
        call_id="boundary_directions", tool_name=PlanningTool.PLAN_DIRECTIONS.value,
        arguments=direction_args.PlanDirectionsArgs(legs=(
            direction_args.DirectionLegIntent(
                leg_id="arrival-transfer", origin_handle="hub-location-arrival", destination_handle="hotel-handle",
                transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1,
            ),
            direction_args.DirectionLegIntent(
                leg_id="departure-transfer", origin_handle="hotel-handle", destination_handle="hub-location-departure",
                transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1,
            ),
        )),
    )
    context = fingerprint_planning_context(
        base.run_id,
        _confirmed_digest(request.confirmed),
        request.hotel_selection_ref,
        _canonical_hash("杭州东站"),
        _canonical_hash("杭州东站"),
    )
    arrival_ref = HubLocationRef(endpoint_kind="arrival", geocode_observation_index=0)
    departure_ref = HubLocationRef(endpoint_kind="departure", geocode_observation_index=1)
    hotel_ref = HotelLocationRef()
    arrival_direction = fingerprint_directions(context, arrival_ref, BaiduCoordinate(latitude=30.29, longitude=120.21), hotel_ref, BaiduCoordinate(latitude=30.25, longitude=120.16), TravelMode.WALKING, 1)
    departure_direction = fingerprint_directions(context, hotel_ref, BaiduCoordinate(latitude=30.25, longitude=120.16), departure_ref, BaiduCoordinate(latitude=30.29, longitude=120.21), TravelMode.WALKING, 1)
    arrival_handle = fingerprint_directions_handle(direction_ids[0], 0, arrival_direction)
    departure_handle = fingerprint_directions_handle(direction_ids[1], 1, departure_direction)
    route = lambda leg_id, origin, destination, directions_handle, start_at, end_at: ProposedRouteLeg(
        leg_id=leg_id, origin_handle=origin, destination_handle=destination,
        start_at=start_at, end_at=end_at, transport_semantic=TransportSemantic.WALKING,
        route_mode=TravelMode.WALKING, directions_handle=directions_handle, alternative_index=0,
    )
    proposal = ProposedItineraryDraft(
        arrival_transfer=ProposedBoundaryTransfer(
            transfer_kind="arrival", hub_handle="hub-location-arrival", hotel_handle="hotel-handle",
            route_leg=route("arrival-transfer", "hub-location-arrival", "hotel-handle", arrival_handle, datetime(2026, 9, 2, 8, 30, tzinfo=shanghai), datetime(2026, 9, 2, 9, 0, tzinfo=shanghai)),
        ),
        days=(ProposedDayPlan(date=date(2026, 9, 3), day_kind=PlanningDayKind.TRANSIT_ONLY, theme="中转", open_intervals=(), visits=(), route_legs=(), backups=()),),
        departure_transfer=ProposedBoundaryTransfer(
            transfer_kind="departure", hub_handle="hub-location-departure", hotel_handle="hotel-handle",
            route_leg=route("departure-transfer", "hotel-handle", "hub-location-departure", departure_handle, datetime(2026, 9, 4, 18, 0, tzinfo=shanghai), datetime(2026, 9, 4, 18, 30, tzinfo=shanghai)),
        ),
        upstream_gap_ids=(),
    )
    submit = SubmitItineraryArgs(
        effective_policy=ProposedEffectivePlanningPolicy(effective_pace=TravelPace.NORMAL, daily_time_rules=(), time_sensitive_must=(), transport_rules=()),
        draft=proposal,
    )
    operation.responses.extend((
        ToolCallsReady(calls=calls, metadata=_metadata()),
        ToolCallsReady(calls=(direction_call,), metadata=_metadata()),
        ToolCallsReady(calls=(ValidatedToolCall(call_id="submit", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=submit),), metadata=_metadata()),
        ToolTurnCompleted(final_text="继续", metadata=_metadata()),
    ))

    first = await service.plan_initial(request)
    second = await service.plan_initial(request)

    assert isinstance(first, ItineraryPlanningIncomplete), first.model_dump(mode="json")
    assert second == first
    assert first.itinerary_ref is None
    assert first.budgets.iterations_consumed == 3
    assert operation.call_count == 4
    assert all(item.day_kind is not PlanningDayKind.TRANSIT_ONLY for item in first.gaps if hasattr(item, "day_kind"))


_R05_DURATION_CASES = {
    "duration_minutes": ("杭州候选1游览30分钟", 30, True),
    "duration_half_hour": ("杭州候选1只玩半小时", 30, True),
    "duration_negated": ("杭州候选1不要安排30分钟，要游览一小时", 30, False),
    "duration_correction": ("杭州候选1不要安排30分钟，要游览一小时", 60, True),
    "duration_chinese_minutes": ("杭州候选1停留三十分钟", 30, True),
    "duration_decimal_hours": ("杭州候选1参观0.5小时", 30, True),
    "duration_round_up": ("杭州候选1游览四十五分钟", 60, True),
    "duration_wrong_rounding": ("杭州候选1游览四十五分钟", 30, False),
    "duration_other_candidate": ("杭州候选2游览30分钟", 30, False),
    "duration_subject_switch": ("杭州候选1游览一小时，杭州候选2游览30分钟", 30, False),
    "duration_negative_only": ("杭州候选1不要安排30分钟", 30, False),
    "duration_negation_before_subject": ("不要在杭州候选1玩半小时", 30, False),
    "duration_conflict": ("杭州候选1游览30分钟，游览一小时", 30, False),
    "duration_range": ("杭州候选1游览30分钟或一小时", 30, False),
    "duration_soft": ("杭州候选1最好游览30分钟", 30, False),
    "duration_estimate_bypass": ("杭州候选1只玩半小时", 60, False),
}
# arrival raw / arrival leg start / departure raw / departure leg start / accepted
_R05_BOUNDARY_CASES = {
    "boundary_arrival_overlap": ("2026-09-03 09:00", "2026-09-03 09:30", "2026-09-04 20:00", "2026-09-04 18:30", False),
    "boundary_departure_overlap": ("2026-09-02 08:00", "2026-09-02 08:30", "2026-09-03 20:00", "2026-09-03 18:30", False),
    "boundary_arrival_touching": ("2026-09-03 08:30", "2026-09-03 09:00", "2026-09-04 20:00", "2026-09-04 18:30", True),
    "boundary_departure_touching": ("2026-09-02 08:00", "2026-09-02 08:30", "2026-09-03 20:30", "2026-09-03 19:00", True),
    "boundary_same_day_valid": ("2026-09-03 08:30", "2026-09-03 09:00", "2026-09-03 20:30", "2026-09-03 19:00", True),
    "boundary_same_day_overlap": ("2026-09-03 09:00", "2026-09-03 09:30", "2026-09-03 20:00", "2026-09-03 18:30", False),
    "boundary_midnight_arrival": ("2026-09-02 23:00", "2026-09-02 23:30", "2026-09-04 20:00", "2026-09-04 18:30", True),
    "boundary_midnight_departure": ("2026-09-02 08:00", "2026-09-02 08:30", "2026-09-04 01:00", "2026-09-03 23:30", True),
}

_R07_MEAL_CASES = {
    "r07_dinner_19": ("9月3日允许活动到22点；杭州候选3晚餐必须19:00开始", 19, True),
    "r07_dinner_20": ("9月3日允许活动到22点；杭州候选3晚餐必须20:00开始", 20, True),
}
_R08_MEAL_CASES = {
    "r08_user_dinner_20": ("9月3日允许活动到22点；杭州候选3晚餐必须20:00开始", 20, 0, 20, 0, True),
    "r08_missing_user_dinner_20": ("9月3日允许活动到22点", 20, 0, 20, 0, False),
    "r08_conflicting_user_dinner_20": ("9月3日允许活动到22点；杭州候选3晚餐必须19:00开始，不要安排20:00晚餐", 20, 0, 20, 0, False),
    "r08_user_dinner_20_15": ("9月3日允许活动到22点；杭州候选3有20:15的晚餐预约", 20, 15, 20, 0, True),
    "r08_uncovered_dinner_20_15": ("9月3日允许活动到22点；杭州候选3有20:15的晚餐预约", 20, 15, 19, 0, False),
}
_R09_MEAL_SOURCE_CASES = {
    "r09_source_ordinary_valid": ("9月3日允许活动到22点；城南饭店晚餐必须20:00开始", 20, 0, 20, 0, True),
    "r09_source_ordinary_wrong_candidate": ("9月3日允许活动到22点；城北饭店晚餐必须20:00开始", 20, 0, 20, 0, False),
    "r09_source_wrong_date": ("9月3日允许活动到22点；9月4日，杭州候选3晚餐必须20:00开始", 20, 0, 20, 0, False),
    "r09_source_wrong_meal": ("9月3日允许活动到22点；杭州候选3午餐必须20:00开始；晚餐必须19:00开始", 20, 0, 20, 0, False),
}
_R10_MEAL_SOURCE_CASES = {
    "r10_wrong_meal_pairing": ("9月3日允许活动到22点；城南饭店午餐必须20:00开始且晚餐必须19:00开始", 20, 0, 20, 0, False),
    "r10_conflicting_dinner": ("9月3日允许活动到22点；城南饭店晚餐必须20:00开始；城南饭店晚餐必须19:00开始", 20, 0, 20, 0, False),
    "r10_conflicting_dinner_reversed": ("9月3日允许活动到22点；城南饭店晚餐必须19:00开始；城南饭店晚餐必须20:00开始", 20, 0, 20, 0, False),
    "r10_unscoped_dinner": ("9月3日允许活动到22点；晚餐必须20:00开始", 20, 0, 20, 0, True),
}
_R11_MEAL_SOURCE_CASES = {
    "r11_unknown_named_dinner": ("9月3日允许活动到22点；城西饭店晚餐必须20:00开始", 20, 0, 20, 0, False),
    "r11_unknown_descriptive_restaurant": ("9月3日允许活动到22点；西湖边那家餐厅晚餐必须20:00开始", 20, 0, 20, 0, False),
}
_R12_ANCHOR_CASES = {
    "r12_fake_user_anchor": ("user_provided", None, False, 5, 30),
    "r12_fake_verified_anchor": ("verified_evidence", "evidence-handle-0001", False, 5, 30),
    "r12_confirmed_user_anchor": ("user_provided", None, True, 10, 15),
    "r12_anchor_empty_policy": ("user_provided", None, False, 10, 15),
}
_R14_POLICY_CASES = {
    "r14_f01_relaxed_delete_anchor": "relaxed_delete_anchor",
    "r14_f02_relaxed": "relaxed",
    "r14_keep_policy_delete_anchor": "keep_policy_delete_anchor",
    "r14_keep_policy_continue": "keep_policy_continue",
}
_R15_POLICY_CASES = {
    "r15_f01_same_pace_delete_anchor": "same_pace_delete_anchor",
    "r15_f01_same_pace_keep_anchor": "same_pace_keep_anchor",
    "r15_f02_supplement_evidence": "supplement_evidence",
    "r15_f02_supplement_locked_mismatch": "supplement_locked_mismatch",
}
_R16_POLICY_CASES = {
    "r16_pace_match_submit": "pace_match_submit",
    "r16_pace_conflict_submit": "pace_conflict_submit",
    "r16_pace_match_supplement": "pace_match_supplement",
    "r16_pace_conflict_supplement": "pace_conflict_supplement",
    "r16_daily_source_missing_submit": "daily_source_missing_submit",
    "r16_daily_source_missing_supplement": "daily_source_missing_supplement",
    "r16_transport_source_missing_submit": "transport_source_missing_submit",
    "r16_transport_source_missing_supplement": "transport_source_missing_supplement",
    "r16_daily_source_valid_submit": "daily_source_valid_submit",
    "r16_transport_source_valid_submit": "transport_source_valid_submit",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ("supported", "expanded_default_window", "split_role_and_hours_evidence", "runtime_supported", "route_rebind_repair", "backup_distance_repair", "duplicate_main_repair", "duplicate_backup_repair", "partial_backups", "backup_slots_order_repair", "backup_slots_duplicate_repair", "meal_intent_mismatch_feedback", "must_valid", "missing_hours", "unproven_duration", "unapproved_early_window", "prefix_3_to_1", "prefix_1_to_3", "missing_backups", "resume_metadata", "resume_coordinate", *_R05_DURATION_CASES, *_R05_BOUNDARY_CASES, *_R07_MEAL_CASES, *_R08_MEAL_CASES, *_R09_MEAL_SOURCE_CASES, *_R10_MEAL_SOURCE_CASES, *_R11_MEAL_SOURCE_CASES, *_R12_ANCHOR_CASES, *_R14_POLICY_CASES, *_R15_POLICY_CASES, *_R16_POLICY_CASES))
async def test_service_completed_requires_main_plan_and_valid_optional_backups(case: str) -> None:
    service, request, _, operation, repository = _fixture()
    if case == "must_valid":
        preferences = request.confirmed.requirement.preferences.model_copy(update={"must_visit_places": ["杭州候选1"]})
        request = request.model_copy(update={"confirmed": request.confirmed.model_copy(update={
            "requirement": request.confirmed.requirement.model_copy(update={"preferences": preferences}),
        })})
    candidate_artifact = CandidateResearchArtifact.model_validate_json(
        json.dumps(repository.contents[request.candidate_set_ref.artifact_id], ensure_ascii=False),
        strict=True,
    )
    first = candidate_artifact.record.state.candidates[0]
    candidate_names = (
        ("湖滨公园", "江南小馆", "城南饭店", "江边公园", "城北饭店", "河畔食堂")
        if case in _R09_MEAL_SOURCE_CASES and case != "r09_source_wrong_date" and case != "r09_source_wrong_meal" or case in _R10_MEAL_SOURCE_CASES or case in _R11_MEAL_SOURCE_CASES
        else tuple(f"杭州候选{index + 1}" for index in range(6))
    )
    candidates = tuple(first.model_copy(update={
        "admission_ordinal": index,
        "core": first.core.model_copy(update={
            "uid": f"candidate-{index + 1}",
            "name": candidate_names[index],
            "address": f"杭州候选路{index + 1}",
            "coordinate": first.core.coordinate.model_copy(update={
                "latitude": 30.24 + index * 0.001,
                "longitude": 120.15 + index * 0.001,
            }),
        }),
    }) for index in range(6))
    if case == "meal_intent_mismatch_feedback":
        candidates = (
            candidates[0],
            candidates[1].model_copy(update={"meal_intents": (MealIntent.DINNER,)}),
            *candidates[2:],
        )
    evidence_refs = list(candidate_artifact.record.state.observation_refs)
    if case != "missing_hours":
        import app.candidate_research.artifacts as candidate_local
        from app.candidate_research import CandidateResearchPlaceObservationArtifact, ResearchTool
        updated = []
        for index, candidate in enumerate(candidates, start=1):
            observation = CandidateResearchPlaceObservationArtifact(
                schema_version="1.0", run_id=request.operation_plan.run_id, mode=ResearchMode.INITIAL,
                invocation_slot_index=0, iteration_index=0, action_ordinal=index - 1,
                tool=ResearchTool.GET_PLACE_DETAILS, action_fingerprint="sha256:" + str(index) * 64,
                result=candidate_local.PlaceDetailsSuccessObservation(
                    record_type="details_success",
                    details=candidate_local.ArtifactPlaceDetailsSnapshot(
                        uid=candidate.core.uid, name=candidate.core.name, address=candidate.core.address,
                        coordinate=candidate.core.coordinate, provider_category="museum", brand=None,
                        telephone=None, rating=None,
                        business_hours=("00:00-23:59",)
                        if case in {"r12_fake_user_anchor", "r12_fake_verified_anchor"}
                        else ("09:00-21:00",),
                    ),
                    provenance=candidate_local.ArtifactProvenanceSnapshot(
                        source="baidu_maps_mcp", capability=MapCapability.PLACE_DETAILS,
                        retrieved_at=datetime(2026, 9, 3, tzinfo=timezone.utc), verified=True,
                    ),
                ),
            )
            _, encoded = FakeRepository._encoded(observation)
            ref = _ref(request.operation_plan.run_id, "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH, f"details_{index}", "e").model_copy(update={
                "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded),
            })
            repository.seed(ref, observation)
            evidence_refs.append(ref)
            updated.append(candidate.model_copy(update={
                "details": ArtifactCandidateDetailsStateSnapshot(status="success", observation_index=index),
                "observation_indexes": (0, index),
                "roles": tuple(
                    role.model_copy(
                        update={
                            "evidence_indexes": (0,)
                            if case == "split_role_and_hours_evidence"
                            else (0, index)
                        }
                    )
                    for role in candidate.roles
                ),
            }))
        candidates = tuple(updated)
    expanded_counts = candidate_artifact.record.state.counts.model_copy(update={
        "attraction_count": 6,
        "restaurant_count": 6,
        "unique_eligible_count": 6,
        "admitted_slots": 6,
    })
    expanded_coverage = candidate_artifact.record.state.coverage.model_copy(update={"details_target": 2, "details_success_count": 0 if case == "missing_hours" else 6})
    expanded_state = candidate_artifact.record.state.model_copy(update={
        "candidates": candidates,
        "counts": expanded_counts,
        "coverage": expanded_coverage,
        "observation_refs": tuple(evidence_refs),
    })
    if case == "must_valid":
        must_group = _expected_must_groups(request.confirmed)[0].model_copy(update={
            "evaluated_candidate_ordinals": (0,),
            "default_verdict": MatchVerdict.CONFIRMED,
            "default_reason_code": RequirementReasonCode.EXACT_NAME_MATCH,
        })
        first = candidates[0].model_copy(update={
            "must_matches": (ArtifactRequirementMatchSnapshot(
                requirement_group_id=must_group.group_id,
                verdict=MatchVerdict.CONFIRMED,
                reason_code=RequirementReasonCode.EXACT_NAME_MATCH,
                evidence_indexes=(0,),
            ),),
        })
        candidates = (first, *candidates[1:])
        expanded_state = expanded_state.model_copy(update={
            "candidates": candidates,
            "requirement_group_states": (must_group,),
            "coverage": expanded_state.coverage.model_copy(update={
                "must_item_count": 1,
                "resolved_must_item_count": 1,
            }),
        })
    expanded_record = candidate_artifact.record.model_copy(update={
        "state": expanded_state,
        "counts": expanded_counts,
        "coverage": expanded_state.coverage,
    })
    expanded_artifact = candidate_artifact.model_copy(update={"record": expanded_record})
    CandidateResearchArtifact.model_validate_json(expanded_artifact.model_dump_json(exclude_none=False), strict=True)
    _, expanded_encoded = FakeRepository._encoded(expanded_artifact)
    expanded_ref = request.candidate_set_ref.model_copy(update={
        "content_hash": "sha256:" + hashlib.sha256(expanded_encoded).hexdigest(),
        "size_bytes": len(expanded_encoded),
    })
    repository.seed(expanded_ref, expanded_artifact)

    shanghai = ZoneInfo("Asia/Shanghai")
    window = request.confirmed.requirement.time_window.model_copy(update={
        "arrival": TravelTimeWindowArrival(
            hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
            local_datetime="2026-09-02 08:00",
        ),
        "departure": TravelTimeWindowDeparture(
            hub_name="杭州东站", hub_type=HubType.RAILWAY_STATION,
            local_datetime="2026-09-04 20:00",
        ),
    })
    request = request.model_copy(update={
        "candidate_set_ref": expanded_ref,
        "confirmed": request.confirmed.model_copy(update={
            "requirement": request.confirmed.requirement.model_copy(update={"time_window": window}),
        }),
    })
    if case == "r12_confirmed_user_anchor":
        preferences = request.confirmed.requirement.preferences.model_copy(update={
            "free_text_notes": "9月3日杭州候选1活动必须10:15开始",
        })
        request = request.model_copy(update={
            "confirmed": request.confirmed.model_copy(update={
                "requirement": request.confirmed.requirement.model_copy(update={"preferences": preferences}),
            }),
        })
    if case in _R14_POLICY_CASES or case in _R15_POLICY_CASES:
        preferences = request.confirmed.requirement.preferences.model_copy(update={
            "daily_time_preferences": "9月3日允许活动到21点",
            "free_text_notes": "9月3日杭州候选1活动必须10:15开始"
            if case == "r14_keep_policy_delete_anchor" else None,
        })
        request = request.model_copy(update={
            "confirmed": request.confirmed.model_copy(update={
                "requirement": request.confirmed.requirement.model_copy(update={"preferences": preferences}),
            }),
        })
    if case in _R16_POLICY_CASES:
        preferences = request.confirmed.requirement.preferences.model_copy(update={
            "pace": TravelPace.RELAXED if case.startswith("r16_pace_") else None,
            "agent_decides_pace": not case.startswith("r16_pace_"),
            "daily_time_preferences": (
                None
                if "daily_source_missing" in case
                else "9月3日允许活动到21点"
                if "daily_source_valid" in case
                else None
            ),
            "transport_preferences": (
                None
                if "transport_source_missing" in case
                else "步行优先，单程不超过60分钟"
                if "transport_source_valid" in case
                else None
            ),
        })
        request = request.model_copy(update={
            "confirmed": request.confirmed.model_copy(update={
                "requirement": request.confirmed.requirement.model_copy(update={"preferences": preferences}),
            }),
        })
    if case in _R07_MEAL_CASES or case in _R08_MEAL_CASES or case in _R09_MEAL_SOURCE_CASES or case in _R10_MEAL_SOURCE_CASES or case in _R11_MEAL_SOURCE_CASES:
        daily_time_preferences = (
            _R07_MEAL_CASES[case][0]
            if case in _R07_MEAL_CASES
            else _R08_MEAL_CASES[case][0]
            if case in _R08_MEAL_CASES
            else _R09_MEAL_SOURCE_CASES[case][0]
            if case in _R09_MEAL_SOURCE_CASES
            else _R10_MEAL_SOURCE_CASES[case][0]
            if case in _R10_MEAL_SOURCE_CASES
            else _R11_MEAL_SOURCE_CASES[case][0]
        )
        preferences = request.confirmed.requirement.preferences.model_copy(update={
            "daily_time_preferences": daily_time_preferences,
        })
        request = request.model_copy(update={
            "confirmed": request.confirmed.model_copy(update={
                "requirement": request.confirmed.requirement.model_copy(update={"preferences": preferences}),
            }),
        })
    if case in _R05_DURATION_CASES:
        request.confirmed.requirement.preferences.free_text_notes = _R05_DURATION_CASES[case][0]
    if case in _R05_BOUNDARY_CASES:
        arrival_raw, _, departure_raw, _, _ = _R05_BOUNDARY_CASES[case]
        request.confirmed.requirement.time_window.arrival.local_datetime = arrival_raw
        request.confirmed.requirement.time_window.departure.local_datetime = departure_raw
    service._map_adapter = FakeMapAdapter(geocode_factory=lambda _: GeocodeSuccess(
        result=GeocodeCandidate(
            formatted_address="杭州东站",
            coordinate=BaiduCoordinate(latitude=30.29, longitude=120.21),
            confidence=0.98,
        ),
        provenance=map_provenance(MapCapability.GEOCODING),
    ))
    if case == "backup_distance_repair":
        def matrix_with_one_distant_backup(matrix_request):
            result = matrix_success(len(matrix_request.origins))
            return result.model_copy(update={
                "cells": tuple(
                    cell.model_copy(update={"distance_m": 4288})
                    if (cell.origin_index, cell.destination_index) == (0, 3)
                    else cell
                    for cell in result.cells
                ),
            })

        service._map_adapter.matrix_factory = matrix_with_one_distant_backup
    base = request.operation_plan
    phase = WorkflowPhase.ITINERARY_PLANNING
    hub_ids = tuple(OperationId(run_id=base.run_id, phase=phase, logical_step=f"e2e_hub_{kind}") for kind in ("arrival", "departure"))
    matrix_id = OperationId(run_id=base.run_id, phase=phase, logical_step="e2e_matrix")
    direction_ids = tuple(OperationId(run_id=base.run_id, phase=phase, logical_step=f"e2e_direction_{index}") for index in range(7 if case.startswith("prefix_") else 6))
    request = request.model_copy(update={"operation_plan": base.model_copy(update={
        "hub_geocode_operation_ids": hub_ids,
        "matrix_operation_ids": (matrix_id,),
        "direction_operation_ids": direction_ids,
    })})
    geocode_args = __import__("app.itinerary_planning.models", fromlist=["GeocodeHubArgs"])
    direction_args = __import__("app.itinerary_planning.models", fromlist=["PlanDirectionsArgs", "DirectionLegIntent"])
    matrix_args = __import__("app.itinerary_planning.models", fromlist=["CalculateRouteMatrixArgs"])
    calls = (
        ValidatedToolCall(call_id="e2e_geocode", tool_name=PlanningTool.GEOCODE_HUB.value, arguments=geocode_args.GeocodeHubArgs(endpoint_kind="arrival")),
        ValidatedToolCall(call_id="e2e_geocode_departure", tool_name=PlanningTool.GEOCODE_HUB.value, arguments=geocode_args.GeocodeHubArgs(endpoint_kind="departure")),
    )
    matrix_call = ValidatedToolCall(call_id="e2e_matrix", tool_name=PlanningTool.CALCULATE_ROUTE_MATRIX.value, arguments=matrix_args.CalculateRouteMatrixArgs(
        date=date(2026, 9, 3), mode=MatrixTravelMode.WALKING,
        location_handles=("candidate-handle-0000", "candidate-handle-0001", "candidate-handle-0002", "candidate-handle-0003", "candidate-handle-0004", "candidate-handle-0005"),
    ))
    context = fingerprint_planning_context(base.run_id, _confirmed_digest(request.confirmed), request.hotel_selection_ref, _canonical_hash("杭州东站"), _canonical_hash("杭州东站"))
    arrival_ref = HubLocationRef(endpoint_kind="arrival", geocode_observation_index=0)
    departure_ref = HubLocationRef(endpoint_kind="departure", geocode_observation_index=1)
    hotel_location = HotelLocationRef()
    locations = tuple(CandidateLocationRef(admission_ordinal=index, uid=f"candidate-{index + 1}") for index in range(6))
    coordinates = tuple(BaiduCoordinate(latitude=30.24 + index * 0.001, longitude=120.15 + index * 0.001) for index in range(6))
    route_specs = (
        ("e2e-arrival", arrival_ref, BaiduCoordinate(latitude=30.29, longitude=120.21), hotel_location, BaiduCoordinate(latitude=30.25, longitude=120.16)),
        ("e2e-hotel-attraction", hotel_location, BaiduCoordinate(latitude=30.25, longitude=120.16), locations[0], coordinates[0]),
        ("e2e-attraction-lunch", locations[0], coordinates[0], locations[1], coordinates[1]),
        ("e2e-lunch-dinner", locations[1], coordinates[1], locations[2], coordinates[2]),
        ("e2e-dinner-hotel", locations[2], coordinates[2], hotel_location, BaiduCoordinate(latitude=30.25, longitude=120.16)),
        ("e2e-departure", hotel_location, BaiduCoordinate(latitude=30.25, longitude=120.16), departure_ref, BaiduCoordinate(latitude=30.29, longitude=120.21)),
    )
    if case.startswith("prefix_"):
        route_specs += (("e2e-attraction-hotel", locations[0], coordinates[0], hotel_location, BaiduCoordinate(latitude=30.25, longitude=120.16)),)
    intents = []
    for index, (leg_id, origin, origin_coordinate, destination, destination_coordinate) in enumerate(route_specs):
        fingerprint = fingerprint_directions(context, origin, origin_coordinate, destination, destination_coordinate, TravelMode.WALKING, 1)
        intents.append(direction_args.DirectionLegIntent(
            leg_id=leg_id,
            origin_handle="hub-location-arrival" if origin == arrival_ref else "hub-location-departure" if origin == departure_ref else "hotel-handle" if origin == hotel_location else f"candidate-handle-{origin.admission_ordinal:04d}",
            destination_handle="hub-location-arrival" if destination == arrival_ref else "hub-location-departure" if destination == departure_ref else "hotel-handle" if destination == hotel_location else f"candidate-handle-{destination.admission_ordinal:04d}",
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1,
        ))
    direction_call = ValidatedToolCall(call_id="e2e_directions", tool_name=PlanningTool.PLAN_DIRECTIONS.value, arguments=direction_args.PlanDirectionsArgs(legs=tuple(intents)))

    def route(leg_id: str, origin: str, destination: str, directions_handle: str, start_at: datetime, end_at: datetime) -> ProposedRouteLeg:
        return ProposedRouteLeg(leg_id=leg_id, origin_handle=origin, destination_handle=destination, start_at=start_at, end_at=end_at, transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, directions_handle=directions_handle, alternative_index=0)

    times = (
        ("e2e-arrival", "hub-location-arrival", "hotel-handle", datetime(2026, 9, 2, 8, 30, tzinfo=shanghai), datetime(2026, 9, 2, 9, tzinfo=shanghai)),
        ("e2e-hotel-attraction", "hotel-handle", "candidate-handle-0000", datetime(2026, 9, 3, 9, 30, tzinfo=shanghai), datetime(2026, 9, 3, 10, tzinfo=shanghai)),
        ("e2e-attraction-lunch", "candidate-handle-0000", "candidate-handle-0001", datetime(2026, 9, 3, 11, tzinfo=shanghai), datetime(2026, 9, 3, 11, 30, tzinfo=shanghai)),
        ("e2e-lunch-dinner", "candidate-handle-0001", "candidate-handle-0002", datetime(2026, 9, 3, 13, tzinfo=shanghai), datetime(2026, 9, 3, 13, 30, tzinfo=shanghai)),
        ("e2e-dinner-hotel", "candidate-handle-0002", "hotel-handle", datetime(2026, 9, 3, 18, 30, tzinfo=shanghai), datetime(2026, 9, 3, 19, tzinfo=shanghai)),
        ("e2e-departure", "hotel-handle", "hub-location-departure", datetime(2026, 9, 4, 18, 30, tzinfo=shanghai), datetime(2026, 9, 4, 19, tzinfo=shanghai)),
    )
    if case.startswith("prefix_"):
        times += (("e2e-attraction-hotel", "candidate-handle-0000", "hotel-handle", datetime(2026, 9, 3, 11, tzinfo=shanghai), datetime(2026, 9, 3, 11, 30, tzinfo=shanghai)),)
    route_values = []
    for index, ((leg_id, origin, destination, start_at, end_at), (_, _, _, _, _)) in enumerate(zip(times, route_specs, strict=True)):
        if case == "runtime_supported":
            handle = f"pending:{leg_id}"
        else:
            fingerprint = fingerprint_directions(context, route_specs[index][1], route_specs[index][2], route_specs[index][3], route_specs[index][4], TravelMode.WALKING, 1)
            handle = fingerprint_directions_handle(direction_ids[index], index, fingerprint)
        route_values.append(route(leg_id, origin, destination, handle, start_at, end_at))
    visits = (
        ProposedScheduledVisit(visit_id="e2e-attraction", candidate_handle="candidate-handle-0000", visit_kind=VisitKind.ATTRACTION, start_at=datetime(2026, 9, 3, 10, tzinfo=shanghai), end_at=datetime(2026, 9, 3, 11, tzinfo=shanghai), duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(), business_hours_state=__import__("app.itinerary_planning.models", fromlist=["BusinessHoursState"]).BusinessHoursState.COMPATIBLE, business_evidence_handles=("evidence-handle-0000",)),
        ProposedScheduledVisit(visit_id="e2e-lunch", candidate_handle="candidate-handle-0001", visit_kind=VisitKind.LUNCH, start_at=datetime(2026, 9, 3, 11, 30, tzinfo=shanghai), end_at=datetime(2026, 9, 3, 12, 30, tzinfo=shanghai), duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(), business_hours_state=__import__("app.itinerary_planning.models", fromlist=["BusinessHoursState"]).BusinessHoursState.COMPATIBLE, business_evidence_handles=("evidence-handle-0000",)),
        ProposedScheduledVisit(visit_id="e2e-dinner", candidate_handle="candidate-handle-0002", visit_kind=VisitKind.DINNER, start_at=datetime(2026, 9, 3, 17, 30, tzinfo=shanghai), end_at=datetime(2026, 9, 3, 18, 30, tzinfo=shanghai), duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(), business_hours_state=__import__("app.itinerary_planning.models", fromlist=["BusinessHoursState"]).BusinessHoursState.COMPATIBLE, business_evidence_handles=("evidence-handle-0000",)),
    )
    backup_values = tuple(ProposedBackupOption(backup_kind=kind, core_visit_id=visit_id, candidate_handle=f"candidate-handle-{candidate_index:04d}", matrix_handle="matrix-handle-0000", matrix_mode=MatrixTravelMode.WALKING) for kind, visit_id, candidate_index in (("attraction", "e2e-attraction", 3), ("lunch", "e2e-lunch", 4), ("dinner", "e2e-dinner", 5)))
    if case != "missing_hours":
        visits = tuple(visit.model_copy(update={"business_evidence_handles": (f"evidence-handle-{index:04d}",)}) for index, visit in enumerate(visits, start=1))
    if case == "must_valid":
        visits = (visits[0].model_copy(update={"must_group_ids": (_expected_must_groups(request.confirmed)[0].group_id,)}), *visits[1:])
    if case == "unproven_duration":
        visits = (visits[0].model_copy(update={"end_at": datetime(2026, 9, 3, 10, 30, tzinfo=shanghai), "duration_source": "user_explicit"}), *visits[1:])
    if case in _R05_DURATION_CASES:
        visits = (visits[0].model_copy(update={
            "end_at": visits[0].start_at + timedelta(minutes=_R05_DURATION_CASES[case][1]),
            "duration_source": "planning_estimate" if case == "duration_estimate_bypass" else "user_explicit",
        }), *visits[1:])
    if case in _R05_BOUNDARY_CASES:
        for index, value in ((0, _R05_BOUNDARY_CASES[case][1]), (5, _R05_BOUNDARY_CASES[case][3])):
            start_at = datetime.fromisoformat(value).replace(tzinfo=shanghai)
            route_values[index] = route_values[index].model_copy(update={"start_at": start_at, "end_at": start_at + timedelta(minutes=30)})
    if case in {"unapproved_early_window", "r12_fake_user_anchor", "r12_fake_verified_anchor"}:
        visits = (visits[0].model_copy(update={"start_at": datetime(2026, 9, 3, 5, 30, tzinfo=shanghai), "end_at": datetime(2026, 9, 3, 6, 30, tzinfo=shanghai)}), *visits[1:])
        route_values[1] = route_values[1].model_copy(update={"start_at": datetime(2026, 9, 3, 5, tzinfo=shanghai), "end_at": datetime(2026, 9, 3, 5, 30, tzinfo=shanghai)})
    if case == "expanded_default_window":
        route_values[1] = route_values[1].model_copy(update={
            "start_at": datetime(2026, 9, 3, 7, 30, tzinfo=shanghai),
            "end_at": datetime(2026, 9, 3, 8, 0, tzinfo=shanghai),
        })
        route_values[4] = route_values[4].model_copy(update={
            "start_at": datetime(2026, 9, 3, 21, 30, tzinfo=shanghai),
            "end_at": datetime(2026, 9, 3, 22, 0, tzinfo=shanghai),
        })
    if case in _R07_MEAL_CASES:
        _, dinner_start_hour, _ = _R07_MEAL_CASES[case]
        dinner_start = datetime(2026, 9, 3, dinner_start_hour, tzinfo=shanghai)
        dinner_end = dinner_start + timedelta(hours=1)
        visits = (*visits[:2], visits[2].model_copy(update={
            "start_at": dinner_start,
            "end_at": dinner_end,
            "time_sensitive_anchor_ids": ("r07-dinner-anchor",),
        }))
        route_values[4] = route_values[4].model_copy(update={
            "start_at": dinner_end,
            "end_at": dinner_end + timedelta(minutes=30),
        })
    if case in _R08_MEAL_CASES or case in _R09_MEAL_SOURCE_CASES or case in _R10_MEAL_SOURCE_CASES or case in _R11_MEAL_SOURCE_CASES:
        _, anchor_hour, anchor_minute, dinner_start_hour, dinner_start_minute, _ = _R08_MEAL_CASES[case] if case in _R08_MEAL_CASES else _R09_MEAL_SOURCE_CASES[case] if case in _R09_MEAL_SOURCE_CASES else _R10_MEAL_SOURCE_CASES[case] if case in _R10_MEAL_SOURCE_CASES else _R11_MEAL_SOURCE_CASES[case]
        dinner_start = datetime(2026, 9, 3, dinner_start_hour, dinner_start_minute, tzinfo=shanghai)
        dinner_end = dinner_start + timedelta(hours=1)
        visits = (*visits[:2], visits[2].model_copy(update={
            "start_at": dinner_start,
            "end_at": dinner_end,
            "time_sensitive_anchor_ids": ("r08-dinner-anchor",),
        }))
        route_values[4] = route_values[4].model_copy(update={
            "start_at": dinner_end,
            "end_at": dinner_end + timedelta(minutes=30),
        })
    day_open_start = datetime(2026, 9, 3, 7, 30, tzinfo=shanghai) if case == "expanded_default_window" else datetime(2026, 9, 3, 9, tzinfo=shanghai)
    day_open_end = datetime(2026, 9, 3, 22, tzinfo=shanghai) if case == "expanded_default_window" or case in _R07_MEAL_CASES or case in _R08_MEAL_CASES or case in _R09_MEAL_SOURCE_CASES or case in _R10_MEAL_SOURCE_CASES or case in _R11_MEAL_SOURCE_CASES else datetime(2026, 9, 3, 21, tzinfo=shanghai)
    proposal = ProposedItineraryDraft(
        arrival_transfer=ProposedBoundaryTransfer(transfer_kind="arrival", hub_handle="hub-location-arrival", hotel_handle="hotel-handle", route_leg=route_values[0]),
            days=(ProposedDayPlan(date=date(2026, 9, 3), day_kind=PlanningDayKind.PARTIAL if case in _R05_BOUNDARY_CASES else PlanningDayKind.FULL, theme="杭州文化慢游", open_intervals=(TimeInterval(start_at=day_open_start, end_at=day_open_end),), visits=visits, route_legs=tuple(route_values[1:5]), backups=backup_values),),
        departure_transfer=ProposedBoundaryTransfer(transfer_kind="departure", hub_handle="hub-location-departure", hotel_handle="hotel-handle", route_leg=route_values[5]),
        upstream_gap_ids=(),
    )
    submit = SubmitItineraryArgs(effective_policy=ProposedEffectivePlanningPolicy(effective_pace=TravelPace.RELAXED, daily_time_rules=(), time_sensitive_must=(), transport_rules=()), draft=proposal)
    if case in _R12_ANCHOR_CASES:
        source, source_handle, _, anchor_hour, anchor_minute = _R12_ANCHOR_CASES[case]
        anchor = ProposedTimeSensitiveMustAnchor(
            anchor_id="review-attraction-anchor", requirement_group_id="review-attraction-anchor",
            date=date(2026, 9, 3), event_at=datetime(2026, 9, 3, anchor_hour, anchor_minute, tzinfo=shanghai),
            source=source, source_handle=source_handle,
        )
        proposal = proposal.model_copy(update={
            "days": (proposal.days[0].model_copy(update={
                "visits": (proposal.days[0].visits[0].model_copy(update={
                    "time_sensitive_anchor_ids": ("review-attraction-anchor",),
                }), *proposal.days[0].visits[1:]),
            }), *proposal.days[1:]),
        })
        submit = submit.model_copy(update={
            "effective_policy": (
                submit.effective_policy
                if case == "r12_anchor_empty_policy"
                else submit.effective_policy.model_copy(update={"time_sensitive_must": (anchor,)})
            ),
            "draft": proposal,
        })
    if case in _R14_POLICY_CASES or case in _R15_POLICY_CASES:
        daily_rule = ProposedDailyTimeRule(
            rule_id="r14-daily-window", date=date(2026, 9, 3),
            start_at=datetime(2026, 9, 3, 9, tzinfo=shanghai),
            end_at=datetime(2026, 9, 3, 21, tzinfo=shanghai),
            strength="hard", source_field="preferences.daily_time_preferences",
        )
        anchor = None
        if case in {"r14_f01_relaxed_delete_anchor", "r15_f01_same_pace_delete_anchor", "r15_f01_same_pace_keep_anchor", "r15_f02_supplement_evidence"}:
            anchor = ProposedTimeSensitiveMustAnchor(
                anchor_id="r14-lock-anchor", requirement_group_id="r14-lock-anchor",
                date=date(2026, 9, 3), event_at=datetime(2026, 9, 3, 10, 15, tzinfo=shanghai),
                source="verified_evidence", source_handle="evidence-handle-0001",
            )
        elif case == "r14_keep_policy_delete_anchor":
            anchor = ProposedTimeSensitiveMustAnchor(
                anchor_id="r14-lock-anchor", requirement_group_id="r14-lock-anchor",
                date=date(2026, 9, 3), event_at=datetime(2026, 9, 3, 10, 15, tzinfo=shanghai),
                source="user_provided", source_handle=None,
            )
        first_proposal = proposal
        if anchor is not None:
            first_proposal = proposal.model_copy(update={
                "days": (proposal.days[0].model_copy(update={
                    "visits": (proposal.days[0].visits[0].model_copy(update={
                        "time_sensitive_anchor_ids": ("r14-lock-anchor",),
                    }), *proposal.days[0].visits[1:]),
                }), *proposal.days[1:]),
            })
        first_policy = ProposedEffectivePlanningPolicy(
            effective_pace=(
                TravelPace.RELAXED
                if case in {"r15_f01_same_pace_delete_anchor", "r15_f01_same_pace_keep_anchor", "r15_f02_supplement_evidence"}
                else TravelPace.NORMAL
            ),
            daily_time_rules=(daily_rule,),
            time_sensitive_must=() if anchor is None else (anchor,),
            transport_rules=(),
        )
        if case == "r15_f02_supplement_locked_mismatch":
            first_proposal = proposal.model_copy(update={
                "days": (proposal.days[0].model_copy(update={"backups": (), "day_kind": PlanningDayKind.PARTIAL}),),
            })
        first_submit = SubmitItineraryArgs(effective_policy=first_policy, draft=first_proposal)
        second_proposal = proposal.model_copy(update={
            "days": (proposal.days[0].model_copy(update={
                "visits": (
                    tuple(item.model_copy(update={"time_sensitive_anchor_ids": ()}) for item in proposal.days[0].visits)
                    if case != "r15_f01_same_pace_keep_anchor"
                    else proposal.days[0].visits
                ),
            }), *proposal.days[1:]),
        })
        second_pace = TravelPace.NORMAL if case == "r14_keep_policy_delete_anchor" else TravelPace.RELAXED
        second_submit = SubmitItineraryArgs(
            effective_policy=ProposedEffectivePlanningPolicy(
                effective_pace=second_pace, daily_time_rules=(daily_rule,),
                time_sensitive_must=(anchor,) if case == "r15_f01_same_pace_keep_anchor" else (), transport_rules=(),
            ),
            draft=second_proposal,
        )
        submit = first_submit
    if case in _R16_POLICY_CASES:
        daily_rule = ProposedDailyTimeRule(
            rule_id="r16-daily-window", date=date(2026, 9, 3),
            start_at=datetime(2026, 9, 3, 9, tzinfo=shanghai),
            end_at=datetime(2026, 9, 3, 21, tzinfo=shanghai),
            strength="hard", source_field="preferences.daily_time_preferences",
        )
        transport_rule = ProposedTransportPolicyRule(
            rule_id="r16-transport", semantic=TransportSemantic.WALKING,
            strength="hard", numeric_limit_minutes=60,
            source_field="preferences.transport_preferences",
        )
        r16_policy = ProposedEffectivePlanningPolicy(
            effective_pace=(
                TravelPace.NORMAL
                if case in {"r16_pace_conflict_submit", "r16_pace_conflict_supplement"}
                else TravelPace.RELAXED
            ),
            daily_time_rules=(daily_rule,) if "daily_source_" in case else (),
            time_sensitive_must=(),
            transport_rules=(transport_rule,) if "transport_source_" in case else (),
        )
        submit = SubmitItineraryArgs(effective_policy=r16_policy, draft=proposal)
    if case in _R07_MEAL_CASES:
        _, dinner_start_hour, _ = _R07_MEAL_CASES[case]
        submit = submit.model_copy(update={
            "effective_policy": submit.effective_policy.model_copy(update={
                "daily_time_rules": (ProposedDailyTimeRule(
                    rule_id="r07-daily-window", date=date(2026, 9, 3),
                    start_at=datetime(2026, 9, 3, 9, tzinfo=shanghai),
                    end_at=datetime(2026, 9, 3, 22, tzinfo=shanghai),
                    strength="hard", source_field="preferences.daily_time_preferences",
                ),),
                "time_sensitive_must": (ProposedTimeSensitiveMustAnchor(
                    anchor_id="r07-dinner-anchor", requirement_group_id="r07-dinner-anchor",
                    date=date(2026, 9, 3), event_at=datetime(2026, 9, 3, dinner_start_hour, tzinfo=shanghai),
                    source="user_provided", source_handle=None,
                ),),
            }),
        })
    if case in _R08_MEAL_CASES or case in _R09_MEAL_SOURCE_CASES or case in _R10_MEAL_SOURCE_CASES or case in _R11_MEAL_SOURCE_CASES:
        _, anchor_hour, anchor_minute, _, _, _ = _R08_MEAL_CASES[case] if case in _R08_MEAL_CASES else _R09_MEAL_SOURCE_CASES[case] if case in _R09_MEAL_SOURCE_CASES else _R10_MEAL_SOURCE_CASES[case] if case in _R10_MEAL_SOURCE_CASES else _R11_MEAL_SOURCE_CASES[case]
        submit = submit.model_copy(update={
            "effective_policy": submit.effective_policy.model_copy(update={
                "daily_time_rules": (ProposedDailyTimeRule(
                    rule_id="r08-daily-window", date=date(2026, 9, 3),
                    start_at=datetime(2026, 9, 3, 9, tzinfo=shanghai),
                    end_at=datetime(2026, 9, 3, 22, tzinfo=shanghai),
                    strength="hard", source_field="preferences.daily_time_preferences",
                ),),
                "time_sensitive_must": (ProposedTimeSensitiveMustAnchor(
                    anchor_id="r08-dinner-anchor", requirement_group_id="r08-dinner-anchor",
                    date=date(2026, 9, 3), event_at=datetime(2026, 9, 3, anchor_hour, anchor_minute, tzinfo=shanghai),
                    source="user_provided", source_handle=None,
                ),),
            }),
        })
    if case in {"unapproved_early_window", "r12_fake_user_anchor", "r12_fake_verified_anchor"}:
        early_day = proposal.days[0].model_copy(update={"open_intervals": (TimeInterval(start_at=datetime(2026, 9, 3, 5, tzinfo=shanghai), end_at=datetime(2026, 9, 3, 21, tzinfo=shanghai)),)})
        submit = submit.model_copy(update={"draft": proposal.model_copy(update={"days": (early_day,)})})
    if case.startswith(("prefix_", "resume_")) or case == "missing_backups":
        three = submit.model_copy(update={"draft": proposal.model_copy(update={"days": (proposal.days[0].model_copy(update={"backups": ()}),)})})
        if case.startswith(("prefix_", "resume_")):
            # Preserve the prefix-strength regression with a real main-plan
            # defect: this full day is incorrectly submitted as partial.
            three = three.model_copy(update={"draft": three.draft.model_copy(update={
                "days": (three.draft.days[0].model_copy(update={"day_kind": PlanningDayKind.PARTIAL}),),
            })})
        submit = three
    if case == "partial_backups":
        submit = submit.model_copy(update={"draft": proposal.model_copy(update={
            "days": (proposal.days[0].model_copy(update={"backups": proposal.days[0].backups[1:2]}),),
        })})
    submit_responses = [ToolCallsReady(calls=(ValidatedToolCall(call_id="e2e_submit", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=submit),), metadata=_metadata())]
    if case.startswith("backup_slots_"):
        original_backups = proposal.days[0].backups
        bad_backups = {
            "backup_slots_order_repair": tuple(reversed(original_backups)),
            "backup_slots_duplicate_repair": (
                *original_backups[:2], original_backups[2].model_copy(update={"backup_kind": "lunch"}),
            ),
        }[case]
        bad_submit = submit.model_copy(update={"draft": proposal.model_copy(update={
            "days": (proposal.days[0].model_copy(update={"backups": bad_backups}),),
        })})
        submit_responses = [
            ToolCallsReady(calls=(ValidatedToolCall(call_id="bad_backup_slots", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=bad_submit),), metadata=_metadata()),
            ToolCallsReady(calls=(ValidatedToolCall(call_id="corrected_backup_slots", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=submit),), metadata=_metadata()),
        ]
    if case == "route_rebind_repair":
        mismatched_arrival = proposal.arrival_transfer.model_copy(update={
            "route_leg": proposal.arrival_transfer.route_leg.model_copy(update={"leg_id": "regenerated-arrival"}),
        })
        mismatched_submit = submit.model_copy(update={
            "draft": proposal.model_copy(update={"arrival_transfer": mismatched_arrival}),
        })
        submit_responses = [
            ToolCallsReady(calls=(ValidatedToolCall(call_id="bad_route_binding", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=mismatched_submit),), metadata=_metadata()),
            ToolCallsReady(calls=(ValidatedToolCall(call_id="corrected_route_binding", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=submit),), metadata=_metadata()),
        ]
    if case == "backup_distance_repair":
        original_backups = proposal.days[0].backups
        repaired_backups = (
            original_backups[0].model_copy(update={"candidate_handle": "candidate-handle-0005"}),
            original_backups[1],
            original_backups[2].model_copy(update={"candidate_handle": "candidate-handle-0003"}),
        )
        repaired_submit = submit.model_copy(update={
            "draft": proposal.model_copy(update={
                "days": (proposal.days[0].model_copy(update={"backups": repaired_backups}),),
            }),
        })
        submit_responses = [
            ToolCallsReady(calls=(ValidatedToolCall(call_id="distant_backup", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=submit),), metadata=_metadata()),
            ToolCallsReady(calls=(ValidatedToolCall(call_id="repaired_backup", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=repaired_submit),), metadata=_metadata()),
        ]
    if case in {"duplicate_main_repair", "duplicate_backup_repair"}:
        if case == "duplicate_main_repair":
            bad_day = proposal.days[0].model_copy(update={
                "visits": (
                    *proposal.days[0].visits[:-1],
                    proposal.days[0].visits[-1].model_copy(
                        update={"candidate_handle": proposal.days[0].visits[-2].candidate_handle}
                    ),
                ),
            })
        else:
            bad_day = proposal.days[0].model_copy(update={
                "backups": (
                    *proposal.days[0].backups[:-1],
                    proposal.days[0].backups[-1].model_copy(
                        update={"candidate_handle": proposal.days[0].backups[-2].candidate_handle}
                    ),
                ),
            })
        duplicate_submit = submit.model_copy(update={
            "draft": proposal.model_copy(update={"days": (bad_day,)}),
        })
        submit_responses = [
            ToolCallsReady(calls=(ValidatedToolCall(call_id="duplicate_candidate", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=duplicate_submit),), metadata=_metadata()),
            ToolCallsReady(calls=(ValidatedToolCall(call_id="distinct_candidates", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=submit),), metadata=_metadata()),
        ]
    if case in _R14_POLICY_CASES or case in _R15_POLICY_CASES:
        submit_responses = [ToolCallsReady(calls=(ValidatedToolCall(call_id="r14_first_submit", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=first_submit),), metadata=_metadata())]
        if case in {"r15_f02_supplement_evidence", "r15_f02_supplement_locked_mismatch"}:
            supplement_policy = first_submit.effective_policy if case == "r15_f02_supplement_evidence" else ProposedEffectivePlanningPolicy(
                effective_pace=TravelPace.RELAXED,
                daily_time_rules=(daily_rule,), time_sensitive_must=(), transport_rules=(),
            )
            supplement = RequestCandidateSupplementArgs(
                effective_policy=supplement_policy,
                draft=proposal if case == "r15_f02_supplement_evidence" else None,
                proposed_gaps=(ProposedSupplementGap(
                    source_gap_type="candidate_supply", date=None,
                    target=ProposedCandidateCountTarget(role=ResearchRole.ATTRACTION, current_count=1, target_count=2),
                    evidence_handles=("evidence-handle-0000",),
                ),),
            )
            submit_responses = [
                ToolCallsReady(calls=(ValidatedToolCall(call_id="r15_first_submit", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=first_submit),), metadata=_metadata())
            ] if case == "r15_f02_supplement_locked_mismatch" else []
            submit_responses.append(ToolCallsReady(calls=(ValidatedToolCall(call_id="r15_supplement", tool_name=PlanningTool.REQUEST_CANDIDATE_SUPPLEMENT.value, arguments=supplement),), metadata=_metadata()))
        elif case in {"r15_f01_same_pace_delete_anchor", "r15_f01_same_pace_keep_anchor"}:
            submit_responses.append(ToolCallsReady(calls=(ValidatedToolCall(call_id="r15_second_submit", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=second_submit),), metadata=_metadata()))
        elif case != "r14_keep_policy_continue":
            submit_responses.append(ToolCallsReady(calls=(ValidatedToolCall(call_id="r14_second_submit", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=second_submit),), metadata=_metadata()))
    if case in _R16_POLICY_CASES:
        if case.endswith("_supplement"):
            supplement = RequestCandidateSupplementArgs(
                effective_policy=submit.effective_policy,
                draft=None,
                proposed_gaps=(ProposedSupplementGap(
                    source_gap_type="candidate_supply", date=None,
                    target=ProposedCandidateCountTarget(role=ResearchRole.ATTRACTION, current_count=1, target_count=2),
                    evidence_handles=("evidence-handle-0000",),
                ),),
            )
            submit_responses = [ToolCallsReady(calls=(ValidatedToolCall(call_id="r16_supplement", tool_name=PlanningTool.REQUEST_CANDIDATE_SUPPLEMENT.value, arguments=supplement),), metadata=_metadata())]
        elif case == "r16_pace_conflict_submit":
            corrected = submit.model_copy(update={
                "effective_policy": submit.effective_policy.model_copy(update={"effective_pace": TravelPace.RELAXED}),
            })
            submit_responses = [
                ToolCallsReady(calls=(ValidatedToolCall(call_id="r16_bad_pace", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=submit),), metadata=_metadata()),
                ToolCallsReady(calls=(ValidatedToolCall(call_id="r16_corrected_pace", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=corrected),), metadata=_metadata()),
            ]
        elif case.endswith("_missing_submit"):
            submit_responses = [ToolCallsReady(calls=(ValidatedToolCall(call_id="r16_missing_source", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=submit),), metadata=_metadata())]
    terminal_response = () if case in {"r15_f02_supplement_evidence", "r15_f02_supplement_locked_mismatch"} or (case in _R16_POLICY_CASES and case.endswith("_supplement")) else (ToolTurnCompleted(final_text="结束", metadata=_metadata()),)
    operation.responses.extend((
        ToolCallsReady(calls=calls, metadata=_metadata()),
        ToolCallsReady(calls=(matrix_call,), metadata=_metadata()),
        ToolCallsReady(calls=(direction_call,), metadata=_metadata()),
        *submit_responses,
        *terminal_response,
    ))
    if case.startswith("prefix_"):
        one_day = proposal.days[0].model_copy(update={"visits": visits[:1], "backups": (),
            "route_legs": (route_values[1], route_values[6]),
            "open_intervals": (TimeInterval(start_at=datetime(2026, 9, 3, 9, tzinfo=shanghai), end_at=datetime(2026, 9, 3, 11, 30, tzinfo=shanghai)),)})
        one = three.model_copy(update={"draft": proposal.model_copy(update={"days": (one_day,)})})
        ordered = (three, one) if case == "prefix_3_to_1" else (one, three)
        operation.responses[-2:] = [*(ToolCallsReady(calls=(ValidatedToolCall(call_id=f"prefix_{index}", tool_name=PlanningTool.SUBMIT_ITINERARY.value, arguments=value),), metadata=_metadata()) for index, value in enumerate(ordered)), ToolTurnCompleted(final_text="结束", metadata=_metadata())]

    if case == "runtime_supported":
        from app.llm_runtime import DeepSeekRuntime
        from app.llm_runtime.protocols import ProviderResponse
        from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper

        class VisibleHandleProvider(ScriptedProviderAdapter):
            def __init__(self):
                super().__init__([])
                self.bindings = {}
                self.steps = list(operation.responses)

            def next_response(self):
                self.call_count += 1
                step = self.steps.pop(0)
                def replace(value):
                    if isinstance(value, dict):
                        return {key: replace(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [replace(item) for item in value]
                    if isinstance(value, str) and (value.startswith(("pending:", "candidate-handle-", "evidence-handle-", "hub-location-", "matrix-handle-")) or value == "hotel-handle"):
                        return self.bindings[value]
                    return value
                calls = tuple((call.call_id, call.tool_name, replace(call.arguments.model_dump(mode="json"))) for call in step.calls)
                return ProviderResponse(content=None, tool_calls=calls, finish_reason="tool_calls", model_name="deepseek-v4-flash",
                    usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "reasoning_tokens": 0, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1}, reasoning_content=None)

            async def ainvoke_tools(self, value):
                context = json.loads(value.user.split("BEGIN_TYPED_PLANNING_DATA\n")[1].split("\nEND_TYPED_PLANNING_DATA")[0])
                for kind, prefix in (("candidate", "candidate-handle-"), ("candidate_evidence", "evidence-handle-")):
                    for index, entry in enumerate(item for item in context["handles"] if item["kind"] == kind):
                        self.bindings[f"{prefix}{index:04d}"] = entry["handle"]
                self.bindings["hotel-handle"] = next(item["handle"] for item in context["handles"] if item["kind"] == "hotel")
                return self.next_response()

            async def ainvoke_tool_continuation(self, value):
                for message in (item for item in value.messages if item["role"] == "tool"):
                    result = json.loads(message["content"])["tool_result"]
                    observation = result["observation"]
                    if result["tool_name"] == "geocode_hub":
                        self.bindings["hub-location-" + observation["endpoint_kind"]] = observation["issued_handles"][0]
                    elif result["tool_name"] == "calculate_route_matrix":
                        self.bindings["matrix-handle-0000"] = observation["issued_handles"][0]
                    elif result["tool_name"] == "plan_directions":
                        for leg in observation["observations"]:
                            self.bindings["pending:" + leg["leg_id"]] = leg["directions_handle"]
                return self.next_response()

        provider = VisibleHandleProvider()
        service._runtime = DeepSeekRuntime(provider=provider, sleeper=RecordingSleeper())

    if case.startswith("resume_"):
        from app.itinerary_planning import ItineraryPlanningCandidateSupplementRequested, ItineraryPlanningResumeRequest, ItineraryPlanningArtifact
        from app.candidate_research import CandidateResearchSupplementalRequest, CandidateCountShortfallGap
        from tests.unit.itinerary_planning.test_service_resume import _supplemental_plan
        # The supplemental successor must retain the actual confirmed context.
        expanded_artifact = expanded_artifact.model_copy(update={"record": expanded_artifact.record.model_copy(update={
            "state": expanded_artifact.record.state.model_copy(update={"confirmed_requirement_digest": _confirmed_digest(request.confirmed)})})})
        _, encoded = FakeRepository._encoded(expanded_artifact)
        expanded_ref = expanded_ref.model_copy(update={"content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)})
        repository.seed(expanded_ref, expanded_artifact)
        request = request.model_copy(update={"candidate_set_ref": expanded_ref})
        supplement = RequestCandidateSupplementArgs(effective_policy=submit.effective_policy, draft=submit.draft,
            proposed_gaps=(ProposedSupplementGap(source_gap_type="candidate_supply", date=None,
                target=ProposedCandidateCountTarget(role=ResearchRole.ATTRACTION, current_count=6, target_count=7),
                evidence_handles=("evidence-handle-0001",)),))
        operation.responses[-1] = ToolCallsReady(calls=(ValidatedToolCall(call_id="preserve-prefix", tool_name="request_candidate_supplement", arguments=supplement),), metadata=_metadata())
        awaiting = await service.plan_initial(request)
        assert isinstance(awaiting, ItineraryPlanningCandidateSupplementRequested), awaiting.model_dump(mode="json")
        run = service._runs[base.run_id]
        before = run.draft
        assert before is not None and len(before.days[0].visits) == 3
        saved = (await repository.get(awaiting.planning_ref, expected_model=ItineraryPlanningArtifact)).content.record.state
        assert saved.draft is not None and len(saved.draft.days[0].visits) == 3
        cache = run.map_executor.cache
        cached_routes = tuple(cache.get("directions", fingerprint_directions(context, origin, origin_coordinate, destination, destination_coordinate, TravelMode.WALKING, 1), context_digest=context)
            for _, origin, origin_coordinate, destination, destination_coordinate in route_specs)
        assert all(item is not None for item in cached_routes)
        actual_plan = _supplemental_plan(base.run_id)
        original = CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=expanded_ref,
            gaps=tuple(CandidateCountShortfallGap(gap_id=gap.gap_id, evidence_refs=gap.evidence_refs,
                role=gap.target.role, current_count=gap.target.current_count, target_count=gap.target.target_count) for gap in awaiting.gaps), operation_plan=actual_plan)
        original_bytes = json.dumps(original.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        old = expanded_artifact.record.state.candidates[0]
        new_core = old.core.model_copy(update={"name": old.core.name + "更新"} if case == "resume_metadata" else {
            "coordinate": old.core.coordinate.model_copy(update={"latitude": 30.28})})
        updated_state = expanded_artifact.record.state.model_copy(update={"candidates": (old.model_copy(update={"core": new_core}), *expanded_artifact.record.state.candidates[1:])})
        successor = expanded_artifact.model_copy(update={"mode": ResearchMode.SUPPLEMENTAL, "predecessor_ref": expanded_ref,
            "invocation_request_digest": "sha256:" + hashlib.sha256(original_bytes).hexdigest(),
            "record": expanded_artifact.record.model_copy(update={"state": updated_state})})
        successor = CandidateResearchArtifact.model_validate_json(successor.model_dump_json(), strict=True)
        _, encoded = FakeRepository._encoded(successor)
        successor_ref = expanded_ref.model_copy(update={"artifact_id": uuid4(), "operation_id": actual_plan.terminal_successor_operation_id,
            "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)})
        repository.seed(successor_ref, successor)
        resume_plan = _plan(base.run_id, suffix="preserve_resume")
        resume_plan = resume_plan.model_copy(update={"iteration_slots": resume_plan.iteration_slots[run.iterations:]})
        resume = ItineraryPlanningResumeRequest(predecessor_planning_ref=awaiting.planning_ref,
            supplemented_candidate_set_ref=successor_ref, supplemental_operation_plan=actual_plan, operation_plan=resume_plan)
        operation.responses.append(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
        resumed = await service.plan_resume(resume)
        assert isinstance(resumed, ItineraryPlanningIncomplete), resumed.model_dump(mode="json")
        assert run.map_executor.cache is cache
        assert run.iterations == saved.budgets.iterations_consumed
        assert operation.call_count == 6
        assert len(service._map_adapter.directions_calls) == 6
        for spec, observation in zip(route_specs, cached_routes, strict=True):
            _, origin, origin_coordinate, destination, destination_coordinate = spec
            assert cache.get("directions", fingerprint_directions(context, origin, origin_coordinate, destination, destination_coordinate, TravelMode.WALKING, 1), context_digest=context) == observation
        if case == "resume_metadata":
            assert run.draft is not None and run.draft.days == before.days
        else:
            assert run.draft is None
            changed = fingerprint_directions(context, hotel_location, BaiduCoordinate(latitude=30.25, longitude=120.16), locations[0], BaiduCoordinate(latitude=30.28, longitude=120.15), TravelMode.WALKING, 1)
            assert cache.get("directions", changed, context_digest=context) is None
        terminal_ref = repository.refs[resume_plan.terminal_successor_operation_id]
        terminal = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
        assert (terminal.draft is None) == (case == "resume_coordinate")
        return

    result = await service.plan_initial(request)
    replay = None if case == "r12_fake_verified_anchor" or case in _R14_POLICY_CASES or case in _R15_POLICY_CASES else await service.plan_initial(request)

    if case in {"missing_backups", "partial_backups"}:
        assert isinstance(result, ItineraryPlanningCompleted)
        assert replay == result
        assert result.summary.backups == (0 if case == "missing_backups" else 1)
        assert result.budgets.iterations_consumed == 3
        assert operation.call_count == 4
        assert len(service._map_adapter.directions_calls) == 6
        assert service._runs[base.run_id].pending_gaps == []
        return

    deferred_policy_case = (
        (case in _R05_DURATION_CASES and case != "duration_estimate_bypass")
        or case in _R07_MEAL_CASES
        or case in _R08_MEAL_CASES
        or case in _R09_MEAL_SOURCE_CASES
        or case in _R10_MEAL_SOURCE_CASES
        or case in _R11_MEAL_SOURCE_CASES
        or case in _R12_ANCHOR_CASES
        or case in _R14_POLICY_CASES
        or case in _R15_POLICY_CASES
        or "daily_source_" in case
        or "transport_source_" in case
    )
    if deferred_policy_case:
        from app.itinerary_planning import ItineraryPlanningArtifact
        if replay is None:
            replay = await service.plan_initial(request)
        assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
        assert replay == result
        assert result.itinerary_ref is None
        terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
        persisted = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
        assert persisted.record_type == "incomplete"
        assert persisted.draft is None
        assert all(
            write.content["record"]["record_type"] != "completed"
            for write in repository.put_requests
            if write.artifact_type == "itinerary_planning.itinerary"
        )
        return

    if case in _R05_DURATION_CASES or case in _R05_BOUNDARY_CASES:
        from app.itinerary_planning import ItineraryPlanningArtifact
        expected = True if case == "duration_estimate_bypass" else (_R05_DURATION_CASES[case] if case in _R05_DURATION_CASES else _R05_BOUNDARY_CASES[case])[-1]
        assert isinstance(result, ItineraryPlanningCompleted if expected else ItineraryPlanningIncomplete), result.model_dump(mode="json")
        assert replay == result
        assert operation.call_count == (4 if expected else 5)
        assert result.budgets.iterations_consumed == (3 if expected else 4)
        assert result.budgets.direction_calls_consumed == 6
        assert len(service._map_adapter.directions_calls) == 6
        terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
        persisted = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
        assert persisted.record_type == result.outcome_type
        if expected:
            assert result.itinerary_ref == terminal_ref
            saved = persisted.draft
            assert len(saved.days) == 1 and len(saved.days[0].visits) == 3
            assert len(saved.days[0].backups) == 3 and len(saved.days[0].route_legs) == 4
            assert saved.arrival_transfer.route_leg.interval.end_at_utc <= saved.days[0].route_legs[0].interval.start_at_utc
            assert saved.days[0].route_legs[-1].interval.end_at_utc <= saved.departure_transfer.route_leg.interval.start_at_utc
            if case in _R05_DURATION_CASES:
                interval = saved.days[0].visits[0].interval
                assert (interval.end_at_utc - interval.start_at_utc).total_seconds() == _R05_DURATION_CASES[case][1] * 60
                assert saved.days[0].visits[0].duration_source == ("planning_estimate" if case == "duration_estimate_bypass" else "user_explicit")
        else:
            assert result.itinerary_ref is None and persisted.draft is None
            for write in repository.put_requests:
                if write.artifact_type == "itinerary_planning.itinerary":
                    assert write.content["record"]["record_type"] != "completed"
        return

    if case in _R07_MEAL_CASES:
        from app.itinerary_planning import ItineraryPlanningArtifact
        _, dinner_start_hour, expected_completed = _R07_MEAL_CASES[case]
        assert isinstance(result, ItineraryPlanningCompleted if expected_completed else ItineraryPlanningIncomplete), result.model_dump(mode="json")
        assert replay == result
        assert operation.call_count == (4 if expected_completed else 5)
        terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
        persisted = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
        assert persisted.record_type == result.outcome_type
        if expected_completed:
            assert result.itinerary_ref == terminal_ref
            assert persisted.draft is not None
            dinner = next(item for item in persisted.draft.days[0].visits if item.visit_kind is VisitKind.DINNER)
            assert dinner.interval.start_at_utc.astimezone(ZoneInfo("Asia/Shanghai")).hour == dinner_start_hour
            assert dinner.time_sensitive_anchor_ids == ("r07-dinner-anchor",)
            assert persisted.draft.effective_policy.time_sensitive_must[0].event_at_utc.astimezone(ZoneInfo("Asia/Shanghai")).hour == dinner_start_hour
        else:
            assert result.itinerary_ref is None
            assert persisted.draft is None
        return

    if case in _R08_MEAL_CASES or case in _R09_MEAL_SOURCE_CASES or case in _R10_MEAL_SOURCE_CASES or case in _R11_MEAL_SOURCE_CASES:
        from app.itinerary_planning import ItineraryPlanningArtifact
        _, anchor_hour, anchor_minute, _, _, expected_completed = _R08_MEAL_CASES[case] if case in _R08_MEAL_CASES else _R09_MEAL_SOURCE_CASES[case] if case in _R09_MEAL_SOURCE_CASES else _R10_MEAL_SOURCE_CASES[case] if case in _R10_MEAL_SOURCE_CASES else _R11_MEAL_SOURCE_CASES[case]
        assert isinstance(result, ItineraryPlanningCompleted if expected_completed else ItineraryPlanningIncomplete), result.model_dump(mode="json")
        assert replay == result
        assert operation.call_count == (4 if expected_completed else 5)
        terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
        persisted = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
        assert persisted.record_type == result.outcome_type
        if expected_completed:
            assert result.itinerary_ref == terminal_ref
            assert persisted.draft is not None
            dinner = next(item for item in persisted.draft.days[0].visits if item.visit_kind is VisitKind.DINNER)
            local_start = dinner.interval.start_at_utc.astimezone(ZoneInfo("Asia/Shanghai"))
            assert (local_start.hour, local_start.minute) == (20, 0)
            assert dinner.time_sensitive_anchor_ids == ("r08-dinner-anchor",)
            local_anchor = persisted.draft.effective_policy.time_sensitive_must[0].event_at_utc.astimezone(ZoneInfo("Asia/Shanghai"))
            assert (local_anchor.hour, local_anchor.minute) == (anchor_hour, anchor_minute)
        else:
            assert result.itinerary_ref is None
            assert result.stop_reasons == (PlanningStopReason.NO_TERMINAL_SUBMISSION,)
            assert any(gap.gap_type == "planning_process" for gap in result.gaps)
            assert persisted.draft is None
        return

    if case in _R12_ANCHOR_CASES:
        from app.itinerary_planning import ItineraryPlanningArtifact
        _, _, expected_completed, anchor_hour, anchor_minute = _R12_ANCHOR_CASES[case]
        if case == "r12_fake_verified_anchor":
            assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
            assert result.itinerary_ref is None
            assert result.stop_reasons == (PlanningStopReason.NO_TERMINAL_SUBMISSION,)
            assert result.budgets.iterations_consumed == 4
            assert result.budgets.semantic_invocations_consumed == 5
            assert result.budgets.direction_calls_consumed == 6
            assert operation.call_count == 5
            assert len(operation.continuations) == 4
            feedback = json.loads(operation.continuations[-1][0].content)
            assert feedback["tool_result"] == {"status": "rejected", "code": "policy_rejected"}
            terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
            persisted = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
            assert persisted.record_type == "incomplete"
            assert persisted.draft is None
            before_counts = (operation.call_count, len(service._map_adapter.directions_calls), len(repository.put_requests))
            replay = await service.plan_initial(request)
            assert replay == result
            assert (operation.call_count, len(service._map_adapter.directions_calls), len(repository.put_requests)) == before_counts
            return
        assert replay == result
        assert operation.call_count == (4 if expected_completed else 5)
        terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
        persisted = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
        assert persisted.record_type == result.outcome_type
        if expected_completed:
            assert isinstance(result, ItineraryPlanningCompleted)
            assert result.itinerary_ref == terminal_ref
            assert persisted.draft is not None
            attraction = next(item for item in persisted.draft.days[0].visits if item.visit_id == "e2e-attraction")
            assert attraction.location.uid == "candidate-1"
            assert attraction.visit_kind is VisitKind.ATTRACTION
            assert attraction.time_sensitive_anchor_ids == ("review-attraction-anchor",)
            assert attraction.interval.start_at_utc.astimezone(ZoneInfo("Asia/Shanghai")).hour == 10
            persisted_anchor = next(
                anchor for anchor in persisted.draft.effective_policy.time_sensitive_must
                if anchor.anchor_id == "review-attraction-anchor"
            )
            local_anchor = persisted_anchor.event_at_utc.astimezone(ZoneInfo("Asia/Shanghai"))
            assert (local_anchor.hour, local_anchor.minute) == (anchor_hour, anchor_minute)
            assert persisted_anchor.source == "user_provided"
            assert persisted_anchor.source_ref_index is None
        else:
            assert isinstance(result, ItineraryPlanningIncomplete)
            assert result.itinerary_ref is None
            assert result.stop_reasons == (PlanningStopReason.NO_TERMINAL_SUBMISSION,)
            assert persisted.draft is None
        return

    if case in _R14_POLICY_CASES:
        from app.itinerary_planning import ItineraryPlanningArtifact
        assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
        expected_stop = PlanningStopReason.NO_PROGRESS if case == "r14_f01_relaxed_delete_anchor" else PlanningStopReason.NO_TERMINAL_SUBMISSION
        assert expected_stop in result.stop_reasons
        terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
        persisted = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
        assert persisted.record_type == "incomplete"
        assert all(
            write.content["record"]["record_type"] != "completed"
            for write in repository.put_requests
            if write.artifact_type == "itinerary_planning.itinerary"
        )
        states = [
            write.content["record"]["state"]
            for write in repository.put_requests
            if write.artifact_type == "itinerary_planning.itinerary"
            and write.content["record"]["record_type"] == "in_progress"
        ]
        expected_states = 1 if case == "r14_keep_policy_continue" else 2
        locked_states = [item for item in states if item["effective_policy"] is not None]
        assert len(locked_states) >= expected_states
        first_policy = locked_states[0]["effective_policy"]
        assert first_policy["effective_pace"] == TravelPace.NORMAL.value
        assert len(first_policy["daily_time_rules"]) == 1
        if case in {"r14_f01_relaxed_delete_anchor", "r14_keep_policy_delete_anchor"}:
            assert len(first_policy["time_sensitive_must"]) == 1
            assert first_policy["time_sensitive_must"][0]["anchor_id"] == "r14-lock-anchor"
        else:
            assert first_policy["time_sensitive_must"] == []
        assert all(item["effective_policy"]["effective_pace"] == TravelPace.NORMAL.value for item in locked_states)
        feedback = [
            json.loads(item.content)["tool_result"]
            for continuation in operation.continuations
            for item in continuation
            if json.loads(item.content).get("tool_result", {}).get("status") == "rejected"
        ]
        expected_rejections = 1 if case in {"r14_f01_relaxed_delete_anchor", "r14_keep_policy_continue"} else 2
        assert feedback == [{"status": "rejected", "code": "policy_rejected"}] * expected_rejections
        expected_calls = 5 if case in {"r14_f01_relaxed_delete_anchor", "r14_keep_policy_continue"} else 6
        expected_iterations = 4 if case == "r14_keep_policy_continue" else 5
        assert operation.call_count == expected_calls
        assert result.budgets.iterations_consumed == expected_iterations
        assert result.budgets.semantic_invocations_consumed == expected_calls
        assert result.budgets.direction_calls_consumed == 6
        before_counts = (
            operation.call_count,
            len(operation.continuations),
            len(repository.put_requests),
            result.budgets.iterations_consumed,
            result.budgets.semantic_invocations_consumed,
            result.budgets.direction_calls_consumed,
        )
        replay = await service.plan_initial(request)
        assert replay == result
        assert (
            operation.call_count,
            len(operation.continuations),
            len(repository.put_requests),
            replay.budgets.iterations_consumed,
            replay.budgets.semantic_invocations_consumed,
            replay.budgets.direction_calls_consumed,
        ) == before_counts
        return

    if case in _R15_POLICY_CASES:
        from app.itinerary_planning import ItineraryPlanningArtifact
        assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
        terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
        persisted = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
        assert persisted.record_type == "incomplete"
        before_counts = (
            operation.call_count,
            len(operation.continuations),
            len(repository.put_requests),
            len(service._map_adapter.directions_calls),
        )
        replay = await service.plan_initial(request)
        assert replay == result
        assert (
            operation.call_count,
            len(operation.continuations),
            len(repository.put_requests),
            len(service._map_adapter.directions_calls),
        ) == before_counts
        expected_counts = {
            "r15_f01_same_pace_delete_anchor": (5, 4, 20, 6),
            "r15_f01_same_pace_keep_anchor": (5, 4, 20, 6),
            "r15_f02_supplement_evidence": (4, 3, 17, 6),
            "r15_f02_supplement_locked_mismatch": (5, 4, 19, 6),
        }
        assert before_counts == expected_counts[case]
        states = [
            write.content["record"]["state"]
            for write in repository.put_requests
            if write.artifact_type == "itinerary_planning.itinerary"
            and write.content["record"]["record_type"] == "in_progress"
        ]
        policy = states[-1]["effective_policy"] if states else None
        if case == "r15_f02_supplement_evidence":
            assert policy is None
            assert result.summary.effective_pace is TravelPace.RELAXED
        else:
            assert policy is not None
            assert policy["daily_time_rules"][0]["rule_id"] == "r14-daily-window"
        if case in {"r15_f01_same_pace_delete_anchor", "r15_f01_same_pace_keep_anchor"}:
            assert policy["effective_pace"] == TravelPace.RELAXED.value
            assert len(policy["time_sensitive_must"]) == 1
            assert policy["time_sensitive_must"][0]["anchor_id"] == "r14-lock-anchor"
            assert policy["time_sensitive_must"][0]["source"] == "verified_evidence"
            assert policy["time_sensitive_must"][0]["source_ref_index"] == 1
        elif case == "r15_f02_supplement_evidence":
            assert policy is None
        else:
            assert policy["effective_pace"] == TravelPace.NORMAL.value
            assert policy["time_sensitive_must"] == []
        if case in {"r15_f01_same_pace_delete_anchor", "r15_f01_same_pace_keep_anchor"}:
            assert PlanningStopReason.NO_PROGRESS in result.stop_reasons
            assert result.itinerary_ref is None
            assert persisted.draft is None
            assert operation.call_count == 5
            assert len(operation.continuations) == 4
            assert result.budgets.iterations_consumed == 5
            assert result.budgets.semantic_invocations_consumed == 5
            assert result.budgets.direction_calls_consumed == 6
        elif case == "r15_f02_supplement_evidence":
            assert result.stop_reasons == (PlanningStopReason.CANDIDATE_SUPPLY_INSUFFICIENT,)
            assert result.itinerary_ref is None
            assert persisted.draft is None
            assert operation.call_count == 4
            assert len(operation.continuations) == 3
            assert result.budgets.iterations_consumed == 3
            assert result.budgets.semantic_invocations_consumed == 4
            assert result.budgets.direction_calls_consumed == 6
        else:
            assert result.stop_reasons == (PlanningStopReason.CANDIDATE_SUPPLY_INSUFFICIENT,)
            assert result.itinerary_ref is not None
            assert persisted.draft is not None
            assert len(persisted.draft.days[0].visits) == 3
            assert persisted.draft.days[0].backups == ()
            assert operation.call_count == 5
            assert len(operation.continuations) == 4
            assert result.budgets.iterations_consumed == 4
            assert result.budgets.semantic_invocations_consumed == 5
            assert result.budgets.direction_calls_consumed == 6
        return

    if case in _R16_POLICY_CASES:
        from app.itinerary_planning import ItineraryPlanningArtifact, ItineraryPlanningCandidateSupplementRequested

        terminal_ref = repository.refs[request.operation_plan.terminal_successor_operation_id]
        persisted = (await repository.get(terminal_ref, expected_model=ItineraryPlanningArtifact)).content.record
        assert replay == result
        expected_counts = {
            "r16_pace_match_submit": (4, 3, 17, 6, 3, 4, 6),
            "r16_pace_conflict_submit": (5, 4, 19, 6, 4, 5, 6),
            "r16_pace_match_supplement": (4, 3, 17, 6, 3, 4, 6),
            "r16_pace_conflict_supplement": (4, 3, 17, 6, 3, 4, 6),
            "r16_daily_source_missing_submit": (5, 4, 19, 6, 4, 5, 6),
            "r16_daily_source_missing_supplement": (4, 3, 17, 6, 3, 4, 6),
            "r16_transport_source_missing_submit": (5, 4, 19, 6, 4, 5, 6),
            "r16_transport_source_missing_supplement": (4, 3, 17, 6, 3, 4, 6),
            "r16_daily_source_valid_submit": (4, 3, 17, 6, 3, 4, 6),
            "r16_transport_source_valid_submit": (4, 3, 17, 6, 3, 4, 6),
        }
        assert (
            operation.call_count,
            len(operation.continuations),
            len(repository.put_requests),
            len(service._map_adapter.directions_calls),
            result.budgets.iterations_consumed,
            result.budgets.semantic_invocations_consumed,
            result.budgets.direction_calls_consumed,
        ) == expected_counts[case]

        feedback = [
            json.loads(item.content)["tool_result"]
            for continuation in operation.continuations
            for item in continuation
            if json.loads(item.content).get("tool_result", {}).get("status") == "rejected"
        ]
        expected_rejected = case in {
            "r16_pace_conflict_submit", "r16_daily_source_missing_submit",
            "r16_transport_source_missing_submit",
        }
        assert feedback == ([{"status": "rejected", "code": "policy_rejected"}] if expected_rejected else [])

        if case == "r16_pace_match_supplement":
            assert isinstance(result, ItineraryPlanningCandidateSupplementRequested), result.model_dump(mode="json")
            assert persisted.record_type == "awaiting_candidate_supplement"
            assert result.planning_ref == terminal_ref
            assert persisted.state.effective_policy is not None
            assert persisted.state.effective_policy.effective_pace == TravelPace.RELAXED.value
            assert persisted.state.draft is None
            assert result.budgets.iterations_consumed == 3
            assert result.budgets.semantic_invocations_consumed == 4
            assert operation.call_count == 4
        elif case.endswith("_supplement"):
            assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
            assert result.stop_reasons == (PlanningStopReason.CANDIDATE_SUPPLY_INSUFFICIENT,)
            assert result.itinerary_ref is None
            assert persisted.record_type == "incomplete"
            assert persisted.draft is None
            states = [
                write.content["record"]["state"]
                for write in repository.put_requests
                if write.artifact_type == "itinerary_planning.itinerary"
                and write.content["record"]["record_type"] == "in_progress"
            ]
            assert states and all(state["effective_policy"] is None for state in states)
            assert result.budgets.iterations_consumed == 3
            assert result.budgets.semantic_invocations_consumed == 4
            assert operation.call_count == 4
        elif case == "r16_pace_conflict_submit":
            assert isinstance(result, ItineraryPlanningCompleted), result.model_dump(mode="json")
            assert persisted.record_type == "completed"
            assert result.itinerary_ref == terminal_ref
            assert persisted.draft is not None
            assert persisted.draft.effective_policy.effective_pace is TravelPace.RELAXED
            assert result.budgets.iterations_consumed == 4
            assert result.budgets.semantic_invocations_consumed == 5
            assert operation.call_count == 5
        elif case.endswith("_missing_submit"):
            assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
            assert result.stop_reasons == (PlanningStopReason.NO_TERMINAL_SUBMISSION,)
            assert result.itinerary_ref is None
            assert persisted.record_type == "incomplete"
            assert persisted.draft is None
            states = [
                write.content["record"]["state"]
                for write in repository.put_requests
                if write.artifact_type == "itinerary_planning.itinerary"
                and write.content["record"]["record_type"] == "in_progress"
            ]
            assert states and all(state["effective_policy"] is None for state in states)
            assert result.budgets.iterations_consumed == 4
            assert result.budgets.semantic_invocations_consumed == 5
            assert operation.call_count == 5
        else:
            assert isinstance(result, ItineraryPlanningCompleted), result.model_dump(mode="json")
            assert persisted.record_type == "completed"
            assert result.itinerary_ref == terminal_ref
            assert persisted.draft is not None
            assert persisted.draft.effective_policy.effective_pace is TravelPace.RELAXED
            if case == "r16_daily_source_valid_submit":
                assert persisted.draft.effective_policy.daily_time_rules[0].source_field == "preferences.daily_time_preferences"
            elif case == "r16_transport_source_valid_submit":
                assert persisted.draft.effective_policy.transport_rules[0].source_field == "preferences.transport_preferences"
            else:
                assert persisted.draft.effective_policy.daily_time_rules == ()
                assert persisted.draft.effective_policy.transport_rules == ()
            assert result.budgets.iterations_consumed == 3
            assert result.budgets.semantic_invocations_consumed == 4
            assert operation.call_count == 4
        assert result.budgets.direction_calls_consumed == 6
        return

    if case not in {"supported", "expanded_default_window", "split_role_and_hours_evidence", "runtime_supported", "route_rebind_repair", "backup_distance_repair", "duplicate_main_repair", "duplicate_backup_repair", "partial_backups", "backup_slots_order_repair", "backup_slots_duplicate_repair", "must_valid"}:
        assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
        assert replay == result
        if case == "meal_intent_mismatch_feedback":
            feedback = json.loads(operation.continuations[-1][0].content)["tool_result"]
            assert feedback == {
                "status": "rejected",
                "code": "policy_rejected",
                "reason": "meal_intent_mismatch",
            }
        if case.startswith("prefix_"):
            assert result.itinerary_ref is not None
            assert not any(gap.gap_type == "backup_unavailable" for gap in result.gaps)
            from app.itinerary_planning import ItineraryPlanningArtifact
            states = [(await repository.get(repository.refs[slot.planning_state_operation_id], expected_model=ItineraryPlanningArtifact)).content.record.state for slot in request.operation_plan.iteration_slots if slot.planning_state_operation_id in repository.refs]
            saved = [state for state in states if state.draft is not None]
            counts = [sum(len(day.visits) for day in item.draft.days) for item in saved]
            if case == "prefix_3_to_1":
                assert counts[-2:] == [3, 3]
                assert saved[-1].no_progress_streak == 1
            elif case == "prefix_1_to_3":
                assert counts[-2:] == [1, 3]
                assert saved[-1].no_progress_streak == 0
        return
    assert isinstance(result, ItineraryPlanningCompleted), result.model_dump(mode="json")
    assert replay == result
    assert result.summary.full_days == 1
    assert result.summary.transit_only_days == 0
    assert result.summary.lunch_count == 1 and result.summary.dinner_count == 1
    assert result.summary.backups == 3
    if case.startswith("backup_slots_"):
        feedback = json.loads(operation.continuations[-1][0].content)["tool_result"]
        assert feedback == {
            "status": "rejected", "code": "policy_rejected", "reason": "backup_slots_mismatch",
            "day_no": 1, "expected_backup_kinds": (
                ["attraction", "lunch"] if case == "backup_slots_duplicate_repair"
                else ["attraction", "lunch", "dinner"]
            ),
            "actual_backup_kinds": [item.backup_kind for item in bad_backups],
        }
        assert bad_submit.draft.days[0].backups == bad_backups
        assert result.budgets.iterations_consumed == 4
        assert result.budgets.semantic_invocations_consumed == operation.call_count == 5
        assert result.budgets.direction_calls_consumed == len(service._map_adapter.directions_calls) == 6
        assert len(service._map_adapter.matrix_calls) == 1
        assert json.loads(operation.continuations[-1][0].content)["tool_result"]["code"] != "success"
    if case == "route_rebind_repair":
        feedback = json.loads(operation.continuations[-1][0].content)["tool_result"]
        assert feedback == {
            "status": "rejected",
            "code": "policy_rejected",
            "reason": "route_leg_binding_mismatch",
        }
    if case == "backup_distance_repair":
        feedback = json.loads(operation.continuations[-1][0].content)["tool_result"]
        assert feedback == {
            "status": "rejected",
            "code": "policy_rejected",
            "reason": "backup_distance_exceeded",
        }
    if case in {"duplicate_main_repair", "duplicate_backup_repair"}:
        feedback = json.loads(operation.continuations[-1][0].content)["tool_result"]
        assert feedback == {
            "status": "rejected",
            "code": "policy_rejected",
            "reason": (
                "main_candidate_duplicate"
                if case == "duplicate_main_repair"
                else "backup_candidate_duplicate"
            ),
        }
    if case == "must_valid":
        assert result.itinerary_ref is not None
        persisted = (await repository.get(result.itinerary_ref, expected_model=__import__("app.itinerary_planning", fromlist=["ItineraryPlanningArtifact"]).ItineraryPlanningArtifact)).content.record
        assert persisted.draft is not None
        assert persisted.draft.days[0].visits[0].must_group_ids == (_expected_must_groups(request.confirmed)[0].group_id,)
    if case == "expanded_default_window":
        assert result.itinerary_ref is not None
        persisted = (await repository.get(result.itinerary_ref, expected_model=__import__("app.itinerary_planning", fromlist=["ItineraryPlanningArtifact"]).ItineraryPlanningArtifact)).content.record
        assert persisted.draft is not None
        day = persisted.draft.days[0]
        local_start = day.open_intervals[0].start_at_utc.astimezone(shanghai)
        local_end = day.open_intervals[0].end_at_utc.astimezone(shanghai)
        outbound = day.route_legs[0].interval.start_at_utc.astimezone(shanghai)
        returned = day.route_legs[-1].interval.end_at_utc.astimezone(shanghai)
        assert (local_start.hour, local_start.minute) == (7, 30)
        assert (local_end.hour, local_end.minute) == (22, 0)
        assert outbound == local_start
        assert returned == local_end
    if case == "runtime_supported":
        assert provider.call_count == 4


def test_rejected_prefix_accepts_a_later_attraction_as_the_backup_core() -> None:
    service, _, _, _, _ = _fixture()
    visits = (
        ScheduledVisit.model_construct(
            visit_id="first-attraction",
            visit_kind=VisitKind.ATTRACTION,
            must_group_ids=(),
        ),
        ScheduledVisit.model_construct(
            visit_id="lunch",
            visit_kind=VisitKind.LUNCH,
            must_group_ids=(),
        ),
        ScheduledVisit.model_construct(
            visit_id="later-attraction",
            visit_kind=VisitKind.ATTRACTION,
            must_group_ids=(),
        ),
        ScheduledVisit.model_construct(
            visit_id="dinner",
            visit_kind=VisitKind.DINNER,
            must_group_ids=(),
        ),
    )
    backups = (
        BackupOption.model_construct(
            backup_kind="attraction",
            core_visit_id="later-attraction",
        ),
        BackupOption.model_construct(
            backup_kind="lunch",
            core_visit_id="lunch",
        ),
    )
    prefix = ItineraryDraft.model_construct(
        days=(
            DayPlan.model_construct(
                date=date(2026, 9, 18),
                visits=visits,
                backups=backups,
            ),
        ),
    )
    run = SimpleNamespace(draft=None, pending_gaps=[])

    assert service._retain_prefix(run, prefix) is True
    assert run.draft is prefix
    assert run.pending_gaps == []  # Optional dinner backup is not a hard gap.


@pytest.mark.asyncio
async def test_repeated_matrix_cache_hits_stop_after_two_no_progress_iterations() -> None:
    call = ValidatedToolCall(
        call_id="matrix_repeat",
        tool_name=PlanningTool.CALCULATE_ROUTE_MATRIX.value,
        arguments=__import__("app.itinerary_planning.models", fromlist=["CalculateRouteMatrixArgs"]).CalculateRouteMatrixArgs(
            date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING,
            location_handles=("candidate-handle-0000", "hotel-handle"),
        ),
    )
    responses = tuple(
        ToolCallsReady(calls=(call,), metadata=_metadata()) for _ in range(3)
    )
    service, request, _, operation, _ = _fixture(*responses)
    base = request.operation_plan
    matrix_ids = tuple(
        OperationId(run_id=base.run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=f"matrix_repeat_{index}")
        for index in range(3)
    )
    request = request.model_copy(update={"operation_plan": base.model_copy(update={"matrix_operation_ids": matrix_ids})})

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete)
    assert result.stop_reasons == (PlanningStopReason.NO_PROGRESS,)
    assert operation.call_count == 3


@pytest.mark.asyncio
async def test_two_rejected_submits_persist_progress_and_stop_without_ordinary_text() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")

    def invalid_route(leg_id: str, origin: str, destination: str) -> ProposedRouteLeg:
        return ProposedRouteLeg(
            leg_id=leg_id,
            origin_handle=origin,
            destination_handle=destination,
            start_at=datetime(2026, 9, 3, 9, tzinfo=shanghai),
            end_at=datetime(2026, 9, 3, 9, 30, tzinfo=shanghai),
            transport_semantic=TransportSemantic.WALKING,
            route_mode=TravelMode.WALKING,
            directions_handle="hotel-handle",
            alternative_index=0,
        )

    invalid_submit = SubmitItineraryArgs(
        effective_policy=ProposedEffectivePlanningPolicy(
            effective_pace=TravelPace.NORMAL,
            daily_time_rules=(),
            time_sensitive_must=(),
            transport_rules=(),
        ),
        draft=ProposedItineraryDraft(
            arrival_transfer=ProposedBoundaryTransfer(
                transfer_kind="arrival",
                hub_handle="hotel-handle",
                hotel_handle="hotel-handle",
                route_leg=invalid_route("invalid-arrival", "hotel-handle", "hotel-handle"),
            ),
            days=(ProposedDayPlan(
                date=date(2026, 9, 3),
                day_kind=PlanningDayKind.TRANSIT_ONLY,
                theme="错误提交",
                open_intervals=(),
                visits=(),
                route_legs=(),
                backups=(),
            ),),
            departure_transfer=ProposedBoundaryTransfer(
                transfer_kind="departure",
                hub_handle="hotel-handle",
                hotel_handle="hotel-handle",
                route_leg=invalid_route("invalid-departure", "hotel-handle", "hotel-handle"),
            ),
            upstream_gap_ids=(),
        ),
    )
    response = ToolCallsReady(
        calls=(ValidatedToolCall(
            call_id="invalid-submit",
            tool_name=PlanningTool.SUBMIT_ITINERARY.value,
            arguments=invalid_submit,
        ),),
        metadata=_metadata(),
    )
    service, request, _, operation, repository = _fixture(response, response)

    result = await service.plan_initial(request)

    assert isinstance(result, ItineraryPlanningIncomplete), result.model_dump(mode="json")
    assert result.stop_reasons == (PlanningStopReason.NO_PROGRESS,)
    assert result.itinerary_ref is None
    assert result.budgets.iterations_consumed == 2
    assert operation.call_count == 2
    assert len([item for item in repository.put_requests if item.artifact_type == "itinerary_planning.model_action"]) == 2
    assert len([item for item in repository.put_requests if item.artifact_type == "itinerary_planning.itinerary"]) == 3


@pytest.mark.asyncio
async def test_service_gate_helpers_cover_replay_resume_and_typed_safety_branches() -> None:
    service, request, _, _, repository = _fixture(ToolTurnCompleted(final_text="结束", metadata=_metadata()))
    await service.plan_initial(request)
    run = service._runs[request.operation_plan.run_id]

    action_request = next(item for item in repository.put_requests if item.artifact_type == "itinerary_planning.model_action")
    action_operation_id = action_request.operation_id
    action = (await repository.get(
        repository.refs[action_operation_id],
        expected_model=ItineraryPlanningModelActionArtifact,
    )).content
    exact_ref, exact_content = await service._persist(
        operation_id=action_operation_id,
        artifact_type="itinerary_planning.model_action",
        content=action,
        expected=ItineraryPlanningModelActionArtifact,
    )
    assert exact_ref == repository.refs[action_operation_id]
    assert exact_content == action
    with pytest.raises(_Indeterminate):
        await service._persist(
            operation_id=action_operation_id,
            artifact_type="itinerary_planning.model_action",
            content=action.model_copy(update={"disposition": "accepted"}),
            expected=ItineraryPlanningModelActionArtifact,
        )
    repository.refs[action_operation_id] = repository.refs[action_operation_id].model_copy(
        update={"content_hash": "sha256:" + "0" * 64}
    )
    with pytest.raises(_Indeterminate):
        await service._persist(
            operation_id=action_operation_id,
            artifact_type="itinerary_planning.model_action",
            content=action,
            expected=ItineraryPlanningModelActionArtifact,
        )
    with pytest.raises(ValueError):
        await service._persist(
            operation_id=OperationId(
                run_id=request.operation_plan.run_id,
                phase=WorkflowPhase.CANDIDATE_RESEARCH,
                logical_step="wrong_phase",
            ),
            artifact_type="itinerary_planning.model_action",
            content=action,
            expected=ItineraryPlanningModelActionArtifact,
        )

    changed_core = run.candidates[0].core.model_copy(update={
        "uid": "candidate-1",
        "name": "更新后的景点",
        "coordinate": BaiduCoordinate(latitude=30.241, longitude=120.151),
    })
    changed_candidate = run.candidates[0].model_copy(update={"core": changed_core})
    service._add_candidate_handles(run, (changed_candidate,), append_only=True)
    added_candidate = changed_candidate.model_copy(update={
        "admission_ordinal": 1,
        "core": changed_core.model_copy(update={"uid": "candidate-2", "name": "新增景点"}),
    })
    service._add_candidate_handles(run, (added_candidate,), append_only=True)
    assert run.directory.require("candidate-handle-0000").label == "更新后的景点"
    assert run.directory.require("candidate-handle-0001").value.uid == "candidate-2"

    evidence_ref = run.candidate_evidence_refs[0]
    service._add_candidate_evidence_handles(run, (
        evidence_ref.model_copy(update={"artifact_id": uuid4()}),
        evidence_ref.model_copy(update={"artifact_id": uuid4()}),
    ))
    assert run.directory.require("evidence-handle-0001", kind="candidate_evidence").value != evidence_ref

    evidence_call = ValidatedToolCall(
        call_id="terminal_evidence",
        tool_name=PlanningTool.GET_CANDIDATE_EVIDENCE.value,
        arguments=__import__("app.itinerary_planning.models", fromlist=["GetCandidateEvidenceArgs"]).GetCandidateEvidenceArgs(
            candidate_handles=("candidate-handle-0000",),
        ),
    )
    service._validate_batch(run, (evidence_call,), terminal_only=True)
    with pytest.raises(ToolBatchError):
        service._validate_batch(run, (), terminal_only=False)
    with pytest.raises(ToolBatchError):
        service._validate_batch(run, (
            evidence_call,
            evidence_call.model_copy(update={
                "call_id": "terminal_evidence_2",
                "arguments": evidence_call.arguments.model_copy(update={"candidate_handles": ("candidate-handle-0001",)}),
            }),
        ), terminal_only=True)
    with pytest.raises(ToolBatchError):
        service._validate_batch(run, (
            ValidatedToolCall(
                call_id="matrix_without_slot",
                tool_name=PlanningTool.CALCULATE_ROUTE_MATRIX.value,
                arguments=CalculateRouteMatrixArgs(
                    date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING,
                    location_handles=("candidate-handle-0000", "hotel-handle"),
                ),
            ),
        ), terminal_only=False)
    assert service._iteration_slot(run, 0).invocation_slot_index == 0
    with pytest.raises(ToolBatchError):
        service._iteration_slot(run, 99)

    progress_empty = ToolDispatchResult((ToolObservation("evidence", "get_candidate_evidence", _CandidateEvidenceObservation(status="no_result", candidates=(), evidence_handles=(), reason_code=None)),), False, 0)
    assert service._dispatch_made_progress(run, progress_empty) is False
    run.no_progress = 2
    assert service._current_gaps(run)[-1].process_reason == "no_progress"
    run.no_progress = 0
    run.iterations = 6
    assert service._current_gaps(run)[-1].process_reason == "iteration_limit_reached"
    run.iterations = 0
    draft = SimpleNamespace(days=(
        SimpleNamespace(day_kind=PlanningDayKind.FULL, visits=(), route_legs=(), backups=()),
        SimpleNamespace(day_kind=PlanningDayKind.PARTIAL, visits=(), route_legs=(), backups=()),
        SimpleNamespace(day_kind=PlanningDayKind.TRANSIT_ONLY, visits=(), route_legs=(), backups=()),
    ))
    summary = service._summary(run, draft)
    assert (summary.full_days, summary.partial_days, summary.transit_only_days) == (1, 1, 1)
    with pytest.raises(ValueError):
        service._target(run, ProposedEffectivePlanningPolicy(effective_pace=TravelPace.NORMAL, daily_time_rules=(), time_sensitive_must=(), transport_rules=()))
    with pytest.raises(ValueError):
        with patch.object(type(request), "model_dump", side_effect=RuntimeError("invalid model")):
            _fresh(request, type(request))

    class _BrokenBudget:
        async def get_snapshot(self, *, key):
            raise RuntimeError("snapshot unavailable")

    service._budget = _BrokenBudget()
    budget = await service._budget_summary(run)
    assert budget.budget_snapshot_complete is False
