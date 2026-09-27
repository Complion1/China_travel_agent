"""Deterministic projection of public, persisted source artifacts (D §§6–7).

The step generator describes reads; it never performs I/O. Both the asynchronous
service and the pure test adapter drive the same bounded validation algorithm.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Generator, Mapping
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.candidate_research import CandidateResearchArtifact, CandidateResearchPlaceObservationArtifact
from app.hotel_selection import HotelDiscoveryArtifact, HotelPlaceSearchArtifact, HotelSelectionArtifact
from app.itinerary_planning import (
    ItineraryPlanningArtifact, ItineraryPlanningDirectionsObservationArtifact,
    ItineraryPlanningHubGeocodeObservationArtifact, ItineraryPlanningModelActionArtifact,
    ItineraryPlanningRouteMatrixObservationArtifact,
)
from app.requirements import ConfirmedTravelRequirement
from app.workflow_contracts import ArtifactReadResult, ArtifactRef

from .artifacts import (
    ResultBackup, ResultCoordinate, ResultCore, ResultDayData, ResultEndpoint,
    ResultInterval, ResultPlace, ResultRequirement, ResultRoute, ResultSource,
    ResultTransfer, ResultVisit, fresh_model,
)
from .errors import ResultFormattingUsageError
from .models import ResultFormattingFailureCode as Code, ResultFormattingRequest


class ProjectionFailure(Exception):
    """Internal control flow carrying only a fixed, non-sensitive code."""

    def __init__(self, code: Code = Code.SOURCE_MISMATCH) -> None:
        self.code = code
        super().__init__(code.value)


def _require(condition: bool, code: Code = Code.SOURCE_MISMATCH) -> None:
    if not condition:
        raise ProjectionFailure(code) from None


def _json(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=False, by_alias=False, exclude_computed_fields=True)


def _hash(value: object, *, normalize_zero: bool = False) -> str:
    def normalized(item: object) -> object:
        if type(item) is float and item == 0:
            return 0.0
        if isinstance(item, dict):
            return {key: normalized(val) for key, val in item.items()}
        if isinstance(item, (tuple, list)):
            return [normalized(val) for val in item]
        return item

    tree = normalized(value) if normalize_zero else value
    encoded = json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def confirmed_digest(confirmed: ConfirmedTravelRequirement) -> str:
    supplied = confirmed.confirmation_summary_digest
    return supplied if re.fullmatch(r"sha256:[0-9a-f]{64}", supplied) else _hash(_json(confirmed))


def input_digest(request: ResultFormattingRequest) -> str:
    return _hash({"version": "result-input-v1", "run_id": str(request.state.run_id),
                  "requirement": _json(request.state.requirement_outcome.requirement),
                  "itinerary_ref": _json(request.itinerary_ref)}, normalize_zero=True)


def bundle_digest(request: ResultFormattingRequest, core: ResultCore, days: tuple[ResultDayData, ...]) -> str:
    return _hash({"version": "result-bundle-v1", "template_version": "zh-guide-v1",
                  "input_digest": input_digest(request), "core": _json(core),
                  "days": [_json(day) for day in days]}, normalize_zero=True)


def require_ready_state(state: Any) -> None:
    _require(type(state.requirement_outcome) is ConfirmedTravelRequirement, Code.INPUT_NOT_READY)
    _require(state.delivery_status.value in {"in_progress", "demo_ready", "degraded"}, Code.INPUT_NOT_READY)
    _require(state.phase.value in {"itinerary_planning", "output_formatting"}, Code.INPUT_NOT_READY)


def require_ready(request: ResultFormattingRequest) -> None:
    state = request.state
    require_ready_state(state)
    _require(request.itinerary_ref.operation_id.run_id == state.run_id)
    _require(request.itinerary_ref in state.artifact_refs)


def project_requirement(confirmed: ConfirmedTravelRequirement) -> ResultRequirement:
    requirement = confirmed.requirement
    window, party, preferences = requirement.time_window, requirement.party, requirement.preferences

    def endpoint(value: Any) -> ResultEndpoint:
        _require(value.hub_type is not None, Code.INPUT_NOT_READY)
        return ResultEndpoint(hub_name=value.hub_name, hub_type=value.hub_type.value,
                              local_datetime=value.local_datetime, is_approximate=value.is_approximate)

    try:
        return ResultRequirement(
            destination=requirement.destination, arrival=endpoint(window.arrival), departure=endpoint(window.departure),
            sightseeing_start_date=date.fromisoformat(window.sightseeing_start_date),
            sightseeing_end_date=date.fromisoformat(window.sightseeing_end_date), sightseeing_days=window.sightseeing_days,
            hotel_check_in_date=date.fromisoformat(window.hotel_check_in_date),
            hotel_check_out_date=date.fromisoformat(window.hotel_check_out_date), hotel_nights=window.hotel_nights,
            adults_count=party.adults_count, children_count=party.children_count, children_ages=tuple(party.children_ages),
            seniors_count=party.seniors_count, total_people=party.total_people, room_count=requirement.rooms.room_count,
            budget_total_cny=requirement.budget.total_cny, budget_is_approximate=requirement.budget.is_approximate,
            budget_scope=requirement.budget.scope, mobility_constraints=requirement.constraints.mobility_constraints,
            dietary_restrictions=requirement.constraints.dietary_restrictions,
            room_type_requirements=requirement.rooms.room_type_requirements, hotel_preferences=preferences.hotel_preferences,
            transport_preferences=preferences.transport_preferences, daily_time_preferences=preferences.daily_time_preferences,
            free_text_notes=preferences.free_text_notes, must_visit_places=tuple(preferences.must_visit_places),
            avoid_places=tuple(preferences.avoid_places),
        )
    except (ValidationError, TypeError, ValueError):
        raise ProjectionFailure(Code.INPUT_NOT_READY) from None


def _coordinate(value: Any) -> ResultCoordinate:
    return ResultCoordinate(coordinate_system="bd09ll", latitude=value.latitude, longitude=value.longitude)


def _identity(value: Any, *, hotel: bool = False) -> tuple[Any, ...]:
    return (value.uid, value.name, value.address, _coordinate(value.coordinate),
            value.category if hotel else value.provider_category)


def _interval(value: Any) -> ResultInterval:
    return ResultInterval(start_at_utc=value.start_at_utc, end_at_utc=value.end_at_utc, timezone="Asia/Shanghai")


_READ_TYPES = {
    HotelSelectionArtifact: ("hotel.selection", "hotel_selection", "1.0"),
    HotelDiscoveryArtifact: ("hotel.candidate_set", "hotel_selection", "1.0"),
    HotelPlaceSearchArtifact: ("hotel.place_search", "hotel_selection", "1.0"),
    CandidateResearchArtifact: ("candidate_research.candidate_set", "candidate_research", "1.0"),
    CandidateResearchPlaceObservationArtifact: ("candidate_research.place_observation", "candidate_research", "1.0"),
    ItineraryPlanningModelActionArtifact: ("itinerary_planning.model_action", "itinerary_planning", "4.0"),
    ItineraryPlanningHubGeocodeObservationArtifact: ("itinerary_planning.hub_geocode_observation", "itinerary_planning", "1.0"),
    ItineraryPlanningDirectionsObservationArtifact: ("itinerary_planning.directions_observation", "itinerary_planning", "1.0"),
    ItineraryPlanningRouteMatrixObservationArtifact: ("itinerary_planning.route_matrix_observation", "itinerary_planning", "1.0"),
}


def projection_steps(request: ResultFormattingRequest, itinerary: ItineraryPlanningArtifact) -> Generator:
    """Yield (Ref, exact public model) and receive a public ArtifactReadResult."""
    require_ready(request)
    _require(itinerary.record.record_type == "completed", Code.ITINERARY_NOT_COMPLETED)
    requirement = project_requirement(request.state.requirement_outcome)
    draft, directories = itinerary.record.draft, itinerary.directories
    digest = confirmed_digest(request.state.requirement_outcome)
    _require(draft.requirement_digest == digest)
    expected_dates = tuple(requirement.sightseeing_start_date + timedelta(days=i) for i in range(requirement.sightseeing_days))
    _require(tuple(day.date for day in draft.days) == expected_dates)
    _require(expected_dates[-1] == requirement.sightseeing_end_date)
    _require(draft.hotel_selection_ref in directories.hotel_selection_refs)
    _require(draft.candidate_set_ref in directories.candidate_set_refs)
    read_cache: dict[UUID, ArtifactReadResult] = {}
    sources: dict[UUID, dict[str, Any]] = {}

    def read(ref: ArtifactRef, model: type[BaseModel]) -> Generator:
        artifact_type, phase, version = _READ_TYPES[model]
        _require(ref.operation_id.run_id == request.state.run_id)
        _require(ref.artifact_type == artifact_type and ref.schema_version == version, Code.ARTIFACT_CONTRACT_ERROR)
        _require(ref.operation_id.phase.value == phase)
        if ref.artifact_id in read_cache:
            saved = read_cache[ref.artifact_id]
            _require(saved.ref == ref)
            _require(type(saved.content) is model, Code.ARTIFACT_CONTRACT_ERROR)
            return saved.content
        saved = yield ref, model
        _require(isinstance(saved, ArtifactReadResult), Code.ARTIFACT_INTEGRITY_ERROR)
        _require(saved.ref == ref)
        try:
            content = fresh_model(saved.content, model)
        except ResultFormattingUsageError:
            raise ProjectionFailure(Code.ARTIFACT_INTEGRITY_ERROR) from None
        if isinstance(content, (CandidateResearchArtifact, CandidateResearchPlaceObservationArtifact)):
            _require(content.run_id == request.state.run_id)
        read_cache[ref.artifact_id] = ArtifactReadResult(ref=ref, content=content)
        return content

    def add_source(ref: ArtifactRef, artifact: Any) -> UUID:
        if type(artifact) is HotelPlaceSearchArtifact:
            source, capability, timestamp, verified = artifact.source, "place_search", artifact.retrieved_at, artifact.verified
        elif type(artifact) is CandidateResearchPlaceObservationArtifact:
            provenance = artifact.result.provenance
            source, capability, timestamp, verified = provenance.source, provenance.capability.value, provenance.retrieved_at, provenance.verified
        else:
            provenance = artifact.provenance
            _require(provenance is not None)
            source, capability, timestamp, verified = provenance.source, provenance.capability.value, provenance.retrieved_at_utc, provenance.verified
        _require(source == "baidu_maps_mcp" and verified is True)
        data = dict(artifact_ref=ref, source=source, capability=capability, retrieved_at_utc=timestamp, verified=True)
        _require(ref.artifact_id not in sources or sources[ref.artifact_id] == data)
        sources[ref.artifact_id] = data
        _require(len(sources) <= 1024, Code.RESULT_TOO_LARGE)
        return ref.artifact_id

    def planning_observation(ref: ArtifactRef, model: type[BaseModel]) -> Generator:
        observation = yield from read(ref, model)
        _require(observation.observation_status == "success")
        _require(observation.model_action_ref in directories.model_action_refs)
        action = yield from read(observation.model_action_ref, ItineraryPlanningModelActionArtifact)
        _require(action.request_digest == observation.request_digest)
        return observation

    hotel = yield from read(draft.hotel_selection_ref, HotelSelectionArtifact)
    _require(hotel.decision.decision_type == "selected")
    discovery = yield from read(hotel.candidate_set_ref, HotelDiscoveryArtifact)
    candidates = yield from read(draft.candidate_set_ref, CandidateResearchArtifact)
    _require(candidates.run_id == request.state.run_id)
    _require(candidates.record.record_type in {"completed", "incomplete"})
    candidate_state = candidates.record.state
    _require(candidate_state.confirmed_requirement_digest == digest and candidate_state.hotel_selection_ref == draft.hotel_selection_ref)
    selected = hotel.decision.selected_candidate
    _require(selected in discovery.result.candidates)
    _require(discovery.confirmation_summary_digest == request.state.requirement_outcome.confirmation_summary_digest)
    hotel_source = None
    for ref in discovery.search_refs:
        search = yield from read(ref, HotelPlaceSearchArtifact)
        if search.evidence.outcome_type == "success" and any(_identity(item, hotel=True) == _identity(selected, hotel=True) for item in search.evidence.places):
            hotel_source = add_source(ref, search)
            break
    _require(hotel_source is not None)
    place_specs = [dict(place_id=1, kind="hotel", uid=selected.uid, name=selected.name, address=selected.address,
                        coordinate=_coordinate(selected.coordinate), identity_source_id=hotel_source,
                        business_hours=(), business_hours_source_id=None)]
    for transfer, endpoint in ((draft.arrival_transfer, requirement.arrival), (draft.departure_transfer, requirement.departure)):
        ref = directories.hub_geocode_refs[transfer.hub.geocode_observation_index]
        observation = yield from planning_observation(ref, ItineraryPlanningHubGeocodeObservationArtifact)
        _require(observation.endpoint_kind == transfer.transfer_kind == transfer.hub.endpoint_kind)
        place_specs.append(dict(place_id=len(place_specs) + 1, kind=transfer.transfer_kind + "_hub", uid=None,
                                name=endpoint.hub_name, address=observation.success_candidate.formatted_address,
                                coordinate=_coordinate(observation.success_candidate.coordinate),
                                identity_source_id=add_source(ref, observation), business_hours=(), business_hours_source_id=None))

    used: set[tuple[int, str]] = set()
    for day in draft.days:
        used.update((visit.location.admission_ordinal, visit.location.uid) for visit in day.visits)
        used.update((backup.candidate.admission_ordinal, backup.candidate.uid) for backup in day.backups)
    candidate_ids: dict[tuple[int, str], int] = {}
    for ordinal, uid in sorted(used):
        matches = [item for item in candidate_state.candidates if item.admission_ordinal == ordinal and item.core.uid == uid]
        _require(len(matches) == 1 and matches[0].state.value == "eligible")
        candidate = matches[0]
        identity_source = None
        for index in sorted(candidate.observation_indexes):
            ref = candidate_state.observation_refs[index]
            observation = yield from read(ref, CandidateResearchPlaceObservationArtifact)
            result = observation.result
            options = result.places if result.record_type == "search_success" else (result.details,) if result.record_type == "details_success" else ()
            if any(_identity(item) == _identity(candidate.core) for item in options):
                identity_source = add_source(ref, observation)
                break
        _require(identity_source is not None)
        hours, hours_source = (), None
        if candidate.details.status == "success":
            ref = candidate_state.observation_refs[candidate.details.observation_index]
            observation = yield from read(ref, CandidateResearchPlaceObservationArtifact)
            _require(observation.result.record_type == "details_success" and observation.result.details.uid == uid)
            hours = observation.result.details.business_hours
            hours_source = add_source(ref, observation)
        place_id = len(place_specs) + 1
        candidate_ids[(ordinal, uid)] = place_id
        place_specs.append(dict(place_id=place_id, kind="candidate", uid=uid, name=candidate.core.name,
                                address=candidate.core.address, coordinate=_coordinate(candidate.core.coordinate),
                                identity_source_id=identity_source, business_hours=hours, business_hours_source_id=hours_source))

    def location_id(location: Any) -> int:
        if location.ref_type == "hotel":
            return 1
        if location.ref_type == "hub":
            transfer = draft.arrival_transfer if location.endpoint_kind == "arrival" else draft.departure_transfer
            _require(location == transfer.hub)
            return 2 if location.endpoint_kind == "arrival" else 3
        key = (location.admission_ordinal, location.uid)
        _require(key in candidate_ids)
        return candidate_ids[key]

    def business_sources(value: Any, uid: str) -> Generator:
        found = []
        for index in value.business_evidence_indexes:
            _require(0 <= index < len(directories.candidate_evidence_refs))
            ref = directories.candidate_evidence_refs[index]
            observation = yield from read(ref, CandidateResearchPlaceObservationArtifact)
            result = observation.result
            options = result.places if result.record_type == "search_success" else (result.details,) if result.record_type == "details_success" else ()
            _require(any(item.uid == uid for item in options))
            found.append(add_source(ref, observation))
        return tuple(found)

    def route_spec(route: Any) -> Generator:
        ref = directories.directions_refs[route.directions_observation_index]
        observation = yield from planning_observation(ref, ItineraryPlanningDirectionsObservationArtifact)
        _require(observation.origin == route.origin and observation.destination == route.destination)
        _require(observation.transport_semantic == route.transport_semantic and observation.route_mode == route.route_mode)
        alternatives = [item for item in observation.alternatives if item.alternative_index == route.alternative_index]
        _require(len(alternatives) == 1 and alternatives[0].distance_m == route.distance_m and alternatives[0].duration_s == route.duration_s)
        return dict(leg_id=route.leg_id, origin_place_id=location_id(route.origin), destination_place_id=location_id(route.destination),
                    interval=_interval(route.interval), transport_semantic=route.transport_semantic.value, route_mode=route.route_mode.value,
                    distance_m=route.distance_m, duration_s=route.duration_s, scheduled_minutes=route.scheduled_minutes,
                    source_id=add_source(ref, observation), alternative_index=route.alternative_index)

    arrival_route = yield from route_spec(draft.arrival_transfer.route_leg)
    day_specs = []
    for day_no, day in enumerate(draft.days, 1):
        visits, routes, backups = [], [], []
        for visit in day.visits:
            _require(visit.duration_source == "planning_estimate")
            evidence_ids = yield from business_sources(visit, visit.location.uid)
            visits.append(dict(visit_id=visit.visit_id, place_id=location_id(visit.location), visit_kind=visit.visit_kind.value,
                               interval=_interval(visit.interval), duration_source=visit.duration_source,
                               business_hours_state=visit.business_hours_state.value, business_source_ids=evidence_ids,
                               requires_verification=visit.requires_verification))
        for route in day.route_legs:
            routes.append((yield from route_spec(route)))
        for backup in day.backups:
            matches = [visit for visit in day.visits if visit.visit_id == backup.core_visit_id]
            _require(len(matches) == 1 and backup.backup_kind in matches[0].visit_kind.value.split("_"))
            ref = directories.matrix_refs[backup.matrix_observation_index]
            matrix = yield from planning_observation(ref, ItineraryPlanningRouteMatrixObservationArtifact)
            _require(matrix.date == day.date and matrix.mode == backup.matrix_mode)
            _require(matches[0].location in matrix.ordered_locations and backup.candidate in matrix.ordered_locations)
            origin_index, destination_index = matrix.ordered_locations.index(matches[0].location), matrix.ordered_locations.index(backup.candidate)
            cells = [cell for cell in matrix.cells if cell.origin_index == origin_index and cell.destination_index == destination_index]
            _require(len(cells) == 1 and cells[0].status == "success" and cells[0].distance_m == backup.distance_m <= 3000)
            evidence_ids = yield from business_sources(backup, backup.candidate.uid)
            backups.append(dict(backup_kind=backup.backup_kind, core_visit_id=backup.core_visit_id, place_id=location_id(backup.candidate),
                                matrix_mode=backup.matrix_mode.value, distance_m=backup.distance_m, source_id=add_source(ref, matrix),
                                business_hours_state=backup.business_hours_state.value, business_source_ids=evidence_ids,
                                requires_verification=backup.requires_verification))
        day_specs.append(dict(day_no=day_no, date=day.date, day_kind=day.day_kind.value, theme=day.theme,
                              open_intervals=tuple(_interval(item) for item in day.open_intervals), visits=visits, route_legs=routes, backups=backups))
    departure_route = yield from route_spec(draft.departure_transfer.route_leg)

    ordered_sources = sorted(sources.values(), key=lambda item: (item["artifact_ref"].artifact_type, item["artifact_ref"].schema_version,
        item["artifact_ref"].operation_id.phase.value, item["artifact_ref"].operation_id.logical_step, item["artifact_ref"].artifact_id.hex))
    source_ids = {item["artifact_ref"].artifact_id: index for index, item in enumerate(ordered_sources, 1)}
    source_dtos = tuple(ResultSource(source_id=index, **item) for index, item in enumerate(ordered_sources, 1))
    places = tuple(ResultPlace(**{**item, "identity_source_id": source_ids[item["identity_source_id"]],
                                "business_hours_source_id": source_ids[item["business_hours_source_id"]] if item["business_hours_source_id"] else None}) for item in place_specs)

    def business_ids(spec: dict[str, Any]) -> dict[str, Any]:
        return {**spec, "business_source_ids": tuple(source_ids[key] for key in spec["business_source_ids"])}

    def route_dto(spec: dict[str, Any]) -> ResultRoute:
        return ResultRoute(**{**spec, "source_id": source_ids[spec["source_id"]]})

    days = tuple(ResultDayData(**{**spec,
        "visits": tuple(ResultVisit(**business_ids(item)) for item in spec["visits"]),
        "route_legs": tuple(route_dto(item) for item in spec["route_legs"]),
        "backups": tuple(ResultBackup(**{**business_ids(item), "source_id": source_ids[item["source_id"]]}) for item in spec["backups"]),
    }) for spec in day_specs)

    def transfer_dto(transfer: Any, route: dict[str, Any]) -> ResultTransfer:
        return ResultTransfer(transfer_kind=transfer.transfer_kind, hub_place_id=location_id(transfer.hub), hotel_place_id=1,
                              route=route_dto(route), processing_minutes=transfer.processing_minutes, approximate_extra_minutes=transfer.approximate_extra_minutes)

    core = ResultCore(requirement=requirement, effective_pace=draft.effective_policy.effective_pace.value, places=places, sources=source_dtos,
                      arrival_transfer=transfer_dto(draft.arrival_transfer, arrival_route), departure_transfer=transfer_dto(draft.departure_transfer, departure_route),
                      unknown_business_visit_count=sum(visit.business_hours_state == "unknown" for day in days for visit in day.visits),
                      unknown_business_backup_count=sum(backup.business_hours_state == "unknown" for day in days for backup in day.backups),
                      weather_status="not_collected", map_status="deferred", cost_status="unverified", scope_notice_code="m09_first_release")
    return core, days


def project_result(request: ResultFormattingRequest, itinerary: ItineraryPlanningArtifact,
                   evidence: Mapping[UUID, ArtifactReadResult]) -> tuple[ResultCore, tuple[ResultDayData, ...]]:
    """Pure adapter: consume already read typed artifacts without Repository I/O."""
    steps = projection_steps(request, itinerary)
    try:
        ref, model = next(steps)
        while True:
            _require(ref.artifact_id in evidence, Code.ARTIFACT_NOT_FOUND)
            ref, model = steps.send(evidence[ref.artifact_id])
    except StopIteration as finished:
        return finished.value
