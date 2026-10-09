"""Old canonical bytes stay unchanged; new versions have closed 19/2 bounds."""
import hashlib
import json
from uuid import uuid4

import pytest

from app.workflow_contracts import ArtifactSchemaRegistry, ArtifactRef
from app.itinerary_planning.artifacts import ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS, ItineraryPlanningModelActionArtifact
from app.itinerary_planning import PlanningBudgetSummary
from tests.contract.itinerary_planning.test_artifact_registration import _registered_artifacts, _budget


def new_budget(**changes):
    tree = _budget().model_dump(mode="python")
    tree.update(schema_version="3.0", model_invocation_limit=19, semantic_invocation_limit=19,
        normal_invocation_limit=17, format_repair_limit=2, format_repair_used=0)
    tree.update(changes)
    return PlanningBudgetSummary.model_validate(tree, strict=True)


def test_all_legacy_artifact_bytes_hashes_and_shapes_are_retained():
    registry = ArtifactSchemaRegistry(ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS)
    for kind, artifact, operation, model in _registered_artifacts():
        raw = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ref = ArtifactRef(artifact_id=uuid4(), artifact_type=kind, schema_version=artifact.schema_version,
            content_hash="sha256:"+hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), operation_id=operation,
            created_at=__import__("datetime").datetime(2026,9,3,tzinfo=__import__("datetime").timezone.utc))
        decoded = registry.decode(ref=ref, canonical_bytes=raw, expected_model=model)
        assert json.dumps(decoded.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() == raw
        if kind=="itinerary_planning.itinerary":
            assert "format_repair_used" not in raw.decode()


@pytest.mark.parametrize("changes", [
    {"format_repair_used":3}, {"model_invocations_consumed":20}, {"known_provider_attempts":39},
    {"format_repair_used":1}, {"model_invocation_limit":17}, {"normal_invocation_limit":19},
])
def test_new_budget_rejects_overspending_and_bad_classification(changes):
    with pytest.raises(ValueError):
        new_budget(**changes)


def test_last_index_18_and_full_19_budget_are_legal_only_in_new_version():
    budget = new_budget(preparation_used=2, transport_used=5, submit_repair_used=5, supplement_request_used=5,
        format_repair_used=2, model_invocations_consumed=19, semantic_invocations_consumed=19, known_provider_attempts=38)
    assert budget.model_invocations_consumed == 19
    old = _registered_artifacts()[0][1].model_dump(mode="python")
    current = ItineraryPlanningModelActionArtifact.model_validate({**old, "schema_version":"6.0", "invocation_index":18,
        "prompt_version":"planning-v6", "budget_category":"format_repair"}, strict=True)
    assert current.invocation_index == 18
    for changes in ({"schema_version":"5.0"}, {"prompt_version":"planning-v5"}, {"invocation_index":19}):
        with pytest.raises(ValueError):
            ItineraryPlanningModelActionArtifact.model_validate({**current.model_dump(mode="python"), **changes}, strict=True)


def test_new_root_cannot_silently_wrap_old_state_or_action_directory():
    from app.itinerary_planning import ItineraryPlanningArtifact
    old = _registered_artifacts()[-1][1].model_dump(mode="python")
    assert old["schema_version"] == "2.0"
    with pytest.raises(ValueError):
        ItineraryPlanningArtifact.model_validate({**old, "schema_version": "3.0"}, strict=True)
    budget = new_budget().model_dump(mode="python")
    for required in ("normal_invocation_limit", "format_repair_limit", "format_repair_used"):
        missing = {key: value for key, value in budget.items() if key != required}
        with pytest.raises(ValueError):
            PlanningBudgetSummary.model_validate(missing, strict=True)


@pytest.mark.asyncio
async def test_new_writer_refuses_legacy_content_before_repository_io():
    from tests.unit.itinerary_planning.test_service_initial import _fixture
    service, _, _, _, repository = _fixture()
    before = (len(repository.lookup_calls), len(repository.put_requests))
    for kind, artifact, operation, model in _registered_artifacts():
        if kind not in {"itinerary_planning.model_action", "itinerary_planning.itinerary"}:
            continue
        with pytest.raises(ValueError, match="envelope version"):
            await service._persist(operation_id=operation, artifact_type=kind, content=artifact, expected=model)
    assert (len(repository.lookup_calls), len(repository.put_requests)) == before
