"""必要判断的覆盖、未知状态和候选自身证据合同。"""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.candidate_research import (
    JudgmentPatch, RoleJudgmentUpdate, RoleVerdict, RoleReasonCode, ResearchRole,
    InterestJudgmentUpdate, InterestReasonCode, MealJudgmentUpdate, MealReasonCode,
    MealIntent, MatchVerdict, EvidenceHandle, RequirementGroupJudgmentUpdate,
    RequirementReasonCode, RequirementVerdictException, ProposedActionBatch,
)
from app.candidate_research.qualification import (
    RequiredJudgment, JudgmentJob, CandidateEvidenceWindow, JudgmentRequiredMissing,
    validate_required_judgments, record_assessments, already_assessed, evidence_digest,
    validate_job_coverage,
)
from app.requirements import InterestTag
from tests.support.candidate_research_fakes import structured, search_success


def job(*required, kind="search_and_judge"):
    return JudgmentJob(step_kind=kind, iteration_index=0, candidate_ordinals=(0,),
        required=tuple(RequiredJudgment(candidate_ordinal=0, kind=key_kind, target=target,
                                      when_supported=condition) for key_kind, target, condition in required),
        evidence_windows=(CandidateEvidenceWindow(candidate_ordinal=0, evidence_indexes=(0,),
                                                  evidence_digest="sha256:" + "a" * 64),))


def candidates(*, restaurant=False):
    return (SimpleNamespace(admission_ordinal=0, roles=(
        SimpleNamespace(role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED),
        SimpleNamespace(role=ResearchRole.RESTAURANT,
                        verdict=RoleVerdict.SUPPORTED if restaurant else RoleVerdict.CONTRADICTED))),)


def required_patch(payload, *, unresolved=False):
    """显式构造合法测试输出，不在边界替身中修正模型输出。"""
    roles, interests, meals, groups = [], [], [], {}
    owned = {item["handle"]: tuple(EvidenceHandle(handle_id=value) for value in item["evidence_handles"][:1])
             for item in payload["candidates"]}
    for key in payload["required_judgments"]:
        handle, target = key["candidate_handle"], key["target"]
        values = dict(candidate_handle=handle, evidence_handles=owned[handle])
        if key["kind"] == "role":
            roles.append(RoleJudgmentUpdate(**values, role=ResearchRole(target),
                verdict=RoleVerdict.UNRESOLVED if unresolved else RoleVerdict.SUPPORTED,
                reason_code=RoleReasonCode.EVIDENCE_INSUFFICIENT if unresolved else RoleReasonCode.NAME_SEMANTICS))
        elif key["kind"] == "interest":
            interests.append(InterestJudgmentUpdate(**values, interest=InterestTag(target),
                verdict=MatchVerdict.UNRESOLVED, reason_code=InterestReasonCode.EVIDENCE_INSUFFICIENT))
        elif key["kind"] == "meal":
            meals.append(MealJudgmentUpdate(**values, meal_intent=MealIntent(target),
                verdict=MatchVerdict.UNRESOLVED, reason_code=MealReasonCode.EVIDENCE_INSUFFICIENT))
        else:
            groups.setdefault((key["kind"], target), []).append(handle)
    for (kind, group_id), handles in groups.items():
        verdict = MatchVerdict.UNRESOLVED if unresolved else (
            MatchVerdict.CONFIRMED if kind == "must_visit" else MatchVerdict.NO_MATCH)
        groups[(kind, group_id)] = RequirementGroupJudgmentUpdate(requirement_group_id=group_id,
            requirement_kind=kind, evaluated_candidate_handles=tuple(handles), default_verdict=verdict,
            default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT if unresolved else RequirementReasonCode.EXACT_NAME_MATCH,
            default_evidence_handles=owned[handles[0]], exceptions=tuple(
                RequirementVerdictException(candidate_handle=handle, verdict=verdict,
                    reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT if unresolved else RequirementReasonCode.EXACT_NAME_MATCH,
                    evidence_handles=owned[handle]) for handle in handles[1:]))
    return JudgmentPatch(role_updates=tuple(roles), interest_updates=tuple(interests),
                         meal_updates=tuple(meals), requirement_group_updates=tuple(groups.values()))


def legal_judgment(operation, patch=None):
    """用于明确要求合法输出的脚本；缺项反例直接传原补丁。"""
    from app.candidate_research.judgments import merge_judgment_patch
    result = required_patch(json.loads(operation.prompts[-1].user_text), unresolved=True)
    return structured(result if patch is None else merge_judgment_patch(result, patch))


@pytest.mark.parametrize("kind,target,condition", [("role", "attraction", "always"),
    ("meal", "lunch", "restaurant"), ("must_visit", "must-a", "always"),
    ("avoid", "avoid-a", "any"), ("interest", InterestTag.CULTURE.value, "attraction")])
def test_all_necessary_categories_reject_omission(kind, target, condition):
    work = job((kind, target, condition))
    patch = JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())
    with pytest.raises(JudgmentRequiredMissing) as error:
        validate_required_judgments(work, candidates(restaurant=True), patch)
    assert error.value.missing == work.required


def test_unknown_is_explicitly_assessed_and_same_evidence_is_not_pending():
    work = job(("role", "attraction", "always"))
    patch = JudgmentPatch(role_updates=(RoleJudgmentUpdate(candidate_handle="c:0", role=ResearchRole.ATTRACTION,
        verdict=RoleVerdict.UNRESOLVED, reason_code=RoleReasonCode.EVIDENCE_INSUFFICIENT,
        evidence_handles=(EvidenceHandle(handle_id="e:0"),)),), interest_updates=(), meal_updates=(), requirement_group_updates=())
    validate_required_judgments(work, candidates(), patch)
    receipts = record_assessments((), work, patch)
    assert receipts[0].verdict == "unresolved"
    assert already_assessed(receipts, work.required[0], work.evidence_windows[0].evidence_digest)
    assert not already_assessed(receipts, work.required[0], "sha256:" + "b" * 64)
    assert record_assessments(receipts, work, patch) == receipts


def test_condition_meals_are_not_required_for_contradicted_restaurant():
    work = job(("meal", "lunch", "restaurant"), ("meal", "dinner", "restaurant"))
    patch = JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())
    validate_required_judgments(work, candidates(), patch)


def test_confirmed_avoid_excludes_candidate_without_other_conditional_judgments():
    work = job(("meal", "lunch", "restaurant"), ("meal", "dinner", "restaurant"),
               ("avoid", "avoid-a", "any"), ("avoid", "avoid-b", "any"))
    patch = JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(),
        requirement_group_updates=(RequirementGroupJudgmentUpdate(
            requirement_group_id="avoid-a", requirement_kind="avoid",
            evaluated_candidate_handles=("c:0",), default_verdict=MatchVerdict.CONFIRMED,
            default_reason_code=RequirementReasonCode.EXACT_NAME_MATCH,
            default_evidence_handles=(EvidenceHandle(handle_id="e:0"),), exceptions=()),))
    validate_required_judgments(work, candidates(restaurant=True), patch)
    receipts = record_assessments((), work, patch)
    assert [(item.kind, item.target, item.verdict) for item in receipts] == [
        ("avoid", "avoid-a", "confirmed")]


def test_scoped_group_default_does_not_assess_an_omitted_candidate():
    work = job(("avoid", "avoid-a", "any"))
    patch = JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(),
        requirement_group_updates=(RequirementGroupJudgmentUpdate(requirement_kind="avoid", requirement_group_id="avoid-a",
            evaluated_candidate_handles=(), default_verdict=MatchVerdict.UNRESOLVED,
            default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT,
            default_evidence_handles=(EvidenceHandle(handle_id="e:0"),), exceptions=()),))
    with pytest.raises(JudgmentRequiredMissing):
        validate_required_judgments(work, candidates(), patch)


def test_evidence_digest_ignores_ids_timestamps_order_and_duplicate_facts():
    fact = {"handle": "e:0", "accepted_search": {"role": "attraction"},
            "result": {"record_type": "search_success", "places": [{"uid": "a", "name": "A"}],
                       "provenance": {"retrieved_at": "old"}}}
    changed = {**fact, "handle": "e:4", "result": {**fact["result"], "provenance": {"retrieved_at": "new"}}}
    assert evidence_digest((fact,)) == evidence_digest((changed, fact))
    assert evidence_digest((fact,)) != evidence_digest(({**fact, "result": {"record_type": "details_success", "details": {"uid": "a", "business_hours": ["10-18"]}}},))


async def test_missing_judgment_recovers_on_same_evidence_before_any_acceptance():
    from tests.unit.candidate_research.test_research_correction_recovery import setup, action
    request, model, maps, repo, service = setup()
    service._budget.iteration_limit = 6
    service._budget.consumed[service._iteration_key()] = 5
    maps.responses.append(search_success("a"))
    empty = JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())
    model.responses.extend([action(model), structured(empty),
        lambda _: structured(required_patch(json.loads(model.prompts[-1].user_text)))])
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete", getattr(result, "failure", None)
    assert (model.call_count, maps.call_count) == (3, 1)
    writes = [item for item in repo.put_requests if item.artifact_type.endswith("model_judgment")]
    assert len(writes) == 1 and writes[0].schema_version == "4.0"
    record = writes[0].content["result"]
    assert record["record_type"] == "accepted"
    attempts = [item.content for item in repo.put_requests if item.artifact_type.endswith("invocation_attempt") and item.content["kind"] == "judgment"]
    assert attempts[0]["recovery_code"] == "judgment_required_missing"
    assert attempts[0]["result"]["record"]["metadata"] is not None
    assert attempts[1]["correction_number"] == 1
    first, retry = map(lambda prompt: json.loads(prompt.user_text), model.prompts[1:])
    retry.pop("output_recovery")
    assert first == retry
    stored = repo.contents[result.candidate_set_ref.artifact_id]
    assert stored["qualification_state"]["assessments"]
    assert stored["qualification_state"]["steps"][0]["step_kind"] == "search_and_judge"
    assert await service.research_initial(request) == result and model.call_count == 3
    await service.aclose()


async def test_missing_judgment_exhausts_shared_recovery_without_accepting_subset():
    from tests.unit.candidate_research.test_research_correction_recovery import setup, action
    request, model, maps, repo, service = setup()
    maps.responses.append(search_success("a"))
    empty = JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())
    model.responses.extend([action(model)] + [structured(empty)] * 3)
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete"
    assert "correction_limit_reached" in {item.value for item in result.stop_reasons}
    assert (model.call_count, maps.call_count) == (4, 1)
    stored = repo.contents[repo.refs[request.operation_plan.terminal_successor_operation_id].artifact_id]
    assert not stored["qualification_state"]["assessments"]
    assert len(stored["record"]["state"]["candidates"]) == 1
    assert stored["record"]["state"]["candidates"][0]["state"] == "withheld"
    assert await service.research_initial(request) == result
    await service.aclose()


async def accepted_case():
    from tests.unit.candidate_research.test_research_correction_recovery import setup, action
    request, model, maps, repo, service = setup()
    service._budget.iteration_limit = 6
    service._budget.consumed[service._iteration_key()] = 5
    maps.responses.append(search_success("a"))
    model.responses.extend([action(model),
        lambda _: structured(required_patch(json.loads(model.prompts[-1].user_text)))])
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete", getattr(result, "failure", None)
    return request, model, maps, repo, service, result


@pytest.mark.parametrize("mutation", ["iteration", "evidence", "candidate", "missing_required"])
async def test_judgment_work_order_rejects_invalid_directory_or_iteration(mutation):
    from app.candidate_research.artifacts import CandidateResearchModelJudgmentArtifact
    request, _, _, repo, service, _ = await accepted_case()
    ref = repo.refs[request.operation_plan.iteration_slots[0].judgment_operation_id]
    tree = json.loads(json.dumps(repo.contents[ref.artifact_id]))
    if mutation == "iteration":
        tree["job"]["iteration_index"] = 1
    elif mutation == "evidence":
        tree["job"]["evidence_windows"][0]["evidence_indexes"] = [47]
    elif mutation == "candidate":
        tree["result"]["patch"]["role_updates"][0]["candidate_ordinal"] = 1
    else:
        tree["result"]["patch"]["role_updates"] = []
    with pytest.raises(ValidationError):
        CandidateResearchModelJudgmentArtifact.model_validate_json(json.dumps(tree), strict=True)
    await service.aclose()


@pytest.mark.parametrize("mutation", ["missing", "digest", "target", "missing_steps", "fake_judge_only"])
async def test_terminal_replay_verifies_accepted_coverage_and_actual_evidence(mutation):
    import hashlib
    request, model, maps, repo, service, result = await accepted_case()
    ref = result.candidate_set_ref
    tree = json.loads(json.dumps(repo.contents[ref.artifact_id]))
    if mutation == "missing":
        tree["qualification_state"]["assessments"] = []
    elif mutation == "digest":
        tree["qualification_state"]["assessments"][0]["evidence_digest"] = "sha256:" + "f" * 64
    elif mutation == "target":
        tree["qualification_state"]["assessments"][0]["target"] = "unknown-role"
    elif mutation == "missing_steps":
        tree["qualification_state"]["steps"] = []
    else:
        step = tree["qualification_state"]["steps"][0]
        step.update(step_kind="judge_existing", action_ref=None, observation_refs=[])
    encoded = json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    repo.contents[ref.artifact_id] = tree
    repo.refs[ref.operation_id] = ref.model_copy(update={
        "content_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(), "size_bytes": len(encoded)})
    replay = await service.research_initial(request)
    assert replay.failure.code.value == "operation_result_indeterminate"
    assert (model.call_count, maps.call_count) == (2, 1)
    await service.aclose()


def test_historical_conditions_use_prior_supported_roles_instead_of_final_state():
    work = job(("meal", "lunch", "restaurant"))
    with pytest.raises(JudgmentRequiredMissing):
        validate_job_coverage(work, {}, {0: {"restaurant"}})
    validate_job_coverage(work, {}, {0: {"attraction"}})


async def test_extra_legal_judgments_cannot_overflow_coverage_after_acceptance():
    from tests.unit.candidate_research.test_research_correction_recovery import setup, action
    from app.requirements import TravelPreferences
    request, model, maps, repo, service = setup()
    preferences = TravelPreferences(interests=request.confirmed.requirement.preferences.interests,
                                   must_visit_places=[f"景点{index}" for index in range(100)])
    requirement = request.confirmed.requirement.model_copy(update={"preferences": preferences})
    request = request.model_copy(update={"confirmed": request.confirmed.model_copy(update={"requirement": requirement})})
    search = search_success("a0")
    maps.responses.append(search.model_copy(update={"places": tuple(
        search.places[0].model_copy(update={"uid": f"a{index}", "name": f"候选{index}"})
        for index in range(16))}))

    def oversized(_):
        payload = json.loads(model.prompts[-1].user_text)
        patch = required_patch(payload)
        groups = tuple(RequirementGroupJudgmentUpdate(requirement_group_id=item["requirement_group_id"],
            requirement_kind=item["requirement_kind"],
            evaluated_candidate_handles=tuple(candidate["handle"] for candidate in payload["candidates"]),
            default_verdict=MatchVerdict.NO_MATCH, default_reason_code=RequirementReasonCode.CANDIDATE_OUT_OF_SCOPE,
            default_evidence_handles=(EvidenceHandle(handle_id="e:0"),), exceptions=())
            for item in payload["requirements"])
        return structured(patch.model_copy(update={"requirement_group_updates": groups}))

    model.responses.extend([action(model), oversized])
    result = await service.research_initial(request)
    assert result.failure.code.value == "judgment_patch_invalid"
    writes = [item for item in repo.put_requests if item.artifact_type.endswith("model_judgment")]
    assert len(writes) == 1 and writes[0].content["result"]["record_type"] == "controller_rejected"
    assert not service._runs[request.operation_plan.run_id].assessments
    assert (model.call_count, maps.call_count) == (2, 1)
    assert await service.research_initial(request) == result
    await service.aclose()
