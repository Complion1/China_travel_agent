"""候选用途的唯一规划准入和纯代码预检；不访问模型、地图或预算。"""
from collections import Counter
from datetime import date, datetime, timedelta
from .policy import candidate_is_eligible
from app.candidate_research import MealIntent, MatchVerdict, ResearchRole


def candidate_purposes(candidate):
    from app.candidate_research import candidate_admitted_purposes
    if not candidate_is_eligible(candidate):
        return ()
    return candidate_admitted_purposes(candidate)


def full_dates(confirmed):
    window = confirmed.requirement.time_window
    boundary = {datetime.fromisoformat(item.local_datetime).date() for item in (window.arrival, window.departure)
                if item.local_datetime is not None}
    start, end = date.fromisoformat(window.sightseeing_start_date), date.fromisoformat(window.sightseeing_end_date)
    return tuple(start + timedelta(days=i) for i in range((end-start).days+1)
                 if start + timedelta(days=i) not in boundary)


def minimum_attractions(confirmed):
    pace = confirmed.requirement.preferences.pace
    return {"轻松": 1, "标准": 3, "紧凑": 4}.get(getattr(pace, "value", pace), 1)


def supply_shortages(candidates, confirmed, groups):
    """Known necessary conditions only; route and time feasibility remain final gates."""
    purposes = {item.core.uid: candidate_purposes(item) for item in candidates}
    shortages = []
    full_count = len(full_dates(confirmed))
    if full_count:
        count = sum("attraction" in values for values in purposes.values())
        required = minimum_attractions(confirmed) * full_count
        if count < required:
            shortages.append(("count", "attraction", count, required, None))
        for meal in ("lunch", "dinner"):
            count = sum(meal in values for values in purposes.values())
            if count < full_count:
                shortages.append(("meal", meal, count, full_count, None))
        usable_restaurants = sum(any(meal in values for meal in ("lunch", "dinner")) for values in purposes.values())
        if usable_restaurants < 2 * full_count and not any(item[0] == "meal" for item in shortages):
            shortages.append(("count", "restaurant", usable_restaurants, 2 * full_count, None))
    for group in groups:
        if group.requirement_kind == "must_visit" and not any(purposes[item.core.uid]
                and any(match.requirement_group_id == group.group_id and match.verdict is MatchVerdict.CONFIRMED
                        for match in item.must_matches) for item in candidates):
            shortages.append(("must", "must", 0, 1, group))
    return tuple(shortages)


def _issue(*, target, rule, field, handle=None, actual=None, expected=None, evidence=()):
    return dict(target=target, rule=rule, field=field, candidate_handle=handle, actual=actual,
                expected=expected, evidence_handles=list(evidence), allowed_actions=["replace_candidate", "research_evidence"])


def qualification_issues(candidates, intents, confirmed, *, require_daily=False, business_facts=None, required_must=()):
    by_handle = {f"candidate-handle-{item.admission_ordinal:04d}": item for item in candidates}
    issues, seen = [], {}
    main_seen, must_seen = set(), set()
    all_main = {intent.candidate_handle for intent in intents if not intent.is_backup}
    backup_seen = set()
    counters = Counter()
    counts = Counter()
    window = confirmed.requirement.time_window
    for index, intent in enumerate(intents):
        purpose = getattr(intent.visit_kind, "value", intent.visit_kind)
        date = intent.date
        backup = intent.is_backup
        group = (date.isoformat(), purpose, backup)
        ordinal = counters[group]
        counters[group] += 1
        target = f"{group[0]}:{'backup' if backup else 'main'}:{purpose}:{ordinal}"
        field = f"visit_intents[{index}]"
        candidate = by_handle.get(intent.candidate_handle)
        own = () if candidate is None else tuple(f"evidence-handle-{i:04d}" for i in candidate.observation_indexes)
        if not window.sightseeing_start_date <= date.isoformat() <= window.sightseeing_end_date:
            issues.append(_issue(target=target, rule="date_out_of_range", field=field+".date", expected="sightseeing_dates"))
        if candidate is None or purpose not in candidate_purposes(candidate):
            issues.append(_issue(target=target, rule="purpose_not_admitted", field=field+".candidate_handle",
                handle=intent.candidate_handle, actual=[] if candidate is None else list(candidate_purposes(candidate)), expected=purpose, evidence=own))
        if candidate is not None:
            confirmed_groups = {match.requirement_group_id for match in candidate.must_matches if match.verdict is MatchVerdict.CONFIRMED}
            if not set(intent.must_group_ids).issubset(confirmed_groups):
                issues.append(_issue(target=target, rule="must_match_unconfirmed", field=field+".must_group_ids",
                    handle=intent.candidate_handle, actual=sorted(set(intent.must_group_ids) - confirmed_groups),
                    expected="confirmed_must", evidence=own))
            if not backup:
                must_seen.update(set(intent.must_group_ids) & confirmed_groups)
            state = getattr(intent.business_hours_state, "value", intent.business_hours_state)
            if state == "conflicts":
                issues.append(_issue(target=target, rule="business_hours_conflict", field=field+".business_hours_state",
                    handle=intent.candidate_handle, actual=state, expected="compatible_or_unknown", evidence=own))
            if state == "compatible":
                fact = (business_facts or {}).get(candidate.core.uid)
                verified = fact is not None and f"evidence-handle-{fact[0]:04d}" in intent.business_evidence_handles
                if not verified:
                    issues.append(_issue(target=target, rule="business_hours_unverified", field=field+".business_evidence_handles",
                        handle=intent.candidate_handle, expected="owned_verified_hours", evidence=own))
        key = (date, intent.candidate_handle)
        if key in seen:
            issues.append(_issue(target=target, rule="candidate_duplicate", field=field+".candidate_handle",
                handle=intent.candidate_handle, expected="distinct_main_and_backup", evidence=own))
        seen[key] = backup
        if not backup and intent.candidate_handle in main_seen:
            issues.append(_issue(target=target, rule="candidate_duplicate", field=field+".candidate_handle",
                handle=intent.candidate_handle, expected="unique_main_itinerary", evidence=own))
        if not backup:
            main_seen.add(intent.candidate_handle)
        elif intent.candidate_handle in all_main or intent.candidate_handle in backup_seen:
            issues.append(_issue(target=target, rule="candidate_duplicate", field=field+".candidate_handle",
                handle=intent.candidate_handle, expected="unique_backups_disjoint_from_main", evidence=own))
        if backup:
            backup_seen.add(intent.candidate_handle)
        if not backup:
            admitted = () if candidate is None else candidate_purposes(candidate)
            if "attraction" in admitted and purpose.startswith("attraction"):
                counts[(date, "attraction")] += 1
            for meal in ("lunch", "dinner"):
                if meal in admitted and purpose in (meal, "attraction_"+meal):
                    counts[(date, meal)] += 1
    if require_daily:
        for date in full_dates(confirmed):
            for purpose, expected in (("attraction", minimum_attractions(confirmed)), ("lunch", 1), ("dinner", 1)):
                if counts[(date, purpose)] < expected:
                    issues.append(_issue(target=f"{date.isoformat()}:main:{purpose}:required", rule="required_visit_missing",
                        field="draft.days.visits", actual=counts[(date, purpose)], expected=expected))
                if purpose in ("lunch", "dinner") and counts[(date, purpose)] > 1:
                    issues.append(_issue(target=f"{date.isoformat()}:main:{purpose}:required", rule="meal_count_exceeded",
                        field="draft.days.visits", actual=counts[(date, purpose)], expected=1))
        for group in required_must:
            if group not in must_seen:
                issues.append(_issue(target=f"itinerary:main:must:{group}", rule="must_visit_unscheduled",
                    field="draft.days.visits.must_group_ids", expected="scheduled_confirmed_must"))
    return tuple({(issue["target"],issue["rule"],issue["field"]):issue for issue in issues}.values())


def problem_counts(issues):
    """Moving an error or deleting a required visit does not resolve its obligation."""
    counts, deficits = Counter(), Counter()
    for issue in issues:
        pieces = issue["target"].split(":")
        identity, purpose = (pieces[1:3] if len(pieces) >= 3 else ("main", "submission"))
        rule = issue["rule"]
        if rule in {"must_match_unconfirmed", "must_visit_unscheduled"}:
            groups = (pieces[-1],) if rule == "must_visit_unscheduled" else issue.get("actual") or ("unconfirmed",)
            for group in groups:
                # Removing an unsupported required must claim leaves the same
                # required group unscheduled, rather than resolving it.
                key = ("required_must", identity, group)
                counts[key] = max(counts[key], 1)
        elif rule in {"purpose_not_admitted", "required_visit_missing", "candidate_duplicate"}:
            parts = purpose.split("_") if purpose.startswith("attraction_") else (purpose,)
            for part in parts:
                if rule == "purpose_not_admitted" and part in (issue.get("actual") or ()):
                    continue
                key = ("required_purpose", identity, part)
                if rule == "required_visit_missing":
                    deficits[key] += max(1, issue["expected"] - issue["actual"])
                else:
                    counts[key] += 1
        else:
            family = "business_hours" if rule.startswith("business_hours") else rule
            counts[(family, identity, purpose)] += 1
    for key, deficit in deficits.items():
        counts[key] = max(counts[key], deficit)
    obligations = Counter()
    for (rule, identity, purpose), count in counts.items():
        # Transferring a bad selection between meals or roles is not a repair.
        key = (rule, identity, purpose) if rule == "required_must" else (rule, identity, "selection")
        obligations[key] += count
    return obligations


def stable_proposal(value):
    """Ignore model-owned IDs and presentation text while retaining choices and times."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: stable_proposal(item) for key, item in value.items()
                if key not in {"theme", "notes", "description", "explanation", "rationale", "visit_id", "core_visit_id", "leg_id"}}
    if isinstance(value, (tuple, list)):
        return [stable_proposal(item) for item in value]
    return value
