import json
from itertools import product
from uuid import uuid4

import pytest
from tests.unit.candidate_research.test_qualification import legal_judgment

from app.candidate_research import (
    CandidateResearchService, JudgmentPatch, RequirementGroupJudgmentUpdate,
    RequirementReasonCode, MatchVerdict, EvidenceHandle,
    ProposedActionBatch, RoleJudgmentUpdate, ResearchRole, RoleVerdict, RoleReasonCode, NoUsefulActionProposed,
)
from app.candidate_research.judgments import merge_requirement_update, merge_judgment_patch
from tests.unit.candidate_research.test_service_initial import _request, _first_objective_action
from tests.support.candidate_research_fakes import (
    FakeRuntime, FakeModelOperation, FakeMapAdapter, FakeRepository, FakeBudgetManager,
    structured, search_success,
)


def group(handles, verdict, *, kind='must_visit', evidence=0):
    return RequirementGroupJudgmentUpdate(requirement_group_id='g', requirement_kind=kind,
        evaluated_candidate_handles=tuple(f'c:{i}' for i in handles), default_verdict=verdict,
        default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT,
        default_evidence_handles=(EvidenceHandle(handle_id=f'e:{evidence}'),), exceptions=())


def expanded(value):
    exceptions = {x.candidate_handle: x for x in value.exceptions}
    return {h: (exceptions[h].verdict, exceptions[h].evidence_handles) if h in exceptions
            else (value.default_verdict, value.default_evidence_handles)
            for h in value.evaluated_candidate_handles}


@pytest.mark.parametrize('kind', ['must_visit', 'avoid'])
@pytest.mark.parametrize('old,new', list(product(MatchVerdict, repeat=2)))
def test_partial_default_preserves_126_other_candidates_and_is_idempotent(kind, old, new):
    current = group(range(128), old, kind=kind)
    patch = group((4, 5), new, kind=kind, evidence=1)
    merged = merge_requirement_update(current, patch)
    before, after = expanded(current), expanded(merged)
    assert set(after) == set(before)
    assert all(after[h] == before[h] for h in before if h not in ('c:4', 'c:5'))
    assert all(after[h] == expanded(patch)[h] for h in ('c:4', 'c:5'))
    assert merge_requirement_update(merged, patch) == merged
    assert current == group(range(128), old, kind=kind)


def test_disjoint_updates_commute_and_public_patch_uses_same_reducer():
    base = group(range(6), MatchVerdict.UNRESOLVED)
    a = group((1, 3), MatchVerdict.CONFIRMED, evidence=1)
    b = group((2, 4), MatchVerdict.NO_MATCH, evidence=2)
    left = merge_requirement_update(merge_requirement_update(base, a), b)
    right = merge_requirement_update(merge_requirement_update(base, b), a)
    assert left == right
    def patch(g):
        return JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=(g,))
    assert merge_judgment_patch(patch(base), patch(a)).requirement_group_updates[0] == merge_requirement_update(base, a)
    with pytest.raises(ValueError, match='identity'):
        merge_requirement_update(base, group((1,), MatchVerdict.CONFIRMED, kind='avoid'))


async def test_full_state_failure_is_rejected_before_accepted_write_and_keeps_usage():
    class BrokenReducer(CandidateResearchService):
        def _apply_patch(self, state, patch):
            raise ValueError('injected invariant failure')
    run_id = uuid4()
    request, hotel_ref, hotel, _ = _request(run_id)
    operation = FakeModelOperation()
    operation.responses.extend([
        lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(operation),))),
        lambda _: legal_judgment(operation, JudgmentPatch(role_updates=(RoleJudgmentUpdate(candidate_handle='c:0',
            role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED,
            reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS,
            evidence_handles=(EvidenceHandle(handle_id='e:0'),)),),
            interest_updates=(), meal_updates=(), requirement_group_updates=())),
    ])
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel)
    maps = FakeMapAdapter([search_success('test')])
    service = BrokenReducer(runtime=FakeRuntime(operation), map_adapter=maps,
        artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    try:
        result = await service.research_initial(request)
        assert result.failure.code.value == 'state_invariant_failed'
        judgments = [r.content for r in repository.put_requests if r.artifact_type.endswith('model_judgment')]
        assert len(judgments) == 1
        assert judgments[0]['schema_version'] == '3.0'
        assert judgments[0]['result']['record_type'] == 'controller_rejected'
        assert operation.call_count == 2 and maps.call_count == 1
        assert result.budgets.semantic_invocations_consumed == 2
        assert result.budgets.known_usage.total_tokens > 0
        before = (operation.call_count, maps.call_count, len(repository.put_requests))
        assert await service.research_initial(request) == result
        assert before == (operation.call_count, maps.call_count, len(repository.put_requests))
    finally:
        await service.aclose()


async def test_new_candidate_does_not_inherit_confirmed_group_default():
    run_id = uuid4()
    request, hotel_ref, hotel, _ = _request(run_id)
    request.confirmed.requirement.preferences.must_visit_places = ['某公园', '某展馆']
    operation = FakeModelOperation()
    def action(_):
        return structured(ProposedActionBatch(actions=(_first_objective_action(operation, query=f'query-{operation.call_count}'),)))
    def first_judgment(_):
        directory = json.loads(operation.prompts[-1].user_text)['requirements'][0]
        update = group((0,), MatchVerdict.CONFIRMED).model_copy(update={
            'requirement_group_id': directory['requirement_group_id']})
        return legal_judgment(operation, JudgmentPatch(role_updates=(RoleJudgmentUpdate(candidate_handle='c:0',
            role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED,
            reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS,
            evidence_handles=(EvidenceHandle(handle_id='e:0'),)),), interest_updates=(), meal_updates=(),
            requirement_group_updates=(update,)))
    operation.responses.extend([action, first_judgment, action,
        lambda _: legal_judgment(operation),
        structured(NoUsefulActionProposed(reason_code='insufficient_remaining_budget')),
        structured(NoUsefulActionProposed(reason_code='prior_no_progress'))])
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel)
    service = CandidateResearchService(runtime=FakeRuntime(operation),
        map_adapter=FakeMapAdapter([search_success('first'), search_success('second')]),
        artifact_repository=repository, budget_manager=FakeBudgetManager(run_id, place_limit=2))
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == 'incomplete', getattr(result, 'failure', None)
        state = repository.contents[result.candidate_set_ref.artifact_id]['record']['state']
        assert len(state['candidates']) == 2
        assert state['candidates'][0]['must_matches'][0]['verdict'] == 'confirmed'
        assert state['candidates'][1]['must_matches'][0]['verdict'] == 'unresolved'
        assert state['candidates'][1]['must_matches'][0]['evidence_indexes'] == [1]
        from app.candidate_research.artifacts import CandidateSetStateSnapshot
        before = CandidateSetStateSnapshot.model_validate_json(json.dumps(state), strict=True)
        def patch(group_id):
            update = group((0,), MatchVerdict.NO_MATCH).model_copy(update={'requirement_group_id': group_id})
            return JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=(update,))
        a, b = (patch(item.group_id) for item in before.requirement_group_states)
        ab = service._apply_patch(service._apply_patch(before, a), b)
        ba = service._apply_patch(service._apply_patch(before, b), a)
        assert ab == ba
        assert [match.requirement_group_id for match in ab.candidates[0].must_matches] == [item.group_id for item in before.requirement_group_states]
    finally:
        await service.aclose()


@pytest.mark.parametrize('after_commit', [False, True])
async def test_judgment_write_interrupt_checks_exact_readback_and_preserves_usage(after_commit):
    from tests.support.candidate_research_fakes import RepoError
    class InterruptRepository(FakeRepository):
        async def put(self, request):
            if request.artifact_type.endswith('model_judgment'):
                if after_commit:
                    await super().put(request)
                raise RepoError()
            return await super().put(request)
    run_id = uuid4()
    request, hotel_ref, hotel, _ = _request(run_id)
    operation = FakeModelOperation()
    operation.responses.extend([
        lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(operation),))),
        lambda _: legal_judgment(operation),
        structured(NoUsefulActionProposed(reason_code='insufficient_remaining_budget')),
        structured(NoUsefulActionProposed(reason_code='prior_no_progress')),
    ])
    repository = InterruptRepository()
    repository.seed(hotel_ref, hotel)
    maps = FakeMapAdapter([search_success('test')])
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=maps,
        artifact_repository=repository, budget_manager=FakeBudgetManager(run_id, place_limit=1))
    try:
        result = await service.research_initial(request)
        if after_commit:
            assert result.outcome_type == 'incomplete'
        else:
            assert result.failure.code.value == 'operation_result_indeterminate'
        assert result.budgets.semantic_invocations_consumed == operation.call_count >= 2
        assert result.budgets.known_usage.total_tokens == 2 * operation.call_count
        assert maps.call_count == result.budgets.place_calls_consumed == 1
        before = (operation.call_count, maps.call_count, len(repository.put_requests))
        assert await service.research_initial(request) == result
        assert before == (operation.call_count, maps.call_count, len(repository.put_requests))
    finally:
        await service.aclose()


async def test_stale_candidate_not_in_actual_incremental_prompt_is_rejected():
    run_id = uuid4()
    request, hotel_ref, hotel, _ = _request(run_id)
    operation = FakeModelOperation()
    def action(_):
        return structured(ProposedActionBatch(actions=(_first_objective_action(operation, query=f'query-{operation.call_count}'),)))
    def judgment(verdict):
        return structured(JudgmentPatch(role_updates=(RoleJudgmentUpdate(candidate_handle='c:0',
            role=ResearchRole.ATTRACTION, verdict=verdict,
            reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS if verdict is RoleVerdict.SUPPORTED else RoleReasonCode.PROVIDER_CATEGORY_CONTRADICTS,
            evidence_handles=(EvidenceHandle(handle_id='e:0'),)),), interest_updates=(), meal_updates=(), requirement_group_updates=()))
    operation.responses.extend([action, lambda _: legal_judgment(operation, judgment(RoleVerdict.SUPPORTED).value),
        action, judgment(RoleVerdict.CONTRADICTED)])
    repo = FakeRepository()
    repo.seed(hotel_ref, hotel)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter([search_success('first'),search_success('second')]),
        artifact_repository=repo,budget_manager=FakeBudgetManager(run_id))
    try:
        result = await service.research_initial(request)
        assert [item['handle'] for item in json.loads(operation.prompts[3].user_text)['candidates']] == ['c:1']
        assert result.failure.code.value == 'judgment_patch_invalid'
        records = [r.content['result']['record_type'] for r in repo.put_requests if r.artifact_type.endswith('model_judgment')]
        assert records == ['accepted', 'controller_rejected']
        state = repo.contents[repo.refs[request.operation_plan.terminal_successor_operation_id].artifact_id]['record']['state']
        assert state['candidates'][0]['roles'][0]['verdict'] == 'supported'
    finally:
        await service.aclose()
