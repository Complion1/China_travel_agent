import json
from uuid import uuid4

import pytest
from tests.unit.candidate_research.test_qualification import legal_judgment
from pydantic import ValidationError

from app.candidate_research import (
    CandidateResearchService, CandidateResearchArtifact, CandidateResearchLegacyArtifact,
    ProposedActionBatch, NoUsefulActionProposed, JudgmentPatch,
    MatchVerdict, RequirementReasonCode, RequirementGroupJudgmentUpdate, ResearchObjectiveKind,
    RequirementVerdictException, EvidenceHandle,
)
from app.candidate_research.correction import target_fingerprint, evidence_fingerprint, record_rejection
from app.candidate_research.policy import ResearchObjective
from tests.unit.candidate_research.test_service_initial import _request, _first_objective_action, _action_batch_from_prompt
from tests.support.candidate_research_fakes import (
    FakeRuntime, FakeModelOperation, FakeMapAdapter, FakeRepository, FakeBudgetManager, structured, search_no_result,
)


@pytest.mark.parametrize('second', ['no_useful', 'wrong_mode'])
async def test_actual_feedback_first_item_schema_bounded_correction_and_exact_replay(second):
    request, hotel_ref, hotel, _ = _request(uuid4())
    model = FakeModelOperation()
    def rejected(_):
        if second == 'no_useful':
            return structured(NoUsefulActionProposed(reason_code='prior_no_progress'))
        action = _first_objective_action(model)
        return structured(ProposedActionBatch(actions=(action.model_copy(update={'interest': request.confirmed.requirement.preferences.interests[0]}),)))
    model.responses.extend((lambda _: structured(ProposedActionBatch(actions=(
        _action_batch_from_prompt(model, 2).actions[1].model_copy(update={'ordinal': 0}),))), rejected))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel)
    maps = FakeMapAdapter()
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps,
        artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    result = await service.research_initial(request)
    assert result.failure.code.value == 'action_correction_exhausted'
    assert model.call_count == 2 and maps.call_count == 0
    assert result.budgets.semantic_invocations_consumed == 2 and result.budgets.known_usage.total_tokens == 4
    first, second_prompt = (json.loads(p.user_text) for p in model.prompts)
    assert len(second_prompt['objectives']) == 1
    assert second_prompt['correction']['attempts'] == 1
    assert second_prompt['correction']['rule'] == 'highest_priority_objective_ignored'
    assert second_prompt['required_objective_id'] == first['objectives'][0]['objective_id']
    from app.llm_runtime.sizing import validate_consumer_schema
    assert not validate_consumer_schema(model.schemas[0])
    schema = model.schemas[0].model_json_schema()
    assert 'outcome_type' in schema['$defs']['ProposedActionBatch']['required']
    assert 'outcome_type' in schema['$defs']['NoUsefulActionProposed']['required']
    assert 'action_type' in schema['$defs']['ProposedSearchAction']['required']
    assert 'action_type' in schema['$defs']['ProposedDetailsAction']['required']
    array = schema['$defs']['ProposedActionBatch']['properties']['actions']
    assert array['maxItems'] == 8
    assert array['prefixItems'][0]['allOf'][1]['properties']['objective_id']['const'] == first['objectives'][0]['objective_id']
    artifact = (await repository.get(repository.refs[request.operation_plan.terminal_successor_operation_id], expected_model=CandidateResearchArtifact)).content
    assert artifact.correction_state[0].attempts == 2
    assert CandidateResearchArtifact.model_validate_json(artifact.model_dump_json(), strict=True) == artifact
    before = (model.call_count, maps.call_count, len(repository.put_requests))
    assert await service.research_initial(request) == result
    assert before == (model.call_count, maps.call_count, len(repository.put_requests))
    await service.aclose()


@pytest.mark.parametrize('missing', ['outcome', 'first_action', 'later_action', 'no_useful'])
def test_generated_action_schema_rejects_missing_union_tags_like_local_decoder(missing):
    from copy import deepcopy
    from jsonschema import Draft202012Validator
    from app.candidate_research.correction import bound_action_schema
    from app.candidate_research.service import _ActionModelEnvelope
    original = _ActionModelEnvelope.model_json_schema()
    saved = deepcopy(original)
    projected = bound_action_schema(_ActionModelEnvelope, None, (), ())
    schema = projected.model_json_schema()
    Draft202012Validator.check_schema(schema)
    actions = [{'action_type': 'search_places', 'ordinal': ordinal, 'objective_id': 'objective:test',
                'query': '测试景点', 'area_mode': 'city'} for ordinal in range(2)]
    valid = {'output': {'outcome_type': 'proposed_actions', 'actions': actions}}
    if missing == 'no_useful':
        valid = {'output': {'outcome_type': 'no_useful_action', 'reason_code': 'no_legal_action'}}
    Draft202012Validator(schema).validate(valid)
    projected.model_validate_json(json.dumps(valid), strict=True)
    invalid = deepcopy(valid)
    if missing in {'outcome', 'no_useful'}:
        invalid['output'].pop('outcome_type')
    else:
        invalid['output']['actions'][missing == 'later_action'].pop('action_type')
    # The original schema permits a response which its own strict decoder rejects.
    assert Draft202012Validator(original).is_valid(invalid)
    with pytest.raises(ValidationError):
        _ActionModelEnvelope.model_validate_json(json.dumps(invalid), strict=True)
    assert not Draft202012Validator(schema).is_valid(invalid)
    with pytest.raises(ValidationError):
        projected.model_validate_json(json.dumps(invalid), strict=True)
    assert _ActionModelEnvelope.model_json_schema() == saved


def test_discriminator_projection_derives_field_names_and_nested_branches():
    from typing import Annotated, Literal
    from pydantic import BaseModel, Field
    from jsonschema import Draft202012Validator
    from app.candidate_research.correction import _require_union_discriminators

    class Left(BaseModel):
        selector: Literal['left'] = 'left'
        count: int

    class Right(BaseModel):
        selector: Literal['right'] = 'right'
        flag: bool

    class Envelope(BaseModel):
        choices: list[Annotated[Left | Right, Field(discriminator='selector')]]

    original = Envelope.model_json_schema()
    schema = _require_union_discriminators(original)
    valid = {'choices': [{'selector': 'left', 'count': 1}, {'selector': 'right', 'flag': True}]}
    Draft202012Validator(schema).validate(valid)
    invalid = {'choices': [{'count': 1}, {'selector': 'right', 'flag': True}]}
    assert Draft202012Validator(original).is_valid(invalid)
    assert not Draft202012Validator(schema).is_valid(invalid)
    assert _require_union_discriminators(schema) == schema
    assert 'selector' not in original['$defs']['Left']['required']


@pytest.mark.parametrize('branch', ['left', 'right', 'other'])
def test_nested_union_branch_keeps_both_discriminators_required(branch):
    from typing import Annotated, Literal
    from pydantic import BaseModel, ConfigDict, Field
    from jsonschema import Draft202012Validator
    from app.candidate_research.correction import _require_union_discriminators

    class Strict(BaseModel):
        model_config = ConfigDict(strict=True, extra='forbid')

    class Left(Strict):
        family: Literal['a'] = 'a'
        selector: Literal['left'] = 'left'
        count: int

    class Right(Strict):
        family: Literal['a'] = 'a'
        selector: Literal['right'] = 'right'
        flag: bool

    class Other(Strict):
        family: Literal['b'] = 'b'
        text: str

    class Envelope(Strict):
        choice: Annotated[Annotated[Left | Right, Field(discriminator='selector')] | Other,
                          Field(discriminator='family')]

    choices = {'left': {'family': 'a', 'selector': 'left', 'count': 1},
               'right': {'family': 'a', 'selector': 'right', 'flag': True},
               'other': {'family': 'b', 'text': 'synthetic'}}
    valid = {'choice': choices[branch]}
    original = Envelope.model_json_schema()
    schema = _require_union_discriminators(original)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(valid)
    Envelope.model_validate_json(json.dumps(valid), strict=True)
    for field in ('family', 'selector') if branch != 'other' else ('family',):
        invalid = {'choice': {key: value for key, value in choices[branch].items() if key != field}}
        assert Draft202012Validator(original).is_valid(invalid)
        assert not Draft202012Validator(schema).is_valid(invalid)
        with pytest.raises(ValidationError):
            Envelope.model_validate_json(json.dumps(invalid), strict=True)
    assert _require_union_discriminators(schema) == schema


async def test_corrected_batch_uses_existing_slot_and_controller_binds_confirmed_name():
    request, hotel_ref, hotel, _ = _request(uuid4())
    request.confirmed.requirement.preferences.must_visit_places = ['明确的博物馆']
    model = FakeModelOperation([
        structured(NoUsefulActionProposed(reason_code='prior_no_progress')),
        lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model, query='泛化景点'),))),
        lambda _: legal_judgment(model),
        structured(NoUsefulActionProposed(reason_code='insufficient_remaining_budget')),
    ])
    repository = FakeRepository(); repository.seed(hotel_ref, hotel)
    maps = FakeMapAdapter([search_no_result()])
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps,
        artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id, place_limit=1))
    result = await service.research_initial(request)
    # Valid correction executes once; it cannot erase the prior correction record.
    assert maps.call_count == 1
    actions = [p.content for p in repository.put_requests if p.artifact_type.endswith('model_action')]
    accepted = next(a for a in actions if a['result']['record_type'] == 'accepted')
    assert accepted['result']['output']['actions'][0]['query'] == '明确的博物馆'
    assert result.budgets.iterations_consumed <= 6 and result.budgets.semantic_invocations_consumed <= 12
    assert result.budgets.known_usage.total_tokens == 2 * model.call_count
    assert result.outcome_type == 'incomplete', getattr(result, 'failure', None)
    from app.candidate_research import CandidateCountShortfallGap, CandidateResearchSupplementalRequest, CandidateResearchOperationPlan, ResearchRole
    from app.workflow_contracts import OperationId, WorkflowPhase
    from tests.unit.candidate_research.test_service_supplemental import _slots
    predecessor = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    assert predecessor.correction_state[0].attempts == 1 and not predecessor.correction_state[0].active
    model.responses.append(structured(NoUsefulActionProposed(reason_code='insufficient_remaining_budget')))
    plan = CandidateResearchOperationPlan(run_id=request.operation_plan.run_id,
        iteration_slots=_slots(request.operation_plan.run_id, count=6-result.budgets.iterations_consumed, prefix='correction_supp'),
        terminal_successor_operation_id=OperationId(run_id=request.operation_plan.run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step='correction_supp_terminal'))
    supplemental = CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=result.candidate_set_ref,
        operation_plan=plan, gaps=(CandidateCountShortfallGap(gap_id='new-count-id', evidence_refs=(result.candidate_set_ref,),
            role=ResearchRole.ATTRACTION, current_count=0, target_count=1),))
    resumed = await service.research_supplemental(supplemental)
    assert resumed.outcome_type == 'incomplete', getattr(resumed, 'failure', None)
    successor = (await repository.get(resumed.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    assert successor.correction_state == predecessor.correction_state
    assert maps.call_count == 1
    await service.aclose()


def test_renamed_targets_and_unrelated_pool_growth_do_not_reset_rejections():
    from tests.unit.itinerary_planning.test_service_initial import _upstream
    state = _upstream(uuid4())[3].record.state
    objective = ResearchObjective(objective_id='old', kind=ResearchObjectiveKind.MUST_VISIT_RESOLUTION, gap_id=None,
        requirement_group_id='g', role=None, interest=None, meal_intent=None, anchor=None, radius_km=None, candidate_uids=())
    # Parse enum values through JSON, just as the actual prompt schema does.
    objective = ResearchObjective.model_validate_json(objective.model_dump_json(), strict=True)
    renamed = objective.model_copy(update={'objective_id':'new', 'candidate_uids':('candidate-2',)})
    assert target_fingerprint(objective) == target_fingerprint(renamed)
    empty = state.model_copy(update={'candidates':()})
    assert evidence_fingerprint(empty, objective) == evidence_fingerprint(state, renamed)
    one = record_rejection((), empty, objective, (), '城', 'highest_priority_objective_ignored')
    two = record_rejection(one, state, renamed, (), '城', 'no_useful_action_invalid')
    assert len(two) == 1 and two[0].attempts == 2


async def test_missing_requirement_context_fails_before_budget_or_model_invocation():
    class MissingContext(CandidateResearchService):
        async def _load_or_create_run(self, *args):
            run = await super()._load_or_create_run(*args)
            run.requirement_context = ()
            return run
    request, hotel_ref, hotel, _ = _request(uuid4())
    request.confirmed.requirement.preferences.must_visit_places = ['某展馆']
    model, maps, repository = FakeModelOperation(), FakeMapAdapter(), FakeRepository()
    repository.seed(hotel_ref, hotel)
    budget = FakeBudgetManager(request.operation_plan.run_id)
    service = MissingContext(runtime=FakeRuntime(model), map_adapter=maps, artifact_repository=repository, budget_manager=budget)
    result = await service.research_initial(request)
    assert result.failure.code.value == 'context_contract_error'
    assert (model.call_count, maps.call_count, budget.consume_count) == (0,0,0)
    await service.aclose()


async def test_complete_with_unrelated_conflicted_avoid_unknown_and_preserve_legacy_gate():
    from tests.support.demo_workflow_fakes import research_boundaries
    from tests.unit.candidate_research.test_service_initial import _multi_place_search
    request, hotel_ref, hotel, _ = _request(uuid4(), place_width=8)
    request.confirmed.requirement.preferences.avoid_places = ['待避让的地点']
    model, maps = research_boundaries()
    original = model.base_patch
    def judgment(_):
        group = json.loads(model.prompts[-1].user_text)['requirements'][0]
        update = RequirementGroupJudgmentUpdate(requirement_group_id=group['requirement_group_id'], requirement_kind='avoid',
            evaluated_candidate_handles=tuple(f'c:{i}' for i in range(11)), default_verdict=MatchVerdict.UNRESOLVED,
            default_reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT, default_evidence_handles=(EvidenceHandle(handle_id='e:4'),),
            exceptions=tuple(RequirementVerdictException(candidate_handle=f'c:{i}', verdict=MatchVerdict.NO_MATCH,
                reason_code=RequirementReasonCode.CANDIDATE_OUT_OF_SCOPE, evidence_handles=(EvidenceHandle(handle_id=f'e:{0 if i<6 else 1}'),)) for i in range(10)))
        return legal_judgment(model, original.model_copy(update={'requirement_group_updates':(update,)}))
    model.responses[1] = judgment
    maps.responses[4] = _multi_place_search(('a0','x0'), category='museum')
    clash = _multi_place_search(('x0',), category='museum')
    maps.responses[6] = clash.model_copy(update={'places':(clash.places[0].model_copy(update={'address':'different'}),)})
    repository = FakeRepository(); repository.seed(hotel_ref, hotel)
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps,
        artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    result = await service.research_initial(request)
    assert result.outcome_type == 'completed', getattr(result,'failure',None)
    artifact = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    extra = artifact.record.state.candidates[-1]
    assert extra.state.value == 'conflicted' and extra.avoid_matches[0].verdict is MatchVerdict.UNRESOLVED
    assert model.call_count == 4
    data = artifact.model_dump(mode='json'); data['schema_version'] = '2.0'; data.pop('correction_state'); data.pop('qualification_state')
    for ref in (*data['record']['state']['model_action_refs'], *data['record']['state']['model_judgment_refs']): ref['schema_version'] = '1.0'
    with pytest.raises(ValidationError, match='legacy completion'):
        CandidateResearchLegacyArtifact.model_validate_json(json.dumps(data), strict=True)
    await service.aclose()


@pytest.mark.parametrize('reason', ['no_legal_action', 'insufficient_remaining_budget'])
@pytest.mark.parametrize('kind', ['must_visit_resolution', 'avoid_resolution', 'supplemental_gap'])
async def test_remaining_requirement_or_supplement_cannot_falsely_claim_no_action(kind, reason):
    from types import SimpleNamespace
    from tests.unit.itinerary_planning.test_service_initial import _upstream
    run_id = uuid4()
    state = _upstream(run_id)[3].record.state
    objective = ResearchObjective.model_validate_json(json.dumps(dict(objective_id='required', kind=kind,
        gap_id='gap' if kind == 'supplemental_gap' else None, gap_type='must_visit_resolution_missing' if kind == 'supplemental_gap' else None,
        requirement_group_id='g', role=None, interest=None, meal_intent=None, anchor=None, radius_km=None, candidate_uids=[])), strict=True)
    run = SimpleNamespace(run_id=run_id, state=state, named_searches=set())
    service = CandidateResearchService(runtime=FakeRuntime(), map_adapter=FakeMapAdapter(), artifact_repository=FakeRepository(), budget_manager=FakeBudgetManager(run_id))
    assert not await service._no_useful_action_is_legal(run, NoUsefulActionProposed(reason_code=reason), (objective,))
    assert service._allowed_objective_actions(run, objective) == ('search_places',)
    await service.aclose()


async def test_terminal_lookup_failure_preserves_actual_usage_and_strict_outcome():
    from pydantic import TypeAdapter
    from app.candidate_research import CandidateResearchOutcome
    from app.workflow_contracts import ArtifactRepositoryError
    class LookupFailure(FakeRepository):
        unavailable = False
        async def lookup(self, **kwargs):
            if self.unavailable:
                raise ArtifactRepositoryError()
            return await super().lookup(**kwargs)
    request, hotel_ref, hotel, _ = _request(uuid4())
    model = FakeModelOperation([structured(NoUsefulActionProposed(reason_code='no_legal_action'))] * 2)
    repository = LookupFailure(); repository.seed(hotel_ref, hotel)
    maps = FakeMapAdapter()
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps, artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    original = await service.research_initial(request)
    assert original.budgets.semantic_invocations_consumed == 2
    repository.unavailable = True
    before = (model.call_count, maps.call_count, len(repository.put_requests))
    failed = await service.research_initial(request)
    assert failed.failure.code.value == 'artifact_error'
    assert failed.budgets == original.budgets
    assert failed.last_consistent_candidate_set_ref == repository.refs[request.operation_plan.terminal_successor_operation_id]
    assert TypeAdapter(CandidateResearchOutcome).validate_json(failed.model_dump_json(), strict=True) == failed
    assert before == (model.call_count, maps.call_count, len(repository.put_requests))
    repository.unavailable = False
    assert await service.research_initial(request) == original
    await service.aclose()


async def test_identical_facts_stop_after_two_stagnant_rounds_with_exact_replay():
    from tests.support.candidate_research_fakes import search_success
    request, hotel_ref, hotel, _ = _request(uuid4())
    request.confirmed.requirement.preferences.must_visit_places = ['固定目标']
    model = FakeModelOperation()
    for index in range(6):
        model.responses.append(lambda _, index=index: structured(ProposedActionBatch(actions=(_first_objective_action(model, query=f'query-{index}'),))))
        if index == 0:
            model.responses.append(lambda _: legal_judgment(model))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel)
    maps = FakeMapAdapter([search_success('same')] * 6)
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps, artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    result = await service.research_initial(request)
    assert result.outcome_type == 'incomplete', getattr(result, 'failure', None)
    assert model.call_count == 4 and maps.call_count == 3
    assert 'no_progress' in {reason.value for reason in result.stop_reasons}
    before = (model.call_count, maps.call_count, len(repository.put_requests))
    assert await service.research_initial(request) == result
    assert before == (model.call_count, maps.call_count, len(repository.put_requests))
    await service.aclose()


def test_same_requirement_target_identity_survives_supplement_and_renaming():
    from tests.unit.itinerary_planning.test_service_initial import _upstream
    state = _upstream(uuid4())[3].record.state
    initial = ResearchObjective(objective_id='first', kind=ResearchObjectiveKind.MUST_VISIT_RESOLUTION,
        gap_id=None, requirement_group_id='g', role=None, interest=None, meal_intent=None, anchor=None, radius_km=None, candidate_uids=())
    supplement = initial.model_copy(update={'kind':ResearchObjectiveKind.SUPPLEMENTAL_GAP, 'objective_id':'new', 'gap_id':'new-gap',
        'gap_type':'must_visit_resolution_missing', 'candidate_uids':('changed-uid',)})
    assert target_fingerprint(initial) == target_fingerprint(supplement)
    one = record_rejection((), state, initial, (), '城', 'no_useful_action_invalid')
    two = record_rejection(one, state, supplement, (), '城', 'no_useful_action_invalid')
    assert len(two) == 1 and two[0].attempts == 2


def test_first_resolution_on_old_evidence_counts_but_verdict_oscillation_does_not():
    from tests.unit.itinerary_planning.test_service_initial import _upstream
    state = _upstream(uuid4())[3].record.state
    candidate = state.candidates[0]
    objective = ResearchObjective(objective_id='interest', kind=ResearchObjectiveKind.INTEREST_COVERAGE, gap_id=None,
        requirement_group_id=None, role=None, interest=candidate.interests[0], meal_intent=None, anchor=None, radius_km=None, candidate_uids=())
    before = state.model_copy(update={'candidates':(candidate.model_copy(update={'interests':()}),)})
    after = state.model_copy(update={'candidates':(candidate,)})
    seen = set()
    assert CandidateResearchService._state_progressed(before, after, objective, {}, seen)
    assert not CandidateResearchService._state_progressed(after, before, objective, {}, seen)
    assert not CandidateResearchService._state_progressed(before, after, objective, {}, seen)


@pytest.mark.parametrize('correct', [False, True])
async def test_new_named_requirement_cannot_be_replaced_by_other_requirement_details(correct):
    from app.candidate_research import ProposedDetailsAction, RoleJudgmentUpdate, ResearchRole, RoleVerdict, RoleReasonCode
    from tests.support.candidate_research_fakes import search_success
    request, hotel_ref, hotel, _ = _request(uuid4())
    request.confirmed.requirement.preferences.must_visit_places = ['甲博物馆', '乙展馆']
    model = FakeModelOperation()
    def resolve_first(_):
        first = json.loads(model.prompts[-1].user_text)['requirements'][0]
        return legal_judgment(model, JudgmentPatch(role_updates=(RoleJudgmentUpdate(candidate_handle='c:0', role=ResearchRole.ATTRACTION,
            verdict=RoleVerdict.SUPPORTED, reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS, evidence_handles=(EvidenceHandle(handle_id='e:0'),)),),
            interest_updates=(), meal_updates=(), requirement_group_updates=(RequirementGroupJudgmentUpdate(
                requirement_group_id=first['requirement_group_id'], requirement_kind='must_visit', evaluated_candidate_handles=('c:0',),
                default_verdict=MatchVerdict.CONFIRMED, default_reason_code=RequirementReasonCode.EXACT_NAME_MATCH,
                default_evidence_handles=(EvidenceHandle(handle_id='e:0'),), exceptions=()),)))
    def wrong_details(_):
        objective = json.loads(model.prompts[-1].user_text)['objectives'][0]
        assert objective['allowed_actions'] == ['search_places'] and not objective['detail_uids']
        schema = model.schemas[-1].model_json_schema()
        assert schema['$defs']['ProposedActionBatch']['properties']['actions']['prefixItems'][0]['allOf'][1]['properties']['action_type']['enum'] == ['search_places']
        return structured(ProposedActionBatch(actions=(ProposedDetailsAction(ordinal=0, objective_id=objective['objective_id'], uid='first-only'),)))
    model.responses.extend((lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model),))), resolve_first, wrong_details))
    if correct:
        model.responses.extend((lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model, query='generic'),))),
            lambda _: legal_judgment(model)))
    else:
        model.responses.append(wrong_details)
    repository = FakeRepository(); repository.seed(hotel_ref, hotel)
    maps = FakeMapAdapter([search_success('first-only'), search_no_result()])
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps, artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    result = await service.research_initial(request)
    assert not maps.details_requests
    if correct:
        assert maps.call_count == 2 and maps.search_requests[-1].query == '乙展馆'
        assert result.outcome_type == 'incomplete', getattr(result, 'failure', None)
    else:
        assert maps.call_count == 1
        assert result.failure.code.value == 'action_correction_exhausted'
    await service.aclose()


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('forged', [None, 'group', 'items', 'kind'])
async def test_same_process_must_supplement_preserves_rejection_limit(empty, forged):
    from app.candidate_research import MustVisitResolutionMissingGap, CandidateResearchSupplementalRequest, CandidateResearchOperationPlan
    from app.workflow_contracts import OperationId, WorkflowPhase
    from tests.unit.candidate_research.test_service_supplemental import _slots
    from tests.support.candidate_research_fakes import search_success
    request, hotel_ref, hotel, _ = _request(uuid4())
    request.confirmed.requirement.preferences.must_visit_places = ['同一个需求']
    request.confirmed.requirement.preferences.avoid_places = ['避让目标']
    model = FakeModelOperation()
    model.responses.extend((lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model),))),
        lambda _: legal_judgment(model),
        structured(NoUsefulActionProposed(reason_code='no_legal_action')),
        lambda _: structured(ProposedActionBatch(actions=(_first_objective_action(model, query='同一个需求'),)))))
    if empty:
        model.responses = [model.responses[2], *model.responses[:2]]
    repository = FakeRepository(); repository.seed(hotel_ref, hotel)
    maps = FakeMapAdapter([search_no_result() if empty else search_success('same')])
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps, artifact_repository=repository, budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    initial = await service.research_initial(request)
    assert initial.outcome_type == 'incomplete', getattr(initial, 'failure', None)
    artifact = (await repository.get(initial.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    summary = next(g for g in artifact.record.gaps if g.gap_type == 'must_visit_resolution_missing')
    group_id, item_ids = summary.requirement_group_id, summary.requirement_item_ids
    if forged == 'group':
        group_id = 'forged-group'
    elif forged == 'items':
        item_ids = ('forged-item',)
    elif forged == 'kind':
        _, group_id, item_ids = next(spec for spec in service._runs[request.operation_plan.run_id].requirement_specs if spec[0] == 'avoid')
    gap = MustVisitResolutionMissingGap(gap_id='renamed-gap', requirement_group_id=group_id,
        requirement_item_ids=item_ids, evidence_refs=tuple(artifact.record.state.observation_refs[i] for i in summary.evidence_indexes) or (initial.candidate_set_ref,))
    supplemental = CandidateResearchSupplementalRequest(schema_version='2.0', predecessor_candidate_set_ref=initial.candidate_set_ref, gaps=(gap,),
        operation_plan=CandidateResearchOperationPlan(run_id=request.operation_plan.run_id,
            iteration_slots=_slots(request.operation_plan.run_id, count=6-initial.budgets.iterations_consumed, prefix='same_must'),
            terminal_successor_operation_id=OperationId(run_id=request.operation_plan.run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step='same_must_terminal')))
    model.responses.append(structured(NoUsefulActionProposed(reason_code='no_legal_action')))
    result = await service.research_supplemental(supplemental)
    if forged:
        assert result.failure.code.value == 'replay_mismatch'
        assert model.call_count == (3 if empty else 4) and maps.call_count == 1
        await service.aclose()
        return
    assert result.failure.code.value == 'action_correction_exhausted'
    assert model.call_count == (4 if empty else 5) and maps.call_count == 1
    terminal = (await repository.get(repository.refs[supplemental.operation_plan.terminal_successor_operation_id], expected_model=CandidateResearchArtifact)).content
    assert len(terminal.correction_state) == 1 and terminal.correction_state[0].attempts == 2
    await service.aclose()


@pytest.mark.parametrize('place_limit', [1, 2])
async def test_every_group_first_search_in_batch_is_bound_and_only_admitted_groups_recorded(place_limit):
    from app.candidate_research import ProposedSearchAction, SearchAreaMode
    request, hotel_ref, hotel, _ = _request(uuid4(), place_width=2)
    request.confirmed.requirement.preferences.must_visit_places = ['甲博物馆', '乙展馆']
    model = FakeModelOperation()
    def batch(_):
        objectives = json.loads(model.prompts[-1].user_text)['objectives'][:2]
        return structured(ProposedActionBatch(actions=tuple(ProposedSearchAction(ordinal=i, objective_id=o['objective_id'],
            query=f'generic-{i}', area_mode=SearchAreaMode.CITY) for i,o in enumerate(objectives))))
    model.responses.extend((batch, lambda _: legal_judgment(model),
        structured(NoUsefulActionProposed(reason_code='insufficient_remaining_budget'))))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel)
    maps = FakeMapAdapter([search_no_result()] * place_limit)
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps, artifact_repository=repository,
        budget_manager=FakeBudgetManager(request.operation_plan.run_id, place_limit=place_limit))
    result = await service.research_initial(request)
    assert result.outcome_type == 'incomplete', getattr(result, 'failure', None)
    assert [item.query for item in maps.search_requests] == ['甲博物馆', '乙展馆'][:place_limit]
    objectives = json.loads(model.prompts[0].user_text)['objectives'][:place_limit]
    assert service._runs[request.operation_plan.run_id].named_searches == {o['requirement_group_id'] for o in objectives}
    before = (maps.call_count, model.call_count, len(repository.put_requests))
    assert await service.research_initial(request) == result
    assert before == (maps.call_count, model.call_count, len(repository.put_requests))
    await service.aclose()
