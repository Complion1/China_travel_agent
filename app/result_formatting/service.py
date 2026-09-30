"""Read-only result generation and complete-index verification (D §§4, 10–11)."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from pydantic import BaseModel

from app.itinerary_planning import ItineraryPlanningArtifact
from app.workflow_contracts import (
    ArtifactIntegrityError, ArtifactNotFoundError, ArtifactReadResult, ArtifactRef,
    ArtifactRepository, ArtifactRepositoryDependencyError, ArtifactRepositoryError,
    ArtifactTooLargeError, parse_travel_session_state_json, serialize_travel_session_state,
)

from .artifacts import (
    ResultCore, ResultDayArtifact, ResultDayEntry, ResultIndexArtifact,
    fresh_model, validate_artifact_size,
)
from .errors import ResultFormattingFailureCode as Code, ResultFormattingUsageError
from .models import (
    ResultFormattingFailed, ResultFormattingLoaded, ResultFormattingOutcome,
    ResultFormattingPrepared, ResultFormattingRequest, ResultIndexOutcome,
    ResultIndexReady, ResultReadOutcome, ResultReadRequest,
)
from .projection import (
    ProjectionFailure, bundle_digest, input_digest, projection_steps, require_ready,
    require_ready_state,
)
from .renderer import render_day, render_footer, render_header


def _require(condition: bool, code: Code = Code.SOURCE_MISMATCH) -> None:
    if not condition:
        raise ProjectionFailure(code) from None


def _fresh_request(value, expected_type):
    try:
        fresh = fresh_model(value, expected_type)
        state = parse_travel_session_state_json(serialize_travel_session_state(fresh.state))
        ref_name = "itinerary_ref" if expected_type is ResultFormattingRequest else "index_ref"
        return expected_type(schema_version="2.0", state=state, **{ref_name: getattr(fresh, ref_name)})
    except Exception:
        raise ResultFormattingUsageError() from None


def _failed(error: Exception) -> ResultFormattingFailed:
    if isinstance(error, ProjectionFailure):
        code = error.code
    elif isinstance(error, ArtifactNotFoundError):
        code = Code.ARTIFACT_NOT_FOUND
    elif isinstance(error, ArtifactIntegrityError):
        code = Code.ARTIFACT_INTEGRITY_ERROR
    elif isinstance(error, ArtifactRepositoryDependencyError):
        code = Code.ARTIFACT_DEPENDENCY_ERROR
    elif isinstance(error, ArtifactTooLargeError):
        code = Code.RESULT_TOO_LARGE
    elif isinstance(error, ArtifactRepositoryError):
        code = Code.ARTIFACT_CONTRACT_ERROR
    else:
        code = Code.INTERNAL_ERROR
    return ResultFormattingFailed(outcome_type="failed", schema_version="2.0", code=code)


@dataclass(repr=False)
class _Reads:
    repository: ArtifactRepository
    run_id: UUID
    cache: dict[UUID, tuple[ArtifactRef, type[BaseModel], ArtifactReadResult]] = field(default_factory=dict)

    async def get(self, ref: ArtifactRef, model: type[BaseModel]) -> ArtifactReadResult:
        _require(ref.operation_id.run_id == self.run_id)
        if ref.artifact_id in self.cache:
            prior_ref, prior_model, result = self.cache[ref.artifact_id]
            _require(prior_ref == ref)
            _require(prior_model is model, Code.ARTIFACT_CONTRACT_ERROR)
            return result
        returned = await self.repository.get(ref, expected_model=model)
        try:
            if not isinstance(returned, ArtifactReadResult):
                raise ResultFormattingUsageError()
            saved_ref = fresh_model(returned.ref, ArtifactRef)
            content = fresh_model(returned.content, model)
        except ResultFormattingUsageError:
            raise ArtifactIntegrityError() from None
        _require(saved_ref == ref)
        result = ArtifactReadResult(ref=saved_ref, content=content)
        self.cache[ref.artifact_id] = (saved_ref, model, result)
        return result


@dataclass(frozen=True, repr=False)
class _Bundle:
    core: ResultCore
    days: tuple[ResultDayArtifact, ...]
    header: str
    footer: str


def _result_ref(ref: ArtifactRef, *, kind: str, run_id: UUID) -> None:
    _require(ref.operation_id.run_id == run_id and ref.operation_id.phase.value == "output_formatting")
    _require(ref.artifact_type == "result_formatting." + kind, Code.ARTIFACT_CONTRACT_ERROR)
    _require(ref.schema_version == "2.0", Code.UNSUPPORTED_RESULT_VERSION)


async def _reconstruct(request: ResultFormattingRequest, reads: _Reads) -> _Bundle:
    require_ready(request)
    ref = request.itinerary_ref
    _require(ref.artifact_type == "itinerary_planning.itinerary" and ref.schema_version == "2.0", Code.ARTIFACT_CONTRACT_ERROR)
    _require(ref.operation_id.phase.value == "itinerary_planning")
    itinerary = (await reads.get(ref, ItineraryPlanningArtifact)).content
    steps = projection_steps(request, itinerary)
    try:
        ref, model = next(steps)
        while True:
            result = await reads.get(ref, model)
            ref, model = steps.send(result)
    except StopIteration as finished:
        core, day_data = finished.value
    finally:
        steps.close()
    input_hash, bundle_hash = input_digest(request), bundle_digest(request, core, day_data)
    # Validate all visible text even when prepare only returns daily fragments.
    header, footer = render_header(core), render_footer(core)
    days = tuple(ResultDayArtifact(
        schema_version="2.0", template_version="zh-guide-v1", run_id=request.state.run_id,
        source_itinerary_ref=request.itinerary_ref, input_digest=input_hash, bundle_digest=bundle_hash,
        data=day, markdown=render_day(core, day),
    ) for day in day_data)
    for day in days:
        validate_artifact_size(day)
    return _Bundle(core=core, days=days, header=header, footer=footer)


async def _checked_days(bundle: _Bundle, refs: tuple[ArtifactRef, ...], reads: _Reads) -> tuple[ResultDayArtifact, ...]:
    _require(len(refs) == len(bundle.days))
    _require(len({ref.artifact_id for ref in refs}) == len(refs))
    _require(len({ref.operation_id for ref in refs}) == len(refs))
    for ref in refs:
        _result_ref(ref, kind="day", run_id=reads.run_id)
    days = []
    for ref, expected in zip(refs, bundle.days, strict=True):
        saved = (await reads.get(ref, ResultDayArtifact)).content
        _require(saved == expected)
        days.append(saved)
    return tuple(days)


def _index(bundle: _Bundle, refs: tuple[ArtifactRef, ...]) -> ResultIndexArtifact:
    first = bundle.days[0]
    index = ResultIndexArtifact(
        schema_version="2.0", template_version="zh-guide-v1", run_id=first.run_id,
        source_itinerary_ref=first.source_itinerary_ref, input_digest=first.input_digest,
        bundle_digest=first.bundle_digest, core=bundle.core,
        days=tuple(ResultDayEntry(day_no=day.data.day_no, date=day.data.date, ref=ref)
                   for day, ref in zip(bundle.days, refs, strict=True)),
        markdown_header=bundle.header, markdown_footer=bundle.footer,
    )
    validate_artifact_size(index)
    return index


class ResultFormattingService:
    def __init__(self, *, artifact_repository: ArtifactRepository) -> None:
        if not callable(getattr(artifact_repository, "get", None)):
            raise ResultFormattingUsageError() from None
        self._repository = artifact_repository

    async def prepare(self, request: ResultFormattingRequest) -> ResultFormattingOutcome:
        fresh = _fresh_request(request, ResultFormattingRequest)
        try:
            bundle = await _reconstruct(fresh, _Reads(self._repository, fresh.state.run_id))
            return ResultFormattingPrepared(outcome_type="prepared", schema_version="2.0", days=bundle.days)
        except Exception as error:
            return _failed(error)

    async def build_index(self, request: ResultFormattingRequest, *, day_refs: tuple[ArtifactRef, ...]) -> ResultIndexOutcome:
        fresh = _fresh_request(request, ResultFormattingRequest)
        if type(day_refs) is not tuple:
            raise ResultFormattingUsageError() from None
        refs = tuple(fresh_model(ref, ArtifactRef) for ref in day_refs)
        try:
            reads = _Reads(self._repository, fresh.state.run_id)
            bundle = await _reconstruct(fresh, reads)
            await _checked_days(bundle, refs, reads)
            return ResultIndexReady(outcome_type="index_ready", schema_version="2.0", index=_index(bundle, refs))
        except Exception as error:
            return _failed(error)

    async def load(self, request: ResultReadRequest) -> ResultReadOutcome:
        fresh = _fresh_request(request, ResultReadRequest)
        try:
            require_ready_state(fresh.state)
            _require(fresh.index_ref in fresh.state.artifact_refs)
            _result_ref(fresh.index_ref, kind="index", run_id=fresh.state.run_id)
            reads = _Reads(self._repository, fresh.state.run_id)
            index = (await reads.get(fresh.index_ref, ResultIndexArtifact)).content
            _require(index.run_id == fresh.state.run_id)
            refs = tuple(day.ref for day in index.days)
            _require(fresh.index_ref.operation_id not in {ref.operation_id for ref in refs})
            # Read all result blocks before following the source itinerary.
            for ref in refs:
                _result_ref(ref, kind="day", run_id=fresh.state.run_id)
                await reads.get(ref, ResultDayArtifact)
            source_request = ResultFormattingRequest(schema_version="2.0", state=fresh.state, itinerary_ref=index.source_itinerary_ref)
            bundle = await _reconstruct(source_request, reads)
            days = await _checked_days(bundle, refs, reads)
            _require(index == _index(bundle, refs))
            markdown = index.markdown_header + "\n\n" + "\n\n".join(day.markdown for day in days) + "\n\n" + index.markdown_footer + "\n"
            return ResultFormattingLoaded(outcome_type="loaded", schema_version="2.0", index_ref=fresh.index_ref,
                                          index=index, days=days, markdown=markdown)
        except Exception as error:
            return _failed(error)
