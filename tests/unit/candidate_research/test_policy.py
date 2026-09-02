import pytest

from app.candidate_research import CandidateResearchStopReason, MealIntent, ResearchRole, MatchVerdict, CandidateState, RoleVerdict, RoleReasonCode, ResearchObjectiveKind, HotelAnchorRef, CandidateAnchorRef
from app.candidate_research.models import CandidateRecord, CandidateCore, ArtifactCoordinateSnapshot, RoleAssessment, CandidateDetailsState
from app.requirements import InterestTag
from app.candidate_research.policy import (
    action_targets_highest_priority, attraction_target, candidate_capacity_remaining,
    compute_stop_reasons, details_target, group_requirement_items, has_progress,
    interest_search_and_eligible, meal_search_and_eligible, next_no_progress_streak, restaurant_target,
    role_target, stop_reasons, must_requirement_satisfied, avoid_requirement_satisfied, eligible_candidates,
    is_completed, initial_spatial_searches_complete, ResearchObjective, derive_candidate_state,
    objective_priority, candidate_state_priority, interest_covered, meal_covered,
    count_role, supplemental_gaps_satisfied, deterministic_objectives,
    highest_priority_objective_kind, action_targets_highest_priority, overflow_uids,
    attraction_target, restaurant_target, progress_flags,
)


def test_capacity_targets_details_and_streak_are_frozen_formulas():
    assert candidate_capacity_remaining(127) == 1
    assert details_target(1) == 1 and details_target(4) == 2
    assert attraction_target(2) == 12 and restaurant_target(2) == 8
    assert role_target(ResearchRole.ATTRACTION, 20) == 60
    assert role_target(ResearchRole.RESTAURANT, 20) == 40
    with pytest.raises(ValueError):
        candidate_capacity_remaining(129)
    with pytest.raises(ValueError):
        role_target(ResearchRole.ATTRACTION, 0)
    assert next_no_progress_streak(1, False) == 2
    assert next_no_progress_streak(2, False) == 2
    assert has_progress((False, True))
    assert has_progress(value for value in (False, True))
    with pytest.raises(ValueError):
        has_progress((1, False))
    with pytest.raises(ValueError):
        has_progress(value for value in (False, "true"))
    with pytest.raises(ValueError):
        has_progress(1)
    assert details_target(128) == 43
    with pytest.raises(ValueError):
        details_target(129)
    with pytest.raises(ValueError):
        details_target(True)


@pytest.mark.parametrize(
    ("role", "days", "expected"),
    [
        (ResearchRole.ATTRACTION, 1, 6),
        (ResearchRole.RESTAURANT, 10, 40),
        (ResearchRole.ATTRACTION, 20, 60),
    ],
)
def test_role_targets_preserve_demo_bounds(role, days, expected):
    assert role_target(role, days) == expected


def test_stop_reason_order_and_necessary_predicates():
    reasons = stop_reasons(candidate_capacity_full=True, place_budget_exhausted=True, iteration_limit_reached=False, no_progress_streak=2, no_legal_action=False, useful_supply_not_found=False)
    assert reasons == (CandidateResearchStopReason.CANDIDATE_CAPACITY_REACHED, CandidateResearchStopReason.PLACE_BUDGET_EXHAUSTED, CandidateResearchStopReason.NO_PROGRESS)
    computed = compute_stop_reasons(
        admitted_slots=128, has_unmet_new_candidate_goal=True, place_remaining=0,
        has_place_progress_goal=True, iteration_remaining=0, completed=False,
        no_progress_streak=2, has_unmet_goal=True, legal_action_count=0,
        supply_gaps=((True, False),),
    )
    assert computed == (
        CandidateResearchStopReason.CANDIDATE_CAPACITY_REACHED,
        CandidateResearchStopReason.PLACE_BUDGET_EXHAUSTED,
        CandidateResearchStopReason.ITERATION_LIMIT_REACHED,
        CandidateResearchStopReason.NO_PROGRESS,
        CandidateResearchStopReason.USEFUL_SUPPLY_NOT_FOUND,
    )
    not_budget_reason = compute_stop_reasons(
        admitted_slots=0, has_unmet_new_candidate_goal=True, place_remaining=0,
        has_place_progress_goal=False, iteration_remaining=1, completed=False,
        no_progress_streak=0, has_unmet_goal=True, legal_action_count=0,
        supply_gaps=(),
    )
    assert CandidateResearchStopReason.NO_LEGAL_ACTION not in not_budget_reason


def test_exact_grouping_and_search_gate_helpers():
    assert group_requirement_items((("i0", "故宫"), ("i1", "故宫"), ("i2", "西湖"))) == (("故宫", ("i0", "i1")), ("西湖", ("i2",)))
    with pytest.raises(ValueError):
        interest_search_and_eligible(search_done=False, candidates=(), interest="人文")
    with pytest.raises(ValueError):
        meal_search_and_eligible(search_done=False, candidates=(), meal="lunch")


def test_must_and_avoid_obligations_use_eligibility_and_exclusion():
    assert must_requirement_satisfied(((True, MatchVerdict.CONFIRMED), (True, MatchVerdict.NO_MATCH)))
    assert must_requirement_satisfied(((True, MatchVerdict.CONFIRMED), (True, MatchVerdict.AMBIGUOUS), (True, MatchVerdict.UNRESOLVED)))
    assert not must_requirement_satisfied(((False, MatchVerdict.CONFIRMED), (True, MatchVerdict.NO_MATCH)))
    assert not must_requirement_satisfied(((False, MatchVerdict.CONFIRMED), (True, MatchVerdict.AMBIGUOUS)))
    assert avoid_requirement_satisfied(((True, MatchVerdict.CONFIRMED), (False, MatchVerdict.NO_MATCH)))
    assert not avoid_requirement_satisfied(((False, MatchVerdict.CONFIRMED),))
    with pytest.raises(ValueError):
        must_requirement_satisfied(((1, MatchVerdict.CONFIRMED),))
    with pytest.raises(ValueError):
        avoid_requirement_satisfied(((True, "confirmed"),))
    with pytest.raises(ValueError):
        must_requirement_satisfied((MatchVerdict.CONFIRMED,))
    with pytest.raises(ValueError):
        avoid_requirement_satisfied((MatchVerdict.NO_MATCH,))


def test_eligible_filter_requires_a_supported_role_even_for_forged_state():
    candidate = CandidateRecord.model_construct(
        schema_version="1.0", admission_ordinal=0,
        core=CandidateCore.model_construct(uid="u1", name="Museum", address="Road", coordinate=ArtifactCoordinateSnapshot(coordinate_system="bd09ll", latitude=30.0, longitude=120.0), provider_category=None),
        state=CandidateState.ELIGIBLE,
        roles=(
            RoleAssessment.model_construct(role=ResearchRole.ATTRACTION, verdict=RoleVerdict.CONTRADICTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_CONTRADICTS, evidence_indexes=(0,)),
            RoleAssessment.model_construct(role=ResearchRole.RESTAURANT, verdict=RoleVerdict.UNRESOLVED, reason_code=RoleReasonCode.EVIDENCE_INSUFFICIENT, evidence_indexes=(0,)),
        ), interests=(), meal_intents=(), must_matches=(), avoid_matches=(),
        details=CandidateDetailsState.model_construct(status="not_requested", observation_index=None), observation_indexes=(0,),
        dietary_restrictions="not_evaluated", mobility_constraints="not_evaluated", budget_fit="not_evaluated",
    )
    assert eligible_candidates((candidate,)) == ()


def test_completion_requires_every_gate_and_strict_progress_inputs():
    base = dict(
        unresolved_must=0, unresolved_avoid=0, unresolved_role=0,
        candidate_counts_met=True, interests_covered=True, meals_covered=True,
        details_mature=True, supplemental_gaps_met=True,
        candidate_identity_stable=True, initial_searches_complete=True, role_blocking_resolved=True,
    )
    assert is_completed(**base)
    for key in ("candidate_counts_met", "interests_covered", "meals_covered", "details_mature", "supplemental_gaps_met", "candidate_identity_stable", "initial_searches_complete", "role_blocking_resolved"):
        changed = dict(base, **({key: False} if key in base else {key: False}))
        assert not is_completed(**changed)
    assert initial_spatial_searches_complete(city_attraction_done=True, hotel_restaurant_nearby_done=True)
    with pytest.raises(ValueError):
        initial_spatial_searches_complete(city_attraction_done=1, hotel_restaurant_nearby_done=True)
    with pytest.raises(TypeError):
        is_completed(**{key: value for key, value in base.items() if key != "candidate_identity_stable"})
    assert is_completed(**dict(base, unresolved_role=1))
    assert not is_completed(**dict(base, unresolved_role=1, role_blocking_resolved=False))


def test_stop_predicates_reject_impossible_supply_and_budget_only_no_legal_action():
    with pytest.raises(ValueError):
        compute_stop_reasons(
            admitted_slots=0, has_unmet_new_candidate_goal=False, place_remaining=1,
            has_place_progress_goal=False, iteration_remaining=1, completed=False,
            no_progress_streak=0, has_unmet_goal=True, legal_action_count=0,
            supply_gaps=((False, True),),
        )
    reasons = compute_stop_reasons(
        admitted_slots=0, has_unmet_new_candidate_goal=False, place_remaining=1,
        has_place_progress_goal=False, iteration_remaining=0, completed=False,
        no_progress_streak=0, has_unmet_goal=True, legal_action_count=0,
        supply_gaps=(),
    )
    assert CandidateResearchStopReason.NO_LEGAL_ACTION not in reasons
    with pytest.raises(ValueError):
        next_no_progress_streak(0, 1)


def test_research_objective_kind_fields_are_required_and_typed():
    common = dict(objective_id="objective:x", gap_id=None, requirement_group_id=None, role=None, interest=None, meal_intent=None, anchor=None, radius_km=None, candidate_uids=())
    valid = ResearchObjective(**{**common, "kind": ResearchObjectiveKind.INITIAL_CITY_ATTRACTION, "role": ResearchRole.ATTRACTION})
    assert valid.kind is ResearchObjectiveKind.INITIAL_CITY_ATTRACTION
    with pytest.raises(ValueError):
        ResearchObjective(**{**common, "kind": ResearchObjectiveKind.ATTRACTION_COUNT})
    with pytest.raises(ValueError):
        ResearchObjective(**{**common, "kind": ResearchObjectiveKind.MUST_VISIT_RESOLUTION})
    with pytest.raises(ValueError):
        ResearchObjective(**{**common, "kind": ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY, "role": ResearchRole.RESTAURANT, "anchor": HotelAnchorRef()})
    with pytest.raises(ValueError):
        ResearchObjective(**{**common, "kind": ResearchObjectiveKind.DETAILS_MATURITY})


def test_avoid_resolution_objective_keeps_typed_candidate_target_after_json_round_trip():
    from app.candidate_research.policy import ResearchObjective
    objective = ResearchObjective(
        objective_id="objective:avoid", kind=ResearchObjectiveKind.AVOID_RESOLUTION,
        gap_id=None, requirement_group_id="group:avoid", role=None, interest=None,
        meal_intent=None, anchor=None, radius_km=None, candidate_uids=("u1", "u2"),
    )
    assert ResearchObjective.model_validate_json(objective.model_dump_json()) == objective


def test_impossible_candidate_role_predicates_are_rejected():
    with pytest.raises(ValueError):
        derive_candidate_state(
            core_conflicted=False, confirmed_must=False, confirmed_avoid=False,
            avoid_unresolved=False, supported_role=False, unresolved_role=False,
            all_roles_contradicted=False,
        )
    with pytest.raises(ValueError):
        derive_candidate_state(
            core_conflicted=False, confirmed_must=False, confirmed_avoid=False,
            avoid_unresolved=False, supported_role=True, unresolved_role=False,
            all_roles_contradicted=True,
        )


@pytest.mark.parametrize("kind", tuple(ResearchObjectiveKind))
def test_every_objective_kind_has_a_deterministic_priority(kind):
    assert objective_priority(kind) == list(ResearchObjectiveKind).index(kind)


@pytest.mark.parametrize("bad", ("not-a-kind", None, 1))
def test_policy_enum_guards_fail_closed(bad):
    with pytest.raises(ValueError):
        objective_priority(bad)
    with pytest.raises(ValueError):
        candidate_state_priority(bad)
    with pytest.raises(ValueError):
        count_role((), bad)
    with pytest.raises(ValueError):
        interest_covered((), bad)
    with pytest.raises(ValueError):
        meal_covered((), bad)


@pytest.mark.parametrize(
    ("state", "expected"),
    [(CandidateState.ELIGIBLE, 0), (CandidateState.WITHHELD, 1), (CandidateState.EXCLUDED, 2), (CandidateState.CONFLICTED, 3)],
)
def test_candidate_state_priority_is_total_and_ordered(state, expected):
    assert candidate_state_priority(state) == expected


@pytest.mark.parametrize(
    ("role", "days", "expected"),
    [(ResearchRole.ATTRACTION, 1, 6), (ResearchRole.ATTRACTION, 60, 60), (ResearchRole.RESTAURANT, 1, 4), (ResearchRole.RESTAURANT, 60, 40)],
)
def test_role_target_boundaries_cover_both_saturation_branches(role, days, expected):
    assert role_target(role, days) == expected


@pytest.mark.parametrize("value", (0, 128))
def test_capacity_and_details_boundaries(value):
    assert candidate_capacity_remaining(value) == 128 - value
    assert details_target(value) == (value + 2) // 3


@pytest.mark.parametrize("bad", (True, -1, 129, 1.0))
def test_policy_numeric_guards_reject_bool_and_out_of_range(bad):
    with pytest.raises(ValueError):
        candidate_capacity_remaining(bad)
    with pytest.raises(ValueError):
        details_target(bad)
    with pytest.raises(ValueError):
        attraction_target(bad)
    with pytest.raises(ValueError):
        restaurant_target(bad)


def test_derive_candidate_state_covers_each_precedence_result():
    common = dict(confirmed_must=False, confirmed_avoid=False, avoid_unresolved=False, supported_role=True, unresolved_role=False, all_roles_contradicted=False)
    assert derive_candidate_state(core_conflicted=True, **common) is CandidateState.CONFLICTED
    assert derive_candidate_state(core_conflicted=False, confirmed_must=True, confirmed_avoid=True, avoid_unresolved=False, supported_role=True, unresolved_role=False, all_roles_contradicted=False) is CandidateState.CONFLICTED
    assert derive_candidate_state(core_conflicted=False, confirmed_must=False, confirmed_avoid=True, avoid_unresolved=False, supported_role=True, unresolved_role=False, all_roles_contradicted=False) is CandidateState.EXCLUDED
    assert derive_candidate_state(core_conflicted=False, confirmed_must=False, confirmed_avoid=False, avoid_unresolved=False, supported_role=False, unresolved_role=False, all_roles_contradicted=True) is CandidateState.EXCLUDED
    assert derive_candidate_state(core_conflicted=False, confirmed_must=False, confirmed_avoid=False, avoid_unresolved=True, supported_role=True, unresolved_role=False, all_roles_contradicted=False) is CandidateState.WITHHELD
    assert derive_candidate_state(core_conflicted=False, confirmed_must=False, confirmed_avoid=False, avoid_unresolved=False, supported_role=False, unresolved_role=True, all_roles_contradicted=False) is CandidateState.WITHHELD
    assert derive_candidate_state(**common, core_conflicted=False) is CandidateState.ELIGIBLE


@pytest.mark.parametrize("flags", [(False, False), (True, False), (False, True), (True, True)])
def test_search_and_eligible_helpers_require_both_predicates(flags):
    assert initial_spatial_searches_complete(city_attraction_done=flags[0], hotel_restaurant_nearby_done=flags[1]) is (flags == (True, True))
    assert interest_search_and_eligible(search_done=flags[0], candidates=(), interest=InterestTag.CULTURE) is False
    assert meal_search_and_eligible(search_done=flags[1], candidates=(), meal=MealIntent.LUNCH) is False


@pytest.mark.parametrize("verdict,expected", [(MatchVerdict.CONFIRMED, True), (MatchVerdict.NO_MATCH, True), (MatchVerdict.AMBIGUOUS, False), (MatchVerdict.UNRESOLVED, False)])
def test_resolution_and_requirement_predicates_cover_all_verdicts(verdict, expected):
    from app.candidate_research.policy import must_avoid_resolved
    assert must_avoid_resolved(verdict) is expected
    assert must_requirement_satisfied(((True, verdict),)) is (verdict is MatchVerdict.CONFIRMED)
    assert avoid_requirement_satisfied(((verdict is MatchVerdict.CONFIRMED, verdict),)) is (verdict in {MatchVerdict.NO_MATCH, MatchVerdict.CONFIRMED})


def test_requirement_predicates_cover_empty_and_malformed_inputs():
    assert must_requirement_satisfied(()) is False
    assert avoid_requirement_satisfied(()) is False
    with pytest.raises(ValueError):
        must_requirement_satisfied(((True, MatchVerdict.CONFIRMED, "extra"),))
    with pytest.raises(ValueError):
        avoid_requirement_satisfied(((False, "bad"),))


@pytest.mark.parametrize("previous", (0, 1, 2))
@pytest.mark.parametrize("progressed", (True, False))
def test_progress_streak_and_flags_are_closed(progressed, previous):
    assert next_no_progress_streak(previous, progressed) == (0 if progressed else min(2, previous + 1))
    flags = progress_flags(added_eligible_uid=progressed)
    assert flags[0] is progressed and has_progress(flags) is progressed


def test_stop_reasons_cover_each_independent_flag_and_no_flag_case():
    assert stop_reasons(candidate_capacity_full=False, place_budget_exhausted=False, iteration_limit_reached=False, no_progress_streak=0, no_legal_action=False, useful_supply_not_found=False) == ()
    for kwargs, expected in (
        ({"candidate_capacity_full": True}, CandidateResearchStopReason.CANDIDATE_CAPACITY_REACHED),
        ({"place_budget_exhausted": True}, CandidateResearchStopReason.PLACE_BUDGET_EXHAUSTED),
        ({"iteration_limit_reached": True}, CandidateResearchStopReason.ITERATION_LIMIT_REACHED),
        ({"no_progress_streak": 2}, CandidateResearchStopReason.NO_PROGRESS),
        ({"no_legal_action": True}, CandidateResearchStopReason.NO_LEGAL_ACTION),
        ({"useful_supply_not_found": True}, CandidateResearchStopReason.USEFUL_SUPPLY_NOT_FOUND),
    ):
        base = {"candidate_capacity_full": False, "place_budget_exhausted": False, "iteration_limit_reached": False, "no_progress_streak": 0, "no_legal_action": False, "useful_supply_not_found": False}
        base.update(kwargs)
        assert expected in stop_reasons(**base)


def test_compute_stop_reasons_covers_no_legal_action_and_supply_success():
    no_legal = compute_stop_reasons(admitted_slots=0, has_unmet_new_candidate_goal=False, place_remaining=1, has_place_progress_goal=False, iteration_remaining=2, completed=False, no_progress_streak=0, has_unmet_goal=True, legal_action_count=0, supply_gaps=())
    assert no_legal == (CandidateResearchStopReason.NO_LEGAL_ACTION,)
    useful = compute_stop_reasons(admitted_slots=0, has_unmet_new_candidate_goal=False, place_remaining=1, has_place_progress_goal=False, iteration_remaining=2, completed=False, no_progress_streak=0, has_unmet_goal=True, legal_action_count=1, supply_gaps=((True, True), (False, False)))
    assert useful == ()


def test_objective_order_and_action_target_cover_empty_and_matching_cases():
    base = dict(objective_id="o1", gap_id=None, requirement_group_id=None, role=None, interest=None, meal_intent=None, anchor=None, radius_km=None, candidate_uids=())
    first = ResearchObjective(**{**base, "kind": ResearchObjectiveKind.DIVERSITY})
    second = ResearchObjective(**{**base, "objective_id": "o2", "kind": ResearchObjectiveKind.ATTRACTION_COUNT, "role": ResearchRole.ATTRACTION})
    ordered = deterministic_objectives((first, second))
    assert ordered[0] is second and highest_priority_objective_kind(()) is None
    assert action_targets_highest_priority(actions=(), objectives=())
    assert action_targets_highest_priority(actions=(), objectives=(first,)) is False


def test_overflow_and_grouping_preserve_order_and_validate_capacity():
    assert overflow_uids((), 0) == ()
    with pytest.raises(ValueError):
        overflow_uids((), 129)
    assert group_requirement_items((('a', 'x'), ('b', 'y'), ('c', 'x'))) == (('x', ('a', 'c')), ('y', ('b',)))
