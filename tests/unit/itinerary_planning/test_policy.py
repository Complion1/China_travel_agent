from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.baidu_map import MatrixTravelMode, TravelMode
from app.candidate_research.models import (
    CandidateCore,
    CandidateDetailsState,
    CandidateRecord,
    CandidateState,
    InterestTag,
    MatchVerdict,
    ResearchRole,
    RequirementMatchState,
    RequirementReasonCode,
    RoleAssessment,
    RoleReasonCode,
    RoleVerdict,
)
from app.itinerary_planning import (
    BusinessHoursState,
    BackupOption,
    CandidateLocationRef,
    DayPlan,
    HotelLocationRef,
    PlanningDayKind,
    RouteLeg,
    RouteTimeInterval,
    ScheduledVisit,
    TimeInterval,
    TransportSemantic,
    VisitKind,
    PlanningStopReason,
)
from app.itinerary_planning.policy import (
    attraction_bounds,
    boundary_processing_minutes,
    candidate_is_eligible,
    candidate_uid_is_allowed,
    ceil_to_grid,
    default_open_interval,
    order_stop_reasons,
    route_mode_for_semantic,
    route_scheduled_minutes,
    validate_business_state,
    validate_backup_options,
    validate_day_policy,
    validate_density,
    validate_meal_slots,
    validate_route_capacity,
    validate_time_sensitive_anchor,
    validate_user_meal_anchor_source,
    validate_user_time_sensitive_anchor_source,
    _policy_json_value,
)
from app.requirements import (
    ConfirmedTravelRequirement,
    FieldMetadata,
    FieldMetadataRegistry,
    FieldStatus,
    HubType,
    RequirementFieldPath,
    TravelPace,
    TravelPreferences,
    TravelRequirement,
    TravelTimeWindow,
)
from app.itinerary_planning.models import (
    ProposedDailyTimeRule,
    ProposedEffectivePlanningPolicy,
    ProposedTimeSensitiveMustAnchor,
    ProposedTransportPolicyRule,
    TimeSensitiveMustAnchor,
)
from app.itinerary_planning.policy import effective_policy_from_proposal
from app.itinerary_planning.policy import _PolicyBusinessRejected


SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize("expression,minutes", (
    ("景点玩半小时", 30), ("景点停留半个小时", 30),
    ("景点参观一小时半", 90), ("景点游览1小时30分钟", 90),
    ("景点游览一个小时又三十分钟", 90), ("景点游览两小时", 120),
    ("景点游览0.5小时", 30), ("景点游览四十五分钟", 45),
    ("景点不要安排三十分钟，要游览一小时", 60),
    ("景点不是30分钟，而是一小时", 60),
    ("不要在景点玩30分钟，要游览一小时", 60),
    ("景点游览30分钟，餐馆用餐一小时", 30),
))
def test_confirmed_duration_units_subject_and_negation(expression, minutes):
    from app.itinerary_planning.policy import confirmed_visit_duration
    assert confirmed_visit_duration("景点", ("景点", "餐馆"), (expression,)) == minutes


@pytest.mark.parametrize("expression", (
    "景点游览30分钟或一小时", "景点游览30分钟，游览一小时",
    "景点至少游览30分钟", "景点游览0分钟", "景点游览半小时左右",
    "景点和餐馆各游览30分钟", "景点游览30分钟，不要安排30分钟",
))
def test_ambiguous_duration_is_not_an_explicit_authorization(expression):
    from app.itinerary_planning.policy import confirmed_visit_duration
    with pytest.raises(ValueError):
        confirmed_visit_duration("景点", ("景点", "餐馆"), (expression,))


def test_explicit_duration_rounding_and_estimate_cannot_bypass_user_choice():
    from app.itinerary_planning.policy import validate_visit_duration
    visit = _simple_visit().model_copy(update={"duration_source": "user_explicit"})
    validate_visit_duration(visit, user_duration_minutes=45)
    for minutes in (None, 0, -30, 30, True):
        with pytest.raises(ValueError):
            validate_visit_duration(visit, user_duration_minutes=minutes)
    with pytest.raises(ValueError):
        validate_visit_duration(_simple_visit(), user_duration_minutes=30)


def _simple_visit(kind: VisitKind = VisitKind.ATTRACTION) -> ScheduledVisit:
    return ScheduledVisit(
        visit_id="policy-visit",
        location=CandidateLocationRef(admission_ordinal=0, uid="policy-candidate"),
        visit_kind=kind,
        interval=TimeInterval(
            start_at=datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 11, 0, tzinfo=SHANGHAI),
        ),
        duration_source="planning_estimate",
        must_group_ids=(), time_sensitive_anchor_ids=(),
        business_hours_state=BusinessHoursState.COMPATIBLE,
        business_evidence_indexes=(), requires_verification=False,
    )


def _candidate_record(*, avoid: tuple[RequirementMatchState, ...] = (), state: CandidateState = CandidateState.ELIGIBLE) -> CandidateRecord:
    return CandidateRecord(
        schema_version="1.0", admission_ordinal=0,
        core=CandidateCore(
            uid="eligible-candidate", name="景点", address="上海市", provider_category="museum",
            coordinate={"coordinate_system": "bd09ll", "latitude": 31.2, "longitude": 121.5},
        ),
        state=state,
        roles=(
            RoleAssessment(role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(0,)),
            RoleAssessment(role=ResearchRole.RESTAURANT, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_indexes=(0,)),
        ),
        interests=(InterestTag.CULTURE,), meal_intents=(), must_matches=(), avoid_matches=avoid,
        details=CandidateDetailsState(status="success", observation_index=0), observation_indexes=(0,),
        dietary_restrictions="not_evaluated", mobility_constraints="not_evaluated", budget_fit="not_evaluated",
    )


def test_grid_buffers_modes_and_raw_duration_ceiling() -> None:
    assert ceil_to_grid(datetime(2026, 9, 3, 9, 1, tzinfo=SHANGHAI)).hour == 9
    assert ceil_to_grid(datetime(2026, 9, 3, 9, 1, tzinfo=SHANGHAI)).minute == 30
    assert boundary_processing_minutes(HubType.AIRPORT, "arrival", is_approximate=True) == 60
    with pytest.raises(ValueError):
        boundary_processing_minutes(HubType.RAILWAY_STATION, "departure", is_approximate=False, user_minutes=10)
    assert route_scheduled_minutes(0) == 0
    assert route_scheduled_minutes(1801) == 60
    assert route_mode_for_semantic(TransportSemantic.PUBLIC_TRANSIT) is TravelMode.TRANSIT


def test_density_distinguishes_full_and_partial_days() -> None:
    assert attraction_bounds(TravelPace.RELAXED, PlanningDayKind.FULL) == (1, 2)
    assert attraction_bounds(TravelPace.NORMAL, PlanningDayKind.FULL) == (3, 3)
    assert attraction_bounds(TravelPace.INTENSE, PlanningDayKind.FULL) == (4, 4)
    assert attraction_bounds(TravelPace.NORMAL, PlanningDayKind.PARTIAL) == (0, 3)

    candidate = CandidateLocationRef(admission_ordinal=0, uid="candidate")
    visit = ScheduledVisit(
        visit_id="visit",
        location=candidate,
        visit_kind=VisitKind.ATTRACTION,
        interval=TimeInterval(start_at=datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI), end_at=datetime(2026, 9, 3, 11, 0, tzinfo=SHANGHAI)),
        duration_source="planning_estimate",
        must_group_ids=(),
        time_sensitive_anchor_ids=(),
        business_hours_state=BusinessHoursState.COMPATIBLE,
        business_evidence_indexes=(),
        requires_verification=False,
    )
    hotel = HotelLocationRef()
    def leg(origin, destination, at):
        return RouteLeg(
            leg_id=f"leg-{at.hour}-{destination.ref_type}", origin=origin, destination=destination,
            interval=RouteTimeInterval(start_at=at, end_at=at), transport_semantic=TransportSemantic.WALKING,
            route_mode=TravelMode.WALKING, directions_observation_index=0, alternative_index=0,
            distance_m=0, duration_s=0, scheduled_minutes=0,
        )
    day = DayPlan(
        date=visit.interval.start_at.date(), day_kind=PlanningDayKind.FULL, theme="主题",
        open_intervals=(TimeInterval(start_at=datetime(2026, 9, 3, 9, 0, tzinfo=SHANGHAI), end_at=datetime(2026, 9, 3, 21, 0, tzinfo=SHANGHAI)),),
        visits=(visit,), route_legs=(leg(hotel, candidate, datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI)), leg(candidate, hotel, datetime(2026, 9, 3, 11, 0, tzinfo=SHANGHAI))), backups=(),
    )
    validate_density(day, TravelPace.RELAXED)
    with pytest.raises(ValueError):
        validate_density(day, TravelPace.INTENSE)


def test_backup_accepts_non_walking_matrix_within_3000m_and_rejects_high_value_invalids() -> None:
    candidate = CandidateLocationRef(admission_ordinal=0, uid="main")
    backup_candidate = CandidateLocationRef(admission_ordinal=1, uid="backup")
    visit = ScheduledVisit(
        visit_id="visit", location=candidate, visit_kind=VisitKind.ATTRACTION,
        interval=TimeInterval(start_at=datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI), end_at=datetime(2026, 9, 3, 11, 0, tzinfo=SHANGHAI)),
        duration_source="planning_estimate", must_group_ids=(), time_sensitive_anchor_ids=(),
        business_hours_state=BusinessHoursState.COMPATIBLE, business_evidence_indexes=(), requires_verification=False,
    )
    backup = BackupOption(
        backup_kind="attraction", core_visit_id="visit", candidate=backup_candidate,
        matrix_observation_index=0, matrix_mode=MatrixTravelMode.DRIVING, distance_m=3000,
        business_hours_state=BusinessHoursState.COMPATIBLE, business_evidence_indexes=(), requires_verification=False,
    )
    day = DayPlan.model_construct(visits=(visit,), backups=(backup,))
    validate_backup_options(day)

    too_far = backup.model_copy(update={"distance_m": 3001})
    with pytest.raises(ValueError):
        validate_backup_options(DayPlan.model_construct(visits=(visit,), backups=(too_far,)))
    duplicate = backup.model_copy(update={"candidate": backup_candidate})
    lunch_visit = visit.model_copy(update={"visit_id": "lunch", "visit_kind": VisitKind.LUNCH})
    duplicate_lunch = duplicate.model_copy(update={"backup_kind": "lunch", "core_visit_id": "lunch"})
    with pytest.raises(ValueError):
        validate_backup_options(DayPlan.model_construct(visits=(visit, lunch_visit), backups=(backup, duplicate_lunch)))
    wrong_role = backup.model_copy(update={"backup_kind": "dinner"})
    with pytest.raises(ValueError):
        validate_backup_options(DayPlan.model_construct(visits=(visit,), backups=(wrong_role,)))
    conflict = backup.model_copy(update={"business_hours_state": BusinessHoursState.CONFLICTS})
    with pytest.raises(ValueError):
        validate_backup_options(DayPlan.model_construct(visits=(visit,), backups=(conflict,)))


def _confirmed(*, pace: TravelPace | None, agent_decides: bool) -> ConfirmedTravelRequirement:
    return ConfirmedTravelRequirement(
        outcome_type="confirmed", schema_version="1.0",
        requirement=TravelRequirement(
            destination="上海",
            time_window=TravelTimeWindow(sightseeing_days=2),
            preferences=TravelPreferences(
                pace=pace,
                agent_decides_pace=agent_decides,
                daily_time_preferences="每天9点后开始游览",
                transport_preferences="公共交通优先",
            ),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(),
        confirmation_summary_digest="summary-digest",
    )


def test_policy_conversion_preserves_hard_soft_and_time_sensitive_evidence_contracts() -> None:
    proposal = ProposedEffectivePlanningPolicy(
        effective_pace=TravelPace.NORMAL,
        daily_time_rules=(ProposedDailyTimeRule(
            rule_id="rule-1", date=None,
            start_at=datetime(2026, 9, 3, 9, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 21, tzinfo=SHANGHAI),
            strength="hard", source_field="daily_time_preferences",
        ),),
        time_sensitive_must=(ProposedTimeSensitiveMustAnchor(
            anchor_id="anchor-1", requirement_group_id="must-1", date=datetime(2026, 9, 3).date(),
            event_at=datetime(2026, 9, 3, 8, 15, tzinfo=SHANGHAI), source="user_provided",
            source_handle=None,
        ),),
        transport_rules=(ProposedTransportPolicyRule(
            rule_id="transport-1", semantic=TransportSemantic.PUBLIC_TRANSIT,
            strength="soft", numeric_limit_minutes=90, source_field="transport_preferences",
        ),),
    )
    with pytest.raises(_PolicyBusinessRejected, match="structured_demo_deferred_policy_rule_rejected"):
        effective_policy_from_proposal(
            proposal, confirmed=_confirmed(pace=None, agent_decides=True), source_handle_indexes={"evidence-1": 4}
        )


def test_policy_rejects_verified_time_anchor_without_typed_event_evidence() -> None:
    proposal = ProposedEffectivePlanningPolicy(
        effective_pace=TravelPace.NORMAL,
        daily_time_rules=(),
        time_sensitive_must=(ProposedTimeSensitiveMustAnchor(
            anchor_id="anchor-verified", requirement_group_id="anchor-verified",
            date=date(2026, 9, 3), event_at=datetime(2026, 9, 3, 10, 15, tzinfo=SHANGHAI),
            source="verified_evidence", source_handle="evidence-1",
        ),),
        transport_rules=(),
    )
    with pytest.raises(_PolicyBusinessRejected, match="structured_demo_deferred_policy_rule_rejected"):
        effective_policy_from_proposal(
            proposal,
            confirmed=_confirmed(pace=None, agent_decides=True),
            source_handle_indexes={"evidence-1": 4},
        )


def test_user_activity_anchor_requires_confirmed_candidate_date_and_time() -> None:
    confirmed = _confirmed(pace=None, agent_decides=True).model_copy(update={
        "requirement": _confirmed(pace=None, agent_decides=True).requirement.model_copy(update={
            "preferences": TravelPreferences(
                daily_time_preferences="9月3日允许活动到21点",
                free_text_notes="9月3日景点活动必须10:15开始",
                pace=None,
                agent_decides_pace=True,
                transport_preferences="公共交通优先",
            ),
        }),
    })
    anchor = TimeSensitiveMustAnchor(
        anchor_id="activity-anchor", requirement_group_id="activity-anchor",
        date=date(2026, 9, 3), event_at=datetime(2026, 9, 3, 10, 15, tzinfo=SHANGHAI),
        source="user_provided", source_ref_index=None,
    )
    validate_user_time_sensitive_anchor_source(
        confirmed, anchor, visit_kind=VisitKind.ATTRACTION,
        candidate_name="景点", candidate_names=("景点", "餐馆"),
    )
    with pytest.raises(ValueError):
        validate_user_time_sensitive_anchor_source(
            confirmed, anchor.model_copy(update={"event_at": datetime(2026, 9, 3, 10, 30, tzinfo=SHANGHAI)}),
            visit_kind=VisitKind.ATTRACTION, candidate_name="景点", candidate_names=("景点", "餐馆"),
        )


def test_policy_conversion_rejects_pace_change_after_fixed_or_locked_choice() -> None:
    proposal = ProposedEffectivePlanningPolicy(effective_pace=TravelPace.INTENSE, daily_time_rules=(), time_sensitive_must=(), transport_rules=())
    with pytest.raises(ValueError):
        effective_policy_from_proposal(proposal, confirmed=_confirmed(pace=TravelPace.NORMAL, agent_decides=False))
    locked = ProposedEffectivePlanningPolicy(effective_pace=TravelPace.NORMAL, daily_time_rules=(), time_sensitive_must=(), transport_rules=())
    existing = effective_policy_from_proposal(locked, confirmed=_confirmed(pace=None, agent_decides=True))
    with pytest.raises(ValueError):
        effective_policy_from_proposal(proposal, confirmed=_confirmed(pace=None, agent_decides=True), locked_policy=existing)


def test_policy_business_conflicts_are_typed_but_structure_errors_remain_value_errors() -> None:
    pace_conflict = ProposedEffectivePlanningPolicy(
        effective_pace=TravelPace.INTENSE, daily_time_rules=(), time_sensitive_must=(), transport_rules=()
    )
    with pytest.raises(_PolicyBusinessRejected, match="pace"):
        effective_policy_from_proposal(
            pace_conflict, confirmed=_confirmed(pace=TravelPace.NORMAL, agent_decides=False)
        )

    missing_daily_source = ProposedEffectivePlanningPolicy(
        effective_pace=TravelPace.NORMAL,
        daily_time_rules=(ProposedDailyTimeRule(
            rule_id="daily-missing", date=date(2026, 9, 3),
            start_at=datetime(2026, 9, 3, 9, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 21, tzinfo=SHANGHAI),
            strength="hard", source_field="preferences.daily_time_preferences",
        ),),
        time_sensitive_must=(), transport_rules=(),
    )
    with pytest.raises(_PolicyBusinessRejected, match="structured_demo_deferred_policy_rule_rejected"):
        effective_policy_from_proposal(
            missing_daily_source,
            confirmed=_confirmed(pace=None, agent_decides=True).model_copy(update={
                "requirement": _confirmed(pace=None, agent_decides=True).requirement.model_copy(update={
                    "preferences": TravelPreferences(
                        pace=None, agent_decides_pace=True,
                        daily_time_preferences=None,
                        transport_preferences="公共交通优先",
                    ),
                }),
            }),
        )

    with pytest.raises(_PolicyBusinessRejected, match="structured_demo_deferred_policy_rule_rejected"):
        effective_policy_from_proposal(
            missing_daily_source.model_copy(update={
                "daily_time_rules": (missing_daily_source.daily_time_rules[0].model_copy(
                    update={"source_field": "preferences.transport_preferences"}
                ),),
            }),
            confirmed=_confirmed(pace=None, agent_decides=True),
        )


def _locked_policy_fixture() -> tuple[
    ProposedEffectivePlanningPolicy,
    ConfirmedTravelRequirement,
]:
    proposal = ProposedEffectivePlanningPolicy(
        effective_pace=TravelPace.NORMAL,
        daily_time_rules=(ProposedDailyTimeRule(
            rule_id="daily-1", date=None,
            start_at=datetime(2026, 9, 3, 9, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 21, tzinfo=SHANGHAI),
            strength="hard", source_field="preferences.daily_time_preferences",
        ),),
        time_sensitive_must=(ProposedTimeSensitiveMustAnchor(
            anchor_id="anchor-1", requirement_group_id="must-1", date=datetime(2026, 9, 3).date(),
            event_at=datetime(2026, 9, 3, 8, 15, tzinfo=SHANGHAI), source="user_provided",
            source_handle=None,
        ),),
        transport_rules=(ProposedTransportPolicyRule(
            rule_id="transport-1", semantic=TransportSemantic.PUBLIC_TRANSIT,
            strength="soft", numeric_limit_minutes=90, source_field="preferences.transport_preferences",
        ),),
    )
    return proposal, _confirmed(pace=None, agent_decides=True)


@pytest.mark.parametrize("change", ["delete", "replace", "add", "reorder"])
def test_locked_policy_rejects_daily_rule_mutation_and_order_change(change: str) -> None:
    proposal, confirmed = _locked_policy_fixture()
    assert change in {"delete", "replace", "add", "reorder"}
    with pytest.raises(_PolicyBusinessRejected, match="structured_demo_deferred_policy_rule_rejected"):
        effective_policy_from_proposal(proposal, confirmed=confirmed)


def test_locked_policy_rejects_anchor_transport_and_forged_source_changes() -> None:
    proposal, confirmed = _locked_policy_fixture()
    changed_anchor = proposal.model_copy(update={
        "time_sensitive_must": (
            proposal.time_sensitive_must[0].model_copy(update={"event_at": datetime(2026, 9, 3, 8, 45, tzinfo=SHANGHAI)}),
        ),
    })
    changed_transport = proposal.model_copy(update={
        "transport_rules": (
            proposal.transport_rules[0].model_copy(update={"numeric_limit_minutes": 120}),
        ),
    })
    forged_source = proposal.model_copy(update={
        "daily_time_rules": (
            proposal.daily_time_rules[0].model_copy(update={"source_field": "preferences.transport_preferences"}),
        ),
    })
    for changed in (changed_anchor, changed_transport, forged_source):
        with pytest.raises(_PolicyBusinessRejected, match="structured_demo_deferred_policy_rule_rejected"):
            effective_policy_from_proposal(changed, confirmed=confirmed)


def test_first_lock_binds_confirmed_source_and_identical_replay_is_stable() -> None:
    confirmed = _confirmed(pace=None, agent_decides=True)
    proposal = ProposedEffectivePlanningPolicy(
        effective_pace=TravelPace.NORMAL, daily_time_rules=(), time_sensitive_must=(), transport_rules=()
    )
    locked = effective_policy_from_proposal(proposal, confirmed=confirmed)
    replay = effective_policy_from_proposal(proposal, confirmed=confirmed, locked_policy=locked)
    assert replay == locked
    changed_confirmed = confirmed.model_copy(update={
        "requirement": confirmed.requirement.model_copy(update={
            "preferences": confirmed.requirement.preferences.model_copy(update={
                "daily_time_preferences": "每天10点后开始游览",
            }),
        }),
    })
    assert effective_policy_from_proposal(proposal, confirmed=changed_confirmed, locked_policy=locked) == locked


def test_locked_policy_binds_complete_confirmed_field_metadata() -> None:
    confirmed = _confirmed(pace=None, agent_decides=True)
    proposal = ProposedEffectivePlanningPolicy(
        effective_pace=TravelPace.NORMAL, daily_time_rules=(), time_sensitive_must=(), transport_rules=()
    )
    metadata = FieldMetadataRegistry(metadata={
        RequirementFieldPath.DAILY_TIME_PREFERENCES: FieldMetadata(
            status=FieldStatus.CONFIRMED,
            evidence_ref="evidence-daily",
            evidence_digest="sha256:" + "e" * 64,
        ),
    })
    confirmed_with_metadata = confirmed.model_copy(update={"metadata_without_raw_evidence": metadata})
    locked = effective_policy_from_proposal(proposal, confirmed=confirmed_with_metadata)
    changed_metadata = metadata.model_copy(update={
        "metadata": {
            RequirementFieldPath.DAILY_TIME_PREFERENCES: metadata.metadata[RequirementFieldPath.DAILY_TIME_PREFERENCES].model_copy(
                update={"evidence_ref": "evidence-daily-replaced"}
            ),
        },
    })
    changed_confirmed = confirmed_with_metadata.model_copy(update={"metadata_without_raw_evidence": changed_metadata})
    assert effective_policy_from_proposal(proposal, confirmed=changed_confirmed, locked_policy=locked) == locked


def test_policy_primitives_cover_invalid_time_meal_route_and_anchor_paths() -> None:
    with pytest.raises(ValueError):
        ceil_to_grid(datetime(2026, 9, 3, 9, 0))
    with pytest.raises(ValueError):
        ceil_to_grid(datetime(2026, 9, 3, 9, 0, tzinfo=ZoneInfo("UTC")))
    assert boundary_processing_minutes(HubType.RAILWAY_STATION, "departure", is_approximate=False) == 60
    assert boundary_processing_minutes(HubType.AIRPORT, "arrival", is_approximate=False, user_minutes=60) == 60
    assert boundary_processing_minutes(HubType.RAILWAY_STATION, "departure", is_approximate=True) == 60
    with pytest.raises(ValueError):
        boundary_processing_minutes(HubType.AIRPORT, "other", is_approximate=False)
    with pytest.raises(ValueError):
        route_scheduled_minutes(-1)
    with pytest.raises(ValueError):
        route_scheduled_minutes(True)
    assert {route_mode_for_semantic(item) for item in TransportSemantic} == {
        TravelMode.WALKING, TravelMode.RIDING, TravelMode.TRANSIT, TravelMode.DRIVING,
    }
    assert attraction_bounds(TravelPace.RELAXED, PlanningDayKind.PARTIAL) == (0, 2)
    assert attraction_bounds(TravelPace.INTENSE, PlanningDayKind.PARTIAL) == (0, 4)
    assert attraction_bounds(TravelPace.NORMAL, PlanningDayKind.TRANSIT_ONLY) == (0, 0)
    assert default_open_interval(date(2026, 9, 3)).start_at.time() == time(9, 0)

    attraction = _simple_visit()
    lunch = attraction.model_copy(update={"visit_id": "lunch", "visit_kind": VisitKind.LUNCH})
    dinner = attraction.model_copy(update={"visit_id": "dinner", "visit_kind": VisitKind.DINNER})
    partial = DayPlan.model_construct(day_kind=PlanningDayKind.PARTIAL, visits=(attraction,), route_legs=(), backups=(), open_intervals=())
    validate_meal_slots(partial)
    validate_day_policy(DayPlan.model_construct(day_kind=PlanningDayKind.PARTIAL, visits=(), route_legs=(), backups=(), open_intervals=()), TravelPace.NORMAL)
    with pytest.raises(ValueError):
        validate_meal_slots(DayPlan.model_construct(day_kind=PlanningDayKind.FULL, visits=(attraction,), route_legs=(), backups=(), open_intervals=()))
    with pytest.raises(ValueError):
        validate_meal_slots(DayPlan.model_construct(day_kind=PlanningDayKind.TRANSIT_ONLY, visits=(lunch,), route_legs=(), backups=(), open_intervals=()))
    lunch_two = lunch.model_copy(update={"visit_id": "lunch-two"})
    full = DayPlan.model_construct(day_kind=PlanningDayKind.FULL, visits=(attraction, lunch, lunch_two), route_legs=(), backups=(), open_intervals=())
    with pytest.raises(ValueError):
        validate_meal_slots(full)

    dinner = _simple_visit(VisitKind.DINNER).model_copy(update={
        "interval": TimeInterval(
            start_at=datetime(2026, 9, 3, 20, 0, tzinfo=SHANGHAI),
            end_at=datetime(2026, 9, 3, 21, 0, tzinfo=SHANGHAI),
        ),
        "time_sensitive_anchor_ids": ("dinner-anchor",),
    })
    dinner_day = DayPlan.model_construct(
        day_kind=PlanningDayKind.PARTIAL, visits=(dinner,), route_legs=(), backups=(), open_intervals=(),
    )
    user_anchor = TimeSensitiveMustAnchor(
        anchor_id="dinner-anchor", requirement_group_id="dinner", date=date(2026, 9, 3),
        event_at=datetime(2026, 9, 3, 20, 0, tzinfo=SHANGHAI), source="user_provided", source_ref_index=None,
    )
    validate_meal_slots(dinner_day, time_sensitive_must=(user_anchor,))
    with pytest.raises(ValueError):
        validate_meal_slots(dinner_day)
    quarter_anchor = user_anchor.model_copy(update={
        "event_at": datetime(2026, 9, 3, 20, 15, tzinfo=SHANGHAI),
    })
    validate_meal_slots(dinner_day, time_sensitive_must=(quarter_anchor,))
    with pytest.raises(ValueError):
        validate_meal_slots(
            dinner_day.model_copy(update={"visits": (dinner.model_copy(update={
                "interval": TimeInterval(
                    start_at=datetime(2026, 9, 3, 19, 0, tzinfo=SHANGHAI),
                    end_at=datetime(2026, 9, 3, 20, 0, tzinfo=SHANGHAI),
                ),
            }),)}),
            time_sensitive_must=(quarter_anchor,),
        )
    verified_anchor = user_anchor.model_copy(update={"source": "verified_evidence", "source_ref_index": 0})
    with pytest.raises(ValueError):
        validate_meal_slots(dinner_day, time_sensitive_must=(verified_anchor,))

    confirmed = _confirmed(pace=None, agent_decides=True).model_copy(update={
        "requirement": _confirmed(pace=None, agent_decides=True).requirement.model_copy(update={
            "preferences": TravelPreferences(
                daily_time_preferences="9月3日允许活动到22点；杭州候选3有20:15的晚餐预约",
            ),
        }),
    })
    validate_user_meal_anchor_source(
        confirmed,
        quarter_anchor,
        visit_kind=VisitKind.DINNER,
        candidate_name="杭州候选3",
    )
    confirmed_19 = confirmed.model_copy(update={
        "requirement": confirmed.requirement.model_copy(update={
            "preferences": TravelPreferences(
                daily_time_preferences="9月3日允许活动到22点；杭州候选3晚餐必须19:00开始，不要安排20:00晚餐",
            ),
        }),
    })
    validate_user_meal_anchor_source(
        confirmed_19,
        user_anchor.model_copy(update={
            "event_at": datetime(2026, 9, 3, 19, 0, tzinfo=SHANGHAI),
        }),
        visit_kind=VisitKind.DINNER,
        candidate_name="杭州候选3",
    )
    for source in (
        "9月3日允许活动到22点",
        "9月3日允许活动到22点；杭州候选3晚餐必须19:00开始，不要安排20:00晚餐",
    ):
        rejected = confirmed.model_copy(update={
            "requirement": confirmed.requirement.model_copy(update={
                "preferences": TravelPreferences(daily_time_preferences=source),
            }),
        })
        with pytest.raises(ValueError):
            validate_user_meal_anchor_source(
                rejected,
                user_anchor,
                visit_kind=VisitKind.DINNER,
                candidate_name="杭州候选3",
            )

    short_leg = type("Leg", (), {"duration_s": 60 * 60 + 1})()
    route_day = DayPlan.model_construct(day_kind=PlanningDayKind.PARTIAL, visits=(), route_legs=(short_leg,), backups=(), open_intervals=())
    with pytest.raises(ValueError):
        validate_route_capacity(route_day, TravelPace.RELAXED)
    with pytest.raises(ValueError):
        validate_route_capacity(DayPlan.model_construct(day_kind=PlanningDayKind.FULL, visits=(), route_legs=(), backups=(), open_intervals=()), TravelPace.NORMAL, explicit_day_limit_seconds=0)

    validate_business_state(attraction)
    with pytest.raises(ValueError):
        validate_business_state(attraction.model_copy(update={"business_hours_state": BusinessHoursState.CONFLICTS}))
    with pytest.raises(ValueError):
        validate_business_state(attraction.model_copy(update={"business_hours_state": BusinessHoursState.UNKNOWN, "requires_verification": False}))
    verified = attraction.model_copy(update={"business_hours_state": BusinessHoursState.UNKNOWN, "requires_verification": True})
    validate_business_state(verified)

    anchor = TimeSensitiveMustAnchor(
        anchor_id="anchor", requirement_group_id="must", date=date(2026, 9, 3),
        event_at=datetime(2026, 9, 3, 10, 30, tzinfo=SHANGHAI), source="user_provided", source_ref_index=None,
    )
    covered = attraction.model_copy(update={"time_sensitive_anchor_ids": ("anchor",)})
    validate_time_sensitive_anchor(anchor, (covered,))
    with pytest.raises(ValueError):
        validate_time_sensitive_anchor(anchor, (attraction,))
    with pytest.raises(ValueError):
        validate_time_sensitive_anchor(anchor, (covered, covered.model_copy(update={"visit_id": "second", "time_sensitive_anchor_ids": ("anchor",)})))


def test_candidate_allowance_and_policy_json_normalization_are_strict() -> None:
    record = _candidate_record()
    assert candidate_is_eligible(record)
    assert candidate_is_eligible(record, ResearchRole.ATTRACTION)
    assert candidate_is_eligible(record, ResearchRole.RESTAURANT)
    assert not candidate_is_eligible(object())
    assert candidate_uid_is_allowed(record)
    confirmed_avoid = RequirementMatchState(
        requirement_group_id="avoid", verdict=MatchVerdict.CONFIRMED,
        reason_code=RequirementReasonCode.EXACT_NAME_MATCH, evidence_indexes=(0,),
    )
    unresolved_avoid = confirmed_avoid.model_copy(update={"verdict": MatchVerdict.UNRESOLVED})
    assert not candidate_uid_is_allowed(_candidate_record(avoid=(confirmed_avoid,), state=CandidateState.EXCLUDED))
    assert not candidate_uid_is_allowed(_candidate_record(avoid=(unresolved_avoid,), state=CandidateState.WITHHELD))
    assert candidate_uid_is_allowed(record, ResearchRole.RESTAURANT)

    interval = TimeInterval(start_at=datetime(2026, 9, 3, 9, tzinfo=SHANGHAI), end_at=datetime(2026, 9, 3, 10, tzinfo=SHANGHAI))
    assert _policy_json_value(None) is None
    assert _policy_json_value(True) is True
    assert _policy_json_value(1) == 1
    assert _policy_json_value(1.5) == 1.5
    assert _policy_json_value(TravelPace.NORMAL) == TravelPace.NORMAL.value
    assert _policy_json_value(date(2026, 9, 3)) == "2026-09-03"
    assert _policy_json_value(datetime(2026, 9, 3, 9, tzinfo=SHANGHAI)).startswith("2026-09-03T09:00")
    assert _policy_json_value(interval)["start_at"].startswith("2026-09-03T09:00")
    assert _policy_json_value((1, [2])) == [1, [2]]
    assert _policy_json_value({"x": 1}) == {"x": 1}
    with pytest.raises(ValueError):
        _policy_json_value(float("nan"))
    with pytest.raises(ValueError):
        _policy_json_value({1: "bad"})
    with pytest.raises(ValueError):
        _policy_json_value(time(9, 0))
    assert order_stop_reasons((PlanningStopReason.NO_PROGRESS, PlanningStopReason.USER_CLARIFICATION_REQUIRED, PlanningStopReason.NO_PROGRESS)) == (
        PlanningStopReason.USER_CLARIFICATION_REQUIRED, PlanningStopReason.NO_PROGRESS,
    )
