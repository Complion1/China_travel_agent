"""Module 09 的 typed 地图执行、预算屏障、缓存和有序并发基础。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import date
from typing import Iterable, Sequence

from app.baidu_map import (
    BaiduCoordinate,
    BaiduMapError,
    BaiduMapErrorCode,
    BaiduMapAdapter,
    DirectionsNoResult,
    DirectionsSuccess,
    DirectionsRequest,
    GeocodeAmbiguous,
    GeocodeNoResult,
    GeocodeRequest,
    GeocodeSuccess,
    MapCapability,
    MatrixTravelMode,
    RouteEndpoint,
    RouteMatrixNoResult,
    RouteMatrixRequest,
    RouteMatrixSuccess,
    TravelMode,
)
from app.workflow_contracts import (
    ArtifactRef,
    BudgetConsumed,
    BudgetConsumeRequest,
    ExecutionBudgetKey,
    OperationId,
    WorkflowPhase,
)

from .fingerprints import (
    fingerprint_directions,
    fingerprint_directions_handle,
    fingerprint_geocode,
    fingerprint_matrix,
)
from .models import (
    ContentDigest,
    HubLocationRef,
    PlanningLocationRef,
    RouteAlternativeSummary,
    TransportSemantic,
)
from .policy import route_mode_for_semantic


# This is the Module 09 business boundary.  It is deliberately shared by all
# executors so separate runs cannot each acquire their own three map slots.
# The small wrapper also avoids reusing a loop-bound asyncio.Semaphore after a
# test/application loop has been closed, while retaining one shared gate per
# live loop.
class _SharedMapConcurrency:
    __slots__ = ("_active", "_loop", "_semaphore")

    def __init__(self) -> None:
        self._active = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        if self._active == 0 and self._loop is not loop:
            self._loop = loop
            self._semaphore = asyncio.Semaphore(3)
        if self._semaphore is None:
            raise RuntimeError("shared map concurrency is not initialized")
        await self._semaphore.acquire()
        self._active += 1

    def release(self) -> None:
        if self._active <= 0 or self._semaphore is None:
            raise RuntimeError("shared map concurrency release is unbalanced")
        self._active -= 1
        self._semaphore.release()


_MODULE09_MAP_CONCURRENCY = _SharedMapConcurrency()


@dataclass(frozen=True, slots=True)
class LocationBinding:
    handle: str
    location: PlanningLocationRef
    coordinate: BaiduCoordinate
    label: str | None = None

    def __post_init__(self) -> None:
        if type(self.handle) is not str or not self.handle.strip() or len(self.handle) > 128:
            raise ValueError("invalid location handle")
        if not isinstance(self.coordinate, BaiduCoordinate):
            raise TypeError("location coordinate must be a public BaiduCoordinate")


@dataclass(frozen=True, slots=True)
class HubEndpointQuery:
    endpoint_kind: str
    address: str
    destination: str
    text_digest: ContentDigest

    def __post_init__(self) -> None:
        if self.endpoint_kind not in {"arrival", "departure"}:
            raise ValueError("invalid hub endpoint")
        if type(self.address) is not str or not self.address.strip() or len(self.address) > 512:
            raise ValueError("invalid hub address")
        if type(self.destination) is not str or not self.destination.strip() or len(self.destination) > 64:
            raise ValueError("invalid destination")


@dataclass(frozen=True, slots=True)
class DirectionsLegSpec:
    leg_id: str
    origin: LocationBinding
    destination: LocationBinding
    transport_semantic: TransportSemantic
    route_mode: TravelMode
    alternatives_limit: int
    operation_id: OperationId

    def __post_init__(self) -> None:
        if type(self.leg_id) is not str or not self.leg_id.strip() or len(self.leg_id) > 128:
            raise ValueError("invalid direction leg id")
        if not 1 <= self.alternatives_limit <= 3:
            raise ValueError("alternatives limit must be between one and three")
        if self.route_mode is not route_mode_for_semantic(self.transport_semantic):
            raise ValueError("transport semantic and route mode mismatch")
        if self.operation_id.phase is not WorkflowPhase.ITINERARY_PLANNING:
            raise ValueError("directions operation must use itinerary planning phase")


@dataclass(frozen=True, slots=True)
class GeocodeObservation:
    endpoint_kind: str
    request_fingerprint: ContentDigest
    status: str
    result: object | None
    candidates: tuple[object, ...]
    provenance: object | None
    error_code: BaiduMapErrorCode | None
    operation_id: OperationId | None
    action_ordinal: int
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class MatrixObservation:
    matrix_date: date
    mode: MatrixTravelMode
    request_fingerprint: ContentDigest
    ordered_locations: tuple[LocationBinding, ...]
    status: str
    cells: tuple[object, ...]
    provenance: object | None
    error_code: BaiduMapErrorCode | None
    operation_id: OperationId | None
    action_ordinal: int
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class DirectionsObservation:
    leg_id: str
    leg_ordinal: int
    leg_fingerprint: ContentDigest
    origin: LocationBinding
    destination: LocationBinding
    transport_semantic: TransportSemantic
    route_mode: TravelMode
    alternatives_limit: int
    status: str
    alternatives: tuple[RouteAlternativeSummary, ...]
    provenance: object | None
    error_code: BaiduMapErrorCode | None
    operation_id: OperationId
    directions_handle: ContentDigest
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class DirectionsBatchResult:
    status: str
    observations: tuple[DirectionsObservation, ...]
    cache_hits: int
    reservations_applied: int
    peak_concurrency: int


@dataclass(frozen=True, slots=True)
class _DirectionsAdmission:
    """只读的整批路线预算准入计划。"""

    ordered: tuple[DirectionsLegSpec, ...]
    cached: tuple[tuple[int, DirectionsObservation], ...]
    misses: tuple[tuple[int, DirectionsLegSpec, ContentDigest], ...]
    status: str

    @property
    def cache_hits(self) -> int:
        return len(self.cached)

    def cached_observations(self) -> tuple[DirectionsObservation, ...]:
        return tuple(item for _, item in self.cached)


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    context_digest: ContentDigest
    value: object


class MapExecutionCache:
    """Typed process-local cache; technical failures are never inserted."""

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[tuple[str, ContentDigest], _CacheEntry] = {}

    def get(self, kind: str, fingerprint: ContentDigest, *, context_digest: ContentDigest) -> object | None:
        entry = self._entries.get((kind, fingerprint))
        if entry is None or entry.context_digest != context_digest:
            return None
        return entry.value

    def put(self, kind: str, fingerprint: ContentDigest, value: object, *, context_digest: ContentDigest) -> None:
        if getattr(value, "status", None) == "failure":
            return
        self._entries[(kind, fingerprint)] = _CacheEntry(context_digest, value)

    def invalidate_context(self, context_digest: ContentDigest) -> None:
        self._entries = {
            key: value for key, value in self._entries.items() if value.context_digest != context_digest
        }

    def invalidate_all(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


class MapExecutionError(RuntimeError):
    """Internal safe execution error; provider exception text is not retained."""


class MapExecutor:
    """Use only the Module 06 public adapter and workflow budget protocols."""

    __slots__ = (
        "_adapter", "_budget", "_cache", "_context_digest", "_hub_queries",
        "_matrix_seen", "_concurrency", "_peak_concurrency",
    )

    def __init__(
        self,
        *,
        adapter: BaiduMapAdapter,
        budget_manager: object,
        context_digest: ContentDigest,
        hub_queries: Iterable[HubEndpointQuery] = (),
        cache: MapExecutionCache | None = None,
    ) -> None:
        self._adapter = adapter
        self._budget = budget_manager
        self._cache = cache or MapExecutionCache()
        self._context_digest = context_digest
        self._hub_queries = {item.endpoint_kind: item for item in hub_queries}
        self._matrix_seen: set[tuple[int, date]] = set()
        self._concurrency = 0
        self._peak_concurrency = 0

    @property
    def cache(self) -> MapExecutionCache:
        return self._cache

    @property
    def peak_concurrency(self) -> int:
        return self._peak_concurrency

    def invalidate_after_user_correction(self) -> None:
        self._cache.invalidate_context(self._context_digest)
        self._matrix_seen.clear()

    def set_context_after_user_correction(self, context_digest: ContentDigest) -> None:
        if type(context_digest) is not str or not context_digest.startswith("sha256:"):
            raise ValueError("planning context digest is invalid")
        self.invalidate_after_user_correction()
        self._context_digest = context_digest

    def require_hub_query(self, endpoint_kind: str) -> HubEndpointQuery:
        selected = self._hub_queries.get(endpoint_kind)
        if selected is None or selected.endpoint_kind != endpoint_kind:
            raise ValueError("hub query must come from confirmed typed endpoint")
        return selected

    async def _begin_call(self) -> None:
        await _MODULE09_MAP_CONCURRENCY.acquire()
        self._concurrency += 1
        self._peak_concurrency = max(self._peak_concurrency, self._concurrency)

    def _end_call(self) -> None:
        self._concurrency -= 1
        _MODULE09_MAP_CONCURRENCY.release()

    async def geocode_hub(
        self,
        *,
        endpoint_kind: str,
        operation_id: OperationId | None = None,
        action_ordinal: int = 0,
        query: HubEndpointQuery | None = None,
    ) -> GeocodeObservation:
        selected = query or self.require_hub_query(endpoint_kind)
        if selected.endpoint_kind != endpoint_kind:
            raise ValueError("hub query must come from confirmed typed endpoint")
        fingerprint = fingerprint_geocode(
            self._context_digest,
            selected.destination,
            endpoint_kind,
            selected.text_digest,
        )
        cached = self._cache.get("geocode", fingerprint, context_digest=self._context_digest)
        if isinstance(cached, GeocodeObservation):
            return replace(cached, cache_hit=True, operation_id=operation_id, action_ordinal=action_ordinal)
        request = GeocodeRequest(address=selected.address, city=selected.destination)
        await self._begin_call()
        try:
            outcome = await self._adapter.geocode(request)
        except BaiduMapError as error:
            return GeocodeObservation(endpoint_kind, fingerprint, "failure", None, (), None, error.code, operation_id, action_ordinal)
        finally:
            self._end_call()
        if isinstance(outcome, GeocodeSuccess):
            observation = GeocodeObservation(endpoint_kind, fingerprint, "success", outcome.result, (), outcome.provenance, None, operation_id, action_ordinal)
        elif isinstance(outcome, GeocodeAmbiguous):
            observation = GeocodeObservation(endpoint_kind, fingerprint, "ambiguous", None, outcome.candidates, outcome.provenance, None, operation_id, action_ordinal)
        elif isinstance(outcome, GeocodeNoResult):
            observation = GeocodeObservation(endpoint_kind, fingerprint, "no_result", None, (), outcome.provenance, None, operation_id, action_ordinal)
        else:
            raise MapExecutionError()
        self._cache.put("geocode", fingerprint, observation, context_digest=self._context_digest)
        return observation

    async def calculate_route_matrix(
        self,
        *,
        matrix_date: date,
        mode: MatrixTravelMode,
        locations: Sequence[LocationBinding],
        iteration_index: int = 0,
        operation_id: OperationId | None = None,
        action_ordinal: int = 0,
    ) -> MatrixObservation:
        ordered = tuple(locations)
        if not 1 <= len(ordered) <= 10:
            raise ValueError("matrix requires one through ten locations")
        if mode not in (MatrixTravelMode.DRIVING, MatrixTravelMode.RIDING, MatrixTravelMode.WALKING):
            raise ValueError("transit is not a matrix mode")
        if len({item.handle for item in ordered}) != len(ordered):
            raise ValueError("matrix locations must be unique")
        marker = (iteration_index, matrix_date)
        if marker in self._matrix_seen:
            raise ValueError("one matrix request per date per iteration")
        self._matrix_seen.add(marker)
        fingerprint = fingerprint_matrix(
            self._context_digest,
            matrix_date,
            mode,
            tuple((item.location, item.coordinate) for item in ordered),
        )
        cached = self._cache.get("matrix", fingerprint, context_digest=self._context_digest)
        if isinstance(cached, MatrixObservation):
            return replace(cached, cache_hit=True, operation_id=operation_id, action_ordinal=action_ordinal)
        request = RouteMatrixRequest(
            mode=mode,
            origins=tuple(item.coordinate for item in ordered),
            destinations=tuple(item.coordinate for item in ordered),
        )
        await self._begin_call()
        try:
            outcome = await self._adapter.calculate_route_matrix(request)
        except BaiduMapError as error:
            return MatrixObservation(matrix_date, mode, fingerprint, ordered, "failure", (), None, error.code, operation_id, action_ordinal)
        finally:
            self._end_call()
        if isinstance(outcome, RouteMatrixSuccess):
            observation = MatrixObservation(matrix_date, mode, fingerprint, ordered, "success", outcome.cells, outcome.provenance, None, operation_id, action_ordinal)
        elif isinstance(outcome, RouteMatrixNoResult):
            observation = MatrixObservation(matrix_date, mode, fingerprint, ordered, "no_result", (), outcome.provenance, None, operation_id, action_ordinal)
        else:
            raise MapExecutionError()
        self._cache.put("matrix", fingerprint, observation, context_digest=self._context_digest)
        return observation

    async def calculate_route_matrices(
        self,
        requests: Sequence[dict[str, object]],
        *,
        iteration_index: int = 0,
    ) -> tuple[MatrixObservation, ...]:
        dates = [request.get("matrix_date") for request in requests]
        if len(dates) != len(set(dates)):
            raise ValueError("matrix dates must be unique within a batch")
        ordered_results: list[MatrixObservation] = []
        for start in range(0, len(requests), 3):
            wave = requests[start:start + 3]
            tasks = [asyncio.create_task(self.calculate_route_matrix(iteration_index=iteration_index, **request)) for request in wave]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for result in results:
                if isinstance(result, BaseException):
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise MapExecutionError() from None
                ordered_results.append(result)
        return tuple(ordered_results)

    async def _run_direction(self, spec: DirectionsLegSpec) -> DirectionsObservation:
        fingerprint = fingerprint_directions(
            self._context_digest,
            spec.origin.location,
            spec.origin.coordinate,
            spec.destination.location,
            spec.destination.coordinate,
            spec.route_mode,
            spec.alternatives_limit,
        )
        request = DirectionsRequest(
            mode=spec.route_mode,
            origin=RouteEndpoint(coordinate=spec.origin.coordinate, label=spec.origin.label),
            destination=RouteEndpoint(coordinate=spec.destination.coordinate, label=spec.destination.label),
            alternatives_limit=spec.alternatives_limit,
        )
        await self._begin_call()
        try:
            outcome = await self._adapter.plan_directions(request)
        except BaiduMapError as error:
            return DirectionsObservation(spec.leg_id, 0, fingerprint, spec.origin, spec.destination, spec.transport_semantic, spec.route_mode, spec.alternatives_limit, "failure", (), None, error.code, spec.operation_id, fingerprint_directions_handle(spec.operation_id, 0, fingerprint))
        finally:
            self._end_call()
        if isinstance(outcome, DirectionsSuccess):
            alternatives = tuple(
                RouteAlternativeSummary(
                    alternative_index=index,
                    distance_m=item.distance_m,
                    duration_s=item.duration_s,
                    step_count=len(item.steps),
                )
                for index, item in enumerate(outcome.alternatives)
            )
            status = "success"
            provenance = outcome.provenance
        elif isinstance(outcome, DirectionsNoResult):
            alternatives = ()
            status = "no_result"
            provenance = outcome.provenance
        else:
            raise MapExecutionError()
        return DirectionsObservation(spec.leg_id, 0, fingerprint, spec.origin, spec.destination, spec.transport_semantic, spec.route_mode, spec.alternatives_limit, status, alternatives, provenance, None, spec.operation_id, fingerprint_directions_handle(spec.operation_id, 0, fingerprint))

    def _validate_direction_batch(
        self,
        ordered: tuple[DirectionsLegSpec, ...],
        *,
        direction_budget_key: ExecutionBudgetKey,
        max_concurrency: int,
    ) -> None:
        if not ordered or len(ordered) > 482:
            raise ValueError("directions batch capacity exceeded")
        if max_concurrency != 3:
            raise ValueError("directions concurrency is fixed at three")
        if len({spec.leg_id for spec in ordered}) != len(ordered):
            raise ValueError("direction leg ids must be unique")
        if len({spec.operation_id for spec in ordered}) != len(ordered):
            raise ValueError("direction operation ids must be unique")
        if direction_budget_key.name != "planning.direction_call" or direction_budget_key.phase is not WorkflowPhase.ITINERARY_PLANNING:
            raise ValueError("directions require the planning direction budget")
        run_id = ordered[0].operation_id.run_id
        if any(spec.operation_id.run_id != run_id for spec in ordered):
            raise ValueError("direction operation ids must share run")

    async def _admit_directions(
        self,
        specs: Sequence[DirectionsLegSpec],
        *,
        direction_budget_key: ExecutionBudgetKey,
        max_concurrency: int = 3,
    ) -> _DirectionsAdmission:
        """在任何 evidence、矩阵、consume 或地图调用前完成整批只读准入。"""
        ordered = tuple(specs)
        self._validate_direction_batch(
            ordered,
            direction_budget_key=direction_budget_key,
            max_concurrency=max_concurrency,
        )
        misses: list[tuple[int, DirectionsLegSpec, ContentDigest]] = []
        cached: list[tuple[int, DirectionsObservation]] = []
        for ordinal, spec in enumerate(ordered):
            fingerprint = fingerprint_directions(
                self._context_digest,
                spec.origin.location,
                spec.origin.coordinate,
                spec.destination.location,
                spec.destination.coordinate,
                spec.route_mode,
                spec.alternatives_limit,
            )
            existing = self._cache.get("directions", fingerprint, context_digest=self._context_digest)
            if isinstance(existing, DirectionsObservation):
                cached.append((ordinal, DirectionsObservation(
                    spec.leg_id, ordinal, existing.leg_fingerprint, spec.origin, spec.destination,
                    spec.transport_semantic, spec.route_mode, spec.alternatives_limit,
                    existing.status, existing.alternatives, existing.provenance, existing.error_code,
                    spec.operation_id, fingerprint_directions_handle(spec.operation_id, ordinal, fingerprint), True,
                )))
            else:
                misses.append((ordinal, spec, fingerprint))

        snapshot = await self._budget.get_snapshot(key=direction_budget_key)
        status = "not_admitted_budget_exhausted" if len(misses) > snapshot.remaining else "admitted"
        return _DirectionsAdmission(tuple(ordered), tuple(cached), tuple(misses), status)

    async def plan_directions(
        self,
        specs: Sequence[DirectionsLegSpec],
        *,
        direction_budget_key: ExecutionBudgetKey,
        max_concurrency: int = 3,
    ) -> DirectionsBatchResult:
        ordered = tuple(specs)
        admission = await self._admit_directions(
            ordered,
            direction_budget_key=direction_budget_key,
            max_concurrency=max_concurrency,
        )
        return await self._execute_admitted_directions(
            ordered,
            direction_budget_key=direction_budget_key,
            max_concurrency=max_concurrency,
            admission=admission,
        )

    async def _execute_admitted_directions(
        self,
        ordered: tuple[DirectionsLegSpec, ...],
        *,
        direction_budget_key: ExecutionBudgetKey,
        max_concurrency: int,
        admission: _DirectionsAdmission,
    ) -> DirectionsBatchResult:
        self._validate_direction_batch(
            ordered,
            direction_budget_key=direction_budget_key,
            max_concurrency=max_concurrency,
        )
        if type(admission) is not _DirectionsAdmission or admission.ordered != ordered:
            raise ValueError("directions admission does not match batch")

        if admission.status == "not_admitted_budget_exhausted":
            return DirectionsBatchResult(
                admission.status,
                admission.cached_observations(),
                admission.cache_hits,
                0,
                self._peak_concurrency,
            )

        misses = list(admission.misses)
        observations: dict[int, DirectionsObservation] = dict(admission.cached)

        reserved = 0
        for _, spec, _ in misses:
            try:
                result = await self._budget.consume(
                    BudgetConsumeRequest(operation_id=spec.operation_id, key=direction_budget_key, amount=1)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return DirectionsBatchResult("reservation_barrier_failed", tuple(observations[index] for index in sorted(observations)), len(observations), reserved, self._peak_concurrency)
            if not isinstance(result, BudgetConsumed):
                return DirectionsBatchResult("reservation_barrier_failed", tuple(observations[index] for index in sorted(observations)), len(observations), reserved, self._peak_concurrency)
            reserved += 1

        for start in range(0, len(misses), max_concurrency):
            wave = misses[start:start + max_concurrency]
            tasks = [asyncio.create_task(self._run_direction(spec)) for _, spec, _ in wave]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for (ordinal, spec, fingerprint), result in zip(wave, results, strict=True):
                if isinstance(result, BaseException):
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise MapExecutionError() from None
                observation = result
                observation = DirectionsObservation(
                    spec.leg_id, ordinal, observation.leg_fingerprint, spec.origin, spec.destination,
                    spec.transport_semantic, spec.route_mode, spec.alternatives_limit,
                    observation.status, observation.alternatives, observation.provenance,
                    observation.error_code, spec.operation_id,
                    fingerprint_directions_handle(spec.operation_id, ordinal, fingerprint),
                )
                observations[ordinal] = observation
                self._cache.put("directions", fingerprint, observation, context_digest=self._context_digest)

        return DirectionsBatchResult(
            "completed",
            tuple(observations[index] for index in range(len(ordered))),
            sum(item.cache_hit for item in observations.values()),
            reserved,
            self._peak_concurrency,
        )


def direction_matches(
    observation: DirectionsObservation,
    *,
    origin: LocationBinding,
    destination: LocationBinding,
    route_mode: TravelMode,
    alternative_index: int,
) -> bool:
    if observation.status != "success" or observation.provenance is None:
        return False
    if getattr(observation.provenance, "capability", None) is not MapCapability.DIRECTIONS:
        return False
    if getattr(observation.provenance, "verified", False) is not True:
        return False
    if observation.origin.coordinate != origin.coordinate or observation.destination.coordinate != destination.coordinate:
        return False
    if observation.origin.location != origin.location or observation.destination.location != destination.location:
        return False
    if observation.route_mode is not route_mode:
        return False
    return 0 <= alternative_index < len(observation.alternatives)


def selected_direction_summary(observation: DirectionsObservation, alternative_index: int) -> RouteAlternativeSummary:
    if not direction_matches(
        observation,
        origin=observation.origin,
        destination=observation.destination,
        route_mode=observation.route_mode,
        alternative_index=alternative_index,
    ):
        raise ValueError("directions observation does not exactly match selection")
    return observation.alternatives[alternative_index]


__all__ = ()
