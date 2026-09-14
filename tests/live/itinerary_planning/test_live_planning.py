"""Module 09 live qualification with real DeepSeek and Baidu Maps calls."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json
from uuid import uuid4

import pytest

from app.baidu_map import (
    BaiduCoordinate,
    MapCapability,
    create_baidu_map_runtime,
    load_baidu_map_settings,
)
from app.candidate_research import (
    CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,
    CandidateResearchArtifact,
    CandidateState,
    MealIntent,
    ResearchMode,
    ResearchObjectiveKind,
    ResearchRole,
    ResearchTool,
    RoleReasonCode,
    RoleVerdict,
)
from app.candidate_research.artifacts import (
    ArtifactCandidateCoreSnapshot,
    ArtifactCandidateDetailsStateSnapshot,
    ArtifactCandidateRecordSnapshot,
    ArtifactCoordinateSnapshot,
    ArtifactKnownLLMUsageSnapshot,
    ArtifactPlaceDetailsSnapshot,
    ArtifactProvenanceSnapshot,
    ArtifactResearchBudgetSummarySnapshot,
    ArtifactResearchCountSummarySnapshot,
    ArtifactResearchCoverageSummarySnapshot,
    ArtifactRoleAssessmentSnapshot,
    CandidateResearchPlaceObservationArtifact,
    CandidateSetStateSnapshot,
    CompletedCandidateSetRecord,
    PlaceDetailsSuccessObservation,
    SearchCoverageState,
    SearchCoverageRecord,
)
from app.hotel_selection import (
    HOTEL_ARTIFACT_REGISTRATIONS,
    HotelSelectedDecision,
    HotelSelectionArtifact,
)
from app.hotel_selection.models import HotelCandidate, HotelCoordinate
from app.itinerary_planning import (
    ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS,
    ItineraryPlanningArtifact,
    ItineraryPlanningCompleted,
    ItineraryPlanningInitialRequest,
    ItineraryPlanningOperationPlan,
    ItineraryPlanningService,
    PlanningIterationOperationIds,
)
from app.itinerary_planning.models import (
    ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
    ITINERARY_PLANNING_ITERATION_BUDGET_KEY,
)
from app.llm_runtime import DeepSeekRuntime
from app.llm_runtime.provider import LangChainDeepSeekAdapter
from app.llm_runtime.settings import load_settings
from app.requirements import (
    ConfirmedTravelRequirement,
    FieldMetadataRegistry,
    HubType,
    InterestTag,
    TravelPace,
    TravelPreferences,
    TravelRequirement,
    TravelTimeWindow,
    TravelTimeWindowArrival,
    TravelTimeWindowDeparture,
)
from app.workflow_contracts import (
    DEMO_ARTIFACT_REPOSITORY_LIMITS,
    ArtifactRef,
    ArtifactSchemaRegistry,
    ArtifactWriteRequest,
    ExecutionBudgetLimit,
    ExecutionBudgetManagerLimits,
    ExecutionBudgetPlan,
    InMemoryArtifactRepository,
    InMemoryExecutionBudgetManager,
    OperationId,
    RandomUUIDFactory,
    SystemUtcClock,
    WorkflowPhase,
)


class _RealSleeper:
    def sleep(self, seconds: float) -> None:
        import time

        time.sleep(seconds)

    async def async_sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _ObservedProvider:
    def __init__(self, delegate: LangChainDeepSeekAdapter) -> None:
        self.delegate = delegate
        self.tool_turns = 0
        self.tool_batches: list[tuple[str, ...]] = []
        self.submit_summaries: list[dict[str, object]] = []
        self.feedback_codes: list[tuple[str | None, str | None]] = []
        self.direction_request_summaries: list[object] = []
        self.matrix_request_summaries: list[object] = []

    @staticmethod
    def _leg_summary(leg: object) -> object:
        if not isinstance(leg, dict):
            return None
        return {
            key: leg.get(key)
            for key in (
                "leg_id",
                "origin_handle",
                "destination_handle",
                "start_at",
                "end_at",
                "transport_semantic",
                "route_mode",
                "directions_handle",
                "alternative_index",
            )
        }

    def _submit_summary(self, arguments: object) -> dict[str, object]:
        if not isinstance(arguments, dict):
            return {"invalid_arguments": True}
        policy = arguments.get("effective_policy")
        draft = arguments.get("draft")
        if not isinstance(policy, dict) or not isinstance(draft, dict):
            return {"invalid_shape": True}
        days = draft.get("days") if isinstance(draft.get("days"), list) else []
        day_summaries = []
        for day in days:
            if not isinstance(day, dict):
                continue
            visits = day.get("visits") if isinstance(day.get("visits"), list) else []
            legs = day.get("route_legs") if isinstance(day.get("route_legs"), list) else []
            backups = day.get("backups") if isinstance(day.get("backups"), list) else []
            day_summaries.append({
                "date": day.get("date"),
                "day_kind": day.get("day_kind"),
                "open_intervals": day.get("open_intervals"),
                "visits": [
                    {
                        key: visit.get(key)
                        for key in (
                            "visit_id",
                            "candidate_handle",
                            "visit_kind",
                            "start_at",
                            "end_at",
                            "duration_source",
                            "business_hours_state",
                            "business_evidence_handles",
                        )
                    }
                    for visit in visits
                    if isinstance(visit, dict)
                ],
                "route_legs": [self._leg_summary(leg) for leg in legs],
                "backups": backups,
            })
        arrival = draft.get("arrival_transfer")
        departure = draft.get("departure_transfer")
        return {
            "effective_pace": policy.get("effective_pace"),
            "daily_rule_count": len(policy.get("daily_time_rules", [])),
            "anchor_count": len(policy.get("time_sensitive_must", [])),
            "transport_rule_count": len(policy.get("transport_rules", [])),
            "arrival_route": self._leg_summary(arrival.get("route_leg")) if isinstance(arrival, dict) else None,
            "days": day_summaries,
            "departure_route": self._leg_summary(departure.get("route_leg")) if isinstance(departure, dict) else None,
            "upstream_gap_ids": draft.get("upstream_gap_ids"),
        }

    def _observe_feedback(self, request) -> None:
        for message in reversed(request.messages):
            if message.get("role") != "tool" or not isinstance(message.get("content"), str):
                continue
            try:
                payload = json.loads(message["content"])
            except (TypeError, ValueError):
                return
            result = payload.get("tool_result") if isinstance(payload, dict) else None
            if isinstance(result, dict):
                self.feedback_codes.append((result.get("status"), result.get("code")))
            return

    def _observe(self, result):
        calls = getattr(result, "tool_calls", ())
        if calls:
            self.tool_batches.append(tuple(call[1] for call in calls))
            for call in calls:
                if call[1] == "submit_itinerary":
                    self.submit_summaries.append(self._submit_summary(call[2]))
                elif call[1] == "plan_directions" and isinstance(call[2], dict):
                    legs = call[2].get("legs")
                    self.direction_request_summaries.append([
                        {
                            key: leg.get(key)
                            for key in (
                                "leg_id",
                                "origin_handle",
                                "destination_handle",
                                "transport_semantic",
                                "route_mode",
                                "alternatives_limit",
                            )
                        }
                        for leg in (legs if isinstance(legs, list) else [])
                        if isinstance(leg, dict)
                    ])
                elif call[1] == "calculate_route_matrix" and isinstance(call[2], dict):
                    self.matrix_request_summaries.append({
                        "date": call[2].get("date"),
                        "mode": call[2].get("mode"),
                        "location_count": len(call[2].get("location_handles", [])),
                    })
        return result

    def invoke_json(self, request):
        return self.delegate.invoke_json(request)

    async def ainvoke_json(self, request):
        return await self.delegate.ainvoke_json(request)

    def invoke_tools(self, request):
        self.tool_turns += 1
        return self._observe(self.delegate.invoke_tools(request))

    async def ainvoke_tools(self, request):
        self.tool_turns += 1
        return self._observe(await self.delegate.ainvoke_tools(request))

    def invoke_tool_continuation(self, request):
        self.tool_turns += 1
        self._observe_feedback(request)
        return self._observe(self.delegate.invoke_tool_continuation(request))

    async def ainvoke_tool_continuation(self, request):
        self.tool_turns += 1
        self._observe_feedback(request)
        return self._observe(await self.delegate.ainvoke_tool_continuation(request))

    def list_models(self):
        return self.delegate.list_models()

    async def alist_models(self):
        return await self.delegate.alist_models()

    def close(self) -> None:
        self.delegate.close()

    async def aclose(self) -> None:
        await self.delegate.aclose()


class _ObservedMapAdapter:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.matrix_calls = 0
        self.direction_calls = 0
        self.matrix_summaries: list[dict[str, object]] = []
        self.direction_summaries: list[dict[str, object]] = []

    async def geocode(self, request):
        return await self.delegate.geocode(request)

    async def calculate_route_matrix(self, request):
        self.matrix_calls += 1
        result = await self.delegate.calculate_route_matrix(request)
        cells = getattr(result, "cells", ())
        distances = [
            cell.distance_m
            for cell in cells
            if getattr(cell, "status", None) == "success"
        ]
        self.matrix_summaries.append({
            "status": getattr(result, "status", None),
            "successful_cells": len(distances),
            "minimum_distance_m": min(distances) if distances else None,
            "maximum_distance_m": max(distances) if distances else None,
        })
        return result

    async def plan_directions(self, request):
        self.direction_calls += 1
        result = await self.delegate.plan_directions(request)
        alternatives = getattr(result, "alternatives", ())
        self.direction_summaries.append({
            "leg_id": getattr(result, "leg_id", None),
            "status": getattr(result, "status", None),
            "alternative_durations_s": [
                item.duration_s for item in alternatives
            ],
        })
        return result


class _ObservedPlanningService(ItineraryPlanningService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.validation_failures: list[dict[str, object]] = []

    def _record_validation_failure(self, stage: str, error: Exception) -> None:
        traceback = error.__traceback__
        while traceback is not None and traceback.tb_next is not None:
            traceback = traceback.tb_next
        self.validation_failures.append({
            "stage": stage,
            "exception": type(error).__name__,
            "reason": getattr(error, "reason", None) or str(error) or None,
            "line": None if traceback is None else traceback.tb_lineno,
        })

    def _materialize_draft(self, run, proposal, policy):
        try:
            return super()._materialize_draft(run, proposal, policy)
        except Exception as error:
            self._record_validation_failure("materialize", error)
            raise

    def _validate_draft(self, run, draft, *, allow_prefix=False, allow_missing_backups=False) -> None:
        try:
            super()._validate_draft(
                run,
                draft,
                allow_prefix=allow_prefix,
                allow_missing_backups=allow_missing_backups,
            )
        except Exception as error:
            self._record_validation_failure("validate", error)
            raise


def _operation_id(run_id, step: str) -> OperationId:
    return OperationId(
        run_id=run_id,
        phase=WorkflowPhase.ITINERARY_PLANNING,
        logical_step=step,
    )


async def _put(repository, operation_id, artifact_type: str, content):
    return await repository.put(
        ArtifactWriteRequest(
            operation_id=operation_id,
            artifact_type=artifact_type,
            schema_version="1.0",
            content=content.model_dump(
                mode="json",
                exclude_none=False,
                by_alias=False,
                exclude_computed_fields=True,
            ),
        )
    )


async def _build_request(repository) -> ItineraryPlanningInitialRequest:
    run_id = uuid4()
    now = datetime.now(timezone.utc)
    confirmed = ConfirmedTravelRequirement(
        outcome_type="confirmed",
        schema_version="1.0",
        requirement=TravelRequirement(
            destination="杭州",
            time_window=TravelTimeWindow(
                sightseeing_start_date="2026-09-11",
                sightseeing_end_date="2026-09-11",
                sightseeing_days=1,
                arrival=TravelTimeWindowArrival(
                    hub_name="杭州东站",
                    hub_type=HubType.RAILWAY_STATION,
                    local_datetime="2026-09-10 18:00",
                ),
                departure=TravelTimeWindowDeparture(
                    hub_name="杭州东站",
                    hub_type=HubType.RAILWAY_STATION,
                    local_datetime="2026-09-12 18:00",
                ),
            ),
            preferences=TravelPreferences(
                interests=[InterestTag.CULTURE, InterestTag.NATURE],
                pace=TravelPace.RELAXED,
                agent_decides_pace=False,
            ),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(),
        confirmation_summary_digest="sha256:" + "c" * 64,
    )

    hotel_candidate_ref = ArtifactRef(
        artifact_id=uuid4(),
        artifact_type="hotel.candidate_set",
        schema_version="1.0",
        content_hash="sha256:" + "a" * 64,
        operation_id=OperationId(
            run_id=run_id,
            phase=WorkflowPhase.HOTEL_SELECTION,
            logical_step="live_hotel_candidates",
        ),
        created_at=now,
        size_bytes=1,
    )
    hotel = HotelSelectionArtifact(
        schema_version="1.0",
        candidate_set_ref=hotel_candidate_ref,
        decision=HotelSelectedDecision(
            schema_version="1.0",
            decision_type="selected",
            selected_candidate=HotelCandidate(
                schema_version="1.0",
                uid="live-hotel",
                name="杭州西湖酒店",
                address="杭州市西湖区湖滨路",
                coordinate=HotelCoordinate(
                    schema_version="1.0",
                    coordinate_system="bd09ll",
                    latitude=30.254,
                    longitude=120.163,
                ),
                category="酒店",
            ),
        ),
    )
    hotel_ref = await _put(
        repository,
        OperationId(
            run_id=run_id,
            phase=WorkflowPhase.HOTEL_SELECTION,
            logical_step="live_hotel_selection",
        ),
        "hotel.selection",
        hotel,
    )

    names = (
        "西湖文化景点",
        "湖滨午餐餐厅",
        "湖滨晚餐餐厅",
        "西湖文化备选景点",
        "湖滨午餐备选餐厅",
        "湖滨晚餐备选餐厅",
    )
    coordinates = (
        (30.2528, 120.1618),
        (30.2518, 120.1608),
        (30.2524, 120.1625),
        (30.2530, 120.1615),
        (30.2516, 120.1605),
        (30.2526, 120.1628),
    )
    observation_refs = []
    candidates = []
    for index, (name, (latitude, longitude)) in enumerate(zip(names, coordinates, strict=True)):
        coordinate = ArtifactCoordinateSnapshot(
            coordinate_system="bd09ll",
            latitude=latitude,
            longitude=longitude,
        )
        observation = CandidateResearchPlaceObservationArtifact(
            schema_version="1.0",
            run_id=run_id,
            mode=ResearchMode.INITIAL,
            invocation_slot_index=0,
            iteration_index=0,
            action_ordinal=index,
            tool=ResearchTool.GET_PLACE_DETAILS,
            action_fingerprint="sha256:" + format(index + 1, "x") * 64,
            result=PlaceDetailsSuccessObservation(
                record_type="details_success",
                details=ArtifactPlaceDetailsSnapshot(
                    uid=f"live-candidate-{index + 1}",
                    name=name,
                    address=f"杭州市西湖区测试路{index + 1}号",
                    coordinate=coordinate,
                    provider_category="旅游景点" if index in {0, 3} else "中餐厅",
                    brand=None,
                    telephone=None,
                    rating=None,
                    business_hours=("09:00-21:00",),
                ),
                provenance=ArtifactProvenanceSnapshot(
                    source="baidu_maps_mcp",
                    capability=MapCapability.PLACE_DETAILS,
                    retrieved_at=now,
                    verified=True,
                ),
            ),
        )
        observation_ref = await _put(
            repository,
            OperationId(
                run_id=run_id,
                phase=WorkflowPhase.CANDIDATE_RESEARCH,
                logical_step=f"live_candidate_details_{index}",
            ),
            "candidate_research.place_observation",
            observation,
        )
        observation_refs.append(observation_ref)
        candidates.append(
            ArtifactCandidateRecordSnapshot(
                schema_version="1.0",
                admission_ordinal=index,
                core=ArtifactCandidateCoreSnapshot(
                    uid=f"live-candidate-{index + 1}",
                    name=name,
                    address=f"杭州市西湖区测试路{index + 1}号",
                    coordinate=coordinate,
                    provider_category="旅游景点" if index in {0, 3} else "中餐厅",
                ),
                state=CandidateState.ELIGIBLE,
                roles=(
                    ArtifactRoleAssessmentSnapshot(
                        role=ResearchRole.ATTRACTION,
                        verdict=RoleVerdict.SUPPORTED,
                        reason_code=RoleReasonCode.NAME_SEMANTICS,
                        evidence_indexes=(index,),
                    ),
                    ArtifactRoleAssessmentSnapshot(
                        role=ResearchRole.RESTAURANT,
                        verdict=RoleVerdict.SUPPORTED,
                        reason_code=RoleReasonCode.NAME_SEMANTICS,
                        evidence_indexes=(index,),
                    ),
                ),
                interests=(InterestTag.CULTURE, InterestTag.NATURE),
                meal_intents=tuple(MealIntent),
                must_matches=(),
                avoid_matches=(),
                details=ArtifactCandidateDetailsStateSnapshot(
                    status="success",
                    observation_index=index,
                ),
                observation_indexes=(index,),
                dietary_restrictions="not_evaluated",
                mobility_constraints="not_evaluated",
                budget_fit="not_evaluated",
            )
        )

    zero_usage = ArtifactKnownLLMUsageSnapshot(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        reasoning_tokens=0,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=0,
    )
    counts = ArtifactResearchCountSummarySnapshot(
        attraction_count=6,
        attraction_target=2,
        restaurant_count=6,
        restaurant_target=1,
        unique_eligible_count=6,
        admitted_slots=6,
        candidate_capacity=128,
    )
    coverage = ArtifactResearchCoverageSummarySnapshot(
        effective_interests=(InterestTag.CULTURE, InterestTag.NATURE),
        covered_interests=(InterestTag.CULTURE, InterestTag.NATURE),
        covered_meals=tuple(MealIntent),
        must_item_count=0,
        resolved_must_item_count=0,
        details_success_count=6,
        details_target=2,
        initial_city_attraction_search_done=True,
        initial_hotel_restaurant_nearby_done=True,
    )
    budgets = ArtifactResearchBudgetSummarySnapshot(
        iteration_limit=6,
        iterations_consumed=1,
        place_call_limit=48,
        place_calls_consumed=6,
        semantic_invocation_limit=12,
        semantic_invocations_consumed=1,
        known_provider_attempts=1,
        provider_attempts_complete=True,
        known_usage=zero_usage,
        usage_complete=True,
    )
    state = CandidateSetStateSnapshot(
        confirmed_requirement_digest=confirmed.confirmation_summary_digest,
        hotel_selection_ref=hotel_ref,
        effective_interests=(InterestTag.CULTURE, InterestTag.NATURE),
        model_action_refs=(),
        model_judgment_refs=(),
        observation_refs=tuple(observation_refs),
        candidates=tuple(candidates),
        requirement_group_states=(),
        search_coverage=SearchCoverageState(
            records=(
                SearchCoverageRecord(
                    objective_id="live-initial-city",
                    objective_kind=ResearchObjectiveKind.INITIAL_CITY_ATTRACTION,
                    action_fingerprint="sha256:" + "0" * 64,
                    observation_index=0,
                    role=ResearchRole.ATTRACTION,
                    interest=None,
                    meal_intent=None,
                    eligible_candidate_ordinals=(0, 3),
                ),
                SearchCoverageRecord(
                    objective_id="live-initial-hotel",
                    objective_kind=ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY,
                    action_fingerprint="sha256:" + "1" * 64,
                    observation_index=1,
                    role=ResearchRole.RESTAURANT,
                    interest=None,
                    meal_intent=None,
                    eligible_candidate_ordinals=(1, 2, 4, 5),
                ),
                SearchCoverageRecord(
                    objective_id="live-interest-culture",
                    objective_kind=ResearchObjectiveKind.INTEREST_COVERAGE,
                    action_fingerprint="sha256:" + "2" * 64,
                    observation_index=2,
                    role=None,
                    interest=InterestTag.CULTURE,
                    meal_intent=None,
                    eligible_candidate_ordinals=(0, 1, 2, 3, 4, 5),
                ),
                SearchCoverageRecord(
                    objective_id="live-interest-nature",
                    objective_kind=ResearchObjectiveKind.INTEREST_COVERAGE,
                    action_fingerprint="sha256:" + "3" * 64,
                    observation_index=3,
                    role=None,
                    interest=InterestTag.NATURE,
                    meal_intent=None,
                    eligible_candidate_ordinals=(0, 1, 2, 3, 4, 5),
                ),
                SearchCoverageRecord(
                    objective_id="live-meal-lunch",
                    objective_kind=ResearchObjectiveKind.MEAL_LUNCH,
                    action_fingerprint="sha256:" + "4" * 64,
                    observation_index=4,
                    role=None,
                    interest=None,
                    meal_intent=MealIntent.LUNCH,
                    eligible_candidate_ordinals=(0, 1, 2, 3, 4, 5),
                ),
                SearchCoverageRecord(
                    objective_id="live-meal-dinner",
                    objective_kind=ResearchObjectiveKind.MEAL_DINNER,
                    action_fingerprint="sha256:" + "5" * 64,
                    observation_index=5,
                    role=None,
                    interest=None,
                    meal_intent=MealIntent.DINNER,
                    eligible_candidate_ordinals=(0, 1, 2, 3, 4, 5),
                ),
            ),
            initial_city_attraction_search_done=True,
            initial_hotel_restaurant_nearby_done=True,
        ),
        counts=counts,
        coverage=coverage,
        budgets=budgets,
        pending_gaps=(),
        iteration_no_progress_streak=0,
    )
    candidate_artifact = CandidateResearchArtifact(
        schema_version="1.0",
        run_id=run_id,
        mode=ResearchMode.INITIAL,
        predecessor_ref=None,
        invocation_request_digest="sha256:" + "b" * 64,
        record=CompletedCandidateSetRecord(
            record_type="completed",
            transition_kind="normal",
            state=state,
            counts=counts,
            coverage=coverage,
            budgets=budgets,
        ),
    )
    candidate_ref = await _put(
        repository,
        OperationId(
            run_id=run_id,
            phase=WorkflowPhase.CANDIDATE_RESEARCH,
            logical_step="live_candidate_set",
        ),
        "candidate_research.candidate_set",
        candidate_artifact,
    )

    slots = tuple(
        PlanningIterationOperationIds(
            invocation_slot_index=index,
            action_operation_id=_operation_id(run_id, f"live_action_{index}"),
            planning_state_operation_id=_operation_id(run_id, f"live_state_{index}"),
        )
        for index in range(6)
    )
    plan = ItineraryPlanningOperationPlan(
        run_id=run_id,
        iteration_slots=slots,
        hub_geocode_operation_ids=(
            _operation_id(run_id, "live_hub_arrival"),
            _operation_id(run_id, "live_hub_departure"),
        ),
        matrix_operation_ids=tuple(
            _operation_id(run_id, f"live_matrix_{index}") for index in range(12)
        ),
        direction_operation_ids=tuple(
            _operation_id(run_id, f"live_direction_{index}") for index in range(10)
        ),
        terminal_action_operation_id=_operation_id(run_id, "live_terminal_action"),
        terminal_successor_operation_id=_operation_id(run_id, "live_terminal"),
    )
    return ItineraryPlanningInitialRequest(
        confirmed=confirmed,
        hotel_selection_ref=hotel_ref,
        candidate_set_ref=candidate_ref,
        operation_plan=plan,
    )


@pytest.mark.asyncio
async def test_plan_initial_completes_with_real_deepseek_and_baidu_maps() -> None:
    registry = ArtifactSchemaRegistry(
        (
            *HOTEL_ARTIFACT_REGISTRATIONS,
            *CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,
            *ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS,
        )
    )
    repository = InMemoryArtifactRepository(
        registry=registry,
        utc_clock=SystemUtcClock(),
        uuid_factory=RandomUUIDFactory(),
        limits=DEMO_ARTIFACT_REPOSITORY_LIMITS,
    )
    request = await _build_request(repository)
    budget_manager = InMemoryExecutionBudgetManager(
        plan=ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=request.operation_plan.run_id,
            limits=(
                ExecutionBudgetLimit(
                    key=ITINERARY_PLANNING_ITERATION_BUDGET_KEY,
                    limit=6,
                ),
                ExecutionBudgetLimit(
                    key=ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
                    limit=10,
                ),
            ),
        ),
        limits=ExecutionBudgetManagerLimits(max_operation_records=4096),
    )
    provider = _ObservedProvider(LangChainDeepSeekAdapter(load_settings()))
    llm_runtime = DeepSeekRuntime(provider=provider, sleeper=_RealSleeper())

    try:
        async with create_baidu_map_runtime(
            settings=load_baidu_map_settings(),
            clock=SystemUtcClock(),
        ) as map_runtime:
            observed_map = _ObservedMapAdapter(map_runtime.adapter)
            service = _ObservedPlanningService(
                runtime=llm_runtime,
                map_adapter=observed_map,
                artifact_repository=repository,
                budget_manager=budget_manager,
            )
            try:
                result = await service.plan_initial(request)
                before_replay = (
                    provider.tool_turns,
                    observed_map.matrix_calls,
                    observed_map.direction_calls,
                )
                replay = await service.plan_initial(request)
            finally:
                await service.aclose()

            diagnostic = {
                "outcome_type": result.outcome_type,
                "failure": getattr(result, "failure", None).model_dump(mode="json")
                if getattr(result, "failure", None) is not None
                else None,
                "tool_batches": provider.tool_batches,
                "feedback_codes": provider.feedback_codes,
                "submit_summaries": provider.submit_summaries,
                "matrix_request_summaries": provider.matrix_request_summaries,
                "direction_request_summaries": provider.direction_request_summaries,
                "matrix_calls": observed_map.matrix_calls,
                "direction_calls": observed_map.direction_calls,
                "matrix_summaries": observed_map.matrix_summaries,
                "direction_summaries": observed_map.direction_summaries,
                "validation_failures": service.validation_failures,
            }
            assert isinstance(result, ItineraryPlanningCompleted), json.dumps(
                diagnostic,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
            assert replay == result
            assert provider.tool_turns >= 1
            assert observed_map.matrix_calls >= 1
            assert observed_map.direction_calls >= 1
            assert (
                provider.tool_turns,
                observed_map.matrix_calls,
                observed_map.direction_calls,
            ) == before_replay
            persisted = (
                await repository.get(
                    result.itinerary_ref,
                    expected_model=ItineraryPlanningArtifact,
                )
            ).content
            assert persisted.record.record_type == "completed"
            assert persisted.record.draft is not None
            assert len(persisted.record.draft.days) == 1
            assert len(persisted.record.draft.days[0].visits) >= 3

            candidate_artifact = (
                await repository.get(
                    request.candidate_set_ref,
                    expected_model=CandidateResearchArtifact,
                )
            ).content
            candidate_names = {
                candidate.core.uid: candidate.core.name
                for candidate in candidate_artifact.record.state.candidates
            }
            day = persisted.record.draft.days[0]
            visit_names = {
                visit.visit_id: candidate_names.get(visit.location.uid, visit.location.uid)
                for visit in day.visits
            }

            def interval_text(interval) -> str:
                start = interval.start_at_utc.astimezone().strftime("%H:%M")
                end = interval.end_at_utc.astimezone().strftime("%H:%M")
                return f"{start}-{end}"

            live_report = {
                "model": "deepseek-v4-flash",
                "outcome": result.outcome_type,
                "open_intervals": [interval_text(item) for item in day.open_intervals],
                "visits": [
                    {
                        "time": interval_text(visit.interval),
                        "kind": visit.visit_kind.value,
                        "name": candidate_names.get(visit.location.uid, visit.location.uid),
                    }
                    for visit in day.visits
                ],
                "route_legs": [
                    {
                        "time": interval_text(leg.interval),
                        "mode": leg.transport_semantic.value,
                        "distance_m": leg.distance_m,
                        "duration_s": leg.duration_s,
                    }
                    for leg in day.route_legs
                ],
                "backups": [
                    {
                        "kind": backup.backup_kind,
                        "core_name": visit_names.get(backup.core_visit_id),
                        "name": candidate_names.get(backup.candidate.uid, backup.candidate.uid),
                    }
                    for backup in day.backups
                ],
                "llm_tool_turns": provider.tool_turns,
                "matrix_calls": observed_map.matrix_calls,
                "direction_calls": observed_map.direction_calls,
                "exact_replay_equal": replay == result,
            }
            print(
                "MODULE09_LIVE_RESULT="
                + json.dumps(live_report, ensure_ascii=False, separators=(",", ":"))
            )
    finally:
        await llm_runtime.aclose()
