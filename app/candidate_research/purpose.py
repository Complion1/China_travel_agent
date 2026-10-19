"""用途关联与营业事实分离；本模块只处理有来源的判断增量。"""
from .models import MealIntent, MatchVerdict, MealReasonCode


def candidate_admitted_purposes(candidate):
    """研究供给统计和规划准入共同使用这一用途规则。"""
    from .models import CandidateRecord, CandidateRecordV2, CandidateState, ResearchRole, RoleVerdict
    from .artifacts import ArtifactCandidateRecordSnapshot, ArtifactCandidateRecordV2Snapshot
    from pydantic import TypeAdapter, ValidationError
    if type(candidate) not in {CandidateRecord, CandidateRecordV2, ArtifactCandidateRecordSnapshot, ArtifactCandidateRecordV2Snapshot}:
        return ()
    try:
        candidate = TypeAdapter(type(candidate)).validate_python(candidate.model_dump(mode="python"), strict=True)
    except (TypeError, ValueError, ValidationError):
        return ()
    if candidate.state is not CandidateState.ELIGIBLE or any(
            item.verdict is not MatchVerdict.NO_MATCH for item in candidate.avoid_matches):
        return ()
    supported = {item.role for item in candidate.roles if item.verdict is RoleVerdict.SUPPORTED}
    result = ["attraction"] if ResearchRole.ATTRACTION in supported else []
    if ResearchRole.RESTAURANT in supported:
        for meal in candidate.meal_intents:
            result.append(meal.value)
            if ResearchRole.ATTRACTION in supported:
                result.append("attraction_" + meal.value)
    return tuple(result)


def candidate_supply_status(artifact):
    """零可用供给时只返回固定原因，不公开地点、证据或模型内容。"""
    from .models import CandidateResearchStopReason, RoleVerdict
    from .qualification import candidate_required_keys, judgment_key

    state = artifact.record.state
    if any(candidate_admitted_purposes(item) for item in state.candidates):
        return None
    if not state.candidates:
        return "research_no_candidates"
    requirements = tuple((group.requirement_kind, group.group_id, group.requirement_item_ids)
                         for group in state.requirement_group_states)
    assessed = {judgment_key(item) for item in artifact.qualification_state.assessments}
    pending = []
    for candidate in state.candidates:
        supported = {item.role.value for item in candidate.roles if item.verdict is RoleVerdict.SUPPORTED}
        must = tuple(item.requirement_group_id for item in candidate.must_matches
                     if item.verdict is MatchVerdict.CONFIRMED)
        keys = candidate_required_keys(candidate, state.search_coverage.records,
                                       requirements, state.effective_interests, must)
        verdicts = {("role", item.role.value): item.verdict.value for item in candidate.roles}
        verdicts.update({("meal", item.meal_intent.value): item.verdict.value
                         for item in candidate.meal_assessments})
        verdicts.update({(kind, item.requirement_group_id): item.verdict.value
                         for kind, matches in (("must_visit", candidate.must_matches),
                                               ("avoid", candidate.avoid_matches)) for item in matches})
        pending.extend(key for key in keys
                       if verdicts.get((key.kind, key.target), "unresolved") in {"unresolved", "ambiguous"}
                       and (key.when_supported == "always" or key.when_supported == "any" and supported
                            or key.when_supported in supported))
    if not pending:
        return "research_candidates_unsuitable"
    exhausted = {CandidateResearchStopReason.ITERATION_LIMIT_REACHED,
                 CandidateResearchStopReason.PLACE_BUDGET_EXHAUSTED,
                 CandidateResearchStopReason.CORRECTION_LIMIT_REACHED,
                 CandidateResearchStopReason.SEMANTIC_BUDGET_INSUFFICIENT}
    if exhausted.intersection(getattr(artifact.record, "stop_reasons", ())):
        return "research_budget_exhausted"
    if any(judgment_key(key) not in assessed for key in pending):
        return "research_judgments_unfinished"
    return "research_evidence_insufficient"


def validate_meal_assessments(candidate):
    assessments = candidate.meal_assessments
    if tuple(item.meal_intent for item in assessments) != tuple(MealIntent):
        raise ValueError("meal assessments must cover lunch and dinner in order")
    for item in assessments:
        if len(set(item.evidence_indexes)) != len(item.evidence_indexes):
            raise ValueError("purpose evidence must be unique")
        if not set(item.evidence_indexes).issubset(candidate.observation_indexes):
            raise ValueError("purpose evidence must belong to candidate")
    positive = tuple(item.meal_intent for item in assessments if item.verdict is MatchVerdict.CONFIRMED)
    if candidate.meal_intents != positive:
        raise ValueError("meal intents must derive from assessments")


def apply_meal_assessment(current, incoming):
    # Missing hours cannot disprove a previously supported meal association.
    if (current.verdict is MatchVerdict.CONFIRMED
            and incoming.verdict is not MatchVerdict.CONFIRMED
            and not (incoming.verdict is MatchVerdict.NO_MATCH
                     and incoming.reason_code == MealReasonCode.BUSINESS_HOURS_CONFLICTS.value)):
        return current
    return incoming


def normalise_meal_update(update, evidence):
    """Require a real source signal, without parsing provider prose as verified hours."""
    from .models import MealJudgmentUpdate
    sources = tuple(evidence.get(item.handle_id, {}) for item in update.evidence_handles)
    targeted = any(source.get("accepted_search", {}).get("meal_intent") == update.meal_intent.value
                   for source in sources if source.get("accepted_search"))
    hours = any(source.get("result", {}).get("record_type") == "details_success"
                and source.get("result", {}).get("details", {}).get("business_hours")
                for source in sources)
    supported = ((update.reason_code is MealReasonCode.TARGETED_MEAL_SEARCH and targeted)
                 or (update.reason_code is MealReasonCode.BUSINESS_HOURS_SUPPORTS and hours))
    contradicted = update.reason_code is MealReasonCode.BUSINESS_HOURS_CONFLICTS and hours
    if ((update.verdict is MatchVerdict.CONFIRMED and not supported)
            or (update.verdict is MatchVerdict.NO_MATCH and not contradicted)):
        return MealJudgmentUpdate(candidate_handle=update.candidate_handle, meal_intent=update.meal_intent,
            verdict=MatchVerdict.UNRESOLVED, reason_code=MealReasonCode.EVIDENCE_INSUFFICIENT,
            evidence_handles=update.evidence_handles)
    return update
