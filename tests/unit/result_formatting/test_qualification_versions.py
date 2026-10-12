"""四版候选集按精确模型读取，历史内容不补造资格或覆盖。"""

import hashlib
import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from app.candidate_research import (CandidateResearchArtifact, CandidateResearchLegacyArtifact,
    CandidateResearchV3Artifact, CandidateResearchModelActionArtifact, CandidateResearchModelJudgmentArtifact)
from app.candidate_research.qualification import (observation_context, candidate_observation,
    evidence_digest, validate_qualification_sources)
from app.result_formatting.projection import project_result, ProjectionFailure
from app.workflow_contracts import ArtifactReadResult, OperationId, WorkflowPhase
from tests.unit.result_formatting.fakes import make_scenario


def encoded(model):
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def replace_refs(value, refs):
    if isinstance(value, dict):
        if "artifact_id" in value and value["artifact_id"] in refs:
            return refs[value["artifact_id"]].model_dump(mode="json")
        return {key: replace_refs(item, refs) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_refs(item, refs) for item in value]
    return value


def add_action_source(data, evidence):
    """新版兼容性场景保存地图批次实际对应的行动来源。"""
    state = data["record"]["state"]
    observations = [evidence[UUID(ref["artifact_id"])] for ref in state["observation_refs"]]
    actions, dispositions = [], []
    for read in observations:
        observation = read.content
        ordinal = observation.action_ordinal
        if observation.tool.value == "search_places":
            actions.append(dict(action_type="search_places", ordinal=ordinal, objective_id=f"objective:search:{ordinal}",
                query="合成地点", tag=None, area_mode="city", anchor=None, radius_km=None, interest=None, meal_intent=None))
        else:
            actions.append(dict(action_type="get_place_details", ordinal=ordinal,
                objective_id=f"objective:details:{ordinal}", uid=observation.result.details.uid))
        dispositions.append(dict(action_ordinal=ordinal, action_fingerprint=observation.action_fingerprint,
                                 disposition="accepted", reused_observation_ref=None))
    action = CandidateResearchModelActionArtifact.model_validate_json(json.dumps(dict(
        schema_version="2.0", run_id=data["run_id"], mode="initial", invocation_index=0,
        invocation_slot_index=0, iteration_index=0, prompt_version="action-v2",
        input_digest="sha256:" + "b" * 64,
        prompt_sha256="sha256:" + "a" * 64, result=dict(record_type="accepted",
            output=dict(outcome_type="proposed_actions", actions=actions,
                effective_interests=state["effective_interests"]), action_dispositions=dispositions,
            metadata=dict(role="candidate_research", model_name="deepseek-v4-flash", finish_reason="stop",
                provider_attempts=1, possible_duplicate=False, usage=dict(prompt_tokens=0, completion_tokens=0,
                    total_tokens=0, reasoning_tokens=0, prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0))))), strict=True)
    payload = encoded(action)
    ref = observations[-1].ref.model_copy(update=dict(artifact_id=uuid4(),
        artifact_type="candidate_research.model_action", schema_version="2.0",
        operation_id=OperationId(run_id=action.run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH,
            logical_step="qualification_action_0"), content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload)))
    evidence[ref.artifact_id] = ArtifactReadResult(ref=ref, content=action)
    state["model_action_refs"] = [ref.model_dump(mode="json")]
    return ref


def add_judgment_source(data, evidence):
    """兼容性场景显式保存判断来源，摘要取自候选自身原观察。"""
    state = data["record"]["state"]
    observations = [evidence[UUID(ref["artifact_id"])] for ref in state["observation_refs"]]
    required, windows, roles, interests, meals, receipts = [], [], [], [], [], []
    for candidate in state["candidates"]:
        ordinal = candidate["admission_ordinal"]
        indexes = candidate["observation_indexes"]
        digest = evidence_digest(tuple(candidate_observation(
            observation_context(index, observations[index].content), {candidate["core"]["uid"]})
            for index in indexes))
        windows.append(dict(candidate_ordinal=ordinal, evidence_indexes=indexes, evidence_digest=digest))
        for kind, target, condition in [("role", item["role"], "always") for item in candidate["roles"]] + [
                ("meal", meal, "restaurant") for meal in ("lunch", "dinner")]:
            required.append(dict(candidate_ordinal=ordinal, kind=kind, target=target, when_supported=condition))
            receipts.append(dict(candidate_ordinal=ordinal, kind=kind, target=target,
                evidence_indexes=indexes, evidence_digest=digest, verdict="supported" if kind == "role" else "confirmed"))
        roles.extend(dict(candidate_ordinal=ordinal, **item) for item in candidate["roles"])
        for interest in candidate["interests"]:
            interests.append(dict(candidate_ordinal=ordinal, interest=interest, verdict="confirmed",
                reason_code="name_or_category_match", evidence_indexes=indexes[:1]))
            receipts.append(dict(candidate_ordinal=ordinal, kind="interest", target=interest,
                evidence_indexes=indexes, evidence_digest=digest, verdict="confirmed"))
        meals.extend(dict(candidate_ordinal=ordinal, **item) for item in candidate["meal_assessments"])
    judgment = CandidateResearchModelJudgmentArtifact.model_validate_json(json.dumps(dict(
        schema_version="3.0", run_id=data["run_id"], mode="initial", invocation_index=1,
        invocation_slot_index=0, iteration_index=0, prompt_version="observation-v4",
        prompt_sha256="sha256:4c00fe74f0e47390e9bddfa3d93251cca732a44de9376b485eb2eb11ec7d54cb",
        evidence_refs=state["observation_refs"], job=dict(step_kind="search_and_judge", iteration_index=0,
            candidate_ordinals=[item["admission_ordinal"] for item in state["candidates"]],
            required=required, evidence_windows=windows), result=dict(record_type="accepted",
            patch=dict(outcome_type="judgment_patch", role_updates=roles, interest_updates=interests,
                meal_updates=meals, requirement_group_updates=[]), metadata=dict(role="candidate_research",
                model_name="deepseek-v4-flash", finish_reason="stop", provider_attempts=1, possible_duplicate=False,
                usage=dict(prompt_tokens=0, completion_tokens=0, total_tokens=0, reasoning_tokens=0,
                    prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0))))), strict=True)
    payload = encoded(judgment)
    ref = observations[-1].ref.model_copy(update=dict(artifact_id=uuid4(),
        artifact_type="candidate_research.model_judgment", schema_version="3.0",
        operation_id=OperationId(run_id=judgment.run_id, phase=WorkflowPhase.CANDIDATE_RESEARCH,
            logical_step="qualification_judgment_0"), content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload)))
    evidence[ref.artifact_id] = ArtifactReadResult(ref=ref, content=judgment)
    state["model_judgment_refs"] = [ref.model_dump(mode="json")]
    receipts.sort(key=lambda item: ({"role": 0, "interest": 1, "meal": 2}[item["kind"]], item["candidate_ordinal"]))
    data["qualification_state"] = dict(assessments=receipts, steps=[dict(step_kind="search_and_judge",
        iteration_index=0, action_ref=state["model_action_refs"][0], observation_refs=state["observation_refs"],
        judgment_ref=ref.model_dump(mode="json"))])
    return ref


@pytest.mark.parametrize("version", ["1.0", "2.0", "3.0", "4.0"])
async def test_exact_candidate_version_projection_preserves_canonical_bytes_and_hash(version):
    await version_scenario(version)


async def version_scenario(version, mutation=None, source_error=None):
    scenario = await make_scenario(planning_version="3.0")
    baseline = project_result(scenario.request, scenario.itinerary, scenario.evidence)
    original = {key: encoded(read.content) for key, read in scenario.evidence.items()}
    evidence, refs = dict(scenario.evidence), {}

    def update(read, data, model=None):
        model = model or type(read.content)
        content = model.model_validate_json(json.dumps(data), strict=True)
        payload = encoded(content)
        ref = read.ref.model_copy(update=dict(schema_version=content.schema_version,
            content_hash="sha256:" + hashlib.sha256(payload).hexdigest(), size_bytes=len(payload)))
        refs[str(ref.artifact_id)] = ref
        evidence[ref.artifact_id] = ArtifactReadResult(ref=ref, content=content)

    if version != "1.0":
        current = evidence[scenario.refs["candidate_set"].artifact_id]
        data = current.content.model_dump(mode="json")
        data["schema_version"] = version
        if version in {"3.0", "4.0"}:
            data["correction_state"] = []
        if version == "4.0":
            data["qualification_state"] = dict(assessments=[], steps=[])
        for candidate in data["record"]["state"]["candidates"]:
            candidate.update(schema_version="2.0", meal_assessments=[
                dict(meal_intent=meal, verdict="confirmed", reason_code="targeted_meal_search",
                     evidence_indexes=candidate["observation_indexes"][:1]) for meal in ("lunch", "dinner")])
        if version == "4.0":
            action_ref = add_action_source(data, evidence)
            judgment_ref = add_judgment_source(data, evidence)
            for budgets in (data["record"]["budgets"], data["record"]["state"]["budgets"]):
                budgets.update(semantic_invocations_consumed=2, known_provider_attempts=2)
            if mutation == "missing_judgment":
                evidence.pop(judgment_ref.artifact_id)
            elif mutation == "stripped_actions":
                data["record"]["state"]["model_action_refs"] = []
                data["qualification_state"]["steps"][0]["action_ref"] = None
            elif mutation in {"action_not_accepted", "wrong_action_fingerprint", "wrong_action_tool"}:
                read = evidence[action_ref.artifact_id]
                action_data = read.content.model_dump(mode="json")
                if mutation == "action_not_accepted":
                    action_data["result"] = dict(record_type="controller_rejected", code="action_batch_invalid",
                        output=action_data["result"]["output"], metadata=action_data["result"]["metadata"])
                elif mutation == "wrong_action_fingerprint":
                    action_data["result"]["action_dispositions"][0]["action_fingerprint"] = "sha256:" + "f" * 64
                else:
                    action_data["result"]["output"]["actions"][0] = dict(action_type="get_place_details",
                        ordinal=0, objective_id="objective:details:0", uid="candidate-0")
                update(read, action_data)
                data = replace_refs(data, refs)
            elif mutation == "stripped_judgments":
                state = data["record"]["state"]
                state["model_judgment_refs"] = []
                data["qualification_state"]["assessments"] = []
                data["qualification_state"]["steps"][0]["judgment_ref"] = None
            elif mutation in {"missing_role_source", "missing_meal_source", "missing_interest_source"}:
                kind = mutation.removeprefix("missing_").removesuffix("_source")
                field = {"role": "role_updates", "meal": "meal_updates", "interest": "interest_updates"}[kind]
                read = evidence[judgment_ref.artifact_id]
                judgment_data = read.content.model_dump(mode="json")
                judgment_data["result"]["patch"][field] = []
                judgment_data["job"]["required"] = [item for item in judgment_data["job"]["required"]
                                                    if item["kind"] != kind]
                data["qualification_state"]["assessments"] = [
                    item for item in data["qualification_state"]["assessments"] if item["kind"] != kind]
                update(read, judgment_data)
                data = replace_refs(data, refs)
            elif mutation == "wrong_step":
                data["qualification_state"]["steps"][0]["step_kind"] = "judge_existing"
                data["qualification_state"]["steps"][0]["action_ref"] = None
                data["qualification_state"]["steps"][0]["observation_refs"] = []
            elif mutation == "wrong_digest":
                read = evidence[judgment_ref.artifact_id]
                judgment_data = read.content.model_dump(mode="json")
                judgment_data["job"]["evidence_windows"][0]["evidence_digest"] = "sha256:" + "f" * 64
                update(read, judgment_data)
                data = replace_refs(data, refs)
            elif mutation == "forged_receipt":
                data["qualification_state"]["assessments"][0]["verdict"] = "unresolved"
            elif mutation in {"search_candidate_absent", "search_capacity_rejected"}:
                state = data["record"]["state"]
                uid = state["candidates"][1]["core"]["uid"]
                read = evidence[UUID(state["observation_refs"][0]["artifact_id"])]
                observation_data = read.content.model_dump(mode="json")
                result = observation_data["result"]
                if mutation == "search_candidate_absent":
                    kept = [(place, disposition) for place, disposition in zip(
                        result["places"], result["admission_dispositions"]) if place["uid"] != uid]
                    result["places"] = [place for place, _ in kept]
                    result["admission_dispositions"] = [dict(disposition, result_ordinal=index)
                        for index, (_, disposition) in enumerate(kept)]
                else:
                    result["admission_dispositions"][1]["disposition"] = "not_admitted_candidate_capacity"
                update(read, observation_data)
                data = replace_refs(data, refs)
                read = evidence[judgment_ref.artifact_id]
                judgment_data = replace_refs(read.content.model_dump(mode="json"), refs)
                digest = evidence_digest((candidate_observation(observation_context(0,
                    evidence[UUID(state["observation_refs"][0]["artifact_id"])].content), {uid}),))
                judgment_data["job"]["evidence_windows"][1]["evidence_digest"] = digest
                for item in data["qualification_state"]["assessments"]:
                    if item["candidate_ordinal"] == 1:
                        item["evidence_digest"] = digest
                update(read, judgment_data)
                data = replace_refs(data, refs)
            elif mutation in {"cross_candidate_details", "initial_details_only", "initial_core_changed"}:
                candidate = data["record"]["state"]["candidates"][
                    0 if mutation == "initial_details_only" else 1]
                if mutation == "initial_core_changed":
                    candidate["core"]["address"] += "合成身份变化"
                else:
                    candidate["observation_indexes"] = [1] if mutation == "initial_details_only" else [0, 1]
                    for field in ("roles", "meal_assessments"):
                        for item in candidate[field]:
                            item["evidence_indexes"] = [1]
                    read = evidence[judgment_ref.artifact_id]
                    judgment_data = read.content.model_dump(mode="json")
                    observation = evidence[UUID(data["record"]["state"]["observation_refs"][1]["artifact_id"])].content
                    # 攻击者可以重算真实详情摘要；归属核验必须另行拒绝。
                    digest = evidence_digest((observation_context(1, observation),))
                    ordinal = candidate["admission_ordinal"]
                    judgment_data["job"]["evidence_windows"][ordinal].update(
                        evidence_indexes=[1], evidence_digest=digest)
                    for field in ("role_updates", "interest_updates", "meal_updates"):
                        for item in judgment_data["result"]["patch"][field]:
                            if item["candidate_ordinal"] == ordinal:
                                item["evidence_indexes"] = [1]
                    for item in data["qualification_state"]["assessments"]:
                        if item["candidate_ordinal"] == ordinal:
                            item.update(evidence_indexes=[1], evidence_digest=digest)
                    update(read, judgment_data)
                    data = replace_refs(data, refs)
            elif mutation in {"recovered", "recovered_rejected", "wrong_window"}:
                read = evidence[judgment_ref.artifact_id]
                judgment_data = read.content.model_dump(mode="json")
                if mutation == "wrong_window":
                    judgment_data["job"]["evidence_windows"][1]["evidence_indexes"] = [1]
                    for field in ("role_updates", "interest_updates", "meal_updates"):
                        for item in judgment_data["result"]["patch"][field]:
                            if item["candidate_ordinal"] == 1:
                                item["evidence_indexes"] = [1]
                else:
                    final = judgment_data["result"]
                    if mutation == "recovered_rejected":
                        final = dict(record_type="controller_rejected", metadata=final["metadata"],
                            code="judgment_required_missing", proposed_patch=dict(outcome_type="judgment_patch",
                                role_updates=[], interest_updates=[], meal_updates=[], requirement_group_updates=[]))
                    judgment_data["result"] = dict(record_type="recovered", final=final, attempts=[dict(
                        recovery_number=1, invocation_index=1, code="judgment_required_missing",
                        metadata=read.content.result.metadata.model_dump(mode="json"))])
                    judgment_data["invocation_index"] = 2
                    for budgets in (data["record"]["budgets"], data["record"]["state"]["budgets"]):
                        budgets.update(semantic_invocations_consumed=3, known_provider_attempts=3)
                update(read, judgment_data)
                data = replace_refs(data, refs)
        model = (CandidateResearchArtifact if version == "4.0" else
                 CandidateResearchV3Artifact if version == "3.0" else CandidateResearchLegacyArtifact)
        update(current, data, model)
        for read in tuple(evidence.values()):
            if read.ref.artifact_type == "itinerary_planning.model_action":
                data = read.content.model_dump(mode="json")
                data.update(schema_version="7.0", prompt_version="planning-v7")
                update(read, data)
        for read in tuple(evidence.values()):
            if hasattr(read.content, "model_action_ref"):
                update(read, replace_refs(read.content.model_dump(mode="json"), refs))
        data = replace_refs(scenario.itinerary.model_dump(mode="json"), refs)
        data["schema_version"] = "4.0"
        data["directories"]["schema_version"] = "3.0"
        data["record"]["budgets"]["schema_version"] = "4.0"
        update(evidence[scenario.request.itinerary_ref.artifact_id], data)
        read = evidence[scenario.request.itinerary_ref.artifact_id]
        state = scenario.request.state.model_copy(update={"artifact_refs": (read.ref,)})
        request = scenario.request.model_copy(update={"state": state, "itinerary_ref": read.ref})
        scenario = replace(scenario, request=request, itinerary=read.content)
    if source_error is not None:
        current = evidence[scenario.refs["candidate_set"].artifact_id].content
        with pytest.raises(ValueError, match=source_error):
            validate_qualification_sources(current, evidence)
    actual = project_result(scenario.request, scenario.itinerary, evidence)
    assert actual[0].requirement == baseline[0].requirement
    assert actual[1] == baseline[1]
    for read in evidence.values():
        assert read.ref.content_hash == "sha256:" + hashlib.sha256(encoded(read.content)).hexdigest()
    assert original == {key: encoded(read.content) for key, read in scenario.evidence.items()}
    candidate = evidence[scenario.refs["candidate_set"].artifact_id].content
    assert candidate.schema_version == version
    assert hasattr(candidate, "qualification_state") is (version == "4.0")
    return candidate, evidence


@pytest.mark.parametrize("mutation", ["missing_judgment", "wrong_step", "wrong_digest", "forged_receipt",
    "wrong_window", "recovered_rejected", "stripped_judgments", "missing_role_source",
    "missing_meal_source", "missing_interest_source", "stripped_actions", "action_not_accepted",
    "wrong_action_fingerprint", "wrong_action_tool"])
async def test_new_qualification_projection_rejects_unverifiable_sources(mutation):
    with pytest.raises(ProjectionFailure):
        await version_scenario("4.0", mutation)


async def test_recovered_judgment_uses_only_final_accepted_coverage():
    await version_scenario("4.0", "recovered")


@pytest.mark.parametrize("mutation,error", [
    ("cross_candidate_details", "candidate observation identity mismatch"),
    ("initial_details_only", "candidate initial admission source mismatch"),
    ("initial_core_changed", "candidate initial identity differs from search source"),
    ("search_candidate_absent", "candidate search evidence identity mismatch"),
    ("search_capacity_rejected", "candidate search evidence identity mismatch"),
])
async def test_candidate_ownership_cannot_be_forged_by_rehashing_judgments(mutation, error):
    with pytest.raises(ProjectionFailure):
        await version_scenario("4.0", mutation, source_error=error)


@pytest.mark.parametrize("result_kind", ["details_no_result", "map_failure", "unverified_result", "not_issued"])
@pytest.mark.parametrize("foreign_owner", [False, True])
async def test_details_without_identity_are_owned_by_the_accepted_action(result_kind, foreign_owner):
    artifact, evidence = await version_scenario("4.0")
    evidence = dict(evidence)
    data = artifact.model_dump(mode="json")
    state = data["record"]["state"]
    refs = {}

    def rewrite(read, changed):
        content = type(read.content).model_validate_json(json.dumps(changed), strict=True)
        payload = encoded(content)
        ref = read.ref.model_copy(update=dict(content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload)))
        refs[str(ref.artifact_id)] = ref
        evidence[ref.artifact_id] = ArtifactReadResult(ref=ref, content=content)

    read = evidence[artifact.record.state.observation_refs[1].artifact_id]
    observation_data = read.content.model_dump(mode="json")
    result = dict(record_type=result_kind)
    if result_kind == "details_no_result":
        result["provenance"] = observation_data["result"]["provenance"]
    elif result_kind == "not_issued":
        result["reason_code"] = "reservation_barrier_failed"
    else:
        result["capability"] = "place_details"
        if result_kind == "map_failure":
            result["code"] = "timeout_error"
    observation_data["result"] = result
    rewrite(read, observation_data)
    state["candidates"][0]["details"] = dict(
        status="no_result" if result_kind == "details_no_result" else "not_requested",
        observation_index=1 if result_kind == "details_no_result" else None)
    for coverage in (state["coverage"], data["record"]["coverage"]):
        coverage["details_success_count"] = 0
    if result_kind == "not_issued":
        for budgets in (state["budgets"], data["record"]["budgets"]):
            budgets["place_calls_consumed"] = 1
    if foreign_owner:
        state["candidates"][1]["observation_indexes"] = [0, 1]
    data = replace_refs(data, refs)
    read = evidence[artifact.record.state.model_judgment_refs[0].artifact_id]
    judgment_data = replace_refs(read.content.model_dump(mode="json"), refs)
    for window in judgment_data["job"]["evidence_windows"]:
        candidate = data["record"]["state"]["candidates"][window["candidate_ordinal"]]
        indexes = candidate["observation_indexes"]
        digest = evidence_digest(tuple(candidate_observation(observation_context(index,
            evidence[artifact.record.state.observation_refs[index].artifact_id].content),
            {candidate["core"]["uid"]}) for index in indexes))
        window.update(evidence_indexes=indexes, evidence_digest=digest)
        for receipt in data["qualification_state"]["assessments"]:
            if receipt["candidate_ordinal"] == candidate["admission_ordinal"]:
                receipt.update(evidence_indexes=indexes, evidence_digest=digest)
    rewrite(read, judgment_data)
    current = CandidateResearchArtifact.model_validate_json(json.dumps(replace_refs(data, refs)), strict=True)
    if foreign_owner:
        with pytest.raises(ValueError, match="candidate observation identity mismatch"):
            validate_qualification_sources(current, evidence)
    else:
        validate_qualification_sources(current, evidence)
