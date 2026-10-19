"""通过公共研究入口验证当前轮纠错、真实计数和成果保留。"""
import json

import pytest

from app.candidate_research import (
    CandidateResearchArtifact, CandidateResearchInvocationAttemptArtifact,
    CandidateResearchService, NoUsefulActionProposed, validate_qualification_sources,
)
from app.workflow_contracts import ArtifactReadResult
from tests.support.candidate_research_fakes import structured, search_no_result, search_success, FakeRuntime
from tests.unit.candidate_research.test_qualification import legal_judgment
from tests.unit.candidate_research.test_research_correction_recovery import setup, action, failure, empty_patch


def rejected():
    return structured(NoUsefulActionProposed(reason_code="prior_no_progress"))


@pytest.mark.parametrize("reservation_failure", [False, True])
async def test_final_action_attempt_follows_actual_budget_admission_before_any_map(reservation_failure):
    from uuid import uuid4
    from app.workflow_contracts import ExecutionBudgetError
    from tests.support.candidate_research_fakes import FakeRepository, FakeBudgetManager, FakeMapAdapter, FakeModelOperation
    from tests.unit.candidate_research.test_service_initial import _request, _action_batch_from_prompt
    request, hotel_ref, hotel, _ = _request(uuid4(), place_width=2)
    events = []
    class Budget(FakeBudgetManager):
        async def consume(self, request):
            if request.key.name == "research.place_call":
                events.append("reserve")
                if reservation_failure and events.count("reserve") == 2:
                    raise ExecutionBudgetError()
            return await super().consume(request)
    class Repository(FakeRepository):
        async def put(self, request):
            if request.artifact_type.endswith("invocation_attempt") and request.content["kind"] == "action":
                events.append("attempt")
                assert events.count("reserve") == 2
            elif request.artifact_type.endswith("model_action"):
                events.append("main")
            return await super().put(request)
    class Maps(FakeMapAdapter):
        async def _next(self):
            events.append("map")
            assert "main" in events
            return await super()._next()
    model, repo, maps = FakeModelOperation(), Repository(), Maps([search_no_result()] * 2)
    model.responses.extend([lambda _: structured(_action_batch_from_prompt(model, 2)), structured(empty_patch())])
    repo.seed(hotel_ref, hotel)
    service = CandidateResearchService(runtime=FakeRuntime(model), map_adapter=maps,
        artifact_repository=repo, budget_manager=Budget(request.operation_plan.run_id, iteration_limit=1))
    result = await service.research_initial(request)
    assert events[:4] == ["reserve", "reserve", "attempt", "main"]
    ref = repo.refs[request.operation_plan.terminal_successor_operation_id]
    artifact, _ = await audited(repo, ref)
    if reservation_failure:
        assert result.outcome_type == "failed" and result.failure.code.value == "budget_error"
        assert maps.call_count == 0
        final = attempts(repo)[0]["result"]["record"]["action_dispositions"]
        assert [item["disposition"] for item in final] == ["accepted", "not_admitted_budget_exhausted"]
        assert artifact.record.state.observation_refs
    else:
        assert result.outcome_type == "incomplete", getattr(result, "failure", None)
        assert maps.call_count == 2
    await service.aclose()


def attempts(repo):
    return [item.content for item in repo.put_requests if item.artifact_type == "candidate_research.invocation_attempt"]


async def audited(repo, ref):
    artifact = (await repo.get(ref, expected_model=CandidateResearchArtifact)).content
    from app.candidate_research import CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
    models = {(item.artifact_type, item.schema_version): item.model for item in CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS}
    state = artifact.record.state
    sources = {}
    for source in (*artifact.invocation_attempt_refs, *state.model_action_refs,
                   *state.model_judgment_refs, *state.observation_refs):
        sources[source.artifact_id] = await repo.get(source, expected_model=models[source.artifact_type, source.schema_version])
    validate_qualification_sources(artifact, sources)
    return artifact, sources


def reseal(artifact, sources, changes):
    """重算修改后的引用链，避免反例只被摘要不一致拦截。"""
    from app.candidate_research.fingerprints import canonical_json_bytes, content_digest
    new_sources = {}
    visiting = set()

    def replace_refs(value):
        if isinstance(value, list):
            return [replace_refs(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "artifact_id" in value and "operation_id" in value:
            from uuid import UUID
            identity = UUID(value["artifact_id"])
            if identity in sources:
                return rebuild(identity).ref.model_dump(mode="json")
        return {key: replace_refs(item) for key, item in value.items()}

    def rebuild(identity):
        if identity in new_sources:
            return new_sources[identity]
        assert identity not in visiting
        visiting.add(identity)
        old = sources[identity]
        tree = changes.get(identity, old.content.model_dump(mode="json"))
        content = type(old.content).model_validate_json(json.dumps(replace_refs(tree)), strict=True)
        ref = old.ref.model_copy(update={"content_hash": content_digest(content), "size_bytes": len(canonical_json_bytes(content))})
        saved = ArtifactReadResult(ref=ref, content=content)
        new_sources[identity] = saved
        visiting.remove(identity)
        return saved

    tree = changes.get("candidate", artifact.model_dump(mode="json"))
    return type(artifact).model_validate_json(json.dumps(replace_refs(tree)), strict=True), new_sources


async def test_third_recoverable_error_returns_incomplete_in_same_iteration():
    request, model, maps, repo, service = setup()
    model.responses.extend([rejected()] * 4)
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete", getattr(result, "failure", None)
    assert (model.call_count, maps.call_count) == (3, 0)
    assert result.budgets.iterations_consumed == 1
    assert result.budgets.corrections_consumed == 2
    assert {item.value for item in result.stop_reasons} >= {"correction_limit_reached"}
    assert [item["is_correction"] for item in attempts(repo)] == [False, True, True]
    assert [item["correction_number"] for item in attempts(repo)] == [None, 1, 2]
    await audited(repo, result.candidate_set_ref)
    assert await service.research_initial(request) == result and model.call_count == 3
    await service.aclose()


async def test_second_correction_success_keeps_normal_research_available():
    request, model, maps, repo, service = setup(slots=2)
    maps.responses.extend([search_no_result(), search_no_result()])
    model.responses.extend([failure(), failure(), action(model), structured(empty_patch()),
                            action(model), structured(empty_patch())])
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete", getattr(result, "failure", None)
    assert (model.call_count, maps.call_count) == (6, 2)
    assert result.budgets.iterations_consumed == 2
    assert result.budgets.corrections_consumed == 2
    assert "correction_limit_reached" not in {item.value for item in result.stop_reasons}
    await audited(repo, result.candidate_set_ref)
    await service.aclose()


async def test_policy_format_and_judgment_share_actual_corrections():
    request, model, maps, repo, service = setup(slots=3)
    maps.responses.append(search_success("verified"))
    model.responses.extend([rejected(), failure(), action(model), structured(empty_patch()),
                            lambda _: legal_judgment(model)])
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete", getattr(result, "failure", None)
    assert (model.call_count, maps.call_count) == (4, 1)
    assert result.budgets.corrections_consumed == 2 and result.budgets.iterations_consumed == 1
    assert "correction_limit_reached" in {item.value for item in result.stop_reasons}
    artifact, _ = await audited(repo, result.candidate_set_ref)
    assert len(artifact.record.state.candidates) == 1
    assert artifact.record.state.candidates[0].state.value == "withheld"
    assert not artifact.qualification_state.assessments
    assert [item["correction_number"] for item in attempts(repo)] == [None, 1, 2, None]
    await service.aclose()


async def test_two_judgment_corrections_preserve_maps_but_never_accept_partial_patch():
    request, model, maps, repo, service = setup(slots=6)
    maps.responses.append(search_success("verified"))
    model.responses.extend([action(model)] + [structured(empty_patch())] * 3)
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete", getattr(result, "failure", None)
    assert (model.call_count, maps.call_count) == (4, 1)
    assert result.budgets.corrections_consumed == 2
    artifact, _ = await audited(repo, result.candidate_set_ref)
    assert len(artifact.record.state.candidates) == 1
    assert not artifact.qualification_state.assessments
    assert artifact.record.state.candidates[0].state.value == "withheld"
    assert result.budgets.iterations_consumed < 6
    await service.aclose()


async def test_seventy_three_candidates_still_allow_correction_in_sixth_iteration():
    from tests.unit.candidate_research.test_service_initial import _action_batch_from_prompt, _first_objective_action
    from tests.unit.candidate_research.test_qualification import required_patch
    from app.candidate_research import ProposedActionBatch
    from app.requirements import InterestTag
    request, model, maps, repo, service = setup(width=2, slots=6)
    for start, width in ((0, 50), (50, 23)):
        response = search_success("seed")
        maps.responses.append(response.model_copy(update={"places": tuple(
            response.places[0].model_copy(update={"uid": f"place-{i}"}) for i in range(start, start + width))}))
    maps.responses.append(search_no_result())
    good = []
    def invalid(_):
        original = _first_objective_action(model, query="last query")
        good.append(original)
        interest = InterestTag.NATURE if original.interest is not InterestTag.NATURE else InterestTag.CULTURE
        return structured(ProposedActionBatch(actions=(original.model_copy(update={"interest": interest}),)))
    model.responses.extend([lambda _: structured(_action_batch_from_prompt(model, 2))]
        + [lambda _: structured(required_patch(json.loads(model.prompts[-1].user_text)))] * 5
        + [invalid, lambda _: structured(ProposedActionBatch(actions=(good[0],))), structured(empty_patch())])
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete", getattr(result, "failure", None)
    assert (model.call_count, maps.call_count) == (9, 3)
    assert result.budgets.iterations_consumed == 6 and result.budgets.corrections_consumed == 1
    artifact, _ = await audited(repo, result.candidate_set_ref)
    assert len(artifact.record.state.candidates) == 73
    assert len(artifact.record.state.model_judgment_refs) == 6
    assert len(artifact.qualification_state.assessments) >= 73
    assert [row["attempt_index"] for row in attempts(repo) if row["iteration_index"] == 5 and row["kind"] == "action"] == [0, 1]
    assert await service.research_initial(request) == result and model.call_count == 9
    await service.aclose()


@pytest.mark.parametrize("mutation", ["omit", "order", "kind", "cost", "correction_number", "main_result"])
async def test_invocation_source_tampering_is_rejected(mutation):
    request, model, maps, repo, service = setup()
    model.responses.extend([rejected()] * 3)
    result = await service.research_initial(request)
    artifact, sources = await audited(repo, result.candidate_set_ref)
    if mutation in {"omit", "order"}:
        refs = artifact.invocation_attempt_refs
        refs = refs[:-1] if mutation == "omit" else tuple(reversed(refs))
        forged = artifact.model_copy(update={"invocation_attempt_refs": refs})
    elif mutation == "main_result":
        ref = artifact.record.state.model_action_refs[0]
        saved = sources[ref.artifact_id]
        content = saved.content.model_copy(update={"invocation_index": 0})
        sources[ref.artifact_id] = ArtifactReadResult(ref=ref, content=content)
        forged = artifact
    else:
        ref = artifact.invocation_attempt_refs[-1]
        saved = sources[ref.artifact_id]
        values = {"kind": "judgment", "cost": 0, "correction_number": 1}
        field = "semantic_cost" if mutation == "cost" else mutation
        content = saved.content.model_copy(update={field: values[mutation]})
        sources[ref.artifact_id] = ArtifactReadResult(ref=ref, content=content)
        forged = artifact
    with pytest.raises(ValueError):
        validate_qualification_sources(forged, sources)
    await service.aclose()


@pytest.mark.parametrize("mutation", ["correction_number", "main_result", "consumption", "order", "omit"])
async def test_rehashed_invocation_chain_still_rejects_false_history(mutation):
    request, model, maps, repo, service = setup()
    model.responses.extend([rejected()] * 3)
    result = await service.research_initial(request)
    artifact, sources = await audited(repo, result.candidate_set_ref)
    tree, changes = artifact.model_dump(mode="json"), {}
    if mutation == "correction_number":
        ref = artifact.invocation_attempt_refs[-1]
        value = sources[ref.artifact_id].content.model_dump(mode="json")
        value["correction_number"] = 1
        changes[ref.artifact_id] = value
    elif mutation == "main_result":
        ref = artifact.record.state.model_action_refs[0]
        value = sources[ref.artifact_id].content.model_dump(mode="json")
        value["result"]["output"]["reason_code"] = "no_legal_action"
        changes[ref.artifact_id] = value
    elif mutation == "consumption":
        tree["record"]["budgets"]["semantic_invocations_consumed"] += 1
        tree["record"]["state"]["budgets"]["semantic_invocations_consumed"] += 1
        for budget in (tree["record"]["budgets"], tree["record"]["state"]["budgets"]):
            budget["provider_attempts_complete"] = False
            budget["usage_complete"] = False
    elif mutation == "order":
        tree["invocation_attempt_refs"].reverse()
    else:
        tree["invocation_attempt_refs"].pop()
    changes["candidate"] = tree
    forged, rehashed_sources = reseal(artifact, sources, changes)
    with pytest.raises(ValueError):
        validate_qualification_sources(forged, rehashed_sources)
    await service.aclose()

@pytest.mark.parametrize("stage", ["action", "judgment"])
@pytest.mark.parametrize("admission", ["zero", "unknown", "conflict", "contradictory_outcome"])
async def test_public_entry_stops_safely_on_admission_dependency_failure(stage, admission):
    from tests.support.candidate_research_fakes import FakeModelOperation
    request, _, maps, repo, service = setup()
    target = 0 if stage == "action" else 1

    class BrokenOperation(FakeModelOperation):
        @property
        def semantic_invocations_consumed(self):
            return None if admission == "unknown" else self._admitted_count

        async def ainvoke_structured(self, prompt, schema):
            if self.call_count != target:
                return await super().ainvoke_structured(prompt, schema)
            self.call_count += 1
            self.prompts.append(prompt)
            self.schemas.append(schema)
            if admission in {"conflict", "contradictory_outcome"}:
                self._admitted_count += 2 if admission == "conflict" else 0
                return rejected() if stage == "action" else structured(empty_patch())
            raise RuntimeError("dependency failed before admission could be confirmed")

    model = BrokenOperation()
    service._runtime.operation = model
    if stage == "judgment":
        model.responses.append(action(model))
        maps.responses.append(search_no_result())
    result = await service.research_initial(request)
    assert result.outcome_type == "failed"
    assert result.failure.code.value == ("dependency_contract_error" if admission == "zero" else "operation_result_indeterminate")
    assert result.possible_duplicate is (admission != "zero")
    assert (model.call_count, maps.call_count) == (target + 1, target)
    assert result.budgets.semantic_invocations_consumed == target
    assert result.budgets.corrections_consumed == 0
    assert result.budgets.usage_complete is (admission == "zero")
    assert result.budgets.provider_attempts_complete is (admission == "zero")
    terminal = repo.refs[request.operation_plan.terminal_successor_operation_id]
    await audited(repo, terminal)
    assert attempts(repo)[-1]["semantic_cost"] == (0 if admission == "zero" else None)
    before = (model.call_count, maps.call_count)
    assert await service.research_initial(request) == result
    assert before == (model.call_count, maps.call_count)
    await service.aclose()


async def test_unknown_zero_admission_does_not_loosen_historical_budget_models():
    from pydantic import ValidationError
    from app.candidate_research.artifacts import ArtifactResearchBudgetSummarySnapshot, CorrectableResearchBudgetSnapshot
    from app.candidate_research.models import ResearchBudgetSummary
    request, model, _, _, service = setup()
    model.responses.append(rejected())
    result = await service.research_initial(request)
    value = result.budgets.model_dump(mode="json")
    value.update(semantic_invocations_consumed=0, known_provider_attempts=0, usage_complete=False,
                 provider_attempts_complete=False, known_usage={key: 0 for key in value["known_usage"]})
    CorrectableResearchBudgetSnapshot.model_validate_json(json.dumps(value), strict=True)
    ResearchBudgetSummary.model_validate_json(json.dumps(value), strict=True)
    value.pop("correction_limit")
    value.pop("corrections_consumed")
    for model_type in (ArtifactResearchBudgetSummarySnapshot, ResearchBudgetSummary):
        with pytest.raises(ValidationError):
            model_type.model_validate_json(json.dumps(value), strict=True)
    await service.aclose()


async def test_budget_read_failure_preserves_paid_correction_and_known_usage():
    from app.workflow_contracts import ExecutionBudgetError
    request, model, maps, _, service = setup()
    delegate = service._budget

    class FailingSnapshot:
        async def consume(self, request):
            return await delegate.consume(request)

        async def get_snapshot(self, *, key):
            if model.call_count >= 2:
                raise ExecutionBudgetError()
            return await delegate.get_snapshot(key=key)

    service._budget = FailingSnapshot()
    model.responses.extend([failure(), action(model)])
    result = await service.research_initial(request)
    assert result.outcome_type == "failed" and result.failure.code.value == "budget_error"
    assert result.budgets.semantic_invocations_consumed == 2
    assert result.budgets.corrections_consumed == 1
    assert result.budgets.known_usage.total_tokens == 2
    assert not result.budgets.usage_complete and not result.budgets.provider_attempts_complete
    assert model.call_count == 2 and maps.call_count == 0
    await service.aclose()


@pytest.mark.parametrize("mutation", ["erase", "invent_complete", "invent_incomplete"])
async def test_failed_invocation_chain_cannot_erase_or_invent_known_usage(mutation):
    request, model, maps, repo, service = setup()
    model.responses.extend([action(model), failure("timeout")])
    maps.responses.append(search_no_result())
    result = await service.research_initial(request)
    assert result.outcome_type == "failed"
    artifact, sources = await audited(repo, repo.refs[request.operation_plan.terminal_successor_operation_id])
    tree = artifact.model_dump(mode="json")
    for budget in (tree["record"]["budgets"], tree["record"]["state"]["budgets"]):
        if mutation == "erase":
            budget["known_provider_attempts"] = 0
            budget["known_usage"] = {key: 0 for key in budget["known_usage"]}
        else:
            budget["known_usage"] = {key: value * 5000 for key, value in budget["known_usage"].items()}
            if mutation == "invent_complete":
                budget["known_provider_attempts"] = budget["semantic_invocations_consumed"]
                budget["usage_complete"] = budget["provider_attempts_complete"] = True
    forged = CandidateResearchArtifact.model_validate_json(json.dumps(tree), strict=True)
    with pytest.raises(ValueError):
        validate_qualification_sources(forged, sources)
    await service.aclose()


async def test_rehashed_judgment_with_possible_duplicate_cannot_authorize_correction():
    request, model, maps, repo, service = setup(slots=3)
    model.responses.extend([action(model), structured(empty_patch()), lambda _: legal_judgment(model),
                            rejected(), rejected()])
    maps.responses.append(search_success("verified"))
    result = await service.research_initial(request)
    assert result.outcome_type == "incomplete", getattr(result, "failure", None)
    artifact, sources = await audited(repo, result.candidate_set_ref)
    ref = next(ref for ref in artifact.invocation_attempt_refs
               if sources[ref.artifact_id].content.kind == "judgment")
    value = sources[ref.artifact_id].content.model_dump(mode="json")
    assert value["result"]["record"]["record_type"] == "controller_rejected"
    value["result"]["record"]["metadata"]["possible_duplicate"] = True
    forged, sources = reseal(artifact, sources, {ref.artifact_id: value})
    with pytest.raises(ValueError):
        validate_qualification_sources(forged, sources)
    await service.aclose()
async def test_uncertain_judgment_rejection_remains_exactly_replayable_without_correction():
    request, model, maps, repo, service = setup()
    response = structured(empty_patch())
    response = response.model_copy(update={"metadata": response.metadata.model_copy(
        update={"possible_duplicate": True, "provider_attempts": 2})})
    model.responses.extend([action(model), response])
    maps.responses.append(search_success("verified"))
    result = await service.research_initial(request)
    assert result.outcome_type == "failed"
    assert result.failure.code.value in {"judgment_patch_invalid", "judgment_required_missing"}
    assert result.budgets.corrections_consumed == 0
    assert (model.call_count, maps.call_count) == (2, 1)
    assert attempts(repo)[-1]["recovery_code"] is None
    await audited(repo, repo.refs[request.operation_plan.terminal_successor_operation_id])
    assert await service.research_initial(request) == result
    assert (model.call_count, maps.call_count) == (2, 1)
    await service.aclose()


@pytest.mark.parametrize("claim", ["complete", "excess_corrections", "lower_bound"])
async def test_failed_without_state_cannot_claim_unproven_complete_usage(claim):
    request, model, _, repo, service = setup()
    model.responses.append(failure("timeout"))
    result = await service.research_initial(request)
    artifact, _ = await audited(repo, repo.refs[request.operation_plan.terminal_successor_operation_id])
    value = artifact.model_dump(mode="json")
    value["record"]["state"] = None
    value["invocation_attempt_refs"] = []
    value["qualification_state"] = {"assessments": [], "steps": []}
    budget = value["record"]["budgets"]
    if claim == "complete":
        budget.update(known_provider_attempts=1, provider_attempts_complete=True, usage_complete=True)
    elif claim == "excess_corrections":
        budget["corrections_consumed"] = 2
    if claim == "lower_bound":
        forged = CandidateResearchArtifact.model_validate_json(json.dumps(value), strict=True)
        validate_qualification_sources(forged, {})
    else:
        with pytest.raises(ValueError):
            forged = CandidateResearchArtifact.model_validate_json(json.dumps(value), strict=True)
            validate_qualification_sources(forged, {})
    await service.aclose()
async def test_public_terminal_replay_rejects_unproven_usage_without_state():
    from app.candidate_research.fingerprints import content_digest, canonical_json_bytes
    from tests.support.candidate_research_fakes import FakeBudgetManager
    request, model, maps, repo, service = setup()
    model.responses.append(failure("timeout"))
    result = await service.research_initial(request)
    ref = repo.refs[request.operation_plan.terminal_successor_operation_id]
    artifact, _ = await audited(repo, ref)
    value = artifact.model_dump(mode="json")
    value["record"]["state"] = None
    value["invocation_attempt_refs"] = []
    value["qualification_state"] = {"assessments": [], "steps": []}
    value["record"]["budgets"].update(known_provider_attempts=1, provider_attempts_complete=True, usage_complete=True)
    forged = CandidateResearchArtifact.model_validate_json(json.dumps(value), strict=True)
    forged_ref = ref.model_copy(update={"content_hash": content_digest(forged), "size_bytes": len(canonical_json_bytes(forged))})
    repo.seed(forged_ref, forged)
    await service.aclose()
    runtime = FakeRuntime()
    replay = CandidateResearchService(runtime=runtime, map_adapter=maps, artifact_repository=repo,
        budget_manager=FakeBudgetManager(request.operation_plan.run_id))
    writes = len(repo.put_requests)
    rejected = await replay.research_initial(request)
    assert rejected.outcome_type == "failed" and rejected.failure.code.value == "operation_result_indeterminate"
    assert rejected.possible_duplicate
    assert runtime.start_count == 0 and maps.call_count == 0
    assert model.call_count == 1 and len(repo.put_requests) == writes
    assert repo.refs[request.operation_plan.terminal_successor_operation_id] == forged_ref
    await replay.aclose()