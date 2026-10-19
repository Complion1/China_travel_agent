"""当前轮纠错的准入与不可变调用来源核验。"""
from __future__ import annotations

from dataclasses import dataclass

from app.workflow_contracts import ArtifactReadResult
from .artifacts import (
    CandidateResearchArtifact, CandidateResearchInvocationAttemptArtifact,
    action_artifact_model, judgment_artifact_model,
)
from .fingerprints import canonical_json_bytes, content_digest


POLICY_CODES = frozenset({"highest_priority_objective_ignored", "objective_parameters", "duplicate",
                          "details_already_requested", "no_useful_action_invalid"})
OUTPUT_CODES = frozenset({"structured_output_invalid", "output_truncated"})
JUDGMENT_CODES = frozenset({"judgment_evidence_mismatch", "judgment_required_missing"})


@dataclass(frozen=True, slots=True)
class CorrectionFeedback:
    code: str
    recovery_number: int


def correction_stop(*, used: int, semantic_used: int, kind: str):
    from .models import CandidateResearchStopReason
    if used >= 2:
        return CandidateResearchStopReason.CORRECTION_LIMIT_REACHED
    needed = 2 if kind == "action" else 1
    if 12 - semantic_used < needed:
        return CandidateResearchStopReason.SEMANTIC_BUDGET_INSUFFICIENT
    return None


def validate_invocation_attempt_sources(artifact, sources):
    """只接受精确来源；拒绝记录不会为候选提供事实。"""
    if type(artifact) is not CandidateResearchArtifact:
        raise ValueError("invocation audit requires candidate set version 5")
    state = artifact.record.state
    if state is None:
        if artifact.invocation_attempt_refs:
            raise ValueError("attempt directory requires a candidate state")
        budget = artifact.record.budgets
        if budget.semantic_invocations_consumed and (budget.provider_attempts_complete or budget.usage_complete):
            raise ValueError("failed research without invocation sources cannot claim complete usage")
        return
    failed = artifact.record.record_type == "failed"

    def source(ref, model):
        saved = sources.get(ref.artifact_id)
        if not isinstance(saved, ArtifactReadResult) or saved.ref != ref or type(saved.content) is not model:
            raise ValueError("invocation source identity mismatch")
        item = model.model_validate_json(saved.content.model_dump_json(), strict=True)
        if (item.run_id != artifact.run_id or item.schema_version != ref.schema_version
                or content_digest(item) != ref.content_hash or len(canonical_json_bytes(item)) != ref.size_bytes):
            raise ValueError("invocation source content mismatch")
        return item

    attempts, by_main = {}, {}
    semantic, corrections, uncertain = 0, 0, False
    provider_attempts, usage = 0, {}
    metadata_complete = True
    for ref in artifact.invocation_attempt_refs:
        item = source(ref, CandidateResearchInvocationAttemptArtifact)
        attempts[ref.artifact_id] = item
        group = by_main.setdefault(item.main_operation_id, [])
        if item.attempt_index != len(group) or item.trigger_ref != (group[-1][0] if group else None):
            raise ValueError("invocation attempt chain order mismatch")
        if group and (item.kind, item.iteration_index, item.invocation_slot_index, item.mode) != (
                group[0][1].kind, group[0][1].iteration_index, group[0][1].invocation_slot_index, group[0][1].mode):
            raise ValueError("invocation attempt chain scope mismatch")
        if group and group[-1][1].recovery_code is None:
            raise ValueError("correction requires a recoverable rejected invocation")
        group.append((ref, item))
        if uncertain:
            raise ValueError("an uncertain invocation must be the final recorded invocation")
        if item.semantic_index != semantic:
            raise ValueError("invocation semantic index mismatch")
        if item.semantic_cost is None:
            uncertain = True
            if not failed:
                raise ValueError("normal research cannot retain uncertain invocations")
        else:
            semantic += item.semantic_cost
        if item.is_correction:
            if item.correction_number != corrections + 1:
                raise ValueError("global correction numbers must be consecutive")
            if item.semantic_cost == 1:
                corrections += 1
        record = getattr(item.result, "record", None)
        metadata = getattr(record, "metadata", None) if record is not None else item.result.metadata
        if metadata is not None:
            provider_attempts += metadata.provider_attempts
            for name, value in metadata.usage.model_dump().items():
                usage[name] = usage.get(name, 0) + value
        elif item.semantic_cost != 0:
            metadata_complete = False
        if record is not None and record.record_type == "llm_failure":
            expected_cost = 0 if record.error.code.value in {"schema_unsupported", "input_too_large", "quota_exhausted"} else 1
            if item.semantic_cost != expected_cost:
                raise ValueError("LLM failure admission differs from its public contract")
        if item.recovery_code is not None:
            if record is None or record.record_type == "accepted":
                raise ValueError("only rejected model results may allow correction")
            if record.record_type == "llm_failure":
                if record.error.possible_duplicate or record.error.code.value not in OUTPUT_CODES or item.recovery_code != record.error.code.value:
                    raise ValueError("uncertain or provider failures cannot be corrected")
            elif item.kind == "action":
                if record.record_type != "policy_rejected" or item.recovery_code != record.reason_code:
                    raise ValueError("action correction code mismatch")
            elif (record.record_type != "controller_rejected" or item.recovery_code not in JUDGMENT_CODES
                  or record.code not in {"judgment_patch_invalid", "judgment_required_missing"}
                  or record.metadata.possible_duplicate):
                raise ValueError("judgment correction code mismatch")

    covered = set()
    for refs, selector, kind in ((state.model_action_refs, action_artifact_model, "action"),
                                  (state.model_judgment_refs, judgment_artifact_model, "judgment")):
        for ref in refs:
            main = source(ref, selector(ref.schema_version))
            group = by_main.get(ref.operation_id, [])
            if tuple(r for r, _ in group) != main.attempt_refs or not group:
                raise ValueError("main record does not retain its complete attempt chain")
            final = group[-1][1]
            if final.kind != kind or final.iteration_index != main.iteration_index or final.invocation_slot_index != main.invocation_slot_index or final.mode != main.mode:
                raise ValueError("main record attempt scope mismatch")
            if final.result.result_type != kind + "_result" or final.result.record != main.result:
                raise ValueError("main record differs from its final invocation result")
            expected_index = max(0, final.semantic_index + (final.semantic_cost or 0) - 1)
            if main.invocation_index != expected_index:
                raise ValueError("main record invocation index mismatch")
            covered.update(r.artifact_id for r, _ in group)
    if not failed and covered != set(attempts):
        raise ValueError("normal research has an invocation without a final main record")
    budget = state.budgets
    if not failed:
        if semantic != budget.semantic_invocations_consumed or corrections != budget.corrections_consumed:
            raise ValueError("research invocation consumption differs from its sources")
        if provider_attempts != budget.known_provider_attempts or any(
                usage.get(name, 0) != value for name, value in budget.known_usage.model_dump().items()):
            raise ValueError("known invocation usage differs from its sources")
        if not metadata_complete and (budget.provider_attempts_complete or budget.usage_complete):
            raise ValueError("invocation metadata cannot become complete after a failed call")
        for group in by_main.values():
            final = group[-1][1]
            if final.recovery_code is not None:
                stopped = correction_stop(used=corrections, semantic_used=semantic, kind=final.kind)
                if stopped is None:
                    raise ValueError("recoverable final rejection still has a correction allowance")
                if artifact.record.record_type == "incomplete" and stopped not in artifact.record.stop_reasons:
                    raise ValueError("final correction rejection requires its actual stop reason")
    else:
        if semantic > budget.semantic_invocations_consumed or corrections > budget.corrections_consumed:
            raise ValueError("failed research consumption is below its proven invocation sources")
        known_usage = budget.known_usage.model_dump()
        if provider_attempts > budget.known_provider_attempts or any(
                usage.get(name, 0) > value for name, value in known_usage.items()):
            raise ValueError("failed research usage is below its proven invocation sources")
        all_calls_recorded = not uncertain and semantic == budget.semantic_invocations_consumed
        if all_calls_recorded and corrections != budget.corrections_consumed:
            raise ValueError("failed research corrections differ from its complete invocation directory")
        if all_calls_recorded and (provider_attempts != budget.known_provider_attempts or any(
                usage.get(name, 0) != value for name, value in known_usage.items())):
            raise ValueError("failed research known usage differs from its complete invocation directory")
        if (not all_calls_recorded or not metadata_complete) and (
                budget.provider_attempts_complete or budget.usage_complete):
            raise ValueError("failed research cannot claim complete metadata for unrecorded or failed invocations")
    if corrections > 2:
        raise ValueError("shared correction budget exceeded")
    if uncertain and (budget.provider_attempts_complete or budget.usage_complete):
        raise ValueError("uncertain invocations require incomplete usage flags")


__all__ = ("validate_invocation_attempt_sources",)
