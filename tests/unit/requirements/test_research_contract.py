import pytest
from app.requirements import (InterestTag, TravelPreferences, interest_selection_error,
    RequirementDomainState, RequirementConfirmation, NeedClarification, ConfirmedTravelRequirement)
from tests.unit.requirements.test_requirement_service import complete_requirement, make_service


@pytest.mark.parametrize('selected,decides,error', [
    ([InterestTag.CULTURE], False, None),
    (list(InterestTag), False, None),
    (None, True, None), ([], True, None),
    (None, False, 'interests_missing'), ([], False, 'interests_missing'),
    ([InterestTag.CULTURE], True, 'interest_selection_conflict'),
    ([InterestTag.CULTURE, InterestTag.CULTURE], False, 'interests_invalid'),
])
async def test_interest_policy_and_confirmation_agree(selected, decides, error):
    requirement = complete_requirement()
    requirement.preferences.interests = selected
    requirement.preferences.agent_decides_interests = decides
    assert interest_selection_error(requirement.preferences) == error
    state = RequirementDomainState(requirement=requirement, confirmation_status='awaiting_confirmation')
    service, extractor, sink = make_service()
    transition = await service.confirm(state, RequirementConfirmation(decision='accept', revision_text=None))
    assert isinstance(transition.outcome, NeedClarification if error else ConfirmedTravelRequirement)
    if error:
        assert error in {issue.code for issue in transition.outcome.issues}
    else:
        from app.itinerary_planning.service import _validate_supported_confirmed_requirement
        _validate_supported_confirmed_requirement(transition.outcome)
    assert transition.state.requirement.preferences.interests == selected
    assert not extractor.calls and not sink.received


async def test_missing_pace_cannot_bypass_confirmation_gate():
    requirement = complete_requirement()
    requirement.preferences.agent_decides_pace = False
    service, extractor, _ = make_service()
    result = await service.confirm(RequirementDomainState(requirement=requirement,
        confirmation_status='awaiting_confirmation'), RequirementConfirmation(decision='accept', revision_text=None))
    assert isinstance(result.outcome, NeedClarification)
    assert any(issue.code == 'pace_missing' for issue in result.outcome.issues)
    assert not extractor.calls


@pytest.mark.parametrize('previous,previous_auto,choose_agent', [
    ([InterestTag.CULTURE], True, False),
    ([InterestTag.CULTURE, InterestTag.CULTURE], False, False),
    ([InterestTag.CULTURE], True, True),
])
async def test_invalid_interest_group_is_repaired_by_evidenced_supplement(previous, previous_auto, choose_agent):
    from app.requirements import StrictFieldEvidenceDTO, RequirementFieldPath, RequirementIntent, NeedConfirmation
    from tests.unit.requirements.fakes import strict_dto
    from tests.unit.requirements.test_requirement_service import turn
    requirement = complete_requirement()
    requirement.preferences.interests = previous
    requirement.preferences.agent_decides_interests = previous_auto
    initial_service, _, _ = make_service()
    invalid = await initial_service.confirm(RequirementDomainState(requirement=requirement,
        confirmation_status='awaiting_confirmation'), RequirementConfirmation(decision='accept', revision_text=None))
    values = {'agent_decides_interests': True} if choose_agent else {'interests': [InterestTag.CULTURE]}
    field = RequirementFieldPath.AGENT_DECIDES_INTERESTS if choose_agent else RequirementFieldPath.INTERESTS
    message = '由Agent决定' if choose_agent else '人文'
    dto = strict_dto(**values, evidence=[StrictFieldEvidenceDTO(field_path=field, text=message)])
    service, extractor, _ = make_service(dto)
    fixed = await service.process_turn(turn(dto_message=message, state=invalid.state, intent=RequirementIntent.SUPPLEMENT))
    assert isinstance(fixed.outcome, NeedConfirmation), [issue.code for issue in fixed.state.issues]
    assert fixed.state.requirement.preferences.interests == (None if choose_agent else [InterestTag.CULTURE])
    assert fixed.state.requirement.preferences.agent_decides_interests is choose_agent
    assert not fixed.state.issues
    assert all(not meta.issues for path, meta in fixed.state.metadata.metadata.items()
               if path.value.startswith('preferences.'))
    confirmed = await service.confirm(fixed.state, RequirementConfirmation(decision='accept', revision_text=None))
    assert isinstance(confirmed.outcome, ConfirmedTravelRequirement)
    from app.itinerary_planning.service import _validate_supported_confirmed_requirement
    _validate_supported_confirmed_requirement(confirmed.outcome)
    assert len(extractor.calls) == 1


def test_valid_interest_cannot_be_changed_by_ordinary_supplement():
    from app.requirements import RequirementPatch
    from app.requirements.merge import RequirementMerger
    requirement = complete_requirement()
    requirement.preferences.interests = [InterestTag.CULTURE]
    requirement.preferences.agent_decides_interests = False
    result = RequirementMerger().merge(requirement, RequirementPatch(preferences=TravelPreferences(
        interests=[InterestTag.NATURE])), verified_paths=('preferences.interests',))
    assert result.requirement.preferences.interests == [InterestTag.CULTURE]
    assert any(issue.code == 'requirement_conflict' for issue in result.issues)


@pytest.mark.parametrize('choose_agent', [False, True])
async def test_conflicting_pace_can_be_repaired_without_silent_valid_value_changes(choose_agent):
    from app.requirements import TravelPace, StrictFieldEvidenceDTO, RequirementFieldPath, RequirementIntent, NeedConfirmation
    from tests.unit.requirements.fakes import strict_dto
    from tests.unit.requirements.test_requirement_service import turn
    requirement = complete_requirement()
    requirement.preferences.pace = TravelPace.NORMAL
    service, _, _ = make_service()
    invalid = await service.confirm(RequirementDomainState(requirement=requirement,
        confirmation_status='awaiting_confirmation'), RequirementConfirmation(decision='accept', revision_text=None))
    assert isinstance(invalid.outcome, NeedClarification)
    values = {'agent_decides_pace': True} if choose_agent else {'pace': TravelPace.RELAXED}
    field = RequirementFieldPath.AGENT_DECIDES_PACE if choose_agent else RequirementFieldPath.PACE
    message = '由Agent决定' if choose_agent else '轻松'
    service, _, _ = make_service(strict_dto(**values, evidence=[StrictFieldEvidenceDTO(field_path=field, text=message)]))
    fixed = await service.process_turn(turn(dto_message=message, state=invalid.state, intent=RequirementIntent.SUPPLEMENT))
    assert isinstance(fixed.outcome, NeedConfirmation)
    confirmed = await service.confirm(fixed.state, RequirementConfirmation(decision='accept', revision_text=None))
    assert isinstance(confirmed.outcome, ConfirmedTravelRequirement)
    from app.itinerary_planning.service import _validate_supported_confirmed_requirement
    _validate_supported_confirmed_requirement(confirmed.outcome)


async def test_group_repair_does_not_resurrect_metadata_conflict_on_later_turn():
    from app.requirements import StrictFieldEvidenceDTO, RequirementFieldPath, RequirementIntent, NeedConfirmation
    from tests.unit.requirements.fakes import strict_dto
    from tests.unit.requirements.test_requirement_service import turn
    req = complete_requirement()
    req.preferences.interests = [InterestTag.CULTURE]
    req.preferences.agent_decides_interests = False
    req.constraints.dietary_restrictions = None
    first = strict_dto(interests=[InterestTag.NATURE], agent_decides_interests=True, evidence=[
        StrictFieldEvidenceDTO(field_path=RequirementFieldPath.INTERESTS, text='自然'),
        StrictFieldEvidenceDTO(field_path=RequirementFieldPath.AGENT_DECIDES_INTERESTS, text='由Agent决定')])
    second = strict_dto(agent_decides_interests=True, evidence=[
        StrictFieldEvidenceDTO(field_path=RequirementFieldPath.AGENT_DECIDES_INTERESTS, text='由Agent决定')])
    third = strict_dto(dietary_restrictions='无', evidence=[
        StrictFieldEvidenceDTO(field_path=RequirementFieldPath.DIETARY_RESTRICTIONS, text='无')])
    service, _, _ = make_service(first, second, third)
    state = RequirementDomainState(requirement=req)
    for message in ('自然，由Agent决定', '由Agent决定', '饮食禁忌无'):
        transition = await service.process_turn(turn(dto_message=message, state=state, intent=RequirementIntent.SUPPLEMENT))
        state = transition.state
    assert isinstance(transition.outcome, NeedConfirmation), [issue.code for issue in state.issues]
    assert not state.issues
    assert not state.metadata.metadata[RequirementFieldPath.INTERESTS].issues
    assert state.requirement.preferences.interests is None
