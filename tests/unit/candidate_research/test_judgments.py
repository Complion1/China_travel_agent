import pytest

from app.candidate_research import (
    EvidenceHandle, JudgmentPatch, MatchVerdict, RequirementGroupJudgmentUpdate,
    RequirementReasonCode, RequirementVerdictException, RoleJudgmentUpdate,
    ResearchRole, RoleVerdict, RoleReasonCode,
)
from app.candidate_research.judgments import (
    apply_judgment_patch, evidence_handle_indexes, evidence_ref_directory, merge_judgment_patch,
    validate_judgment_patch,
)


def _patch(handle="c:0", evidence="e:0", verdict=RoleVerdict.SUPPORTED):
    return JudgmentPatch(
        role_updates=(RoleJudgmentUpdate(candidate_handle=handle, role=ResearchRole.ATTRACTION, verdict=verdict, reason_code=RoleReasonCode.NAME_SEMANTICS, evidence_handles=(EvidenceHandle(handle_id=evidence),)),),
        interest_updates=(), meal_updates=(), requirement_group_updates=(),
    )


def _validate(patch=None, *, candidate_handles=("c:0",), evidence_handles=("e:0",), candidate_evidence_handles=None):
    if candidate_evidence_handles is None:
        candidate_evidence_handles = {handle: evidence_handles for handle in candidate_handles}
    return validate_judgment_patch(
        patch=patch or _patch(),
        candidate_handles=candidate_handles,
        evidence_handles=evidence_handles,
        candidate_evidence_handles=candidate_evidence_handles,
    )


def test_handle_visibility_fresh_validation_and_zero_based_directory():
    patch = _validate()
    assert patch.role_updates[0].candidate_handle == "c:0"
    assert evidence_handle_indexes((EvidenceHandle(handle_id="e:0"),), ("e:0",)) == (0,)
    with pytest.raises(ValueError):
        _validate(_patch(handle="c:9"))
    with pytest.raises(ValueError):
        _validate(_patch(evidence="e:48"), evidence_handles=("e:48",))


def test_merge_updates_existing_semantic_key_without_duplicates():
    current = _patch(verdict=RoleVerdict.SUPPORTED)
    incoming = _patch(verdict=RoleVerdict.CONTRADICTED)
    merged = merge_judgment_patch(current, incoming)
    assert len(merged.role_updates) == 1
    assert merged.role_updates[0].verdict is RoleVerdict.CONTRADICTED
    assert current.role_updates[0].verdict is RoleVerdict.SUPPORTED


def test_evidence_directory_preserves_zero_based_order():
    refs = []
    assert evidence_ref_directory(refs) == {}
    with pytest.raises(ValueError):
        _validate(candidate_handles=("c:0", "c:0"))


@pytest.mark.parametrize("candidate_handles,evidence_handles", [((), ("e:0",)), (("c:0",), ()), (("c:0",), ("e:0", "e:0"))])
def test_judgment_handle_directories_reject_missing_or_duplicate_visibility(candidate_handles, evidence_handles):
    with pytest.raises(ValueError):
        _validate(candidate_handles=candidate_handles, evidence_handles=evidence_handles)


def test_candidate_updates_reject_globally_visible_evidence_owned_by_another_candidate():
    with pytest.raises(ValueError, match="does not belong"):
        _validate(
            _patch(evidence="e:1"),
            candidate_handles=("c:0", "c:1"),
            evidence_handles=("e:0", "e:1"),
            candidate_evidence_handles={"c:0": ("e:0",), "c:1": ("e:1",)},
        )


def test_candidate_evidence_directory_must_cover_candidates_and_global_evidence():
    with pytest.raises(ValueError, match="cover visible candidates"):
        _validate(candidate_evidence_handles={})
    with pytest.raises(ValueError, match="invalid candidate evidence directory"):
        _validate(candidate_evidence_handles={"c:0": ("e:1",)})


def test_requirement_group_exception_uses_its_own_evidence_instead_of_default_evidence():
    patch = JudgmentPatch(
        role_updates=(),
        interest_updates=(),
        meal_updates=(),
        requirement_group_updates=(
            RequirementGroupJudgmentUpdate(
                requirement_group_id="group:1",
                requirement_kind="avoid",
                evaluated_candidate_handles=("c:0", "c:1"),
                default_verdict=MatchVerdict.NO_MATCH,
                default_reason_code=RequirementReasonCode.DETAILS_DISAMBIGUATED,
                default_evidence_handles=(EvidenceHandle(handle_id="e:0"),),
                exceptions=(
                    RequirementVerdictException(
                        candidate_handle="c:1",
                        verdict=MatchVerdict.CONFIRMED,
                        reason_code=RequirementReasonCode.DETAILS_DISAMBIGUATED,
                        evidence_handles=(EvidenceHandle(handle_id="e:1"),),
                    ),
                ),
            ),
        ),
    )

    assert _validate(
        patch,
        candidate_handles=("c:0", "c:1"),
        evidence_handles=("e:0", "e:1"),
        candidate_evidence_handles={"c:0": ("e:0",), "c:1": ("e:1",)},
    ) == patch

    invalid_default = patch.model_copy(
        update={
            "requirement_group_updates": (
                patch.requirement_group_updates[0].model_copy(update={"exceptions": ()}),
            )
        }
    )
    with pytest.raises(ValueError, match="does not belong"):
        _validate(
            invalid_default,
            candidate_handles=("c:0", "c:1"),
            evidence_handles=("e:0", "e:1"),
            candidate_evidence_handles={"c:0": ("e:0",), "c:1": ("e:1",)},
        )


@pytest.mark.parametrize("handle", ("c:1", "c:0"))
def test_evidence_handle_indexes_cover_unknown_and_known_handles(handle):
    if handle == "c:1":
        with pytest.raises(ValueError):
            evidence_handle_indexes((EvidenceHandle(handle_id="e:0"),), ("e:1",))
    else:
        assert evidence_handle_indexes((EvidenceHandle(handle_id="e:0"),), ("e:0", "e:1")) == (0,)


def test_merge_judgment_patch_covers_new_keys_and_noop_identity():
    current = JudgmentPatch(role_updates=(), interest_updates=(), meal_updates=(), requirement_group_updates=())
    incoming = _patch()
    merged = merge_judgment_patch(current, incoming)
    assert merged.role_updates == incoming.role_updates
    same = merge_judgment_patch(merged, incoming)
    assert same.role_updates == merged.role_updates
    assert apply_judgment_patch(current, incoming).role_updates == incoming.role_updates


def test_evidence_ref_directory_keeps_first_duplicate_id_and_rejects_conflicting_refs():
    from datetime import datetime, timezone
    from uuid import uuid4
    from app.workflow_contracts import ArtifactRef, OperationId, WorkflowPhase
    run = uuid4()
    first = ArtifactRef(artifact_id=uuid4(), artifact_type="candidate_research.place_observation", schema_version="1.0", content_hash="sha256:" + "a" * 64, operation_id=OperationId(run_id=run, phase=WorkflowPhase.CANDIDATE_RESEARCH, logical_step="a"), created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), size_bytes=1)
    assert evidence_ref_directory((first,))["e:0"] == first
    assert evidence_ref_directory(()) == {}
