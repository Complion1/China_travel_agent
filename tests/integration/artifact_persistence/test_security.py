"""AP-T03/AP-T04 integrity, security, and cross-module compatibility tests."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from psycopg_pool import AsyncConnectionPool

from app.artifact_persistence.repository import _PostgresArtifactRepository
from app.workflow_contracts.artifacts.models import (
    ArtifactFound,
    ArtifactRepositoryLimits,
    ArtifactWriteRequest,
)
from app.workflow_contracts.artifacts.registry import (
    ArtifactSchemaRegistry,
)
from app.workflow_contracts.errors import (
    ArtifactIntegrityError,
    ArtifactRequestValidationError,
)
from app.workflow_contracts.identifiers import OperationId, WorkflowPhase
from tests.contract.candidate_research import test_artifact_registration as candidate_cases
from tests.contract.hotel_selection import test_artifact_registration as hotel_cases
from tests.contract.itinerary_planning import test_artifact_registration as itinerary_cases


pytestmark = pytest.mark.usefixtures("artifact_store_isolated")


SECRET_SENTINEL = "m10-repository-secret-sentinel-9e0d"


def _operation(step: str) -> OperationId:
    return OperationId(
        run_id=uuid4(),
        phase=WorkflowPhase.REQUIREMENTS,
        logical_step=step,
    )


def _request(operation_id: OperationId, *, label: str) -> ArtifactWriteRequest:
    return ArtifactWriteRequest(
        operation_id=operation_id,
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={
            "label": label,
            "count": 1,
            "ratio": 1.0,
            "occurred_on": "2026-08-11",
            "observed_at": "2026-08-11T09:30:00Z",
            "note": None,
        },
    )


def _sample_registry() -> ArtifactSchemaRegistry:
    from app.workflow_contracts.artifacts.registry import ArtifactSchemaRegistration
    from tests.unit.workflow_contracts.fakes import StrictSampleArtifact

    return ArtifactSchemaRegistry(
        (
            ArtifactSchemaRegistration(
                artifact_type="test.sample_artifact",
                schema_version="1.0",
                model=StrictSampleArtifact,
            ),
        )
    )


def _repository(
    pool: AsyncConnectionPool,
    limits: ArtifactRepositoryLimits,
    registry: ArtifactSchemaRegistry | None = None,
) -> _PostgresArtifactRepository:
    return _PostgresArtifactRepository(
        pool=pool,
        registry=registry or _sample_registry(),
        limits=limits,
    )


@pytest.mark.asyncio
async def test_parameter_binding_and_secret_sentinel_are_not_exposed(
    artifact_pool: AsyncConnectionPool,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """Values containing SQL syntax and the synthetic secret remain data/errors only."""

    from tests.unit.workflow_contracts.fakes import StrictSampleArtifact

    repository = _repository(artifact_pool, test_repository_limits)
    malicious_label = "x'); DROP TABLE artifact_store.artifacts; --"
    ref = await repository.put(_request(_operation("sql_parameterized"), label=malicious_label))
    read = await repository.get(ref, expected_model=StrictSampleArtifact)
    assert read.content.label == malicious_label

    secret_ref = await repository.put(
        _request(_operation("secret_sentinel"), label=SECRET_SENTINEL)
    )
    assert (
        await repository.get(secret_ref, expected_model=StrictSampleArtifact)
    ).content.label == SECRET_SENTINEL

    forged = ArtifactWriteRequest.model_construct(
        operation_id=_operation("secret_error"),
        artifact_type="test.sample_artifact",
        schema_version="1.0",
        content={"bad": SECRET_SENTINEL},
    )
    with pytest.raises(ArtifactRequestValidationError) as error:
        await repository.put(forged)
    assert SECRET_SENTINEL not in str(error.value)
    assert SECRET_SENTINEL not in repr(error.value)


@pytest.mark.asyncio
async def test_forged_and_nested_mutated_envelopes_are_revalidated(
    artifact_pool: AsyncConnectionPool,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """OperationId and ArtifactRef model_construct/mutation do not bypass strict copies."""

    from tests.unit.workflow_contracts.fakes import StrictSampleArtifact

    repository = _repository(artifact_pool, test_repository_limits)
    forged_operation = OperationId.model_construct(
        run_id="not-a-uuid",
        phase="requirements",
        logical_step="forged",
    )
    with pytest.raises(ArtifactRequestValidationError):
        await repository.lookup(operation_id=forged_operation)

    ref = await repository.put(_request(_operation("nested_ref"), label="stored"))
    mutated_operation = ref.operation_id.model_copy()
    object.__setattr__(mutated_operation, "logical_step", "mutated_nested")
    mutated_ref = ref.model_copy(update={"operation_id": mutated_operation})
    with pytest.raises(ArtifactIntegrityError):
        await repository.get(mutated_ref, expected_model=StrictSampleArtifact)

    forged_ref = ref.model_construct(
        artifact_id=ref.artifact_id,
        artifact_type=ref.artifact_type,
        schema_version=ref.schema_version,
        content_hash=ref.content_hash,
        operation_id=OperationId.model_construct(
            run_id="not-a-uuid",
            phase="requirements",
            logical_step="forged_ref",
        ),
        created_at=ref.created_at,
        size_bytes=ref.size_bytes,
    )
    with pytest.raises(ArtifactRequestValidationError):
        await repository.get(forged_ref, expected_model=StrictSampleArtifact)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ["bytes", "hash", "size", "type", "version", "logical_step"],
)
async def test_canonical_bytes_hash_size_and_ref_corruption_fails_closed(
    artifact_pool: AsyncConnectionPool,
    db_connection,
    test_repository_limits: ArtifactRepositoryLimits,
    corruption: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stored正文、hash、size及ref元数据损坏均只报告完整性错误。"""

    from tests.unit.workflow_contracts.fakes import StrictSampleArtifact

    registry = _sample_registry()
    decode_spy = Mock(wraps=registry.decode)
    monkeypatch.setattr(registry, "decode", decode_spy)
    repository = _repository(artifact_pool, test_repository_limits, registry)
    ref = await repository.put(_request(_operation("corruption"), label="stored"))
    with db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT artifact_id, run_id, phase, logical_step, artifact_type,
                   schema_version, content_hash, canonical_bytes, created_at, size_bytes
            FROM artifact_store.artifacts
            WHERE artifact_id = %s
            """,
            (ref.artifact_id,),
        )
        before = cursor.fetchone()
        cursor.execute(
            """
            SELECT artifact_count, total_content_bytes
            FROM artifact_store.capacity_state
            WHERE singleton_id = 1
            """
            )
        capacity_before = cursor.fetchone()
    db_connection.commit()
    with db_connection.transaction():
        with db_connection.cursor() as cursor:
            if corruption == "bytes":
                cursor.execute(
                    """
                    UPDATE artifact_store.artifacts
                    SET canonical_bytes = set_byte(
                        canonical_bytes,
                        octet_length(canonical_bytes) - 1,
                        93
                    )
                    WHERE artifact_id = %s
                    """,
                    (ref.artifact_id,),
                )
            elif corruption == "hash":
                cursor.execute(
                    "UPDATE artifact_store.artifacts SET content_hash = %s WHERE artifact_id = %s",
                    ("sha256:" + "0" * 64, ref.artifact_id),
                )
            elif corruption == "size":
                cursor.execute(
                    """
                    UPDATE artifact_store.artifacts
                    SET canonical_bytes = canonical_bytes || %s,
                        size_bytes = size_bytes + %s
                    WHERE artifact_id = %s
                    """,
                    (b"x", 1, ref.artifact_id),
                )
            elif corruption == "type":
                cursor.execute(
                    "UPDATE artifact_store.artifacts SET artifact_type = %s WHERE artifact_id = %s",
                    ("test.other_artifact", ref.artifact_id),
                )
            elif corruption == "version":
                cursor.execute(
                    "UPDATE artifact_store.artifacts SET schema_version = %s WHERE artifact_id = %s",
                    ("1.1", ref.artifact_id),
                )
            elif corruption == "logical_step":
                cursor.execute(
                    "UPDATE artifact_store.artifacts SET logical_step = %s WHERE artifact_id = %s",
                    ("corrupted_step", ref.artifact_id),
                )
            else:
                raise AssertionError(f"unknown corruption case: {corruption}")
    with pytest.raises(ArtifactIntegrityError):
        await repository.get(ref, expected_model=StrictSampleArtifact)

    with db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT artifact_id, run_id, phase, logical_step, artifact_type,
                   schema_version, content_hash, canonical_bytes, created_at, size_bytes
            FROM artifact_store.artifacts
            WHERE artifact_id = %s
            """,
            (ref.artifact_id,),
        )
        after = cursor.fetchone()
        cursor.execute(
            """
            SELECT artifact_count, total_content_bytes
            FROM artifact_store.capacity_state
            WHERE singleton_id = 1
            """
        )
        assert cursor.fetchone() == capacity_before

    assert after is not None
    changed_indexes = {
        "bytes": {7},
        "hash": {6},
        "size": {7, 9},
        "type": {4},
        "version": {5},
        "logical_step": {3},
    }[corruption]
    for index, (before_value, after_value) in enumerate(zip(before, after, strict=True)):
        if index not in changed_indexes:
            assert after_value == before_value
    if corruption == "bytes":
        assert after[7] != before[7]
        assert len(after[7]) == len(before[7])
        assert after[9] == before[9]
        assert decode_spy.call_count == 1
    elif corruption == "hash":
        assert after[6] != before[6]
        assert after[7] == before[7]
        assert after[9] == before[9]
        assert decode_spy.call_count == 0
    elif corruption == "size":
        assert after[7] != before[7]
        assert after[9] == before[9] + 1
        assert decode_spy.call_count == 0
    elif corruption == "type":
        assert after[4] == "test.other_artifact"
        assert decode_spy.call_count == 0
    elif corruption == "version":
        assert after[5] == "1.1"
        assert decode_spy.call_count == 0
    elif corruption == "logical_step":
        assert after[3] == "corrupted_step"
        assert decode_spy.call_count == 0


def test_lookup_sql_is_metadata_only_and_repository_has_no_business_imports() -> None:
    """Static boundary checks complement the real PostgreSQL lookup assertion."""

    source_path = Path("app/artifact_persistence/repository.py")
    source = source_path.read_text(encoding="utf-8")
    lookup_start = source.index("_LOOKUP_SQL")
    get_start = source.index("_GET_SQL")
    lookup_sql = source[lookup_start:get_start]
    assert "canonical_bytes" not in lookup_sql
    assert "%(run_id)s" in lookup_sql
    assert "%(phase)s" in lookup_sql
    assert "%(logical_step)s" in lookup_sql

    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )
    assert not any(
        name.startswith("app.hotel_selection")
        or name.startswith("app.candidate_research")
        or name.startswith("app.itinerary_planning")
        for name in imported
    )
    public_methods = {
        name
        for name, value in vars(_PostgresArtifactRepository).items()
        if callable(value) and not name.startswith("_")
    }
    assert public_methods == {"put", "lookup", "get"}
    assert _PostgresArtifactRepository.__name__.startswith("_")


@pytest.mark.asyncio
async def test_module07_08_09_registrations_put_and_get_without_production_composition(
    artifact_pool: AsyncConnectionPool,
    test_repository_limits: ArtifactRepositoryLimits,
) -> None:
    """Current Module 07-09 registrations are combined only by this test."""

    from app.candidate_research.artifacts import (
        CandidateResearchArtifact,
        CandidateResearchModelJudgmentArtifact,
        CandidateResearchPlaceObservationArtifact,
        InProgressCandidateSetRecord,
        JudgmentPatchSnapshot,
        ModelJudgmentAcceptedRecord,
        PlaceMapFailureObservation,
    )
    from app.candidate_research import ResearchMode, ResearchTool
    from app.baidu_map import BaiduMapErrorCode, MapCapability
    from app.hotel_selection.artifacts import HOTEL_ARTIFACT_REGISTRATIONS, _artifact_write_request
    from app.itinerary_planning.artifacts import ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS

    # Module 07 uses its existing contract samples.
    hotel_registrations = HOTEL_ARTIFACT_REGISTRATIONS
    # Module 08 uses the current public registration tuple and complete valid samples.
    candidate_registrations = candidate_cases.CANDIDATE_RESEARCH_ARTIFACT_REGISTRATIONS
    # Module 09 samples are produced by the existing registration contract helper.
    itinerary_registrations = ITINERARY_PLANNING_ARTIFACT_REGISTRATIONS
    registry = ArtifactSchemaRegistry(
        hotel_registrations + candidate_registrations + itinerary_registrations
    )
    repository = _repository(artifact_pool, test_repository_limits, registry)

    for index, (artifact_type, artifact, operation_id, expected_model) in enumerate(
        hotel_cases.artifact_cases()
    ):
        request = _artifact_write_request(
            operation_id=operation_id,
            artifact_type=artifact_type,
            artifact=artifact,
        )
        ref = await repository.put(request)
        read = await repository.get(ref, expected_model=expected_model)
        assert read.content == artifact
        assert index >= 0

    action, candidate_run_id = candidate_cases._action_artifact()
    candidate_samples = [(
        "candidate_research.model_action",
        action,
        candidate_run_id,
        type(action),
    )]
    observation_ref = candidate_cases._ref(
        candidate_run_id,
        "candidate_research.place_observation",
        logical_step="observation",
    )
    judgment = CandidateResearchModelJudgmentArtifact(
        schema_version="1.0",
        run_id=candidate_run_id,
        mode=ResearchMode.INITIAL,
        invocation_index=0,
        invocation_slot_index=0,
        iteration_index=0,
        prompt_version="observation-v1",
        prompt_sha256="sha256:" + "b" * 64,
        evidence_refs=(observation_ref,),
        result=ModelJudgmentAcceptedRecord(
            record_type="accepted",
            patch=JudgmentPatchSnapshot(
                outcome_type="judgment_patch",
                role_updates=(),
                interest_updates=(),
                meal_updates=(),
                requirement_group_updates=(),
            ),
            metadata=candidate_cases._metadata(),
        ),
    )
    observation = CandidateResearchPlaceObservationArtifact(
        schema_version="1.0",
        run_id=candidate_run_id,
        mode=ResearchMode.INITIAL,
        invocation_slot_index=0,
        iteration_index=0,
        action_ordinal=0,
        tool=ResearchTool.SEARCH_PLACES,
        action_fingerprint="sha256:" + "c" * 64,
        result=PlaceMapFailureObservation(
            record_type="map_failure",
            code=BaiduMapErrorCode.TIMEOUT_ERROR,
            capability=MapCapability.PLACE_SEARCH,
        ),
    )
    candidate_set = CandidateResearchArtifact(
        schema_version="1.0",
        run_id=candidate_run_id,
        mode=ResearchMode.INITIAL,
        predecessor_ref=None,
        invocation_request_digest="sha256:" + "d" * 64,
        record=InProgressCandidateSetRecord(
            record_type="in_progress",
            state=candidate_cases._minimal_state(candidate_run_id),
        ),
    )
    candidate_samples.extend(
        [
            ("candidate_research.model_judgment", judgment, candidate_run_id, type(judgment)),
            ("candidate_research.place_observation", observation, candidate_run_id, type(observation)),
            ("candidate_research.candidate_set", candidate_set, candidate_run_id, type(candidate_set)),
        ]
    )
    for index, (artifact_type, artifact, run_id, expected_model) in enumerate(candidate_samples):
        operation_id = OperationId(
            run_id=run_id,
            phase=WorkflowPhase.CANDIDATE_RESEARCH,
            logical_step=f"candidate_compat_{index}",
        )
        request = ArtifactWriteRequest(
            operation_id=operation_id,
            artifact_type=artifact_type,
            schema_version="1.0",
            content=artifact.model_dump(mode="json"),
        )
        ref = await repository.put(request)
        assert (await repository.get(ref, expected_model=expected_model)).content == artifact

    for index, (artifact_type, artifact, operation_id, expected_model) in enumerate(
        itinerary_cases._registered_artifacts()
    ):
        request = ArtifactWriteRequest(
            operation_id=operation_id,
            artifact_type=artifact_type,
            schema_version="3.0" if artifact_type.endswith("model_action") else "1.0",
            content=artifact.model_dump(mode="json"),
        )
        ref = await repository.put(request)
        assert (await repository.get(ref, expected_model=expected_model)).content == artifact
        assert index >= 0
