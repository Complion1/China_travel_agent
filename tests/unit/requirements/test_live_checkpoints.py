"""Offline checkpoint contracts; synthetic records never qualify as live snapshots."""
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.workflow_contracts import (ArtifactRef, ArtifactWriteRequest, OperationId, WorkflowPhase,
                                    InMemoryArtifactRepository, DEMO_ARTIFACT_REPOSITORY_LIMITS)
from tests.support.hotel_selection_fakes import FixedClock, SequenceUUIDFactory
from tests.live.travel_pipeline import checkpoints as cp
from tests.live.travel_pipeline import test_live_requirements_to_result as worker


def manifest():
    ref = ArtifactRef(artifact_id=UUID(int=1), artifact_type="live_test.context", schema_version="1.0",
                      content_hash="sha256:" + "a" * 64, created_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
                      size_bytes=10, operation_id=OperationId(run_id=worker.RUN_ID,
                      phase=WorkflowPhase.REQUIREMENTS, logical_step="test_context"))
    return cp.StageManifest(stage="requirements", input_sha256="b" * 64,
                            source_fingerprint=cp.source_fingerprint("requirements"),
                            run_id=worker.RUN_ID, trace_id=worker.TRACE_ID, artifacts=(ref,))


def seal(directory):
    (directory / "database.dump").write_bytes(b"synthetic-dump-for-unit-test")
    return cp.seal_checkpoint(directory, source_tag="offline-case", source_image="sha256:" + "c" * 64,
                              manifest=manifest(), calls={}, purpose="offline")


def test_sealed_checkpoint_identity_integrity_and_live_origin_gate(tmp_path):
    metadata = seal(tmp_path)
    assert cp.inspect_checkpoint(tmp_path, source_tag="offline-case", stage="requirements", purpose="offline") == metadata
    for source, stage, purpose in (("other-case", "requirements", "offline"),
                                   ("offline-case", "hotel", "offline"),
                                   ("offline-case", "requirements", "live")):
        with pytest.raises(ValueError, match="CHECKPOINT_IDENTITY_MISMATCH"):
            cp.inspect_checkpoint(tmp_path, source_tag=source, stage=stage, purpose=purpose)
    with pytest.raises(ValueError, match="CHECKPOINT_ALREADY_SEALED"):
        seal(tmp_path)
    (tmp_path / "database.dump").write_bytes(b"changed")
    with pytest.raises(ValueError, match="CHECKPOINT_DUMP_MISMATCH"):
        cp.inspect_checkpoint(tmp_path, source_tag="offline-case", stage="requirements", purpose="offline")


def test_checkpoint_rejects_changed_source_and_incomplete_manifest(tmp_path, monkeypatch):
    seal(tmp_path)
    monkeypatch.setattr(cp, "source_fingerprint", lambda stage: "0" * 64)
    with pytest.raises(ValueError, match="CHECKPOINT_SOURCE_CHANGED"):
        cp.inspect_checkpoint(tmp_path, source_tag="offline-case", stage="requirements", purpose="offline")
    (tmp_path / "manifest.json").write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError):
        cp.inspect_checkpoint(tmp_path, source_tag="offline-case", stage="requirements", purpose="offline")


def test_checkpoint_cannot_be_sealed_live_without_real_call_evidence(tmp_path):
    (tmp_path / "database.dump").write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="CHECKPOINT_LIVE_CALLS_MISSING"):
        cp.seal_checkpoint(tmp_path, source_tag="offline-case", source_image="sha256:" + "c" * 64,
                           manifest=manifest(), calls={}, purpose="live")
    assert not (tmp_path / "manifest.json").exists()


def test_dependency_fingerprint_ignores_failed_module_but_tracks_passed_modules(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("locked", encoding="utf-8")
    for package in ("requirements", "itinerary_planning", "result_formatting"):
        folder = tmp_path / "app" / package
        folder.mkdir(parents=True)
        (folder / "service.py").write_text("original", encoding="utf-8")
    research = cp.source_fingerprint("research", root=tmp_path)
    planning = cp.source_fingerprint("planning", root=tmp_path)
    (tmp_path / "app/itinerary_planning/service.py").write_text("fixed", encoding="utf-8")
    assert cp.source_fingerprint("research", root=tmp_path) == research
    assert cp.source_fingerprint("planning", root=tmp_path) != planning
    get_profile = cp.get_profile
    monkeypatch.setattr(cp, "get_profile", lambda role: get_profile(role).model_copy(update={"max_request_utf8_bytes": 999})
                        if role is cp.ModelRole.ITINERARY_PLANNING else get_profile(role))
    assert cp.source_fingerprint("research", root=tmp_path) == research
    (tmp_path / "app/requirements/service.py").write_text("changed", encoding="utf-8")
    assert cp.source_fingerprint("research", root=tmp_path) != research


def repository():
    return InMemoryArtifactRepository(registry=worker.registry(),
        utc_clock=FixedClock(datetime(2026, 9, 27, tzinfo=timezone.utc)),
        uuid_factory=SequenceUUIDFactory(start=90000), limits=DEMO_ARTIFACT_REPOSITORY_LIMITS)


@pytest.mark.asyncio
async def test_offline_probe_round_trip_preserves_refs_without_qualifying_unconfirmed_context(monkeypatch):
    monkeypatch.setattr(worker, "COUNTS", Counter())
    repo = repository()
    result = await worker.checkpoint_probe(repo)
    monkeypatch.setenv("CHECKPOINT_STAGE_MANIFEST", cp.StageManifest.model_validate_json(__import__("json").dumps(result)).model_dump_json())
    assert await worker.checkpoint_probe(repo, restore=True) == result
    with pytest.raises(worker.HarnessFailure, match="CHECKPOINT_UNCONFIRMED"):
        await worker.saved_context(repo)
    assert not worker.COUNTS


@pytest.mark.asyncio
async def test_formatting_from_persisted_itinerary_never_creates_api_runtime(monkeypatch):
    from tests.unit.result_formatting.fakes import make_scenario
    monkeypatch.setattr(worker, "COUNTS", Counter())
    repo = repository()
    scenario = await make_scenario(repository=repo, days=3, run_id=worker.RUN_ID, budget_total_cny=None)
    monkeypatch.setattr(worker, "TRACE_ID", scenario.request.state.trace_id)
    # This is an explicitly synthetic offline scenario, not the real trip input.
    monkeypatch.setattr(worker, "scenario_matches", lambda requirement: None)
    await repo.put(worker.context_write(scenario.request.state))
    await repo.put(ArtifactWriteRequest(operation_id=worker.op(WorkflowPhase.ITINERARY_PLANNING, "plan_terminal"),
        artifact_type="itinerary_planning.itinerary", schema_version=scenario.itinerary.schema_version, content=scenario.itinerary.model_dump(mode="json")))
    def forbidden(*args, **kwargs):
        raise AssertionError("formatting must not construct an API runtime")
    monkeypatch.setattr(worker, "DeepSeekRuntime", forbidden)
    monkeypatch.setattr(worker, "create_baidu_map_runtime", forbidden)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("BAIDU_MAPS_API_KEY", raising=False)
    result = await worker.live_stage(repo, "formatting")
    assert result["day_count"] == 3 and not worker.COUNTS
    assert await worker.verify(repo) == result
