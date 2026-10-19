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
    from app.workflow_contracts import ArtifactWriteRequest, OperationId, UnsupportedArtifactVersionError, WorkflowPhase
    from app.candidate_research import CandidateResearchArtifact, CandidateResearchLegacyArtifact, CandidateResearchV3Artifact, CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
    from app.itinerary_planning import ItineraryPlanningArtifact, ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS
    from app.itinerary_planning.artifacts import ItineraryPlanningModelActionArtifact
    from app.llm_runtime import ToolTurnCompleted
    from tests.unit.itinerary_planning.test_service_initial import _fixture, _upstream, _metadata
    from tests.contract.itinerary_planning.test_artifact_registration import _registered_artifacts
    from tests.unit.result_formatting.fakes import make_scenario
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
    registered_artifacts = _registered_artifacts()
    current_action, current_itinerary = registered_artifacts[0][1],registered_artifacts[-1][1]
    action_v6 = current_action.model_dump(mode="json")
    action_v6.update(schema_version="6.0",prompt_version="planning-v6")
    minute_itinerary = (await make_scenario(minute_schedule=True,run_id=uuid4())).itinerary
    assert current_itinerary.schema_version == minute_itinerary.schema_version == "6.0"
    boundary_itinerary = (await make_scenario(planning_version="8.0", run_id=uuid4())).itinerary
    samples += [("itinerary_planning.model_action",current_action,ItineraryPlanningModelActionArtifact),
        ("itinerary_planning.model_action",ItineraryPlanningModelActionArtifact.model_validate_json(json.dumps(action_v6),strict=True),ItineraryPlanningModelActionArtifact),
        ("itinerary_planning.itinerary",current_itinerary,ItineraryPlanningArtifact),
        ("itinerary_planning.itinerary",minute_itinerary,ItineraryPlanningArtifact),
        ("itinerary_planning.itinerary",boundary_itinerary,ItineraryPlanningArtifact)]
    planner,request,_,_,memory = _fixture(ToolTurnCompleted(final_text="结束",metadata=_metadata()))
    outcome = await planner.plan_initial(request)
    assert outcome.outcome_type == "incomplete"
    for write in memory.put_requests:
        if write.artifact_type in {"itinerary_planning.model_action","itinerary_planning.itinerary"}:
            model = ItineraryPlanningModelActionArtifact if write.artifact_type.endswith("model_action") else ItineraryPlanningArtifact
            samples.append((write.artifact_type,model.model_validate_json(json.dumps(write.content),strict=True),model))
    registry = ArtifactSchemaRegistry((*CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS,*ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS))
    for version in ("2.0","3.0","4.0","5.0"):
        history = current_itinerary.model_dump(mode="json")
        history["schema_version"] = version
        with pytest.raises(ValueError):
            ItineraryPlanningArtifact.model_validate_json(json.dumps(history),strict=True)
        operation = OperationId(run_id=uuid4(),phase=WorkflowPhase.ITINERARY_PLANNING,
            logical_step=f"unsupported_itinerary_{version.replace('.', '_')}")
        with pytest.raises(UnsupportedArtifactVersionError):
            registry.prepare(ArtifactWriteRequest(operation_id=operation,
                artifact_type="itinerary_planning.itinerary",schema_version=version,content=history))
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
