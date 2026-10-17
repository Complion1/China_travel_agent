"""M24 public projections and recording services; no startup side effects."""
from .models import PublicFact, PublicTrip, PublicRecordBody, PublicRecord, PublicProgressPage, PublicProgressError
from .store import PublicProgressStore
from .recorder import PublicProgressRecorder, ObservedArtifactRepository


async def verify_progress_schema(connection) -> None:
    from .migrate import verify_schema
    await verify_schema(connection)

__all__ = ["PublicFact", "PublicTrip", "PublicRecordBody", "PublicRecord", "PublicProgressPage",
    "PublicProgressError", "PublicProgressStore", "PublicProgressRecorder", "ObservedArtifactRepository", "verify_progress_schema"]
