"""Real upstream DTO identity, M04 storage and M12 evidence consumption."""

import pytest

from app.candidate_research import CandidateResearchLegacyArtifact as CandidateResearchArtifact
from app.itinerary_planning import ItineraryPlanningModelActionArtifact
from app.requirements import ConfirmedTravelRequirement
from app.result_formatting.projection import project_result
from app.workflow_contracts import InMemoryArtifactRepository
from tests.unit.result_formatting.fakes import digest, make_scenario, tree


@pytest.mark.asyncio
async def test_real_confirmation_and_incomplete_candidate_set_feed_completed_itinerary():
    scenario = await make_scenario()
    assert type(scenario.repository) is InMemoryArtifactRepository
    confirmed = scenario.request.state.requirement_outcome
    assert type(confirmed) is ConfirmedTravelRequirement
    assert len(confirmed.confirmation_summary_digest) == 64
    assert not confirmed.confirmation_summary_digest.startswith("sha256:")
    candidate = await scenario.repository.get(
        scenario.refs["candidate_set"], expected_model=CandidateResearchArtifact,
    )
    assert candidate.content.record.record_type == "incomplete"
    assert scenario.itinerary.record.record_type == "completed"
    expected_digest = digest(tree(confirmed))
    assert candidate.content.record.state.confirmed_requirement_digest == expected_digest
    assert scenario.itinerary.record.draft.requirement_digest == expected_digest

    core, days = project_result(scenario.request, scenario.itinerary, scenario.evidence)
    assert len(days) == 1
    assert core.requirement.hotel_check_in_date.isoformat() == "2026-09-02"
    assert core.requirement.hotel_check_out_date.isoformat() == "2026-09-04"
    assert core.requirement.hotel_nights == 2
    # Every presented source is an actual saved/read public artifact.
    for source in core.sources:
        saved = scenario.evidence[source.artifact_ref.artifact_id]
        loaded = await scenario.repository.get(source.artifact_ref, expected_model=type(saved.content))
        assert loaded == saved


@pytest.mark.asyncio
async def test_resume_can_consume_prior_observation_with_matching_public_action():
    scenario = await make_scenario(resume=True)
    action = await scenario.repository.get(
        scenario.refs["planning_action"], expected_model=ItineraryPlanningModelActionArtifact,
    )
    observation = scenario.evidence[scenario.refs["arrival_route"].artifact_id].content
    assert observation.request_digest == action.content.request_digest
    assert observation.request_digest != scenario.itinerary.request_digest
    assert observation.model_action_ref in scenario.itinerary.directories.model_action_refs

    core, days = project_result(scenario.request, scenario.itinerary, scenario.evidence)
    assert len(days) == 1
    assert core.arrival_transfer.route.duration_s == 601
    assert all(source.artifact_ref != action.ref for source in core.sources)
