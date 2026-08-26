"""酒店查询构造、类别资格和 UID 身份去重纯函数。"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, TypeAlias, cast

from pydantic import TypeAdapter, ValidationError

from app.baidu_map import CitySearchArea, PlaceSearchRequest
from app.baidu_map import PlaceSearchAreaType, PlaceSummary
from app.requirements import ConfirmedTravelRequirement
from app.workflow_contracts import ContentHash

from .errors import HotelCandidateIdentityConflictError, HotelInputError
from .models import HotelCandidate, HotelCoordinate, _qualify_hotel_category

_CONFIRMED_ADAPTER = TypeAdapter(ConfirmedTravelRequirement)
_PLACE_ADAPTER = TypeAdapter(PlaceSummary)


def _fresh_confirmed(confirmed: ConfirmedTravelRequirement) -> ConfirmedTravelRequirement:
    if type(confirmed) is not ConfirmedTravelRequirement:
        raise HotelInputError()
    try:
        python_tree = confirmed.model_dump(
            mode="python",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        first = _CONFIRMED_ADAPTER.validate_python(python_tree, strict=True)
        json_tree = first.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        payload = json.dumps(
            json_tree,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return _CONFIRMED_ADAPTER.validate_json(payload, strict=True)
    except (AttributeError, TypeError, ValueError, ValidationError, UnicodeError):
        raise HotelInputError() from None


def build_queries(confirmed: ConfirmedTravelRequirement) -> tuple[PlaceSearchRequest, ...]:
    """按冻结顺序构造三条严格 typed Place Search 请求。"""

    fresh = _fresh_confirmed(confirmed)
    try:
        destination = fresh.requirement.destination.strip()
        preference = fresh.requirement.preferences.hotel_preferences
        if preference is not None:
            preference = preference.strip()
        if not destination or preference == "":
            raise HotelInputError()

        raw_queries: list[str] = []
        if preference is not None:
            raw_queries.append(f"{preference} 酒店")
        raw_queries.extend(("酒店", "宾馆", "住宿"))

        requests: list[PlaceSearchRequest] = []
        previous_queries: set[str] = set()
        for raw_query in raw_queries:
            request = PlaceSearchRequest(
                schema_version="1.0",
                query=raw_query,
                tag=None,
                area=CitySearchArea(
                    schema_version="1.0",
                    area_type=PlaceSearchAreaType.CITY,
                    region=destination,
                ),
                result_limit=10,
            )
            if request.query in previous_queries:
                continue
            requests.append(request)
            previous_queries.add(request.query)
        result = tuple(requests[:3])
        if len(result) != 3:
            raise HotelInputError()
        return result
    except HotelInputError:
        raise
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise HotelInputError() from None


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise HotelInputError() from None


def hash_request(request: PlaceSearchRequest) -> ContentHash:
    if type(request) is not PlaceSearchRequest:
        raise HotelInputError()
    try:
        tree = request.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
    except (AttributeError, TypeError, ValueError):
        raise HotelInputError() from None
    digest = "sha256:" + hashlib.sha256(_canonical_json(tree)).hexdigest()
    return cast(ContentHash, digest)


def hash_query_plan(requests: tuple[PlaceSearchRequest, ...]) -> ContentHash:
    if type(requests) is not tuple or len(requests) != 3:
        raise HotelInputError()
    trees: list[object] = []
    for request in requests:
        if type(request) is not PlaceSearchRequest:
            raise HotelInputError()
        try:
            trees.append(
                request.model_dump(
                    mode="json",
                    exclude_none=False,
                    by_alias=False,
                    exclude_computed_fields=True,
                )
            )
        except (AttributeError, TypeError, ValueError):
            raise HotelInputError() from None
    digest = "sha256:" + hashlib.sha256(_canonical_json(trees)).hexdigest()
    return cast(ContentHash, digest)


def qualify_category(category: str | None) -> bool:
    """按精确分隔符和冻结 allowlist 判定住宿类别。"""
    return _qualify_hotel_category(category)


def _fresh_place(place: PlaceSummary) -> PlaceSummary:
    if type(place) is not PlaceSummary:
        raise HotelInputError()
    try:
        python_tree = place.model_dump(
            mode="python",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        first = _PLACE_ADAPTER.validate_python(python_tree, strict=True)
        json_tree = first.model_dump(
            mode="json",
            exclude_none=False,
            by_alias=False,
            exclude_computed_fields=True,
        )
        payload = json.dumps(
            json_tree,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return _PLACE_ADAPTER.validate_json(payload, strict=True)
    except (AttributeError, TypeError, ValueError, ValidationError, UnicodeError):
        raise HotelInputError() from None


def project_candidates(places: tuple[PlaceSummary, ...]) -> tuple[HotelCandidate, ...]:
    """按供应商顺序投影最多五个候选，同时完整检查本次响应的 UID 身份。"""

    if type(places) is not tuple or len(places) > 10:
        raise HotelInputError()
    seen_identity_by_uid: dict[str, tuple[str, str, float, float]] = {}
    emitted_uids: set[str] = set()
    candidates: list[HotelCandidate] = []

    for place_input in places:
        place = _fresh_place(place_input)
        coordinate = place.coordinate
        identity = (
            place.name,
            place.address,
            coordinate.latitude,
            coordinate.longitude,
        )
        previous = seen_identity_by_uid.get(place.uid)
        if previous is not None and previous != identity:
            raise HotelCandidateIdentityConflictError()
        if previous is None:
            seen_identity_by_uid[place.uid] = identity

        if not qualify_category(place.category):
            continue
        if place.uid in emitted_uids:
            continue
        emitted_uids.add(place.uid)
        if len(candidates) < 5:
            candidates.append(
                HotelCandidate(
                    schema_version="1.0",
                    uid=place.uid,
                    name=place.name,
                    address=place.address,
                    coordinate=HotelCoordinate(
                        schema_version="1.0",
                        coordinate_system="bd09ll",
                        latitude=coordinate.latitude,
                        longitude=coordinate.longitude,
                    ),
                    category=place.category,
                )
            )
    return tuple(candidates)


# Descriptive aliases keep the pure-function surface discoverable without
# re-exporting helpers from app.hotel_selection.__init__.
is_qualified_hotel_category = qualify_category
build_request_hash = hash_request
build_query_plan_hash = hash_query_plan
project_and_deduplicate = project_candidates


__all__ = (
    "build_queries",
    "hash_request",
    "hash_query_plan",
    "qualify_category",
    "project_candidates",
    "is_qualified_hotel_category",
    "build_request_hash",
    "build_query_plan_hash",
    "project_and_deduplicate",
)
