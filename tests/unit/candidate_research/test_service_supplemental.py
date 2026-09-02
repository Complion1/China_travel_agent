from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.candidate_research.service import CandidateResearchService
from app.candidate_research import (
    AvoidResolutionMissingGap,
    CandidateResearchControlFailureCode,
    CandidateResearchIncomplete,
    CandidateResearchInitialRequest,
    CandidateCountShortfallGap,
    CandidateResearchOperationPlan,
    CandidateResearchSupplementalRequest,
    EvidenceHandle,
    HotelAnchorRef,
    JudgmentPatch,
    MatchVerdict,
    MealIntent,
    MustVisitResolutionMissingGap,
    NoUsefulActionProposed,
    ProposedActionBatch,
    ProposedDetailsAction,
    ProposedSearchAction,
    RequirementGroupJudgmentUpdate,
    RequirementReasonCode,
    RequirementVerdictException,
    RoleJudgmentUpdate,
    RoleReasonCode,
    RoleVerdict,
    ResearchRole,
    ResearchIterationOperationIds,
    SearchAreaMode,
)
from app.candidate_research.artifacts import CandidateResearchArtifact
from app.llm_runtime import LLMInvocationFailure, LLMRuntimeError, LLMErrorCode, ModelRole
from app.requirements import ConfirmedTravelRequirement, FieldMetadataRegistry, InterestTag, TravelPreferences, TravelRequirement, TravelTimeWindow
from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase
from tests.support.candidate_research_fakes import FakeBudgetManager, FakeMapAdapter, FakeModelOperation, FakeRepository, FakeRuntime, details_success, search_no_result, search_success, structured, hotel_selection


def test_aclose_is_idempotent_without_runs() -> None:
    class Runtime:
        def start_operation(self, role):
            raise AssertionError

    class Map:
        async def search_places(self, request):
            raise AssertionError

        async def get_place_details(self, request):
            raise AssertionError

    class Repository:
        async def put(self, request):
            raise AssertionError

        async def lookup(self, *, operation_id):
            raise AssertionError

    class Budget:
        async def consume(self, request):
            raise AssertionError

    service = CandidateResearchService(runtime=Runtime(), map_adapter=Map(), artifact_repository=Repository(), budget_manager=Budget())
    asyncio.run(service.aclose())
    asyncio.run(service.aclose())


def test_invalid_input_marker_is_fail_closed() -> None:
    outcome = CandidateResearchService._zero_failed(CandidateResearchControlFailureCode.INVALID_INPUT)
    assert outcome.failure.code is CandidateResearchControlFailureCode.INVALID_INPUT
    assert outcome.budgets.iterations_consumed == 0


@pytest.mark.asyncio
async def test_public_supplemental_without_open_run_is_run_context_missing() -> None:
    run_id = uuid4()
    def op(name):
        return OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=name)
    predecessor = ArtifactRef(artifact_id=uuid4(), artifact_type="candidate_research.candidate_set", schema_version="1.0", content_hash="sha256:" + "a" * 64, operation_id=op("predecessor"), created_at=datetime.now(timezone.utc), size_bytes=1)
    evidence = ArtifactRef(artifact_id=uuid4(), artifact_type="candidate_research.place_observation", schema_version="1.0", content_hash="sha256:" + "b" * 64, operation_id=op("evidence"), created_at=datetime.now(timezone.utc), size_bytes=1)
    gap = CandidateCountShortfallGap(gap_id="gap:attraction", evidence_refs=(evidence,), role=ResearchRole.ATTRACTION, current_count=0, target_count=1)
    plan = CandidateResearchOperationPlan(run_id=run_id, iteration_slots=(), terminal_successor_operation_id=op("terminal"))
    request = CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=predecessor, gaps=(gap,), operation_plan=plan)
    service = CandidateResearchService(runtime=FakeRuntime(), map_adapter=FakeMapAdapter(), artifact_repository=FakeRepository(), budget_manager=FakeBudgetManager(run_id))
    result = await service.research_supplemental(request)
    assert result.failure.code is CandidateResearchControlFailureCode.RUN_CONTEXT_MISSING


async def _initial_with_predecessor() -> tuple[CandidateResearchService, CandidateResearchInitialRequest, FakeRepository, ArtifactRef]:
    run_id = uuid4()
    def op(name):
        return OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=name)
    slots = tuple(ResearchIterationOperationIds(invocation_slot_index=index, action_operation_id=op(f"a{index}"), judgment_operation_id=op(f"j{index}"), candidate_set_operation_id=op(f"c{index}"), place_observation_operation_ids=(op(f"o{index}"),)) for index in range(6))
    confirmed = __import__("tests.unit.candidate_research.test_service_initial", fromlist=["_request"])._request(run_id)[0].confirmed
    hotel_ref, hotel_artifact = __import__("tests.support.candidate_research_fakes", fromlist=["hotel_selection"]).hotel_selection(run_id)
    initial = __import__("app.candidate_research", fromlist=["CandidateResearchInitialRequest"]).CandidateResearchInitialRequest(confirmed=confirmed, hotel_selection_ref=hotel_ref, operation_plan=CandidateResearchOperationPlan(run_id=run_id, iteration_slots=slots, terminal_successor_operation_id=op("terminal")))
    operation = FakeModelOperation()
    def action(_count):
        import json
        objective_id = json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"]
        return structured(ProposedActionBatch(actions=(ProposedSearchAction(ordinal=0, objective_id=objective_id, query="museum", area_mode=SearchAreaMode.CITY),)))
    operation.responses.extend((action, structured(JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())), structured(NoUsefulActionProposed(reason_code="prior_no_progress")), structured(NoUsefulActionProposed(reason_code="prior_no_progress"))))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel_artifact)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=FakeMapAdapter((search_no_result(),)), artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    await service.research_initial(initial)
    predecessor = repository.refs[slots[0].candidate_set_operation_id]
    return service, initial, repository, predecessor


@pytest.mark.asyncio
async def test_public_supplemental_rejects_predecessor_that_is_not_current_head() -> None:
    service, initial, repository, predecessor = await _initial_with_predecessor()
    run_id = initial.operation_plan.run_id
    predecessor_artifact = (await repository.get(predecessor, expected_model=CandidateResearchArtifact)).content
    evidence = predecessor_artifact.record.state.observation_refs[0]
    operation = OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="supplemental_terminal")
    request = CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=predecessor, gaps=(CandidateCountShortfallGap(gap_id="gap:attraction", evidence_refs=(evidence,), role=ResearchRole.ATTRACTION, current_count=0, target_count=1),), operation_plan=CandidateResearchOperationPlan(run_id=run_id, iteration_slots=(), terminal_successor_operation_id=operation))
    result = await service.research_supplemental(request)
    assert result.failure.code is CandidateResearchControlFailureCode.REPLAY_MISMATCH


@pytest.mark.asyncio
async def test_public_supplemental_rejects_evidence_outside_current_predecessor_directory() -> None:
    service, initial, repository, predecessor = await _initial_with_predecessor()
    run_id = initial.operation_plan.run_id
    fake_evidence = ArtifactRef(artifact_id=uuid4(), artifact_type="candidate_research.place_observation", schema_version="1.0", content_hash="sha256:" + "f" * 64, operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="not_in_directory"), created_at=datetime.now(timezone.utc), size_bytes=1)
    terminal = repository.refs[initial.operation_plan.terminal_successor_operation_id]
    request = CandidateResearchSupplementalRequest(predecessor_candidate_set_ref=terminal, gaps=(CandidateCountShortfallGap(gap_id="gap:attraction", evidence_refs=(fake_evidence,), role=ResearchRole.ATTRACTION, current_count=0, target_count=1),), operation_plan=CandidateResearchOperationPlan(run_id=run_id, iteration_slots=(), terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="supplemental_evidence_terminal")))
    result = await service.research_supplemental(request)
    assert result.failure.code is CandidateResearchControlFailureCode.REPLAY_MISMATCH


def _slots(run_id, *, count: int, prefix: str, place_width: int = 1) -> tuple[ResearchIterationOperationIds, ...]:
    def op(name: str) -> OperationId:
        return OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step=name)

    return tuple(
        ResearchIterationOperationIds(
            invocation_slot_index=index,
            action_operation_id=op(f"{prefix}_action_{index}"),
            judgment_operation_id=op(f"{prefix}_judgment_{index}"),
            candidate_set_operation_id=op(f"{prefix}_candidate_{index}"),
            place_observation_operation_ids=tuple(op(f"{prefix}_observation_{index}_{ordinal}") for ordinal in range(place_width)),
        )
        for index in range(count)
    )


@pytest.mark.asyncio
async def test_public_supplemental_must_coverage_is_restored_resolved_and_replayed_without_side_effects() -> None:
    from tests.unit.candidate_research.test_service_initial import _request

    run_id = uuid4()
    initial, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=1)
    confirmed = ConfirmedTravelRequirement(
        schema_version="1.0",
        requirement=TravelRequirement(
            destination="杭州",
            time_window=TravelTimeWindow(sightseeing_days=1),
            preferences=TravelPreferences(
                interests=[InterestTag.CULTURE, InterestTag.NATURE],
                must_visit_places=["West Lake"],
            ),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(),
        confirmation_summary_digest="confirmed",
    )
    initial = CandidateResearchInitialRequest(
        confirmed=confirmed,
        hotel_selection_ref=hotel_ref,
        operation_plan=initial.operation_plan,
    )
    operation = FakeModelOperation()

    def initial_action(_count: int):
        objective = json.loads(operation.prompts[-1].user_text)["objectives"][0]
        return structured(
            ProposedActionBatch(
                actions=(
                    ProposedSearchAction(
                        ordinal=0,
                        objective_id=objective["objective_id"],
                        query="west-lake-initial",
                        area_mode=SearchAreaMode.CITY,
                    ),
                )
            )
        )

    def ignored_initial_action(_count: int):
        objective = json.loads(operation.prompts[-1].user_text)["objectives"][1]
        if objective["kind"] == "initial_hotel_restaurant_nearby":
            action = ProposedSearchAction(
                ordinal=0,
                objective_id=objective["objective_id"],
                query="ignored-nearby",
                area_mode=SearchAreaMode.NEARBY,
                anchor=HotelAnchorRef(),
                radius_km=3,
            )
        else:
            action = ProposedSearchAction(
                ordinal=0,
                objective_id=objective["objective_id"],
                query="ignored",
                area_mode=SearchAreaMode.CITY,
            )
        return structured(ProposedActionBatch(actions=(action,)))

    operation.responses.extend(
        (
            initial_action,
            structured(
                JudgmentPatch(
                    role_updates=(
                        RoleJudgmentUpdate(
                            candidate_handle="c:0",
                            role=ResearchRole.ATTRACTION,
                            verdict=RoleVerdict.SUPPORTED,
                            reason_code=RoleReasonCode.PROVIDER_CATEGORY_SUPPORTS,
                            evidence_handles=(EvidenceHandle(handle_id="e:0"),),
                        ),
                    ),
                    interest_updates=(),
                    meal_updates=(),
                    requirement_group_updates=(),
                )
            ),
            ignored_initial_action,
            structured(NoUsefulActionProposed(reason_code="prior_no_progress")),
        )
    )
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    map_adapter = FakeMapAdapter((search_success("must-place"),))
    budget = FakeBudgetManager(run_id)
    service = CandidateResearchService(
        runtime=FakeRuntime(operation),
        map_adapter=map_adapter,
        artifact_repository=repository,
        budget_manager=budget,
    )

    initial_result = await service.research_initial(initial)

    assert isinstance(initial_result, CandidateResearchIncomplete)
    predecessor_ref = initial_result.candidate_set_ref
    predecessor = (await repository.get(predecessor_ref, expected_model=CandidateResearchArtifact)).content
    predecessor_state = predecessor.record.state
    assert predecessor_state.coverage.must_item_count == 1
    assert predecessor_state.coverage.resolved_must_item_count == 0
    assert predecessor_state.requirement_group_states[0].default_verdict is MatchVerdict.UNRESOLVED
    summary = next(item for item in predecessor.record.gaps if item.gap_type == "must_visit_resolution_missing")
    gap = MustVisitResolutionMissingGap.model_validate_json(
        json.dumps(
            {
                "schema_version": "1.0",
                "gap_type": "must_visit_resolution_missing",
                "gap_id": summary.gap_id,
                "evidence_refs": [
                    predecessor_state.observation_refs[index].model_dump(mode="json", exclude_none=False)
                    for index in summary.evidence_indexes
                ],
                "requirement_group_id": summary.requirement_group_id,
                "requirement_item_ids": list(summary.requirement_item_ids),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        strict=True,
    )
    remaining = predecessor_state.budgets.iteration_limit - predecessor_state.budgets.iterations_consumed
    supplemental_slots = _slots(run_id, count=remaining, prefix="supplemental_must", place_width=1)

    def supplemental_details(_count: int):
        objective = json.loads(operation.prompts[-1].user_text)["objectives"][0]
        return structured(
            ProposedActionBatch(
                actions=(
                    ProposedDetailsAction(
                        ordinal=0,
                        objective_id=objective["objective_id"],
                        uid="must-place",
                    ),
                )
            )
        )

    def supplemental_search(_count: int):
        objective = json.loads(operation.prompts[-1].user_text)["objectives"][0]
        return structured(
            ProposedActionBatch(
                actions=(
                    ProposedSearchAction(
                        ordinal=0,
                        objective_id=objective["objective_id"],
                        query="west-lake-follow-up",
                        area_mode=SearchAreaMode.CITY,
                    ),
                )
            )
        )

    def resolving_judgment(_count: int):
        group = json.loads(operation.prompts[-1].user_text)["requirements"][0]
        return structured(
            JudgmentPatch(
                role_updates=(),
                interest_updates=(),
                meal_updates=(),
                requirement_group_updates=(
                    RequirementGroupJudgmentUpdate(
                        requirement_group_id=group["requirement_group_id"],
                        requirement_kind="must_visit",
                        evaluated_candidate_handles=("c:0", "c:1"),
                        default_verdict=MatchVerdict.CONFIRMED,
                        default_reason_code=RequirementReasonCode.DETAILS_DISAMBIGUATED,
                        default_evidence_handles=(EvidenceHandle(handle_id="e:1"),),
                        exceptions=(
                            RequirementVerdictException(
                                candidate_handle="c:1",
                                verdict=MatchVerdict.CONFIRMED,
                                reason_code=RequirementReasonCode.EXACT_NAME_MATCH,
                                evidence_handles=(EvidenceHandle(handle_id="e:2"),),
                            ),
                        ),
                    ),
                ),
            )
        )

    def ignored_supplemental_action(_count: int):
        objective = json.loads(operation.prompts[-1].user_text)["objectives"][1]
        if objective["kind"] == "initial_hotel_restaurant_nearby":
            action = ProposedSearchAction(
                ordinal=0,
                objective_id=objective["objective_id"],
                query="ignored-supplemental-nearby",
                area_mode=SearchAreaMode.NEARBY,
                anchor=HotelAnchorRef(),
                radius_km=3,
            )
        else:
            action = ProposedSearchAction(
                ordinal=0,
                objective_id=objective["objective_id"],
                query="ignored-supplemental",
                area_mode=SearchAreaMode.CITY,
                interest=InterestTag(objective["interest"]) if objective.get("interest") is not None else None,
                meal_intent=MealIntent(objective["meal_intent"]) if objective.get("meal_intent") is not None else None,
            )
        return structured(ProposedActionBatch(actions=(action,)))

    operation.responses.extend(
        (
            supplemental_details,
            structured(JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())),
            supplemental_search,
            resolving_judgment,
            ignored_supplemental_action,
        )
    )
    map_adapter.responses.extend((details_success("must-place"), search_success("must-place-alternative")))
    supplemental = CandidateResearchSupplementalRequest(
        predecessor_candidate_set_ref=predecessor_ref,
        gaps=(gap,),
        operation_plan=CandidateResearchOperationPlan(
            run_id=run_id,
            iteration_slots=supplemental_slots,
            terminal_successor_operation_id=OperationId(
                run_id=run_id,
                phase=WorkflowPhase.CANDIDATE_RESEARCH,
                logical_step="supplemental_must_terminal",
            ),
        ),
    )

    result = await service.research_supplemental(supplemental)

    assert isinstance(result, CandidateResearchIncomplete)
    unchanged_ref = repository.refs[supplemental_slots[0].candidate_set_operation_id]
    unchanged = (await repository.get(unchanged_ref, expected_model=CandidateResearchArtifact)).content.record.state
    assert unchanged.coverage.must_item_count == 1
    assert unchanged.coverage.resolved_must_item_count == 0
    assert unchanged.requirement_group_states[0].default_verdict is MatchVerdict.UNRESOLVED
    terminal = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content.record.state
    assert terminal.coverage.must_item_count == result.coverage.must_item_count == 1
    assert terminal.coverage.resolved_must_item_count == result.coverage.resolved_must_item_count == 1
    assert terminal.candidates[0].state.value == "eligible"
    assert terminal.requirement_group_states[0].default_verdict is MatchVerdict.CONFIRMED
    assert terminal.candidates[0].must_matches[0].verdict is MatchVerdict.CONFIRMED
    assert predecessor.record.state.coverage.resolved_must_item_count == 0

    side_effect_counts = (
        operation.call_count,
        map_adapter.call_count,
        budget.consume_count,
        len(repository.put_requests),
    )
    replayed = await service.research_supplemental(supplemental)
    assert replayed == result
    assert (
        operation.call_count,
        map_adapter.call_count,
        budget.consume_count,
        len(repository.put_requests),
    ) == side_effect_counts


def _avoid_initial_request(run_id):
    hotel_ref, hotel_artifact = hotel_selection(run_id)
    slots = _slots(run_id, count=6, prefix="initial")
    confirmed = ConfirmedTravelRequirement(
        schema_version="1.0",
        requirement=TravelRequirement(
            destination="杭州",
            time_window=TravelTimeWindow(sightseeing_days=1),
            preferences=TravelPreferences(interests=[InterestTag.CULTURE, InterestTag.NATURE], avoid_places=["夜市"]),
        ),
        metadata_without_raw_evidence=FieldMetadataRegistry(),
        confirmation_summary_digest="confirmed",
    )
    request = CandidateResearchInitialRequest(
        confirmed=confirmed,
        hotel_selection_ref=hotel_ref,
        operation_plan=CandidateResearchOperationPlan(
            run_id=run_id,
            iteration_slots=slots,
            terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="initial_terminal"),
        ),
    )
    return request, hotel_ref, hotel_artifact


def _fresh_avoid_gap(summary, state) -> AvoidResolutionMissingGap:
    payload = {
        "schema_version": "1.0",
        "gap_type": "avoid_resolution_missing",
        "gap_id": summary.gap_id,
        "evidence_refs": [state.observation_refs[index].model_dump(mode="json", exclude_none=False) for index in summary.evidence_indexes],
        "requirement_group_id": summary.requirement_group_id,
        "requirement_item_ids": list(summary.requirement_item_ids),
        "candidate_uids": list(summary.candidate_uids),
    }
    return AvoidResolutionMissingGap.model_validate_json(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)


async def _avoid_predecessor():
    run_id = uuid4()
    initial, hotel_ref, hotel_artifact = _avoid_initial_request(run_id)
    operation = FakeModelOperation()

    def initial_action(_count: int):
        objective = json.loads(operation.prompts[-1].user_text)["objectives"][0]
        query = "target" if _count in {1, 3} else "extra"
        return structured(ProposedActionBatch(actions=(ProposedSearchAction(ordinal=0, objective_id=objective["objective_id"], query=query, area_mode=SearchAreaMode.CITY),)))

    empty_patch = structured(JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=()))
    operation.responses.extend((initial_action, empty_patch, initial_action, initial_action, empty_patch, initial_action, structured(NoUsefulActionProposed(reason_code="prior_no_progress"))))
    repository = FakeRepository()
    repository.seed(hotel_ref, hotel_artifact)
    map_adapter = FakeMapAdapter((search_success("target-1"), search_success("extra-1")))
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=map_adapter, artifact_repository=repository, budget_manager=FakeBudgetManager(run_id))
    initial_result = await service.research_initial(initial)
    assert isinstance(initial_result, CandidateResearchIncomplete)
    predecessor_ref = initial_result.candidate_set_ref
    predecessor = (await repository.get(predecessor_ref, expected_model=CandidateResearchArtifact)).content
    summary = next(item for item in predecessor.record.gaps if item.gap_type == "avoid_resolution_missing")
    return service, operation, map_adapter, repository, initial, predecessor_ref, predecessor, _fresh_avoid_gap(summary, predecessor.record.state)


@pytest.mark.asyncio
async def test_public_supplemental_avoid_target_resolution_is_satisfied_and_replayed_as_noop() -> None:
    service, operation, map_adapter, repository, initial, predecessor_ref, predecessor, gap = await _avoid_predecessor()
    run_id = initial.operation_plan.run_id
    supplemental_slots = _slots(run_id, count=1, prefix="supplemental", place_width=2)
    objective_action = lambda _count: structured(ProposedActionBatch(actions=(
        ProposedDetailsAction(ordinal=0, objective_id=json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"], uid="target-1"),
        ProposedDetailsAction(ordinal=1, objective_id=json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"], uid="extra-1"),
    )))
    operation.responses.extend((objective_action, structured(JudgmentPatch(
        role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=(RequirementGroupJudgmentUpdate(
            requirement_group_id=gap.requirement_group_id,
            requirement_kind="avoid",
            evaluated_candidate_handles=("c:0", "c:1"),
            default_verdict=MatchVerdict.NO_MATCH,
            default_reason_code=RequirementReasonCode.DETAILS_DISAMBIGUATED,
            default_evidence_handles=(EvidenceHandle(handle_id="e:2"),),
            exceptions=(RequirementVerdictException(candidate_handle="c:1", verdict=MatchVerdict.CONFIRMED, reason_code=RequirementReasonCode.DETAILS_DISAMBIGUATED, evidence_handles=(EvidenceHandle(handle_id="e:3"),)),),
        ),),
    ))))
    map_adapter.responses.extend((details_success("target-1"), details_success("extra-1")))
    plan = CandidateResearchOperationPlan(run_id=run_id, iteration_slots=supplemental_slots, terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="supplemental_terminal_resolved"))
    request = CandidateResearchSupplementalRequest.model_validate_json(json.dumps({
        "predecessor_candidate_set_ref": predecessor_ref.model_dump(mode="json", exclude_none=False),
        "gaps": [gap.model_dump(mode="json", exclude_none=False)],
        "operation_plan": plan.model_dump(mode="json", exclude_none=False),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
    result = await service.research_supplemental(request)
    assert isinstance(result, CandidateResearchIncomplete)
    assert operation.call_count == 9
    assert map_adapter.call_count == 4
    terminal = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    assert all(item.gap_type != "avoid_resolution_missing" for item in terminal.record.gaps)
    assert terminal.record.state.candidates[1].state.value == "excluded"

    before_model_calls = operation.call_count
    before_map_calls = map_adapter.call_count
    replayed = await service.research_supplemental(request)
    assert replayed == result
    assert operation.call_count == before_model_calls
    assert map_adapter.call_count == before_map_calls


@pytest.mark.asyncio
async def test_public_supplemental_avoid_new_unresolved_candidate_keeps_gap_pending() -> None:
    service, operation, map_adapter, repository, initial, predecessor_ref, _predecessor, gap = await _avoid_predecessor()
    run_id = initial.operation_plan.run_id
    slots = _slots(run_id, count=1, prefix="supplemental_new", place_width=1)
    operation.responses.extend((
        lambda _count: structured(ProposedActionBatch(actions=(ProposedSearchAction(
            ordinal=0,
            objective_id=json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"],
            query="new",
            area_mode=SearchAreaMode.CITY,
        ),))),
        structured(JudgmentPatch(
            role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=(RequirementGroupJudgmentUpdate(
                requirement_group_id=gap.requirement_group_id,
                requirement_kind="avoid",
                evaluated_candidate_handles=("c:0", "c:1", "c:2"),
                default_verdict=MatchVerdict.NO_MATCH,
                default_reason_code=RequirementReasonCode.DETAILS_DISAMBIGUATED,
                default_evidence_handles=(EvidenceHandle(handle_id="e:0"),),
                exceptions=(
                    RequirementVerdictException(candidate_handle="c:1", verdict=MatchVerdict.NO_MATCH, reason_code=RequirementReasonCode.DETAILS_DISAMBIGUATED, evidence_handles=(EvidenceHandle(handle_id="e:1"),)),
                    RequirementVerdictException(candidate_handle="c:2", verdict=MatchVerdict.UNRESOLVED, reason_code=RequirementReasonCode.EVIDENCE_INSUFFICIENT, evidence_handles=(EvidenceHandle(handle_id="e:2"),)),
                ),
            ),),
        )),
    ))
    map_adapter.responses.append(search_success("new-1"))
    plan = CandidateResearchOperationPlan(run_id=run_id, iteration_slots=slots, terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="supplemental_new_terminal"))
    request = CandidateResearchSupplementalRequest.model_validate_json(json.dumps({
        "predecessor_candidate_set_ref": predecessor_ref.model_dump(mode="json", exclude_none=False),
        "gaps": [gap.model_dump(mode="json", exclude_none=False)],
        "operation_plan": plan.model_dump(mode="json", exclude_none=False),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
    result = await service.research_supplemental(request)
    assert isinstance(result, CandidateResearchIncomplete)
    terminal = (await repository.get(result.candidate_set_ref, expected_model=CandidateResearchArtifact)).content
    avoid_gaps = [item for item in terminal.record.gaps if item.gap_type == "avoid_resolution_missing"]
    assert len(avoid_gaps) == 1
    assert tuple(avoid_gaps[0].candidate_uids) == ("new-1",)
    assert operation.call_count == 9
    assert map_adapter.call_count == 3


@pytest.mark.asyncio
async def test_public_supplemental_avoid_missing_target_uid_is_rejected() -> None:
    service, operation, map_adapter, repository, initial, predecessor_ref, _predecessor, gap = await _avoid_predecessor()
    run_id = initial.operation_plan.run_id
    gap_payload = gap.model_dump(mode="json", exclude_none=False)
    gap_payload["candidate_uids"][0] = "missing-uid"
    missing_gap = AvoidResolutionMissingGap.model_validate_json(json.dumps(gap_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
    plan = CandidateResearchOperationPlan(run_id=run_id, iteration_slots=(), terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="supplemental_missing_terminal"))
    request = CandidateResearchSupplementalRequest.model_validate_json(json.dumps({
        "predecessor_candidate_set_ref": predecessor_ref.model_dump(mode="json", exclude_none=False),
        "gaps": [missing_gap.model_dump(mode="json", exclude_none=False)],
        "operation_plan": plan.model_dump(mode="json", exclude_none=False),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
    result = await service.research_supplemental(request)
    assert result.failure.code is CandidateResearchControlFailureCode.REPLAY_MISMATCH
    assert operation.call_count == 7
    assert map_adapter.call_count == 2


@pytest.mark.asyncio
async def test_public_supplemental_candidate_count_gap_uses_current_head_and_evidence_directory() -> None:
    from tests.unit.candidate_research.test_service_initial import _request

    run_id = uuid4()
    initial, hotel_ref, hotel_artifact, _ = _request(run_id, place_width=1)
    operation = FakeModelOperation()
    operation.responses.extend((
        lambda _count: structured(ProposedActionBatch(actions=(ProposedSearchAction(
            ordinal=0,
            objective_id=json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"],
            query="initial-candidate-count",
            area_mode=SearchAreaMode.CITY,
        ),))),
        structured(JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())),
        structured(NoUsefulActionProposed(reason_code="prior_no_progress")),
    ))
    repository = FakeRepository(); repository.seed(hotel_ref, hotel_artifact)
    map_adapter = FakeMapAdapter((search_no_result(),))
    budget = FakeBudgetManager(run_id)
    service = CandidateResearchService(runtime=FakeRuntime(operation), map_adapter=map_adapter, artifact_repository=repository, budget_manager=budget)
    initial_result = await service.research_initial(initial)
    predecessor_ref = initial_result.candidate_set_ref
    run_id = initial.operation_plan.run_id
    predecessor = (await repository.get(predecessor_ref, expected_model=CandidateResearchArtifact)).content
    summary = next(item for item in predecessor.record.gaps if item.gap_type == "candidate_count_shortfall")
    state = predecessor.record.state
    gap = CandidateCountShortfallGap.model_validate_json(json.dumps({
        "schema_version": "1.0", "gap_type": "candidate_count_shortfall", "gap_id": summary.gap_id,
        "evidence_refs": [state.observation_refs[index].model_dump(mode="json", exclude_none=False) for index in summary.evidence_indexes],
        "role": summary.role.value, "current_count": summary.current_count, "target_count": summary.target_count,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
    operation.responses.extend((
        lambda _count: structured(ProposedActionBatch(actions=(ProposedSearchAction(
            ordinal=0,
            objective_id=json.loads(operation.prompts[-1].user_text)["objectives"][0]["objective_id"],
            query="supplemental-candidate-count",
            area_mode=SearchAreaMode.CITY,
        ),))),
        structured(JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())),
        structured(NoUsefulActionProposed(reason_code="prior_no_progress")),
    ))
    map_adapter.responses.append(search_success("supplemental-place"))
    remaining = state.budgets.iteration_limit - state.budgets.iterations_consumed
    plan = CandidateResearchOperationPlan(run_id=run_id, iteration_slots=_slots(run_id, count=max(1, remaining), prefix="supplemental_count"), terminal_successor_operation_id=OperationId(run_id=run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="supplemental_count_terminal"))
    request = CandidateResearchSupplementalRequest.model_validate_json(json.dumps({
        "predecessor_candidate_set_ref": predecessor_ref.model_dump(mode="json", exclude_none=False),
        "gaps": [gap.model_dump(mode="json", exclude_none=False)],
        "operation_plan": plan.model_dump(mode="json", exclude_none=False),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), strict=True)
    result = await service.research_supplemental(request)
    assert result.outcome_type in {"incomplete", "failed"}
    assert map_adapter.call_count >= 2
