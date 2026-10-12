"""True-PG capacity contract, enabled by the W03 isolated migration gate."""
import pytest
import os
from app.artifact_persistence import create_artifact_persistence_runtime, load_artifact_persistence_settings
from app.workflow_contracts import ArtifactSchemaRegistry
from tests.integration.artifact_persistence.conftest import TEST_REPOSITORY_LIMITS

@pytest.fixture(scope="session")
def artifact_persistence_settings():
    settings=load_artifact_persistence_settings(environ=os.environ)
    prefix=os.environ.get("MODULE10_TEST_PREFIX", "")
    token=os.environ.get("MODULE10_TEST_IDENTITY_TOKEN", "")
    m14_prefix=os.environ.get("MODULE14_TEST_PREFIX", "")
    m14_token=os.environ.get("MODULE14_TEST_IDENTITY_TOKEN", "")
    m14_mode=(m14_prefix == prefix and m14_token == token
        and prefix.replace("-", "_") in settings.database)
    m10_mode=settings.database == prefix+"-identity-ap"
    if (not prefix.startswith("cta-m14-") or token != prefix+"-identity"
            or not (m14_mode or m10_mode) or settings.host not in {"postgres", "m14-test-postgres"}):
        pytest.fail("explicit M14 or upstream regression identity required")
    database=os.environ.get("MODULE14_CAPACITY_DATABASE", "")
    if database != settings.database+"_cap":
        pytest.fail("explicit isolated capacity database required")
    return settings.model_copy(update={"database":database})

@pytest.mark.asyncio
async def test_capacity_snapshot_roundtrip_on_isolated_postgres(artifact_persistence_settings, migrated_artifact_database):
    runtime=await create_artifact_persistence_runtime(settings=artifact_persistence_settings,
        registry=ArtifactSchemaRegistry(()),limits=TEST_REPOSITORY_LIMITS)
    try:
        snapshot=await runtime.get_capacity_snapshot()
        assert snapshot.schema_version=="1.0"
        assert snapshot.artifact_count>=0
        assert snapshot==await runtime.get_capacity_snapshot()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_purpose_contract_versions_keep_canonical_bytes_hashes_after_reopening_postgres(artifact_persistence_settings,migrated_artifact_database):
    import hashlib
    import json
    from uuid import uuid4
    import psycopg
    from app.workflow_contracts import ArtifactWriteRequest, OperationId, WorkflowPhase
    from app.candidate_research import CandidateResearchArtifact, CandidateResearchLegacyArtifact, CandidateResearchV3Artifact, CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
    from app.itinerary_planning import ItineraryPlanningArtifact, ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS
    from app.itinerary_planning.artifacts import ItineraryPlanningModelActionArtifact
    from app.llm_runtime import ToolTurnCompleted
    from tests.unit.itinerary_planning.test_service_initial import _fixture, _upstream, _metadata
    from tests.contract.itinerary_planning.test_artifact_registration import _registered_artifacts
    from tests.contract.itinerary_planning.test_recovery_version_compatibility import new_budget
    new_candidate = _upstream(uuid4())[3]
    legacy = new_candidate.model_dump(mode="json")
    legacy["schema_version"] = "1.0"
    for candidate in legacy["record"]["state"]["candidates"]:
        candidate["schema_version"] = "1.0"
        candidate.pop("meal_assessments")
    legacy.pop("correction_state")
    legacy.pop("qualification_state")
    old_candidate = CandidateResearchLegacyArtifact.model_validate_json(json.dumps(legacy),strict=True)
    samples = [("candidate_research.candidate_set",item,type(item)) for item in (old_candidate,new_candidate)]
    for version,model in (("2.0",CandidateResearchLegacyArtifact),("3.0",CandidateResearchV3Artifact)):
        history = new_candidate.model_dump(mode="json")
        history["schema_version"] = version
        history.pop("qualification_state")
        if version == "2.0":
            history.pop("correction_state")
        samples.append(("candidate_research.candidate_set",model.model_validate_json(json.dumps(history),strict=True),model))
    legacy_artifacts = _registered_artifacts()
    old_action, old_itinerary = legacy_artifacts[0][1],legacy_artifacts[-1][1]
    action_v6 = old_action.model_dump(mode="json")
    action_v6.update(schema_version="6.0",prompt_version="planning-v6")
    itinerary_v3 = old_itinerary.model_dump(mode="json")
    itinerary_v3["schema_version"] = "3.0"
    itinerary_v3["directories"]["schema_version"] = "2.0"
    for ref in itinerary_v3["directories"]["model_action_refs"]: ref["schema_version"] = "6.0"
    itinerary_v3["record"]["state"].update(schema_version="3.0",format_repair_used=0,budgets=new_budget().model_dump(mode="json"))
    samples += [("itinerary_planning.model_action",old_action,ItineraryPlanningModelActionArtifact),
        ("itinerary_planning.model_action",ItineraryPlanningModelActionArtifact.model_validate_json(json.dumps(action_v6),strict=True),ItineraryPlanningModelActionArtifact),
        ("itinerary_planning.itinerary",old_itinerary,ItineraryPlanningArtifact),
        ("itinerary_planning.itinerary",ItineraryPlanningArtifact.model_validate_json(json.dumps(itinerary_v3),strict=True),ItineraryPlanningArtifact)]
    planner,request,_,_,memory = _fixture(ToolTurnCompleted(final_text="结束",metadata=_metadata()))
    outcome = await planner.plan_initial(request)
    assert outcome.outcome_type == "incomplete"
    for write in memory.put_requests:
        if write.artifact_type in {"itinerary_planning.model_action","itinerary_planning.itinerary"}:
            model = ItineraryPlanningModelActionArtifact if write.artifact_type.endswith("model_action") else ItineraryPlanningArtifact
            samples.append((write.artifact_type,model.model_validate_json(json.dumps(write.content),strict=True),model))
    registry = ArtifactSchemaRegistry((*CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,*ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS))
    runtime = await create_artifact_persistence_runtime(settings=artifact_persistence_settings,registry=registry,limits=TEST_REPOSITORY_LIMITS)
    stored = []
    try:
        for index,(kind,content,model) in enumerate(samples):
            operation = OperationId(run_id=uuid4(),phase=WorkflowPhase.CANDIDATE_RESEARCH if kind.startswith("candidate_research") else WorkflowPhase.ITINERARY_PLANNING,
                logical_step=f"purpose_bytes_{index}")
            write = ArtifactWriteRequest(operation_id=operation,artifact_type=kind,schema_version=content.schema_version,content=content.model_dump(mode="json"))
            raw = json.dumps(write.content,ensure_ascii=False,sort_keys=True,separators=(",", ":"),allow_nan=False).encode()
            ref = await runtime.repository.put(write)
            assert ref.content_hash == "sha256:"+hashlib.sha256(raw).hexdigest()
            assert await runtime.repository.put(write) == ref
            stored.append((ref,raw,model))
    finally:
        await runtime.close()
    reopened = await create_artifact_persistence_runtime(settings=artifact_persistence_settings,registry=registry,limits=TEST_REPOSITORY_LIMITS)
    try:
        async with await psycopg.AsyncConnection.connect(host=artifact_persistence_settings.host,port=artifact_persistence_settings.port,
                dbname=artifact_persistence_settings.database,user=artifact_persistence_settings.user,
                password=artifact_persistence_settings.password.get_secret_value()) as connection:
            for ref,raw,model in stored:
                read = await reopened.repository.get(ref,expected_model=model)
                assert json.dumps(read.content.model_dump(mode="json"),ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode() == raw
                cursor = await connection.execute("SELECT canonical_bytes, content_hash FROM artifact_store.artifacts WHERE artifact_id=%s",(ref.artifact_id,))
                saved = await cursor.fetchone()
                assert bytes(saved[0]) == raw and saved[1] == ref.content_hash
    finally:
        await reopened.close()
