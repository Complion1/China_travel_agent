"""Test-only, bounded PostgreSQL snapshot metadata. Never stores provider history."""
from __future__ import annotations

import hashlib
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import sys
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.llm_runtime.profiles import get_profile
from app.llm_runtime import ModelRole
from app.workflow_contracts import ArtifactRef

STAGES = ("requirements", "hotel", "research", "planning")
Stage = Literal["requirements", "hotel", "research", "planning"]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class StageManifest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    stage: Stage
    input_sha256: Digest
    source_fingerprint: Digest
    run_id: UUID
    trace_id: UUID
    artifacts: Annotated[tuple[ArtifactRef, ...], Field(min_length=1, max_length=4)]


class CheckpointMetadata(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    source_tag: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{2,30}$")]
    source_image: Annotated[str, StringConstraints(pattern=r"^sha256:[a-f0-9]{64}$")]
    purpose: Literal["live", "offline"]
    manifest: StageManifest
    database_sha256: Digest
    database_size: Annotated[int, Field(gt=0)]
    calls: dict[Literal["llm_invoke_json", "llm_invoke_tools", "llm_invoke_tool_continuation",
                        "map_runtime_start", "map_geocode", "map_reverse_geocode", "map_search_places",
                        "map_get_place_details", "map_calculate_route_matrix", "map_plan_directions",
                        "map_get_weather"], Annotated[int, Field(ge=0)]]


def source_fingerprint(stage: Stage, *, root: Path | None = None) -> str:
    """Only passed modules and their dependencies invalidate a checkpoint."""
    if stage not in STAGES:
        raise ValueError("CHECKPOINT_STAGE_INVALID")
    root = root or Path(__file__).resolve().parents[3]
    packages = ["requirements", "llm_runtime", "requirement_extraction", "workflow_contracts",
                "requirement_subgraph", "artifact_persistence"]
    roles = [ModelRole.REQUIREMENT_EXTRACTION]
    if STAGES.index(stage) >= 1:
        packages += ["baidu_map", "hotel_selection"]
    if STAGES.index(stage) >= 2:
        packages += ["candidate_research"]
        roles += [ModelRole.CANDIDATE_RESEARCH]
    if STAGES.index(stage) >= 3:
        packages += ["itinerary_planning"]
        roles += [ModelRole.ITINERARY_PLANNING]
    files = [root / "pyproject.toml"]
    for package in packages:
        files.extend(path for path in (root / "app" / package).rglob("*")
                     if path.is_file() and path.suffix in {".py", ".txt", ".ini"}
                     and path != root / "app/llm_runtime/profiles.py")
    payload = {
        "checkpoint_contract": "live-stage-v1",
        "files": {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(files)},
        "profiles": {role.value: get_profile(role).model_dump(mode="json") for role in roles},
        "packages": {item.metadata["Name"]: item.version for item in distributions()},
        "python": list(sys.version_info[:3]),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def dump_identity(directory: Path) -> tuple[str, int]:
    path = directory / "database.dump"
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
        raise ValueError("CHECKPOINT_DUMP_INVALID")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest(), path.stat().st_size


def seal_checkpoint(directory: Path, *, source_tag: str, source_image: str,
                    manifest: StageManifest, calls: dict[str, int], purpose: Literal["live", "offline"]) -> CheckpointMetadata:
    if (directory / "manifest.json").exists():
        raise ValueError("CHECKPOINT_ALREADY_SEALED")
    digest, size = dump_identity(directory)
    metadata = CheckpointMetadata(source_tag=source_tag, source_image=source_image, manifest=manifest,
                                  database_sha256=digest, database_size=size, calls=calls, purpose=purpose)
    if purpose == "live":
        needed = {"requirements": ("llm_invoke_json",), "hotel": ("map_search_places",),
                  "research": ("llm_invoke_json",),
                  "planning": ("map_calculate_route_matrix", "map_plan_directions")}[manifest.stage]
        if not all(calls.get(name, 0) > 0 for name in needed):
            raise ValueError("CHECKPOINT_LIVE_CALLS_MISSING")
    temporary = directory / "manifest.pending"
    temporary.write_text(metadata.model_dump_json(), encoding="utf-8")
    temporary.replace(directory / "manifest.json")
    return metadata


def inspect_checkpoint(directory: Path, *, source_tag: str, stage: Stage,
                       purpose: Literal["live", "offline"] = "live") -> CheckpointMetadata:
    path = directory / "manifest.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("CHECKPOINT_MANIFEST_INVALID")
    metadata = CheckpointMetadata.model_validate_json(path.read_bytes(), strict=True)
    if metadata.source_tag != source_tag or metadata.manifest.stage != stage or metadata.purpose != purpose:
        raise ValueError("CHECKPOINT_IDENTITY_MISMATCH")
    if dump_identity(directory) != (metadata.database_sha256, metadata.database_size):
        raise ValueError("CHECKPOINT_DUMP_MISMATCH")
    if metadata.manifest.source_fingerprint != source_fingerprint(stage):
        raise ValueError("CHECKPOINT_SOURCE_CHANGED")
    return metadata


def main() -> int:
    try:
        directory = Path("/checkpoint")
        if sys.argv[1:] == ["seal"]:
            seal_checkpoint(directory, source_tag=os.environ["CHECKPOINT_SOURCE_TAG"],
                            source_image=os.environ["CHECKPOINT_SOURCE_IMAGE"],
                            manifest=StageManifest.model_validate_json(os.environ["CHECKPOINT_STAGE_MANIFEST"], strict=True),
                            calls=json.loads(os.environ["CHECKPOINT_CALLS"]), purpose=os.environ["CHECKPOINT_PURPOSE"])
        elif sys.argv[1:] != ["inspect"]:
            raise ValueError("CHECKPOINT_MODE_INVALID")
        metadata = inspect_checkpoint(directory, source_tag=os.environ["CHECKPOINT_SOURCE_TAG"],
                                      stage=os.environ["CHECKPOINT_STAGE"], purpose=os.environ["CHECKPOINT_PURPOSE"])
        print("CHECKPOINT_METADATA " + metadata.model_dump_json(), flush=True)
        return 0
    except Exception:
        print("CHECKPOINT_ERROR VALIDATION_FAILED", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
