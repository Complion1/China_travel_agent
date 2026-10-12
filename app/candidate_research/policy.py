"""候选研究控制器使用的纯确定性 policy 函数。"""

from __future__ import annotations

import math
from typing import Iterable, Literal

from pydantic import Field, field_validator, model_validator

from .models import (
    CandidateRecord, CandidateResearchStopReason, InterestTag, MealIntent, MatchVerdict,
    ResearchAnchorRef, ResearchBudgetSummary, ResearchCountSummary, ResearchCoverageSummary,
    ResearchGapSummary, ResearchObjectiveKind, ResearchRole, CandidateState, _PublicModel, StableId, BoundedText,
    ProposedAction,
)


class ResearchObjective(_PublicModel):
    objective_id: StableId
    kind: ResearchObjectiveKind
    gap_id: StableId | None
    requirement_group_id: StableId | None
    role: ResearchRole | None
    interest: InterestTag | None
    meal_intent: MealIntent | None
    anchor: ResearchAnchorRef | None
    radius_km: Literal[1, 3, 5, 10] | None
    candidate_uids: tuple[BoundedText, ...]
    gap_type: StableId | None = None
    rejected_uid: BoundedText | None = None

    @field_validator("candidate_uids")
    @classmethod
    def _validate_uids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 128 or len(set(value)) != len(value):
            raise ValueError("candidate uids must be unique and bounded")
        return value

    @model_validator(mode="after")
    def _kind_fields(self) -> "ResearchObjective":
        kind = self.kind
        if (self.anchor is None) != (self.radius_km is None):
            raise ValueError("objective anchor/radius must be paired")
        if kind is ResearchObjectiveKind.SUPPLEMENTAL_GAP:
            if self.gap_id is None:
                raise ValueError("supplemental objective requires gap_id")
        elif kind in {ResearchObjectiveKind.MUST_VISIT_RESOLUTION, ResearchObjectiveKind.AVOID_RESOLUTION}:
            if self.requirement_group_id is None:
                raise ValueError("requirement objective requires requirement_group_id")
            if any(value is not None for value in (self.gap_id, self.role, self.interest, self.meal_intent, self.anchor, self.radius_km)):
                raise ValueError("requirement objective has unrelated fields")
        elif kind is ResearchObjectiveKind.INITIAL_CITY_ATTRACTION:
            if self.role is not ResearchRole.ATTRACTION or any(value is not None for value in (self.gap_id, self.requirement_group_id, self.interest, self.meal_intent, self.anchor, self.radius_km)) or self.candidate_uids:
                raise ValueError("initial city objective fields are inconsistent")
        elif kind is ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY:
            if self.role is not ResearchRole.RESTAURANT or self.anchor is None or getattr(self.anchor, "anchor_type", None) != "hotel" or self.radius_km is None:
                raise ValueError("initial hotel nearby objective requires hotel restaurant target")
            if any(value is not None for value in (self.gap_id, self.requirement_group_id, self.interest, self.meal_intent)) or self.candidate_uids:
                raise ValueError("initial hotel objective has unrelated fields")
        elif kind is ResearchObjectiveKind.INTEREST_COVERAGE:
            if self.interest is None:
                raise ValueError("interest objective requires interest")
            if any(value is not None for value in (self.gap_id, self.requirement_group_id, self.role, self.meal_intent)) or self.candidate_uids:
                raise ValueError("interest objective has unrelated fields")
        elif kind in {ResearchObjectiveKind.MEAL_LUNCH, ResearchObjectiveKind.MEAL_DINNER}:
            expected = MealIntent.LUNCH if kind is ResearchObjectiveKind.MEAL_LUNCH else MealIntent.DINNER
            if self.meal_intent is not expected or self.role is not ResearchRole.RESTAURANT:
                raise ValueError("meal objective requires matching restaurant intent")
            if any(value is not None for value in (self.gap_id, self.requirement_group_id, self.interest)) or self.candidate_uids:
                raise ValueError("meal objective has unrelated fields")
        elif kind is ResearchObjectiveKind.ATTRACTION_COUNT:
            if self.role is not ResearchRole.ATTRACTION or any(value is not None for value in (self.gap_id, self.requirement_group_id, self.interest, self.meal_intent, self.anchor, self.radius_km)) or self.candidate_uids:
                raise ValueError("attraction count objective fields are inconsistent")
        elif kind is ResearchObjectiveKind.RESTAURANT_COUNT:
            if self.role is not ResearchRole.RESTAURANT or any(value is not None for value in (self.gap_id, self.requirement_group_id, self.interest, self.meal_intent, self.anchor, self.radius_km)) or self.candidate_uids:
                raise ValueError("restaurant count objective fields are inconsistent")
        elif kind is ResearchObjectiveKind.DETAILS_MATURITY:
            if not self.candidate_uids or any(value is not None for value in (self.gap_id, self.requirement_group_id, self.role, self.interest, self.meal_intent, self.anchor, self.radius_km)):
                raise ValueError("details objective fields are inconsistent")
        elif kind is ResearchObjectiveKind.DIVERSITY:
            if any(value is not None for value in (self.gap_id, self.requirement_group_id, self.role, self.interest, self.meal_intent, self.anchor, self.radius_km)) or self.candidate_uids:
                raise ValueError("diversity objective has unrelated fields")
        return self


OBJECTIVE_PRIORITY: tuple[ResearchObjectiveKind, ...] = (
    ResearchObjectiveKind.SUPPLEMENTAL_GAP,
    ResearchObjectiveKind.MUST_VISIT_RESOLUTION,
    ResearchObjectiveKind.AVOID_RESOLUTION,
    ResearchObjectiveKind.INITIAL_CITY_ATTRACTION,
    ResearchObjectiveKind.INITIAL_HOTEL_RESTAURANT_NEARBY,
    ResearchObjectiveKind.MEAL_LUNCH,
    ResearchObjectiveKind.MEAL_DINNER,
    ResearchObjectiveKind.INTEREST_COVERAGE,
    ResearchObjectiveKind.ATTRACTION_COUNT,
    ResearchObjectiveKind.RESTAURANT_COUNT,
    ResearchObjectiveKind.DETAILS_MATURITY,
    ResearchObjectiveKind.DIVERSITY,
)


def objective_priority(kind: ResearchObjectiveKind) -> int:
    if not isinstance(kind, ResearchObjectiveKind):
        raise ValueError("unknown objective kind")
    return OBJECTIVE_PRIORITY.index(kind)


def candidate_state_priority(state: CandidateState) -> int:
    if not isinstance(state, CandidateState):
        raise ValueError("unknown candidate state")
    return {
        CandidateState.ELIGIBLE: 0,
        CandidateState.WITHHELD: 1,
        CandidateState.EXCLUDED: 2,
        CandidateState.CONFLICTED: 3,
    }[state]


def candidate_capacity_remaining(admitted_slots: int) -> int:
    if type(admitted_slots) is not int or not 0 <= admitted_slots <= 128:
        raise ValueError("invalid admitted slot count")
    return 128 - admitted_slots


def details_target(unique_eligible_count: int) -> int:
    if type(unique_eligible_count) is not int or not 0 <= unique_eligible_count <= 128:
        raise ValueError("invalid candidate count")
    return math.ceil(unique_eligible_count / 3)


def role_target(role: ResearchRole, sightseeing_days: int) -> int:
    if type(sightseeing_days) is not int or not 1 <= sightseeing_days <= 60:
        raise ValueError("invalid sightseeing days")
    if role is ResearchRole.ATTRACTION:
        return attraction_target(sightseeing_days)
    if role is ResearchRole.RESTAURANT:
        return restaurant_target(sightseeing_days)
    raise ValueError("unknown role")


def attraction_target(sightseeing_days: int) -> int:
    if type(sightseeing_days) is not int or not 1 <= sightseeing_days <= 60:
        raise ValueError("invalid sightseeing days")
    return min(sightseeing_days * 6, 60)


def restaurant_target(sightseeing_days: int) -> int:
    if type(sightseeing_days) is not int or not 1 <= sightseeing_days <= 60:
        raise ValueError("invalid sightseeing days")
    return min(sightseeing_days * 4, 40)


def eligible_candidates(candidates: Iterable[CandidateRecord]) -> tuple[CandidateRecord, ...]:
    return tuple(
        candidate for candidate in candidates
        if candidate.state is CandidateState.ELIGIBLE
        and any(item.verdict.value == "supported" for item in candidate.roles)
    )


def derive_candidate_state(*, core_conflicted: bool, confirmed_must: bool, confirmed_avoid: bool, avoid_unresolved: bool, supported_role: bool, unresolved_role: bool, all_roles_contradicted: bool) -> CandidateState:
    flags = (core_conflicted, confirmed_must, confirmed_avoid, avoid_unresolved, supported_role, unresolved_role, all_roles_contradicted)
    if any(type(value) is not bool for value in flags):
        raise ValueError("candidate state predicates must be bool")
    if (supported_role and all_roles_contradicted) or (unresolved_role and all_roles_contradicted) or (not supported_role and not unresolved_role and not all_roles_contradicted):
        raise ValueError("candidate role predicates are impossible")
    if core_conflicted or (confirmed_must and confirmed_avoid):
        return CandidateState.CONFLICTED
    if confirmed_avoid or all_roles_contradicted:
        return CandidateState.EXCLUDED
    if avoid_unresolved or (not supported_role and unresolved_role):
        return CandidateState.WITHHELD
    return CandidateState.ELIGIBLE


def count_role(candidates: Iterable[CandidateRecord], role: ResearchRole) -> int:
    if not isinstance(role, ResearchRole):
        raise ValueError("unknown role")
    return sum(1 for candidate in eligible_candidates(candidates) if any(item.role is role and item.verdict.value == "supported" for item in candidate.roles))


def interest_covered(candidates: Iterable[CandidateRecord], interest: InterestTag) -> bool:
    if not isinstance(interest, InterestTag):
        raise ValueError("unknown interest")
    return any(interest in candidate.interests for candidate in eligible_candidates(candidates))


def meal_covered(candidates: Iterable[CandidateRecord], meal: MealIntent) -> bool:
    if not isinstance(meal, MealIntent):
        raise ValueError("unknown meal")
    return any(meal in candidate.meal_intents for candidate in eligible_candidates(candidates))


def interest_search_and_eligible(
    *, search_done: bool, candidates: Iterable[CandidateRecord], interest: InterestTag
) -> bool:
    if type(search_done) is not bool:
        raise ValueError("search_done must be bool")
    if not isinstance(interest, InterestTag):
        raise ValueError("unknown interest")
    return search_done and interest_covered(candidates, interest)


def meal_search_and_eligible(
    *, search_done: bool, candidates: Iterable[CandidateRecord], meal: MealIntent
) -> bool:
    if type(search_done) is not bool:
        raise ValueError("search_done must be bool")
    if not isinstance(meal, MealIntent):
        raise ValueError("unknown meal")
    return search_done and meal_covered(candidates, meal)


def must_avoid_resolved(verdict: MatchVerdict) -> bool:
    if not isinstance(verdict, MatchVerdict):
        raise ValueError("verdict must be MatchVerdict")
    return verdict in {MatchVerdict.CONFIRMED, MatchVerdict.NO_MATCH}


def must_requirement_satisfied(
    matches: Iterable[tuple[bool, MatchVerdict]],
) -> bool:
    values = tuple(matches)
    if not values:
        return False
    if any(not isinstance(item, tuple) or len(item) != 2 or type(item[0]) is not bool or not isinstance(item[1], MatchVerdict) for item in values):
        raise ValueError("must matches must contain bool eligibility and verdict")
    return any(item[0] and item[1] is MatchVerdict.CONFIRMED for item in values)


def avoid_requirement_satisfied(
    matches: Iterable[tuple[bool, MatchVerdict]],
) -> bool:
    values = tuple(matches)
    if not values:
        return False
    if any(not isinstance(item, tuple) or len(item) != 2 or type(item[0]) is not bool or not isinstance(item[1], MatchVerdict) for item in values):
        raise ValueError("avoid matches must contain bool exclusion and verdict")
    return all(item[1] in {MatchVerdict.NO_MATCH, MatchVerdict.CONFIRMED} and (item[1] is not MatchVerdict.CONFIRMED or item[0]) for item in values)


def initial_spatial_searches_complete(*, city_attraction_done: bool, hotel_restaurant_nearby_done: bool) -> bool:
    if type(city_attraction_done) is not bool or type(hotel_restaurant_nearby_done) is not bool:
        raise ValueError("initial search flags must be bool")
    return city_attraction_done and hotel_restaurant_nearby_done


def details_mature(*, details_success_count: int, unique_eligible_count: int) -> bool:
    if type(details_success_count) is not int or type(unique_eligible_count) is not int or not 0 <= details_success_count <= 128 or not 0 <= unique_eligible_count <= 128:
        raise ValueError("invalid details counts")
    return details_success_count >= details_target(unique_eligible_count)


def supplemental_gaps_satisfied(pending_gaps: Iterable[ResearchGapSummary]) -> bool:
    return len(tuple(pending_gaps)) == 0


def progress_flags(*, added_eligible_uid: bool = False, completed_must: bool = False, resolved_avoid: bool = False, resolved_conflict: bool = False, enriched_details: bool = False, covered_interest: bool = False, satisfied_gap: bool = False) -> tuple[bool, ...]:
    values = (added_eligible_uid, completed_must, resolved_avoid, resolved_conflict, enriched_details, covered_interest, satisfied_gap)
    if any(type(value) is not bool for value in values):
        raise ValueError("progress flags must be bool")
    return values


def has_progress(flags: Iterable[bool]) -> bool:
    try:
        values = tuple(flags)
    except TypeError as exc:
        raise ValueError("progress flags must be iterable") from exc
    if any(type(value) is not bool for value in values):
        raise ValueError("progress flags must be bool")
    return any(values)


def next_no_progress_streak(previous: int, progressed: bool) -> int:
    if type(previous) is not int or previous < 0 or previous > 2:
        raise ValueError("invalid no-progress streak")
    if type(progressed) is not bool:
        raise ValueError("progressed must be bool")
    return 0 if progressed else min(2, previous + 1)


def stop_reasons(*, candidate_capacity_full: bool, place_budget_exhausted: bool, iteration_limit_reached: bool, no_progress_streak: int, no_legal_action: bool, useful_supply_not_found: bool) -> tuple[CandidateResearchStopReason, ...]:
    if type(no_progress_streak) is not int or not 0 <= no_progress_streak <= 2:
        raise ValueError("invalid no-progress streak")
    flags = (candidate_capacity_full, place_budget_exhausted, iteration_limit_reached, no_legal_action, useful_supply_not_found)
    if any(type(value) is not bool for value in flags):
        raise ValueError("stop flags must be bool")
    ordered: list[CandidateResearchStopReason] = []
    if candidate_capacity_full:
        ordered.append(CandidateResearchStopReason.CANDIDATE_CAPACITY_REACHED)
    if place_budget_exhausted:
        ordered.append(CandidateResearchStopReason.PLACE_BUDGET_EXHAUSTED)
    if iteration_limit_reached:
        ordered.append(CandidateResearchStopReason.ITERATION_LIMIT_REACHED)
    if no_progress_streak >= 2:
        ordered.append(CandidateResearchStopReason.NO_PROGRESS)
    if no_legal_action:
        ordered.append(CandidateResearchStopReason.NO_LEGAL_ACTION)
    if useful_supply_not_found:
        ordered.append(CandidateResearchStopReason.USEFUL_SUPPLY_NOT_FOUND)
    return tuple(ordered)


def compute_stop_reasons(
    *,
    admitted_slots: int,
    has_unmet_new_candidate_goal: bool,
    place_remaining: int,
    has_place_progress_goal: bool,
    iteration_remaining: int,
    completed: bool,
    no_progress_streak: int,
    has_unmet_goal: bool,
    legal_action_count: int,
    supply_gaps: Iterable[tuple[bool, bool]],
) -> tuple[CandidateResearchStopReason, ...]:
    """Apply the necessary-and-sufficient stop predicates from the design."""
    if type(admitted_slots) is not int or not 0 <= admitted_slots <= 128:
        raise ValueError("invalid admitted slots")
    if type(place_remaining) is not int or not 0 <= place_remaining <= 48:
        raise ValueError("invalid place budget")
    if type(iteration_remaining) is not int or not 0 <= iteration_remaining <= 6:
        raise ValueError("invalid iteration budget")
    if type(legal_action_count) is not int or not 0 <= legal_action_count <= 8:
        raise ValueError("invalid legal action count")
    if type(completed) is not bool or type(has_unmet_new_candidate_goal) is not bool or type(has_place_progress_goal) is not bool or type(has_unmet_goal) is not bool:
        raise ValueError("stop predicates must be bool")
    supply_entries = tuple(supply_gaps)
    if any(type(entry) is not tuple or len(entry) != 2 or type(entry[0]) is not bool or type(entry[1]) is not bool for entry in supply_entries):
        raise ValueError("supply gaps must be (search_done, useful_supply) bool pairs")
    if any(useful and not done for done, useful in supply_entries):
        raise ValueError("useful supply cannot exist before a search")
    useful_supply_not_found = bool(supply_entries) and all(done and not useful for done, useful in supply_entries)
    no_legal_action = has_unmet_goal and iteration_remaining > 0 and place_remaining > 0 and legal_action_count == 0
    return stop_reasons(
        candidate_capacity_full=admitted_slots == 128 and has_unmet_new_candidate_goal,
        place_budget_exhausted=place_remaining == 0 and has_place_progress_goal,
        iteration_limit_reached=iteration_remaining == 0 and not completed,
        no_progress_streak=no_progress_streak,
        no_legal_action=no_legal_action,
        useful_supply_not_found=useful_supply_not_found,
    )


def is_completed(*, unresolved_must: int, unresolved_avoid: int, unresolved_role: int, candidate_counts_met: bool, interests_covered: bool, meals_covered: bool, details_mature: bool, supplemental_gaps_met: bool, candidate_identity_stable: bool, initial_searches_complete: bool, role_blocking_resolved: bool) -> bool:
    counts = (unresolved_must, unresolved_avoid, unresolved_role)
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError("invalid unresolved count")
    flags = (candidate_counts_met, interests_covered, meals_covered, details_mature, supplemental_gaps_met, candidate_identity_stable, initial_searches_complete, role_blocking_resolved)
    if any(type(value) is not bool for value in flags):
        raise ValueError("completion flags must be bool")
    return all((unresolved_must == 0, unresolved_avoid == 0, *flags))


def overflow_uids(candidates: Iterable[CandidateRecord], capacity: int = 128) -> tuple[str, ...]:
    if type(capacity) is not int or not 0 <= capacity <= 128:
        raise ValueError("invalid candidate capacity")
    ordered = tuple(candidates)
    return tuple(item.core.uid for item in ordered[capacity:])


def deterministic_objectives(objectives: Iterable[ResearchObjective]) -> tuple[ResearchObjective, ...]:
    # Python's stable sort preserves controller insertion order for same-kind objectives.
    return tuple(sorted(tuple(objectives), key=lambda item: objective_priority(item.kind)))


def highest_priority_objective_kind(objectives: Iterable[ResearchObjective]) -> ResearchObjectiveKind | None:
    ordered = deterministic_objectives(objectives)
    return None if not ordered else ordered[0].kind


def action_targets_highest_priority(
    *, actions: Iterable[ProposedAction], objectives: Iterable[ResearchObjective]
) -> bool:
    ordered = deterministic_objectives(objectives)
    if not ordered:
        return True
    allowed = {item.objective_id for item in ordered if item.kind is ordered[0].kind}
    return any(action.objective_id in allowed for action in actions)


validate_action_targets_highest = action_targets_highest_priority


def group_requirement_items(items: Iterable[tuple[str, str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Group only exact-equal requirement strings, preserving first occurrence order."""
    groups: list[tuple[str, list[str]]] = []
    positions: dict[str, int] = {}
    for item_id, text in items:
        if text not in positions:
            positions[text] = len(groups)
            groups.append((text, [item_id]))
        else:
            groups[positions[text]][1].append(item_id)
    return tuple((text, tuple(ids)) for text, ids in groups)


# Descriptive aliases keep the deterministic helpers convenient for callers without
# expanding the package-root API.
compute_details_target = details_target
is_completion_reached = is_completed
calculate_progress = has_progress
completed_gate = is_completed
completion_gate = is_completed


__all__ = (
    "ResearchObjective", "OBJECTIVE_PRIORITY", "objective_priority", "candidate_state_priority",
    "candidate_capacity_remaining", "details_target", "role_target", "eligible_candidates", "count_role",
    "attraction_target", "restaurant_target",
    "interest_covered", "meal_covered", "must_avoid_resolved", "progress_flags", "has_progress",
    "interest_search_and_eligible", "meal_search_and_eligible", "must_requirement_satisfied",
    "avoid_requirement_satisfied", "initial_spatial_searches_complete", "details_mature",
    "supplemental_gaps_satisfied",
    "derive_candidate_state",
    "next_no_progress_streak", "stop_reasons", "compute_stop_reasons", "is_completed", "overflow_uids", "deterministic_objectives",
    "highest_priority_objective_kind", "action_targets_highest_priority", "group_requirement_items",
    "validate_action_targets_highest",
    "compute_details_target", "is_completion_reached", "calculate_progress", "completed_gate", "completion_gate",
)
