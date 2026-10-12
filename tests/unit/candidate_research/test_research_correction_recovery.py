import asyncio
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.candidate_research import (
    CandidateResearchService, CandidateResearchArtifact, ProposedActionBatch,
    JudgmentPatch, RoleJudgmentUpdate, ResearchRole, RoleVerdict, RoleReasonCode, EvidenceHandle,
    CandidateCountShortfallGap, CandidateResearchSupplementalRequest, CandidateResearchOperationPlan,
)
from app.candidate_research.artifacts import (
    CandidateResearchModelActionArtifact, CandidateResearchModelJudgmentArtifact,
    unwrap_model_record,
)
from app.llm_runtime import LLMInvocationFailure, LLMRuntimeError, LLMErrorCode, ModelRole
from app.workflow_contracts import OperationId, WorkflowPhase, ArtifactRepositoryError
from tests.support.candidate_research_fakes import (
    FakeRuntime, FakeModelOperation, FakeMapAdapter, FakeRepository, FakeBudgetManager,
    structured, search_no_result, search_success,
)
from tests.unit.candidate_research.test_service_initial import _request, _first_objective_action, _action_batch_from_prompt
from tests.unit.candidate_research.test_service_supplemental import _slots


def failure(code='structured_output_invalid', *, duplicate=False):
    return LLMInvocationFailure(error=LLMRuntimeError(code=LLMErrorCode(code),
        role=ModelRole.CANDIDATE_RESEARCH, retryable=False, possible_duplicate=duplicate))


def empty_patch():
    return JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())


def setup(*, width=1, slots=1, service_class=CandidateResearchService, repository=None):
    request, hotel_ref, hotel, _ = _request(uuid4(), place_width=width)
    model, maps = FakeModelOperation(), FakeMapAdapter()
    repository = repository or FakeRepository()
    repository.seed(hotel_ref, hotel)
    service = service_class(runtime=FakeRuntime(model), map_adapter=maps,
        artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id, iteration_limit=slots))
    return request, model, maps, repository, service


def action(model):
    return lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model),)))


@pytest.mark.parametrize('stage', ['action', 'judgment'])
@pytest.mark.parametrize('code', ['structured_output_invalid', 'output_truncated'])
async def test_recovery_reuses_evidence_and_terminal_replays_without_calls(stage, code):
    request, model, maps, repo, service = setup()
    maps.responses.append(search_no_result())
    model.responses.extend(([failure(code), action(model), structured(empty_patch())] if stage == 'action'
        else [action(model), failure(code), structured(empty_patch())]))
    result = await service.research_initial(request)
    assert result.outcome_type == 'incomplete', getattr(result, 'failure', None)
    assert (model.call_count, maps.call_count) == (3, 1)
    assert result.budgets.semantic_invocations_consumed == 3
    assert result.budgets.iterations_consumed == 1 and result.budgets.place_calls_consumed == 1
    assert result.budgets.known_usage.total_tokens == 4
    assert not result.budgets.usage_complete and not result.budgets.provider_attempts_complete
    prior = json.loads(model.prompts[0 if stage == 'action' else 1].user_text)
    retry = json.loads(model.prompts[1 if stage == 'action' else 2].user_text)
    feedback = retry.pop('output_recovery')
    assert prior == retry and feedback['code'] == code and feedback['attempt'] == 1
    writes = [item for item in repo.put_requests if item.artifact_type.endswith(f'model_{stage}')]
    assert len(writes) == 1
    record = writes[0].content['result']
    assert record['record_type'] == 'recovered' and record['final']['record_type'] == 'accepted'
    assert record['attempts'][0]['metadata'] is None
    before = (model.call_count, maps.call_count, len(repo.put_requests))
    await service.aclose()
    replay = CandidateResearchService(runtime=FakeRuntime(), map_adapter=maps,
        artifact_repository=repo, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    assert await replay.research_initial(request) == result
    assert before == (model.call_count, maps.call_count, len(repo.put_requests))
    await replay.aclose()


async def test_action_and_judgment_share_two_attempts_and_third_error_is_final():
    request, model, maps, repo, service = setup()
    maps.responses.append(search_no_result())
    model.responses.extend([failure(), action(model), failure(), failure(), structured(empty_patch())])
    result = await service.research_initial(request)
    assert result.failure.error.code is LLMErrorCode.STRUCTURED_OUTPUT_INVALID
    assert model.call_count == 4 and len(model.responses) == 1 and maps.call_count == 1
    assert result.budgets.semantic_invocations_consumed == 4
    attempts = [a for w in repo.put_requests if w.artifact_type.endswith(('model_action', 'model_judgment'))
                for a in w.content['result'].get('attempts', ())]
    assert [a['recovery_number'] for a in attempts] == [1, 2]
    assert [a['invocation_index'] for a in attempts] == [0, 2]
    assert await service.research_initial(request) == result and model.call_count == 4
    await service.aclose()


async def test_two_recoveries_still_invalid_stop_without_maps_or_partial_output():
    request, model, maps, repo, service = setup()
    model.responses.extend([failure()] * 4)
    result = await service.research_initial(request)
    assert result.failure.error.code is LLMErrorCode.STRUCTURED_OUTPUT_INVALID
    assert (model.call_count, maps.call_count) == (3, 0)
    assert result.budgets.semantic_invocations_consumed == 3
    record = (await repo.get(repo.refs[request.operation_plan.iteration_slots[0].action_operation_id],
        expected_model=CandidateResearchModelActionArtifact)).content
    assert len(record.result.attempts) == 2 and record.invocation_index == 2
    # Replaying a stored failed operation cannot start another recovery loop.
    run = service._runs[request.operation_plan.run_id]
    replay = await service._replay_action(run, request.operation_plan.iteration_slots[0].action_operation_id)
    assert replay[1].error.code is LLMErrorCode.STRUCTURED_OUTPUT_INVALID
    assert model.call_count == 3
    await service.aclose()


@pytest.mark.parametrize('code,duplicate', [('timeout', False), ('provider_unavailable', False),
    ('authentication_failed', False), ('structured_output_invalid', True), ('output_truncated', True)])
async def test_non_recoverable_or_uncertain_failure_does_not_consume_recovery(code, duplicate):
    request, model, maps, repo, service = setup()
    model.responses.extend([failure(code, duplicate=duplicate), action(model)])
    result = await service.research_initial(request)
    assert result.failure.error.code.value == code and model.call_count == 1 and maps.call_count == 0
    assert service._runs[request.operation_plan.run_id].output_recoveries_used == 0
    await service.aclose()


@pytest.mark.parametrize('used', [10, 11])
async def test_semantic_twelve_limit_includes_recovery(used):
    class NearlySpent(CandidateResearchService):
        async def _load_or_create_run(self, *args):
            run = await super()._load_or_create_run(*args)
            run.semantic_invocations = used
            run.provider_attempts = used
            run.usage = dict(prompt_tokens=used, completion_tokens=used, total_tokens=used * 2,
                            reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=used)
            return run
    request, model, maps, repo, service = setup(service_class=NearlySpent)
    model.responses.extend([failure()] * 4)
    result = await service.research_initial(request)
    if used == 10:
        assert result.failure.error.code is LLMErrorCode.STRUCTURED_OUTPUT_INVALID
        assert model.call_count == 2 and result.budgets.semantic_invocations_consumed == 12
    else:
        assert result.outcome_type == 'incomplete'
        assert model.call_count == 0 and result.budgets.semantic_invocations_consumed == 11
        assert 'semantic_budget_insufficient' in {item.value for item in result.stop_reasons}
    assert maps.call_count == 0
    await service.aclose()


async def test_initial_and_supplemental_share_recovery_without_repeating_maps():
    request, model, maps, repo, service = setup()
    model.responses.extend([failure(), failure(), action(model), structured(empty_patch())])
    maps.responses.append(search_no_result())
    initial = await service.research_initial(request)
    assert initial.outcome_type == 'incomplete'
    run_id = request.operation_plan.run_id
    predecessor = (await repo.get(initial.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    evidence = predecessor.record.state.observation_refs[0]
    plan = CandidateResearchOperationPlan(run_id=run_id, iteration_slots=_slots(run_id, count=5, prefix='output_recovery_supp'),
        terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step='recovery_supp_terminal'))
    supplemental = CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=initial.candidate_set_ref,
        operation_plan=plan, gaps=(CandidateCountShortfallGap(gap_id='remaining', evidence_refs=(evidence,),
            role=ResearchRole.ATTRACTION, current_count=0, target_count=1),))
    model.responses.extend([failure(), action(model)])
    service._budget.iteration_limit = 6
    result = await service.research_supplemental(supplemental)
    assert result.failure.error.code is LLMErrorCode.STRUCTURED_OUTPUT_INVALID
    assert model.call_count == 5 and maps.call_count == 1
    assert result.budgets.semantic_invocations_consumed == 5
    await service.aclose()


def role_patch(handle='c:0', evidence='e:1'):
    return JudgmentPatch(role_updates=(RoleJudgmentUpdate(candidate_handle=handle,
        role=ResearchRole.ATTRACTION, verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS,
        evidence_handles=(EvidenceHandle(handle_id=evidence),)),), interest_updates=(), meal_updates=(), requirement_group_updates=())


@pytest.mark.parametrize('mode', ['fixed', 'exhausted', 'unknown', 'unknown_evidence', 'internal'])
async def test_judgment_ownership_recovers_but_unknown_refs_and_state_errors_fail(mode):
    class BrokenState(CandidateResearchService):
        def _apply_patch(self, state, patch):
            raise ValueError('injected_internal')
    request, model, maps, repo, service = setup(width=2, service_class=BrokenState if mode == 'internal' else CandidateResearchService)
    service._budget.iteration_limit = 6
    service._budget.consumed[service._iteration_key()] = 5
    maps.responses.extend([search_success('one'), search_success('two')])
    model.responses.append(lambda _: structured(_action_batch_from_prompt(model, 2)))
    if mode == 'unknown_evidence':
        model.responses.append(structured(role_patch(evidence='e:47')))
    elif mode == 'unknown':
        model.responses.append(structured(role_patch('c:9', 'e:0')))
    elif mode == 'internal':
        from tests.unit.candidate_research.test_qualification import required_patch
        model.responses.append(lambda _: structured(required_patch(json.loads(model.prompts[-1].user_text))))
    else:
        model.responses.extend([structured(role_patch())] * (3 if mode == 'exhausted' else 1))
        if mode == 'fixed':
            from tests.unit.candidate_research.test_qualification import required_patch
            model.responses.append(lambda _: structured(required_patch(json.loads(model.prompts[-1].user_text))))
    result = await service.research_initial(request)
    expected_calls = {'fixed': 3, 'exhausted': 4, 'unknown': 2, 'unknown_evidence': 2, 'internal': 2}[mode]
    assert model.call_count == expected_calls and maps.call_count == 2
    assert result.budgets.known_usage.total_tokens == 2 * expected_calls and result.budgets.usage_complete
    if mode == 'fixed':
        assert result.outcome_type == 'incomplete', getattr(result, 'failure', None)
        artifact = (await repo.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
        assert artifact.record.state.candidates[0].roles[0].verdict is RoleVerdict.SUPPORTED
    else:
        assert result.failure.code.value == ('state_invariant_failed' if mode == 'internal' else 'judgment_patch_invalid')
        assert service._runs[request.operation_plan.run_id].state.candidates == ()
    records = [w.content for w in repo.put_requests if w.artifact_type.endswith('model_judgment')]
    assert len(records) == 1
    assert records[0]['result']['record_type'] == ('recovered' if mode in {'fixed', 'exhausted'} else 'controller_rejected')
    assert await service.research_initial(request) == result and model.call_count == expected_calls
    await service.aclose()


@pytest.mark.parametrize('invalid', ['json', 'tag', 'role', 'interest', 'meal', 'group'])
async def test_real_m02_strict_decoder_recovers_schema_and_duplicate_keys(invalid):
    from app.llm_runtime import DeepSeekRuntime
    from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper
    from tests.unit.llm_runtime.test_structured_runtime import make_response
    from app.candidate_research import InterestJudgmentUpdate, InterestReasonCode, MealJudgmentUpdate, MealIntent, MealReasonCode
    from app.requirements import InterestTag
    from tests.unit.candidate_research.test_incremental_contract import group
    from app.candidate_research import MatchVerdict

    class Provider(ScriptedProviderAdapter):
        def __init__(self):
            super().__init__([])
            self.invalid_sent = False

        async def ainvoke_json(self, request):
            self.json_requests.append(request)
            self.call_count += 1
            data = json.loads(request.user)
            is_action = 'objectives' in data
            if is_action:
                from types import SimpleNamespace
                output = _first_objective_action(SimpleNamespace(prompts=[type('Prompt', (), {'user_text': request.user})()]))
                value = {'output': ProposedActionBatch(actions=(output,)).model_dump(mode='json')}
                if invalid in {'json', 'tag'} and not self.invalid_sent:
                    self.invalid_sent = True
                    if invalid == 'json':
                        return make_response('{')
                    value['output'].pop('outcome_type')
            else:
                patch = empty_patch()
                if invalid not in {'json', 'tag'} and not self.invalid_sent:
                    self.invalid_sent = True
                    evidence = (EvidenceHandle(handle_id='e:0'),)
                    if invalid == 'role':
                        item = role_patch(evidence='e:0').role_updates[0]
                    elif invalid == 'interest':
                        item = InterestJudgmentUpdate(candidate_handle='c:0', interest=InterestTag.CULTURE,
                            verdict=MatchVerdict.CONFIRMED, reason_code=InterestReasonCode.NAME_OR_CATEGORY_MATCH, evidence_handles=evidence)
                    elif invalid == 'meal':
                        item = MealJudgmentUpdate(candidate_handle='c:0', meal_intent=MealIntent.LUNCH,
                            verdict=MatchVerdict.UNRESOLVED, reason_code=MealReasonCode.EVIDENCE_INSUFFICIENT, evidence_handles=evidence)
                    else:
                        item = group((), MatchVerdict.UNRESOLVED)
                    field = 'requirement_group_updates' if invalid == 'group' else invalid + '_updates'
                    patch = patch.model_copy(update={field: (item, item)})
                value = patch.model_dump(mode='json')
            return make_response(json.dumps(value))

    request, _, maps, repo, service = setup()
    maps.responses.append(search_no_result())
    provider, sleeper = Provider(), RecordingSleeper()
    runtime = DeepSeekRuntime(provider=provider, sleeper=sleeper)
    service._runtime = runtime
    result = await service.research_initial(request)
    assert result.outcome_type == 'incomplete', getattr(result, 'failure', None)
    assert provider.call_count == 3 and maps.call_count == 1
    assert not sleeper.async_sleeps and result.budgets.semantic_invocations_consumed == 3
    assert result.budgets.known_usage.total_tokens == 60 and not result.budgets.usage_complete
    recovery = [json.loads(r.user)['output_recovery'] for r in provider.json_requests if 'output_recovery' in json.loads(r.user)]
    assert len(recovery) == 1 and recovery[0]['code'] == 'structured_output_invalid'
    first_schema = provider.json_requests[0].json_schema
    assert 'outcome_type' in first_schema['$defs']['ProposedActionBatch']['required']
    await service.aclose()
    await runtime.aclose()


async def test_cancel_during_recovery_propagates_without_new_map_or_accepted_record():
    request, model, maps, repo, service = setup()
    model.responses.extend([failure(), asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await service.research_initial(request)
    assert model.call_count == 2 and maps.call_count == 0
    run = service._runs[request.operation_plan.run_id]
    assert run.semantic_invocations == 2 and run.output_recoveries_used == 1
    assert not run.usage_complete and not run.provider_attempts_complete
    assert not [w for w in repo.put_requests if w.artifact_type.endswith('model_action')]
    await service.aclose()


@pytest.mark.parametrize('final_code', ['structured_output_invalid', 'input_too_large'])
async def test_middle_judgment_replay_preserves_known_costs_and_unknown_final_flags(final_code):
    from dataclasses import replace
    class CaptureBeforeJudgment(CandidateResearchService):
        before = None
        async def _judgment(self, run, *args):
            self.before = replace(run, usage=run.usage.copy(), judgment_refs=list(run.judgment_refs),
                                  action_refs=list(run.action_refs), observation_refs=list(run.observation_refs))
            return await super()._judgment(run, *args)
    request, model, maps, repo, service = setup(width=2, service_class=CaptureBeforeJudgment)
    maps.responses.extend([search_success('one'), search_success('two')])
    model.responses.extend([lambda _: structured(_action_batch_from_prompt(model, 2)),
        structured(role_patch()), structured(role_patch()), failure(final_code)])
    result = await service.research_initial(request)
    assert result.failure.error.code.value == final_code
    saved = result.budgets
    before = service.before
    calls = (model.call_count, maps.call_count, len(repo.put_requests))
    slot = request.operation_plan.iteration_slots[0]
    replay = await CandidateResearchService._judgment(service, before, request, slot, 0)
    assert replay.error.code.value == final_code
    current = await service._state_budget(before)
    assert current.budgets.semantic_invocations_consumed == saved.semantic_invocations_consumed
    assert current.budgets.known_provider_attempts == saved.known_provider_attempts
    assert current.budgets.known_usage.total_tokens == saved.known_usage.total_tokens == 6
    assert current.budgets.usage_complete == saved.usage_complete == (final_code == 'input_too_large')
    assert current.budgets.provider_attempts_complete == saved.provider_attempts_complete
    assert before.output_recoveries_used == 2
    assert calls == (model.call_count, maps.call_count, len(repo.put_requests))
    # Re-reading the already remembered operation cannot double-count it.
    assert await CandidateResearchService._judgment(service, before, request, slot, 0) == replay
    assert (await service._state_budget(before)).budgets == current.budgets
    await service.aclose()


@pytest.mark.parametrize('accepted', [False, True])
async def test_twelfth_saved_action_replays_outcome_before_budget_guard(accepted):
    from dataclasses import replace
    from app.candidate_research import NoUsefulActionProposed
    class InterruptedTerminal(FakeRepository):
        async def put(self, request):
            if request.operation_id.logical_step == 'terminal':
                raise asyncio.CancelledError()
            return await super().put(request)
    class NearlySpent(CandidateResearchService):
        async def _load_or_create_run(self, *args):
            run = await super()._load_or_create_run(*args)
            run.semantic_invocations = 10
            self.before = replace(run, usage=run.usage.copy(), action_refs=[], judgment_refs=[], observation_refs=[])
            return run
    request, model, maps, repo, service = setup(service_class=NearlySpent, repository=InterruptedTerminal())
    # Legitimate no-useful-action proof without a map side effect.
    service._budget.place_limit = 0
    model.responses.extend([failure(), structured(NoUsefulActionProposed(reason_code='insufficient_remaining_budget')) if accepted else failure()])
    with pytest.raises(asyncio.CancelledError):
        await service.research_initial(request)
    run = service._runs[request.operation_plan.run_id]
    action_ref = repo.refs[request.operation_plan.iteration_slots[0].action_operation_id]
    artifact = (await repo.get(action_ref, expected_model=CandidateResearchModelActionArtifact)).content
    assert artifact.invocation_index == 11 and run.semantic_invocations == 12
    # Stop only at finalization so the already durable Action can be exercised.
    async def failed(*args):
        return args[2]
    service._failed = failed
    replayed = await service._iteration(service.before, request, request.operation_plan.iteration_slots[0], 0, service._objectives(service.before, request))
    if accepted:
        assert replayed is None
    else:
        assert replayed.error.code is LLMErrorCode.STRUCTURED_OUTPUT_INVALID
    assert model.call_count == 2 and maps.call_count == 0
    await service.aclose()


@pytest.mark.parametrize('case', ['json', 'length', 'ownership'])
async def test_uncertain_technical_retry_never_enters_output_recovery(case):
    from types import SimpleNamespace
    from app.llm_runtime import DeepSeekRuntime
    from app.llm_runtime.protocols import ProviderFailure
    from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper
    from tests.unit.llm_runtime.test_structured_runtime import make_response
    class Provider(ScriptedProviderAdapter):
        async def ainvoke_json(self, request):
            self.json_requests.append(request)
            self.call_count += 1
            if case == 'ownership' and self.call_count == 1:
                batch = _action_batch_from_prompt(SimpleNamespace(prompts=[SimpleNamespace(user_text=request.user)]), 2)
                return make_response(json.dumps({'output': batch.model_dump(mode='json')}))
            if self.call_count == (2 if case == 'ownership' else 1):
                return ProviderFailure(code='timeout', retryable=True, provider_status_code=None, retry_after_seconds=None)
            assert self.call_count == (3 if case == 'ownership' else 2), 'must not issue a recovery'
            return make_response(role_patch().model_dump_json() if case == 'ownership' else '{',
                                 finish='length' if case == 'length' else 'stop')
    request, _, maps, repo, service = setup(width=2)
    provider = Provider([])
    runtime = DeepSeekRuntime(provider=provider, sleeper=RecordingSleeper())
    service._runtime = runtime
    maps.responses.extend([search_success('one'), search_success('two')])
    result = await service.research_initial(request)
    assert result.outcome_type == 'failed'
    assert service._runs[request.operation_plan.run_id].output_recoveries_used == 0
    assert provider.call_count == (3 if case == 'ownership' else 2)
    assert maps.call_count == (2 if case == 'ownership' else 0)
    if case != 'ownership':
        assert result.failure.error.possible_duplicate
    else:
        assert result.failure.code.value == 'judgment_patch_invalid'
    await service.aclose()
    await runtime.aclose()


@pytest.mark.parametrize('admitted', [False, True])
async def test_real_m02_cancelled_recovery_accounts_only_admitted_invocation(admitted):
    from app.llm_runtime import DeepSeekRuntime
    from tests.unit.llm_runtime.fakes import ScriptedProviderAdapter, RecordingSleeper
    from tests.unit.llm_runtime.test_structured_runtime import make_response
    class CancellingProvider(ScriptedProviderAdapter):
        async def ainvoke_json(self, request):
            self.call_count += 1
            if self.call_count == 1:
                return make_response('{')
            raise asyncio.CancelledError()
    class BeforeAdmission:
        def __init__(self, operation):
            self.operation = operation
        @property
        def semantic_invocations_consumed(self):
            return self.operation.semantic_invocations_consumed
        async def ainvoke_structured(self, prompt, schema):
            if not admitted and self.semantic_invocations_consumed == 1:
                raise asyncio.CancelledError()
            return await self.operation.ainvoke_structured(prompt, schema)
        def close(self):
            self.operation.close()
    request, _, maps, repo, service = setup()
    provider = CancellingProvider([])
    runtime = DeepSeekRuntime(provider=provider, sleeper=RecordingSleeper())
    operation = runtime.start_operation(ModelRole.CANDIDATE_RESEARCH)
    service._runtime = FakeRuntime(BeforeAdmission(operation))
    with pytest.raises(asyncio.CancelledError):
        await service.research_initial(request)
    run = service._runs[request.operation_plan.run_id]
    assert run.semantic_invocations == operation.semantic_invocations_consumed == (2 if admitted else 1)
    assert provider.call_count == (2 if admitted else 1) and maps.call_count == 0
    assert not run.usage_complete and not run.provider_attempts_complete
    replay = await service.research_initial(request)
    assert replay.failure.code.value == 'operation_result_indeterminate'
    assert replay.budgets.semantic_invocations_consumed == (2 if admitted else 1)
    assert provider.call_count == (2 if admitted else 1)
    await service.aclose()
    await runtime.aclose()


async def test_save_failure_after_valid_recovery_keeps_consumption_and_issues_no_maps():
    class CannotSave(FakeRepository):
        async def put(self, request):
            if request.artifact_type.endswith('model_action'):
                raise ArtifactRepositoryError()
            return await super().put(request)
    request, model, maps, repo, service = setup(repository=CannotSave())
    model.responses.extend([failure(), action(model)])
    result = await service.research_initial(request)
    assert result.outcome_type == 'failed' and result.budgets.semantic_invocations_consumed == 2
    assert not result.budgets.usage_complete and result.budgets.known_usage.total_tokens == 2
    assert model.call_count == 2 and maps.call_count == 0
    await service.aclose()


@pytest.mark.parametrize('stage', ['action', 'judgment'])
async def test_recovered_artifact_strict_roundtrip_legacy_rejection_and_index_validation(stage):
    request, model, maps, repo, service = setup()
    model.responses.extend([failure(), action(model), structured(empty_patch())] if stage == 'action'
        else [action(model), failure(), structured(empty_patch())])
    maps.responses.append(search_no_result())
    await service.research_initial(request)
    artifact_model = CandidateResearchModelActionArtifact if stage == 'action' else CandidateResearchModelJudgmentArtifact
    content = next(w.content for w in repo.put_requests if w.artifact_type.endswith(f'model_{stage}'))
    artifact = artifact_model.model_validate_json(json.dumps(content), strict=True)
    assert artifact_model.model_validate_json(artifact.model_dump_json(), strict=True) == artifact
    for mutation in ('legacy', 'index', 'number', 'metadata'):
        invalid = json.loads(artifact.model_dump_json())
        if mutation == 'legacy':
            invalid['schema_version'] = '1.0'
        elif mutation == 'index':
            invalid['invocation_index'] += 1
        elif mutation == 'number':
            invalid['result']['attempts'][0]['recovery_number'] = 3
        else:
            invalid['result']['attempts'][0]['metadata'] = invalid['result']['final']['metadata']
        with pytest.raises(ValidationError):
            artifact_model.model_validate_json(json.dumps(invalid), strict=True)
    # Original normal record layout and hash input are unchanged.
    normal = json.loads(artifact.model_dump_json())
    normal['result'] = normal['result']['final']
    assert artifact_model.model_validate_json(json.dumps(normal), strict=True).model_dump(mode='json') == normal
    await service.aclose()
