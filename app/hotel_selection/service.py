"""Module 07 候选发现、重放、预算和人工选择编排。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pydantic import TypeAdapter, ValidationError

from app.baidu_map import (
    BaiduCoordinate,
    BaiduMapAdapter,
    BaiduMapError,
    BaiduMapErrorCode,
    PlaceSummary,
    PlaceSearchNoResult,
    PlaceSearchOutcome,
    PlaceSearchRequest,
    PlaceSearchSuccess,
)
from app.requirements import ConfirmedTravelRequirement
from app.workflow_contracts import (
    ArtifactAbsent,
    ArtifactFound,
    ArtifactRef,
    ArtifactRepository,
    ArtifactConflictError,
    BudgetConsumed,
    BudgetConsumeRequest,
    BudgetExceeded,
    ExecutionBudgetManager,
    OperationId,
    OperationResultIndeterminateError,
    UtcClock,
    WorkflowPhase,
)

from .artifacts import (
    HotelCandidatesInsufficientRecord,
    HotelCandidatesReadyRecord,
    HotelDeclinedDecision,
    HotelDiscoveryArtifact,
    HotelPlaceSearchArtifact,
    HotelSearchEvidence,
    HotelSearchFailedEvidence,
    HotelSearchNoResultEvidence,
    HotelSearchSuccessEvidence,
    HotelSelectedDecision,
    HotelSelectionArtifact,
    _artifact_write_request,
)
from .errors import HotelInputError, HotelReplayError
from .models import (
    HOTEL_PLACE_CALL_BUDGET_KEY,
    HotelCandidate,
    HotelCandidateDiscoveryFailed,
    HotelCandidateDiscoveryFailureCode,
    HotelCandidateDiscoveryOutcome,
    HotelCandidateOperationIds,
    HotelCandidatesInsufficient,
    HotelCandidatesReady,
    HotelCoordinate,
    HotelDeclineCommand,
    HotelMapFailureCode,
    HotelPlaceEvidence,
    HotelSelected,
    HotelSelectionDeclined,
    HotelSelectionOutcome,
    HotelSelectionRejected,
    HotelSelectionRejectionCode,
    HotelSelectionRequest,
    HotelSelectCommand,
)
from .query_policy import hash_query_plan, hash_request, project_candidates, build_queries


_MAP_FAILURE_CODES = {
    BaiduMapErrorCode.REQUEST_ERROR: HotelMapFailureCode.REQUEST_ERROR,
    BaiduMapErrorCode.CONFIGURATION_ERROR: HotelMapFailureCode.CONFIGURATION_ERROR,
    BaiduMapErrorCode.AUTHENTICATION_ERROR: HotelMapFailureCode.AUTHENTICATION_ERROR,
    BaiduMapErrorCode.RATE_LIMIT_ERROR: HotelMapFailureCode.RATE_LIMIT_ERROR,
    BaiduMapErrorCode.TIMEOUT_ERROR: HotelMapFailureCode.TIMEOUT_ERROR,
    BaiduMapErrorCode.TRANSPORT_ERROR: HotelMapFailureCode.TRANSPORT_ERROR,
    BaiduMapErrorCode.SESSION_ERROR: HotelMapFailureCode.SESSION_ERROR,
    BaiduMapErrorCode.TOOL_UNAVAILABLE: HotelMapFailureCode.TOOL_UNAVAILABLE,
    BaiduMapErrorCode.TOOL_SCHEMA_ERROR: HotelMapFailureCode.TOOL_SCHEMA_ERROR,
    BaiduMapErrorCode.PROTOCOL_ERROR: HotelMapFailureCode.PROTOCOL_ERROR,
    BaiduMapErrorCode.RESPONSE_ERROR: HotelMapFailureCode.RESPONSE_ERROR,
    BaiduMapErrorCode.CONCURRENCY_ERROR: HotelMapFailureCode.CONCURRENCY_ERROR,
}


def _fresh(model_type: object, value: object) -> object:
    try:
        python_tree = value.model_dump(
            mode="python",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        first = TypeAdapter(model_type).validate_python(python_tree, strict=True)
        json_tree = first.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        encoded = json.dumps(
            json_tree,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return TypeAdapter(model_type).validate_json(encoded, strict=True)
    except (AttributeError, TypeError, ValueError, ValidationError, UnicodeError):
        raise HotelInputError() from None


def _fresh_exact(model_type: type[object], value: object) -> object:
    if type(value) is not model_type:
        raise HotelInputError()
    return _fresh(model_type, value)


def _same_operation(left: OperationId, right: OperationId) -> bool:
    return left == right


class HotelCandidateSelectionService:
    def __init__(
        self,
        adapter: BaiduMapAdapter,
        repository: ArtifactRepository,
        budget_manager: ExecutionBudgetManager,
        utc_clock: UtcClock,
    ) -> None:
        if not isinstance(adapter, BaiduMapAdapter):
            raise HotelInputError()
        for dependency, methods in (
            (repository, ("put", "lookup", "get")),
            (budget_manager, ("consume", "get_snapshot", "get_all_snapshots")),
            (utc_clock, ("now",)),
        ):
            if any(not callable(getattr(dependency, method, None)) for method in methods):
                raise HotelInputError()
        self._adapter = adapter
        self._repository = repository
        self._budget_manager = budget_manager
        self._utc_clock = utc_clock

    def _now(self) -> datetime:
        try:
            value = self._utc_clock.now()
        except (AttributeError, TypeError, ValueError):
            raise HotelInputError() from None
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
        ):
            raise HotelInputError()
        return value

    @staticmethod
    def _evidence_places(evidence: HotelSearchSuccessEvidence) -> tuple[PlaceSummary, ...]:
        return tuple(
            PlaceSummary(
                uid=item.uid,
                name=item.name,
                address=item.address,
                coordinate=BaiduCoordinate(
                    latitude=item.coordinate.latitude,
                    longitude=item.coordinate.longitude,
                ),
                category=item.category,
            )
            for item in evidence.places
        )

    @staticmethod
    def _public_discovery_outcome(
        result: HotelCandidatesReadyRecord | HotelCandidatesInsufficientRecord,
        candidate_set_ref: ArtifactRef,
    ) -> HotelCandidateDiscoveryOutcome:
        if type(result) is HotelCandidatesReadyRecord:
            return HotelCandidatesReady(
                candidate_set_ref=candidate_set_ref,
                candidates=tuple(result.candidates),
            )
        return HotelCandidatesInsufficient(
            candidate_set_ref=candidate_set_ref,
            qualified_candidates=tuple(result.candidates),
            searches_attempted=3,
        )

    @staticmethod
    def _failure(
        *,
        reason: HotelCandidateDiscoveryFailureCode,
        ordinal: int,
        qualified_count: int,
        search_ref: ArtifactRef | None,
    ) -> HotelCandidateDiscoveryFailed:
        return HotelCandidateDiscoveryFailed(
            reason=reason,
            failed_search_ordinal=ordinal,
            qualified_count=qualified_count,
            failure_search_ref=search_ref,
        )

    @staticmethod
    def _merge_places(
        places: tuple[object, ...],
        identity_sources: dict[str, object],
        candidates: list[HotelCandidate],
        emitted_uids: set[str],
    ) -> None:
        for place in places:
            previous = identity_sources.get(place.uid)
            if previous is None:
                identity_sources[place.uid] = place
            else:
                project_candidates((previous, place))
            projected = project_candidates((place,))
            if projected and place.uid not in emitted_uids and len(candidates) < 5:
                candidates.append(projected[0])
                emitted_uids.add(place.uid)

    async def _replay_candidate_set(
        self,
        *,
        confirmed: ConfirmedTravelRequirement,
        operation_ids: HotelCandidateOperationIds,
        query_plan_hash: object,
    ) -> HotelCandidateDiscoveryOutcome | None:
        lookup = await self._repository.lookup(
            operation_id=operation_ids.candidate_set_operation_id,
        )
        if type(lookup) is ArtifactAbsent:
            return None
        if type(lookup) is not ArtifactFound:
            raise HotelReplayError()
        read = await self._repository.get(
            lookup.ref,
            expected_model=HotelDiscoveryArtifact,
        )
        if read.ref.operation_id != operation_ids.candidate_set_operation_id:
            raise HotelReplayError()
        content = read.content
        if (
            content.confirmation_summary_digest != confirmed.confirmation_summary_digest
            or content.query_plan_hash != query_plan_hash
            or content.search_operation_ids != operation_ids.search_operation_ids
        ):
            raise HotelReplayError()
        return self._public_discovery_outcome(content.result, read.ref)

    async def _read_search_slot(
        self,
        *,
        operation_id: OperationId,
        ordinal: int,
        request: PlaceSearchRequest,
        request_hash: object,
    ) -> tuple[ArtifactRef | None, HotelSearchEvidence | None, tuple[PlaceSummary, ...] | None]:
        lookup = await self._repository.lookup(operation_id=operation_id)
        if type(lookup) is ArtifactFound:
            read = await self._repository.get(
                lookup.ref,
                expected_model=HotelPlaceSearchArtifact,
            )
            if (
                read.ref.operation_id != operation_id
                or read.content.search_ordinal != ordinal
                or read.content.request_hash != request_hash
            ):
                raise HotelReplayError()
            evidence = read.content.evidence
            if type(evidence) is HotelSearchSuccessEvidence:
                return read.ref, evidence, self._evidence_places(evidence)
            if type(evidence) is HotelSearchNoResultEvidence:
                return read.ref, evidence, None
            return read.ref, evidence, None
        if type(lookup) is not ArtifactAbsent:
            raise HotelReplayError()

        budget = await self._budget_manager.consume(
            BudgetConsumeRequest(
                operation_id=operation_id,
                key=HOTEL_PLACE_CALL_BUDGET_KEY,
                amount=1,
            )
        )
        if type(budget) is BudgetExceeded:
            return None, None, None
        if type(budget) is not BudgetConsumed:
            raise HotelReplayError()
        if budget.disposition == "replayed":
            raise OperationResultIndeterminateError()
        if budget.disposition != "applied":
            raise HotelReplayError()

        try:
            map_outcome = await self._adapter.search_places(request)
        except BaiduMapError as error:
            try:
                failure_code = _MAP_FAILURE_CODES[error.code]
            except KeyError:
                raise HotelInputError() from None
            search_artifact = HotelPlaceSearchArtifact(
                schema_version="1.0",
                search_ordinal=ordinal,
                request_hash=request_hash,
                source="baidu_maps_mcp",
                verified=False,
                retrieved_at=self._now(),
                evidence=HotelSearchFailedEvidence(
                    schema_version="1.0",
                    outcome_type="failed",
                    failure_code=failure_code,
                ),
            )
            search_ref = await self._repository.put(
                _artifact_write_request(
                    operation_id=operation_id,
                    artifact_type="hotel.place_search",
                    artifact=search_artifact,
                )
            )
            return search_ref, search_artifact.evidence, None

        if type(map_outcome) not in (PlaceSearchSuccess, PlaceSearchNoResult):
            raise HotelInputError()
        map_outcome = _fresh(PlaceSearchOutcome, map_outcome)
        provenance = map_outcome.provenance
        if provenance.verified is not True:
            evidence: HotelSearchEvidence = HotelSearchFailedEvidence(
                schema_version="1.0",
                outcome_type="failed",
                failure_code=HotelMapFailureCode.UNVERIFIED_RESULT,
            )
            places: tuple[object, ...] | None = None
        elif type(map_outcome) is PlaceSearchSuccess:
            evidence = HotelSearchSuccessEvidence(
                schema_version="1.0",
                outcome_type="success",
                places=tuple(
                    HotelPlaceEvidence(
                        schema_version="1.0",
                        uid=place.uid,
                        name=place.name,
                        address=place.address,
                        coordinate=HotelCoordinate(
                            schema_version="1.0",
                            coordinate_system="bd09ll",
                            latitude=place.coordinate.latitude,
                            longitude=place.coordinate.longitude,
                        ),
                        category=place.category,
                    )
                    for place in map_outcome.places
                ),
            )
            places = tuple(map_outcome.places)
        else:
            evidence = HotelSearchNoResultEvidence(
                schema_version="1.0",
                outcome_type="no_result",
            )
            places = None
        search_artifact = HotelPlaceSearchArtifact(
            schema_version="1.0",
            search_ordinal=ordinal,
            request_hash=request_hash,
            source=provenance.source,
            verified=provenance.verified is True and type(evidence) is not HotelSearchFailedEvidence,
            retrieved_at=provenance.retrieved_at,
            evidence=evidence,
        )
        search_ref = await self._repository.put(
            _artifact_write_request(
                operation_id=operation_id,
                artifact_type="hotel.place_search",
                artifact=search_artifact,
            )
        )
        return search_ref, search_artifact.evidence, places

    async def discover_candidates(
        self,
        confirmed: ConfirmedTravelRequirement,
        operation_ids: HotelCandidateOperationIds,
    ) -> HotelCandidateDiscoveryOutcome:
        fresh_confirmed = _fresh_exact(ConfirmedTravelRequirement, confirmed)
        fresh_operation_ids = _fresh_exact(HotelCandidateOperationIds, operation_ids)
        requests = build_queries(fresh_confirmed)
        plan_hash = hash_query_plan(requests)
        replay = await self._replay_candidate_set(
            confirmed=fresh_confirmed,
            operation_ids=fresh_operation_ids,
            query_plan_hash=plan_hash,
        )
        if replay is not None:
            return _fresh(type(replay), replay)

        identity_sources: dict[str, object] = {}
        candidates: list[HotelCandidate] = []
        emitted_uids: set[str] = set()
        search_refs: list[ArtifactRef] = []
        for ordinal, (operation_id, request) in enumerate(
            zip(fresh_operation_ids.search_operation_ids, requests, strict=True),
            start=1,
        ):
            search_ref, evidence, places = await self._read_search_slot(
                operation_id=operation_id,
                ordinal=ordinal,
                request=request,
                request_hash=hash_request(request),
            )
            if search_ref is None:
                return self._failure(
                    reason=HotelCandidateDiscoveryFailureCode.BUDGET_EXHAUSTED,
                    ordinal=ordinal,
                    qualified_count=len(candidates),
                    search_ref=None,
                )
            if evidence is None:
                raise HotelReplayError()
            search_refs.append(search_ref)
            if type(evidence) is HotelSearchFailedEvidence:
                return self._failure(
                    reason=HotelCandidateDiscoveryFailureCode.MAP_CALL_FAILED,
                    ordinal=ordinal,
                    qualified_count=len(candidates),
                    search_ref=search_ref,
                )
            if places is not None:
                self._merge_places(places, identity_sources, candidates, emitted_uids)
                if len(candidates) >= 3:
                    break

        if len(candidates) >= 3:
            result: HotelCandidatesReadyRecord | HotelCandidatesInsufficientRecord = HotelCandidatesReadyRecord(
                schema_version="1.0",
                result_type="ready",
                candidates=tuple(candidates[:5]),
            )
        else:
            result = HotelCandidatesInsufficientRecord(
                schema_version="1.0",
                result_type="insufficient",
                candidates=tuple(candidates[:2]),
            )
        discovery_artifact = HotelDiscoveryArtifact(
            schema_version="1.0",
            confirmation_summary_digest=fresh_confirmed.confirmation_summary_digest,
            query_plan_hash=plan_hash,
            search_operation_ids=fresh_operation_ids.search_operation_ids,
            search_refs=tuple(search_refs),
            result=result,
        )
        candidate_set_ref = await self._repository.put(
            _artifact_write_request(
                operation_id=fresh_operation_ids.candidate_set_operation_id,
                artifact_type="hotel.candidate_set",
                artifact=discovery_artifact,
            )
        )
        return _fresh(
            HotelCandidatesReady if type(result) is HotelCandidatesReadyRecord else HotelCandidatesInsufficient,
            self._public_discovery_outcome(result, candidate_set_ref),
        )

    @staticmethod
    def _selection_matches(
        *,
        command: object,
        artifact: HotelSelectionArtifact,
        candidate_set_ref: ArtifactRef,
        candidates: tuple[HotelCandidate, ...],
    ) -> bool:
        if artifact.candidate_set_ref != candidate_set_ref:
            return False
        if type(command) is HotelSelectCommand and type(artifact.decision) is HotelSelectedDecision:
            expected = next(
                (candidate for candidate in candidates if candidate.uid == command.selected_uid),
                None,
            )
            return expected is not None and artifact.decision.selected_candidate == expected
        return type(command) is HotelDeclineCommand and type(artifact.decision) is HotelDeclinedDecision

    @staticmethod
    def _selection_outcome(
        artifact: HotelSelectionArtifact,
        selection_ref: ArtifactRef,
    ) -> HotelSelectionOutcome:
        if type(artifact.decision) is HotelSelectedDecision:
            return HotelSelected(
                selection_ref=selection_ref,
                selected_candidate=artifact.decision.selected_candidate,
            )
        if type(artifact.decision) is HotelDeclinedDecision:
            return HotelSelectionDeclined(selection_ref=selection_ref)
        raise HotelReplayError()

    async def record_selection(
        self,
        request: HotelSelectionRequest,
        *,
        active_candidate_set_ref: ArtifactRef,
    ) -> HotelSelectionOutcome:
        fresh_request = _fresh_exact(HotelSelectionRequest, request)
        fresh_active_ref = _fresh_exact(ArtifactRef, active_candidate_set_ref)
        if fresh_request.candidate_set_ref != fresh_active_ref:
            return HotelSelectionRejected(reason=HotelSelectionRejectionCode.STALE_CANDIDATE_SET)

        candidate_read = await self._repository.get(
            fresh_active_ref,
            expected_model=HotelDiscoveryArtifact,
        )
        if candidate_read.ref != fresh_active_ref:
            raise HotelReplayError()
        selection_operation = fresh_request.selection_operation_id
        search_operations = candidate_read.content.search_operation_ids
        if (
            selection_operation.phase is not WorkflowPhase.HOTEL_SELECTION
            or selection_operation.run_id != candidate_read.ref.operation_id.run_id
            or selection_operation == candidate_read.ref.operation_id
            or any(_same_operation(selection_operation, item) for item in search_operations)
        ):
            return HotelSelectionRejected(reason=HotelSelectionRejectionCode.DECISION_CONFLICT)
        result = candidate_read.content.result
        if type(result) is not HotelCandidatesReadyRecord:
            return HotelSelectionRejected(reason=HotelSelectionRejectionCode.CANDIDATE_SET_NOT_READY)

        lookup = await self._repository.lookup(operation_id=selection_operation)
        if type(lookup) is ArtifactFound:
            read = await self._repository.get(lookup.ref, expected_model=HotelSelectionArtifact)
            if read.ref.operation_id != selection_operation:
                raise HotelReplayError()
            if self._selection_matches(
                command=fresh_request.command,
                artifact=read.content,
                candidate_set_ref=fresh_active_ref,
                candidates=result.candidates,
            ):
                outcome = self._selection_outcome(read.content, read.ref)
                return _fresh(type(outcome), outcome)
            return HotelSelectionRejected(reason=HotelSelectionRejectionCode.DECISION_CONFLICT)
        if type(lookup) is not ArtifactAbsent:
            raise HotelReplayError()

        if type(fresh_request.command) is HotelSelectCommand:
            selected = next(
                (item for item in result.candidates if item.uid == fresh_request.command.selected_uid),
                None,
            )
            if selected is None:
                return HotelSelectionRejected(reason=HotelSelectionRejectionCode.CANDIDATE_UID_NOT_FOUND)
            decision = HotelSelectedDecision(
                schema_version="1.0",
                decision_type="selected",
                selected_candidate=_fresh_exact(HotelCandidate, selected),
            )
        else:
            decision = HotelDeclinedDecision(schema_version="1.0", decision_type="declined")
        selection_artifact = HotelSelectionArtifact(
            schema_version="1.0",
            candidate_set_ref=fresh_active_ref,
            decision=decision,
        )
        selection_write = _artifact_write_request(
            operation_id=selection_operation,
            artifact_type="hotel.selection",
            artifact=selection_artifact,
        )
        try:
            selection_ref = await self._repository.put(selection_write)
        except ArtifactConflictError:
            race_lookup = await self._repository.lookup(operation_id=selection_operation)
            if type(race_lookup) is ArtifactAbsent:
                raise HotelReplayError() from None
            if type(race_lookup) is not ArtifactFound:
                raise HotelReplayError()
            race_read = await self._repository.get(
                race_lookup.ref,
                expected_model=HotelSelectionArtifact,
            )
            if race_read.ref.operation_id != selection_operation:
                raise HotelReplayError()
            if self._selection_matches(
                command=fresh_request.command,
                artifact=race_read.content,
                candidate_set_ref=fresh_active_ref,
                candidates=result.candidates,
            ):
                outcome = self._selection_outcome(race_read.content, race_read.ref)
                return _fresh(type(outcome), outcome)
            return HotelSelectionRejected(reason=HotelSelectionRejectionCode.DECISION_CONFLICT)
        outcome = self._selection_outcome(selection_artifact, selection_ref)
        return _fresh(type(outcome), outcome)


__all__ = ("HotelCandidateSelectionService",)
