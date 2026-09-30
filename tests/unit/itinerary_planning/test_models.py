from datetime import date, datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest


def test_resume_v2_requires_actual_upstream_plan_and_preserves_other_versions():
    from pydantic import ValidationError
    from app.itinerary_planning import ItineraryPlanningResumeRequest, ITINERARY_PLANNING_SCHEMA_VERSION
    from tests.unit.itinerary_planning.test_service_initial import _fixture, _ref
    from tests.unit.itinerary_planning.test_service_resume import _supplemental_plan
    _, initial, *_ = _fixture()
    run_id = initial.operation_plan.run_id
    fields = dict(predecessor_planning_ref=_ref(run_id, "itinerary_planning.itinerary", WorkflowPhase.ITINERARY_PLANNING, "awaiting"),
        supplemented_candidate_set_ref=initial.candidate_set_ref, operation_plan=initial.operation_plan,
        supplemental_operation_plan=_supplemental_plan(run_id))
    request = ItineraryPlanningResumeRequest(**fields)
    assert tuple(type(request).model_fields) == ("schema_version", "predecessor_planning_ref", "supplemented_candidate_set_ref", "operation_plan", "supplemental_operation_plan")
    assert request.schema_version == "3.0"
    assert request.operation_plan.schema_version == ITINERARY_PLANNING_SCHEMA_VERSION == "2.0"
    assert request.supplemental_operation_plan.schema_version == "1.0"
    assert ItineraryPlanningResumeRequest.model_validate_json(request.model_dump_json(), strict=True) == request
    with pytest.raises(ValidationError):
        ItineraryPlanningResumeRequest(**fields, schema_version='2.0')
    with pytest.raises(ValidationError):
        ItineraryPlanningResumeRequest(**{key: value for key, value in fields.items() if key != "supplemental_operation_plan"})

from app.workflow_contracts import ArtifactRef, ExecutionBudgetKey, ExecutionBudgetScope, OperationId, WorkflowPhase

from app.baidu_map import MatrixTravelMode, TravelMode
from app.candidate_research import ResearchRole
from app.itinerary_planning import (
    BusinessHoursState,
    CandidateLocationRef,
    DayPlan,
    HotelLocationRef,
    ItineraryPlanningOperationPlan,
    PlanningInvocationOperationIds,
    PlanningDayKind,
    PlanningFailureCode,
    PlanningStopReason,
    RouteLeg,
    RouteTimeInterval,
    ScheduledVisit,
    TimeInterval,
    TransportSemantic,
    VisitKind,
)
from app.itinerary_planning import models
from app.candidate_research.models import RequirementMatchState, RequirementReasonCode, MatchVerdict


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _candidate(uid: str = "c-1") -> CandidateLocationRef:
    return CandidateLocationRef(admission_ordinal=0, uid=uid)


def _visit() -> ScheduledVisit:
    return ScheduledVisit(
        visit_id="visit-1",
        location=_candidate(),
        visit_kind=VisitKind.ATTRACTION,
        interval=TimeInterval(
            start_at=datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 11, 0, tzinfo=SHANGHAI),
        ),
        duration_source="planning_estimate",
        must_group_ids=(),
        time_sensitive_anchor_ids=(),
        business_hours_state=BusinessHoursState.COMPATIBLE,
        business_evidence_indexes=(),
        requires_verification=False,
    )


def _visit_for(candidate: CandidateLocationRef, visit_id: str, start_hour: int, end_hour: int) -> ScheduledVisit:
    return ScheduledVisit(
        visit_id=visit_id,
        location=candidate,
        visit_kind=VisitKind.ATTRACTION,
        interval=TimeInterval(
            start_at=datetime(2026, 9, 3, start_hour, 0, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, end_hour, 0, tzinfo=SHANGHAI),
        ),
        duration_source="planning_estimate",
        must_group_ids=(),
        time_sensitive_anchor_ids=(),
        business_hours_state=BusinessHoursState.COMPATIBLE,
        business_evidence_indexes=(),
        requires_verification=False,
    )


def _leg(origin, destination, start, end, *, seconds=0) -> RouteLeg:
    return RouteLeg(
        leg_id=f"leg-{start.hour}-{destination['ref_type'] if isinstance(destination, dict) else destination.ref_type}",
        origin=origin,
        destination=destination,
        interval=RouteTimeInterval(start_at=start, end_at=end),
        transport_semantic=TransportSemantic.WALKING,
        route_mode=TravelMode.WALKING,
        directions_observation_index=0,
        alternative_index=0,
        distance_m=0,
        duration_s=seconds,
        scheduled_minutes=0,
    )


def _day_with_visits(visit_count: int) -> DayPlan:
    hotel = HotelLocationRef()
    candidates = tuple(_candidate(f"c-{index + 1}") for index in range(visit_count))
    visits = tuple(
        _visit_for(candidate, f"visit-{index + 1}", 10 + index * 2, 11 + index * 2)
        for index, candidate in enumerate(candidates)
    )
    route_hours = (10,) + tuple(11 + index * 2 for index in range(visit_count))
    route_refs = (hotel, *candidates, hotel)
    route_legs = tuple(
        _leg(
            route_refs[index], route_refs[index + 1],
            datetime(2026, 9, 3, route_hours[index], 0, tzinfo=SHANGHAI),
            datetime(2026, 9, 3, route_hours[index], 0, tzinfo=SHANGHAI),
        )
        for index in range(visit_count + 1)
    )
    return DayPlan(
        date=datetime(2026, 9, 3, tzinfo=SHANGHAI).date(),
        day_kind=PlanningDayKind.PARTIAL,
        theme="多访问日",
        open_intervals=(TimeInterval(
            start_at=datetime(2026, 9, 3, 9, 0, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 21, 0, tzinfo=SHANGHAI),
        ),),
        visits=visits,
        route_legs=route_legs,
        backups=(),
    )


def test_strict_time_and_route_contracts() -> None:
    interval = TimeInterval(
        start_at=datetime(2026, 9, 3, 9, 0, tzinfo=SHANGHAI),
        end_at=datetime(2026, 9, 3, 9, 30, tzinfo=SHANGHAI),
    )
    assert interval.model_validate_json(interval.model_dump_json()) == interval
    with pytest.raises(ValueError):
        TimeInterval(
            start_at=datetime(2026, 9, 3, 9, 15, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI),
        )
    with pytest.raises(ValueError):
        RouteTimeInterval(
            start_at=datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc),
        )


def test_route_semantic_and_zero_duration_boundary() -> None:
    hotel = {"ref_type": "hotel", "hotel_selection_ref_index": 0}
    leg = _leg(
        hotel,
        _candidate(),
        datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI),
        datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI),
    )
    assert leg.scheduled_minutes == 0
    with pytest.raises(ValueError):
        RouteLeg(**{**leg.model_dump(mode="python"), "transport_semantic": TransportSemantic.CYCLING})


def test_day_plan_closes_hotel_loop_and_transit_only_is_empty() -> None:
    candidate = _candidate()
    hotel_ref = HotelLocationRef()
    visit = _visit()
    day = DayPlan(
        date=visit.interval.start_at.date(),
        day_kind=PlanningDayKind.PARTIAL,
        theme="文化街区",
        open_intervals=(TimeInterval(start_at=datetime(2026, 9, 3, 9, 0, tzinfo=SHANGHAI), end_at=datetime(2026, 9, 3, 21, 0, tzinfo=SHANGHAI)),),
        visits=(visit,),
        route_legs=(
            _leg(hotel_ref, candidate, datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI), datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI)),
            _leg(candidate, hotel_ref, datetime(2026, 9, 3, 11, 0, tzinfo=SHANGHAI), datetime(2026, 9, 3, 11, 0, tzinfo=SHANGHAI)),
        ),
        backups=(),
    )
    assert day.model_validate_json(day.model_dump_json()) == day
    transit = DayPlan(
        date=visit.interval.start_at.date(),
        day_kind=PlanningDayKind.TRANSIT_ONLY,
        theme="返程日",
        open_intervals=(),
        visits=(),
        route_legs=(),
        backups=(),
    )
    assert transit.visits == ()
    with pytest.raises(ValueError):
        DayPlan(**{**transit.model_dump(mode="python"), "theme": "#unsafe"})


@pytest.mark.parametrize("visit_count", (1, 2))
def test_day_plan_accepts_single_and_double_visit_hotel_loops(visit_count: int) -> None:
    day = _day_with_visits(visit_count)
    assert len(day.route_legs) == visit_count + 1
    assert day.route_legs[0].origin.ref_type == "hotel"
    assert day.route_legs[-1].destination.ref_type == "hotel"


@pytest.mark.parametrize("field", ("origin", "destination"))
def test_day_plan_rejects_double_visit_middle_route_endpoint_mismatch(field: str) -> None:
    day = _day_with_visits(2)
    route_legs = list(day.route_legs)
    route_legs[1] = route_legs[1].model_copy(update={field: HotelLocationRef()})
    with pytest.raises(ValueError):
        DayPlan(**{**day.model_dump(mode="python"), "route_legs": tuple(route_legs)})


def test_day_plan_rejects_last_middle_route_not_connected_to_next_visit() -> None:
    day = _day_with_visits(3)
    route_legs = list(day.route_legs)
    route_legs[2] = route_legs[2].model_copy(update={"destination": HotelLocationRef()})
    with pytest.raises(ValueError):
        DayPlan(**{**day.model_dump(mode="python"), "route_legs": tuple(route_legs)})


def test_day_plan_keeps_boundary_endpoint_and_time_guards() -> None:
    day = _day_with_visits(2)

    first_endpoint = list(day.route_legs)
    first_endpoint[0] = first_endpoint[0].model_copy(update={"origin": _candidate("wrong-start")})
    with pytest.raises(ValueError):
        DayPlan(**{**day.model_dump(mode="python"), "route_legs": tuple(first_endpoint)})

    last_endpoint = list(day.route_legs)
    last_endpoint[-1] = last_endpoint[-1].model_copy(update={"destination": _candidate("wrong-end")})
    with pytest.raises(ValueError):
        DayPlan(**{**day.model_dump(mode="python"), "route_legs": tuple(last_endpoint)})

    first_time = list(day.route_legs)
    first_time[0] = first_time[0].model_copy(update={
        "interval": RouteTimeInterval(
            start_at=datetime(2026, 9, 3, 10, 30, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 10, 30, tzinfo=SHANGHAI),
        ),
    })
    with pytest.raises(ValueError):
        DayPlan(**{**day.model_dump(mode="python"), "route_legs": tuple(first_time)})

    last_time = list(day.route_legs)
    last_time[-1] = last_time[-1].model_copy(update={
        "interval": RouteTimeInterval(
            start_at=datetime(2026, 9, 3, 12, 30, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 12, 30, tzinfo=SHANGHAI),
        ),
    })
    with pytest.raises(ValueError):
        DayPlan(**{**day.model_dump(mode="python"), "route_legs": tuple(last_time)})


def test_enum_order_and_failure_values_are_frozen() -> None:
    assert tuple(item.value for item in PlanningStopReason) == (
        "user_clarification_required", "candidate_supply_insufficient", "schedule_infeasible",
        "direction_budget_exhausted", "iteration_limit_reached", "no_progress",
        "no_legal_action", "no_terminal_submission",
    )
    assert tuple(item.value for item in PlanningFailureCode) == (
        "input_invalid", "predecessor_invalid", "operation_plan_invalid", "request_mismatch",
        "model_contract_invalid", "action_batch_invalid", "policy_contract_invalid", "artifact_error",
        "artifact_capacity_exceeded", "budget_error", "observation_invalid",
        "operation_result_indeterminate", "run_context_closed", "internal_invariant_broken",
    )


def test_public_proposal_and_tool_dtos_have_versioned_first_field() -> None:
    import importlib

    models = importlib.import_module("app.itinerary_planning.models")
    names = (
        "CandidateLocationRef", "HotelLocationRef", "HubLocationRef", "TimeInterval", "RouteTimeInterval",
        "ProposedDailyTimeRule", "ProposedTimeSensitiveMustAnchor", "ProposedTransportPolicyRule",
        "ProposedEffectivePlanningPolicy", "ProposedScheduledVisit", "ProposedRouteLeg",
        "ProposedBoundaryTransfer", "ProposedBackupOption", "ProposedDayPlan", "ProposedItineraryDraft",
        "ProposedCandidateCountTarget", "ProposedAreaCoverageTarget", "ProposedMealCoverageTarget",
        "ProposedMustResolutionTarget", "ProposedAvoidResolutionTarget", "ProposedCandidateDetailsTarget",
        "ProposedReplacementTarget", "ProposedSupplementGap", "GetCandidateEvidenceArgs", "GeocodeHubArgs",
        "CalculateRouteMatrixArgs", "DirectionLegIntent", "PlanDirectionsArgs", "SubmitItineraryArgs",
        "RequestCandidateSupplementArgs",
    )
    for name in names:
        model = getattr(models, name)
        assert tuple(model.model_fields) [0] == "schema_version"
        assert model.model_fields["schema_version"].default == "1.0"
        assert dict(model.model_config) == {
            "strict": True, "frozen": True, "extra": "forbid", "validate_default": True,
        }
    original = CandidateLocationRef(admission_ordinal=0, uid="fresh")
    copy = CandidateLocationRef.model_validate_json(original.model_dump_json())
    assert copy == original
    assert copy is not original


def _planning_operation(run_id, step: str) -> OperationId:
    return OperationId(run_id=run_id, phase=WorkflowPhase.ITINERARY_PLANNING, logical_step=step.replace("-", "_"))


def test_resume_accepts_remaining_contiguous_slots_and_rejects_bad_sequences() -> None:
    run_id = uuid4()

    def plan(indexes):
        slots = tuple(
            {
                "invocation_slot_index": index,
                "action_operation_id": _planning_operation(run_id, f"action-{index}"),
                "planning_state_operation_id": _planning_operation(run_id, f"state-{index}"),
            }
            for index in indexes
        )
        return ItineraryPlanningOperationPlan(
            run_id=run_id, invocation_slots=slots, hub_geocode_operation_ids=(), matrix_operation_ids=(),
            direction_operation_ids=(),
            terminal_successor_operation_id=_planning_operation(run_id, "terminal-successor"),
        )

    assert tuple(item.invocation_slot_index for item in plan((3, 4, 5)).invocation_slots) == (3, 4, 5)
    assert plan(())
    with pytest.raises(ValueError):
        plan((3, 5))
    with pytest.raises(ValueError):
        plan((4, 3))
    with pytest.raises(ValueError):
        plan((3, 3))
    with pytest.raises(ValueError):
        plan((1, 2, 3, 4, 5, 5))


def test_model_validator_matrix_covers_public_boundary_and_tool_rejections() -> None:
    utc = timezone.utc
    with pytest.raises(ValueError):
        TimeInterval(
            start_at=datetime(2026, 9, 3, 9, tzinfo=utc),
            end_at=datetime(2026, 9, 3, 10, tzinfo=utc),
        )
    with pytest.raises(ValueError):
        TimeInterval(
            start_at=datetime(2026, 9, 3, 9, 15, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 10, tzinfo=SHANGHAI),
        )
    with pytest.raises(ValueError):
        RouteTimeInterval(
            start_at=datetime(2026, 9, 3, 10, 30, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI),
        )
    with pytest.raises(ValueError):
        RouteTimeInterval(
            start_at=datetime(2026, 9, 3, 10, 15, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 10, 45, tzinfo=SHANGHAI),
        )

    digest = "sha256:" + "a" * 64
    daily = models.DailyTimeRule(
        rule_id="daily", date=date(2026, 9, 3),
        interval=TimeInterval(
            start_at=datetime(2026, 9, 3, 9, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 10, tzinfo=SHANGHAI),
        ), strength="hard", source_field="preferences.daily_time_preferences", source_digest=digest,
    )
    with pytest.raises(ValueError):
        models.DailyTimeRule(**{**daily.model_dump(mode="python"), "date": date(2026, 9, 4)})
    with pytest.raises(ValueError):
        models.TimeSensitiveMustAnchor(
            anchor_id="a", requirement_group_id="g", date=date(2026, 9, 3),
            event_at=datetime(2026, 9, 3, 9, tzinfo=utc), source="user_provided", source_ref_index=None,
        )
    with pytest.raises(ValueError):
        models.TimeSensitiveMustAnchor(
            anchor_id="a", requirement_group_id="g", date=date(2026, 9, 3),
            event_at=datetime(2026, 9, 3, 9, tzinfo=SHANGHAI), source="user_provided", source_ref_index=0,
        )
    with pytest.raises(ValueError):
        models.TimeSensitiveMustAnchor(
            anchor_id="a", requirement_group_id="g", date=date(2026, 9, 3),
            event_at=datetime(2026, 9, 3, 9, tzinfo=SHANGHAI), source="verified_evidence", source_ref_index=None,
        )

    visit = _visit()
    for update in (
        {"must_group_ids": ("must", "must")},
        {"time_sensitive_anchor_ids": ("anchor", "anchor")},
        {"business_evidence_indexes": (0, 0)},
        {"business_hours_state": BusinessHoursState.CONFLICTS},
        {"business_hours_state": BusinessHoursState.UNKNOWN, "requires_verification": False},
    ):
        with pytest.raises(ValueError):
            ScheduledVisit(**{**visit.model_dump(mode="python"), **update})

    route = _day_with_visits(1).route_legs[0]
    with pytest.raises(ValueError):
        RouteLeg(**{**route.model_dump(mode="python"), "route_mode": TravelMode.DRIVING})
    with pytest.raises(ValueError):
        RouteLeg(**{**route.model_dump(mode="python"), "scheduled_minutes": 30})
    with pytest.raises(ValueError):
        RouteLeg(**{**route.model_dump(mode="python"), "interval": RouteTimeInterval(start_at=datetime(2026, 9, 3, 10, tzinfo=SHANGHAI), end_at=datetime(2026, 9, 3, 10, 30, tzinfo=SHANGHAI)), "duration_s": 0, "scheduled_minutes": 0})

    hub = models.HubLocationRef(endpoint_kind="arrival", geocode_observation_index=0)
    hotel = HotelLocationRef()
    zero = RouteTimeInterval(start_at=datetime(2026, 9, 3, 8, tzinfo=SHANGHAI), end_at=datetime(2026, 9, 3, 8, tzinfo=SHANGHAI))
    boundary_leg = RouteLeg(
        leg_id="boundary", origin=hub, destination=hotel, interval=zero,
        transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.WALKING,
        directions_observation_index=0, alternative_index=0, distance_m=0, duration_s=0, scheduled_minutes=0,
    )
    boundary = models.BoundaryTransfer(
        transfer_kind="arrival", hub=hub, hotel=hotel, route_leg=boundary_leg,
        processing_minutes=30, approximate_extra_minutes=0,
    )
    assert boundary.transfer_kind == "arrival"
    with pytest.raises(ValueError):
        models.BoundaryTransfer(**{**boundary.model_dump(mode="python"), "route_leg": boundary_leg.model_copy(update={"origin": hotel, "destination": hub})})
    departure_leg = boundary_leg.model_copy(update={"origin": hotel, "destination": hub})
    departure = boundary.model_copy(update={"transfer_kind": "departure", "route_leg": departure_leg})
    assert departure.transfer_kind == "departure"
    with pytest.raises(ValueError):
        models.BoundaryTransfer(**{**departure.model_dump(mode="python"), "route_leg": boundary_leg})

    backup = models.BackupOption(
        backup_kind="attraction", core_visit_id="visit-1", candidate=_candidate("backup"),
        matrix_observation_index=0, matrix_mode=MatrixTravelMode.DRIVING, distance_m=100,
        business_hours_state=BusinessHoursState.COMPATIBLE, business_evidence_indexes=(0,), requires_verification=False,
    )
    for update in (
        {"business_evidence_indexes": (0, 0)},
        {"business_hours_state": BusinessHoursState.CONFLICTS},
        {"business_hours_state": BusinessHoursState.UNKNOWN, "requires_verification": False},
    ):
        with pytest.raises(ValueError):
            models.BackupOption(**{**backup.model_dump(mode="python"), **update})

    with pytest.raises(ValueError):
        models.ProposedDailyTimeRule(
            rule_id="p", date=None, start_at=datetime(2026, 9, 3, 9, 15, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 10, tzinfo=SHANGHAI), strength="hard", source_field="preferences.daily_time_preferences",
        )
    with pytest.raises(ValueError):
        models.ProposedTimeSensitiveMustAnchor(
            anchor_id="a", requirement_group_id="g", date=date(2026, 9, 3), event_at=datetime(2026, 9, 3, 9, tzinfo=SHANGHAI),
            source="verified_evidence", source_handle=None,
        )
    proposed_visit = models.ProposedScheduledVisit(
        visit_id="pv", candidate_handle="candidate-handle", visit_kind=VisitKind.ATTRACTION,
        start_at=datetime(2026, 9, 3, 10, tzinfo=SHANGHAI), end_at=datetime(2026, 9, 3, 11, tzinfo=SHANGHAI),
        duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(),
        business_hours_state=BusinessHoursState.COMPATIBLE, business_evidence_handles=(),
    )
    with pytest.raises(ValueError):
        models.ProposedScheduledVisit(**{**proposed_visit.model_dump(mode="python"), "business_evidence_handles": ("e", "e")})
    with pytest.raises(ValueError):
        models.ProposedRouteLeg(
            leg_id="pl", origin_handle="a", destination_handle="b",
            start_at=datetime(2026, 9, 3, 10, tzinfo=SHANGHAI), end_at=datetime(2026, 9, 3, 11, tzinfo=SHANGHAI),
            transport_semantic=TransportSemantic.WALKING, route_mode=TravelMode.DRIVING, directions_handle="d", alternative_index=0,
        )
    with pytest.raises(ValueError):
        models.ProposedCandidateCountTarget(role=ResearchRole.ATTRACTION, current_count=2, target_count=2)
    with pytest.raises(ValueError):
        models.ProposedReplacementTarget(rejected_candidate_handle="c", role=ResearchRole.ATTRACTION, anchor_handle="a", radius_km=None)

    target = models.ProposedCandidateCountTarget(role=ResearchRole.ATTRACTION, current_count=0, target_count=1)
    with pytest.raises(ValueError):
        models.ProposedSupplementGap(source_gap_type="backup_unavailable", date=None, target=target, evidence_handles=("e",))
    with pytest.raises(ValueError):
        models.ProposedSupplementGap(source_gap_type="candidate_supply", date=None, target=target, evidence_handles=("e", "e"))
    with pytest.raises(ValueError):
        models.GetCandidateEvidenceArgs(candidate_handles=("a", "a"))
    with pytest.raises(ValueError):
        models.CalculateRouteMatrixArgs(date=date(2026, 9, 3), mode=MatrixTravelMode.DRIVING, location_handles=("a", "a"))
    with pytest.raises(ValueError):
        models.DirectionLegIntent(
            leg_id="d", origin_handle="a", destination_handle="b", transport_semantic=TransportSemantic.WALKING,
            route_mode=TravelMode.DRIVING, alternatives_limit=1,
        )
    direction = models.DirectionLegIntent(
        leg_id="d", origin_handle="a", destination_handle="b", transport_semantic=TransportSemantic.WALKING,
        route_mode=TravelMode.WALKING, alternatives_limit=1,
    )
    with pytest.raises(ValueError):
        models.PlanDirectionsArgs(legs=(direction, direction.model_copy(update={"leg_id": "other"})))
    with pytest.raises(ValueError):
        models.CandidateCountTarget(role=ResearchRole.ATTRACTION, current_count=1, target_count=1)
