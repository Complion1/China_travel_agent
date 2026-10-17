from datetime import date, datetime, timezone
from types import UnionType
from typing import Annotated, Union, get_args, get_origin
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from app.itinerary_planning import (
    ItineraryPlanningArtifact,
    ItineraryPlanningDirectionsObservationArtifact,
    ItineraryPlanningHubGeocodeObservationArtifact,
    ItineraryPlanningModelActionArtifact,
    ItineraryPlanningRouteMatrixObservationArtifact,
    BusinessHoursState,
    PlanningFailureCode,
    PlanningDayKind,
    PlanningStopReason,
    TransportSemantic,
    VisitKind,
    ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS,
)
from app.baidu_map import MatrixTravelMode, TravelMode
from app.requirements import TravelPace
from app.itinerary_planning import artifacts as local
from app.llm_runtime import InvocationMetadata, LLMUsage, ModelRole
from app.workflow_contracts import (
    ArtifactRef,
    ArtifactRequestValidationError,
    ArtifactSchemaRegistry,
    ArtifactWriteRequest,
    OperationId,
    WorkflowPhase,
)


RUN_ID = UUID("12345678-1234-5678-1234-567812345678")
UTC = datetime(2026, 9, 3, 2, 0, tzinfo=timezone.utc)
DIGEST = "sha256:" + "a" * 64


def _op(step: str, phase: WorkflowPhase = WorkflowPhase.ITINERARY_PLANNING, run_id: UUID = RUN_ID) -> OperationId:
    return OperationId(run_id=run_id, phase=phase, logical_step=step)


def _ref(number: int, artifact_type: str, phase: WorkflowPhase, step: str, run_id: UUID = RUN_ID, schema_version: str | None = None) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=UUID(int=number), artifact_type=artifact_type, schema_version=schema_version or ("9.0" if artifact_type == "itinerary_planning.model_action" else "6.0" if artifact_type == "itinerary_planning.itinerary" else "4.0" if artifact_type == "candidate_research.candidate_set" else "1.0"),
        content_hash=DIGEST, operation_id=_op(step, phase, run_id), created_at=UTC, size_bytes=1,
    )


def _directories() -> local.ArtifactRefDirectories:
    return local.ArtifactRefDirectories(
        schema_version="5.0",
        hotel_selection_refs=(_ref(1, "hotel.selection", WorkflowPhase.HOTEL_SELECTION, "hotel"),),
        candidate_set_refs=(_ref(2, "candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH, "candidate_set"),),
        model_action_refs=(_ref(3, "itinerary_planning.model_action", WorkflowPhase.ITINERARY_PLANNING, "model_action"),),
        hub_geocode_refs=(_ref(4, "itinerary_planning.hub_geocode_observation", WorkflowPhase.ITINERARY_PLANNING, "hub"),),
        matrix_refs=(),
        directions_refs=(_ref(5, "itinerary_planning.directions_observation", WorkflowPhase.ITINERARY_PLANNING, "directions"),),
        candidate_evidence_refs=(),
    )


def _budget() -> local.PlanningBudgetSummarySnapshot:
    return local.PlanningBudgetSummarySnapshot(
        schema_version='6.0', model_invocation_limit=19, model_invocations_consumed=0, direction_call_limit=2,
        normal_invocation_limit=17, format_repair_limit=2, format_repair_used=0,
        direction_calls_consumed=0, budget_snapshot_complete=True, semantic_invocation_limit=19,
        semantic_invocations_consumed=0, known_provider_attempts=0, provider_attempts_complete=True,
        known_usage=LLMUsage(
            prompt_tokens=1, completion_tokens=0, total_tokens=1, reasoning_tokens=0,
            prompt_cache_hit_tokens=1, prompt_cache_miss_tokens=0,
        ), usage_complete=True, cache_hits=0,
        preparation_limit=2, preparation_used=min(2, 0), transport_limit=5, transport_used=max(0, 0 - 2), submit_repair_limit=5, submit_repair_used=0, supplement_request_limit=5, supplement_request_used=0)


def _state(*, pending: str | None = None, request_kind: str = "initial") -> local._PlanningStateSnapshotV6:
    return local._PlanningStateSnapshotV6(
        schema_version='6.0', request_kind=request_kind, requirement_digest=DIGEST,
        effective_policy=None, draft=None, budgets=_budget(), model_invocations_consumed=0,
        no_progress_streak=0,
        supplement_fingerprints=(), pending_call_id=pending,
        current_stage='preparation', preparation_used=0, transport_used=0, submit_repair_used=0,
        supplement_request_used=0, format_repair_used=0, last_rejection=None,
        correction_targets=(), correction_plan_fingerprints=(), correction_evidence_fingerprint=None,
        correction_fact_fingerprints=(), correction_no_progress=0, last_proposal_fingerprint=None)


def _summary_gap() -> local.PlanningProcessGapSummarySnapshot:
    return local.PlanningProcessGapSummarySnapshot(
        schema_version="1.0", summary_type="planning_process", gap_id="gap-1",
        evidence_indexes=(), process_reason="no_progress",
    )


def _record_variants() -> tuple[BaseModel, ...]:
    summary = local.PlanningSummarySnapshot(
        schema_version="1.0", effective_pace=None, sightseeing_days=1, full_days=0, partial_days=0,
        transit_only_days=1, attraction_count=0, lunch_count=0, dinner_count=0,
        unique_main_candidates=0, boundary_transfers=0, route_legs=0, backups=0, unknown_hours=0,
    )
    return (
        local.InProgressItineraryRecord(schema_version="1.0", record_type="in_progress", state=_state(), gaps=()),
        local.AwaitingCandidateSupplementRecord(
            schema_version="1.0", record_type="awaiting_candidate_supplement",
            state=_state(pending="call-1", request_kind="resume"), gaps=(_summary_gap(),),
            gap_fingerprint=DIGEST, pending_call_id="call-1",
        ),
        local.IncompleteItineraryRecord(
            schema_version="1.0", record_type="incomplete", draft=None, summary=summary,
            budgets=_budget(), gaps=(_summary_gap(),), stop_reasons=(PlanningStopReason.NO_PROGRESS,),
        ),
        local.FailedItineraryRecord(
            schema_version="1.0", record_type="failed", last_consistent_itinerary_ref=None,
            failure=local.ControlPlanningFailureSnapshot(
                schema_version="1.0", failure_type="control", code=PlanningFailureCode.ARTIFACT_ERROR,
                retryable=True, possible_duplicate=False,
            ), budgets=None, retryable=True, possible_duplicate=False,
        ),
    )


def _draft(directories: local.ArtifactRefDirectories) -> local.ItineraryDraftSnapshot:
    hotel = local.HotelLocationRefSnapshot(schema_version="1.0", ref_type="hotel", hotel_selection_ref_index=0)
    arrival_hub = local.HubLocationRefSnapshot(schema_version="1.0", ref_type="hub", endpoint_kind="arrival", geocode_observation_index=0)
    departure_hub = local.HubLocationRefSnapshot(schema_version="1.0", ref_type="hub", endpoint_kind="departure", geocode_observation_index=0)
    arrival_leg = local.RouteLegSnapshot(
        schema_version="3.0", leg_id="arrival-leg", origin=arrival_hub, destination=hotel,
        interval=local.PersistedRouteTimeInterval(
            schema_version="2.0", start_at_utc=UTC, end_at_utc=UTC, timezone="Asia/Shanghai",
            local_start_date=UTC.astimezone(timezone.utc).date(), local_end_date=UTC.astimezone(timezone.utc).date(),
        ), transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, directions_observation_index=0,
        alternative_index=0, distance_m=0, duration_s=0, scheduled_minutes=0,
    )
    departure_leg = arrival_leg.model_copy(update={"leg_id": "departure-leg", "origin": hotel, "destination": departure_hub})
    return local.ItineraryDraftSnapshot(
        schema_version="1.0", requirement_digest=DIGEST,
        hotel_selection_ref=directories.hotel_selection_refs[0], candidate_set_ref=directories.candidate_set_refs[0],
        effective_policy=local.EffectivePlanningPolicySnapshot(
            schema_version="1.0", effective_pace=TravelPace.NORMAL, daily_time_rules=(), time_sensitive_must=(), transport_rules=(), locked=True,
        ),
        arrival_transfer=local.BoundaryTransferSnapshot(
            schema_version="1.0", transfer_kind="arrival", hub=arrival_hub, hotel=hotel, route_leg=arrival_leg,
            processing_minutes=60, approximate_extra_minutes=0,
        ),
        days=(local.DayPlanSnapshot(
            schema_version="1.0", date=UTC.date(), day_kind=PlanningDayKind.TRANSIT_ONLY, theme="抵达与入住",
            open_intervals=(), visits=(), route_legs=(), backups=(),
        ),),
        departure_transfer=local.BoundaryTransferSnapshot(
            schema_version="1.0", transfer_kind="departure", hub=departure_hub, hotel=hotel, route_leg=departure_leg,
            processing_minutes=120, approximate_extra_minutes=0,
        ), upstream_gap_ids=(),
    )


def _persisted_interval(start_at_utc: datetime, end_at_utc: datetime) -> local.PersistedTimeInterval:
    return local.PersistedTimeInterval(
        schema_version="2.0", start_at_utc=start_at_utc, end_at_utc=end_at_utc,
        timezone="Asia/Shanghai", local_start_date=date(2026, 9, 3), local_end_date=date(2026, 9, 3),
    )


def _persisted_route_interval(at_utc: datetime) -> local.PersistedRouteTimeInterval:
    return local.PersistedRouteTimeInterval(
        schema_version="2.0", start_at_utc=at_utc, end_at_utc=at_utc,
        timezone="Asia/Shanghai", local_start_date=date(2026, 9, 3), local_end_date=date(2026, 9, 3),
    )


def _non_transit_day(visit_count: int) -> local.DayPlanSnapshot:
    hotel = local.HotelLocationRefSnapshot(schema_version="1.0", ref_type="hotel", hotel_selection_ref_index=0)
    candidates = tuple(
        local.CandidateLocationRefSnapshot(
            schema_version="1.0", ref_type="candidate", admission_ordinal=index, uid=f"candidate-{index + 1}"
        )
        for index in range(visit_count)
    )
    visit_windows = ((UTC, datetime(2026, 9, 3, 3, 0, tzinfo=timezone.utc)),
                     (datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc), datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc)))
    visits = tuple(
        local.ScheduledVisitSnapshot(
            schema_version="1.0", visit_id=f"visit-{index + 1}", location=candidate, visit_kind=VisitKind.ATTRACTION,
            interval=_persisted_interval(*visit_windows[index]), duration_source="planning_estimate",
            must_group_ids=(), time_sensitive_anchor_ids=(), business_hours_state=BusinessHoursState.COMPATIBLE,
            business_evidence_indexes=(), requires_verification=False,
        )
        for index, candidate in enumerate(candidates)
    )
    route_points = (datetime(2026, 9, 3, 1, 30, tzinfo=timezone.utc),
                    datetime(2026, 9, 3, 3, 0, tzinfo=timezone.utc),
                    datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc))
    route_refs = (hotel, *candidates, hotel)
    route_legs = tuple(
        local.RouteLegSnapshot(
            schema_version="3.0", leg_id=f"leg-{index + 1}", origin=route_refs[index], destination=route_refs[index + 1],
            interval=_persisted_route_interval(route_points[index]), transport_semantic=TransportSemantic.WALKING,
            route_mode=TravelMode.WALKING, directions_observation_index=0, alternative_index=0,
            distance_m=0, duration_s=0, scheduled_minutes=0,
        )
        for index in range(visit_count + 1)
    )
    return local.DayPlanSnapshot(
        schema_version="1.0", date=date(2026, 9, 3), day_kind=PlanningDayKind.PARTIAL, theme="游览日",
        open_intervals=(_persisted_interval(datetime(2026, 9, 3, 1, 0, tzinfo=timezone.utc), datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)),),
        visits=visits, route_legs=route_legs, backups=(),
    )


def _draft_with_day(directories: local.ArtifactRefDirectories, day: local.DayPlanSnapshot) -> local.ItineraryDraftSnapshot:
    data = _draft(directories).model_dump(mode="python")
    data["days"] = (day.model_dump(mode="python"),)
    return local.ItineraryDraftSnapshot.model_validate(data, strict=True)


def test_non_transit_single_and_multi_visit_snapshots_keep_adjacent_route_pairing() -> None:
    single = _non_transit_day(1)
    multi = _non_transit_day(2)
    assert len(single.visits) == 1
    assert len(multi.visits) == 2
    assert multi.route_legs[1].origin == multi.visits[0].location
    assert multi.route_legs[1].destination == multi.visits[1].location


def test_cross_midnight_day_snapshot_round_trips_public_dto_and_hotel_closure() -> None:
    snapshot = _non_transit_day(1)
    public = local._from_day_snapshot(snapshot)
    data = public.model_dump(mode="json")
    data["open_intervals"][0]["end_at"] = "2026-09-04T00:30:00+08:00"
    data["route_legs"][-1]["interval"].update(
        start_at="2026-09-04T00:00:00+08:00",
        end_at="2026-09-04T00:10:00+08:00",
    )
    data["route_legs"][-1].update(duration_s=600, scheduled_minutes=10)

    cross_midnight = local.DayPlan.model_validate_json(
        __import__("json").dumps(data, ensure_ascii=False), strict=True
    )
    persisted = local._to_day_snapshot(cross_midnight)
    restored = local._from_day_snapshot(persisted)

    assert restored == cross_midnight
    assert persisted.open_intervals[0].local_end_date == date(2026, 9, 4)
    assert persisted.route_legs[-1].interval.local_start_date == date(2026, 9, 4)
    assert persisted.route_legs[-1].interval.local_end_date == date(2026, 9, 4)


@pytest.mark.parametrize("mutation", ("endpoint", "order", "time", "open_interval", "visit_overlap", "route_overlap"))
def test_non_transit_snapshot_rejects_route_and_schedule_regressions(mutation: str) -> None:
    day = _non_transit_day(2)
    route_legs = list(day.route_legs)
    visits = list(day.visits)
    open_intervals = list(day.open_intervals)
    if mutation == "endpoint":
        route_legs[1] = route_legs[1].model_copy(update={"destination": route_legs[0].origin})
    elif mutation == "order":
        route_legs[1], route_legs[2] = route_legs[2], route_legs[1]
    elif mutation == "time":
        route_legs[1] = route_legs[1].model_copy(update={"interval": _persisted_route_interval(datetime(2026, 9, 3, 2, 30, tzinfo=timezone.utc))})
    elif mutation == "open_interval":
        open_intervals[0] = _persisted_interval(datetime(2026, 9, 3, 1, 0, tzinfo=timezone.utc), datetime(2026, 9, 3, 2, 30, tzinfo=timezone.utc))
    elif mutation == "visit_overlap":
        visits[1] = visits[1].model_copy(update={"interval": _persisted_interval(datetime(2026, 9, 3, 2, 30, tzinfo=timezone.utc), datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc))})
    else:
        route_legs[2] = route_legs[2].model_copy(update={"interval": _persisted_route_interval(datetime(2026, 9, 3, 2, 30, tzinfo=timezone.utc))})
    data = day.model_dump(mode="python")
    data.update(route_legs=tuple(route_legs), visits=tuple(visits), open_intervals=tuple(open_intervals))
    with pytest.raises(ValueError):
        local.DayPlanSnapshot.model_validate(data, strict=True)


@pytest.mark.parametrize("visit_count", (1, 2))
def test_completed_non_transit_artifact_round_trips_through_real_registry(visit_count: int) -> None:
    directories = _directories()
    day = _non_transit_day(visit_count)
    draft = _draft_with_day(directories, day)
    summary = local.PlanningSummarySnapshot(
        schema_version="1.0", effective_pace=TravelPace.NORMAL, sightseeing_days=1, full_days=0, partial_days=1,
        transit_only_days=0, attraction_count=visit_count, lunch_count=0, dinner_count=0, unique_main_candidates=visit_count,
        boundary_transfers=2, route_legs=visit_count + 3, backups=0, unknown_hours=0,
    )
    artifact = ItineraryPlanningArtifact(
        schema_version="6.0", request_digest=DIGEST, parent_planning_ref=None, directories=directories,
        record=local.CompletedItineraryRecord(
            schema_version="1.0", record_type="completed", draft=draft, summary=summary, budgets=_budget(),
        ),
    )
    registry = ArtifactSchemaRegistry(ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS)
    request = ArtifactWriteRequest(
        operation_id=_op("non_transit_write"), artifact_type="itinerary_planning.itinerary", schema_version="6.0",
        content=artifact.model_dump(mode="json"),
    )
    prepared = registry.prepare(request)
    ref = ArtifactRef(
        artifact_id=uuid4(), artifact_type=request.artifact_type, schema_version=request.schema_version,
        content_hash=prepared.content_hash, operation_id=request.operation_id, created_at=UTC, size_bytes=prepared.size_bytes,
    )
    decoded = registry.decode(ref=ref, canonical_bytes=prepared.canonical_bytes, expected_model=ItineraryPlanningArtifact)
    assert decoded == artifact
    assert decoded is not artifact
    assert decoded.record.draft.days[0] is not artifact.record.draft.days[0]


def test_all_five_record_branches_are_constructible_and_pending_rules_hold() -> None:
    directories = _directories()
    draft = _draft(directories)
    summary = local.PlanningSummarySnapshot(
        schema_version="1.0", effective_pace=TravelPace.NORMAL, sightseeing_days=1, full_days=0, partial_days=0,
        transit_only_days=1, attraction_count=0, lunch_count=0, dinner_count=0,
        unique_main_candidates=0, boundary_transfers=2, route_legs=2, backups=0, unknown_hours=0,
    )
    completed = local.CompletedItineraryRecord(schema_version="1.0", record_type="completed", draft=draft, summary=summary, budgets=_budget())
    assert completed.record_type == "completed"
    artifact = ItineraryPlanningArtifact(schema_version="6.0", request_digest=DIGEST, parent_planning_ref=None, directories=directories, record=completed)
    assert artifact.record.draft.hotel_selection_ref == directories.hotel_selection_refs[0]


def _metadata() -> InvocationMetadata:
    return InvocationMetadata(
        role=ModelRole.ITINERARY_PLANNING, model_name="deepseek-v4-flash", finish_reason="stop",
        provider_attempts=1, possible_duplicate=False,
        usage=LLMUsage(
            prompt_tokens=1, completion_tokens=0, total_tokens=1, reasoning_tokens=0,
            prompt_cache_hit_tokens=1, prompt_cache_miss_tokens=0,
        ),
    )


def _registered_artifacts() -> tuple[tuple[str, BaseModel, OperationId, type[BaseModel]], ...]:
    directories = _directories()
    candidate = local.CandidateLocationRefSnapshot(schema_version="1.0", ref_type="candidate", admission_ordinal=0, uid="candidate-1")
    hotel = local.HotelLocationRefSnapshot(schema_version="1.0", ref_type="hotel", hotel_selection_ref_index=0)
    action_ref = directories.model_action_refs[0]
    action = ItineraryPlanningModelActionArtifact(
        schema_version='9.0', request_digest=DIGEST, invocation_index=0, prompt_version='planning-v9',
        prompt_hash=DIGEST, action_kind="final_text", call_summaries=(), invocation_metadata=_metadata(), disposition="accepted",
        request_stage='preparation', budget_category='preparation')
    geocode = ItineraryPlanningHubGeocodeObservationArtifact(
        schema_version="1.0", request_digest=DIGEST, model_action_ref=action_ref, action_ordinal=0,
        endpoint_kind="arrival", request_fingerprint=DIGEST, observation_status="not_issued",
        success_candidate=None, ambiguous_candidates=(), provenance=None, map_failure=None,
    )
    matrix = ItineraryPlanningRouteMatrixObservationArtifact(
        schema_version="1.0", request_digest=DIGEST, model_action_ref=action_ref, action_ordinal=0,
        date=UTC.date(), mode=MatrixTravelMode.DRIVING, request_fingerprint=DIGEST, ordered_locations=(candidate,), cells=(),
        observation_status="not_issued", provenance=None, map_failure=None,
    )
    directions = ItineraryPlanningDirectionsObservationArtifact(
        schema_version="1.0", request_digest=DIGEST, model_action_ref=action_ref, action_ordinal=0,
        leg_ordinal=0, leg_fingerprint=DIGEST, origin=candidate, destination=hotel,
        transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING, alternatives_limit=1,
        observation_status="not_issued", alternatives=(), provenance=None, map_failure=None,
    )
    itinerary = ItineraryPlanningArtifact(
        schema_version="6.0", request_digest=DIGEST, parent_planning_ref=None, directories=directories,
        record=_record_variants()[0],
    )
    return (
        ("itinerary_planning.model_action", action, _op("action_write"), ItineraryPlanningModelActionArtifact),
        ("itinerary_planning.hub_geocode_observation", geocode, _op("geocode_write"), ItineraryPlanningHubGeocodeObservationArtifact),
        ("itinerary_planning.route_matrix_observation", matrix, _op("matrix_write"), ItineraryPlanningRouteMatrixObservationArtifact),
        ("itinerary_planning.directions_observation", directions, _op("directions_write"), ItineraryPlanningDirectionsObservationArtifact),
        ("itinerary_planning.itinerary", itinerary, _op("itinerary_write"), ItineraryPlanningArtifact),
    )


def test_real_registry_accepts_complete_graph_and_each_registered_artifact_round_trips() -> None:
    registry = ArtifactSchemaRegistry(ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS)
    for artifact_type, artifact, operation_id, expected_model in _registered_artifacts():
        request = ArtifactWriteRequest(
            operation_id=operation_id, artifact_type=artifact_type, schema_version=artifact.schema_version,
            content=artifact.model_dump(mode="json"),
        )
        prepared = registry.prepare(request)
        ref = ArtifactRef(
            artifact_id=uuid4(), artifact_type=artifact_type, schema_version=artifact.schema_version,
            content_hash=prepared.content_hash, operation_id=operation_id, created_at=UTC, size_bytes=prepared.size_bytes,
        )
        decoded = registry.decode(ref=ref, canonical_bytes=prepared.canonical_bytes, expected_model=expected_model)
        assert decoded == artifact
        assert decoded is not artifact


def test_reachable_artifact_models_are_exact_registry_models_with_no_public_embedding() -> None:
    allowed = {ArtifactRef, InvocationMetadata, LLMUsage, OperationId}
    visited: set[type[BaseModel]] = set()

    def walk(annotation: object) -> None:
        origin = get_origin(annotation)
        if origin is Annotated:
            walk(get_args(annotation)[0])
            return
        if origin in {Union, UnionType}:
            for branch in get_args(annotation):
                walk(branch)
            return
        if origin is tuple:
            for item in get_args(annotation):
                if item is not Ellipsis:
                    walk(item)
            return
        if origin is list:
            walk(get_args(annotation)[0])
            return
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in visited:
                return
            visited.add(annotation)
            assert annotation in allowed or annotation.__module__ == "app.itinerary_planning.artifacts"
            assert dict(annotation.model_config) == {"strict": True, "frozen": True, "extra": "forbid"}
            assert not getattr(annotation, "__pydantic_computed_fields__", {})
            for field in annotation.model_fields.values():
                assert field.is_required()
                assert field.default is PydanticUndefined
                assert field.alias is None
                walk(field.annotation)

    for registration in ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS:
        walk(registration.model)
    assert not any(
        item.__module__ in {"app.itinerary_planning.models", "app.baidu_map.models.common", "app.baidu_map.models.routes", "app.llm_runtime.models"}
        for item in visited if item not in allowed
    )


def test_registry_rejects_extra_coercion_forged_branch_and_unknown_version() -> None:
    registry = ArtifactSchemaRegistry(ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS)
    artifact = _registered_artifacts()[0][1]
    request = ArtifactWriteRequest(
        operation_id=_op("security_write"), artifact_type="itinerary_planning.model_action", schema_version="9.0",
        content=artifact.model_dump(mode="json"),
    )
    with pytest.raises(ArtifactRequestValidationError):
        registry.prepare(request.model_copy(update={"content": {**request.content, "unexpected": True}}))
    with pytest.raises(ArtifactRequestValidationError):
        registry.prepare(request.model_copy(update={"content": {**request.content, "invocation_index": "0"}}))
    forged = ItineraryPlanningModelActionArtifact.model_construct(
        **{**artifact.model_dump(mode="python"), "action_kind": "llm_failure", "invocation_metadata": _metadata()}
    )
    with pytest.raises(ArtifactRequestValidationError):
        registry.prepare(request.model_copy(update={"content": forged.model_dump(mode="json")}))
    with pytest.raises(Exception):
        registry.prepare(request.model_copy(update={"schema_version": "2.0"}))
    with pytest.raises(Exception):
        registry.prepare(request.model_copy(update={"schema_version": "3.0"}))
    with pytest.raises(ArtifactRequestValidationError):
        registry.prepare(request.model_copy(update={"content": {**request.content, "prompt_version": "planning-v3"}}))


def test_directories_cross_run_and_pending_zero_prefix_contracts() -> None:
    directories = _directories()
    artifact = ItineraryPlanningArtifact(
        schema_version="6.0", request_digest=DIGEST, parent_planning_ref=None,
        directories=directories, record=_record_variants()[1],
    )
    assert artifact.record.state.draft is None

    with pytest.raises(ValueError):
        local.ArtifactRefDirectories.model_validate(
            {**directories.model_dump(mode="python"), "candidate_set_refs": (
                _ref(20, "candidate_research.candidate_set", WorkflowPhase.CANDIDATE_RESEARCH, "candidate_other", uuid4()),
            )}, strict=True,
        )
    with pytest.raises(ValueError):
        local.ItineraryPlanningArtifact.model_validate({
            **artifact.model_dump(mode="python"),
            "parent_planning_ref": _ref(21, "itinerary_planning.itinerary", WorkflowPhase.ITINERARY_PLANNING, "parent", uuid4()),
        }, strict=True)

    state_with_pending = _state(pending="call-2")
    with pytest.raises(ValueError):
        local.InProgressItineraryRecord(
            schema_version="1.0", record_type="in_progress", state=state_with_pending, gaps=(),
        )
