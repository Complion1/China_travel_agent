"""Modules 07-09 live qualification through their public interfaces.

This test intentionally starts from a confirmed requirement.  Module 07 obtains
real hotel candidates from Baidu Maps, the test records an explicit user choice,
Module 08 researches real attraction and restaurant candidates, and Module 09
plans against those persisted predecessor artifacts.  No upstream artifact is
fabricated in this qualification path.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date
import json
import os
import traceback
from uuid import UUID, uuid4

import pytest

from app.baidu_map import create_baidu_map_runtime, load_baidu_map_settings
from app.candidate_research import (
    CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,
    CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY,
    CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY,
    CandidateResearchArtifact,
    CandidateResearchCompleted,
    CandidateResearchInitialRequest,
    CandidateResearchModelActionArtifact,
    CandidateResearchModelJudgmentArtifact,
    CandidateResearchOperationPlan,
    CandidateResearchService,
    ResearchIterationOperationIds,
)
from app.hotel_selection import (
    HOTEL_ARTIFACT_REGISTRATIONS,
    HOTEL_PLACE_CALL_BUDGET_KEY,
    HotelCandidateOperationIds,
    HotelCandidateSelectionService,
    HotelCandidatesReady,
    HotelDiscoveryArtifact,
    HotelPlaceSearchArtifact,
    HotelSelectCommand,
    HotelSelected,
    HotelSelectionRequest,
)
from app.itinerary_planning import (
    ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS,
    ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY,
    ITINERARY_PLANNING_ITERATION_BUDGET_KEY,
    ItineraryPlanningArtifact,
    ItineraryPlanningCompleted,
    ItineraryPlanningInitialRequest,
    ItineraryPlanningOperationPlan,
    ItineraryPlanningService,
    PlanningIterationOperationIds,
    VisitKind,
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
    ArtifactSchemaRegistry,
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
        self.json_invocations = 0
        self.tool_invocations = 0

    def invoke_json(self, request):
        self.json_invocations += 1
        return self.delegate.invoke_json(request)

    async def ainvoke_json(self, request):
        self.json_invocations += 1
        return await self.delegate.ainvoke_json(request)

    def invoke_tools(self, request):
        self.tool_invocations += 1
        return self.delegate.invoke_tools(request)

    async def ainvoke_tools(self, request):
        self.tool_invocations += 1
        return await self.delegate.ainvoke_tools(request)

    def invoke_tool_continuation(self, request):
        self.tool_invocations += 1
        return self.delegate.invoke_tool_continuation(request)

    async def ainvoke_tool_continuation(self, request):
        self.tool_invocations += 1
        return await self.delegate.ainvoke_tool_continuation(request)

    def list_models(self):
        return self.delegate.list_models()

    async def alist_models(self):
        return await self.delegate.alist_models()

    def close(self) -> None:
        self.delegate.close()

    async def aclose(self) -> None:
        await self.delegate.aclose()


class _ObservedMapAdapter:
    """Full structural BaiduMapAdapter wrapper with per-capability counters."""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls = {
            "geocode": 0,
            "reverse_geocode": 0,
            "search_places": 0,
            "get_place_details": 0,
            "calculate_route_matrix": 0,
            "plan_directions": 0,
            "get_weather": 0,
        }

    async def geocode(self, request):
        self.calls["geocode"] += 1
        return await self.delegate.geocode(request)

    async def reverse_geocode(self, request):
        self.calls["reverse_geocode"] += 1
        return await self.delegate.reverse_geocode(request)

    async def search_places(self, request):
        self.calls["search_places"] += 1
        return await self.delegate.search_places(request)

    async def get_place_details(self, request):
        self.calls["get_place_details"] += 1
        return await self.delegate.get_place_details(request)

    async def calculate_route_matrix(self, request):
        self.calls["calculate_route_matrix"] += 1
        return await self.delegate.calculate_route_matrix(request)

    async def plan_directions(self, request):
        self.calls["plan_directions"] += 1
        return await self.delegate.plan_directions(request)

    async def get_weather(self, request):
        self.calls["get_weather"] += 1
        return await self.delegate.get_weather(request)


class _ObservedResearchService(CandidateResearchService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.action_rejections: list[str] = []
        self.internal_errors: list[dict[str, object]] = []

    def _validate_actions(self, run, actions, objectives, slot):
        reason = super()._validate_actions(run, actions, objectives, slot)
        if reason is not None:
            self.action_rejections.append(reason)
        return reason

    async def _run_locked(self, request, mode, digest):
        try:
            return await super()._run_locked(request, mode, digest)
        except Exception as error:
            last = traceback.extract_tb(error.__traceback__)[-1]
            self.internal_errors.append(
                {
                    "type": type(error).__name__,
                    "message": str(error),
                    "file": last.filename,
                    "line": last.lineno,
                }
            )
            raise


class _ObservedPlanningService(ItineraryPlanningService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.action_batches: list[list[dict[str, object]]] = []
        self.action_rejections: list[dict[str, object]] = []
        self.submit_attempts: list[dict[str, object]] = []
        self.validation_errors: list[dict[str, object]] = []

    def _validate_batch(self, run, calls, *, terminal_only):
        batch = [
            {
                "call_id": call.call_id,
                "tool": call.tool_name,
                "arguments": call.arguments.model_dump(mode="json"),
            }
            for call in calls
        ]
        self.action_batches.append(batch)
        try:
            return super()._validate_batch(run, calls, terminal_only=terminal_only)
        except Exception as error:
            self.action_rejections.append(
                {
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "terminal_only": terminal_only,
                    "calls": batch,
                }
            )
            raise

    def _materialize_draft(self, run, proposal, policy):
        try:
            return super()._materialize_draft(run, proposal, policy)
        except Exception as error:
            last = traceback.extract_tb(error.__traceback__)[-1]
            self.validation_errors.append(
                {
                    "stage": "materialize",
                    "type": type(error).__name__,
                    "reason": getattr(error, "reason", None),
                    "message": str(error),
                    "line": last.lineno,
                }
            )
            raise

    def _validate_draft(self, run, draft, *, allow_prefix=False, allow_missing_backups=False):
        try:
            return super()._validate_draft(
                run,
                draft,
                allow_prefix=allow_prefix,
                allow_missing_backups=allow_missing_backups,
            )
        except Exception as error:
            last = traceback.extract_tb(error.__traceback__)[-1]
            self.validation_errors.append(
                {
                    "stage": "validate",
                    "type": type(error).__name__,
                    "reason": getattr(error, "reason", None),
                    "message": str(error),
                    "line": last.lineno,
                    "allow_prefix": allow_prefix,
                    "allow_missing_backups": allow_missing_backups,
                }
            )
            raise

    async def _submit(self, run, args, action_ref, terminal_only):
        attempt = {
            "terminal_only": terminal_only,
            "proposal": args.model_dump(mode="json"),
            "rejection": None,
        }
        self.submit_attempts.append(attempt)
        try:
            return await super()._submit(run, args, action_ref, terminal_only)
        except Exception as error:
            attempt["rejection"] = {
                "type": type(error).__name__,
                "reason": getattr(error, "reason", None),
                "message": str(error),
            }
            raise


def _operation(run_id: UUID, phase: WorkflowPhase, step: str) -> OperationId:
    return OperationId(run_id=run_id, phase=phase, logical_step=step)


def _confirmed() -> ConfirmedTravelRequirement:
    return ConfirmedTravelRequirement(
        outcome_type="confirmed",
        schema_version="1.0",
        requirement=TravelRequirement(
            destination="杭州",
            time_window=TravelTimeWindow(
                sightseeing_start_date="2026-09-18",
                sightseeing_end_date="2026-09-18",
                sightseeing_days=1,
                arrival=TravelTimeWindowArrival(
                    hub_name="杭州东站",
                    hub_type=HubType.RAILWAY_STATION,
                    local_datetime="2026-09-17 18:00",
                ),
                departure=TravelTimeWindowDeparture(
                    hub_name="杭州东站",
                    hub_type=HubType.RAILWAY_STATION,
                    local_datetime="2026-09-19 18:00",
                ),
            ),
            preferences=TravelPreferences(
                interests=[InterestTag.CULTURE, InterestTag.NATURE],
                pace=TravelPace.NORMAL,
                agent_decides_pace=False,
                hotel_preferences="西湖附近",
            ),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(),
        confirmation_summary_digest="sha256:" + "c" * 64,
    )


def _hotel_plan(run_id: UUID) -> HotelCandidateOperationIds:
    return HotelCandidateOperationIds(
        search_operation_ids=tuple(
            _operation(run_id, WorkflowPhase.HOTEL_SELECTION, f"live_hotel_search_{index}")
            for index in range(3)
        ),
        candidate_set_operation_id=_operation(
            run_id, WorkflowPhase.HOTEL_SELECTION, "live_hotel_candidates"
        ),
    )


def _research_plan(run_id: UUID) -> CandidateResearchOperationPlan:
    slots = tuple(
        ResearchIterationOperationIds(
            invocation_slot_index=index,
            action_operation_id=_operation(
                run_id, WorkflowPhase.CANDIDATE_RESEARCH, f"live_research_action_{index}"
            ),
            judgment_operation_id=_operation(
                run_id, WorkflowPhase.CANDIDATE_RESEARCH, f"live_research_judgment_{index}"
            ),
            candidate_set_operation_id=_operation(
                run_id, WorkflowPhase.CANDIDATE_RESEARCH, f"live_research_state_{index}"
            ),
            place_observation_operation_ids=tuple(
                _operation(
                    run_id,
                    WorkflowPhase.CANDIDATE_RESEARCH,
                    f"live_research_place_{index}_{ordinal}",
                )
                for ordinal in range(8)
            ),
        )
        for index in range(6)
    )
    return CandidateResearchOperationPlan(
        run_id=run_id,
        iteration_slots=slots,
        terminal_successor_operation_id=_operation(
            run_id, WorkflowPhase.CANDIDATE_RESEARCH, "live_research_terminal"
        ),
    )


def _planning_plan(run_id: UUID) -> ItineraryPlanningOperationPlan:
    return ItineraryPlanningOperationPlan(
        run_id=run_id,
        iteration_slots=tuple(
            PlanningIterationOperationIds(
                invocation_slot_index=index,
                action_operation_id=_operation(
                    run_id, WorkflowPhase.ITINERARY_PLANNING, f"live_plan_action_{index}"
                ),
                planning_state_operation_id=_operation(
                    run_id, WorkflowPhase.ITINERARY_PLANNING, f"live_plan_state_{index}"
                ),
            )
            for index in range(6)
        ),
        hub_geocode_operation_ids=(
            _operation(run_id, WorkflowPhase.ITINERARY_PLANNING, "live_plan_hub_arrival"),
            _operation(run_id, WorkflowPhase.ITINERARY_PLANNING, "live_plan_hub_departure"),
        ),
        matrix_operation_ids=tuple(
            _operation(run_id, WorkflowPhase.ITINERARY_PLANNING, f"live_plan_matrix_{index}")
            for index in range(12)
        ),
        direction_operation_ids=tuple(
            _operation(run_id, WorkflowPhase.ITINERARY_PLANNING, f"live_plan_direction_{index}")
            for index in range(24)
        ),
        terminal_action_operation_id=_operation(
            run_id, WorkflowPhase.ITINERARY_PLANNING, "live_plan_terminal_action"
        ),
        terminal_successor_operation_id=_operation(
            run_id, WorkflowPhase.ITINERARY_PLANNING, "live_plan_terminal"
        ),
    )


def _budget(run_id: UUID) -> InMemoryExecutionBudgetManager:
    return InMemoryExecutionBudgetManager(
        plan=ExecutionBudgetPlan(
            schema_version="1.0",
            run_id=run_id,
            limits=(
                ExecutionBudgetLimit(key=HOTEL_PLACE_CALL_BUDGET_KEY, limit=3),
                ExecutionBudgetLimit(key=CANDIDATE_RESEARCH_ITERATION_BUDGET_KEY, limit=6),
                ExecutionBudgetLimit(key=CANDIDATE_RESEARCH_PLACE_CALL_BUDGET_KEY, limit=48),
                ExecutionBudgetLimit(key=ITINERARY_PLANNING_ITERATION_BUDGET_KEY, limit=6),
                ExecutionBudgetLimit(
                    key=ITINERARY_PLANNING_DIRECTION_CALL_BUDGET_KEY, limit=24
                ),
            ),
        ),
        limits=ExecutionBudgetManagerLimits(max_operation_records=4096),
    )


def _time_text(interval) -> str:
    start = interval.start_at_utc.astimezone().strftime("%H:%M")
    end = interval.end_at_utc.astimezone().strftime("%H:%M")
    return f"{start}-{end}"


async def _qualify_once(run_number: int) -> dict[str, object]:
    run_id = uuid4()
    confirmed = _confirmed()
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
    budget = _budget(run_id)
    provider = _ObservedProvider(LangChainDeepSeekAdapter(load_settings()))
    runtime = DeepSeekRuntime(provider=provider, sleeper=_RealSleeper())

    try:
        async with create_baidu_map_runtime(
            settings=load_baidu_map_settings(), clock=SystemUtcClock()
        ) as map_runtime:
            observed_map = _ObservedMapAdapter(map_runtime.adapter)

            hotel_service = HotelCandidateSelectionService(
                observed_map, repository, budget, SystemUtcClock()
            )
            hotel_request = _hotel_plan(run_id)
            hotels = await hotel_service.discover_candidates(confirmed, hotel_request)
            if not isinstance(hotels, HotelCandidatesReady):
                discovery = (
                    await repository.get(
                        hotels.candidate_set_ref, expected_model=HotelDiscoveryArtifact
                    )
                ).content
                searches = [
                    (
                        await repository.get(ref, expected_model=HotelPlaceSearchArtifact)
                    ).content.evidence.model_dump(mode="json")
                    for ref in discovery.search_refs
                ]
                raise AssertionError(
                    json.dumps(
                        {
                            "outcome": hotels.model_dump(mode="json"),
                            "searches": searches,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )
            hotel_search_calls = observed_map.calls["search_places"]
            assert hotel_search_calls >= 1
            assert await hotel_service.discover_candidates(confirmed, hotel_request) == hotels
            assert observed_map.calls["search_places"] == hotel_search_calls

            selected_hotel = hotels.candidates[0]
            selection_request = HotelSelectionRequest(
                selection_operation_id=_operation(
                    run_id, WorkflowPhase.HOTEL_SELECTION, "live_hotel_selection"
                ),
                candidate_set_ref=hotels.candidate_set_ref,
                command=HotelSelectCommand(selected_uid=selected_hotel.uid),
            )
            selection = await hotel_service.record_selection(
                selection_request, active_candidate_set_ref=hotels.candidate_set_ref
            )
            assert isinstance(selection, HotelSelected), selection.model_dump(mode="json")
            assert (
                await hotel_service.record_selection(
                    selection_request, active_candidate_set_ref=hotels.candidate_set_ref
                )
                == selection
            )

            research_service = _ObservedResearchService(
                runtime=runtime,
                map_adapter=observed_map,
                artifact_repository=repository,
                budget_manager=budget,
            )
            research_request = CandidateResearchInitialRequest(
                confirmed=confirmed,
                hotel_selection_ref=selection.selection_ref,
                operation_plan=_research_plan(run_id),
            )
            try:
                research = await research_service.research_initial(research_request)
                research_counts = dict(observed_map.calls)
                research_llm_calls = provider.json_invocations
                research_replay = await research_service.research_initial(research_request)
            finally:
                await research_service.aclose()
            if not isinstance(research, CandidateResearchCompleted):
                model_output = None
                candidate_state_counts = None
                candidate_set_ref = getattr(research, "candidate_set_ref", None)
                if candidate_set_ref is not None:
                    candidate_set = (
                        await repository.get(
                            candidate_set_ref, expected_model=CandidateResearchArtifact
                        )
                    ).content
                    candidate_state_counts = dict(
                        Counter(
                            candidate.state.value
                            for candidate in candidate_set.record.state.candidates
                        )
                    )
                failure_ref = getattr(research, "failure_artifact_ref", None)
                if (
                    failure_ref is not None
                    and failure_ref.artifact_type == "candidate_research.model_action"
                ):
                    model_output = (
                        await repository.get(
                            failure_ref, expected_model=CandidateResearchModelActionArtifact
                        )
                    ).content.model_dump(mode="json")
                elif (
                    failure_ref is not None
                    and failure_ref.artifact_type == "candidate_research.model_judgment"
                ):
                    model_output = (
                        await repository.get(
                            failure_ref,
                            expected_model=CandidateResearchModelJudgmentArtifact,
                        )
                    ).content.model_dump(mode="json")
                raise AssertionError(
                    json.dumps(
                        {
                            "outcome": research.model_dump(mode="json"),
                            "action_rejections": research_service.action_rejections,
                                "internal_errors": research_service.internal_errors,
                                "candidate_state_counts": candidate_state_counts,
                            "model_output": model_output,
                            "map_calls": research_counts,
                            "json_invocations": research_llm_calls,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )
            assert research_replay == research
            assert observed_map.calls == research_counts
            assert provider.json_invocations == research_llm_calls
            assert research_llm_calls >= 2
            assert research_counts["search_places"] > hotel_search_calls
            assert research_counts["get_place_details"] >= 1

            research_artifact = (
                await repository.get(
                    research.candidate_set_ref, expected_model=CandidateResearchArtifact
                )
            ).content
            candidate_state = research_artifact.record.state
            assert candidate_state is not None
            candidate_names = {
                candidate.core.uid: candidate.core.name for candidate in candidate_state.candidates
            }

            planning_service = _ObservedPlanningService(
                runtime=runtime,
                map_adapter=observed_map,
                artifact_repository=repository,
                budget_manager=budget,
            )
            planning_request = ItineraryPlanningInitialRequest(
                confirmed=confirmed,
                hotel_selection_ref=selection.selection_ref,
                candidate_set_ref=research.candidate_set_ref,
                operation_plan=_planning_plan(run_id),
            )
            try:
                planning = await planning_service.plan_initial(planning_request)
                final_counts = dict(observed_map.calls)
                final_json_calls = provider.json_invocations
                final_tool_calls = provider.tool_invocations
                planning_replay = await planning_service.plan_initial(planning_request)
            finally:
                await planning_service.aclose()

            assert isinstance(planning, ItineraryPlanningCompleted), json.dumps(
                {
                    "outcome": planning.model_dump(mode="json"),
                    "action_batches": planning_service.action_batches,
                    "action_rejections": planning_service.action_rejections,
                    "submit_attempts": planning_service.submit_attempts,
                    "validation_errors": planning_service.validation_errors,
                    "map_calls": final_counts,
                    "tool_invocations": final_tool_calls,
                },
                ensure_ascii=False,
                default=str,
            )
            assert planning_replay == planning
            assert observed_map.calls == final_counts
            assert provider.json_invocations == final_json_calls
            assert provider.tool_invocations == final_tool_calls
            assert final_tool_calls >= 1
            assert final_counts["calculate_route_matrix"] >= 1
            assert final_counts["plan_directions"] >= 1

            itinerary_artifact = (
                await repository.get(
                    planning.itinerary_ref, expected_model=ItineraryPlanningArtifact
                )
            ).content
            draft = itinerary_artifact.record.draft
            assert draft is not None
            assert len(draft.days) == 1
            day = draft.days[0]
            attraction_visits = tuple(
                item for item in day.visits if item.visit_kind is VisitKind.ATTRACTION
            )
            meal_visits = tuple(
                item
                for item in day.visits
                if item.visit_kind in {VisitKind.LUNCH, VisitKind.DINNER}
            )
            assert len(attraction_visits) == 3
            assert {item.visit_kind for item in meal_visits} == {
                VisitKind.LUNCH,
                VisitKind.DINNER,
            }
            assert len({item.location.uid for item in meal_visits}) == 2
            assert all(item.location.uid in candidate_names for item in day.visits)

            return {
                "qualification_run": run_number,
                "model": "deepseek-v4-flash",
                "hotel": {
                    "selected": selected_hotel.name,
                    "address": selected_hotel.address,
                    "candidate_count": len(hotels.candidates),
                },
                "candidate_research": {
                    "eligible": research.counts.unique_eligible_count,
                    "attractions": research.counts.attraction_count,
                    "restaurants": research.counts.restaurant_count,
                    "details": research.coverage.details_success_count,
                    "iterations": research.budgets.iterations_consumed,
                },
                "itinerary": {
                    "date": str(day.date),
                    "theme": day.theme,
                    "visits": [
                        {
                            "time": _time_text(item.interval),
                            "kind": item.visit_kind.value,
                            "name": candidate_names[item.location.uid],
                        }
                        for item in day.visits
                    ],
                    "route_legs": [
                        {
                            "time": _time_text(item.interval),
                            "mode": item.transport_semantic.value,
                            "distance_m": item.distance_m,
                            "duration_s": item.duration_s,
                        }
                        for item in day.route_legs
                    ],
                    "backups": [candidate_names[item.candidate.uid] for item in day.backups],
                },
                "live_calls": final_counts,
                "llm_calls": {
                    "candidate_research": research_llm_calls,
                    "itinerary_planning": final_tool_calls,
                },
            }
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_modules_07_08_09_complete_one_real_network_run() -> None:
    run_count = int(os.environ.get("MODULE070809_LIVE_RUNS", "1"))
    assert run_count == 1
    reports = []
    for run_number in range(1, run_count + 1):
        report = await _qualify_once(run_number)
        reports.append(report)
        print("MODULE070809_LIVE_RESULT=" + json.dumps(report, ensure_ascii=False))
    assert len(reports) == 1
    assert all(report["model"] == "deepseek-v4-flash" for report in reports)
