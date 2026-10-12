import json
from dataclasses import replace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.candidate_research.context import RequirementContext, project_requirements
from app.candidate_research import CandidateResearchService, NoUsefulActionProposed
from app.candidate_research.artifacts import CandidateResearchArtifact, CandidateResearchLegacyArtifact
from app.requirements import InterestTag
from tests.unit.candidate_research.test_service_initial import _request
from tests.support.candidate_research_fakes import FakeModelOperation, FakeRuntime, FakeRepository, FakeMapAdapter, FakeBudgetManager, structured


@pytest.mark.parametrize('fault', ['missing', 'wrong_id', 'empty_name', 'wrong_kind'])
def test_requirement_directory_rejects_incomplete_context(fault):
    item = RequirementContext('must_visit', 'g', ('i',), '某博物馆')
    spec = ((item.kind, item.group_id, item.item_ids),)
    data = {'missing': (), 'wrong_id': (replace(item, group_id='other'),),
            'empty_name': (replace(item, name=' '),), 'wrong_kind': (replace(item, kind='avoid'),)}[fault]
    with pytest.raises(ValueError):
        project_requirements(spec, data)


async def test_actual_action_context_keeps_multiple_names_and_duplicate_item_identity():
    run_id = uuid4()
    request, hotel_ref, hotel, _ = _request(run_id)
    preferences = request.confirmed.requirement.preferences
    preferences.interests = [InterestTag.CULTURE]
    preferences.must_visit_places = ['某博物馆', '另一博物馆', '某博物馆']
    preferences.avoid_places = ['某公园']
    operation = FakeModelOperation([structured(NoUsefulActionProposed(reason_code='insufficient_remaining_budget')),
                                    structured(NoUsefulActionProposed(reason_code='prior_no_progress'))])
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter(),
        artifact_repository=repository, budget_manager=FakeBudgetManager(run_id, place_limit=0))
    try:
        result = await service.research_initial(request)
        assert result.outcome_type == 'incomplete', getattr(result, 'failure', None)
        payload = json.loads(operation.prompts[0].user_text)
        directory = payload['requirements']
        assert [item['confirmed_name'] for item in directory] == ['某博物馆', '另一博物馆', '某公园']
        assert len(directory[0]['requirement_item_ids']) == 2
        assert len({item['requirement_group_id'] for item in directory}) == 3
        assert result.candidate_set_ref.schema_version == '4.0'
        artifact = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
        assert artifact.record.state.effective_interests == (InterestTag.CULTURE,)
        data = artifact.model_dump(mode='json')
        data.pop("correction_state")
        data.pop("qualification_state")
        for ref in data['record']['state']['model_action_refs']:
            ref['schema_version'] = '1.0'
        for legacy in ('1.0', '2.0'):
            data['schema_version'] = legacy
            with pytest.raises(ValidationError, match='legacy candidate sets'):
                CandidateResearchLegacyArtifact.model_validate_json(json.dumps(data), strict=True)
    finally:
        await service.aclose()
