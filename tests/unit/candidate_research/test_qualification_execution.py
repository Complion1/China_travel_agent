"""旧候选补判、事实消费及余额边界的公共入口验收。"""

import asyncio
import json
from uuid import uuid4

import pytest

from app.candidate_research import (
    CandidateResearchService, ProposedActionBatch, ProposedDetailsAction, JudgmentPatch, ResearchRole,
    RoleJudgmentUpdate, RoleVerdict, RoleReasonCode, EvidenceHandle,
    MatchVerdict, RequirementGroupJudgmentUpdate, RequirementReasonCode,
)
from app.candidate_research.artifacts import CandidateResearchArtifact
from app.candidate_research.qualification import build_judgment_context, record_assessments
from app.requirements import InterestTag
from tests.support.candidate_research_fakes import (
    FakeRuntime, FakeModelOperation, FakeMapAdapter, FakeRepository, FakeBudgetManager,
    structured, search_success,
    details_success,
)
from tests.unit.candidate_research.test_service_initial import _request, _first_objective_action
from tests.unit.candidate_research.test_qualification import required_patch, legal_judgment, accepted_case
from tests.unit.candidate_research.test_research_correction_recovery import failure
from app.candidate_research.purpose import candidate_admitted_purposes


def setup_batch(*, spent=0, iterations_used=4, service_class=CandidateResearchService, repository=None):
    request, hotel_ref, hotel, _ = _request(uuid4())
    model, repository = FakeModelOperation(), repository or FakeRepository()
    repository.seed(hotel_ref, hotel)
    response = search_success("a0")
    places = tuple(response.places[0].model_copy(update={"uid": f"a{index}"}) for index in range(17))
    maps = FakeMapAdapter([response.model_copy(update={"places": places})])

    class WithKnownUsage(service_class):
        async def _load_or_create_run(self, *args):
            run = await super()._load_or_create_run(*args)
            if not run.semantic_invocations and spent:
                run.semantic_invocations = run.provider_attempts = spent
                run.usage = dict(prompt_tokens=spent, completion_tokens=spent, total_tokens=spent * 2,
                                reasoning_tokens=0, prompt_cache_hit_tokens=spent, prompt_cache_miss_tokens=0)
            return run

    service = WithKnownUsage(runtime=FakeRuntime(model), map_adapter=maps, artifact_repository=repository,
        budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    service._budget.consumed[service._iteration_key()] = iterations_used
    model.responses.extend([
        lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model),))),
        lambda _: legal_judgment(model),
        lambda _: legal_judgment(model),
    ])
    return request, model, maps, repository, service


@pytest.mark.parametrize("spent", [0, 9])
async def test_unselected_candidate_is_judged_from_own_history_with_one_call_and_no_maps(spent):
    request, model, maps, repository, service = setup_batch(spent=spent)
    try:
        result = await service.research_initial(request)
        if spent:
            assert result.outcome_type == "failed"
            assert result.failure.code.value == "state_invariant_failed"
            return
        assert result.outcome_type == "incomplete", getattr(result, "failure", None)
        assert (model.call_count, maps.call_count) == (3, 1)
        first, second = (json.loads(model.prompts[index].user_text) for index in (1, 2))
        assert len(first["candidates"]) == 16
        assert second["step_kind"] == "judge_existing"
        assert [item["handle"] for item in second["candidates"]] == ["c:16"]
        assert [item["uid"] for item in second["evidence"][0]["result"]["places"]] == ["a16"]
        artifact = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
        assert len(artifact.qualification_state.steps) == 2
        step = artifact.qualification_state.steps[-1]
        assert (step.action_ref, step.observation_refs, step.iteration_index) == (None, (), 5)
        assert step.judgment_ref is not None
        assert result.budgets.semantic_invocations_consumed == spent + 3
        assert result.budgets.iterations_consumed == 6
        assert request.operation_plan.iteration_slots[1].action_operation_id not in repository.refs
        assert await service.research_initial(request) == result
        assert (model.call_count, maps.call_count) == (3, 1)
    finally:
        await service.aclose()


async def test_available_old_work_is_not_stopped_by_global_no_progress():
    class Stalled(CandidateResearchService):
        def _refresh_state(self, state, *, no_progress):
            return super()._refresh_state(state, no_progress=2)
    request, model, maps, _, service = setup_batch(service_class=Stalled)
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "incomplete"
        assert json.loads(model.prompts[2].user_text)["step_kind"] == "judge_existing"
        assert (model.call_count, maps.call_count) == (3, 1)
    finally:
        await service.aclose()


async def test_repeated_search_facts_save_actual_step_without_an_empty_judgment_call():
    request, model, maps, repository, service = setup_batch()
    request.confirmed.requirement.preferences.must_visit_places = ["Place"]
    maps.responses = [search_success("a0"), search_success("a0")]

    def followup(_):
        action = _first_objective_action(model)
        return structured(ProposedActionBatch(actions=(action.model_copy(update={"query": "Place followup"}),)))

    model.responses = [lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model),))),
                       lambda _: legal_judgment(model), followup]
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "incomplete", getattr(result, "failure", None)
        assert (model.call_count, maps.call_count) == (3, 2)
        artifact = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
        assert artifact.qualification_state.steps[0].judgment_ref is not None
        second = artifact.qualification_state.steps[1]
        assert second.action_ref is not None and len(second.observation_refs) == 1
        assert second.judgment_ref is None
        assert result.budgets.semantic_invocations_consumed == 3
        assert await service.research_initial(request) == result
        assert (model.call_count, maps.call_count) == (3, 2)
        await service.aclose()
        fresh_model, fresh_maps = FakeModelOperation(), FakeMapAdapter()
        fresh = CandidateResearchService(runtime=FakeRuntime(fresh_model), map_adapter=fresh_maps,
            artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
        try:
            before_writes = len(repository.put_requests)
            assert await fresh.research_initial(request) == result
            assert (fresh_model.call_count, fresh_maps.call_count, len(repository.put_requests)) == (0, 0, before_writes)
        finally:
            await fresh.aclose()
    finally:
        await service.aclose()


async def test_saved_empty_job_is_replayed_before_new_judgment_skip():
    from app.candidate_research.artifacts import CandidateResearchModelJudgmentArtifact
    from app.candidate_research.fingerprints import content_digest
    request, model, maps, repository, service, _ = await accepted_case()
    try:
        run = service._runs[request.operation_plan.run_id]
        slot = request.operation_plan.iteration_slots[0]
        ref = repository.refs[slot.judgment_operation_id]
        saved = (await repository.get(ref, expected_model=CandidateResearchModelJudgmentArtifact)).content
        saved = CandidateResearchModelJudgmentArtifact.model_validate_json(
            saved.model_copy(update={"job": saved.job.model_copy(update={"required": ()})}).model_dump_json())
        tree = saved.model_dump(mode="json", exclude_none=False)
        encoded = json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ref = ref.model_copy(update={"content_hash": content_digest(tree), "size_bytes": len(encoded)})
        repository.seed(ref, saved)
        run.judgment_refs = [ref]
        run.steps = ()
        run.active_judgment_ref = None
        calls = (model.call_count, maps.call_count, len(repository.put_requests))
        assert await service._judgment(run, request, slot, saved.iteration_index) is None
        assert run.active_judgment_ref == ref
        assert calls == (model.call_count, maps.call_count, len(repository.put_requests))
    finally:
        await service.aclose()


async def test_action_recovery_cannot_use_last_model_call_and_then_issue_maps():
    from tests.unit.candidate_research.test_research_correction_recovery import setup
    from tests.unit.candidate_research.test_inline_correction import audited, rejected
    request, model, maps, repo, service = setup(slots=6)
    for i in range(5):
        maps.responses.append(search_success(f"a{i}"))
        model.responses.extend((lambda _, i=i: structured(ProposedActionBatch(actions=(_first_objective_action(model, query=f"query-{i}"),))), lambda _: legal_judgment(model)))
    model.responses.append(rejected())
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "incomplete", getattr(result, "failure", None)
        assert "semantic_budget_insufficient" in {item.value for item in result.stop_reasons}
        assert "correction_limit_reached" not in {item.value for item in result.stop_reasons}
        assert (model.call_count, maps.call_count) == (11, 5)
        assert result.budgets.semantic_invocations_consumed == 11
        assert result.budgets.corrections_consumed == 0
        await audited(repo, result.candidate_set_ref)
    finally:
        await service.aclose()


async def test_last_model_call_still_affords_pending_judgment_when_research_steps_are_exhausted():
    from tests.unit.candidate_research.test_research_correction_recovery import setup
    from tests.unit.candidate_research.test_inline_correction import audited
    request, model, maps, repo, service = setup(slots=6)
    for i in range(4):
        maps.responses.append(search_success(f"a{i}"))
        model.responses.extend((lambda _, i=i: structured(ProposedActionBatch(actions=(_first_objective_action(model, query=f"query-{i}"),))), lambda _: legal_judgment(model)))
    response = search_success("last")
    maps.responses.append(response.model_copy(update={"places": tuple(response.places[0].model_copy(update={"uid": f"last-{i}"}) for i in range(17))}))
    model.responses.extend((lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model, query="last-query"),))), failure(), lambda _: legal_judgment(model), lambda _: legal_judgment(model)))
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "incomplete", getattr(result, "failure", None)
        assert result.budgets.semantic_invocations_consumed == 12
        assert result.budgets.iterations_consumed == 6 and result.budgets.corrections_consumed == 1
        assert (model.call_count, maps.call_count) == (12, 5)
        assert json.loads(model.prompts[-1].user_text)["step_kind"] == "judge_existing"
        assert request.operation_plan.iteration_slots[5].action_operation_id not in repo.refs
        await audited(repo, result.candidate_set_ref)
        assert await service.research_initial(request) == result and model.call_count == 12
    finally:
        await service.aclose()


async def test_conditional_unresolved_role_does_not_repeatedly_open_meal_only_job():
    request, model, maps, _, service = setup_batch(iterations_used=5)
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "incomplete"
        run = service._runs[request.operation_plan.run_id]
        candidate = run.state.candidates[0]
        run.state = run.state.model_copy(update={"candidates": (candidate,)})
        assert await service._pending_judgment(run) is None
        assert (model.call_count, maps.call_count) == (2, 1)
    finally:
        await service.aclose()


@pytest.mark.parametrize("additional_fact", [None, 1, 8, 9, "new"])
async def test_fact_windows_prioritise_details_and_target_search_then_consume_older_facts(additional_fact):
    request, model, maps, _, service, _ = await accepted_case()
    try:
        run = service._runs[request.operation_plan.run_id]
        candidate = run.state.candidates[0]
        observations, _ = await service._observation_contexts(run, tuple(run.observation_refs))
        original = observations[0]
        observations = {index: json.loads(json.dumps(original)) for index in range(10)}
        for index, observation in observations.items():
            observation["handle"] = f"e:{index}"
            observation["result"]["places"][0]["category"] = f"category-{index}"
        observations[0]["accepted_search"]["interest"] = InterestTag.CULTURE.value
        observations[9]["result"] = {"record_type": "details_success", "details": {"business_hours": ["10:00-20:00"]}}
        candidate = candidate.model_copy(update={"observation_indexes": tuple(range(10)),
            "details": candidate.details.model_copy(update={"observation_index": 9})})
        state = run.state.model_copy(update={"candidates": (candidate,)})
        arguments = dict(iteration_index=0, step_kind="judge_existing", selected_ordinals=(0,))
        first, payload = build_judgment_context(state, observations, (), (), **arguments)
        assert first.evidence_windows[0].evidence_indexes[:2] == (9, 0)
        assert len(first.evidence_windows[0].evidence_indexes) == 8
        receipts = record_assessments((), first, required_patch(payload, unresolved=True))
        second, payload = build_judgment_context(state, observations, (), receipts, **arguments)
        assert second.evidence_windows[0].evidence_digest != first.evidence_windows[0].evidence_digest
        assert second.evidence_windows[0].evidence_indexes == (9, 2, 1)
        receipts = record_assessments(receipts, second, required_patch(payload, unresolved=True))
        final, _ = build_judgment_context(state, observations, (), receipts, **arguments)
        assert not final.required and not final.candidate_ordinals
        if additional_fact is not None:
            observations[10] = json.loads(json.dumps(observations[8 if additional_fact == "new" else additional_fact]))
            observations[10]["handle"] = "e:10"
            if additional_fact == "new":
                observations[10]["result"]["places"][0]["category"] = "a-new-category"
            candidate = candidate.model_copy(update={"observation_indexes": tuple(range(11)),
                "details": candidate.details.model_copy(update={"observation_index": 10})
                    if additional_fact == 9 else candidate.details})
            state = state.model_copy(update={"candidates": (candidate,)})
            next_job, payload = build_judgment_context(state, observations, (), receipts, **arguments)
            if additional_fact == "new":
                assert next_job.required and 10 in next_job.evidence_windows[0].evidence_indexes
                receipts = record_assessments(receipts, next_job, required_patch(payload, unresolved=True))
                next_job, _ = build_judgment_context(state, observations, (), receipts, **arguments)
            assert not next_job.required and not next_job.candidate_ordinals
    finally:
        await service.aclose()


@pytest.mark.parametrize("must_already_available", [False, True])
async def test_bounded_job_prioritises_unmet_confirmed_must_not_already_available_must(must_already_available):
    from app.candidate_research.artifacts import ArtifactCandidateRecordV2Snapshot
    request, _, _, _, service, _ = await accepted_case()
    try:
        run = service._runs[request.operation_plan.run_id]
        base = run.state.candidates[0]
        observations, _ = await service._observation_contexts(run, tuple(run.observation_refs))
        candidates, places = [], []
        for ordinal in range(17):
            tree = base.model_dump(mode="json")
            tree["admission_ordinal"] = ordinal
            tree["core"]["uid"] = f"a{ordinal}"
            tree["state"] = "withheld" if ordinal == 16 else "eligible"
            tree["must_matches"] = [dict(requirement_group_id=group,
                verdict="confirmed" if (group == ("must-b" if ordinal == 16 else "must-a")
                    or must_already_available and ordinal == 0 and group == "must-b") else "no_match",
                reason_code="exact_name_match", evidence_indexes=[0]) for group in ("must-a", "must-b")]
            tree["avoid_matches"] = [dict(requirement_group_id="avoid-a",
                verdict="unresolved" if ordinal == 16 else "no_match",
                reason_code="evidence_insufficient" if ordinal == 16 else "candidate_out_of_scope",
                evidence_indexes=[0])]
            candidates.append(ArtifactCandidateRecordV2Snapshot.model_validate_json(json.dumps(tree)))
            places.append({**observations[0]["result"]["places"][0], "uid": f"a{ordinal}"})
        observations[0]["result"]["places"] = places
        state = run.state.model_copy(update={"candidates": tuple(candidates)})
        requirements = tuple(dict(requirement_group_id=group, requirement_kind=kind,
            requirement_item_ids=(group,), confirmed_name=group)
            for kind, group in (("must_visit", "must-a"), ("must_visit", "must-b"), ("avoid", "avoid-a")))
        job, _ = build_judgment_context(state, observations, requirements, (),
                                       iteration_index=5, step_kind="judge_existing")
        assert len(job.candidate_ordinals) == 16
        if must_already_available:
            assert job.candidate_ordinals == tuple(range(16))
        else:
            assert job.candidate_ordinals == (16, *range(15))
            assert any(item.candidate_ordinal == 16 and item.kind == "avoid" for item in job.required)
    finally:
        await service.aclose()


@pytest.mark.parametrize("cancel_admitted", [False, True])
async def test_judge_only_cancellation_preserves_actual_call_count(cancel_admitted):
    request, model, maps, _, service = setup_batch()
    if cancel_admitted:
        model.responses[2] = asyncio.CancelledError()
    else:
        original = model.ainvoke_structured
        async def cancel_before_admission(prompt, schema):
            if model.call_count == 2:
                raise asyncio.CancelledError()
            return await original(prompt, schema)
        model.ainvoke_structured = cancel_before_admission
    try:
        with pytest.raises(asyncio.CancelledError):
            await service.research_initial(request)
        run = service._runs[request.operation_plan.run_id]
        assert run.semantic_invocations == 2 + int(cancel_admitted)
        assert maps.call_count == 1
        assert not any(item.candidate_ordinal == 16 for item in run.assessments)
        assert run.provider_attempts_complete is not cancel_admitted
        replay = await service.research_initial(request)
        assert replay.failure.code.value == "operation_result_indeterminate"
        assert model.call_count == 2 + int(cancel_admitted)
    finally:
        await service.aclose()


async def test_judge_only_accepted_write_failure_does_not_publish_assessments():
    from app.workflow_contracts.errors import ArtifactRepositoryError
    class RejectJudge(FakeRepository):
        async def put(self, request):
            if request.artifact_type.endswith("model_judgment") and request.content["job"]["step_kind"] == "judge_existing":
                raise ArtifactRepositoryError()
            return await super().put(request)
    request, model, maps, _, service = setup_batch(repository=RejectJudge())
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "failed"
        assert result.failure.code.value == "operation_result_indeterminate"
        run = service._runs[request.operation_plan.run_id]
        assert not any(item.candidate_ordinal == 16 for item in run.assessments)
        assert (model.call_count, maps.call_count) == (3, 1)
        assert result.budgets.semantic_invocations_consumed == 3
    finally:
        await service.aclose()


async def test_confirmed_must_blocked_by_avoid_uses_details_instead_of_repeating_name_search():
    request, model, maps, repository, service = setup_batch()
    request.confirmed.requirement.preferences.must_visit_places = ["Place"]
    request.confirmed.requirement.preferences.avoid_places = ["Other Place"]
    maps.responses = [search_success("a0"), details_success("a0")]

    def judgment(_, *, resolved):
        payload = json.loads(model.prompts[-1].user_text)
        evidence = EvidenceHandle(handle_id="e:1" if resolved else "e:0")
        groups = tuple(RequirementGroupJudgmentUpdate(requirement_group_id=item["requirement_group_id"],
            requirement_kind=item["requirement_kind"], evaluated_candidate_handles=("c:0",),
            default_verdict=MatchVerdict.CONFIRMED if item["requirement_kind"] == "must_visit" else (
                MatchVerdict.NO_MATCH if resolved else MatchVerdict.UNRESOLVED),
            default_reason_code=RequirementReasonCode.EXACT_NAME_MATCH if item["requirement_kind"] == "must_visit" else (
                RequirementReasonCode.CANDIDATE_OUT_OF_SCOPE if resolved else RequirementReasonCode.EVIDENCE_INSUFFICIENT),
            default_evidence_handles=(evidence,), exceptions=()) for item in payload["requirements"])
        patch = JudgmentPatch(role_updates=(RoleJudgmentUpdate(candidate_handle="c:0",
            role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED,
            reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS, evidence_handles=(evidence,)),),
            interest_updates=(), meal_updates=(), requirement_group_updates=groups)
        return legal_judgment(model, patch)

    model.responses = [
        lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model),))),
        lambda count: judgment(count, resolved=False),
        lambda _: structured(ProposedActionBatch(actions=(ProposedDetailsAction(ordinal=0,
            objective_id=json.loads(model.prompts[-1].user_text)["objectives"][0]["objective_id"], uid="a0"),))),
        lambda count: judgment(count, resolved=True),
    ]
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "incomplete", getattr(result, "failure", None)
        assert len(maps.search_requests) == 1 and len(maps.details_requests) == 1
        assert maps.search_requests[0].query == "Place"
        assert maps.details_requests[0].uid == "a0"
        artifact = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
        candidate = artifact.record.state.candidates[0]
        assert candidate_admitted_purposes(candidate) == ("attraction",)
        assert candidate.must_matches[0].verdict is MatchVerdict.CONFIRMED
        assert candidate.avoid_matches[0].verdict is MatchVerdict.NO_MATCH
        assert (model.call_count, maps.call_count) == (4, 2)
        assert await service.research_initial(request) == result
    finally:
        await service.aclose()


@pytest.mark.parametrize("details_result", ["no_result", "insufficient"])
async def test_confirmed_must_can_search_other_candidates_after_own_details_exhausted(details_result):
    from tests.support.candidate_research_fakes import details_no_result
    request, model, maps, _, service = setup_batch(iterations_used=3)
    request.confirmed.requirement.preferences.must_visit_places = ["Place"]
    request.confirmed.requirement.preferences.avoid_places = ["Other Place"]
    maps.responses = [search_success("a0"),
        details_no_result() if details_result == "no_result" else details_success("a0"), search_success("a1")]

    def unresolved_avoid(_):
        payload = json.loads(model.prompts[-1].user_text)
        patch = required_patch(payload, unresolved=True)
        patch = patch.model_copy(update={"requirement_group_updates": tuple(group.model_copy(update={
            "default_verdict": MatchVerdict.CONFIRMED,
            "default_reason_code": RequirementReasonCode.EXACT_NAME_MATCH}) if group.requirement_kind == "must_visit"
            else group for group in patch.requirement_group_updates)})
        return structured(patch)

    def alternative(_):
        payload = json.loads(model.prompts[-1].user_text)
        assert payload["objectives"][0]["kind"] == "must_visit_resolution"
        assert "search_places" in payload["objectives"][0]["allowed_actions"]
        return structured(ProposedActionBatch(actions=(_first_objective_action(model, query="alternative"),)))

    model.responses = [
        lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model),))), unresolved_avoid,
        lambda _: structured(ProposedActionBatch(actions=(ProposedDetailsAction(ordinal=0,
            objective_id=json.loads(model.prompts[-1].user_text)["objectives"][0]["objective_id"], uid="a0"),))),
        unresolved_avoid, alternative, lambda _: legal_judgment(model),
    ]
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "incomplete", (getattr(result, "failure", None), model.call_count,
            maps.call_count, [json.loads(prompt.user_text).get("step_kind", "action") for prompt in model.prompts])
        assert [item.query for item in maps.search_requests] == ["Place", "alternative"]
        assert len(maps.details_requests) == 1
        assert (model.call_count, maps.call_count) == (6, 3)
    finally:
        await service.aclose()


async def test_action_two_remaining_calls_pay_for_correction_and_judgment():
    from tests.unit.candidate_research.test_research_correction_recovery import setup, action
    from tests.unit.candidate_research.test_inline_correction import audited
    request, model, maps, repo, service = setup(slots=6)
    for i in range(4):
        maps.responses.append(search_success(f"a{i}"))
        model.responses.extend((lambda _, i=i: structured(ProposedActionBatch(actions=(_first_objective_action(model, query=f"query-{i}"),))), lambda _: legal_judgment(model)))
    maps.responses.append(search_success("last"))
    model.responses.extend((failure(), failure(), action(model), lambda _: legal_judgment(model)))
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == "incomplete", getattr(result, "failure", None)
        assert result.budgets.semantic_invocations_consumed == 12 and result.budgets.corrections_consumed == 2
        assert result.budgets.iterations_consumed == 5
        assert (model.call_count, maps.call_count) == (12, 5)
        assert "correction_limit_reached" not in {item.value for item in result.stop_reasons}
        await audited(repo, result.candidate_set_ref)
    finally:
        await service.aclose()
