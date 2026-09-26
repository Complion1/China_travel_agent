"""RF-PERSIST worker: new-process reads of a complete, synthetic saved result.

Run only in the isolated Module 12 Docker harness. No source data, password,
or exception text is printed; the host receives safe identities and hashes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from collections.abc import Mapping
from uuid import UUID

from app.artifact_persistence import (
    ArtifactPersistenceSettings, create_artifact_persistence_runtime,
    load_artifact_persistence_settings,
)
from app.itinerary_planning import ItineraryPlanningArtifact
from app.result_formatting import (
    ResultDayArtifact, ResultFormattingLoaded, ResultFormattingPrepared,
    ResultFormattingService, ResultIndexArtifact, ResultIndexReady,
    ResultReadRequest, build_result_artifact_write_request,
)
from app.workflow_contracts import (
    ArtifactAbsent, ArtifactFound, ArtifactRef, DeliveryStatus, OperationId,
    TravelSessionState, WorkflowPhase,
)
from tests.unit.result_formatting.fakes import (
    TRACE_ID, make_confirmed, make_read_request, make_registry, make_scenario,
    save_result_days, save_result_index,
)


RESTART_RUN_ID = UUID("12000000-0000-4000-8000-00000000c001")
RESTART_DAYS = 3
INDEX_STEP = "result_index"
PREFIX_PATTERN = re.compile(r"china-travel-agent-m12-[a-z0-9][a-z0-9-]{2,30}")


class HarnessFailure(Exception):
    """Fixed safe failure; never retain connection or business payload text."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise HarnessFailure(code) from None


def isolated_settings(environ: Mapping[str, str] | None = None) -> ArtifactPersistenceSettings:
    """Consume only explicit V §6.3 identities; never load a dotenv file."""
    source = os.environ if environ is None else environ
    prefix = source.get("MODULE12_TEST_PREFIX", "")
    password = source.get("POSTGRES_PASSWORD", "")
    valid = (
        type(prefix) is str and PREFIX_PATTERN.fullmatch(prefix) is not None
        and source.get("MODULE12_TEST_IDENTITY_TOKEN") == prefix + "-identity"
        and source.get("POSTGRES_HOST") == "postgres"
        and source.get("POSTGRES_PORT") == "5432"
        and source.get("POSTGRES_DB") == prefix.replace("-", "_") + "_main"
        and source.get("POSTGRES_USER") == "m12_test"
        and type(password) is str and len(password) >= 16
        and all(33 <= ord(character) <= 126 for character in password)
        and password.casefold() not in {"postgres", "password", "changeme", "m12_test"}
    )
    require(bool(valid), "M12_INVALID_ISOLATED_DATABASE_IDENTITY")
    return load_artifact_persistence_settings(environ={
        name: source[name] for name in (
            "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
            "POSTGRES_USER", "POSTGRES_PASSWORD",
        )
    })


async def open_runtime(settings: ArtifactPersistenceSettings):
    # A failing pool may log provider exception text; this harness reports only
    # its fixed safe failure code. The M10 public factory owns pool lifecycle.
    logging.getLogger("psycopg.pool").disabled = True
    return await create_artifact_persistence_runtime(settings=settings, registry=make_registry())


def operation(run_id: UUID, logical_step: str, *, phase=WorkflowPhase.OUTPUT_FORMATTING) -> OperationId:
    return OperationId(run_id=run_id, phase=phase, logical_step=logical_step)


def safe_ref(ref: ArtifactRef) -> dict[str, object]:
    return {"artifact_id": str(ref.artifact_id), "content_hash": ref.content_hash,
            "size_bytes": ref.size_bytes}


def checked_canonical(artifact, ref: ArtifactRef) -> bytes:
    """Compare real Registry canonical bytes to the checked saved Ref."""
    prepared = make_registry().prepare(build_result_artifact_write_request(
        operation_id=ref.operation_id, artifact=artifact,
    ))
    require(prepared.content_hash == ref.content_hash, "M12_CANONICAL_HASH_MISMATCH")
    require(prepared.size_bytes == ref.size_bytes, "M12_CANONICAL_SIZE_MISMATCH")
    require("sha256:" + hashlib.sha256(prepared.canonical_bytes).hexdigest() == ref.content_hash,
            "M12_CANONICAL_BYTES_MISMATCH")
    return prepared.canonical_bytes


def result_manifest(loaded: ResultFormattingLoaded, *, mode: str) -> dict[str, object]:
    checked_canonical(loaded.index, loaded.index_ref)
    days = []
    for entry, day in zip(loaded.index.days, loaded.days, strict=True):
        checked_canonical(day, entry.ref)
        require(day.data.day_no == entry.day_no and day.data.date == entry.date,
                "M12_DAY_INDEX_MISMATCH")
        require(day.source_itinerary_ref == loaded.index.source_itinerary_ref,
                "M12_SOURCE_INDEX_MISMATCH")
        days.append({"day_no": entry.day_no, **safe_ref(entry.ref)})
    return {
        "schema_version": "1.0", "mode": mode, "run_id": str(loaded.index.run_id),
        "index": safe_ref(loaded.index_ref), "days": days,
        "source_itinerary": safe_ref(loaded.index.source_itinerary_ref),
        "day_count": len(days),
        "markdown_sha256": "sha256:" + hashlib.sha256(loaded.markdown.encode("utf-8")).hexdigest(),
    }


async def seed(repository) -> dict[str, object]:
    index_operation = operation(RESTART_RUN_ID, INDEX_STEP)
    require(isinstance(await repository.lookup(operation_id=index_operation), ArtifactAbsent),
            "M12_RESTART_SEED_ALREADY_EXISTS")
    scenario = await make_scenario(repository=repository, days=RESTART_DAYS, run_id=RESTART_RUN_ID)
    service = ResultFormattingService(artifact_repository=repository)
    prepared = await service.prepare(scenario.request)
    require(type(prepared) is ResultFormattingPrepared, "M12_SEED_PREPARE_FAILED")
    refs = await save_result_days(scenario, prepared.days)
    ready = await service.build_index(scenario.request, day_refs=refs)
    require(type(ready) is ResultIndexReady, "M12_SEED_INDEX_FAILED")
    index_ref = await save_result_index(scenario, ready.index)
    loaded = await service.load(make_read_request(scenario, index_ref))
    require(type(loaded) is ResultFormattingLoaded, "M12_SEED_LOAD_FAILED")
    require(len(loaded.days) == RESTART_DAYS, "M12_SEED_DAY_COUNT_MISMATCH")
    return result_manifest(loaded, mode="seed")


async def verify(repository) -> dict[str, object]:
    # Only fixed run/operation identities and fixed synthetic M01 confirmation
    # are reconstructed. Every M07/M08/M09/M12 Artifact comes from lookup/get.
    found_index = await repository.lookup(operation_id=operation(RESTART_RUN_ID, INDEX_STEP))
    found_source = await repository.lookup(operation_id=operation(
        RESTART_RUN_ID, "itinerary", phase=WorkflowPhase.ITINERARY_PLANNING,
    ))
    require(isinstance(found_index, ArtifactFound), "M12_RESTART_INDEX_NOT_FOUND")
    require(isinstance(found_source, ArtifactFound), "M12_RESTART_SOURCE_NOT_FOUND")
    index_read = await repository.get(found_index.ref, expected_model=ResultIndexArtifact)
    source_read = await repository.get(found_source.ref, expected_model=ItineraryPlanningArtifact)
    require(index_read.content.source_itinerary_ref == source_read.ref, "M12_RESTART_SOURCE_MISMATCH")
    for entry in index_read.content.days:
        found_day = await repository.lookup(operation_id=operation(
            RESTART_RUN_ID, f"result_day_{entry.day_no:02d}",
        ))
        require(isinstance(found_day, ArtifactFound) and found_day.ref == entry.ref,
                "M12_RESTART_DAY_OPERATION_MISMATCH")
        day_read = await repository.get(entry.ref, expected_model=ResultDayArtifact)
        checked_canonical(day_read.content, entry.ref)
    confirmation = await make_confirmed(RESTART_DAYS)
    state = TravelSessionState(
        schema_version="2.0", run_id=RESTART_RUN_ID, trace_id=TRACE_ID,
        phase=WorkflowPhase.OUTPUT_FORMATTING, delivery_status=DeliveryStatus.IN_PROGRESS,
        terminal_code=None, terminal_outcome=None, requirement_state=confirmation.state,
        requirement_outcome=confirmation.outcome, artifact_refs=(found_source.ref, found_index.ref),
    )
    request = ResultReadRequest(schema_version="1.0", state=state, index_ref=found_index.ref)
    loaded = await ResultFormattingService(artifact_repository=repository).load(request)
    require(type(loaded) is ResultFormattingLoaded, "M12_RESTART_LOAD_FAILED")
    require(len(loaded.days) == RESTART_DAYS, "M12_RESTART_DAY_COUNT_MISMATCH")
    return result_manifest(loaded, mode="verify")


async def run(mode: str) -> dict[str, object]:
    runtime = await open_runtime(isolated_settings())
    try:
        return await (seed(runtime.repository) if mode == "seed" else verify(runtime.repository))
    finally:
        await runtime.close()


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"seed", "verify"}:
        print("M12_WORKER_INVALID_MODE", file=sys.stderr)
        return 2
    try:
        manifest = asyncio.run(run(sys.argv[1]))
    except Exception:
        print("M12_WORKER_FAILED", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
