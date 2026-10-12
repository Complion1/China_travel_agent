"""必要判断工作单、显式覆盖和候选自身证据消费。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.requirements import InterestTag
from app.workflow_contracts import ArtifactRef, ArtifactReadResult

from .fingerprints import content_digest, candidate_handle
from .models import JudgmentPatch, MatchVerdict, MealIntent, ResearchRole, RoleVerdict


MAX_JOB_CANDIDATES = 16
MAX_JOB_KEYS = 256
MAX_CANDIDATE_EVIDENCE = 8
Index = Annotated[int, Field(ge=0, le=47)]
Ordinal = Annotated[int, Field(ge=0, le=127)]
Target = Annotated[str, Field(min_length=1, max_length=128)]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Kind = Literal["role", "interest", "meal", "must_visit", "avoid"]


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RequiredJudgment(_Snapshot):
    candidate_ordinal: Ordinal
    kind: Kind
    target: Target
    when_supported: Literal["always", "any", "attraction", "restaurant"]

    @model_validator(mode="after")
    def _target(self):
        validate_judgment_target(self.kind, self.target)
        return self


class CandidateEvidenceWindow(_Snapshot):
    candidate_ordinal: Ordinal
    evidence_indexes: Annotated[tuple[Index, ...], Field(min_length=1, max_length=8)]
    evidence_digest: Digest

    @model_validator(mode="after")
    def _unique(self):
        if len(set(self.evidence_indexes)) != len(self.evidence_indexes):
            raise ValueError("candidate evidence indexes must be unique")
        return self


class JudgmentJob(_Snapshot):
    step_kind: Literal["search_and_judge", "judge_existing"]
    iteration_index: Annotated[int, Field(ge=0, le=5)]
    candidate_ordinals: Annotated[tuple[Ordinal, ...], Field(max_length=16)]
    required: Annotated[tuple[RequiredJudgment, ...], Field(max_length=256)]
    evidence_windows: Annotated[tuple[CandidateEvidenceWindow, ...], Field(max_length=16)]

    @model_validator(mode="after")
    def _directory(self):
        if len(set(self.candidate_ordinals)) != len(self.candidate_ordinals):
            raise ValueError("job candidates must be unique")
        if tuple(item.candidate_ordinal for item in self.evidence_windows) != self.candidate_ordinals:
            raise ValueError("job evidence must exactly cover selected candidates")
        keys = tuple(judgment_key(item) for item in self.required)
        if len(set(keys)) != len(keys) or any(key[0] not in self.candidate_ordinals for key in keys):
            raise ValueError("required judgments must have unique selected keys")
        return self


class AssessmentReceipt(_Snapshot):
    candidate_ordinal: Ordinal
    kind: Kind
    target: Target
    evidence_indexes: Annotated[tuple[Index, ...], Field(min_length=1, max_length=8)]
    evidence_digest: Digest
    verdict: Literal["supported", "contradicted", "unresolved", "confirmed", "no_match", "ambiguous"]

    @model_validator(mode="after")
    def _judgment(self):
        validate_judgment_target(self.kind, self.target)
        if len(set(self.evidence_indexes)) != len(self.evidence_indexes):
            raise ValueError("assessment evidence indexes must be unique")
        allowed = {"supported", "contradicted", "unresolved"} if self.kind == "role" else {
            "confirmed", "no_match", "ambiguous", "unresolved"}
        if self.verdict not in allowed:
            raise ValueError("assessment verdict must match judgment kind")
        return self


class ResearchStep(_Snapshot):
    step_kind: Literal["search_and_judge", "judge_existing"]
    iteration_index: Annotated[int, Field(ge=0, le=5)]
    action_ref: ArtifactRef | None
    observation_refs: Annotated[tuple[ArtifactRef, ...], Field(max_length=8)]
    judgment_ref: ArtifactRef | None

    @model_validator(mode="after")
    def _kind(self):
        if self.step_kind == "judge_existing" and (self.action_ref is not None or self.observation_refs):
            raise ValueError("judge-only step cannot contain action or map requests")
        for ref, expected in ((self.action_ref, "candidate_research.model_action"),
                              (self.judgment_ref, "candidate_research.model_judgment")):
            if ref is not None and ref.artifact_type != expected:
                raise ValueError("step model reference type mismatch")
        if any(ref.artifact_type != "candidate_research.place_observation" for ref in self.observation_refs):
            raise ValueError("step map references must be place observations")
        return self


class QualificationState(_Snapshot):
    assessments: Annotated[tuple[AssessmentReceipt, ...], Field(max_length=1536)]
    steps: Annotated[tuple[ResearchStep, ...], Field(max_length=6)]

    @model_validator(mode="after")
    def _unique(self):
        keys = tuple((judgment_key(item), item.evidence_digest) for item in self.assessments)
        if len(set(keys)) != len(keys):
            raise ValueError("assessment evidence windows must be unique")
        if len({item.iteration_index for item in self.steps}) != len(self.steps):
            raise ValueError("research steps must have unique iteration indexes")
        return self


class JudgmentRequiredMissing(ValueError):
    def __init__(self, missing: Sequence[RequiredJudgment]):
        super().__init__("required judgments are missing")
        self.missing = tuple(missing)


def judgment_key(item) -> tuple[int, str, str]:
    return item.candidate_ordinal, item.kind, item.target


def validate_judgment_target(kind: str, target: str) -> None:
    enums = {"role": ResearchRole, "meal": MealIntent, "interest": InterestTag}
    if kind in enums:
        enums[kind](target)
    if target != target.strip():
        raise ValueError("judgment target must be normalized")


def supplied_judgments(patch: JudgmentPatch) -> dict[tuple[int, str, str], str]:
    result = {}
    for kind, updates, field in (("role", patch.role_updates, "role"),
                                  ("interest", patch.interest_updates, "interest"),
                                  ("meal", patch.meal_updates, "meal_intent")):
        for update in updates:
            result[(int(update.candidate_handle[2:]), kind, getattr(update, field).value)] = update.verdict.value
    for group in patch.requirement_group_updates:
        exceptions = {item.candidate_handle: item.verdict for item in group.exceptions}
        for handle in group.evaluated_candidate_handles:
            result[(int(handle[2:]), group.requirement_kind, group.requirement_group_id)] = exceptions.get(handle, group.default_verdict).value
    return result


def resolved_judgment_key_count(patch) -> int:
    return (len(patch.role_updates) + len(patch.interest_updates) + len(patch.meal_updates)
            + sum(len(group.evaluated_candidate_ordinals) for group in patch.requirement_group_updates))


def supplied_resolved_judgments(patch) -> dict[tuple[int, str, str], str]:
    result = {}
    for kind, updates, field in (("role", patch.role_updates, "role"),
                                  ("interest", patch.interest_updates, "interest"),
                                  ("meal", patch.meal_updates, "meal_intent")):
        for update in updates:
            result[(update.candidate_ordinal, kind, getattr(update, field).value)] = update.verdict.value
    for group in patch.requirement_group_updates:
        exceptions = {item.candidate_ordinal: item.verdict for item in group.exceptions}
        for ordinal in group.evaluated_candidate_ordinals:
            result[(ordinal, group.requirement_kind, group.group_id)] = exceptions.get(ordinal, group.default_verdict).value
    return result


def required_after_patch(job: JudgmentJob, candidates, patch: JudgmentPatch) -> tuple[RequiredJudgment, ...]:
    """按合并后的用途和排除结果要求判断，明确排除后不补无用标签。"""
    roles = {item.admission_ordinal: {role.role.value for role in item.roles if role.verdict is RoleVerdict.SUPPORTED}
             for item in candidates}
    excluded = {item.admission_ordinal for item in candidates
                if any(match.verdict is MatchVerdict.CONFIRMED
                       for match in getattr(item, "avoid_matches", ()))}
    return required_from_decisions(job, supplied_judgments(patch), roles, excluded)


def required_from_decisions(job, supplied, prior_supported=None, prior_excluded=()) -> tuple[RequiredJudgment, ...]:
    roles = {ordinal: set(values) for ordinal, values in (prior_supported or {}).items()}
    excluded = set(prior_excluded)
    for (ordinal, kind, target), verdict in supplied.items():
        if kind == "role":
            current = roles.setdefault(ordinal, set())
            current.add(target) if verdict == "supported" else current.discard(target)
        elif kind == "avoid" and verdict == "confirmed":
            excluded.add(ordinal)
    result = []
    for item in job.required:
        values = roles.get(item.candidate_ordinal, set())
        if item.candidate_ordinal in excluded and item.when_supported != "always":
            continue
        if (item.when_supported == "always" or item.when_supported == "any" and values
                or item.when_supported in values):
            result.append(item)
    return tuple(result)


def validate_job_coverage(job, supplied, prior_supported=None, prior_excluded=()) -> None:
    if len(supplied) > MAX_JOB_KEYS:
        raise ValueError("a judgment job cannot submit more than 256 distinct keys")
    required = required_from_decisions(job, supplied, prior_supported, prior_excluded)
    missing = tuple(item for item in required if judgment_key(item) not in supplied)
    if missing:
        raise JudgmentRequiredMissing(missing)


def validate_required_judgments(job: JudgmentJob, candidates, patch: JudgmentPatch) -> None:
    supplied = supplied_judgments(patch)
    if len(supplied) > MAX_JOB_KEYS:
        raise ValueError("a judgment job cannot submit more than 256 distinct keys")
    missing = tuple(item for item in required_after_patch(job, candidates, patch)
                    if judgment_key(item) not in supplied)
    if missing:
        raise JudgmentRequiredMissing(missing)


def record_assessments(receipts, job: JudgmentJob, patch: JudgmentPatch) -> tuple[AssessmentReceipt, ...]:
    """只在完整接受后调用，明确未知也记录为已判断。"""
    return _record_decisions(receipts, job, supplied_judgments(patch))


def _record_decisions(receipts, job, decisions):
    windows = {item.candidate_ordinal: item for item in job.evidence_windows}
    result = {(judgment_key(item), item.evidence_digest): item for item in receipts}
    for (ordinal, kind, target), verdict in decisions.items():
        window = windows[ordinal]
        receipt = AssessmentReceipt(candidate_ordinal=ordinal, kind=kind, target=target,
                                    evidence_indexes=window.evidence_indexes,
                                    evidence_digest=window.evidence_digest, verdict=verdict)
        result[((ordinal, kind, target), window.evidence_digest)] = receipt
    return tuple(result.values())


def validate_qualification_sources(artifact, sources) -> None:
    """只读核验新版资格来源，不执行外调，也不修改候选或判断。"""
    from .artifacts import (
        CandidateResearchArtifact, CandidateResearchModelActionArtifact,
        CandidateResearchModelJudgmentArtifact, CandidateResearchPlaceObservationArtifact,
        ModelActionAcceptedRecord, ModelJudgmentAcceptedRecord, unwrap_model_record,
    )
    from .fingerprints import canonical_json_bytes

    if type(artifact) is not CandidateResearchArtifact:
        raise ValueError("qualification source validation requires candidate set version 4")
    if not isinstance(sources, Mapping):
        raise ValueError("qualification source directory mismatch")
    try:
        artifact = CandidateResearchArtifact.model_validate_json(artifact.model_dump_json(), strict=True)
    except (TypeError, ValueError):
        raise ValueError("qualification artifact contract mismatch") from None
    state = getattr(artifact.record, "state", None)
    if state is None:
        return

    def source(ref, model):
        read = sources.get(ref.artifact_id)
        if not isinstance(read, ArtifactReadResult) or read.ref != ref or type(read.content) is not model:
            raise ValueError("qualification source reference mismatch")
        try:
            content = model.model_validate_json(read.content.model_dump_json(), strict=True)
        except (TypeError, ValueError):
            raise ValueError("qualification source contract mismatch") from None
        payload = canonical_json_bytes(content)
        if (content.run_id != artifact.run_id or content.schema_version != ref.schema_version
                or content_digest(content) != ref.content_hash or len(payload) != ref.size_bytes):
            raise ValueError("qualification source integrity mismatch")
        return content

    source_steps = {}
    def step(index):
        return source_steps.setdefault(index, {"action_ref": None, "judgment_ref": None,
            "observation_refs": [], "step_kind": "search_and_judge"})

    observations, place_sources, action_sources, observation_actions = {}, {}, {}, {}
    searches = {item.observation_index: item for item in state.search_coverage.records}
    for index, ref in enumerate(state.observation_refs):
        observation = source(ref, CandidateResearchPlaceObservationArtifact)
        place_sources[index] = observation
        observations[index] = observation_context(index, observation, searches.get(index))
        step(observation.iteration_index)["observation_refs"].append(ref)
    for ref in state.model_action_refs:
        action = source(ref, CandidateResearchModelActionArtifact)
        current = step(action.iteration_index)
        if current["action_ref"] is not None:
            raise ValueError("research iteration has multiple action records")
        current["action_ref"] = ref
        action_sources[action.iteration_index] = action

    for iteration, values in source_steps.items():
        if not values["observation_refs"]:
            continue
        action = action_sources.get(iteration)
        record = unwrap_model_record(action.result) if action is not None else None
        if not isinstance(record, ModelActionAcceptedRecord) or record.output.outcome_type != "proposed_actions":
            raise ValueError("map observations require an accepted action source")
        for ref in values["observation_refs"]:
            index = state.observation_refs.index(ref)
            observation = place_sources[index]
            ordinal = observation.action_ordinal
            if ordinal >= len(record.output.actions):
                raise ValueError("map observation action ordinal mismatch")
            proposed, disposition = record.output.actions[ordinal], record.action_dispositions[ordinal]
            if (observation.invocation_slot_index != action.invocation_slot_index
                    or proposed.action_type != observation.tool.value
                    or disposition.disposition != "accepted"
                    or disposition.action_fingerprint != observation.action_fingerprint):
                raise ValueError("map observation differs from accepted action")
            if (observation.result.record_type == "details_success"
                    and proposed.uid != observation.result.details.uid):
                raise ValueError("map details identity differs from accepted action")
            observation_actions[index] = proposed

    _validate_candidate_observation_ownership(state, place_sources, observation_actions)

    receipts, accepted_patches = (), {}
    supported_roles, avoid_decisions = {}, {}
    last_judgment_iteration = -1
    for ref in state.model_judgment_refs:
        judgment = source(ref, CandidateResearchModelJudgmentArtifact)
        if judgment.iteration_index <= last_judgment_iteration:
            raise ValueError("judgment source directory order mismatch")
        last_judgment_iteration = judgment.iteration_index
        if judgment.evidence_refs != state.observation_refs[:len(judgment.evidence_refs)]:
            raise ValueError("judgment observation directory prefix mismatch")
        current = step(judgment.iteration_index)
        if current["judgment_ref"] is not None:
            raise ValueError("research iteration has multiple judgment records")
        current.update(judgment_ref=ref, step_kind=judgment.job.step_kind)
        record = unwrap_model_record(judgment.result)
        if not isinstance(record, ModelJudgmentAcceptedRecord):
            continue
        accepted_patches[judgment.iteration_index] = record.patch
        decisions = supplied_resolved_judgments(record.patch)
        excluded = {ordinal for ordinal, values in avoid_decisions.items()
                    if "confirmed" in values.values()}
        validate_job_coverage(judgment.job, decisions, supported_roles, excluded)
        for window in judgment.job.evidence_windows:
            if window.candidate_ordinal >= len(state.candidates):
                raise ValueError("judgment candidate outside state directory")
            candidate = state.candidates[window.candidate_ordinal]
            if not set(window.evidence_indexes).issubset(candidate.observation_indexes):
                raise ValueError("judgment window is not candidate-owned")
            facts = tuple(candidate_observation(observations[index], {candidate.core.uid})
                          for index in window.evidence_indexes)
            if evidence_digest(facts) != window.evidence_digest:
                raise ValueError("judgment evidence digest mismatch")
        receipts = _record_decisions(receipts, judgment.job, decisions)
        for (ordinal, kind, target), verdict in decisions.items():
            if kind == "role":
                roles = supported_roles.setdefault(ordinal, set())
                roles.add(target) if verdict == "supported" else roles.discard(target)
            elif kind == "avoid":
                avoid_decisions.setdefault(ordinal, {})[target] = verdict
    if receipts != artifact.qualification_state.assessments:
        raise ValueError("qualification coverage differs from accepted judgments")
    _validate_candidate_conclusions(state, source_steps, observations, accepted_patches)
    saved_steps = {item.iteration_index: item for item in artifact.qualification_state.steps}
    for index, values in source_steps.items():
        expected = ResearchStep(iteration_index=index,
            **{**values, "observation_refs": tuple(values["observation_refs"])})
        if saved_steps.get(index) != expected:
            raise ValueError("research steps differ from actual sources")
    for index, saved in saved_steps.items():
        if index >= state.budgets.iterations_consumed:
            raise ValueError("research step exceeds consumed iteration budget")
        if index not in source_steps and (saved.action_ref or saved.judgment_ref or saved.observation_refs):
            raise ValueError("research step has no source artifacts")


def _validate_candidate_observation_ownership(state, observations, actions) -> None:
    """候选声明的自身目录必须与真实搜索或详情请求的身份一致。"""
    from .artifacts import ArtifactCandidateCoreSnapshot
    from .fingerprints import candidate_core_equal, canonical_json

    for candidate in state.candidates:
        uid = candidate.core.uid
        for index in candidate.observation_indexes:
            observation = observations[index]
            result = observation.result
            if result.record_type == "search_success":
                own = tuple((place, disposition) for place, disposition in zip(
                    result.places, result.admission_dispositions)
                    if place.uid == uid and disposition.disposition != "not_admitted_candidate_capacity")
                if not own:
                    raise ValueError("candidate search evidence identity mismatch")
            elif observation.tool.value == "get_place_details" and actions[index].uid == uid:
                own = ()
            else:
                raise ValueError("candidate observation identity mismatch")
            if index != candidate.observation_indexes[0]:
                continue
            admitted = next((place for place, disposition in own
                             if disposition.disposition == "admitted_new"), None)
            if admitted is None or index != min(candidate.observation_indexes):
                raise ValueError("candidate initial admission source mismatch")
            core = ArtifactCandidateCoreSnapshot.model_validate_json(canonical_json(
                {key: getattr(admitted, key) for key in type(candidate.core).model_fields}), strict=True)
            if not candidate_core_equal(candidate.core, core):
                raise ValueError("candidate initial identity differs from search source")


def _judgment_signature(item):
    return (item.verdict, item.reason_code, item.evidence_indexes)


def _validate_candidate_conclusions(state, steps, observations, patches) -> None:
    """从初始未知和实际来源推导最终标签，不把候选保存的结论当来源。"""
    from .artifacts import (
        ArtifactCandidateCoreSnapshot, ArtifactRoleAssessmentSnapshot,
        ArtifactMealAssessmentSnapshot, ArtifactRequirementMatchSnapshot,
    )
    from .fingerprints import candidate_core_equal, canonical_json
    from .models import RoleReasonCode
    from .purpose import apply_meal_assessment

    roles, meals, requirements, interests = {}, {}, {}, set()
    for candidate in state.candidates:
        ordinal, initial = candidate.admission_ordinal, candidate.observation_indexes[:1]
        for role in ResearchRole:
            roles[(ordinal, role)] = ArtifactRoleAssessmentSnapshot(role=role,
                verdict=RoleVerdict.UNRESOLVED, reason_code=RoleReasonCode.EVIDENCE_INSUFFICIENT,
                evidence_indexes=initial)
        for meal in MealIntent:
            meals[(ordinal, meal)] = ArtifactMealAssessmentSnapshot(meal_intent=meal,
                verdict=MatchVerdict.UNRESOLVED, reason_code="evidence_insufficient", evidence_indexes=initial)
        for group in state.requirement_group_states:
            requirements[(ordinal, group.requirement_kind, group.group_id)] = (
                MatchVerdict.UNRESOLVED, "evidence_insufficient", initial)

    observation_indexes = {ref.artifact_id: index for index, ref in enumerate(state.observation_refs)}
    for iteration, step in sorted(steps.items()):
        # 本轮地图事实先于判断；真实身份冲突只追加冲突原因，不授予资格。
        for ref in step["observation_refs"]:
            index = observation_indexes[ref.artifact_id]
            result = observations[index]["result"]
            places = result.get("places", ()) if result["record_type"] == "search_success" else (
                (result["details"],) if result["record_type"] == "details_success" else ())
            for candidate in state.candidates:
                if index not in candidate.observation_indexes or index == candidate.observation_indexes[0]:
                    continue
                place = next((item for item in places if item["uid"] == candidate.core.uid), None)
                if place is None:
                    continue
                core = ArtifactCandidateCoreSnapshot.model_validate_json(canonical_json(
                    {key: place[key] for key in type(candidate.core).model_fields}), strict=True)
                if candidate_core_equal(candidate.core, core):
                    continue
                for role in ResearchRole:
                    key = (candidate.admission_ordinal, role)
                    previous = roles[key]
                    indexes = tuple(dict.fromkeys((*previous.evidence_indexes, index)))
                    roles[key] = previous.model_copy(update=dict(
                        reason_code=RoleReasonCode.EVIDENCE_CONFLICTED, evidence_indexes=indexes))
        patch = patches.get(iteration)
        if patch is None:
            continue
        for update in patch.role_updates:
            roles[(update.candidate_ordinal, update.role)] = update
        for update in patch.meal_updates:
            key = (update.candidate_ordinal, update.meal_intent)
            meals[key] = apply_meal_assessment(meals[key], update)
        for update in patch.interest_updates:
            key = (update.candidate_ordinal, update.interest)
            interests.add(key) if update.verdict is MatchVerdict.CONFIRMED else interests.discard(key)
        for group in patch.requirement_group_updates:
            exceptions = {item.candidate_ordinal: item for item in group.exceptions}
            for ordinal in group.evaluated_candidate_ordinals:
                item = exceptions.get(ordinal)
                requirements[(ordinal, group.requirement_kind, group.group_id)] = (
                    _judgment_signature(item) if item is not None else
                    (group.default_verdict, group.default_reason_code, group.default_evidence_indexes))

    for candidate in state.candidates:
        ordinal = candidate.admission_ordinal
        if any(_judgment_signature(item) != _judgment_signature(roles[(ordinal, item.role)])
               for item in candidate.roles):
            raise ValueError("candidate roles differ from actual sources")
        if any(_judgment_signature(item) != _judgment_signature(meals[(ordinal, item.meal_intent)])
               for item in candidate.meal_assessments):
            raise ValueError("candidate meal assessments differ from accepted judgments")
        if set(candidate.interests) != {interest for index, interest in interests if index == ordinal}:
            raise ValueError("candidate interests differ from accepted judgments")
        for kind, matches in (("must_visit", candidate.must_matches), ("avoid", candidate.avoid_matches)):
            expected = {group: value for (index, category, group), value in requirements.items()
                        if index == ordinal and category == kind}
            if {item.requirement_group_id: _judgment_signature(item) for item in matches} != expected:
                raise ValueError("candidate requirement matches differ from accepted judgments")


def evidence_digest(facts: Sequence[dict]) -> str:
    """时间戳、观察编号和重复事实不构成新的判断证据。"""
    normalized = {}
    for item in facts:
        result = item["result"]
        search = item.get("accepted_search")
        value = {"accepted_search": None if search is None else {
                     key: search.get(key) for key in ("role", "interest", "meal_intent")},
                 "record_type": result["record_type"],
                 "places": result.get("places", ()), "details": result.get("details")}
        normalized[content_digest(value)] = value
    return content_digest(tuple(normalized[key] for key in sorted(normalized)))


def candidate_observation(observation: dict, uids: set[str]) -> dict:
    """裁剪为选中候选的自身事实，不把其他搜索结果当作证据。"""
    value = {**observation, "result": dict(observation["result"])}
    result = value["result"]
    if result["record_type"] == "search_success":
        result["places"] = tuple(item for item in result["places"] if item["uid"] in uids)
        result.pop("admission_dispositions", None)
    result.pop("provenance", None)
    return value


def observation_context(index: int, artifact, search=None, requirement_group_id=None) -> dict:
    return {
        "handle": f"e:{index}", "tool": artifact.tool.value,
        "accepted_search": None if search is None else {
            "role": search.role.value if search.role else (
                "attraction" if search.objective_kind.value == "interest_coverage" else None),
            "interest": search.interest.value if search.interest else None,
            "meal_intent": search.meal_intent.value if search.meal_intent else None,
            "requirement_group_id": requirement_group_id,
        },
        "result": artifact.result.model_dump(mode="json"),
    }


def candidate_required_keys(candidate, search_records, requirements, interests, must_groups=()) -> tuple[RequiredJudgment, ...]:
    if candidate.state.value in {"excluded", "conflicted"}:
        return ()
    ordinal = candidate.admission_ordinal
    def key(kind, target, condition="always"):
        return RequiredJudgment(candidate_ordinal=ordinal, kind=kind, target=target,
                                when_supported=condition)
    related = tuple(item for item in search_records if item.observation_index in candidate.observation_indexes)
    roles = tuple(dict.fromkeys(item.role for item in related if item.role is not None)) or tuple(ResearchRole)
    if must_groups or any(item.verdict is MatchVerdict.CONFIRMED for item in candidate.must_matches):
        roles = tuple(ResearchRole)
    unresolved_roles = {item.role for item in candidate.roles if item.verdict is RoleVerdict.UNRESOLVED}
    result = [key("role", role.value) for role in roles if role in unresolved_roles]
    result.extend(key("meal", meal.value, "restaurant") for meal in MealIntent)
    current = {item.requirement_group_id: item.verdict for item in (*candidate.must_matches, *candidate.avoid_matches)}
    result.extend(key(kind, group, "any" if kind == "avoid" else "always")
                  for kind, group, _ in requirements if (kind == "avoid" or group in must_groups)
                  and current.get(group) in {MatchVerdict.UNRESOLVED, MatchVerdict.AMBIGUOUS})
    needed_interests = {item.interest for item in related if item.interest in interests}
    if ResearchRole.ATTRACTION in roles or any(item.role is ResearchRole.ATTRACTION
            and item.verdict is RoleVerdict.SUPPORTED for item in candidate.roles):
        needed_interests.update(interests)
    result.extend(key("interest", interest.value, "attraction") for interest in interests
                  if interest in needed_interests and interest not in candidate.interests)
    return tuple(result)


def already_assessed(receipts, key, digest: str) -> bool:
    return any(judgment_key(item) == judgment_key(key) and item.evidence_digest == digest for item in receipts)


def candidate_fact_digests(candidate, observations) -> dict[int, str]:
    return {index: evidence_digest((candidate_observation(observations[index], {candidate.core.uid}),))
            for index in candidate.observation_indexes}


def consumed_facts(candidate, receipts, fact_digests) -> dict[tuple[int, str, str], set[str]]:
    """按判断键重建已展示的规范事实，窗口重新组合不产生新证据。"""
    result = {}
    for receipt in receipts:
        if receipt.candidate_ordinal == candidate.admission_ordinal:
            result.setdefault(judgment_key(receipt), set()).update(
                fact_digests[index] for index in receipt.evidence_indexes)
    return result


def evidence_windows(candidate, observations, required, fact_digests=None):
    """窗口优先详情及目标搜索；较早事实按有限批次补入，重复事实不重开。"""
    targets = {(item.kind, item.target) for item in required}
    def priority(index):
        search = observations[index].get("accepted_search") or {}
        targeted = (search.get("requirement_group_id") in {target for kind, target in targets if kind == "must_visit"}
            or ("meal", search.get("meal_intent")) in targets
            or ("interest", search.get("interest")) in targets)
        return index != candidate.details.observation_index, not targeted, -index
    fact_digests = fact_digests or candidate_fact_digests(candidate, observations)
    representatives = {}
    for index in sorted(candidate.observation_indexes):
        representatives.setdefault(fact_digests[index], index)
    detail_digest = fact_digests.get(candidate.details.observation_index)
    detail_index = representatives.get(detail_digest)
    ordered = tuple(sorted(representatives.values(), key=lambda index: (
        index != detail_index, *priority(index)[1:])))
    if not ordered:
        return
    fixed = ordered[:1] if ordered[0] == detail_index else ()
    rest = ordered[len(fixed):]
    size = MAX_CANDIDATE_EVIDENCE - len(fixed)
    seen = set()
    for start in range(0, max(1, len(rest)), size):
        indexes = fixed + rest[start:start + size]
        facts = tuple(candidate_observation(observations[index], {candidate.core.uid}) for index in indexes)
        digest = evidence_digest(facts)
        if digest not in seen:
            seen.add(digest)
            yield CandidateEvidenceWindow(candidate_ordinal=candidate.admission_ordinal,
                                          evidence_indexes=indexes, evidence_digest=digest)


def potentially_required(candidate, keys):
    supported = {item.role.value for item in candidate.roles if item.verdict is RoleVerdict.SUPPORTED}
    pending_roles = {item.target for item in keys if item.kind == "role"}
    return tuple(item for item in keys if item.when_supported == "always"
                 or item.when_supported == "any" and (supported or pending_roles)
                 or item.when_supported in supported | pending_roles)


def job_has_pending_judgments(job, candidates) -> bool:
    return any(potentially_required(candidate, tuple(item for item in job.required
               if item.candidate_ordinal == candidate.admission_ordinal)) for candidate in candidates)


def relevant_must_groups(candidate, observations, requirements):
    groups = {observations[index]["accepted_search"]["requirement_group_id"]
              for index in candidate.observation_indexes if observations[index].get("accepted_search")}
    groups.update(item["requirement_group_id"] for item in requirements
                  if item["requirement_kind"] == "must_visit" and item["confirmed_name"] == candidate.core.name)
    return groups


def admitted_must_groups(state) -> set[str]:
    from .purpose import candidate_admitted_purposes
    return {match.requirement_group_id for candidate in state.candidates
            if candidate_admitted_purposes(candidate) for match in candidate.must_matches
            if match.verdict is MatchVerdict.CONFIRMED}


def candidate_needed(state, candidate, related_must_groups=(), admitted_must=None):
    """只给仍影响必要供给的候选补判；明确不适用的候选不占后续工作单。"""
    if candidate.state.value in {"excluded", "conflicted"}:
        return False
    admitted_must = admitted_must_groups(state) if admitted_must is None else admitted_must
    if set(related_must_groups) - admitted_must:
        return True
    if any(match.verdict is MatchVerdict.CONFIRMED and match.requirement_group_id not in admitted_must
           for match in candidate.must_matches):
        return True
    related = tuple(item for item in state.search_coverage.records
                    if item.observation_index in candidate.observation_indexes)
    query_roles = {item.role for item in related if item.role is not None} or set(ResearchRole)
    roles = {item.role: item.verdict for item in candidate.roles}
    attraction = roles.get(ResearchRole.ATTRACTION) is RoleVerdict.SUPPORTED or (
        ResearchRole.ATTRACTION in query_roles and roles.get(ResearchRole.ATTRACTION) is RoleVerdict.UNRESOLVED)
    restaurant = roles.get(ResearchRole.RESTAURANT) is RoleVerdict.SUPPORTED or (
        ResearchRole.RESTAURANT in query_roles and roles.get(ResearchRole.RESTAURANT) is RoleVerdict.UNRESOLVED)
    for gap in state.pending_gaps:
        kind = gap.gap_type
        if kind == "avoid_resolution_missing" and candidate.core.uid in gap.candidate_uids:
            return True
        if kind in {"meal_count_shortfall", "meal_coverage_missing"} and restaurant:
            return True
        if kind == "interest_coverage_missing" and attraction:
            return True
        if kind in {"candidate_count_shortfall", "replacement_candidate_needed"} and (
                attraction if gap.role is ResearchRole.ATTRACTION else restaurant):
            return True
    return bool(attraction and (state.counts.attraction_count < state.counts.attraction_target
                or state.coverage.covered_interests != state.effective_interests)
        or restaurant and (state.counts.restaurant_count < state.counts.restaurant_target
                or state.coverage.covered_meals != tuple(MealIntent)))


def build_judgment_context(state, observations, requirements, receipts, *, iteration_index,
                           current_indexes=(), selected_ordinals=None, step_kind="search_and_judge",
                           saved_job=None):
    """搜索和补判共用工作单及证据投影，不重复实现接受规则。"""
    if saved_job is not None:
        selected_ordinals = saved_job.candidate_ordinals
    admitted_must = admitted_must_groups(state)
    if selected_ordinals is None:
        selected_ordinals = tuple(item.admission_ordinal for item in state.candidates
            if (candidate_needed(state, item, relevant_must_groups(item, observations, requirements), admitted_must) if step_kind == "judge_existing" else
                bool(set(item.observation_indexes).intersection(current_indexes))))
    by_ordinal = {item.admission_ordinal: item for item in state.candidates}
    selected = [by_ordinal[index] for index in selected_ordinals]
    if saved_job is None:
        selected.sort(key=lambda item: (not any(match.verdict is MatchVerdict.CONFIRMED
                                               and match.requirement_group_id not in admitted_must
                                               for match in item.must_matches), item.admission_ordinal))
    keys, windows, bounded = [], [], []
    interests = tuple(value for value in state.effective_interests if value not in state.coverage.covered_interests)
    for candidate in selected:
        if len(bounded) == MAX_JOB_CANDIDATES:
            break
        if saved_job is not None:
            window = next(item for item in saved_job.evidence_windows if item.candidate_ordinal == candidate.admission_ordinal)
            needed = [item for item in saved_job.required if item.candidate_ordinal == candidate.admission_ordinal]
            facts = tuple(candidate_observation(observations[index], {candidate.core.uid}) for index in window.evidence_indexes)
            if evidence_digest(facts) != window.evidence_digest:
                raise ValueError("saved work order evidence digest mismatch")
        else:
            specs = tuple((item["requirement_kind"], item["requirement_group_id"], item["requirement_item_ids"])
                          for item in requirements)
            required = candidate_required_keys(candidate, state.search_coverage.records, specs, interests,
                                               relevant_must_groups(candidate, observations, requirements))
            if step_kind == "judge_existing":
                required = potentially_required(candidate, required)
            window, needed = None, []
            fact_digests = candidate_fact_digests(candidate, observations)
            consumed = consumed_facts(candidate, receipts, fact_digests)
            for proposed in evidence_windows(candidate, observations, required, fact_digests):
                facts = {fact_digests[index] for index in proposed.evidence_indexes}
                pending = [item for item in required if facts - consumed.get(judgment_key(item), set())]
                if step_kind == "judge_existing":
                    pending = list(potentially_required(candidate, pending))
                if pending or step_kind == "search_and_judge":
                    window, needed = proposed, pending
                    break
            if window is None:
                continue
        if len(keys) + len(needed) > MAX_JOB_KEYS:
            break
        bounded.append(candidate)
        windows.append(window)
        keys.extend(needed)
    job = saved_job or JudgmentJob(step_kind=step_kind, iteration_index=iteration_index,
        candidate_ordinals=tuple(item.admission_ordinal for item in bounded), required=tuple(keys),
        evidence_windows=tuple(windows))
    uids = {item.core.uid for item in bounded}
    visible = {index for window in windows for index in window.evidence_indexes}
    if not bounded and step_kind == "search_and_judge":
        visible.update(current_indexes)
    candidates = tuple({
        "handle": candidate_handle(item.admission_ordinal), "uid": item.core.uid,
        "name": item.core.name, "address": item.core.address,
        "coordinate": item.core.coordinate.model_dump(mode="json"), "provider_category": item.core.provider_category,
        "evidence_handles": tuple(f"e:{index}" for index in window.evidence_indexes),
        "state": item.state.value, "roles": tuple(value.model_dump(mode="json") for value in item.roles),
        "interests": tuple(value.value for value in item.interests), "meal_intents": tuple(value.value for value in item.meal_intents),
        "meal_assessments": tuple(value.model_dump(mode="json") for value in getattr(item, "meal_assessments", ())),
        "must_matches": tuple(value.model_dump(mode="json") for value in item.must_matches),
        "avoid_matches": tuple(value.model_dump(mode="json") for value in item.avoid_matches),
        "details": item.details.model_dump(mode="json"),
    } for item, window in zip(bounded, windows))
    return job, {"step_kind": job.step_kind, "candidates": candidates, "requirements": requirements,
        "evidence": tuple(candidate_observation(observations[index], uids) for index in sorted(visible)),
        "current_evidence_handles": tuple(f"e:{index}" for index in current_indexes if index in visible),
        "required_judgments": tuple({"candidate_handle": candidate_handle(item.candidate_ordinal),
            "kind": item.kind, "target": item.target, "when_supported": item.when_supported} for item in job.required)}


def qualification_resolution_keys(state) -> set[str]:
    """只记录第一次取得可用用途和必要供给，不给同证据反复改判计进展。"""
    from .purpose import candidate_admitted_purposes
    result = set()
    for candidate in state.candidates:
        purposes = candidate_admitted_purposes(candidate)
        result.update(f"qualification:{candidate.admission_ordinal}:{purpose}" for purpose in purposes)
        if purposes:
            result.update(f"qualification:must:{match.requirement_group_id}" for match in candidate.must_matches
                          if match.verdict is MatchVerdict.CONFIRMED)
    result.update(f"qualification:interest:{value.value}" for value in state.coverage.covered_interests)
    result.update(f"qualification:meal:{value.value}" for value in state.coverage.covered_meals)
    return result
