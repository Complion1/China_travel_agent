"""Synthetic, strictly persisted M01/M07/M08/M09 evidence for Module 12.

Only public upstream Artifact classes are constructed.  Nested JSON payloads
are validated by their actual registered models; no parallel DTO definitions
or production-private conversion helpers are used here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.candidate_research import (
    CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,
    CandidateResearchArtifact,
    CandidateResearchPlaceObservationArtifact,
)
from app.hotel_selection import (
    HOTEL_ARTIFACT_REGISTRATIONS,
    HotelDiscoveryArtifact,
    HotelPlaceSearchArtifact,
    HotelSelectionArtifact,
)
from app.itinerary_planning import (
    ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS,
    ItineraryPlanningArtifact,
    ItineraryPlanningDirectionsObservationArtifact,
    ItineraryPlanningHubGeocodeObservationArtifact,
    ItineraryPlanningModelActionArtifact,
    ItineraryPlanningRouteMatrixObservationArtifact,
)
from app.requirements import (
    ConfirmedTravelRequirement,
    RequirementConfirmation,
    RequirementDomainService,
    RequirementDomainState,
    TravelRequirement,
)
from app.workflow_contracts import (
    ArtifactReadResult,
    ArtifactRef,
    ArtifactRepository,
    ArtifactSchemaRegistry,
    ArtifactWriteRequest,
    DEMO_ARTIFACT_REPOSITORY_LIMITS,
    DeliveryStatus,
    InMemoryArtifactRepository,
    OperationId,
    TravelSessionState,
    WorkflowPhase,
)
from tests.support.hotel_selection_fakes import FixedClock, SequenceUUIDFactory
from tests.unit.requirements.fakes import (
    FixedClock as RequirementClock,
    InMemoryEvidenceSink,
    RecordingRequirementExtractor,
)


RUN_ID = UUID("12000000-0000-4000-8000-000000000001")
TRACE_ID = UUID("12000000-0000-4000-8000-000000000002")
SOURCE_TIME = datetime(2026, 8, 20, 2, 3, 4, tzinfo=timezone.utc)
WRITE_TIME = datetime(2026, 8, 21, 5, 6, 7, tzinfo=timezone.utc)
START_DATE = date(2026, 9, 3)
SHANGHAI = ZoneInfo("Asia/Shanghai")
ZERO_USAGE = dict(prompt_tokens=0, completion_tokens=0, total_tokens=0,
                  reasoning_tokens=0, prompt_cache_hit_tokens=0,
                  prompt_cache_miss_tokens=0)


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def tree(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude_none=False, by_alias=False,
                            exclude_computed_fields=True)


def parsed(model: type[BaseModel], payload: dict[str, Any]) -> Any:
    return model.model_validate_json(encode(payload), strict=True)


def make_registry() -> ArtifactSchemaRegistry:
    from app.result_formatting import RESULT_FORMATTING_ARTIFACT_REGISTRATIONS

    return ArtifactSchemaRegistry((
        *HOTEL_ARTIFACT_REGISTRATIONS,
        *CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,
        *ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS,
        *RESULT_FORMATTING_ARTIFACT_REGISTRATIONS,
    ))


@dataclass
class Scenario:
    request: Any
    itinerary: ItineraryPlanningArtifact
    evidence: dict[UUID, ArtifactReadResult]
    repository: ArtifactRepository
    refs: dict[str, ArtifactRef]


async def make_confirmed(days: int = 1):
    """Use real M01 confirmation; its digest deliberately has no prefix."""
    last = START_DATE + timedelta(days=days - 1)
    arrival = START_DATE - timedelta(days=1)
    departure = last + timedelta(days=1)
    requirement = parsed(TravelRequirement, {
        "destination": "杭州",
        "time_window": {
            "arrival": {"hub_name": "杭州东站", "hub_type": "railway_station",
                        "local_datetime": f"{arrival} 18:00", "is_approximate": False},
            "departure": {"hub_name": "杭州萧山机场", "hub_type": "airport",
                          "local_datetime": f"{departure} 18:00", "is_approximate": True},
            "sightseeing_start_date": str(START_DATE), "sightseeing_end_date": str(last),
            "sightseeing_days": days, "hotel_check_in_date": str(arrival),
            "hotel_check_out_date": str(departure), "hotel_nights": days + 1,
        },
        "budget": {"total_cny": 10000.5, "is_approximate": True, "scope": "destination_only"},
        "party": {"adults_count": 2, "children_count": 1, "children_ages": [8],
                  "seniors_count": 0, "total_people": 3},
        "rooms": {"room_count": 1, "room_type_requirements": "家庭房"},
        "constraints": {"mobility_constraints": "无", "dietary_restrictions": "不吃辣"},
        "preferences": {"interests": ["人文", "自然"], "pace": "标准",
                        "agent_decides_interests": False, "agent_decides_pace": False,
                        "hotel_preferences": "安静", "transport_preferences": "少换乘",
                        "daily_time_preferences": "晚一点出发", "free_text_notes": "带儿童同行",
                        "must_visit_places": [], "avoid_places": []},
    })
    state = RequirementDomainState(requirement=requirement,
                                   confirmation_status="awaiting_confirmation")
    service = RequirementDomainService(
        extractor=RecordingRequirementExtractor([]), evidence_sink=InMemoryEvidenceSink(),
        clock=RequirementClock(date(2026, 8, 20)),
    )
    transition = await service.confirm(
        state, RequirementConfirmation(decision="accept", revision_text=None),
    )
    assert isinstance(transition.outcome, ConfirmedTravelRequirement)
    assert len(transition.outcome.confirmation_summary_digest) == 64
    return transition


def coordinate(ordinal: int = 0, *, versioned: bool = False) -> dict[str, Any]:
    value = {"coordinate_system": "bd09ll", "latitude": 30.2 + ordinal / 10000,
             "longitude": 120.1 + ordinal / 10000}
    return {"schema_version": "1.0", **value} if versioned else value


def local_time(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


def interval(start: datetime, end: datetime) -> dict[str, Any]:
    return {"schema_version": "1.0", "start_at_utc": start.astimezone(timezone.utc).isoformat(),
            "end_at_utc": end.astimezone(timezone.utc).isoformat(), "timezone": "Asia/Shanghai",
            "local_start_date": str(start.date()), "local_end_date": str(end.date())}


def provenance(capability: str, *, planning: bool = False) -> dict[str, Any]:
    value = {"source": "baidu_maps_mcp", "capability": capability, "verified": True,
             "retrieved_at_utc" if planning else "retrieved_at": SOURCE_TIME.isoformat()}
    return {"schema_version": "1.0", **value} if planning else value


async def make_scenario(
    repository: ArtifactRepository | None = None, *, days: int = 1,
    resume: bool = False, transit_only: bool = False, cross_midnight: bool = False,
    zero_route: bool = False, run_id: UUID = RUN_ID,
) -> Scenario:
    """Persist complete evidence; return request, itinerary, evidence and repository.

    Evidence is keyed by artifact_id and contains actual Repository.get results.
    A supplied repository must use make_registry(), making this same scenario
    usable by M10 integration workers.  All IDs, dates and text are synthetic.
    """
    if type(days) is not int or not 1 <= days <= 60:
        raise ValueError("scenario days must be between one and sixty")
    if repository is None:
        repository = InMemoryArtifactRepository(
            registry=make_registry(), utc_clock=FixedClock(WRITE_TIME),
            uuid_factory=SequenceUUIDFactory(start=12000), limits=DEMO_ARTIFACT_REPOSITORY_LIMITS,
        )
    evidence: dict[UUID, ArtifactReadResult] = {}
    refs: dict[str, ArtifactRef] = {}

    def operation(step: str, phase: WorkflowPhase) -> OperationId:
        return OperationId(run_id=run_id, phase=phase, logical_step=step)

    async def save(key: str, model: type[BaseModel], artifact_type: str,
                   phase: WorkflowPhase, payload: dict[str, Any]) -> ArtifactRef:
        artifact = parsed(model, payload)
        ref = await repository.put(ArtifactWriteRequest(
            operation_id=operation(key, phase), artifact_type=artifact_type,
            schema_version=artifact.schema_version, content=tree(artifact),
        ))
        evidence[ref.artifact_id] = await repository.get(ref, expected_model=model)
        refs[key] = ref
        return ref

    confirmation = await make_confirmed(days)
    confirmed = confirmation.outcome
    requirement_digest = digest(tree(confirmed))
    hotel_candidates = [{"schema_version": "1.0", "uid": f"hotel-{i}",
                         "name": f"合成酒店{i}", "address": f"合成酒店路{i}号",
                         "coordinate": coordinate(i, versioned=True), "category": "酒店"}
                        for i in range(3)]
    search = await save("hotel_search", HotelPlaceSearchArtifact, "hotel.place_search",
                        WorkflowPhase.HOTEL_SELECTION, {
        "schema_version": "1.0", "search_ordinal": 1, "request_hash": digest("hotel-search"),
        "source": "baidu_maps_mcp", "verified": True, "retrieved_at": SOURCE_TIME.isoformat(),
        "evidence": {"schema_version": "1.0", "outcome_type": "success", "places": hotel_candidates},
    })
    discovery = await save("hotel_discovery", HotelDiscoveryArtifact, "hotel.candidate_set",
                           WorkflowPhase.HOTEL_SELECTION, {
        "schema_version": "1.0", "confirmation_summary_digest": confirmed.confirmation_summary_digest,
        "query_plan_hash": digest("hotel-query"), "search_operation_ids": [tree(search.operation_id),
            tree(operation("hotel_search_unused_2", WorkflowPhase.HOTEL_SELECTION)),
            tree(operation("hotel_search_unused_3", WorkflowPhase.HOTEL_SELECTION))],
        "search_refs": [tree(search)], "result": {"schema_version": "1.0", "result_type": "ready",
                                                     "candidates": hotel_candidates},
    })
    hotel = await save("hotel_selection", HotelSelectionArtifact, "hotel.selection",
                      WorkflowPhase.HOTEL_SELECTION, {
        "schema_version": "1.0", "candidate_set_ref": tree(discovery),
        "decision": {"schema_version": "1.0", "decision_type": "selected",
                     "selected_candidate": hotel_candidates[0]},
    })

    candidate_count = 2 * days + 4
    cores = [{"uid": f"candidate-{i}", "name": f"合成景点餐厅{i}", "address": f"合成游览路{i}号",
              "coordinate": coordinate(i + 10), "provider_category": "旅游景点;餐饮"}
             for i in range(candidate_count)]
    observation_refs = []
    for offset in range(0, candidate_count, 50):
        places = cores[offset:offset + 50]
        index = len(observation_refs)
        observation_refs.append(await save(
            f"candidate_search_{index}", CandidateResearchPlaceObservationArtifact,
            "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH, {
                "schema_version": "1.0", "run_id": str(run_id), "mode": "initial",
                "invocation_slot_index": 0, "iteration_index": 0, "action_ordinal": index,
                "tool": "search_places", "action_fingerprint": digest(f"candidate-search-{index}"),
                "result": {"record_type": "search_success", "places": places,
                    "admission_dispositions": [{"result_ordinal": i, "uid": value["uid"],
                                                "disposition": "admitted_new"}
                                               for i, value in enumerate(places)],
                    "provenance": provenance("place_search")},
            }))
    details_index = len(observation_refs)
    observation_refs.append(await save(
        "candidate_details", CandidateResearchPlaceObservationArtifact,
        "candidate_research.place_observation", WorkflowPhase.CANDIDATE_RESEARCH, {
            "schema_version": "1.0", "run_id": str(run_id), "mode": "initial",
            "invocation_slot_index": 0, "iteration_index": 0, "action_ordinal": details_index,
            "tool": "get_place_details", "action_fingerprint": digest("candidate-details"),
            "result": {"record_type": "details_success", "details": {**cores[0],
                       "brand": None, "telephone": None, "rating": None,
                       "business_hours": ["09:00-21:00"]}, "provenance": provenance("place_details")},
        }))
    candidates = []
    for i, core in enumerate(cores):
        source_index = i // 50
        candidates.append({
            "schema_version": "1.0", "admission_ordinal": i, "core": core, "state": "eligible",
            "roles": [{"role": role, "verdict": "supported", "reason_code": "provider_category_supports",
                       "evidence_indexes": [source_index]} for role in ("attraction", "restaurant")],
            "interests": ["人文", "自然"], "meal_intents": ["lunch", "dinner"],
            "must_matches": [], "avoid_matches": [],
            "details": {"status": "success" if i == 0 else "not_requested",
                        "observation_index": details_index if i == 0 else None},
            "observation_indexes": [source_index, details_index] if i == 0 else [source_index],
            "dietary_restrictions": "not_evaluated", "mobility_constraints": "not_evaluated",
            "budget_fit": "not_evaluated",
        })
    counts = dict(attraction_count=candidate_count, attraction_target=min(60, 2 * days),
                  restaurant_count=candidate_count, restaurant_target=min(40, days),
                  unique_eligible_count=candidate_count, admitted_slots=candidate_count, candidate_capacity=128)
    coverage = dict(effective_interests=["人文", "自然"], covered_interests=[], covered_meals=[],
                    must_item_count=0, resolved_must_item_count=0, details_success_count=1,
                    details_target=(candidate_count + 2) // 3,
                    initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False)
    research_budgets = dict(iteration_limit=6, iterations_consumed=1, place_call_limit=48,
                           place_calls_consumed=len(observation_refs), semantic_invocation_limit=12,
                           semantic_invocations_consumed=1, known_provider_attempts=1,
                           provider_attempts_complete=True, known_usage=ZERO_USAGE, usage_complete=True)
    gap = {"gap_type": "candidate_details_missing", "gap_id": "gap:remaining_details",
           "evidence_indexes": [0], "candidate_uids": [cores[1]["uid"]]}
    candidate_state = dict(confirmed_requirement_digest=requirement_digest, hotel_selection_ref=tree(hotel),
                          effective_interests=["人文", "自然"], model_action_refs=[], model_judgment_refs=[],
                          observation_refs=[tree(ref) for ref in observation_refs], candidates=candidates,
                          requirement_group_states=[], search_coverage=dict(records=[],
                          initial_city_attraction_search_done=False, initial_hotel_restaurant_nearby_done=False),
                          counts=counts, coverage=coverage, budgets=research_budgets,
                          pending_gaps=[gap], iteration_no_progress_streak=0)
    candidate_set = await save("candidate_set", CandidateResearchArtifact, "candidate_research.candidate_set",
                               WorkflowPhase.CANDIDATE_RESEARCH, {
        "schema_version": "1.0", "run_id": str(run_id), "mode": "initial", "predecessor_ref": None,
        "invocation_request_digest": digest("research-request"), "record": {
            "record_type": "incomplete", "transition_kind": "normal", "state": candidate_state,
            "counts": counts, "coverage": coverage, "budgets": research_budgets,
            "gaps": [gap], "stop_reasons": ["useful_supply_not_found"]},
    })

    planning_digest = digest("planning-original-request")
    action = await save("planning_action", ItineraryPlanningModelActionArtifact,
                        "itinerary_planning.model_action", WorkflowPhase.ITINERARY_PLANNING, {
        "schema_version": "3.0", "request_digest": planning_digest, "invocation_index": 0,
        "prompt_version": "planning-v3", "prompt_hash": digest("synthetic-prompt"),
        "action_kind": "tool_calls", "call_summaries": [{"schema_version": "1.0",
            "call_id": "synthetic-geocode", "ordinal": 0, "tool": "geocode_hub",
            "arguments_fingerprint": digest("arguments"), "handle_count": 0,
            "handles_digest": digest([]), "handle_preview": []}],
        "invocation_metadata": {"role": "itinerary_planning", "model_name": "deepseek-v4-flash",
            "finish_reason": "tool_calls", "provider_attempts": 1, "possible_duplicate": False,
            "usage": ZERO_USAGE}, "disposition": "accepted",
    })
    base_observation = dict(schema_version="1.0", request_digest=planning_digest,
                            model_action_ref=tree(action), action_ordinal=0,
                            observation_status="success", map_failure=None)
    hub_refs = []
    for index, endpoint in enumerate(("arrival", "departure")):
        hub_refs.append(await save(
            f"hub_{endpoint}", ItineraryPlanningHubGeocodeObservationArtifact,
            "itinerary_planning.hub_geocode_observation", WorkflowPhase.ITINERARY_PLANNING, {
                **base_observation, "endpoint_kind": endpoint, "request_fingerprint": digest(endpoint),
                "success_candidate": {"schema_version": "1.0", "formatted_address": f"合成{endpoint}枢纽地址",
                                      "coordinate": coordinate(200 + index, versioned=True), "confidence": 0.9},
                "ambiguous_candidates": [], "provenance": provenance("geocoding", planning=True),
            }))
    hotel_location = {"schema_version": "1.0", "ref_type": "hotel", "hotel_selection_ref_index": 0}
    hubs = [{"schema_version": "1.0", "ref_type": "hub", "endpoint_kind": endpoint,
             "geocode_observation_index": i} for i, endpoint in enumerate(("arrival", "departure"))]
    locations = [{"schema_version": "1.0", "ref_type": "candidate", "admission_ordinal": i,
                  "uid": core["uid"]} for i, core in enumerate(cores)]
    direction_refs = []

    async def route(key: str, origin: dict, destination: dict, start: datetime,
                    *, zero: bool = False) -> dict[str, Any]:
        index = len(direction_refs)
        duration, distance, minutes = (0, 0, 0) if zero else (601, 1201, 30)
        direction_refs.append(await save(
            key, ItineraryPlanningDirectionsObservationArtifact, "itinerary_planning.directions_observation",
            WorkflowPhase.ITINERARY_PLANNING, {**base_observation, "leg_ordinal": index,
                "leg_fingerprint": digest(key), "origin": origin, "destination": destination,
                "transport_semantic": "walking", "route_mode": "walking", "alternatives_limit": 1,
                "alternatives": [{"schema_version": "1.0", "alternative_index": 0,
                                  "distance_m": distance, "duration_s": duration, "step_count": 1}],
                "provenance": provenance("directions", planning=True)}))
        return {"schema_version": "1.0", "leg_id": key, "origin": origin, "destination": destination,
                "interval": interval(start, start + timedelta(minutes=minutes)), "transport_semantic": "walking",
                "route_mode": "walking", "directions_observation_index": index, "alternative_index": 0,
                "distance_m": distance, "duration_s": duration, "scheduled_minutes": minutes}

    arrival_route = await route("arrival_route", hubs[0], hotel_location,
                                local_time(START_DATE - timedelta(days=1), 18, 30))
    matrix_refs = []
    plan_days = []
    for day_index in range(days):
        current = START_DATE + timedelta(days=day_index)
        if transit_only:
            plan_days.append(dict(schema_version="1.0", date=str(current), day_kind="transit_only",
                                  theme="抵离交通", open_intervals=[], visits=[], route_legs=[], backups=[]))
            continue
        main_indexes = [2 * day_index, 2 * day_index + 1, 2 * days]
        kinds = ["attraction_lunch", "attraction", "dinner"]
        windows = [(10, 12), (13, 15), (18, 19)]
        visits = [{"schema_version": "1.0", "visit_id": f"day_{day_index + 1}_visit_{i + 1}",
                   "location": locations[ordinal], "visit_kind": kinds[i],
                   "interval": interval(local_time(current, start), local_time(current, end)),
                   "duration_source": "planning_estimate", "must_group_ids": [], "time_sensitive_anchor_ids": [],
                   "business_hours_state": "compatible" if ordinal == 0 else "unknown",
                   "business_evidence_indexes": [details_index] if ordinal == 0 else [],
                   "requires_verification": ordinal != 0}
                  for i, (ordinal, (start, end)) in enumerate(zip(main_indexes, windows, strict=True))]
        route_points = [local_time(current, 9, 30), local_time(current, 12), local_time(current, 17, 30),
                        local_time(current + timedelta(days=1), 0) if cross_midnight else local_time(current, 19)]
        path = [hotel_location, *(locations[i] for i in main_indexes), hotel_location]
        legs = [await route(f"day_{day_index + 1}_route_{i + 1}", path[i], path[i + 1], point,
                            zero=zero_route and i == 3) for i, point in enumerate(route_points)]
        # Deliberately asymmetric core->backup and backup->core distances.
        matrix_locations = [locations[main_indexes[0]], locations[main_indexes[2]],
                            *(locations[2 * days + i] for i in (1, 2, 3))]
        matrix_index = len(matrix_refs)
        cells = [{"schema_version": "1.0", "status": "success", "origin_index": i,
                  "destination_index": j, "distance_m": 0 if i == j else 400 + i * 100 + j,
                  "duration_s": 0 if i == j else 120}
                 for i in range(5) for j in range(5)]
        matrix_refs.append(await save(
            f"day_{day_index + 1}_matrix", ItineraryPlanningRouteMatrixObservationArtifact,
            "itinerary_planning.route_matrix_observation", WorkflowPhase.ITINERARY_PLANNING,
            {**base_observation, "date": str(current), "mode": "walking", "request_fingerprint": digest(str(current)),
             "ordered_locations": matrix_locations, "cells": cells, "provenance": provenance("route_matrix", planning=True)}))
        backups = [{"schema_version": "1.0", "backup_kind": role,
                    "core_visit_id": visits[2 if role == "dinner" else 0]["visit_id"],
                    "candidate": locations[2 * days + i + 1], "matrix_observation_index": matrix_index,
                    "matrix_mode": "walking", "distance_m": 400 + (100 if role == "dinner" else 0) + i + 2,
                    "business_hours_state": "unknown", "business_evidence_indexes": [], "requires_verification": True}
                   for i, role in enumerate(("attraction", "lunch", "dinner"))]
        end = local_time(current + timedelta(days=1), 0, 30) if cross_midnight else local_time(current, 22)
        plan_days.append(dict(schema_version="1.0", date=str(current), day_kind="full", theme=f"第{day_index + 1}天合成游览",
                              open_intervals=[interval(local_time(current, 7, 30), end)],
                              visits=visits, route_legs=legs, backups=backups))
    departure_route = await route("departure_route", hotel_location, hubs[1],
                                  local_time(START_DATE + timedelta(days=days), 15))
    directories = dict(schema_version="1.0", hotel_selection_refs=[tree(hotel)],
                       candidate_set_refs=[tree(candidate_set)], model_action_refs=[tree(action)],
                       hub_geocode_refs=[tree(ref) for ref in hub_refs], matrix_refs=[tree(ref) for ref in matrix_refs],
                       directions_refs=[tree(ref) for ref in direction_refs],
                       candidate_evidence_refs=[tree(ref) for ref in observation_refs])
    transfers = [{"schema_version": "1.0", "transfer_kind": kind, "hub": hubs[i], "hotel": hotel_location,
                  "route_leg": leg, "processing_minutes": 30 if i == 0 else 120,
                  "approximate_extra_minutes": 0 if i == 0 else 30}
                 for i, (kind, leg) in enumerate((("arrival", arrival_route), ("departure", departure_route)))]
    draft = dict(schema_version="1.0", requirement_digest=requirement_digest, hotel_selection_ref=tree(hotel),
                 candidate_set_ref=tree(candidate_set), effective_policy=dict(schema_version="1.0", effective_pace="标准",
                 daily_time_rules=[], time_sensitive_must=[], transport_rules=[], locked=True),
                 arrival_transfer=transfers[0], days=plan_days, departure_transfer=transfers[1], upstream_gap_ids=[])
    itinerary_ref = await save("itinerary", ItineraryPlanningArtifact, "itinerary_planning.itinerary",
                                WorkflowPhase.ITINERARY_PLANNING, {
        "schema_version": "1.0", "request_digest": digest("planning-resume-request") if resume else planning_digest,
        "parent_planning_ref": None, "directories": directories, "record": {
            "schema_version": "1.0", "record_type": "completed", "draft": draft,
            "summary": dict(schema_version="1.0", effective_pace="标准", sightseeing_days=days,
                full_days=0 if transit_only else days, partial_days=0, transit_only_days=days if transit_only else 0,
                attraction_count=0 if transit_only else 2 * days, lunch_count=0 if transit_only else days,
                dinner_count=0 if transit_only else days, unique_main_candidates=0 if transit_only else 2 * days + 1,
                boundary_transfers=2, route_legs=len(direction_refs), backups=0 if transit_only else 3 * days,
                unknown_hours=0 if transit_only else 6 * days - 1),
            "budgets": dict(schema_version="1.0", iteration_limit=6, iterations_consumed=1,
                direction_call_limit=8 * days + 2, direction_calls_consumed=len(direction_refs),
                budget_snapshot_complete=True, semantic_invocation_limit=12, semantic_invocations_consumed=1,
                known_provider_attempts=1, provider_attempts_complete=True, known_usage=ZERO_USAGE,
                usage_complete=True, cache_hits=0)},
    })
    state = TravelSessionState(schema_version="2.0", run_id=run_id, trace_id=TRACE_ID,
        phase=WorkflowPhase.ITINERARY_PLANNING, delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code=None, terminal_outcome=None, requirement_state=confirmation.state,
        requirement_outcome=confirmed, artifact_refs=(itinerary_ref,))
    from app.result_formatting import ResultFormattingRequest

    request = ResultFormattingRequest(schema_version="1.0", state=state, itinerary_ref=itinerary_ref)
    return Scenario(request=request, itinerary=evidence[itinerary_ref.artifact_id].content,
                    evidence=evidence, repository=repository, refs=refs)


async def replace_itinerary(scenario: Scenario, payload: dict[str, Any],
                            *, logical_step: str = "itinerary_variant") -> Scenario:
    """Persist a structurally valid but possibly inconsistent terminal for negatives."""
    artifact = parsed(ItineraryPlanningArtifact, payload)
    ref = await scenario.repository.put(ArtifactWriteRequest(
        operation_id=OperationId(run_id=scenario.request.state.run_id,
                                 phase=WorkflowPhase.ITINERARY_PLANNING,
                                 logical_step=logical_step),
        artifact_type="itinerary_planning.itinerary", schema_version="1.0", content=tree(artifact),
    ))
    read = await scenario.repository.get(ref, expected_model=ItineraryPlanningArtifact)
    state = TravelSessionState.model_validate(
        {**scenario.request.state.model_dump(mode="python"), "artifact_refs": (ref,)}, strict=True,
    )
    request = type(scenario.request)(schema_version="1.0", state=state, itinerary_ref=ref)
    return replace(scenario, request=request, itinerary=read.content,
                   evidence={**scenario.evidence, ref.artifact_id: read},
                   refs={**scenario.refs, "itinerary": ref})


class RecordingReadRepository:
    """Borrowed repository spy; writes and lifecycle operations are forbidden.

    Faults happen at the public get boundary. The backing repository remains a
    real M04 repository, and callers save exclusively through that backing port.
    """

    def __init__(self, repository: ArtifactRepository, *, error=None,
                 fail_ref: ArtifactRef | None = None, replacement=None) -> None:
        self.repository = repository
        self.error = error
        self.fail_ref = fail_ref
        self.replacement = replacement
        self.get_calls: list[tuple[ArtifactRef, type[BaseModel]]] = []
        self.forbidden_calls: list[str] = []

    async def get(self, ref, *, expected_model):
        self.get_calls.append((ref, expected_model))
        if self.fail_ref is None or ref == self.fail_ref:
            if self.error is not None:
                raise self.error
            if self.replacement is not None:
                return self.replacement(ref, expected_model)
        return await self.repository.get(ref, expected_model=expected_model)

    async def put(self, request):
        self.forbidden_calls.append("put")
        raise AssertionError("result formatter must not write")

    async def lookup(self, *, operation_id):
        self.forbidden_calls.append("lookup")
        raise AssertionError("result formatter must not lookup")

    async def aclose(self):
        self.forbidden_calls.append("aclose")
        raise AssertionError("result formatter does not own the repository")

    def close(self):
        self.forbidden_calls.append("close")
        raise AssertionError("result formatter does not own the repository")


async def save_result_days(scenario: Scenario, days, *, prefix="result"):
    """M13-like external save harness; not a claim of M11 journal integration."""
    from app.result_formatting import build_result_artifact_write_request

    refs = []
    for day in days:
        refs.append(await scenario.repository.put(build_result_artifact_write_request(
            operation_id=OperationId(run_id=scenario.request.state.run_id,
                phase=WorkflowPhase.OUTPUT_FORMATTING,
                logical_step=f"{prefix}_day_{day.data.day_no:02d}"),
            artifact=day,
        )))
    return tuple(refs)


async def save_result_index(scenario: Scenario, index, *, logical_step="result_index"):
    from app.result_formatting import build_result_artifact_write_request

    return await scenario.repository.put(build_result_artifact_write_request(
        operation_id=OperationId(run_id=scenario.request.state.run_id,
            phase=WorkflowPhase.OUTPUT_FORMATTING, logical_step=logical_step),
        artifact=index,
    ))


def make_read_request(scenario: Scenario, index_ref: ArtifactRef, *, include_source=True):
    from app.result_formatting import ResultReadRequest

    refs = (scenario.request.itinerary_ref, index_ref) if include_source else (index_ref,)
    state = TravelSessionState.model_validate({
        **scenario.request.state.model_dump(mode="python"),
        "phase": WorkflowPhase.OUTPUT_FORMATTING, "artifact_refs": refs,
    }, strict=True)
    return ResultReadRequest(schema_version="1.0", state=state, index_ref=index_ref)
