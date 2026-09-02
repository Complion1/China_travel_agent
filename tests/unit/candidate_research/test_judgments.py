import pytest

from app.candidate_research import (
    EvidenceHandle, JudgmentPatch, RoleJudgmentUpdate, ResearchRole,
    RoleVerdict, RoleReasonCode,
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


def test_handle_visibility_fresh_validation_and_zero_based_directory():
    patch = validate_judgment_patch(patch=_patch(), candidate_handles=("c:0",), evidence_handles=("e:0",))
    assert patch.role_updates[0].candidate_handle == "c:0"
    assert evidence_handle_indexes((EvidenceHandle(handle_id="e:0"),), ("e:0",)) == (0,)
    with pytest.raises(ValueError):
        validate_judgment_patch(patch=_patch(handle="c:9"), candidate_handles=("c:0",), evidence_handles=("e:0",))
    with pytest.raises(ValueError):
        validate_judgment_patch(patch=_patch(evidence="e:48"), candidate_handles=("c:0",), evidence_handles=("e:48",))


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
        validate_judgment_patch(patch=_patch(), candidate_handles=("c:0", "c:0"), evidence_handles=("e:0",))


@pytest.mark.parametrize("candidate_handles,evidence_handles", [((), ("e:0",)), (("c:0",), ()), (("c:0",), ("e:0", "e:0"))])
def test_judgment_handle_directories_reject_missing_or_duplicate_visibility(candidate_handles, evidence_handles):
    with pytest.raises(ValueError):
        validate_judgment_patch(patch=_patch(), candidate_handles=candidate_handles, evidence_handles=evidence_handles)


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
